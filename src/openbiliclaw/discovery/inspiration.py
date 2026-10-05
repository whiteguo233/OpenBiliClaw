"""Pure helpers for discovery-query inspiration and lateral expansion.

The live inspiration probe is intentionally kept outside this module. These
helpers make the deterministic parts testable: extract specific adjacent
terms from search previews, filter generic drift, and enforce bounded lateral
expansion depth before keywords are generated.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from openbiliclaw.soul.profile import SoulProfile


_DEFAULT_GENERIC_TERMS = {
    "ai",
    "内容",
    "好玩",
    "推荐",
    "教程",
    "攻略",
    "游戏",
    "视频",
    "热门",
    "精选",
}

_KNOWN_SLUGS = {
    "人工智能": "artificial-intelligence",
    "秩序感": "order-sense",
    "环境叙事": "environmental-narrative",
    "碎片化线索": "fragmented-clues",
}

_PLATFORM_STYLE_MARKERS: dict[str, tuple[str, ...]] = {
    "bilibili": (
        "b站",
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
    "xiaohongshu": (
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
    "douyin": ("挑战", "热点", "实拍", "reaction", "高能", "合集", "一分钟", "vlog"),
    "youtube": (
        "review",
        "analysis",
        "explained",
        "guide",
        "workflow",
        "tutorial",
        "documentary",
        "breakdown",
        "interview",
    ),
    "reddit": (
        "discussion",
        "tips",
        "workflow",
        "review",
        "analysis",
        "community",
        "explained",
    ),
    "twitter": ("thread", "debate", "reaction", "analysis", "breaking", "takeaways"),
    "zhihu": ("为什么", "如何看待", "经验", "分析", "讨论", "方法", "复盘"),
    "v2ex": ("讨论", "经验", "折腾", "求助", "实测", "方案", "开源"),
    "github": (
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
}

_ENGLISH_SCRIPT_PLATFORMS = {"youtube", "reddit", "twitter", "x"}


class LensFamily(StrEnum):
    """Canonical brainstorm lens families."""

    WORK_ENTITY = "work_entity"
    HANDS_ON = "hands_on"
    COMMUNITY_LANGUAGE = "community_language"
    CREATOR = "creator"
    METHOD = "method"
    EVENT = "event"
    ADJACENT = "adjacent"
    OTHER = "other"


@dataclass(frozen=True)
class ExaPreviewItem:
    """Search-preview fragment consumed by the inspiration extractor."""

    title: str
    url: str
    highlights: Sequence[str] = ()


@dataclass(frozen=True)
class InspirationSeed:
    """A specific adjacent concept found from search evidence."""

    inspiration_id: str
    source_terms: tuple[str, ...]
    evidence_titles: tuple[str, ...] = ()
    evidence_urls: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ProfileAspect:
    """One profile aspect used to seed inspiration search."""

    aspect_id: str
    label: str
    source: str
    weight: float = 0.0
    seed_queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecondaryInterest:
    """A positive, narrow interest selected for query brainstorming."""

    interest_id: str
    label: str
    parent: str = ""
    weight: float = 0.0
    source: str = ""
    coverage_score: float = 0.0
    low_specificity: bool = False


@dataclass(frozen=True)
class BrainstormBranch:
    """LLM-brainstormed probe branch under a selected secondary interest."""

    secondary_interest: str
    branch_id: str
    branch_label: str = ""
    lens_family: str = ""
    kind_fit: str = "both"
    probe_queries: tuple[str, ...] = ()
    expected_platform_fit: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    why_it_might_work: str = ""


@dataclass(frozen=True)
class GroundedProbe:
    """Search-grounded evidence for one brainstormed probe query."""

    secondary_interest: str
    branch_id: str
    probe_query: str
    source_terms: tuple[str, ...] = ()
    evidence_titles: tuple[str, ...] = ()
    evidence_urls: tuple[str, ...] = ()
    lens_family: str = ""
    evidence_quality: float = 0.0
    grounding_source: str = ""


@dataclass(frozen=True)
class RealizedKeyword:
    """Concrete keyword plus provenance metadata for `discovery_keywords`."""

    keyword: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class MaterializeCandidate:
    """Candidate keyword concept emitted by inspiration curation."""

    interest: str
    axis_label: str
    platform: str
    core_concept: str
    decoration: str
    recency_sensitivity: str
    origin: str
    # Real ``discovery_inspiration_axis.axis_id`` when the concept traces back to
    # a library axis (deterministic fill / an LLM ref that resolved to an axis).
    # Empty when unknown — the realize path derives it from interest + label.
    axis_id: str = ""


@dataclass(frozen=True)
class AllocationTarget:
    """Per-interest materialization target across platforms and axes."""

    platforms: tuple[str, ...]
    min_axes: int


def platform_style_score(keyword: str, platform: str) -> float:
    """Return a soft platform-style ranking score."""

    text = _normalize_match_text(keyword)
    markers = _PLATFORM_STYLE_MARKERS.get(_normalize_platform(platform), ())
    if not text or not markers:
        return 0.0
    score = 0.0
    for marker in markers:
        marker_text = marker.casefold()
        if marker_text not in text:
            continue
        score += 0.35 if marker_text in {"解析", "analysis", "discussion"} else 0.25
    return min(1.0, score)


# Generic / style marker vocabulary the specificity check strips off a
# core_concept before deciding whether a real anchor remains. Reuses the
# assembler's own platform-style markers (`_PLATFORM_STYLE_MARKERS`) plus the
# extra topic-level fillers named in the richness spec (§F1.5).
_SPECIFICITY_EXTRA_MARKERS: tuple[str, ...] = (
    "盘点",
    "推荐",
    "资讯",
    "速看",
    "合集",
    "攻略",
    "测评",
    "解析",
    "科普",
    "避坑",
    "亲测",
    "清单",
    "如何",
    "评价",
    "原理",
    "discussion",
    "review",
    "explained",
    "recommendation",
    "tips",
)
# Normalized inline (``strip().casefold()``) rather than via
# ``_normalize_match_text`` because this module-level constant is evaluated
# before that helper is defined; markers are whitespace-free tokens so the two
# agree. ``is_specific`` normalizes the per-call core_concept / spans with the
# real helper at call time.
_SPECIFICITY_MARKER_TERMS: frozenset[str] = frozenset(
    normalized
    for marker in (
        *(term for markers in _PLATFORM_STYLE_MARKERS.values() for term in markers),
        *_SPECIFICITY_EXTRA_MARKERS,
    )
    if (normalized := str(marker).strip().casefold())
)


def is_specific(core_concept: object, interest: object, axis_label: object) -> bool:
    """Whether a ``core_concept`` anchors on a real entity vs restates the topic.

    Strip-residual by *substring span*, not whitespace tokens (CJK keywords are
    often space-free, so token-equality would miss removals — richness spec
    §F1.5, Codex R3 CJK fix): from the normalized ``core_concept`` string,
    remove the ``interest`` span, the ``axis_label`` span, and every generic /
    style marker, longest-first as substrings; strip residual whitespace /
    punctuation; a non-empty remainder means a genuine anchor survives
    (``True``), an empty remainder is a topic-name + filler restatement
    (``False``). A marker that equals the whole core_concept therefore strips to
    empty → ``False`` (a bare filler word is never specific).
    """

    text = _normalize_match_text(core_concept)
    if not text:
        return False
    spans: set[str] = set(_SPECIFICITY_MARKER_TERMS)
    for value in (interest, axis_label):
        normalized = _normalize_match_text(value)
        if normalized:
            spans.add(normalized)
    for span in sorted(spans, key=len, reverse=True):
        text = text.replace(span, " ")
    remainder = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return bool(remainder)


def restatement_rate(keywords: Sequence[RealizedKeyword]) -> float:
    """Fraction of realized keywords that merely restate their interest / axis.

    Deterministic richness metric (Spec AC5): a keyword counts as a restatement
    when :func:`is_specific` finds no anchor left after stripping the interest,
    axis_label and generic markers. ``0.0`` for an empty input.
    """

    items = list(keywords)
    if not items:
        return 0.0
    restated = sum(
        0
        if is_specific(
            item.keyword,
            item.metadata.get("source_interest"),
            item.metadata.get("axis_label"),
        )
        else 1
        for item in items
    )
    return restated / len(items)


def materialize_platform_keywords(
    candidates: Sequence[MaterializeCandidate],
    allocation: Mapping[str, AllocationTarget],
    *,
    axes: Sequence[AxisRow] = (),
    max_keywords_per_platform: int = 12,
    max_keyword_chars: int = 48,
) -> tuple[list[RealizedKeyword], dict[str, object]]:
    """Materialize platform keywords with coverage-first deterministic allocation."""

    platform_cap = max(0, int(max_keywords_per_platform))
    char_cap = max(1, int(max_keyword_chars))
    telemetry: dict[str, object] = {
        "axis_coverage": {},
        "soft_score_distribution": {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0},
        "hard_gate_rejects": [],
        "deterministic_fill_count": 0,
        "coverage_shortfall": [],
    }
    if platform_cap <= 0:
        return [], telemetry

    axis_lookup = _materialize_axes_by_interest(axes)
    target_lookup = _normalized_allocation(allocation)
    allowed_slots = {
        (interest_key, _normalize_platform(platform))
        for interest_key, target in target_lookup.items()
        for platform in target.platforms
        if _normalize_platform(platform)
    }
    valid_candidates: list[_ScoredMaterializeCandidate] = []
    seen_keywords: set[tuple[str, str]] = set()
    rejects = _telemetry_list(telemetry, "hard_gate_rejects")
    script_mismatch_slots: dict[tuple[str, str], str] = {}
    scores: list[float] = []
    deterministic_fill_count = 0

    for index, candidate in enumerate(candidates):
        interest = _clean_term(candidate.interest)
        platform = _normalize_platform(candidate.platform)
        interest_key = _normalize_match_text(interest)
        if not interest_key or (interest_key, platform) not in allowed_slots:
            continue
        keyword = _assemble_materialize_keyword(candidate, max_keyword_chars=char_cap)
        reason = _materialize_hard_gate_reason(keyword, platform)
        keyword_key = (platform, _normalize_match_text(keyword))
        if not reason and keyword_key in seen_keywords:
            reason = "duplicate_keyword"
        if reason:
            rejects.append({"keyword": keyword, "platform": platform, "reason": reason})
            if reason == "script_mismatch":
                script_mismatch_slots[(interest_key, platform)] = interest
            continue
        seen_keywords.add(keyword_key)
        score = platform_style_score(keyword, platform)
        scores.append(score)
        valid_candidates.append(
            _ScoredMaterializeCandidate(
                candidate=candidate,
                keyword=keyword,
                platform=platform,
                interest=interest,
                interest_key=interest_key,
                axis_label=_clean_term(candidate.axis_label),
                score=score,
                index=index,
            )
        )

    selected: list[_ScoredMaterializeCandidate] = []
    selected_keys: set[tuple[str, str]] = set()
    platform_counts: defaultdict[str, int] = defaultdict(int)
    coverage: defaultdict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"axes": set(), "platforms": set()}
    )
    slot_coverage: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    candidates_by_slot: defaultdict[tuple[str, str], list[_ScoredMaterializeCandidate]] = (
        defaultdict(list)
    )
    for scored in valid_candidates:
        candidates_by_slot[(scored.interest_key, scored.platform)].append(scored)
    for slot_candidates in candidates_by_slot.values():
        slot_candidates.sort(
            key=lambda item: (
                _axis_selected(item.axis_label, coverage[item.interest_key]["axes"]),
                item.score,
                -item.index,
            ),
            reverse=True,
        )

    # Allocate breadth before depth. The previous interest-major loop filled
    # ``min_axes`` for the first interests before visiting later ones, so a
    # four-keyword cap with four selected interests materialized only the first
    # two (two axes each). Round-robin by coverage depth gives every selected
    # interest one chance before any interest receives a second axis.
    slots = [
        (interest_key, platform, target)
        for interest_key, target in target_lookup.items()
        for platform in (_normalize_platform(raw) for raw in target.platforms)
        if platform
    ]
    max_slot_depth = max(
        (max(1, int(target.min_axes)) for _interest, _platform, target in slots),
        default=0,
    )
    for desired_depth in range(1, max_slot_depth + 1):
        for interest_key, platform, target in slots:
            if platform_counts[platform] >= platform_cap:
                continue
            slot_axes = slot_coverage[(interest_key, platform)]
            if len(slot_axes) >= desired_depth:
                continue
            slot_candidates = [
                item
                for item in candidates_by_slot.get((interest_key, platform), [])
                if (item.platform, _normalize_match_text(item.keyword)) not in selected_keys
            ]
            chosen = _choose_materialize_candidate(
                slot_candidates,
                slot_axes,
                min_axes=desired_depth,
            )
            if chosen is None:
                chosen = _deterministic_fill_candidate(
                    interest_key=interest_key,
                    platform=platform,
                    allocation=target,
                    axis_lookup=axis_lookup,
                    coverage_axes=slot_axes,
                    selected_keys=selected_keys,
                    max_keyword_chars=char_cap,
                    rejects=rejects,
                    shortfalls=_telemetry_list(telemetry, "coverage_shortfall"),
                )
                if chosen is None:
                    continue
                deterministic_fill_count += 1
                scores.append(chosen.score)
            _append_materialize_selection(
                chosen,
                selected=selected,
                selected_keys=selected_keys,
                platform_counts=platform_counts,
                coverage=coverage,
                slot_coverage=slot_coverage,
            )

    _record_materialize_shortfalls(
        target_lookup,
        slot_coverage,
        axis_lookup,
        script_mismatch_slots=script_mismatch_slots,
        telemetry=_telemetry_list(telemetry, "coverage_shortfall"),
    )
    telemetry["axis_coverage"] = _materialize_axis_coverage(coverage)
    telemetry["soft_score_distribution"] = _score_distribution(scores)
    telemetry["deterministic_fill_count"] = deterministic_fill_count
    return [_realized_from_materialize(item) for item in selected], telemetry


@dataclass(frozen=True)
class _ScoredMaterializeCandidate:
    candidate: MaterializeCandidate
    keyword: str
    platform: str
    interest: str
    interest_key: str
    axis_label: str
    score: float
    index: int


def _normalize_platform(value: object) -> str:
    platform = _normalize_match_text(value).replace("-", "_")
    if platform == "twitter":
        return "x"
    return platform


def _normalized_allocation(
    allocation: Mapping[str, AllocationTarget],
) -> dict[str, AllocationTarget]:
    result: dict[str, AllocationTarget] = {}
    for interest, target in allocation.items():
        interest_label = _clean_term(interest)
        interest_key = _normalize_match_text(interest_label)
        if not interest_key:
            continue
        result[interest_key] = AllocationTarget(
            platforms=tuple(
                platform
                for platform in (_normalize_platform(raw) for raw in target.platforms)
                if platform
            ),
            min_axes=max(0, int(target.min_axes)),
        )
    return result


def _materialize_axes_by_interest(
    axes: Sequence[AxisRow],
) -> dict[str, list[AxisRow]]:
    result: dict[str, list[AxisRow]] = {}
    for axis in axes:
        interest_key = _normalize_match_text(axis.interest_label)
        if not interest_key or not axis.axis_label:
            continue
        result.setdefault(interest_key, []).append(axis)
    return result


def _assemble_materialize_keyword(
    candidate: MaterializeCandidate,
    *,
    max_keyword_chars: int,
) -> str:
    core = _strip_literal_years(_clean_term(candidate.core_concept))
    decoration = _strip_literal_years(_clean_term(candidate.decoration))
    if not core or not decoration:
        return core
    tokens = decoration.split()
    appended = core
    appended_count = 0
    for token in tokens:
        next_text = f"{appended} {token}".strip()
        if len(next_text) > max_keyword_chars:
            break
        appended = next_text
        appended_count += 1
    if len(tokens) > 1 and appended_count < 2:
        return core
    return appended


def _strip_literal_years(value: str) -> str:
    return re.sub(r"\b20[0-9]{2}\b|20[0-9]{2}年", "", value).strip()


def _materialize_hard_gate_reason(keyword: str, platform: str) -> str:
    text = _clean_term(keyword)
    if not text:
        return "empty_keyword"
    if "http://" in text or "https://" in text:
        return "url_keyword"
    if len(text) > 48:
        return "keyword_too_long"
    if _script_mismatch(text, platform):
        return "script_mismatch"
    return ""


def _script_mismatch(keyword: str, platform: str) -> bool:
    if platform not in _ENGLISH_SCRIPT_PLATFORMS:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", keyword)) and not re.search(r"[A-Za-z]", keyword)


def _axis_selected(axis_label: str, selected_axes: set[str]) -> int:
    return 0 if _normalize_match_text(axis_label) in selected_axes else 1


def _choose_materialize_candidate(
    candidates: list[_ScoredMaterializeCandidate],
    selected_axes: set[str],
    *,
    min_axes: int,
) -> _ScoredMaterializeCandidate | None:
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            len(selected_axes) < min_axes
            and _normalize_match_text(item.axis_label) not in selected_axes,
            is_specific(item.candidate.core_concept, item.interest, item.axis_label),
            item.score,
            -item.index,
        ),
        reverse=True,
    )
    return candidates[0]


def _deterministic_fill_candidate(
    *,
    interest_key: str,
    platform: str,
    allocation: AllocationTarget,
    axis_lookup: Mapping[str, Sequence[AxisRow]],
    coverage_axes: set[str],
    selected_keys: set[tuple[str, str]],
    max_keyword_chars: int,
    rejects: list[dict[str, object]],
    shortfalls: list[dict[str, object]],
) -> _ScoredMaterializeCandidate | None:
    axes = list(axis_lookup.get(interest_key, ()))
    if not axes:
        return None
    axes.sort(
        key=lambda axis: (
            _normalize_match_text(axis.axis_label) in coverage_axes,
            axis.axis_label,
        )
    )
    script_rejects: list[tuple[str, str]] = []
    for axis in axes:
        axis_label = _clean_term(axis.axis_label)
        if not axis_label:
            continue
        terms = tuple(axis.example_terms) or (axis_label,)
        for term in terms:
            keyword = _clean_term(f"{axis.interest_label} {term}")
            keyword = _fit_materialize_keyword(keyword, max_keyword_chars=max_keyword_chars)
            key = (platform, _normalize_match_text(keyword))
            if key in selected_keys:
                continue
            reason = _materialize_hard_gate_reason(keyword, platform)
            if reason == "script_mismatch":
                script_rejects.append((keyword, axis_label))
                continue
            if reason:
                rejects.append({"keyword": keyword, "platform": platform, "reason": reason})
                continue
            return _ScoredMaterializeCandidate(
                candidate=MaterializeCandidate(
                    interest=axis.interest_label,
                    axis_label=axis_label,
                    platform=platform,
                    core_concept=keyword,
                    decoration="",
                    recency_sensitivity="low",
                    origin="deterministic_fill",
                    axis_id=axis.axis_id,
                ),
                keyword=keyword,
                platform=platform,
                interest=axis.interest_label,
                interest_key=interest_key,
                axis_label=axis_label,
                score=platform_style_score(keyword, platform),
                index=10_000,
            )
    if script_rejects:
        missing_axes = [
            axis.axis_label
            for axis in axes
            if _normalize_match_text(axis.axis_label) not in coverage_axes
        ] or [axes[0].axis_label]
        shortfalls.append(
            {
                "interest": axes[0].interest_label,
                "platform": platform,
                "reason": "script_mismatch",
                "missing_axes": missing_axes[: max(1, int(allocation.min_axes))],
                "missing_platforms": [platform],
            }
        )
        return None
    shortfalls.append(
        {
            "interest": axes[0].interest_label,
            "platform": platform,
            "reason": "missing_axes",
            "missing_axes": max(1, int(allocation.min_axes) - len(coverage_axes)),
            "missing_platforms": [platform],
        }
    )
    return None


def _fit_materialize_keyword(keyword: str, *, max_keyword_chars: int) -> str:
    text = _clean_term(keyword)
    if len(text) <= max_keyword_chars:
        return text
    kept: list[str] = []
    for token in text.split():
        candidate = " ".join([*kept, token]).strip()
        if len(candidate) > max_keyword_chars:
            break
        kept.append(token)
    return " ".join(kept).strip() or text[:max_keyword_chars].rstrip()


def _append_materialize_selection(
    item: _ScoredMaterializeCandidate,
    *,
    selected: list[_ScoredMaterializeCandidate],
    selected_keys: set[tuple[str, str]],
    platform_counts: defaultdict[str, int],
    coverage: defaultdict[str, dict[str, set[str]]],
    slot_coverage: defaultdict[tuple[str, str], set[str]],
) -> None:
    selected.append(item)
    selected_keys.add((item.platform, _normalize_match_text(item.keyword)))
    platform_counts[item.platform] += 1
    axis_key = _normalize_match_text(item.axis_label)
    if axis_key:
        coverage[item.interest_key]["axes"].add(axis_key)
        slot_coverage[(item.interest_key, item.platform)].add(axis_key)
    coverage[item.interest_key]["platforms"].add(item.platform)


def _record_materialize_shortfalls(
    allocation: Mapping[str, AllocationTarget],
    slot_coverage: Mapping[tuple[str, str], set[str]],
    axis_lookup: Mapping[str, Sequence[AxisRow]],
    *,
    script_mismatch_slots: Mapping[tuple[str, str], str],
    telemetry: list[dict[str, object]],
) -> None:
    seen = {
        (
            str(item.get("interest") or ""),
            str(item.get("platform") or ""),
        )
        for item in telemetry
    }
    for interest_key, target in allocation.items():
        axes = axis_lookup.get(interest_key, ())
        interest = axes[0].interest_label if axes else _fallback_interest_label(interest_key)
        for platform in target.platforms:
            platform_name = _normalize_platform(platform)
            covered_axes = slot_coverage.get((interest_key, platform_name), set())
            missing_axes_count = max(0, int(target.min_axes) - len(covered_axes))
            if missing_axes_count <= 0:
                continue
            key = (interest, platform_name)
            if key in seen:
                continue
            if (interest_key, platform_name) in script_mismatch_slots and not covered_axes:
                telemetry.append(
                    {
                        "interest": script_mismatch_slots[(interest_key, platform_name)],
                        "platform": platform_name,
                        "reason": "script_mismatch",
                        "missing_axes": missing_axes_count,
                        "missing_platforms": [platform_name],
                    }
                )
                continue
            telemetry.append(
                {
                    "interest": interest,
                    "platform": platform_name,
                    "reason": "missing_axes",
                    "missing_axes": missing_axes_count,
                    "missing_platforms": [platform_name],
                }
            )


def _fallback_interest_label(interest_key: str) -> str:
    return interest_key


def _materialize_axis_coverage(
    coverage: Mapping[str, dict[str, set[str]]],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for interest_key, values in coverage.items():
        axes = sorted(values.get("axes", set()))
        platforms = sorted(values.get("platforms", set()))
        result[interest_key] = {"count": len(axes), "axes": axes, "platforms": platforms}
    return result


def _score_distribution(scores: Sequence[float]) -> dict[str, float | int]:
    if not scores:
        return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0}
    total = sum(scores)
    return {
        "count": len(scores),
        "min": min(scores),
        "max": max(scores),
        "mean": total / len(scores),
    }


def _realized_from_materialize(item: _ScoredMaterializeCandidate) -> RealizedKeyword:
    return RealizedKeyword(
        keyword=item.keyword,
        metadata={
            "source_interest": item.interest,
            "source_domain": item.platform,
            "axis_label": item.axis_label,
            "axis_id": item.candidate.axis_id,
            # F3 (observation-only): carry the source concept + decoration for
            # richness diagnostics. Does NOT change the assembled ``keyword``.
            # Deterministic-fill candidates carry their template core + "".
            "core_concept": item.candidate.core_concept,
            "decoration": item.candidate.decoration,
            "origin": item.candidate.origin,
            "recency_sensitivity": item.candidate.recency_sensitivity,
            "platform_style_score": item.score,
            "normalized_keyword": _normalize_match_text(item.keyword),
        },
    )


def _telemetry_list(telemetry: dict[str, object], key: str) -> list[dict[str, object]]:
    value = telemetry.get(key)
    if isinstance(value, list):
        return value
    result: list[dict[str, object]] = []
    telemetry[key] = result
    return result


def build_grounding_probes(
    selected_interests: Sequence[SecondaryInterest],
    axes: Sequence[AxisRow],
    pooled_terms: Mapping[str, Sequence[str]] | Sequence[str],
    *,
    limit: int,
) -> list[BrainstormBranch]:
    """Build deterministic search probes from selected interests and reusable axes."""

    cap = max(0, int(limit))
    if cap <= 0:
        return []

    axes_by_interest: dict[str, list[AxisRow]] = {}
    for axis in axes:
        key = _normalize_match_text(axis.interest_label)
        if not key:
            continue
        axes_by_interest.setdefault(key, []).append(axis)

    result: list[BrainstormBranch] = []
    seen_queries: set[str] = set()

    def append_probe(
        interest: SecondaryInterest,
        query: str,
        *,
        branch_label: str,
        lens_family: str = LensFamily.OTHER.value,
    ) -> bool:
        cleaned_query = _clean_term(query)
        normalized_query = _normalize_match_text(cleaned_query)
        if not cleaned_query or normalized_query in seen_queries:
            return False
        seen_queries.add(normalized_query)
        result.append(
            BrainstormBranch(
                secondary_interest=interest.label,
                branch_id=f"grounding-{_slug_for_term(f'{interest.label}-{cleaned_query}')}",
                branch_label=branch_label,
                lens_family=lens_family,
                kind_fit="both",
                probe_queries=(cleaned_query,),
            )
        )
        return len(result) >= cap

    for interest in selected_interests:
        interest_label = _clean_term(interest.label)
        if not interest_label:
            continue
        if append_probe(interest, interest_label, branch_label=interest_label):
            break
        interest_axes = axes_by_interest.get(_normalize_match_text(interest_label), [])
        for axis in interest_axes:
            axis_label = _clean_term(axis.axis_label)
            axis_lens = normalize_lens_family(axis.axis_kind)
            if axis_label and append_probe(
                interest,
                f"{interest_label} {axis_label}",
                branch_label=axis_label,
                lens_family=axis_lens,
            ):
                return result
            for example_term in axis.example_terms:
                cleaned_term = _clean_term(example_term)
                if cleaned_term and append_probe(
                    interest,
                    f"{interest_label} {cleaned_term}",
                    branch_label=cleaned_term,
                    lens_family=axis_lens,
                ):
                    return result
        for pooled_term in _pooled_terms_for_interest(pooled_terms, interest_label):
            if append_probe(
                interest,
                f"{interest_label} {pooled_term}",
                branch_label=pooled_term,
            ):
                return result
    return result


def derive_inspiration_axis_id(interest_label: object, axis_label: object) -> str:
    """Return the stable storage id for an inspiration axis."""

    seed = f"{str(interest_label or '').strip()}\0{_normalize_axis_label_for_id(axis_label)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    return f"axis:{digest}"


def _normalize_axis_label_for_id(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


@dataclass(frozen=True)
class AxisRow:
    """Persistent inspiration axis carried between keyword-inspiration rounds."""

    interest_label: str
    axis_label: str
    axis_kind: str
    source: str
    axis_id: str = ""
    interest_id: str = ""
    example_terms: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    time_sensitive: bool = False
    freshness_ttl_days: int | None = None
    yield_score: float = 0.0
    admissions: int = 0
    use_count: int = 0
    status: str = "active"
    created_at: str = ""
    last_used_at: str | None = None
    last_refreshed_at: str | None = None

    def __post_init__(self) -> None:
        clean_interest = _clean_term(self.interest_label)
        clean_label = _clean_term(self.axis_label)
        object.__setattr__(self, "interest_label", clean_interest)
        object.__setattr__(self, "axis_label", clean_label)
        object.__setattr__(self, "axis_kind", _clean_term(self.axis_kind) or "other")
        object.__setattr__(self, "source", _clean_term(self.source) or "external_search")
        object.__setattr__(self, "interest_id", _clean_term(self.interest_id))
        object.__setattr__(
            self,
            "axis_id",
            _clean_term(self.axis_id) or derive_inspiration_axis_id(clean_interest, clean_label),
        )
        object.__setattr__(self, "example_terms", tuple(_clean_unique_values(self.example_terms)))
        object.__setattr__(self, "evidence_refs", tuple(_clean_unique_values(self.evidence_refs)))
        object.__setattr__(self, "time_sensitive", bool(self.time_sensitive))
        object.__setattr__(self, "yield_score", max(0.0, float(self.yield_score)))
        object.__setattr__(self, "admissions", max(0, int(self.admissions)))
        object.__setattr__(self, "use_count", max(0, int(self.use_count)))
        object.__setattr__(self, "status", _clean_term(self.status) or "active")
        object.__setattr__(self, "created_at", _clean_term(self.created_at))
        object.__setattr__(self, "last_used_at", _clean_optional_text(self.last_used_at))
        object.__setattr__(
            self,
            "last_refreshed_at",
            _clean_optional_text(self.last_refreshed_at) or _clean_term(self.created_at),
        )


def _clean_unique_values(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_term(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _clean_optional_text(value: object) -> str | None:
    text = _clean_term(value)
    return text or None


def build_like_secondary_interest_window(
    profile: SoulProfile,
    *,
    coverage_snapshot: dict[str, dict[str, object]] | None = None,
    max_interests: int,
) -> list[SecondaryInterest]:
    """Select positive secondary interests, penalizing already-covered ones."""

    limit = max(0, int(max_interests))
    if limit <= 0:
        return []
    coverage = {
        _normalize_match_text(key): value
        for key, value in (coverage_snapshot or {}).items()
        if _normalize_match_text(key) and isinstance(value, dict)
    }
    disliked = _normalized_terms(_profile_disliked_terms(profile))
    interests = _positive_secondary_interest_candidates(profile)
    result: list[SecondaryInterest] = []
    seen: set[str] = set()
    for label, parent, weight, source, low_specificity in interests:
        if not label:
            continue
        norm = _normalize_match_text(label)
        if norm in seen or norm in disliked:
            continue
        seen.add(norm)
        score_weight = weight * (0.45 if low_specificity else 1.0)
        coverage_score = _secondary_interest_score(
            weight=score_weight,
            source=source,
            coverage=coverage.get(norm, {}),
        )
        result.append(
            SecondaryInterest(
                interest_id=f"interest:{_slug_for_term(label)}",
                label=label,
                parent=parent,
                weight=weight,
                source=source,
                coverage_score=coverage_score,
                low_specificity=low_specificity,
            )
        )
    return _select_diverse_secondary_interests(result, limit)


def _positive_secondary_interest_candidates(
    profile: object,
) -> list[tuple[str, str, float, str, bool]]:
    """Return positive interest candidates as label, parent, weight, source, low-specificity."""

    onion_candidates = _onion_like_secondary_interest_candidates(profile)
    if onion_candidates:
        return onion_candidates

    preferences = getattr(profile, "preferences", None)
    result: list[tuple[str, str, float, str, bool]] = []
    for interest in getattr(preferences, "interests", []) or []:
        label = _clean_term(getattr(interest, "name", ""))
        if not label:
            continue
        parent = _clean_term(getattr(interest, "category", ""))
        source = _clean_term(getattr(interest, "source", ""))
        weight = _safe_float(getattr(interest, "weight", 0.0))
        low_specificity = bool(
            parent and _normalize_match_text(parent) == _normalize_match_text(label)
        )
        result.append((label, parent, weight, source, low_specificity))
    return result


def _onion_like_secondary_interest_candidates(
    profile: object,
) -> list[tuple[str, str, float, str, bool]]:
    interest_layer = getattr(profile, "interest", None)
    likes = getattr(interest_layer, "likes", []) or []
    result: list[tuple[str, str, float, str, bool]] = []
    for domain in likes:
        parent = _clean_term(getattr(domain, "domain", ""))
        if not parent:
            continue
        source = _positive_like_source(getattr(domain, "source", ""))
        domain_weight = _safe_float(getattr(domain, "weight", 0.0))
        valid_specific_count = 0
        for specific in getattr(domain, "specifics", []) or []:
            label = _clean_term(getattr(specific, "name", ""))
            if not label:
                continue
            if _normalize_match_text(label) == _normalize_match_text(parent):
                continue
            specific_weight = _safe_float(getattr(specific, "weight", 0.0)) or domain_weight
            blended_weight = (domain_weight * 0.65) + (specific_weight * 0.35)
            result.append((label, parent, blended_weight, source, False))
            valid_specific_count += 1
        if valid_specific_count <= 0:
            result.append((parent, parent, domain_weight, source, True))
    return result


def _profile_disliked_terms(profile: object) -> list[str]:
    result: list[str] = list(
        getattr(getattr(profile, "preferences", None), "disliked_topics", []) or []
    )
    interest_layer = getattr(profile, "interest", None)
    for domain in getattr(interest_layer, "dislikes", []) or []:
        label = _clean_term(getattr(domain, "domain", ""))
        if label:
            result.append(label)
        for specific in getattr(domain, "specifics", []) or []:
            specific_label = _clean_term(getattr(specific, "name", ""))
            if specific_label:
                result.append(specific_label)
    return result


def _positive_like_source(source: object) -> str:
    cleaned = _clean_term(source)
    return f"like:{cleaned}" if cleaned else "like"


def _select_diverse_secondary_interests(
    interests: list[SecondaryInterest],
    limit: int,
) -> list[SecondaryInterest]:
    selected: list[SecondaryInterest] = []
    remaining = list(interests)
    parent_counts: dict[str, int] = {}
    while remaining and len(selected) < limit:
        remaining.sort(
            key=lambda item: (
                -_parent_adjusted_interest_score(item, parent_counts),
                -item.coverage_score,
                -item.weight,
                item.label,
            )
        )
        item = remaining.pop(0)
        selected.append(item)
        parent_key = _secondary_interest_parent_key(item)
        if parent_key:
            parent_counts[parent_key] = parent_counts.get(parent_key, 0) + 1
    return selected


def _parent_adjusted_interest_score(
    item: SecondaryInterest,
    parent_counts: dict[str, int],
) -> float:
    parent_key = _secondary_interest_parent_key(item)
    count = parent_counts.get(parent_key, 0) if parent_key else 0
    return float(item.coverage_score) / float((1.0 + count) ** 1.25)


def _secondary_interest_parent_key(item: SecondaryInterest) -> str:
    return _normalize_match_text(item.parent or item.label)


def derive_inspiration_seeds(
    seed_query: str,
    preview_items: Sequence[ExaPreviewItem],
    *,
    max_seeds: int,
    generic_terms: Iterable[str] | None = None,
) -> list[InspirationSeed]:
    """Extract bounded inspiration seeds from search preview snippets.

    The function favors terms surfaced directly by search previews instead of
    inventing new concepts locally. Very broad terms and exact seed-query
    fragments are filtered so the downstream query planner does not collapse
    back to high-frequency words.
    """

    limit = max(0, int(max_seeds))
    if limit <= 0:
        return []
    blocked = _normalized_terms((*_DEFAULT_GENERIC_TERMS, *(generic_terms or ())))
    seed_parts = _normalized_terms(_split_terms(seed_query))
    blocked.update(seed_parts)

    result: list[InspirationSeed] = []
    seen: set[str] = set()
    for item in preview_items:
        for term in _seed_term_candidates(item):
            if not term:
                continue
            norm = _normalize_match_text(term)
            if norm in blocked or norm in seen:
                continue
            seen.add(norm)
            result.append(
                InspirationSeed(
                    inspiration_id=_slug_for_term(term),
                    source_terms=(term,),
                    evidence_titles=_one_nonempty(item.title),
                    evidence_urls=_one_nonempty(item.url),
                    reason="Search preview surfaced a specific adjacent term.",
                )
            )
            if len(result) >= limit:
                return result
    return result


def _seed_term_candidates(item: ExaPreviewItem) -> list[str]:
    candidates: list[str] = []
    for raw_term in item.highlights:
        term = _clean_seed_candidate(raw_term)
        if term:
            candidates.append(term)
    if candidates:
        return candidates
    title_term = _clean_seed_candidate(item.title)
    if title_term:
        candidates.append(title_term)
    return candidates


def _clean_seed_candidate(value: object) -> str:
    text = _clean_term(value)
    if not text:
        return ""
    text = re.sub(r"[-—|｜_]\s*[^-—|｜_]{1,20}$", "", text).strip()
    text = re.sub(r"^(以及|还有|并且|同时|或者|和|与|及|或)", "", text).strip()
    text = text.strip("：:。.!！?？")
    if _looks_like_preview_noise(text):
        return ""
    if len(text) > 48:
        return ""
    return text


def _looks_like_preview_noise(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", stripped):
        return True
    if "---" in stripped or stripped.startswith(("|", "｜")) or stripped.endswith(("|", "｜")):
        return True
    return len(stripped) <= 4 and stripped.endswith(("是", "为"))


def _clean_term(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


_LENS_NORMALIZATION_KEYWORDS: tuple[tuple[LensFamily, tuple[str, ...]], ...] = (
    (
        LensFamily.ADJACENT,
        (
            "adjacent",
            "one-hop",
            "one_hop",
            "lateral",
            "explore",
            "exploration",
            "相邻",
            "横向",
            "陌生",
        ),
    ),
    (LensFamily.COMMUNITY_LANGUAGE, ("community", "圈层", "社区", "黑话", "subreddit")),
    (LensFamily.CREATOR, ("creator", "expert", "作者", "up主", "博主", "创作者")),
    (LensFamily.METHOD, ("method", "workflow", "guide", "tutorial", "方法", "流程", "教程")),
    (LensFamily.HANDS_ON, ("hands_on", "hands-on", "practical", "实测", "上手", "体验")),
    (LensFamily.EVENT, ("event", "news", "update", "争议", "事件", "发布")),
    (LensFamily.WORK_ENTITY, ("work_entity", "artifact", "作品", "实体", "案例")),
)


def normalize_lens_family(value: object) -> str:
    """Normalize free-text lens labels to the canonical enum values."""

    raw = _clean_term(value)
    norm = _normalize_match_text(raw).replace(" ", "_")
    if not norm:
        return LensFamily.OTHER.value
    for family in LensFamily:
        if norm == family.value:
            return family.value
    search = norm.replace("_", " ")
    for family, keywords in _LENS_NORMALIZATION_KEYWORDS:
        if any(keyword in norm or keyword in search for keyword in keywords):
            return family.value
    return LensFamily.OTHER.value


def _as_clean_unique(value: object) -> list[str]:
    if isinstance(value, str):
        values: list[object] = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _clean_term(item)
        norm = _normalize_match_text(text)
        if not text or norm in seen:
            continue
        seen.add(norm)
        result.append(text)
    return result


def _pooled_terms_for_interest(
    pooled_terms: Mapping[str, Sequence[str]] | Sequence[str],
    interest_label: str,
) -> list[str]:
    if isinstance(pooled_terms, Mapping):
        raw_terms = pooled_terms.get(interest_label)
        if raw_terms is None:
            raw_terms = pooled_terms.get(_normalize_match_text(interest_label), ())
    else:
        raw_terms = pooled_terms
    return _as_clean_unique(raw_terms)


def _secondary_interest_score(
    *,
    weight: float,
    source: str,
    coverage: dict[str, object],
) -> float:
    generated = _safe_float(coverage.get("generated_keyword_count"))
    interest_selected = _safe_float(coverage.get("interest_selection_count"))
    selected = _safe_float(coverage.get("selected_keyword_count"))
    candidate = _safe_float(coverage.get("candidate_count"))
    admitted = _safe_float(coverage.get("admitted_count"))
    yielded = _safe_float(coverage.get("yield_count"))
    candidate_share = _safe_float(coverage.get("candidate_share"))
    admitted_share = _safe_float(coverage.get("admitted_share"))
    dominant_share = _safe_float(coverage.get("dominant_content_type_share"))
    dominant_candidate_share = _safe_float(coverage.get("dominant_candidate_content_type_share"))
    denominator = (
        (1.0 + interest_selected) ** 1.25
        * (1.0 + generated) ** 0.55
        * (1.0 + selected) ** 0.25
        * (1.0 + candidate) ** 0.25
        * (1.0 + admitted + yielded) ** 0.65
        * (1.0 + candidate_share) ** 0.8
        * (1.0 + admitted_share) ** 0.8
        * (1.0 + dominant_share) ** 0.5
        * (1.0 + dominant_candidate_share) ** 0.35
    )
    score = float(weight) * _positive_source_boost(source) / float(denominator)
    return score if score > 0.0 else 0.0


def _positive_source_boost(source: str) -> float:
    normalized = _normalize_match_text(source)
    if any(token in normalized for token in ("like", "favorite", "accepted", "card_like")):
        return 1.2
    if "profile" in normalized or "edit" in normalized:
        return 1.0
    if not normalized:
        return 0.95
    return 0.85


def _safe_float(value: object) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value if isinstance(value, (int, float, str)) else str(value))
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0.0 else 0.0


def _one_nonempty(value: object) -> tuple[str, ...]:
    text = _clean_term(value)
    return (text,) if text else ()


def _split_terms(value: object) -> list[str]:
    return [part for part in re.split(r"[\s,，、/|]+", str(value or "")) if part]


def _normalized_terms(values: Iterable[object]) -> set[str]:
    return {_normalize_match_text(value) for value in values if _normalize_match_text(value)}


def _normalize_match_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _slug_for_term(term: str) -> str:
    if term in _KNOWN_SLUGS:
        return _KNOWN_SLUGS[term]
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", term).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    digest = hashlib.sha1(term.encode("utf-8")).hexdigest()[:10]
    return f"term-{digest}"
