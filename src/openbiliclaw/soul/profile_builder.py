"""Structured initial soul-profile generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_soul_profile_prompt
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.llm.task_options import without_core_memory_kwargs

from .profile import SoulProfile
from .tone import build_tone_profile

logger = logging.getLogger(__name__)


class SupportsCoreMemoryTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        inject_core_memory: bool = True,
    ) -> LLMResponse: ...


class SoulProfileBuildError(Exception):
    """Raised when soul-profile generation fails or returns invalid data."""


# Init history sampling (2026-07-26). The profile builder used to feed the LLM
# the first N rows of history — titles[:100], contexts[:100], recent/older[:50]
# — i.e. whatever the fetch happened to return first. On a real account that is
# the newest slice only: with 1000 rows of history the model saw ~100 and every
# long-standing interest older than that window was invisible, no matter how
# strongly the user had engaged with it.
#
# Selection now mirrors what the incremental path already believes:
#   * weight  — the same satisfaction semantics preference analysis uses
#               (explicit interaction > finished watch > bounced), so a
#               collected/liked item outranks a 10-second bounce.
#   * spread  — history is bucketed by time and every bucket gets a quota, so
#               the profile covers the whole span instead of the newest tail.
# Rows without usable timestamps keep arrival order (degrade, never drop).
_HISTORY_TIME_BUCKETS = 6
# Prompt budget: ~100 rows of titles+contexts is what the previous slicing
# already cost, so this keeps token spend flat while changing *which* rows.
_HISTORY_SAMPLE_LIMIT = 100
# Share of the budget held for explicit interactions before time bucketing.
# 0.4 leaves the majority to time coverage while guaranteeing that a burst
# of collects is never scheduled away by other buckets' quotas.
_HISTORY_STRONG_RESERVE = 0.4
_STRONG_HISTORY_WEIGHT = 3.0
# Explicit acts of intent. Kept in sync with cognition_cycle's strong-signal
# notion: these are the events a user had to choose to perform.
_STRONG_HISTORY_EVENTS = frozenset(
    {"favorite", "like", "coin", "feedback", "comment", "reply", "danmaku", "collect"}
)


def _history_timestamp(item: dict[str, Any]) -> float:
    """Best-effort epoch seconds for a history row (0.0 when unknown)."""
    raw_metadata = item.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for value in (
        item.get("view_at"),
        # Favourites timestamp themselves with fav_time; without it every
        # favourite would look undated and fall out of the time bucketing that
        # keeps early behaviour represented.
        item.get("fav_time"),
        item.get("timestamp"),
        metadata.get("timestamp"),
        metadata.get("view_at"),
        metadata.get("fav_time"),
    ):
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        # Extension events arrive in epoch milliseconds; history rows in seconds.
        return number / 1000.0 if number > 1e11 else number
    return 0.0


def _history_weight(item: dict[str, Any]) -> float:
    """How representative this row is of the user, not how recent it is."""
    raw_metadata = item.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    event_type = str(item.get("event_type", "") or "").strip().lower()
    if event_type in _STRONG_HISTORY_EVENTS:
        return 3.0

    def _number(*candidates: object) -> float:
        for value in candidates:
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        return 0.0

    duration = _number(item.get("duration"), metadata.get("video_duration_seconds"))
    watched = _number(
        item.get("progress"), item.get("watch_seconds"), metadata.get("watch_seconds")
    )
    if duration <= 0 or watched <= 0:
        return 1.0  # unknown completion — treat as an ordinary view
    ratio = watched / duration
    if ratio >= 0.8:
        return 2.0
    if ratio >= 0.3:
        return 1.0
    return 0.3  # bounced: still evidence, just weak


def _sample_representative(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pick ``limit`` rows spread across time, preferring representative ones.

    Two passes, because time coverage and representativeness genuinely compete:
    a burst of collects inside one week would otherwise be crowded out by the
    per-bucket quota of every other week.

    1. Reserve up to ``_HISTORY_STRONG_RESERVE`` of the budget for explicit acts
       of intent (collect / like / coin ...). Those are rows the user chose to
       perform; losing them to a scheduling rule is the same class of bug as
       burying confusions under high-confidence hypotheses.
    2. Spend the rest bucketed by time so long-standing interests survive a
       recent binge.

    Returns rows in chronological order so the model reads a coherent stream.
    Falls back to arrival order when there is not enough time signal to bucket.
    """
    if limit <= 0 or len(items) <= limit:
        return list(items)
    dated = [(idx, item, _history_timestamp(item)) for idx, item in enumerate(items)]
    timestamps = [ts for _, _, ts in dated if ts > 0]
    if len(timestamps) < len(dated) // 2:
        return items[:limit]  # not enough time signal to bucket honestly

    picked: list[tuple[int, dict[str, Any], float]] = []
    chosen: set[int] = set()

    reserve = int(limit * _HISTORY_STRONG_RESERVE)
    by_weight = sorted(dated, key=lambda row: (-_history_weight(row[1]), row[2], row[0]))
    for row in by_weight:
        if len(picked) >= reserve or _history_weight(row[1]) < _STRONG_HISTORY_WEIGHT:
            break
        picked.append(row)
        chosen.add(id(row[1]))

    remaining = limit - len(picked)
    if remaining > 0:
        ordered = sorted(
            (row for row in dated if id(row[1]) not in chosen),
            key=lambda row: (row[2], row[0]),
        )
        span = max(1, len(ordered))
        buckets: list[list[tuple[int, dict[str, Any], float]]] = [
            [] for _ in range(_HISTORY_TIME_BUCKETS)
        ]
        for position, row in enumerate(ordered):
            index = min(_HISTORY_TIME_BUCKETS - 1, position * _HISTORY_TIME_BUCKETS // span)
            buckets[index].append(row)
        for bucket_index, bucket in enumerate(buckets):
            share = remaining // max(1, _HISTORY_TIME_BUCKETS - bucket_index)
            ranked = sorted(bucket, key=lambda row: (-_history_weight(row[1]), row[2]))
            take = ranked[:share]
            picked.extend(take)
            chosen.update(id(taken[1]) for taken in take)
            remaining -= len(take)
        if remaining > 0:  # thin buckets left budget on the table
            leftovers = sorted(
                (row for row in dated if id(row[1]) not in chosen),
                key=lambda row: (-_history_weight(row[1]), row[2]),
            )
            picked.extend(leftovers[:remaining])
    return [row[1] for row in sorted(picked, key=lambda row: (row[2], row[0]))]


@dataclass
class ProfileBuilder:
    """Generate an initial soul profile from history and preference context."""

    registry: SupportsCoreMemoryTask
    # Free-text reply-style instruction (issue #255), injected into the
    # profile prompt's tone block. Empty (default) is byte-identical.
    reply_style: str = ""

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError("ProfileBuilder requires a service with complete_structured_task().")

    async def build(
        self,
        *,
        history: list[dict[str, Any]],
        preference: dict[str, Any],
        awareness_notes: list[dict[str, Any]],
        active_insights: list[dict[str, Any]],
    ) -> SoulProfile:
        history_summary = self._summarize_history(history)
        try:
            return await self._build_from_summary(
                history_summary=history_summary,
                preference=preference,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
            )
        except SoulProfileBuildError:
            if not preference and len(history) < 100:
                raise
            logger.warning(
                "soul profile build failed; retrying with compact history summary",
                exc_info=True,
            )
            return await self._build_from_summary(
                history_summary=self._compact_history_summary(
                    history_summary,
                    original_count=len(history),
                ),
                preference=preference,
                awareness_notes=awareness_notes,
                active_insights=active_insights,
            )

    async def _build_from_summary(
        self,
        *,
        history_summary: dict[str, object],
        preference: dict[str, Any],
        awareness_notes: list[dict[str, Any]],
        active_insights: list[dict[str, Any]],
    ) -> SoulProfile:
        raw_mix = preference.get("source_platform_mix") if isinstance(preference, dict) else None
        source_mix = raw_mix if isinstance(raw_mix, dict) and raw_mix else None
        messages = build_soul_profile_prompt(
            history_summary=history_summary,
            preference_summary=preference,
            recent_awareness=awareness_notes,
            active_insights=active_insights,
            tone_profile=build_tone_profile(
                profile=None,
                preference_summary=preference,
                recent_feedback=[],
            ),
            source_platform_mix=source_mix,
            reply_style=self.reply_style,
        )
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.profile_build",
                temperature=0.5,
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise SoulProfileBuildError(str(exc)) from exc
        payload = self._parse_response(response.content)
        profile = SoulProfile(
            personality_portrait=str(payload.get("personality_portrait", "")),
            core_traits=self._as_str_list(payload.get("core_traits")),
            cognitive_style=self._as_str_list(payload.get("cognitive_style")),
            motivational_drivers=self._as_str_list(payload.get("motivational_drivers")),
            current_phase=str(payload.get("current_phase", "")),
            values=self._as_str_list(payload.get("values")),
            life_stage=str(payload.get("life_stage", "")),
            deep_needs=self._as_str_list(payload.get("deep_needs")),
        )
        # Attach raw MBTI data so OnionProfile.from_legacy() can pick it up
        profile._raw_mbti = payload.get("mbti")  # type: ignore[attr-defined]
        return profile

    @staticmethod
    def _compact_history_summary(
        history_summary: dict[str, object],
        *,
        original_count: int,
    ) -> dict[str, object]:
        """Build a low-risk retry summary that avoids raw titles/contexts."""
        raw_count = history_summary.get("count")
        count = (
            raw_count
            if isinstance(raw_count, int) and not isinstance(raw_count, bool)
            else original_count
        )
        compact: dict[str, object] = {
            "count": count,
            "fallback": "history omitted after profile-build retry",
            "fallback_hint": (
                "原始 history_summary 在画像生成时触发了模型安全/格式失败。"
                "本次重试只使用结构化 preference_summary、来源分布、"
                "awareness 和 insight 来生成人格画像。"
            ),
        }
        for key in ("favorites_summary", "following_summary"):
            value = history_summary.get(key)
            if isinstance(value, str) and value.strip():
                compact[f"{key}_present"] = True
        return compact

    def _parse_response(self, content: str) -> dict[str, object]:
        if not content.strip():
            raise SoulProfileBuildError("LLM returned an empty soul profile.")
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="soul profile"),
            )
            raise SoulProfileBuildError(
                f"LLM returned invalid JSON for soul profile (raw_len={len(content.strip())})"
            )
        if not isinstance(parsed, dict):
            raise SoulProfileBuildError("LLM soul profile response must be a JSON object.")
        payload: dict[str, object] = {key: value for key, value in parsed.items()}
        payload = self._normalize_payload(payload)
        self._validate_payload(payload)
        return payload

    def _normalize_payload(self, payload: dict[str, object]) -> dict[str, object]:
        optional_list_fields = (
            "core_traits",
            "cognitive_style",
            "motivational_drivers",
            "values",
            "deep_needs",
        )
        defaulted: list[str] = []
        for field in optional_list_fields:
            if field not in payload:
                payload[field] = []
                defaulted.append(field)
                continue
            value = payload[field]
            if isinstance(value, list):
                continue
            if isinstance(value, str) and value.strip():
                payload[field] = [value.strip()]
            else:
                payload[field] = []
            defaulted.append(field)

        if "life_stage" not in payload:
            payload["life_stage"] = ""
            defaulted.append("life_stage")
        if not str(payload.get("current_phase", "")).strip():
            payload["current_phase"] = "还在根据最近的行为信号整理当前阶段。"
            defaulted.append("current_phase")

        if defaulted:
            logger.warning(
                "LLM soul profile response missing/invalid optional fields; defaulted fields: %s",
                ", ".join(defaulted),
            )
        return payload

    def _validate_payload(self, payload: Mapping[str, object]) -> None:
        if "personality_portrait" not in payload:
            raise SoulProfileBuildError(
                "LLM soul profile response is missing fields: personality_portrait"
            )

        portrait = str(payload.get("personality_portrait", "")).strip()
        portrait_len = len(portrait)
        if portrait_len < 120 or portrait_len > 500:
            raise SoulProfileBuildError(
                f"LLM soul profile portrait length out of range "
                f"(got {portrait_len}, expected 120-500 chars)."
            )

        list_fields = (
            "core_traits",
            "cognitive_style",
            "motivational_drivers",
            "values",
            "deep_needs",
        )
        for field in list_fields:
            if not isinstance(payload.get(field), list):
                raise SoulProfileBuildError(f"LLM soul profile field '{field}' must be a list.")

    @staticmethod
    def _summarize_history(history: list[dict[str, Any]]) -> dict[str, object]:
        # Separate enriched items (favorites/following summaries) from regular history
        regular_items: list[dict[str, Any]] = []
        favorites_summary: str = ""
        following_summary: str = ""
        for item in history:
            if item.get("_favorites_summary"):
                favorites_summary = str(item["_favorites_summary"])
            elif item.get("_following_summary"):
                following_summary = str(item["_following_summary"])
            else:
                regular_items.append(item)

        # Representative sampling replaces "whatever arrived first". Everything
        # below derives from this sample, so titles / contexts / recent / older
        # all describe the same rows rather than three different prefixes.
        sampled_items = _sample_representative(regular_items, _HISTORY_SAMPLE_LIMIT)
        titles = [str(item.get("title", "")).strip() for item in sampled_items if item.get("title")]
        # Extract authors from multiple possible field names
        authors: list[str] = []
        for item in regular_items:  # frequency ranking stays on the full set
            author = (
                item.get("author_name")
                or item.get("author")
                or item.get("up_name")
                or (item.get("metadata") or {}).get("author", "")
                or (item.get("metadata") or {}).get("up_name", "")
            )
            if author and str(author).strip():
                authors.append(str(author).strip())
        # Deduplicate while preserving order for frequency ranking
        from collections import Counter

        author_counts = Counter(authors)
        top_authors = [name for name, _ in author_counts.most_common(50)]

        # v0.3.23+: per-item natural-language context. For history rows
        # that already carry ``context`` (xhs items, future sources that
        # plumbed through event_format) we use it verbatim. For raw B站
        # history items we synthesize from event_format.format_event_context
        # so the LLM sees a uniform stream of "在 X 平台干了 Y" sentences
        # regardless of where the signal originated. This makes
        # cross-platform behaviour readable instead of forcing the model
        # to reverse-engineer it from titles + author lists.
        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            format_event_context,
        )

        def _item_context(item: dict[str, Any]) -> str:
            existing = str(item.get("context", "")).strip()
            if existing:
                return existing
            raw_metadata = item.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            source_platform = (
                str(item.get("source_platform", "")).strip()
                or str(metadata.get("source_platform", "")).strip()
                or SOURCE_BILIBILI  # legacy raw-B站-history default
            )
            event_type = (
                str(item.get("event_type", "")).strip()
                or "view"  # raw history items are implicitly views
            )
            title = str(item.get("title", "")).strip()
            author = (
                str(item.get("author_name", "")).strip()
                or str(item.get("author", "")).strip()
                or str(item.get("up_name", "")).strip()
                or str(metadata.get("author", "") or metadata.get("up_name", "") or "").strip()
            )
            if not title:
                return ""
            return format_event_context(
                event_type=event_type,
                source_platform=source_platform,
                title=title,
                author=author,
            )

        # Time-based grouping: split into recent vs older if timestamps exist
        recent_titles: list[str] = []
        older_titles: list[str] = []
        recent_contexts: list[str] = []
        older_contexts: list[str] = []
        cutoff = max(1, len(sampled_items) * 3 // 10)
        for i, item in enumerate(sampled_items):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            ctx_line = _item_context(item)
            if i < cutoff:
                recent_titles.append(title)
                if ctx_line:
                    recent_contexts.append(ctx_line)
            else:
                older_titles.append(title)
                if ctx_line:
                    older_contexts.append(ctx_line)

        # Cap context lists to keep prompt token cost bounded. Each line
        # is ~30 chars Chinese ≈ 60-90 tokens; 50 + 50 + 100 ≈ 12k tokens
        # additional payload at the worst case, comparable to the existing
        # titles[:100] payload.
        all_contexts: list[str] = []
        for item in sampled_items:
            ctx_line = _item_context(item)
            if ctx_line:
                all_contexts.append(ctx_line)

        summary: dict[str, object] = {
            "count": len(regular_items),
            "titles": titles,
            "authors": top_authors,
        }
        if len(sampled_items) < len(regular_items):
            summary["sampling_hint"] = (
                f"共 {len(regular_items)} 条历史，按「时间分布 + 代表性权重」抽取 "
                f"{len(sampled_items)} 条送入分析："
                "先按时间分成若干段保证长期兴趣不被最近行为淹没，"
                "段内优先收藏/点赞/投币等明确互动与高完播内容，"
                "短暂划走的内容只保留少量。titles / contexts 描述的是同一批被抽中的行为。"
            )
        if all_contexts:
            summary["contexts"] = all_contexts
            summary["contexts_hint"] = (
                "contexts 是 v0.3.22+ 跨源统一的事件自然语言摘要,"
                "每行一个'在 X 平台干了 Y'。优先以 contexts 来理解用户行为,"
                "titles / authors / favorites_summary / following_summary "
                "可作为细化的结构化补充。"
            )
        if recent_titles:
            summary["recent_titles"] = recent_titles
            summary["recent_hint"] = (
                f"最近观看的 {len(recent_titles)} 个视频(前30%)代表当前活跃兴趣"
            )
        if older_titles:
            summary["older_titles"] = older_titles
        if recent_contexts:
            summary["recent_contexts"] = recent_contexts
        if older_contexts:
            summary["older_contexts"] = older_contexts
        if favorites_summary:
            summary["favorites_summary"] = favorites_summary
        if following_summary:
            summary["following_summary"] = following_summary
        return summary

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]
