"""Insight-layer generation from awareness and preference context."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Protocol

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    extract_llm_json_list,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import build_insight_prompt
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.soul.event_prompt_views import normalize_cognition_input_view

from .profile import AwarenessNote, InsightHypothesis

logger = logging.getLogger(__name__)

# Default production dedup similarity. Calibrated to collapse obvious wording
# variants without merging distinct directions; can be tuned if users want
# even tighter integration.
_INSIGHT_DEDUP_SIMILARITY_THRESHOLD = 0.65


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


class InsightGenerationError(Exception):
    """Raised when insight generation fails or returns invalid data."""


@dataclass
class InsightAnalyzer:
    """Generate and merge structured insight hypotheses."""

    registry: SupportsCoreMemoryTask
    cognition_prompt_view: str = "legacy"

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError("InsightAnalyzer requires a service with complete_structured_task().")
        self.cognition_prompt_view = normalize_cognition_input_view(self.cognition_prompt_view)

    async def analyze(
        self,
        *,
        awareness_notes: list[AwarenessNote],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        existing_insights: list[InsightHypothesis] | None = None,
        max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
    ) -> list[InsightHypothesis]:
        messages = build_insight_prompt(
            awareness_notes=[self._note_to_dict(note) for note in awareness_notes],
            preference_summary=preference,
            soul_profile=soul_profile,
            existing_hypotheses=[
                self._hypothesis_to_context_dict(item) for item in (existing_insights or [])
            ],
            input_view=self.cognition_prompt_view,
        )
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=max_tokens,
                caller="soul.insight",
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise InsightGenerationError(str(exc)) from exc
        payload = self._parse_response(response.content)
        return [self._build_hypothesis(item) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _merge_confidence(
        current: InsightHypothesis,
        incoming: InsightHypothesis,
        verdict: str,
    ) -> float:
        """Combine an existing score with a fresh pass, respecting user verdicts.

        - ``rejected``: the user said 不准. A later pass is the same model
          re-reading the same kind of behaviour, so it must not talk the score
          back up — that silently undid the rejection and re-queued the
          hypothesis in the 待聊 list. The score may still fall further.
        - ``confirmed``: the user said 准. One weak pass should not demote it
          below the confirm floor, but it can still rise.
        - never judged: track the latest evidence in *both* directions. The old
          ``max()`` meant a hypothesis that scored high once stayed high forever
          no matter how the behaviour changed.
        """
        if verdict == "rejected":
            return round(min(current.confidence, incoming.confidence), 4)
        if verdict == "confirmed":
            return round(max(current.confidence, incoming.confidence), 4)
        return round(incoming.confidence, 4)

    @staticmethod
    def _dedupe_norm_title(value: str) -> str:
        return re.sub(r"[\W_]+", "", value).lower()

    @staticmethod
    def _same_semantic_state(current: InsightHypothesis, incoming: InsightHypothesis) -> bool:
        return (
            current.validated == incoming.validated
            and current.user_verdict == incoming.user_verdict
        )

    @classmethod
    def _is_near_duplicate(
        cls,
        existing: InsightHypothesis,
        incoming: InsightHypothesis,
    ) -> bool:
        left = cls._dedupe_norm_title(existing.hypothesis)
        right = cls._dedupe_norm_title(incoming.hypothesis)
        if left == right:
            return True
        # Keep short labels apart; they are usually deliberate distinct topics.
        if len(left) < 20 or len(right) < 20:
            return False
        return SequenceMatcher(None, left, right).ratio() >= _INSIGHT_DEDUP_SIMILARITY_THRESHOLD

    @classmethod
    def dedupe_hypotheses(
        cls,
        insights: list[InsightHypothesis],
    ) -> list[InsightHypothesis]:
        """Deduplicate one stored insight list in-place style.

        This is used at persistence boundaries so the production backlog does
        not keep growing with near-copies.  Conflicting user states are kept
        separate because they represent different semantic information.
        """
        kept: list[InsightHypothesis] = []
        for item in insights:
            match_index = None
            for index, current in enumerate(kept):
                if not cls._is_near_duplicate(current, item):
                    continue
                if cls._dedupe_norm_title(current.hypothesis) != cls._dedupe_norm_title(
                    item.hypothesis
                ) and not cls._same_semantic_state(current, item):
                    continue
                match_index = index
                break
            if match_index is None:
                kept.append(item)
                continue
            current = kept[match_index]
            verdict = current.user_verdict or item.user_verdict
            kept[match_index] = InsightHypothesis(
                hypothesis=current.hypothesis or item.hypothesis,
                evidence=sorted({*current.evidence, *item.evidence}),
                confidence=cls._merge_confidence(current, item, verdict),
                validated=current.validated or item.validated,
                created_at=current.created_at or item.created_at,
                user_verdict=verdict,
            )
        return kept

    def merge_insights(
        self,
        existing: list[InsightHypothesis],
        incoming: list[InsightHypothesis],
    ) -> list[InsightHypothesis]:
        """Merge hypotheses by normalized text and near-duplicate wording.

        Production-stage deduplication happens here, before new hypotheses are
        persisted, so the insight layer does not accumulate a dozen copies of
        the same observation with slightly different wording.  Conflicting user
        states (confirmed vs rejected vs unjudged) are kept separate because
        they carry different semantic signal.
        """
        merged: list[InsightHypothesis] = list(existing)
        for item in incoming:
            match_index = None
            for index, current in enumerate(merged):
                if not self._is_near_duplicate(current, item):
                    continue
                # Exact matches keep the existing merge semantics even across
                # states.  Near-duplicate matches are only folded when both
                # sides are in the same semantic state, otherwise we would
                # collapse a reject into a confirm and lose the user verdict.
                if self._dedupe_norm_title(current.hypothesis) != self._dedupe_norm_title(
                    item.hypothesis
                ) and not self._same_semantic_state(current, item):
                    continue
                match_index = index
                break
            if match_index is None:
                merged.append(item)
                continue
            current = merged[match_index]
            verdict = current.user_verdict or item.user_verdict
            merged[match_index] = InsightHypothesis(
                hypothesis=current.hypothesis or item.hypothesis,
                evidence=sorted({*current.evidence, *item.evidence}),
                confidence=self._merge_confidence(current, item, verdict),
                validated=current.validated or item.validated,
                created_at=current.created_at or item.created_at,
                user_verdict=verdict,
            )
        return merged

    def _parse_response(self, content: str) -> list[object]:
        if not content.strip():
            return []
        payload = extract_llm_json_list(
            content,
            wrapper_keys=(
                "results",
                "items",
                "insights",
                "hypotheses",
                "data",
                "output",
                "list",
                "array",
            ),
            allow_singleton=True,
            item_predicate=lambda item: "hypothesis" in item or "evidence" in item,
        )
        if payload is not None:
            return list(payload)

        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="insight generation"),
            )
            raise InsightGenerationError(
                f"LLM returned invalid JSON for insight generation (raw_len={len(content.strip())})"
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
    def _hypothesis_to_context_dict(item: InsightHypothesis) -> dict[str, object]:
        """Compact view of an existing hypothesis for the prompt's context block.

        Only the fields the LLM needs to avoid restating / to refine an
        existing hypothesis — keeps the incremental prompt cheap.
        """
        return {
            "hypothesis": item.hypothesis,
            "confidence": round(float(item.confidence), 4),
            "validated": bool(item.validated),
            "user_verdict": str(item.user_verdict or "").strip(),
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
