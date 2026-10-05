"""Unified keyword planner — deficit-pulled merged keyword generation (P1.6).

The planner is the generation half of the Discover double-buffered
backpressure model (design spec §5.2). It runs as its own background object
(constructed in ``api/runtime_context.py``, launched by the refresh
controller's ``run_forever``) and, when the
``[discovery].unified_keyword_planner_enabled`` flag is on, periodically:

1. Reconciles bounded, recent, safe regular ``pending`` rows across profile
   digest churn, then finds the ``due`` platforms — those whose usable keyword
   cache is below ``kw_cache_low`` **and**
   that have a real search deficit (the controller's existing pool-replenish
   口径, including raw-material headroom + in-flight rows — NOT just visible
   pool rows). B站 additionally enters ``due`` on its existing catalysts
   (pool-below-target or ≥ ``signal_event_threshold`` pending signal events),
   even when its cache is not below low.
2. Builds one merged ``<platforms>`` block and issues a **single** structured
   LLM call covering all due platforms. Parsed keywords are inserted as
   ``pending`` per platform under the current digest. Reused rows keep their
   original digest and generation provenance.
3. Decline vs failure (P2.2). When the merged call **succeeds**, a platform the
   model explicitly returned an empty list ``[]`` for is an **intentional
   decline** (its supply advantage doesn't fit the user) — it is skipped this
   cycle with NO interest-name fallback (it stays at its current pending and is
   re-offered next cycle if still due). A platform the model **omits** still
   falls back. When the merged call **fails entirely** (raised / no usable
   response), ALL due platforms fall back to deterministic interest names.
4. Rotation polish (P2.3). ``claim_keywords`` is FIFO (oldest pending first), so
   generated words rotate fairly. Recent words participate in the generation
   cache key and are enforced again before insert, so a stable profile cannot
   replay one cached batch for the whole plan TTL. After a generation cycle, a
   non-declined due platform whose pending is still below ``kw_cache_low`` may
   reuse an old ``used`` word only after the configured history window has
   elapsed; a declined platform is left alone.

It never fetches — fetch (claim → search) is P1.7. Single-flight is enforced
through the DB-level planner lock, whose write transaction is released
**before** the LLM call so a slow provider never blocks other writers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import socket
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.config import InspirationBreadthParams, derive_inspiration_breadth_params
from openbiliclaw.discovery.inspiration import (
    AxisRow,
    BrainstormBranch,
    GroundedProbe,
    MaterializeCandidate,
    SecondaryInterest,
    normalize_lens_family,
)
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.discovery.pool_snapshot import (
    build_cold_start_pool_snapshot,
    build_pool_distribution_snapshot,
)
from openbiliclaw.discovery.strategies._utils import (
    build_query_generation_profile_summary,
    cached_embedding_lookup,
)
from openbiliclaw.llm.prompt_cache import PromptLayerRenderCache, profile_prompt_layers
from openbiliclaw.llm.prompts import (
    build_merged_keywords_prompt,
    parse_merged_keywords_with_presence,
    parse_merged_keywords_with_presence_and_explore_domains,
)
from openbiliclaw.llm.task_options import without_core_memory_kwargs

if TYPE_CHECKING:
    from openbiliclaw.config import Config, DiscoveryConfig
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

_INSPIRATION_PROVIDER_TIMEOUT_SECONDS = 8.0


def _ledger_int(value: object) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError):
        return 0


# Canonical long-form platform identifiers. These match the keys the keyword
# store, pool-source shares, and the merged prompt builder all expect — short
# codes (xhs/dy/yt/bili) are NOT used here.
_PLANNER_PLATFORMS: tuple[str, ...] = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "github",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "weibo",
    "instagram",
)
_BILIBILI = "bilibili"
_PLATFORM_QUERY_STYLES: dict[str, dict[str, tuple[str, ...]]] = {
    "bilibili": {
        "native_markers": (
            "B站",
            "Vlog",
            "vlog",
            "解析",
            "幕后",
            "盘点",
            "测评",
            "实测",
            "教程",
            "记录",
            "解说",
            "复盘",
            "流程",
            "体验",
            "案例",
            "争议",
            "设计",
        ),
        "examples": ("日本动画制作进行 幕后解析", "寿喜烧 家庭复刻 教程"),
        "avoid_markers": ("小红书", "笔记", "种草"),
    },
    "xiaohongshu": {
        "native_markers": (
            "攻略",
            "避坑",
            "种草",
            "打卡",
            "清单",
            "真实体验",
            "人均",
            "亲测",
            "笔记",
            "同款",
            "不踩雷",
            "复刻",
            "收藏",
        ),
        "examples": ("日式寿喜烧 家庭复刻 不踩雷", "上海小店 人均50真实体验"),
        "avoid_markers": ("B站", "短视频", "热议"),
    },
    "douyin": {
        "native_markers": (
            "爆款",
            "同款",
            "挑战",
            "实拍",
            "现场",
            "速看",
            "一分钟",
            "短视频",
            "探店",
            "合集",
            "榜单",
            "不踩雷",
            "复刻",
        ),
        "examples": ("动画制作幕后 一分钟速看", "上海小店 探店实拍 不踩雷"),
        "avoid_markers": ("知乎", "小红书笔记"),
    },
    "twitter": {
        "native_markers": (
            "#",
            "热议",
            "讨论",
            "观点",
            "争议",
            "梗",
            "meme",
            "trend",
            "drama",
            "fandom",
            "reaction",
        ),
        "examples": ("#动画制作 热议", "寿喜烧复刻 讨论"),
        "avoid_markers": ("小红书", "知乎"),
    },
    "zhihu": {
        "native_markers": (
            "如何",
            "为什么",
            "怎么",
            "是否",
            "值得",
            "区别",
            "原理",
            "经验",
            "评价",
            "看待",
            "分析",
            "原因",
            "机制",
            "职业",
            "入门",
        ),
        "examples": ("如何评价日本动画制作进行", "寿喜烧家庭复刻有哪些技巧"),
        "avoid_markers": ("小红书", "抖音", "#"),
    },
    "youtube": {
        "native_markers": (
            "review",
            "guide",
            "how",
            "explained",
            "best",
            "documentary",
            "vlog",
            "behind",
            "workflow",
            "production",
            "recipe",
            "restaurant",
            "anime",
        ),
        "examples": ("anime production workflow explained", "homemade sukiyaki recipe guide"),
        "avoid_markers": ("小红书", "知乎", "抖音"),
    },
    "reddit": {
        "native_markers": (
            "recommendation",
            "recommendations",
            "discussion",
            "tips",
            "experience",
            "review",
            "guide",
            "how",
            "vs",
            "explained",
            "recipe",
            "anime",
            "fandom",
            "meme",
            "design",
        ),
        "examples": ("sakuga terminology explained", "homemade sukiyaki recipe tips"),
        "avoid_markers": ("小红书", "知乎", "抖音"),
    },
    "bangumi": {
        "native_markers": (
            "动画",
            "漫画",
            "轻小说",
            "游戏",
            "原作",
            "监督",
            "作者",
            "制作公司",
            "题材",
            "系列",
            "机种",
        ),
        "examples": ("赛博朋克 动画", "独立游戏 时间循环"),
        "avoid_markers": ("爆款", "热议", "速看", "探店"),
    },
    "linuxdo": {
        "native_markers": (
            "教程",
            "经验",
            "分享",
            "求助",
            "踩坑",
            "开源",
            "部署",
            "折腾",
            "讨论",
            "方案",
            "自建",
            "实测",
            "复盘",
        ),
        "examples": ("本地大模型 部署 踩坑", "开源自托管 工具 分享"),
        "avoid_markers": ("小红书", "抖音", "爆款", "种草"),
    },
    "v2ex": {
        "native_markers": ("讨论", "经验", "折腾", "求助", "实测", "踩坑", "方案", "开源"),
        "examples": ("本地运行 Agent 讨论", "家庭网络折腾 实测 踩坑"),
        "avoid_markers": ("短视频", "种草笔记"),
    },
    "weibo": {
        "native_markers": (
            "热议",
            "话题",
            "观点",
            "现场",
            "回应",
            "进展",
            "讨论",
            "争议",
            "超话",
        ),
        "examples": ("AI Agent 热议", "动画制作 业内回应"),
        "avoid_markers": ("小红书", "subreddit"),
    },
    "instagram": {
        "native_markers": (
            "photography",
            "design",
            "art",
            "architecture",
            "travel",
            "food",
            "fashion",
            "fitness",
            "gaming",
            "anime",
            "diy",
            "home",
            "street",
            "makeup",
            "technology",
            "nature",
            "music",
        ),
        "examples": ("urban photography", "indie game art"),
        "avoid_markers": ("小红书", "知乎", "B站", "微博"),
    },
    "github": {
        "native_markers": (
            "repository",
            "repo",
            "library",
            "framework",
            "toolkit",
            "sdk",
            "cli",
            "starter",
            "template",
            "awesome",
            "开源",
            "项目",
            "框架",
            "工具",
        ),
        "examples": ("local llm agent framework", "self hosted knowledge base"),
        "avoid_markers": ("热议", "探店", "种草", "短视频"),
    },
}
# The planner reclaims in-flight rows that leaked past the claim lease before
# each generation pass. ``executing`` rows belong to genuinely async (XHS)
# tasks, so give them a much wider timeout than a plain claim lease.
_EXECUTING_TIMEOUT_MULTIPLIER = 6
# P3.2 dynamic cache high-water: a platform's generation target may grow up to
# this multiple of the static ``kw_cache_high`` when its observed yield is low
# (lots of duplicate hits → need more words to fill the same deficit). Below
# ``_DYNAMIC_MIN_SAMPLES`` used keywords the yield estimate is too noisy → fall
# back to the static high.
_DYNAMIC_HIGH_CAP_MULT = 3
_DYNAMIC_MIN_SAMPLES = 10
# P3.1 per-platform topic saturation: a platform with fewer than this many of
# its own fresh pooled rows falls back to the global avoid (too little data to
# judge); above the floor, a topic is "saturated for a platform" once its count
# reaches max(_MIN, platform_total // _DIV) of that platform's own pool.
_PER_PLATFORM_AVOID_FLOOR = 10
_PER_PLATFORM_AVOID_MIN_THRESHOLD = 5
_PER_PLATFORM_AVOID_DIVISOR = 5
# P3.3 data-driven supply advantage: the top topic_groups a platform has
# actually admitted (non-disliked, all-time) ride along as a per-call hint that
# complements the static <supply_advantage> table. A platform needs at least
# _FLOOR admitted rows before the signal is trusted (else cold start → static
# table alone); a topic needs max(_MIN, total // _DIV) admits to count as a
# strength, and at most _TOP are surfaced. The platform's current avoid set is
# subtracted so a topic is never both "lean in" and "avoid".
_PER_PLATFORM_SUPPLY_FLOOR = 10
_PER_PLATFORM_SUPPLY_MIN_THRESHOLD = 3
_PER_PLATFORM_SUPPLY_DIVISOR = 10
_PER_PLATFORM_SUPPLY_TOP = 8
# Merged-generation token budget. The merged call is the largest-output call in
# the system (every due platform × up to gen_batch keywords in one JSON), so a
# fixed max_tokens can truncate the trailing platforms — they then fall onto the
# interest-name fallback. Size max_tokens from the actual per-cycle ask (sum of
# the gen_batch-capped needs) with a generous per-keyword budget (Chinese phrase
# + JSON quoting). Over-provisioning is effectively free: max_tokens is a ceiling
# billed on real output, not a charge. The floor is 8192 (was 4096) because
# reasoning-first instances can otherwise spend the whole budget on invisible
# thinking and return no final JSON at all.
_MERGED_TOKENS_PER_KEYWORD = 48
_MERGED_JSON_OVERHEAD_TOKENS = 1024
_MERGED_MIN_MAX_TOKENS = 8192
_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS = 8192
# F2 (Phase 2.1): the single axis+keyword call keeps the 8192 floor for small
# slot counts and only scales past a comfortable threshold, so high-platform
# runs (F1 makes each core_concept longer) do not brush the output ceiling.
# ``max_tokens = min(CEIL, floor + max(0, slots - THRESHOLD) * PER_SLOT)``;
# slots = len(selected_interests) * len(target_platforms). THRESHOLD=12 is the
# 3-platform comfort point (production caps interests at 4, so 3×4=12); the
# 6-platform case (4×6=24) therefore gets real headroom (11264) instead of
# sitting exactly on the threshold. CEIL stays 16384 — the bounded single retry
# at the floor is the fallback for a provider/gateway whose real ceiling turns
# out lower (see report: deepseek-v4-flash's own ceiling is 64K, but the
# sensenova gateway cap could not be confirmed here).
_INSPIRATION_AXIS_KEYWORD_MAX_TOKENS_CEIL = 16384
_PER_SLOT_TOKEN_BUDGET = 256
_INSPIRATION_AXIS_KEYWORD_SLOT_THRESHOLD = 12
_INSPIRATION_AXIS_KEYWORD_MAX_INTERESTS = 4
_INSPIRATION_AXIS_KEYWORD_MAX_AXES_PER_INTEREST = 6
_INSPIRATION_AXIS_KEYWORD_MAX_EVIDENCE_TOTAL = 24
_INSPIRATION_AXIS_KEYWORD_MAX_EVIDENCE_PER_INTEREST = 8
# Spec AC3 requires every interest to cover at least two axes or report a shortfall.
_INSPIRATION_MIN_AXES_PER_INTEREST = 2
# Axis yield backfill + lifecycle run at most once per this interval (checked
# against the table-wide MAX(yield_backfilled_at)). Production stages only —
# preview never triggers the tick (observation must not mutate the observed).
_AXIS_BACKFILL_MIN_INTERVAL_HOURS = 6


def _as_str_list(value: object) -> list[str]:
    """Coerce a loosely-typed JSON value into a clean ``list[str]``."""
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _mapping_get(row: object, key: str, default: object = "") -> object:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _inspiration_interest_label(row: object) -> str:
    for key in ("label", "interest", "interest_label", "name"):
        value = _as_text(_mapping_get(row, key))
        if value:
            return value
    return ""


def _axis_interest_label(axis: object) -> str:
    return _as_text(_mapping_get(axis, "interest_label")) or _as_text(
        _mapping_get(axis, "interest")
    )


def _evidence_interest_label(row: object) -> str:
    return _as_text(_mapping_get(row, "interest")) or _as_text(_mapping_get(row, "interest_label"))


def _axis_prompt_row(axis: object) -> dict[str, object]:
    return {
        "axis_id": _as_text(_mapping_get(axis, "axis_id")),
        "interest": _axis_interest_label(axis),
        "axis_label": _as_text(_mapping_get(axis, "axis_label")),
        "axis_kind": _as_text(_mapping_get(axis, "axis_kind")) or "other",
        "example_terms": _as_str_list(_mapping_get(axis, "example_terms")),
        "evidence_refs": _as_str_list(_mapping_get(axis, "evidence_refs")),
        "time_sensitive": _as_bool(_mapping_get(axis, "time_sensitive", False)),
    }


def _interest_prompt_row(row: object) -> dict[str, object]:
    return {
        "interest_id": _as_text(_mapping_get(row, "interest_id")),
        "label": _inspiration_interest_label(row),
        "parent": _as_text(_mapping_get(row, "parent")),
        "weight": _mapping_get(row, "weight", 0.0),
    }


def _evidence_prompt_row(row: object) -> dict[str, object]:
    if isinstance(row, Mapping):
        return {str(key): value for key, value in row.items()}
    return {
        "interest": _evidence_interest_label(row),
        "title": _as_text(_mapping_get(row, "title")),
        "url": _as_text(_mapping_get(row, "url")),
        "source": _as_text(_mapping_get(row, "source")),
    }


def _allocation_target_platforms(allocation_targets: Mapping[str, object]) -> set[str]:
    platforms: set[str] = set()
    for raw_target in allocation_targets.values():
        if not isinstance(raw_target, Mapping):
            continue
        for platform in _as_str_list(raw_target.get("platforms")):
            platforms.add(platform)
    return platforms


def _filter_platform_guides(platform_guides: object, target_platforms: set[str]) -> object:
    if isinstance(platform_guides, Mapping):
        return {
            str(platform): value
            for platform, value in platform_guides.items()
            if str(platform) in target_platforms
        }
    if isinstance(platform_guides, Sequence) and not isinstance(platform_guides, (str, bytes)):
        guides: dict[str, object] = {}
        for raw_guide in platform_guides:
            if not isinstance(raw_guide, Mapping):
                continue
            platform = _as_text(raw_guide.get("platform"))
            if platform in target_platforms:
                guides[platform] = dict(raw_guide)
        return guides
    return {}


def _platform_guides_count(platform_guides: object) -> int:
    if isinstance(platform_guides, Mapping):
        return len(platform_guides)
    if isinstance(platform_guides, Sequence) and not isinstance(platform_guides, (str, bytes)):
        return len(platform_guides)
    return 0


def _cap_inspiration_axis_keyword_inputs(
    *,
    platform_guides: object,
    selected_interests: Sequence[object],
    existing_axes: Sequence[object],
    fresh_evidence: Sequence[object],
    allocation_targets: Mapping[str, object],
) -> tuple[
    object,
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, int],
]:
    selected = [
        _interest_prompt_row(row)
        for row in selected_interests[:_INSPIRATION_AXIS_KEYWORD_MAX_INTERESTS]
    ]
    telemetry = {
        "selected_interests_truncated": max(0, len(selected_interests) - len(selected)),
        "existing_axes_truncated": 0,
        "fresh_evidence_truncated": 0,
        "platform_guides_dropped": 0,
    }

    axes_by_interest: dict[str, int] = {}
    capped_axes: list[dict[str, object]] = []
    for axis in existing_axes:
        interest = _axis_interest_label(axis)
        count = axes_by_interest.get(interest, 0)
        if count >= _INSPIRATION_AXIS_KEYWORD_MAX_AXES_PER_INTEREST:
            telemetry["existing_axes_truncated"] += 1
            continue
        axes_by_interest[interest] = count + 1
        capped_axes.append(_axis_prompt_row(axis))

    evidence_by_interest: dict[str, int] = {}
    capped_evidence: list[dict[str, object]] = []
    for row in fresh_evidence:
        interest = _evidence_interest_label(row)
        count = evidence_by_interest.get(interest, 0)
        if (
            count >= _INSPIRATION_AXIS_KEYWORD_MAX_EVIDENCE_PER_INTEREST
            or len(capped_evidence) >= _INSPIRATION_AXIS_KEYWORD_MAX_EVIDENCE_TOTAL
        ):
            telemetry["fresh_evidence_truncated"] += 1
            continue
        evidence_by_interest[interest] = count + 1
        capped_evidence.append(_evidence_prompt_row(row))

    target_platforms = _allocation_target_platforms(allocation_targets)
    capped_guides = _filter_platform_guides(platform_guides, target_platforms)
    telemetry["platform_guides_dropped"] = max(
        0,
        _platform_guides_count(platform_guides) - _platform_guides_count(capped_guides),
    )
    return capped_guides, selected, capped_axes, capped_evidence, telemetry


def _loads_json_object(content: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(content.strip())
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_object_array_prefix(content: str, key: str) -> tuple[list[dict[str, object]], int]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', content)
    if match is None:
        return [], 0
    decoder = json.JSONDecoder()
    index = match.end()
    result: list[dict[str, object]] = []
    dropped = 0
    while index < len(content):
        while index < len(content) and content[index].isspace():
            index += 1
        if index < len(content) and content[index] == ",":
            index += 1
            continue
        if index >= len(content) or content[index] == "]":
            break
        try:
            item, next_index = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            if content[index:].strip():
                dropped += 1
            break
        if isinstance(item, dict):
            result.append({str(k): v for k, v in item.items()})
        else:
            dropped += 1
        index = next_index
    return result, dropped


def _axis_rows_from_payload(raw_axes: Sequence[object]) -> list[AxisRow]:
    axes: list[AxisRow] = []
    for raw_axis in raw_axes:
        if not isinstance(raw_axis, Mapping):
            continue
        interest = _as_text(raw_axis.get("interest")) or _as_text(raw_axis.get("interest_label"))
        axis_label = _as_text(raw_axis.get("axis_label"))
        if not interest or not axis_label:
            continue
        axes.append(
            AxisRow(
                interest_label=interest,
                axis_label=axis_label,
                axis_kind=_as_text(raw_axis.get("axis_kind")) or "other",
                source="llm_axis_keyword",
                axis_id=_as_text(raw_axis.get("axis_id")),
                example_terms=tuple(_as_str_list(raw_axis.get("example_terms"))),
                evidence_refs=tuple(_as_str_list(raw_axis.get("evidence_refs"))),
                time_sensitive=_as_bool(raw_axis.get("time_sensitive")),
            )
        )
    return axes


def _materialize_candidates_from_payload(
    raw_keywords: Sequence[object],
) -> list[MaterializeCandidate]:
    candidates: list[MaterializeCandidate] = []
    for raw_keyword in raw_keywords:
        if not isinstance(raw_keyword, Mapping):
            continue
        interest = _as_text(raw_keyword.get("interest"))
        axis_label = _as_text(raw_keyword.get("axis_id_or_label")) or _as_text(
            raw_keyword.get("axis_label")
        )
        platform = _as_text(raw_keyword.get("platform"))
        core_concept = _as_text(raw_keyword.get("core_concept"))
        if not interest or not axis_label or not platform or not core_concept:
            continue
        candidates.append(
            MaterializeCandidate(
                interest=interest,
                axis_label=axis_label,
                platform=platform,
                core_concept=core_concept,
                decoration=_as_text(raw_keyword.get("decoration")),
                recency_sensitivity=_as_text(raw_keyword.get("recency_sensitivity")) or "low",
                origin="llm_axis_keyword",
            )
        )
    return candidates


def _legacy_axis_keyword_payload_from_expansions(
    raw_expansions: Sequence[object],
    *,
    selected_interests: Sequence[object],
    platforms: Sequence[str],
) -> tuple[list[AxisRow], list[MaterializeCandidate]]:
    interest_by_id = {
        _as_text(_mapping_get(item, "interest_id")): _inspiration_interest_label(item)
        for item in selected_interests
        if _as_text(_mapping_get(item, "interest_id")) and _inspiration_interest_label(item)
    }
    fallback_interest = ""
    selected_labels = [
        _inspiration_interest_label(item)
        for item in selected_interests
        if _inspiration_interest_label(item)
    ]
    if len(selected_labels) == 1:
        fallback_interest = selected_labels[0]

    axes: list[AxisRow] = []
    candidates: list[MaterializeCandidate] = []
    for raw_expansion in raw_expansions:
        if not isinstance(raw_expansion, Mapping):
            continue
        text = _as_text(raw_expansion.get("text"))
        interest = interest_by_id.get(_as_text(raw_expansion.get("aspect_id")), fallback_interest)
        if not interest:
            continue
        axis_values = _as_str_list(raw_expansion.get("detail_axes")) or [
            _as_text(raw_expansion.get("relation")) or text or "legacy_axis"
        ]
        axis_label = axis_values[0]
        axes.append(
            AxisRow(
                interest_label=interest,
                axis_label=axis_label,
                axis_kind=normalize_lens_family(axis_label),
                source="legacy_axis_keyword_payload",
                example_terms=(text,) if text else (),
            )
        )
        platform_keywords = raw_expansion.get("platform_keywords")
        emitted_platforms: set[str] = set()
        if isinstance(platform_keywords, Mapping):
            for raw_platform, raw_keywords in platform_keywords.items():
                platform = _as_text(raw_platform)
                for keyword in _as_str_list(raw_keywords):
                    candidates.append(
                        MaterializeCandidate(
                            interest=interest,
                            axis_label=axis_label,
                            platform=platform,
                            core_concept=keyword,
                            decoration="",
                            recency_sensitivity="low",
                            origin="legacy_axis_keyword_payload",
                        )
                    )
                    emitted_platforms.add(platform)
        for keyword in _as_str_list(raw_expansion.get("keywords")):
            target_platforms = tuple(platforms) if platforms else tuple(emitted_platforms)
            for platform in target_platforms:
                if platform in emitted_platforms:
                    continue
                candidates.append(
                    MaterializeCandidate(
                        interest=interest,
                        axis_label=axis_label,
                        platform=platform,
                        core_concept=keyword,
                        decoration="",
                        recency_sensitivity="low",
                        origin="legacy_axis_keyword_payload",
                    )
                )
    return axes, candidates


def _parse_inspiration_axis_keyword_payload(
    content: str,
    *,
    selected_interests: Sequence[object] = (),
    platforms: Sequence[str] = (),
) -> tuple[list[AxisRow], list[MaterializeCandidate], dict[str, object]]:
    telemetry: dict[str, object] = {
        "parse_salvaged": False,
        "parse_dropped_count": 0,
    }
    if not content.strip():
        return [], [], telemetry

    payload = _loads_json_object(content)
    raw_axes: Sequence[object] = []
    raw_keywords: Sequence[object] = []
    if payload is not None:
        axes_value = payload.get("axes")
        keywords_value = payload.get("keywords")
        raw_axes = axes_value if isinstance(axes_value, list) else []
        raw_keywords = keywords_value if isinstance(keywords_value, list) else []
        expansions_value = payload.get("expansions")
        if not raw_axes and not raw_keywords and isinstance(expansions_value, list):
            axes, candidates = _legacy_axis_keyword_payload_from_expansions(
                expansions_value,
                selected_interests=selected_interests,
                platforms=platforms,
            )
            return axes, candidates, telemetry
    else:
        axes_prefix, axes_dropped = _extract_object_array_prefix(content, "axes")
        keywords_prefix, keywords_dropped = _extract_object_array_prefix(content, "keywords")
        raw_axes = axes_prefix
        raw_keywords = keywords_prefix
        dropped_count = axes_dropped + keywords_dropped
        telemetry["parse_salvaged"] = bool(raw_axes or raw_keywords)
        telemetry["parse_dropped_count"] = dropped_count

    return (
        _axis_rows_from_payload(raw_axes),
        _materialize_candidates_from_payload(raw_keywords),
        telemetry,
    )


class KeywordDeficitSource(Protocol):
    """The deficit口径 the planner reuses (satisfied by the refresh controller).

    The planner deliberately does NOT recompute the pool deficit itself — it
    asks the controller, so it shares the exact same in-flight / raw-headroom
    accounting that drives ``_build_source_replenishment_plan``.
    """

    def keyword_planner_real_deficit(self, platform: str) -> int: ...

    def keyword_planner_bilibili_catalyst(self) -> bool: ...

    def keyword_planner_explore_due_soon(self) -> bool: ...

    def keyword_planner_explore_covered_topic_groups(self) -> list[str]: ...

    def keyword_planner_mark_explore_planned(self) -> None: ...


class _SoulEngineLike(Protocol):
    async def get_profile(self) -> Any: ...


class KeywordPlanner:
    """Deficit-pulled merged keyword generator (design spec §5.2).

    Holds its own ``llm_service`` + ``database`` + ``config`` (the controller
    has no LLM field). The deficit source is injected after construction via
    :meth:`bind_deficit_source` because the controller is built after the
    planner.
    """

    def __init__(
        self,
        *,
        llm_service: Any,
        database: Any,
        config: Config,
        soul_engine: _SoulEngineLike | None = None,
        pool_target_count: int | None = None,
        signal_event_threshold: int = 6,
        owner: str | None = None,
        embedding_service: Any | None = None,
        inspiration_provider: Any | None = None,
        inspiration_params: InspirationBreadthParams | None = None,
    ) -> None:
        self._llm = llm_service
        self._db = database
        self._config = config
        self._soul_engine = soul_engine
        self._embedding_service = embedding_service
        self._inspiration_provider = inspiration_provider
        # Internal config view for the inspiration knobs. When injected (CLI
        # one-shot overrides, tests) it wins; otherwise the values derive from
        # the ``inspiration_breadth`` tier at read time.
        self._inspiration_params_override = inspiration_params
        self._deficit_source: KeywordDeficitSource | None = None
        self._pool_target_count = pool_target_count
        self._signal_event_threshold = signal_event_threshold
        # Unique-per-process lock owner so the CAS single-flight lock can tell
        # this planner instance apart from a stale crashed one.
        self._owner = owner or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        # In-process single-flight: the DB planner lock is re-entrant for the
        # same ``owner`` (so a crashed-then-restarted planner can retake it), so
        # it does NOT stop two overlapping ``run_once`` calls on the SAME
        # instance from double-generating. This lock does — cross-process /
        # cross-instance contention is still handled by the DB lock below.
        self._inflight_lock = asyncio.Lock()
        # P1.9 per-cycle observability ledger: the most recent
        # ``{platform: {"generated": n, "yield": y}}`` snapshot emitted by a
        # generation pass. Empty until the first pass that generates anything.
        self.last_cycle_ledger: dict[str, dict[str, int]] = {}
        # Aggregate-only digest-grace observability. This deliberately stores
        # counts rather than keyword/profile/digest text.
        self.last_digest_grace_ledger: dict[str, dict[str, int]] = {}
        self._grace_inventory_ready: set[str] = set()
        # Phase 2.3 telemetry: True when the last coexist explore round fell back
        # from rich axis-library generation to the flat ``explore_domains``
        # queries (rich-gen degraded / empty). Reset each explore attempt.
        self.last_explore_inspiration_degraded: bool = False
        self._profile_prompt_cache = PromptLayerRenderCache()
        self._generation_cache: dict[
            str,
            tuple[float, dict[str, list[str]], set[str], list[dict[str, object]]],
        ] = {}
        # Part D: the ①–⑥ inspiration orchestration lives in a dedicated
        # pipeline. The four compatibility delegates below forward to it. The
        # ``host`` back-reference covers the planner infra helpers shared with
        # the merged-keyword path (_history / _insert / _avoid_hints /
        # _supply_hints / _load_profile). Lazy import avoids an import cycle.
        from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

        self._inspiration_pipeline: InspirationKeywordPipeline = InspirationKeywordPipeline(
            host=self,
            db=database,
            llm_service=llm_service,
            inspiration_provider=inspiration_provider,
            discovery=lambda: self._discovery,
            inspiration_params=lambda: self._inspiration_params,
            clock=lambda: datetime.now(UTC),
            embedding_service=embedding_service,
        )

    # ── wiring ──────────────────────────────────────────────────────────

    def bind_deficit_source(self, source: KeywordDeficitSource) -> None:
        """Inject the controller as the shared pool-deficit / catalyst口径."""
        self._deficit_source = source

    def bind_soul_engine(self, soul_engine: _SoulEngineLike) -> None:
        """Inject the soul engine (the planner always reads the live profile)."""
        self._soul_engine = soul_engine

    @property
    def owner(self) -> str:
        return self._owner

    # ── config helpers ──────────────────────────────────────────────────

    @property
    def _discovery(self) -> DiscoveryConfig:
        return self._config.discovery

    @property
    def _inspiration_params(self) -> InspirationBreadthParams:
        if self._inspiration_params_override is not None:
            return self._inspiration_params_override
        return derive_inspiration_breadth_params(
            getattr(self._discovery, "inspiration_breadth", "medium")
        )

    @property
    def enabled(self) -> bool:
        return bool(self._discovery.unified_keyword_planner_enabled)

    @property
    def poll_seconds(self) -> int:
        return max(1, int(self._discovery.planner_poll_seconds))

    def _resolved_pool_target(self) -> int:
        if self._pool_target_count is not None:
            return int(self._pool_target_count)
        scheduler = getattr(self._config, "scheduler", None)
        return int(getattr(scheduler, "pool_target_count", 300))

    # ── inspiration compatibility delegates (Part D) ────────────────────
    #
    # The ①–⑥ orchestration physically moved to ``InspirationKeywordPipeline``.
    # These thin delegates keep the private APIs existing tests / the runtime
    # controller call directly, with signatures unchanged.

    @property
    def last_axis_backfill(self) -> dict[str, object]:
        return self._inspiration_pipeline.last_axis_backfill

    async def plan_inspiration_axis_keywords(
        self,
        *,
        profile_digest: object,
        platform_guides: object,
        selected_interests: Sequence[object],
        existing_axes: Sequence[object],
        fresh_evidence: Sequence[object],
        allocation_targets: Mapping[str, object],
    ) -> tuple[list[AxisRow], list[MaterializeCandidate], dict[str, object]]:
        return await self._inspiration_pipeline.plan_inspiration_axis_keywords(
            profile_digest=profile_digest,
            platform_guides=platform_guides,
            selected_interests=selected_interests,
            existing_axes=existing_axes,
            fresh_evidence=fresh_evidence,
            allocation_targets=allocation_targets,
        )

    async def preview_inspiration_keywords(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile | None = None,
        query_kind: str = "regular",
        persist_axes: bool = False,
    ) -> dict[str, object]:
        return await self._inspiration_pipeline.preview_inspiration_keywords(
            platforms,
            profile=profile,
            query_kind=query_kind,
            persist_axes=persist_axes,
        )

    async def _run_shared_inspiration_stage(
        self,
        regular_platforms: list[str],
        *,
        explore_platforms: list[str],
        profile: SoulProfile,
        digest: str,
    ) -> tuple[dict[str, int], int]:
        return await self._inspiration_pipeline._run_shared_inspiration_stage(
            regular_platforms,
            explore_platforms=explore_platforms,
            profile=profile,
            digest=digest,
        )

    async def _run_inspiration_stage(
        self,
        platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        query_kind: str = "regular",
    ) -> dict[str, int]:
        return await self._inspiration_pipeline._run_inspiration_stage(
            platforms,
            profile=profile,
            digest=digest,
            query_kind=query_kind,
        )

    async def _run_explore_inspiration_stage(
        self,
        explore_platforms: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        explore_domains: Sequence[Mapping[str, object]],
        covered_topic_groups: Sequence[str],
    ) -> tuple[dict[str, int], dict[str, object]]:
        return await self._inspiration_pipeline._run_explore_inspiration_stage(
            explore_platforms,
            profile=profile,
            digest=digest,
            explore_domains=explore_domains,
            covered_topic_groups=covered_topic_groups,
        )

    def _selected_inspiration_interests(
        self,
        profile: SoulProfile,
        coverage_snapshot: dict[str, dict[str, object]],
    ) -> list[SecondaryInterest]:
        return self._inspiration_pipeline._selected_inspiration_interests(
            profile, coverage_snapshot
        )

    @staticmethod
    def _interest_hint_terms(
        selected_interests: list[SecondaryInterest],
        branches: list[BrainstormBranch],
        grounding_records: list[GroundedProbe],
    ) -> dict[str, set[str]]:
        from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

        return InspirationKeywordPipeline._interest_hint_terms(
            selected_interests, branches, grounding_records
        )

    @staticmethod
    def _infer_source_interest_from_keyword(
        keyword: str,
        interest_hint_terms: dict[str, set[str]],
    ) -> str:
        from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

        return InspirationKeywordPipeline._infer_source_interest_from_keyword(
            keyword, interest_hint_terms
        )

    @staticmethod
    def _build_axis_id_index(
        axes: Sequence[AxisRow],
    ) -> tuple[set[str], dict[tuple[str, str], str]]:
        from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

        return InspirationKeywordPipeline._build_axis_id_index(axes)

    @staticmethod
    def _resolve_realized_axis_id(
        *,
        raw_axis_id: str,
        source_interest: str,
        axis_label: str,
        axis_id_index: tuple[set[str], dict[tuple[str, str], str]],
    ) -> str:
        from openbiliclaw.runtime.inspiration_pipeline import InspirationKeywordPipeline

        return InspirationKeywordPipeline._resolve_realized_axis_id(
            raw_axis_id=raw_axis_id,
            source_interest=source_interest,
            axis_label=axis_label,
            axis_id_index=axis_id_index,
        )

    # ── loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Poll loop: reclaim leases + run one planning pass each interval.

        When the feature flag is OFF this is a pure no-op (it still sleeps so
        ``run_forever``'s gather keeps a live task, but it never touches the
        store or the LLM) — guaranteeing zero behavior change pre-cutover.
        """
        poll_seconds = self.poll_seconds
        while True:
            if self.enabled:
                try:
                    self.reclaim_leases()
                except Exception:
                    logger.exception("keyword planner lease reclaim failed")
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("keyword planner run_once failed")
            await asyncio.sleep(poll_seconds)

    def reclaim_leases(self) -> None:
        reclaim = getattr(self._db, "reclaim_leased_keywords", None)
        if not callable(reclaim):
            return
        claim_lease_minutes = float(self._discovery.claim_lease_minutes)
        executing_timeout_minutes = claim_lease_minutes * _EXECUTING_TIMEOUT_MULTIPLIER
        reclaimed = int(
            reclaim(
                claim_lease_minutes=claim_lease_minutes,
                executing_timeout_minutes=executing_timeout_minutes,
            )
        )
        if reclaimed:
            logger.info("keyword planner reclaimed %d leased keyword(s) to pending", reclaimed)

    def _retire_min_age_minutes(self) -> float:
        """Age floor before a 0-yield ``used`` word may be retired.

        Must comfortably exceed the worst-case admit latency so a freshly-used
        word whose yield is still pending (fetch-only X/YT, async XHS — marked
        ``used`` at handoff, credited only once the shared pipeline admits) is
        not retired prematurely. Reuse the (much wider) ``executing`` timeout so
        even an in-flight XHS task's eventual admit lands before retirement.
        """
        claim_lease_minutes = float(self._discovery.claim_lease_minutes)
        return max(60.0, claim_lease_minutes * _EXECUTING_TIMEOUT_MULTIPLIER)

    def retire_zero_yield(self) -> int:
        """Retire barren ``used`` words across all planner platforms (P1.8).

        Best-effort; a retire failure on one platform never aborts the pass.
        Returns the total number of rows retired (for observability / tests).
        """
        retire = getattr(self._db, "retire_zero_yield_keywords", None)
        if not callable(retire):
            return 0
        min_age = self._retire_min_age_minutes()
        total = 0
        for platform in _PLANNER_PLATFORMS:
            try:
                total += int(retire(platform, min_age_minutes=min_age))
            except Exception:
                logger.exception("retire_zero_yield_keywords failed for %s", platform)
        if total:
            logger.info("keyword planner retired %d zero-yield keyword(s)", total)
        return total

    # ── one planning pass ───────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Run a single deficit-pulled merged-generation pass.

        Returns a per-platform ``{platform: inserted}`` ledger (empty when
        nothing was due or the flag is off) for observability / tests.
        """
        if not self.enabled:
            return {}

        # P1.8: retire demonstrably-barren search words (``used`` with yield 0
        # past a conservative age floor) every pass. Cheap single UPDATE, runs
        # before the due short-circuit so it fires even when nothing is due, and
        # decoupled from generation/fetch. The age floor protects freshly-used
        # words still pending their async (X / YT / XHS) admit.
        self.retire_zero_yield()

        # In-process single-flight: a second overlapping pass on this instance
        # bails immediately (the DB lock is re-entrant for our own owner).
        if self._inflight_lock.locked():
            logger.debug("keyword planner pass skipped: a pass is already in flight")
            return {}
        async with self._inflight_lock:
            return await self._run_once_locked()

    async def _run_once_locked(self) -> dict[str, int]:
        self.last_digest_grace_ledger = {}
        self._grace_inventory_ready.clear()
        profile = await self._load_profile()
        if profile is None:
            return {}

        digest = profile_kw_digest(profile)
        hints_by_platform = self._avoid_hints(profile)
        self._reconcile_pending_inventory(
            digest=digest,
            hints_by_platform=hints_by_platform,
        )
        due = self._due_platforms(digest)
        if not due:
            return {}

        # Single-flight: short CAS lock, released BEFORE the LLM call.
        lease_seconds = max(1.0, float(self._discovery.claim_lease_minutes) * 60.0)
        if not self._acquire_lock(lease_seconds):
            logger.debug("keyword planner pass skipped: another owner holds the lock")
            return {}

        ledger: dict[str, int] = {}
        try:
            ledger = await self._generate_for(
                due,
                profile=profile,
                digest=digest,
                hints_by_platform=hints_by_platform,
            )
        finally:
            self._release_lock()
        return ledger

    async def _generate_for(
        self,
        due: list[str],
        *,
        profile: SoulProfile,
        digest: str,
        hints_by_platform: dict[str, dict[str, object]],
    ) -> dict[str, int]:
        supply_by_platform = self._supply_hints(hints_by_platform)
        blocks: list[dict[str, object]] = []
        needs: dict[str, int] = {}
        total_ask = 0
        gen_batch = max(0, int(self._discovery.gen_batch))
        for platform in due:
            current_pending = self._count_pending(platform, digest)
            need = max(0, self._target_high(platform) - current_pending)
            # Never ask the model for more than we keep this cycle: the parse caps
            # each platform at gen_batch, so asking for the full (possibly dynamic,
            # up to high × _DYNAMIC_HIGH_CAP_MULT) gap only bloats the merged JSON
            # and pushes the trailing platforms toward truncation. Cap the ask.
            shown_need = min(need, gen_batch)
            if shown_need <= 0:
                # No gap to fill (or gen_batch disabled). The B站 catalyst can mark
                # a platform due while its cache is already full — skip it.
                continue
            needs[platform] = need
            total_ask += shown_need
            avoid = hints_by_platform.get(platform, {})
            blocks.append(
                {
                    "platform": platform,
                    "need": shown_need,
                    "recent_keywords": self._history(platform),
                    "avoid_topics": _as_str_list(avoid.get("avoid_topics")),
                    "avoid_styles": _as_str_list(avoid.get("avoid_styles")),
                    "avoid_franchises": _as_str_list(avoid.get("avoid_franchises")),
                    "prefer_axes": _as_str_list(avoid.get("prefer_axes")),
                    "cold_start": bool(avoid.get("cold_start")),
                    "supply_hint": list(supply_by_platform.get(platform, [])),
                }
            )

        if self._inspiration_replaces_merged_keywords():
            explore_request = self._explore_domains_request() if needs else None
            if explore_request is not None:
                (
                    inspiration_only_ledger,
                    explore_inserted,
                ) = await self._run_shared_inspiration_stage(
                    list(needs),
                    explore_platforms=[_BILIBILI],
                    profile=profile,
                    digest=digest,
                )
                if explore_inserted > 0:
                    self._mark_explore_planned()
            else:
                inspiration_only_ledger = await self._run_inspiration_stage(
                    list(needs),
                    profile=profile,
                    digest=digest,
                    query_kind="regular",
                )
            self._emit_cycle_ledger(inspiration_only_ledger, digest)
            return inspiration_only_ledger

        generated: dict[str, list[str]] = {}
        present: set[str] = set()
        # ``call_failed`` distinguishes "the merged LLM call raised / returned
        # nothing usable" (→ fall back for ALL due platforms) from "the call
        # succeeded but platform X returned an explicit empty list" (→ X
        # declined, skip it without a fallback). It stays False when there was
        # nothing to call (``blocks`` empty) — no failure, just nothing to do.
        call_failed = False
        explore_request = self._explore_domains_request() if blocks else None
        explore_domains: list[dict[str, object]] = []
        if blocks:
            target_platforms = [str(block["platform"]) for block in blocks]
            cache_key = self._generation_cache_key(digest, blocks, explore_request)
            cached = self._cached_generation(cache_key)
            if cached is not None:
                generated, present, explore_domains = cached
            else:
                # Budget the merged call's max_tokens from the actual ask (sum of the
                # gen_batch-capped needs) so the trailing platforms in the JSON are
                # never truncated onto the interest-name fallback. Scales with
                # platform count and gen_batch; floored at 8192 so reasoning-first
                # instances can finish thinking before emitting the JSON.
                merged_max_tokens = max(
                    _MERGED_MIN_MAX_TOKENS,
                    total_ask * _MERGED_TOKENS_PER_KEYWORD + _MERGED_JSON_OVERHEAD_TOKENS,
                )
                try:
                    profile_summary = build_query_generation_profile_summary(
                        profile,
                        embedding_lookup=cached_embedding_lookup(self._embedding_service),
                    )
                    profile_blocks = self._profile_prompt_cache.render_json_layers(
                        profile_prompt_layers(profile_summary)
                    )
                    messages = build_merged_keywords_prompt(
                        profile_summary=profile_summary,
                        profile_blocks=profile_blocks,
                        platform_blocks=blocks,
                        explore_domains_block=explore_request,
                    )
                    complete_structured = self._llm.complete_structured_task
                    response = await complete_structured(
                        system_instruction=messages[0]["content"],
                        user_input=messages[1]["content"],
                        caller="discovery.keyword_planner",
                        reasoning_effort=None,
                        max_tokens=merged_max_tokens,
                        **without_core_memory_kwargs(complete_structured),
                    )
                    content = str(getattr(response, "content", "") or "")
                    if explore_request is not None:
                        (
                            generated,
                            present,
                            explore_domains,
                        ) = parse_merged_keywords_with_presence_and_explore_domains(
                            content,
                            target_platforms,
                            per_platform_cap=gen_batch,
                            max_explore_domains=int(cast("Any", explore_request["need_domains"])),
                            queries_per_domain=int(
                                cast("Any", explore_request["queries_per_domain"])
                            ),
                        )
                    else:
                        generated, present = parse_merged_keywords_with_presence(
                            content,
                            target_platforms,
                            per_platform_cap=gen_batch,
                        )
                    self._store_generation(cache_key, generated, present, explore_domains)
                except Exception:
                    logger.exception(
                        "keyword planner merged generation failed; "
                        "falling back to interest names for %s",
                        target_platforms,
                    )
                    generated = {}
                    present = set()
                    call_failed = True

        low = int(self._discovery.kw_cache_low)
        ledger: dict[str, int] = {}
        # Only platforms with a real need (need > 0) generate / insert. A
        # platform marked due purely by the B站 catalyst whose cache is already
        # at high (need == 0) was dropped from ``blocks`` above and must NOT
        # receive a fallback insert.
        inspiration_platforms: list[str] = []
        for platform in needs:
            words = generated.get(platform, [])
            declined = False
            if not words:
                if not call_failed and platform in present:
                    # P2.2 decline: the merged call succeeded and the model
                    # explicitly returned [] for this platform → intentional
                    # decline (interests don't fit its supply advantage). Skip:
                    # NO fallback, NO recycle. It keeps its current pending and
                    # is re-offered next cycle if still due.
                    declined = True
                else:
                    # Call failed entirely, or the model omitted this platform →
                    # deterministic interest-name fallback (P1.3 mirror).
                    cap = max(0, int(self._discovery.gen_batch))
                    words = self._interest_fallback(profile, cap)

            if declined:
                ledger[platform] = 0
                continue

            inspiration_platforms.append(platform)
            inserted = self._insert(platform, words, digest)
            if inserted <= 0:
                # Sparse profile: generation + fallback produced nothing new
                # for a due platform → recycle its oldest used keywords so the
                # cache does not starve.
                inserted += self._recycle(platform, needs[platform], digest)
            else:
                # P2.3 recycle-on-shortfall: the platform produced SOME new
                # words but its pending is still below the low watermark → top
                # it up from its oldest used words (no extra LLM call) so
                # variety keeps flowing. Conservative: only the remaining gap to
                # low, and never for a declined platform (handled above).
                shortfall = low - self._count_pending(platform, digest)
                if shortfall > 0:
                    inserted += self._recycle(platform, shortfall, digest)
            ledger[platform] = inserted

        if explore_request is not None and explore_domains:
            covered = _as_str_list(explore_request.get("covered_topic_groups"))
            explore_ledger = await self._run_coexist_explore(
                profile=profile,
                digest=digest,
                explore_domains=explore_domains,
                covered_topic_groups=covered,
            )
            for platform, inserted in explore_ledger.items():
                ledger[platform] = int(ledger.get(platform, 0)) + int(inserted)

        inspiration_ledger = await self._run_inspiration_stage(
            inspiration_platforms,
            profile=profile,
            digest=digest,
        )
        for platform, inserted in inspiration_ledger.items():
            ledger[platform] = int(ledger.get(platform, 0)) + int(inserted)

        self._emit_cycle_ledger(ledger, digest)
        return ledger

    async def _run_coexist_explore(
        self,
        *,
        profile: SoulProfile,
        digest: str,
        explore_domains: list[dict[str, object]],
        covered_topic_groups: list[str],
    ) -> dict[str, int]:
        """Coexist explore channel (Phase 2.3): rich axis-library generation with
        a flat-``explore_domains`` fallback.

        When inspiration search is enabled, route the due explore round through
        the cross-domain axis pipeline (``_run_explore_inspiration_stage``). If it
        degrades — LLM failure / empty output (``explore_degraded``) or it raises
        — fall back to the OLD ``_explore_domain_queries`` flatten so the explore
        pool is never left bare (the merged call already produced these domains,
        so the fallback has real data). When inspiration is disabled, take the
        flatten path directly (byte-identical to the pre-2.3 behaviour). Marks the
        explore plan exactly once, on whichever path actually inserted words.
        """

        self.last_explore_inspiration_degraded = False
        ledger: dict[str, int] = {}
        if not explore_domains:
            return ledger

        use_rich = (
            bool(getattr(self._discovery, "inspiration_search_enabled", False))
            and self._inspiration_provider is not None
        )
        if use_rich:
            try:
                explore_ledger, report = await self._run_explore_inspiration_stage(
                    [_BILIBILI],
                    profile=profile,
                    digest=digest,
                    explore_domains=explore_domains,
                    covered_topic_groups=covered_topic_groups,
                )
            except Exception:
                logger.debug("explore rich generation raised; degrading to flatten", exc_info=True)
                explore_ledger, report = {}, {"explore_degraded": True}
            degraded = bool(report.get("explore_degraded")) or not explore_ledger
            if not degraded:
                for platform, inserted in explore_ledger.items():
                    ledger[platform] = int(ledger.get(platform, 0)) + int(inserted)
                self._mark_explore_planned()
                return ledger
            # Rich generation degraded → fall through to the flat fallback so the
            # explore pool still replenishes (never bare).
            self.last_explore_inspiration_degraded = True

        explore_queries = self._explore_domain_queries(explore_domains)
        inserted = self._insert(_BILIBILI, explore_queries, digest, keyword_kind="explore")
        if inserted > 0:
            ledger[_BILIBILI] = int(ledger.get(_BILIBILI, 0)) + inserted
            self._mark_explore_planned()
        return ledger

    def _inspiration_replaces_merged_keywords(self) -> bool:
        return (
            bool(getattr(self._discovery, "inspiration_search_enabled", False))
            and bool(getattr(self._discovery, "inspiration_replace_merged_keywords", False))
            and self._inspiration_provider is not None
        )

    @staticmethod
    def _match_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    def _generation_cache_key(
        self,
        digest: str,
        blocks: list[dict[str, object]],
        explore_request: dict[str, object] | None = None,
    ) -> str:
        cache_blocks: list[dict[str, object]] = []
        for block in blocks:
            cache_blocks.append(
                {
                    "platform": str(block.get("platform", "")),
                    "need": int(cast("Any", block.get("need", 0)) or 0),
                    # The prompt already receives this list. It must also shape
                    # the cache key: omitting it made a stable profile replay the
                    # same generated batch for ``plan_ttl_hours`` even after the
                    # words had been searched and moved into recent history.
                    "recent_keywords": [
                        self._match_text(item)
                        for item in _as_str_list(block.get("recent_keywords"))
                    ],
                    "avoid_topics": _as_str_list(block.get("avoid_topics")),
                    "avoid_styles": _as_str_list(block.get("avoid_styles")),
                    "avoid_franchises": _as_str_list(block.get("avoid_franchises")),
                    "prefer_axes": _as_str_list(block.get("prefer_axes")),
                    "cold_start": bool(block.get("cold_start")),
                    "supply_hint": _as_str_list(block.get("supply_hint")),
                }
            )
        blob = json.dumps(
            {
                "digest": digest,
                "gen_batch": int(self._discovery.gen_batch),
                "blocks": cache_blocks,
                "explore_domains": explore_request or None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return blob

    def _generation_cache_ttl_seconds(self) -> float:
        return max(1.0, float(self._discovery.plan_ttl_hours) * 60.0 * 60.0)

    def _cached_generation(
        self,
        cache_key: str,
    ) -> tuple[dict[str, list[str]], set[str], list[dict[str, object]]] | None:
        cached = self._generation_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, generated, present, explore_domains = cached
        if time.monotonic() >= expires_at:
            self._generation_cache.pop(cache_key, None)
            return None
        return (
            {platform: list(words) for platform, words in generated.items()},
            set(present),
            [dict(domain) for domain in explore_domains],
        )

    def _store_generation(
        self,
        cache_key: str,
        generated: dict[str, list[str]],
        present: set[str],
        explore_domains: list[dict[str, object]] | None = None,
    ) -> None:
        self._generation_cache[cache_key] = (
            time.monotonic() + self._generation_cache_ttl_seconds(),
            {platform: list(words) for platform, words in generated.items()},
            set(present),
            [dict(domain) for domain in (explore_domains or [])],
        )

    # ── per-cycle observability ledger (P1.9) ───────────────────────────

    def _emit_cycle_ledger(
        self, generated: dict[str, int], digest: str
    ) -> dict[str, dict[str, int]]:
        """Record + log the per-platform production/yield ledger for this cycle.

        The merged generation is a **single** ``discovery.keyword_planner`` LLM
        response (P1.6), so token cost can NOT be apportioned per platform — the
        cost ledger keeps one caller. To still give operators per-platform
        visibility this structured line surfaces, for every platform generated
        this cycle, how many keywords it produced (``generated``) plus the
        platform's cumulative admit-credited ``yield`` (cheap ``SUM(yield_count)``
        via :meth:`Database.keyword_yield_total`, when available). It does NOT
        fake token-level platform attribution.

        Stored on :attr:`last_cycle_ledger` (for observability / tests) and
        emitted as one ``logger.info`` structured line. Returns the structured
        ``{platform: {"generated": n, "yield": y}}`` dict.
        """
        structured: dict[str, dict[str, int]] = {}
        for platform, count in generated.items():
            structured[platform] = {
                "generated": int(count),
                "yield": self._yield_total(platform),
            }
        self.last_cycle_ledger = structured
        if structured:
            logger.info(
                "keyword planner cycle ledger (digest=%s): %s",
                digest,
                ", ".join(
                    f"{p}=generated:{v['generated']}/yield:{v['yield']}"
                    for p, v in structured.items()
                ),
            )
        return structured

    def _yield_total(self, platform: str) -> int:
        """Cumulative admit-credited yield for a platform (0 if unavailable)."""
        getter = getattr(self._db, "keyword_yield_total", None)
        if not callable(getter):
            return 0
        try:
            return int(getter(platform))
        except Exception:
            logger.debug("keyword_yield_total lookup failed for %s", platform, exc_info=True)
            return 0

    # ── due computation ─────────────────────────────────────────────────

    def _due_platforms(self, digest: str) -> list[str]:
        low = int(self._discovery.kw_cache_low)
        due: list[str] = []
        for platform in _PLANNER_PLATFORMS:
            cache_below_low = self._count_pending(platform, digest) < low
            has_deficit = self._real_deficit(platform) > 0
            platform_due = cache_below_low and has_deficit
            if platform == _BILIBILI and not platform_due and self._bilibili_catalyst():
                platform_due = True
            if platform_due:
                due.append(platform)
        return due

    def _real_deficit(self, platform: str) -> int:
        source = self._deficit_source
        if source is None:
            return 0
        try:
            return max(0, int(source.keyword_planner_real_deficit(platform)))
        except Exception:
            logger.exception("keyword planner deficit lookup failed for %s", platform)
            return 0

    def _bilibili_catalyst(self) -> bool:
        source = self._deficit_source
        if source is None:
            return False
        try:
            return bool(source.keyword_planner_bilibili_catalyst())
        except Exception:
            logger.exception("keyword planner bilibili catalyst lookup failed")
            return False

    def _explore_domains_request(self) -> dict[str, object] | None:
        """Optional explore-domain request piggybacked on a merged call.

        Explore domain generation is tied to the refresh plan clock: only ask
        when the controller says explore is due / nearly due AND Bilibili has
        real replenishment room. The planner never fires a separate LLM call
        just for explore; this method is used only when a normal merged keyword
        call is already being built.
        """
        if self._real_deficit(_BILIBILI) <= 0:
            return None
        source = self._deficit_source
        due = getattr(source, "keyword_planner_explore_due_soon", None)
        if not callable(due):
            return None
        try:
            if not bool(due()):
                return None
        except Exception:
            logger.exception("keyword planner explore due lookup failed")
            return None

        covered: list[str] = []
        covered_getter = getattr(source, "keyword_planner_explore_covered_topic_groups", None)
        if callable(covered_getter):
            try:
                covered = [str(item).strip() for item in covered_getter() if str(item).strip()]
            except Exception:
                logger.debug("keyword planner explore covered-topic lookup failed", exc_info=True)
                covered = []
        return {
            "need_domains": 5,
            "queries_per_domain": 3,
            "covered_topic_groups": covered[:12],
        }

    @staticmethod
    def _explore_domain_queries(explore_domains: list[dict[str, object]]) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()
        for domain in explore_domains:
            raw_queries = domain.get("queries", [])
            if not isinstance(raw_queries, (list, tuple)):
                continue
            for raw in raw_queries:
                query = str(raw).strip()
                if not query or query in seen:
                    continue
                seen.add(query)
                queries.append(query)
        return queries

    def _mark_explore_planned(self) -> None:
        source = self._deficit_source
        marker = getattr(source, "keyword_planner_mark_explore_planned", None)
        if not callable(marker):
            return
        try:
            marker()
        except Exception:
            logger.debug("keyword planner explore planned marker failed", exc_info=True)

    # ── store + snapshot helpers ────────────────────────────────────────

    def _reconcile_pending_inventory(
        self,
        *,
        digest: str,
        hints_by_platform: dict[str, dict[str, object]],
    ) -> None:
        """Prepare safe usable inventory before due calculation.

        Both reconciliation and all-digest counting must be available. Any
        missing/raising capability falls back to the legacy hard-expiration +
        exact-digest count path for that platform.
        """
        reconcile = getattr(self._db, "reconcile_pending_keyword_digests", None)
        count_all = getattr(self._db, "count_pending_keywords_all_digests", None)
        grace_hours = int(getattr(self._discovery, "keyword_digest_grace_hours", 0))

        for platform in _PLANNER_PLATFORMS:
            if not callable(reconcile) or not callable(count_all):
                self._legacy_expire_pending(platform, digest)
                continue
            avoid_topics = _as_str_list(hints_by_platform.get(platform, {}).get("avoid_topics"))
            try:
                raw_ledger = reconcile(
                    platform,
                    digest,
                    grace_hours=grace_hours,
                    max_pending=self._target_high(platform),
                    # These are inventory-diversity hints derived from current
                    # pool saturation, not user dislikes. An ordinary dislike
                    # constrains recommendation output and must never revoke a
                    # pending search term.
                    blocked_terms=avoid_topics,
                    keyword_kind="regular",
                )
                # Validate the companion count while still in the reconciliation
                # phase. A partial rollout must fail back before due computation.
                int(count_all(platform, keyword_kind="regular"))
                if not isinstance(raw_ledger, Mapping):
                    raise TypeError("keyword digest reconciliation returned a non-mapping ledger")
                ledger = {
                    key: max(0, _ledger_int(raw_ledger.get(key, 0)))
                    for key in (
                        "current",
                        "reused",
                        "expired_aged",
                        "expired_blocked",
                        "expired_excess",
                    )
                }
            except Exception:
                logger.exception(
                    "keyword digest reconciliation failed for %s; using hard expiration",
                    platform,
                )
                self._legacy_expire_pending(platform, digest)
                continue
            self._grace_inventory_ready.add(platform)
            if any(ledger.values()):
                self.last_digest_grace_ledger[platform] = ledger
            if any(ledger[key] for key in ledger if key != "current"):
                logger.info(
                    "keyword digest reconciliation: platform=%s current=%d reused=%d "
                    "expired_aged=%d expired_blocked=%d expired_excess=%d",
                    platform,
                    ledger["current"],
                    ledger["reused"],
                    ledger["expired_aged"],
                    ledger["expired_blocked"],
                    ledger["expired_excess"],
                )

    def _legacy_expire_pending(self, platform: str, digest: str) -> None:
        try:
            self._db.expire_pending_by_digest(platform, digest)
        except Exception:
            logger.exception("expire_pending_by_digest failed for %s", platform)

    def _count_pending(self, platform: str, digest: str) -> int:
        if platform in self._grace_inventory_ready:
            try:
                return int(
                    self._db.count_pending_keywords_all_digests(
                        platform,
                        keyword_kind="regular",
                    )
                )
            except Exception:
                logger.exception(
                    "all-digest pending count failed for %s; using hard expiration",
                    platform,
                )
                self._grace_inventory_ready.discard(platform)
                self._legacy_expire_pending(platform, digest)
        try:
            return int(self._db.count_pending_keywords(platform, digest))
        except Exception:
            logger.exception("count_pending_keywords failed for %s", platform)
            return 0

    def _history(self, platform: str, *, keyword_kind: str = "regular") -> list[str]:
        include_pending = (
            keyword_kind == "regular"
            and platform in self._grace_inventory_ready
            and int(getattr(self._discovery, "keyword_digest_grace_hours", 0)) > 0
        )
        try:
            return list(
                self._db.history_keywords(
                    platform,
                    int(self._discovery.history_window_size),
                    float(self._discovery.history_window_hours),
                    keyword_kind=keyword_kind,
                    include_pending=include_pending,
                )
            )
        except TypeError:
            if keyword_kind != "regular":
                return []
            return list(
                self._db.history_keywords(
                    platform,
                    int(self._discovery.history_window_size),
                    float(self._discovery.history_window_hours),
                )
            )
        except Exception:
            logger.exception("history_keywords failed for %s", platform)
            return []

    def _insert(
        self,
        platform: str,
        words: list[str],
        digest: str,
        *,
        keyword_kind: str = "regular",
        metadata_by_keyword: dict[str, dict[str, object]] | None = None,
    ) -> int:
        if not words:
            return 0
        filtered = self._novel_keywords(
            platform,
            words,
            keyword_kind=keyword_kind,
        )
        if not filtered:
            return 0
        try:
            return int(
                self._db.insert_pending_keywords(
                    platform,
                    filtered,
                    digest,
                    keyword_kind=keyword_kind,
                    metadata_by_keyword=metadata_by_keyword,
                )
            )
        except TypeError:
            if keyword_kind != "regular":
                return 0
            return int(self._db.insert_pending_keywords(platform, filtered, digest))
        except Exception:
            logger.exception("insert_pending_keywords failed for %s", platform)
            return 0

    def _novel_keywords(
        self,
        platform: str,
        words: Sequence[str],
        *,
        keyword_kind: str = "regular",
    ) -> list[str]:
        """Remove exact and suffix-only variants of recently consumed queries."""

        recent = {
            self._keyword_family_key(item)
            for item in self._history(platform, keyword_kind=keyword_kind)
            if self._keyword_family_key(item)
        }
        filtered: list[str] = []
        seen: set[str] = set()
        for word in words:
            key = self._keyword_family_key(word)
            if not key or key in recent or key in seen:
                continue
            seen.add(key)
            filtered.append(str(word))
        if len(filtered) != len(words):
            logger.info(
                "keyword planner novelty filter: platform=%s kind=%s input=%d kept=%d",
                platform,
                keyword_kind,
                len(words),
                len(filtered),
            )
        return filtered

    def _recycle(self, platform: str, n: int, digest: str) -> int:
        recycle = getattr(self._db, "recycle_oldest_used", None)
        if not callable(recycle) or n <= 0:
            return 0
        try:
            return int(
                recycle(
                    platform,
                    n,
                    digest,
                    min_age_hours=float(self._discovery.history_window_hours),
                )
            )
        except TypeError:
            return int(recycle(platform, n, digest))
        except Exception:
            logger.exception("recycle_oldest_used failed for %s", platform)
            return 0

    @staticmethod
    def _keyword_novelty_key(value: object) -> str:
        """Normalize formatting-only query differences for exact cooldown.

        This is deliberately conservative: it collapses case, spaces and
        punctuation but does not attempt semantic similarity, which could hide
        genuinely different long-tail searches under the same broad interest.
        """
        return re.sub(r"[\W_]+", "", str(value or "").strip().casefold())

    @classmethod
    def _keyword_family_key(cls, value: object) -> str:
        """Collapse a query plus generic trailing style markers to one family.

        The rule stays intentionally narrow: only suffixes that add no new
        search subject are stripped. Different entities or mechanisms remain
        distinct, while ``同一事件 争议`` and ``同一事件 争议 复盘`` share a
        cooldown family.
        """

        key = cls._keyword_novelty_key(value)
        if not key:
            return ""
        suffixes = (
            "深度解析",
            "技术解析",
            "原理解析",
            "复盘分析",
            "实测评测",
            "入门教程",
            "完整教程",
            "详细教程",
            "教程",
            "复盘",
            "解析",
            "分析",
            "盘点",
            "解说",
            "科普",
            "测评",
            "评测",
            "实测",
            "推荐",
            "攻略",
            "教学",
            "explained",
            "tutorial",
            "analysis",
            "review",
            "guide",
            "tips",
        )
        changed = True
        while changed:
            changed = False
            for suffix in suffixes:
                if key.endswith(suffix) and len(key) > len(suffix):
                    key = key[: -len(suffix)]
                    changed = True
                    break
        return key

    def _avoid_hints(self, profile: SoulProfile | None = None) -> dict[str, dict[str, object]]:
        """Per-platform topic avoid + global style/franchise avoid (P3.1).

        P1/P2 fed every platform the GLOBAL avoid, which over-avoids — a topic
        saturated on B站 may be absent on 小红书. P3.1 gives each platform its
        OWN saturated topics (relative to that platform's own pool); styles and
        franchises stay global (coarser, less platform-specific). A platform
        with too little of its own pool falls back to the global topic avoid.
        Empty-pool cold start falls back to profile-derived soft diversity hints
        so every platform's first keyword batch does not collapse onto the same
        top-weighted interest.
        """
        hints: dict[str, object] = {}
        try:
            snapshot = build_pool_distribution_snapshot(
                self._db,
                pool_target_count=self._resolved_pool_target(),
                source_targets=self._source_targets(),
            )
            if profile is not None and int(snapshot.pool_available_count) <= 0:
                cold_snapshot = build_cold_start_pool_snapshot(
                    profile,
                    pool_target_count=self._resolved_pool_target(),
                    source_targets=self._source_targets(),
                )
                if cold_snapshot is not None:
                    snapshot = cold_snapshot
            hints = snapshot.to_prompt_hints()
        except Exception:
            logger.exception("keyword planner failed to build pool distribution snapshot")
        global_topics = _as_str_list(hints.get("avoid_topics"))
        shared_styles = _as_str_list(hints.get("avoid_styles"))
        shared_franchises = _as_str_list(hints.get("avoid_franchises"))
        shared_prefer_axes = _as_str_list(hints.get("prefer_axes"))
        cold_start = bool(hints.get("cold_start"))

        per_platform: dict[str, dict[str, int]] = {}
        getter = getattr(self._db, "get_pool_topic_counts_by_platform", None)
        if callable(getter):
            try:
                per_platform = getter()
            except Exception:
                logger.exception("keyword planner failed to read per-platform topic counts")

        result: dict[str, dict[str, object]] = {}
        for platform in _PLANNER_PLATFORMS:
            topic_counts = per_platform.get(platform, {})
            total = sum(int(count) for count in topic_counts.values())
            if total < _PER_PLATFORM_AVOID_FLOOR:
                avoid_topics = list(global_topics)
            else:
                threshold = max(
                    _PER_PLATFORM_AVOID_MIN_THRESHOLD, total // _PER_PLATFORM_AVOID_DIVISOR
                )
                avoid_topics = [
                    topic
                    for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                    if int(count) >= threshold
                ][:12]
            result[platform] = {
                "avoid_topics": avoid_topics,
                "avoid_styles": list(shared_styles),
                "avoid_franchises": list(shared_franchises),
                "prefer_axes": list(shared_prefer_axes),
                "cold_start": cold_start,
            }
        return result

    def _supply_hints(
        self, avoid_by_platform: dict[str, dict[str, object]]
    ) -> dict[str, list[str]]:
        """Per-platform data-driven supply-advantage topics (P3.3).

        The static ``<supply_advantage>`` table in the system prompt gives
        platform priors; this augments it with THIS user's actual admit
        history — the ``topic_group``s each platform has most delivered into the
        cache. The platform's current avoid set is subtracted so a topic is
        never both "lean in" and "avoid" (a saturated-now strength stays only in
        avoid this cycle). Empty until a platform has admitted
        ``_PER_PLATFORM_SUPPLY_FLOOR`` rows (cold start → static table only).
        """
        admitted: dict[str, dict[str, int]] = {}
        getter = getattr(self._db, "get_admitted_topic_counts_by_platform", None)
        if callable(getter):
            try:
                admitted = getter()
            except Exception:
                logger.exception(
                    "keyword planner failed to read per-platform admitted topic counts"
                )
        result: dict[str, list[str]] = {}
        for platform in _PLANNER_PLATFORMS:
            topic_counts = admitted.get(platform, {})
            total = sum(int(count) for count in topic_counts.values())
            if total < _PER_PLATFORM_SUPPLY_FLOOR:
                result[platform] = []
                continue
            avoid = set(_as_str_list(avoid_by_platform.get(platform, {}).get("avoid_topics")))
            threshold = max(
                _PER_PLATFORM_SUPPLY_MIN_THRESHOLD, total // _PER_PLATFORM_SUPPLY_DIVISOR
            )
            result[platform] = [
                topic
                for topic, count in sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))
                if int(count) >= threshold and topic not in avoid
            ][:_PER_PLATFORM_SUPPLY_TOP]
        return result

    def _target_high(self, platform: str) -> int:
        """P3.2 dynamic cache high-water for a platform.

        Sizes the pending target from the live search deficit ÷ the platform's
        observed average yield-per-keyword, so a low-yield platform (lots of
        duplicate hits) generates MORE words to fill the same gap and a
        high-yield one fewer. Falls back to the static ``kw_cache_high`` on cold
        start (too little yield history), when there is no deficit source, or
        when the deficit is non-positive. Clamped to ``[low+fetch_batch ..
        kw_cache_high × _DYNAMIC_HIGH_CAP_MULT]`` so the cache stays functional.
        """
        static_high = max(1, int(self._discovery.kw_cache_high))
        source = self._deficit_source
        if source is None:
            return static_high
        try:
            deficit = int(source.keyword_planner_real_deficit(platform))
        except Exception:
            return static_high
        if deficit <= 0:
            return static_high
        avg_yield = self._avg_yield(platform)
        if avg_yield <= 0.0:
            return static_high
        target = math.ceil(deficit / avg_yield)
        floor = max(1, int(self._discovery.kw_cache_low) + int(self._discovery.fetch_batch))
        cap = static_high * _DYNAMIC_HIGH_CAP_MULT
        return max(floor, min(target, cap))

    def _avg_yield(self, platform: str) -> float:
        """Observed yield-per-keyword (total yield ÷ used keywords) for a platform.

        Returns 0.0 (→ caller uses the static high) until at least
        ``_DYNAMIC_MIN_SAMPLES`` used keywords exist, so the cold-start estimate
        isn't driven by one or two noisy samples.
        """
        used = 0
        getter = getattr(self._db, "used_keyword_count", None)
        if callable(getter):
            try:
                used = int(getter(platform))
            except Exception:
                used = 0
        if used < _DYNAMIC_MIN_SAMPLES:
            return 0.0
        total = 0
        total_getter = getattr(self._db, "keyword_yield_total", None)
        if callable(total_getter):
            try:
                total = int(total_getter(platform))
            except Exception:
                total = 0
        return total / used if used > 0 else 0.0

    def _source_targets(self) -> dict[str, int]:
        source = self._deficit_source
        getter = getattr(source, "_source_target_counts", None)
        if callable(getter):
            try:
                return {str(k): int(v) for k, v in dict(getter()).items()}
            except Exception:
                logger.exception("keyword planner source-target lookup failed")
        return {}

    # ── lock ────────────────────────────────────────────────────────────

    def _acquire_lock(self, lease_seconds: float) -> bool:
        acquire = getattr(self._db, "acquire_planner_lock", None)
        if not callable(acquire):
            # No lock support → behave single-process (still safe in tests).
            return True
        try:
            return bool(acquire(self._owner, lease_seconds))
        except Exception:
            logger.exception("acquire_planner_lock failed")
            return False

    def _release_lock(self) -> None:
        release = getattr(self._db, "release_planner_lock", None)
        if not callable(release):
            return
        try:
            release(self._owner)
        except Exception:
            logger.exception("release_planner_lock failed")

    # ── profile + fallback ──────────────────────────────────────────────

    async def _load_profile(self) -> SoulProfile | None:
        if self._soul_engine is None:
            return None
        try:
            profile = await self._soul_engine.get_profile()
        except Exception as exc:
            # Missing profile is normal before the first successful init (and
            # immediately after a failed attempt). Keep the reason without an
            # INFO-level traceback that makes a clean skip look like a crash.
            logger.info("keyword planner skipped: soul profile unavailable: %s", exc)
            return None
        return cast("SoulProfile | None", profile)

    @staticmethod
    def _interest_fallback(profile: SoulProfile, count: int) -> list[str]:
        """Deterministic weight-ranked interest names (mirrors P1.3 XHS/X)."""
        if count <= 0:
            return []
        ranked = sorted(
            profile.preferences.interests,
            key=lambda tag: float(tag.weight or 0.0),
            reverse=True,
        )
        seen: set[str] = set()
        out: list[str] = []
        for tag in ranked:
            name = str(tag.name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) >= count:
                break
        return out
