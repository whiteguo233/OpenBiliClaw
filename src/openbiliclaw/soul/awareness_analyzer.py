"""Awareness-layer generation from recent behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_awareness_prompt
from openbiliclaw.llm.service import LLMServiceError

from .profile import AwarenessNote

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
    ) -> LLMResponse: ...


class AwarenessGenerationError(Exception):
    """Raised when awareness generation fails or returns invalid data."""


@dataclass
class AwarenessAnalyzer:
    """Generate structured recent-awareness notes from events."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError(
                "AwarenessAnalyzer requires a service with complete_structured_task()."
            )

    async def analyze(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
    ) -> list[AwarenessNote]:
        messages = build_awareness_prompt(
            events=events,
            preference_summary=preference,
            soul_profile=soul_profile,
        )
        try:
            response = await self.registry.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise AwarenessGenerationError(str(exc)) from exc
        payload = self._parse_response(response.content)
        return [self._build_note(item) for item in payload if isinstance(item, dict)]

    def merge_notes(
        self,
        existing: list[AwarenessNote],
        incoming: list[AwarenessNote],
    ) -> list[AwarenessNote]:
        """Merge awareness notes while deduplicating same-day observations."""
        merged = list(existing)
        seen = {(note.date, self._normalize_text(note.observation)) for note in existing}
        for note in incoming:
            key = (note.date, self._normalize_text(note.observation))
            if key in seen:
                continue
            merged.append(note)
            seen.add(key)
        return merged

    def _parse_response(self, content: str) -> list[object]:
        if not content.strip():
            return []
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="awareness generation"),
            )
            raise AwarenessGenerationError(
                f"LLM returned invalid JSON for awareness generation "
                f"(raw_len={len(content.strip())})"
        )
        if not isinstance(parsed, list):
            raise AwarenessGenerationError("LLM awareness response must be a JSON array.")
        return list(parsed)

    @staticmethod
    def _build_note(raw_item: dict[str, object]) -> AwarenessNote:
        return AwarenessNote(
            date=str(raw_item.get("date", "")).strip(),
            observation=str(raw_item.get("observation", "")).strip(),
            trend=str(raw_item.get("trend", "")).strip(),
            emotion_guess=str(raw_item.get("emotion_guess", "")).strip(),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())
