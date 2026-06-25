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
    ) -> LLMResponse: ...


class SoulProfileBuildError(Exception):
    """Raised when soul-profile generation fails or returns invalid data."""


@dataclass
class ProfileBuilder:
    """Generate an initial soul profile from history and preference context."""

    registry: SupportsCoreMemoryTask

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
        )
        try:
            response = await self.registry.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.profile_build",
                temperature=0.5,
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

        titles = [str(item.get("title", "")).strip() for item in regular_items if item.get("title")]
        # Extract authors from multiple possible field names
        authors: list[str] = []
        for item in regular_items:
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
        cutoff = max(1, len(regular_items) * 3 // 10)
        for i, item in enumerate(regular_items):
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
        for item in regular_items:
            ctx_line = _item_context(item)
            if ctx_line:
                all_contexts.append(ctx_line)

        summary: dict[str, object] = {
            "count": len(regular_items),
            "titles": titles[:100],
            "authors": top_authors,
        }
        if all_contexts:
            summary["contexts"] = all_contexts[:100]
            summary["contexts_hint"] = (
                "contexts 是 v0.3.22+ 跨源统一的事件自然语言摘要,"
                "每行一个'在 X 平台干了 Y'。优先以 contexts 来理解用户行为,"
                "titles / authors / favorites_summary / following_summary "
                "可作为细化的结构化补充。"
            )
        if recent_titles:
            summary["recent_titles"] = recent_titles[:50]
            summary["recent_hint"] = (
                f"最近观看的 {len(recent_titles)} 个视频(前30%)代表当前活跃兴趣"
            )
        if older_titles:
            summary["older_titles"] = older_titles[:50]
        if recent_contexts:
            summary["recent_contexts"] = recent_contexts[:50]
        if older_contexts:
            summary["older_contexts"] = older_contexts[:50]
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
