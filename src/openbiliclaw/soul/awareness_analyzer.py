"""Awareness-layer generation from recent behavior."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import uuid4

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.json_utils import (
    DEFAULT_STRUCTURED_MAX_TOKENS,
    extract_llm_json_list,
    format_parse_failure,
    parse_llm_json_tolerant,
)
from openbiliclaw.llm.prompts import (
    build_awareness_prompt,
    build_awareness_with_confusions_prompt,
)
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.soul.event_prompt_views import normalize_cognition_input_view

from .profile import AwarenessNote

logger = logging.getLogger(__name__)

_AWARENESS_WRAPPED_ARRAY_KEYS = (
    "results",
    "items",
    "notes",
    "awareness_notes",
    "awareness",
    "data",
    "output",
    "list",
    "array",
    # MiMo / reasoning-model variants seen in the wild (v0.3.x resilience pass).
    "observations",
    "recent_observations",
    "latest",
    "latest_observations",
)

# The full schema of a single awareness note, used by `_looks_like_single_note`
# below. The runtime check only requires `observation` (the only field whose
# absence makes the note worthless); the other keys are recovered with sensible
# defaults in `_build_note`.
_NOTE_SHAPE_KEYS = frozenset({"date", "observation", "trend", "emotion_guess"})

_SPONSORED_CITATION_INSTRUCTIONS = (
    "\nsource_event_ids只允许引用user消息current_evidence_ids列出的本轮recent_events。"
    "read_only_lookback与已有画像/觉察仅供历史上下文，不能作为本轮证据编号。"
    "不确定由哪条支持时不要编造编号；没有新的可靠观察时notes可为空。"
    "date只使用引用事件的真实发生日期，无法确定时留空，不能复制示例日期。"
    "同一天发生两次不等于连续两天，不能用事件数量充当天数。"
    "若有validation_feedback，上一轮已被校验拒绝，请依据原始事件重新生成完整结果。"
)


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


class AwarenessGenerationError(Exception):
    """Raised when awareness generation fails or returns invalid data."""


class AwarenessGroundingError(AwarenessGenerationError):
    """A known evidence error that the existing single retry can correct."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            {
                "source_event_ids": "awareness citation outside the analysed batch",
                "consecutive_days": "awareness day-count exceeds cited source chronology",
            }[code]
        )


