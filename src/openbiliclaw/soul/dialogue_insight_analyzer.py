"""Structured extraction of dialogue-derived insight candidates."""

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
from openbiliclaw.llm.prompts import build_dialogue_insight_prompt
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.soul.meta_usage import should_drop_dialogue_candidate

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


class DialogueInsightAnalysisError(Exception):
    """Raised when dialogue insight extraction fails or returns invalid data."""


@dataclass
class DialogueInsightAnalyzer:
    """Extract structured insight candidates from chat turns."""

    registry: SupportsCoreMemoryTask

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError(
                "DialogueInsightAnalyzer requires a service with complete_structured_task()."
            )

    _ALLOWED_SETTLE_KINDS = frozenset({"speculation", "insight", "confusion"})
    _ALLOWED_SETTLE_VERDICTS = frozenset({"confirm", "reject"})
    _ALLOWED_ANCHOR_RELATIONS = frozenset(
        {"support", "contradict", "revise", "answer", "ambiguous", "unrelated"}
    )
    _ANCHOR_RELATIONS_BY_KIND = {
        "hypothesis": frozenset({"support", "contradict", "revise", "ambiguous", "unrelated"}),
        "confusion": frozenset({"answer", "ambiguous", "unrelated"}),
    }
    _CONFUSION_INTERPRETATIONS = frozenset({"real_interest", "proxy_behavior", "dismissed"})

    async def extract(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        core_memory: dict[str, object],
        active_list: dict[str, object] | None = None,
        anchor: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Extract candidate insights + settles from a single chat exchange.

        Returns ``{"candidates": [...], "settles": [...]}``. ``active_list``
        (speculations / insights / confusions) is injected so the LLM can
        reference active objects by their natural keys in ``settles``.
        """
        messages = build_dialogue_insight_prompt(
            user_message=user_message,
            assistant_reply=assistant_reply,
            core_memory=core_memory,
            active_list=active_list or {},
            anchor=anchor,
        )
        try:
            # ``build_dialogue_insight_prompt`` already serializes the full
            # ``core_memory`` dict (soul + preference + awareness + insights) into
            # the user prompt, so the default core-memory *injection* would be an
            # exact duplicate. Opt out: the model still sees the curated core memory
            # via the explicit param, we just drop the redundant second copy.
            response = await self.registry.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=DEFAULT_STRUCTURED_MAX_TOKENS,
                caller="soul.dialogue_insight",
                **without_core_memory_kwargs(self.registry.complete_structured_task),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise DialogueInsightAnalysisError(str(exc)) from exc

        parsed = self._parse_response(
            response.content,
            anchor_kind=str(anchor.get("kind", "")) if anchor else "",
        )
        # Talking about configuring / testing / deploying this product is not a
        # taste. Candidates raised in such a turn are dropped when they are
        # themselves operational ("系统配置与测试"), while a genuine interest
        # mentioned in the same turn survives. Settles are untouched: they
        # answer existing hypotheses rather than adding new ones.
        candidates = parsed.get("candidates")
        if isinstance(candidates, list):
            kept = [
                candidate
                for candidate in candidates
                if not should_drop_dialogue_candidate(
                    str(candidate.get("content", "")),
                    user_message=user_message,
                )
            ]
            dropped = len(candidates) - len(kept)
            if dropped:
                logger.info(
                    "dialogue insight: dropped %d meta-usage candidate(s) from a "
                    "product-operations turn",
                    dropped,
                )
            parsed["candidates"] = kept
        return parsed

    def _parse_response(self, content: str, *, anchor_kind: str = "") -> dict[str, object]:
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="dialogue insight analysis"),
            )
            raise DialogueInsightAnalysisError(
                f"LLM returned invalid JSON for dialogue insight analysis "
                f"(raw_len={len(content.strip())})"
            )
        if not isinstance(parsed, dict):
            raise DialogueInsightAnalysisError("Dialogue insight response must be a JSON object.")
        raw_candidates = parsed.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise DialogueInsightAnalysisError("Dialogue insight candidates must be a list.")
        normalized: list[dict[str, object]] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            content_text = str(item.get("content", "")).strip()
            if not content_text:
                continue
            normalized.append(
                {
                    "kind": str(item.get("kind", "")).strip() or "state",
                    "content": content_text,
                    "confidence": self._clamp_confidence(item.get("confidence", 0.0)),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
            )
        result: dict[str, object] = {
            "candidates": normalized,
            "settles": self._parse_settles(parsed.get("settles")),
        }
        if anchor_kind:
            result["anchor"] = self._parse_anchor_decision(
                parsed.get("anchor"),
                anchor_kind=anchor_kind,
            )
        return result

    def _parse_anchor_decision(
        self,
        raw_anchor: object,
        *,
        anchor_kind: str,
    ) -> dict[str, object] | None:
        """Whitelist an anchor decision and coerce matrix violations to unrelated."""
        if not isinstance(raw_anchor, dict):
            logger.warning("dialogue anchor decision dropped: missing object")
            return None
        raw_relation = raw_anchor.get("relation")
        relation = raw_relation.strip() if isinstance(raw_relation, str) else ""
        if relation not in self._ALLOWED_ANCHOR_RELATIONS:
            logger.warning("dialogue anchor decision dropped: bad relation=%r", raw_relation)
            return None
        allowed = self._ANCHOR_RELATIONS_BY_KIND.get(anchor_kind)
        if allowed is None:
            logger.warning("dialogue anchor decision dropped: bad kind=%r", anchor_kind)
            return None
        if relation not in allowed:
            logger.warning(
                "dialogue anchor relation outside kind matrix: kind=%s relation=%s; "
                "coercing to unrelated",
                anchor_kind,
                relation,
            )
            relation = "unrelated"
        interpretation = str(raw_anchor.get("interpretation", "")).strip()
        if (
            anchor_kind == "confusion"
            and relation == "answer"
            and interpretation not in self._CONFUSION_INTERPRETATIONS
        ):
            logger.warning(
                "dialogue anchor decision dropped: bad confusion interpretation=%r",
                interpretation,
            )
            return None
        derived = self._parse_anchor_derived(raw_anchor.get("derived"))
        return {
            "relation": relation,
            "interpretation": interpretation,
            "derived": derived if relation == "revise" else [],
        }

    def _parse_anchor_derived(self, raw_derived: object) -> list[dict[str, object]]:
        if not isinstance(raw_derived, list):
            return []
        derived: list[dict[str, object]] = []
        for item in raw_derived:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            derived.append(
                {
                    "content": content,
                    "confidence": self._clamp_confidence(item.get("confidence", 0.0)),
                    "evidence": str(item.get("evidence", "")).strip(),
                }
            )
            if len(derived) >= 3:
                break
        return derived

    def _parse_settles(self, raw_settles: object) -> list[dict[str, object]]:
        """Whitelist/validate settles; drop malformed rows (parse-failure = drop)."""
        if not isinstance(raw_settles, list):
            return []
        settles: list[dict[str, object]] = []
        for item in raw_settles:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).strip()
            ref = str(item.get("ref", "")).strip()
            verdict = str(item.get("verdict", "")).strip()
            if kind not in self._ALLOWED_SETTLE_KINDS:
                logger.warning("dialogue settle dropped: bad kind=%r", kind)
                continue
            if verdict not in self._ALLOWED_SETTLE_VERDICTS:
                logger.warning("dialogue settle dropped: bad verdict=%r (ref=%r)", verdict, ref)
                continue
            if not ref:
                continue
            settles.append(
                {
                    "kind": kind,
                    "ref": ref,
                    "verdict": verdict,
                    "note": str(item.get("note", "")).strip(),
                }
            )
        return settles

    @staticmethod
    def _clamp_confidence(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.0
        else:
            value = 0.0
        return max(0.0, min(1.0, round(value, 4)))
