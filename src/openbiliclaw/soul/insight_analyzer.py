"""Insight-layer generation from awareness and preference context."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_insight_prompt
from openbiliclaw.llm.service import LLMServiceError

from .profile import AwarenessNote, InsightHypothesis

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


class InsightGenerationError(Exception):
    """Raised when insight generation fails or returns invalid data."""


@dataclass
class InsightAnalyzer:
    """Generate and merge structured insight hypotheses."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError(
                "InsightAnalyzer requires a service with complete_structured_task()."
            )

    async def analyze(
        self,
        *,
        awareness_notes: list[AwarenessNote],
        preference: dict[str, object],
        soul_profile: dict[str, object],
    ) -> list[InsightHypothesis]:
        messages = build_insight_prompt(
            awareness_notes=[self._note_to_dict(note) for note in awareness_notes],
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
            raise InsightGenerationError(str(exc)) from exc
        payload = self._parse_response(response.content)
        return [self._build_hypothesis(item) for item in payload if isinstance(item, dict)]

    def merge_insights(
        self,
        existing: list[InsightHypothesis],
        incoming: list[InsightHypothesis],
    ) -> list[InsightHypothesis]:
        """Merge hypotheses by normalized hypothesis text."""
        merged = {
            self._normalize_text(item.hypothesis): item
            for item in existing
        }
        for item in incoming:
            key = self._normalize_text(item.hypothesis)
            current = merged.get(key)
            if current is None:
                merged[key] = item
                continue
            merged[key] = InsightHypothesis(
                hypothesis=current.hypothesis or item.hypothesis,
                evidence=sorted({*current.evidence, *item.evidence}),
                confidence=max(current.confidence, item.confidence),
                validated=current.validated or item.validated,
                created_at=current.created_at or item.created_at,
            )
        return list(merged.values())

    def _parse_response(self, content: str) -> list[object]:
        if not content.strip():
            return []
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="insight generation"),
            )
            raise InsightGenerationError(
                f"LLM returned invalid JSON for insight generation "
                f"(raw_len={len(content.strip())})"
        )
        if not isinstance(parsed, list):
            raise InsightGenerationError("LLM insight response must be a JSON array.")
        return list(parsed)

    @staticmethod
    def _build_hypothesis(raw_item: dict[str, object]) -> InsightHypothesis:
        return InsightHypothesis(
            hypothesis=str(raw_item.get("hypothesis", "")).strip(),
            evidence=InsightAnalyzer._as_str_list(raw_item.get("evidence")),
            confidence=InsightAnalyzer._clamp_confidence(raw_item.get("confidence")),
            validated=False,
            created_at=datetime.now().date().isoformat(),
        )

    @staticmethod
    def _note_to_dict(note: AwarenessNote) -> dict[str, object]:
        return {
            "date": note.date,
            "observation": note.observation,
            "trend": note.trend,
            "emotion_guess": note.emotion_guess,
        }

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())

    @staticmethod
    def _clamp_confidence(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.5
        else:
            value = 0.5
        return max(0.0, min(1.0, round(value, 4)))