@dataclass
class AwarenessAnalyzer:
    """Generate structured recent-awareness notes from events."""

    registry: SupportsCoreMemoryTask
    plain_prompt_view: str = "legacy"
    confusions_prompt_view: str = "legacy"

    def __post_init__(self) -> None:
        if not hasattr(self.registry, "complete_structured_task"):
            raise TypeError("AwarenessAnalyzer requires a service with complete_structured_task().")
        self.plain_prompt_view = normalize_cognition_input_view(self.plain_prompt_view)
        self.confusions_prompt_view = normalize_cognition_input_view(self.confusions_prompt_view)

    async def analyze(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
        source_event_ids: list[int] | None = None,
    ) -> list[AwarenessNote]:
        """Generate awareness notes.

        ``source_event_ids`` (Phase 0 evidence chain) is the id subset of
        events consumed this round. Explicit valid citations are preserved;
        absent citations use the batch as approximate provenance. When ``None``,
        ids are derived from ``events``. Free-mode prompts separate this batch
        from read-only historical context and reject out-of-batch citations.
        """
        evidence_ids = (
            list(source_event_ids) if source_event_ids is not None else self._event_ids_from(events)
        )
        messages = build_awareness_prompt(
            events=self._prompt_events(events, evidence_ids),
            preference_summary=preference,
            soul_profile=soul_profile,
            input_view=self.plain_prompt_view,
        )
        self._add_sponsored_citation_boundary(messages, evidence_ids, events)
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=max_tokens,
                caller="soul.awareness",
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise AwarenessGenerationError(str(exc)) from exc
        payload = self._parse_response(response.content)
        self._check_sponsored_citations(response, payload, evidence_ids)
        self._ground_sponsored_dates(response, payload, events, evidence_ids)
        return [self._build_note(item, evidence_ids) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _event_ids_from(events: list[dict[str, object]]) -> list[int]:
        ids: list[int] = []
        for event in events:
            raw = event.get("id")
            if isinstance(raw, bool) or not isinstance(raw, int | str | float):
                continue
            try:
                ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        return ids

    async def analyze_with_confusions(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        existing_confusions: list[dict[str, object]] | None = None,
        max_tokens: int = DEFAULT_STRUCTURED_MAX_TOKENS,
        source_event_ids: list[int] | None = None,
        validation_feedback: str = "",
    ) -> tuple[list[AwarenessNote], list[dict[str, object]]]:
        """Generate awareness notes AND confusion candidates (Phase 2).

        Uses the independent :func:`build_awareness_with_confusions_prompt`
        builder — the ``analyze()`` render path is left byte-identical. Returns
        ``(notes, confusion_candidates)``; ``notes`` are built exactly like
        ``analyze()`` (same ``_build_note`` + evidence attribution), and
        confusion candidates are whitelist-validated dicts (bad rows dropped).
        """
        evidence_ids = (
            list(source_event_ids) if source_event_ids is not None else self._event_ids_from(events)
        )
        messages = build_awareness_with_confusions_prompt(
            events=self._prompt_events(events, evidence_ids),
            preference_summary=preference,
            soul_profile=soul_profile,
            existing_confusions=existing_confusions,
            input_view=self.confusions_prompt_view,
        )
        self._add_sponsored_citation_boundary(messages, evidence_ids, events, validation_feedback)
        try:
            complete_structured = self.registry.complete_structured_task
            response = await complete_structured(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=max_tokens,
                caller="soul.awareness_confusions",
                **without_core_memory_kwargs(complete_structured),
            )
        except (LLMProviderError, LLMServiceError) as exc:
            raise AwarenessGenerationError(str(exc)) from exc
        notes_payload, confusions_payload = self._parse_with_confusions(response.content)
        self._check_sponsored_citations(response, notes_payload, evidence_ids)
        self._ground_sponsored_dates(response, notes_payload, events, evidence_ids)
        notes = [
            self._build_note(item, evidence_ids) for item in notes_payload if isinstance(item, dict)
        ]
        return notes, confusions_payload

    @staticmethod
    def _prompt_events(
        events: list[dict[str, object]], evidence_ids: list[int]
    ) -> list[dict[str, object]]:
        from openbiliclaw.llm.sponsored_router import sponsored_enabled

        if not sponsored_enabled():
            return events
        allowed = set(evidence_ids)
        return [event for event in events if set(_coerce_event_ids([event.get("id")])) & allowed]

    @staticmethod
    def _add_sponsored_citation_boundary(
        messages: list[dict[str, str]],
        evidence_ids: list[int],
        events: list[dict[str, object]],
        validation_feedback: str = "",
    ) -> None:
        from openbiliclaw.llm.sponsored_router import sponsored_enabled

        if sponsored_enabled():
            from openbiliclaw.soul.event_provenance import prepare_cognition_events
            from openbiliclaw.sources.event_format import render_retraction_marked_events

            allowed = set(evidence_ids)
            historical = [
                event for event in events if not set(_coerce_event_ids([event.get("id")])) & allowed
            ]
            # Keep history's dates/content for trends, but never offer its IDs
            # in the list from which this batch's provenance is selected.
            lookback = [
                {key: value for key, value in event.items() if key != "id"}
                for event in prepare_cognition_events(render_retraction_marked_events(historical))
            ]
            messages[0]["content"] += _SPONSORED_CITATION_INSTRUCTIONS
            messages[1]["content"] += (
                "\n<read_only_lookback>\n"
                + json.dumps(lookback, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n</read_only_lookback>\n<current_evidence_ids>\n"
                + json.dumps(evidence_ids, ensure_ascii=False, indent=2)
                + "\n</current_evidence_ids>"
            )
            if validation_feedback in {"source_event_ids", "consecutive_days"}:
                messages[1]["content"] += (
                    "\n<validation_feedback>\n"
                    + json.dumps({"invalid_field": validation_feedback}, sort_keys=True)
                    + "\n</validation_feedback>"
                )

    @staticmethod
    def _ground_sponsored_dates(
        response: LLMResponse,
        rows: list[object],
        events: list[dict[str, object]],
        evidence_ids: list[int],
    ) -> None:
        """Use the latest cited source day (UTC), never an invented/ingest date."""
        if response.provider != "sponsored":
            return
        from openbiliclaw.sources.event_format import parse_event_timestamp

        from .event_provenance import apply_event_provenance, event_metadata

        dates: dict[int, str] = {}
        for event in apply_event_provenance(events):
            event_ids = _coerce_event_ids([event.get("id")])
            metadata = event_metadata(event)
            timestamp = parse_event_timestamp(metadata)
            if event_ids and timestamp is not None and metadata.get("time_quality") != "ingest":
                dates[event_ids[0]] = timestamp.date().isoformat()
        for row in rows:
            if not isinstance(row, dict):
                continue
            cited = _coerce_event_ids(row.get("source_event_ids")) or evidence_ids
            # Unknown time in any referenced event makes the represented time
            # uncertain. An unrelated newer event cannot date an older note.
            row["date"] = (
                max(dates[event_id] for event_id in cited)
                if cited and all(event_id in dates for event_id in cited)
                else ""
            )
            days = sorted({date.fromisoformat(dates[i]).toordinal() for i in cited if i in dates})
            longest = streak = 0
            previous: int | None = None
            for day in days:
                streak = streak + 1 if previous is not None and day == previous + 1 else 1
                longest = max(longest, streak)
                previous = day
            text = " ".join(
                str(row.get(key, "")) for key in ("observation", "trend", "emotion_guess")
            )
            if any(count > longest for count in _claimed_consecutive_days(text)):
                raise AwarenessGroundingError("consecutive_days")

    @staticmethod
    def _check_sponsored_citations(
        response: LLMResponse, rows: list[object], evidence_ids: list[int]
    ) -> None:
        if response.provider != "sponsored":
            return
        allowed = set(evidence_ids)
        for row in rows:
            if isinstance(row, dict) and any(
                event_id not in allowed
                for event_id in _coerce_event_ids(row.get("source_event_ids"))
            ):
                # Raise before producing any notes. Cognition already retries
                # AwarenessGenerationError and preserves its watermark on failure.
                raise AwarenessGroundingError("source_event_ids")

    def _parse_with_confusions(
        self,
        content: str,
    ) -> tuple[list[object], list[dict[str, object]]]:
        """Parse the {"notes", "confusions"} object; tolerate legacy shapes.

        A bare array (or a wrapped-array under a known notes key) is treated as
        notes-only with no confusions — so a model that ignores the confusion
        contract still yields valid notes (parse-failure = drop confusions).
        """
        if not content.strip():
            return [], []
        parsed = parse_llm_json_tolerant(content)
        if parsed is None:
            exc = ValueError("unrecoverable JSON")
            logger.error(
                "%s",
                format_parse_failure(content, exc, label="awareness+confusions generation"),
            )
            raise AwarenessGenerationError(
                f"LLM returned invalid JSON for awareness+confusions generation "
                f"(raw_len={len(content.strip())})"
            )
        if isinstance(parsed, dict) and ("notes" in parsed or "confusions" in parsed):
            raw_notes = parsed.get("notes", [])
            notes = self._coerce_note_list(raw_notes) or []
            confusions = self._validate_confusions(parsed.get("confusions"))
            return notes, confusions
        # Legacy: array or wrapped-array of notes, no confusions.
        notes = self._coerce_note_list(parsed) or []
        return notes, []

    @staticmethod
    def _validate_confusions(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        result: list[dict[str, object]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            observation = str(item.get("observation", "")).strip()
            if not observation:
                logger.warning("confusion candidate dropped: empty observation")
                continue
            evidence = [
                str(ref).strip()
                for ref in (item.get("evidence_refs") or [])
                if isinstance(item.get("evidence_refs"), list) and str(ref).strip()
            ]
            result.append(
                {
                    "topic": str(item.get("topic", "")).strip(),
                    "observation": observation,
                    "interpretation": str(item.get("interpretation", "")).strip(),
                    "interpretation_confidence": _clamp_unit(
                        item.get("interpretation_confidence", 0.0)
                    ),
                    "evidence_refs": evidence,
                }
            )
        return result

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
        helper_payload = extract_llm_json_list(
            content,
            wrapper_keys=_AWARENESS_WRAPPED_ARRAY_KEYS,
            allow_singleton=True,
            item_predicate=lambda item: "observation" in item,
        )
        if helper_payload is not None:
            return list(helper_payload)

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
        payload = self._coerce_note_list(parsed)
        if payload is None:
            raise AwarenessGenerationError("LLM awareness response must be a JSON array.")
        return payload

    @staticmethod
    def _looks_like_single_note(value: object) -> bool:
        # Only `observation` is load-bearing — `date`, `trend`, `emotion_guess`
        # are recovered with defaults by `_build_note`. Reasoning models that
        # return a bare singular note dict (no array wrapper) are still
        # recoverable as long as `observation` is present.
        return isinstance(value, dict) and "observation" in value

    @staticmethod
    def _coerce_note_list(value: object) -> list[object] | None:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            for key in _AWARENESS_WRAPPED_ARRAY_KEYS:
                nested = value.get(key)
                if isinstance(nested, list):
                    return list(nested)
                if AwarenessAnalyzer._looks_like_single_note(nested):
                    return [nested]
            if AwarenessAnalyzer._looks_like_single_note(value):
                return [value]
        return None

    @staticmethod
    def _build_note(
        raw_item: dict[str, object],
        source_event_ids: list[int],
    ) -> AwarenessNote:
        # Prefer the per-note attribution the model returned, but only after
        # checking it against the batch actually shown to it (pitfall #4:
        # validate structured LLM output before persisting). Anything outside
        # the batch is a hallucinated citation — an evidence chain that points
        # at events which never fed the observation is worse than an honest
        # approximate one, so a single bad id demotes the whole note back to
        # batch attribution rather than silently keeping the survivors.
        allowed = set(source_event_ids)
        claimed = _coerce_event_ids(raw_item.get("source_event_ids"))
        precise: list[int] = []
        if claimed:
            unknown = [item for item in claimed if item not in allowed]
            if unknown:
                logger.warning(
                    "awareness note cited %d event id(s) outside the analysed batch "
                    "(%s...); falling back to approximate batch attribution",
                    len(unknown),
                    unknown[:5],
                )
            else:
                precise = claimed
        return AwarenessNote(
            date=str(raw_item.get("date", "")).strip(),
            observation=str(raw_item.get("observation", "")).strip(),
            trend=str(raw_item.get("trend", "")).strip(),
            emotion_guess=str(raw_item.get("emotion_guess", "")).strip(),
            note_id=uuid4().hex[:12],
            source_event_ids=precise or list(source_event_ids),
            # Only a validated per-note citation is exact. Without one the whole
            # consumed batch is attached and flagged approximate, so a consumer
            # can always tell "these events produced this note" from "this note
            # came from somewhere in this batch".
            source_event_ids_approximate=not precise and bool(source_event_ids),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())


def _claimed_consecutive_days(text: str) -> list[int]:
    """Read explicit Chinese/decimal day counts; not a general prose verifier."""
    digits = {char: value for value, char in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    counts: list[int] = []
    for match in re.finditer(
        r"连续\s*(\d+|[一二两三四五六七八九]?十[一二三四五六七八九]?|[一二两三四五六七八九])\s*天",
        text,
    ):
        raw = match.group(1)
        if raw.isdecimal():
            counts.append(int(raw))
        elif "十" in raw:
            tens, _, ones = raw.partition("十")
            counts.append(digits.get(tens, 1) * 10 + digits.get(ones, 0))
        else:
            counts.append(digits[raw])
    return counts


def _coerce_event_ids(value: object) -> list[int]:
    """Read a note's claimed evidence ids, dropping anything unusable.

    Order-preserving and deduplicated so a model repeating an id cannot inflate
    the chain. ``bool`` is rejected explicitly (it is an ``int`` subclass).
    """
    if not isinstance(value, list):
        return []
    seen: set[int] = set()
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | str | float):
            continue
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        ids.append(number)
    return ids


def _clamp_unit(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(number, 4)))
