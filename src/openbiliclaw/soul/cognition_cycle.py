"""Periodic cognition cycle — throttled awareness + insight generation.

The ProfileUpdatePipeline calls ``CognitionCycle.run_if_due()`` from its
``tick()`` loop. On each call, the cycle checks whether enough time has
passed since the last successful run (default: 12 hours) and, if so,
regenerates awareness notes and insight hypotheses via the LLM-backed
analyzers, then syncs the results into the OnionProfile so the extension
popup's profile view shows them.

State is persisted to ``<data_dir>/memory/cognition_cycle_state.json`` so
throttling survives process restarts.

This module exists to bridge a gap that was previously "orphaned": the
AwarenessAnalyzer and InsightAnalyzer were defined but had zero runtime
callers, so ``profile.recent_awareness`` and ``profile.active_insights``
were always empty. The cycle wires them into the normal tick loop with a
cost-aware throttle so LLM spend stays bounded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.soul.awareness_analyzer import AwarenessGenerationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.awareness_analyzer import AwarenessAnalyzer
    from openbiliclaw.soul.insight_analyzer import InsightAnalyzer
    from openbiliclaw.soul.profile import AwarenessNote, InsightHypothesis

from openbiliclaw.soul.profile import (
    OnionProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
)

logger = logging.getLogger(__name__)

# Default throttle: generate awareness+insight once every 12 hours.
DEFAULT_MIN_INTERVAL_SECONDS = 12 * 60 * 60

# --- Cursor-based incremental reads (replaces the old fixed limit=50) ----
# Awareness reads events with id > last_awareness_event_id rather than the
# most-recent-50 window, so a burst of >50 events in one throttle window is
# never silently dropped, and a quiet window doesn't re-send the same events.
#
# Bound on the newest still-unprocessed events folded into a single awareness
# run. On a huge backlog (e.g. first run after a long offline period) the
# watermark jumps to the newest event and older unprocessed events beyond this
# window are skipped (logged, not silent) to keep "recent awareness" recent.
_AWARENESS_BACKLOG_CAP = 900
# Per-LLM-call batch size. Sized for modern long-context models (256k+): an
# event is ~100 tokens, so 300 events ≈ 30-45k input tokens — a typical 12h
# window (even heavy usage) fits in a SINGLE call, no needless splitting.
# Batching only kicks in for pathological backlogs (> 300 new events in one
# window), as a safety net so worst-case is a few modest calls rather than one
# 90k-token call that smaller-context providers might choke on.
_AWARENESS_EVENT_BATCH_SIZE = 300
# Recent already-processed events (id <= watermark) included read-only in the
# first batch so observations stay trend-aware even when few events are new.
_AWARENESS_CONTEXT_LOOKBACK = 10

# Insight reads awareness notes after last_insight_awareness_index (a positional
# cursor — notes are append-only) instead of the full awareness history, so the
# insight prompt no longer grows without bound. Notes are denser than events
# (each is an LLM-written observation), so the batch is smaller than awareness'
# but still large enough that real runs (a handful of new notes) are one call.
_INSIGHT_NOTE_BACKLOG_CAP = 450
_INSIGHT_NOTE_BATCH_SIZE = 150

# Output-token budget for the batched cognition LLM calls. Larger than the
# generic 16k default so a dense batch of events/notes can emit a full notes /
# hypotheses array without truncation.
_COGNITION_MAX_TOKENS = 32768

# How many notes/insights to keep attached to the OnionProfile (surfaced in UI).
_PROFILE_AWARENESS_WINDOW = 8
_PROFILE_INSIGHT_WINDOW = 6

# Backoff between the first and second awareness attempt. MiMo 502s and
# transient JSON-shape glitches typically clear on a re-call after a brief
# pause; 2s is enough to dodge most retryable bursts without lengthening
# the cycle noticeably.
_AWARENESS_RETRY_BACKOFF_SECONDS = 2.0


@dataclass
class CognitionCycleResult:
    """Summary of one cognition cycle run."""

    ran: bool = False
    throttled: bool = False
    awareness_generated: int = 0
    insight_generated: int = 0
    total_awareness_after: int = 0
    total_insight_after: int = 0
    errors: list[str] = field(default_factory=list)


class CognitionCycle:
    """Throttled awareness + insight generation runner.

    Usage:
        cycle = CognitionCycle(
            memory=memory,
            awareness_analyzer=...,
            insight_analyzer=...,
            min_interval_seconds=43200,
        )
        result = await cycle.run_if_due()
    """

    def __init__(
        self,
        *,
        memory: MemoryManager,
        awareness_analyzer: AwarenessAnalyzer,
        insight_analyzer: InsightAnalyzer,
        min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    ) -> None:
        self._memory = memory
        self._awareness_analyzer = awareness_analyzer
        self._insight_analyzer = insight_analyzer
        self._min_interval_seconds = int(min_interval_seconds)

    # -- Public API -----------------------------------------------------------

    async def run_if_due(self, *, now: datetime | None = None) -> CognitionCycleResult:
        """Run awareness+insight generation if the throttle interval has elapsed.

        Returns a result describing what happened. On throttle skip, returns
        ``CognitionCycleResult(ran=False, throttled=True)``.
        """
        current_time = now or datetime.now()
        state = self._load_state()
        result = CognitionCycleResult()

        # Gate: awareness + insight LLM calls feed on `preference` and `soul`
        # memory layers. If neither has been built yet (init's first ~7
        # minutes), the analyzer prompts get near-empty inputs and tend to
        # blow up. Silent skip here avoids the ERROR-level traces every
        # cognition tick before the profile lands, while still allowing a
        # partially initialized profile to accrue fresh awareness.
        preference_data = self._memory.get_layer("preference").data
        soul_data = self._memory.get_layer("soul").data
        if not preference_data and not soul_data:
            logger.debug("CognitionCycle skipped: preference and soul layers are empty")
            result.throttled = True
            return result

        last_awareness_at = _parse_iso(state.get("last_awareness_at"))
        last_insight_at = _parse_iso(state.get("last_insight_at"))

        awareness_due = self._is_due(last_awareness_at, current_time)
        insight_due = self._is_due(last_insight_at, current_time)

        if not awareness_due and not insight_due:
            result.throttled = True
            return result

        result.ran = True

        # 1. Awareness pass
        if awareness_due:
            try:
                added = await self._run_awareness(state)
                result.awareness_generated = added
                state["last_awareness_at"] = current_time.isoformat()
            except AwarenessGenerationError as exc:
                # Recoverable: bad JSON shape or single LLM hiccup. Log at
                # WARNING (not ERROR) and DO NOT advance ``last_awareness_at``
                # — the next tick will re-attempt instead of waiting the full
                # 12h throttle. Pre-resilience this fell through the generic
                # ``except Exception`` branch which silently advanced the
                # schedule and blanked the awareness window for half a day.
                logger.warning(
                    "Awareness analyzer failed twice; will retry next tick: %s",
                    exc,
                )
                result.errors.append(f"awareness: {exc}")
            except Exception as exc:
                logger.exception("Awareness analyzer failed during cognition cycle")
                result.errors.append(f"awareness: {exc}")

        # 2. Insight pass — runs after awareness so it can use the fresh notes
        if insight_due:
            try:
                added = await self._run_insight(state)
                result.insight_generated = added
                state["last_insight_at"] = current_time.isoformat()
            except Exception as exc:
                logger.exception("Insight analyzer failed during cognition cycle")
                result.errors.append(f"insight: {exc}")

        # 3. Sync the fresh awareness/insights into the OnionProfile so the
        # popup sees them immediately. This is a best-effort write — a
        # missing soul layer or mid-init state should not break the cycle.
        try:
            self._sync_to_profile(result)
        except Exception:
            logger.exception("Failed to sync cognition cycle output into profile")

        self._save_state(state)
        return result

    # -- Internal -------------------------------------------------------------

    def _is_due(
        self,
        last_run_at: datetime | None,
        now: datetime,
    ) -> bool:
        if last_run_at is None:
            return True
        elapsed = (now - last_run_at).total_seconds()
        return elapsed >= self._min_interval_seconds

    async def _run_awareness(self, state: dict[str, Any]) -> int:
        """Fold events newer than the watermark into awareness notes.

        Cursor-based: reads events with ``id > last_awareness_event_id`` (the
        newest ``_AWARENESS_BACKLOG_CAP`` of them on a large backlog), processes
        them in ``_AWARENESS_EVENT_BATCH_SIZE`` chunks, and advances the
        watermark after each successful chunk so partial progress survives a
        later-chunk failure. A small lookback of already-processed events rides
        in the first chunk so observations stay trend-aware when little is new.

        Each chunk's analyze call retries once on ``AwarenessGenerationError``
        (mirrors the legacy single-call behavior). A persistent failure bubbles
        up to ``run_if_due`` — the watermark stays at the last good chunk, so
        the next tick resumes from there instead of waiting the full throttle.

        Returns the number of NEW notes added across all chunks.
        """
        watermark = _coerce_int(state.get("last_awareness_event_id", 0))
        rows = self._memory.query_events(
            after_event_id=watermark,
            limit=_AWARENESS_BACKLOG_CAP,
        )
        if not rows:
            return 0
        if len(rows) >= _AWARENESS_BACKLOG_CAP:
            logger.warning(
                "Awareness backlog hit cap %d; older unprocessed events are "
                "skipped (watermark jumps to newest of this window).",
                _AWARENESS_BACKLOG_CAP,
            )
        rows.reverse()  # query returns newest-first; process chronologically

        lookback = self._awareness_lookback(watermark)
        preference = self._memory.get_layer("preference").data
        soul_profile_data = self._memory.get_layer("soul").data

        total_added = 0
        for batch_index, batch in enumerate(_chunk(rows, _AWARENESS_EVENT_BATCH_SIZE)):
            events_for_call = (lookback + batch) if batch_index == 0 else batch
            new_notes = await self._awareness_with_retry(
                events_for_call, preference, soul_profile_data
            )
            if new_notes:
                existing = self._load_awareness_notes()
                merged = self._awareness_analyzer.merge_notes(existing, new_notes)
                total_added += max(0, len(merged) - len(existing))
                self._save_awareness_notes(merged)
            # Advance the watermark past this chunk and persist immediately so a
            # failure in a later chunk doesn't reprocess this one next tick.
            batch_max_id = max(_coerce_int(item.get("id", 0)) for item in batch)
            watermark = max(watermark, batch_max_id)
            state["last_awareness_event_id"] = watermark
            self._save_state(state)
        return total_added

    async def _awareness_with_retry(
        self,
        events: list[dict[str, Any]],
        preference: dict[str, Any],
        soul_profile_data: dict[str, Any],
    ) -> list[AwarenessNote]:
        """One awareness analyze call with a single retry on structured failure."""
        try:
            return await self._awareness_analyzer.analyze(
                events=events,
                preference=preference,
                soul_profile=soul_profile_data,
                max_tokens=_COGNITION_MAX_TOKENS,
            )
        except AwarenessGenerationError:
            await asyncio.sleep(_AWARENESS_RETRY_BACKOFF_SECONDS)
            return await self._awareness_analyzer.analyze(
                events=events,
                preference=preference,
                soul_profile=soul_profile_data,
                max_tokens=_COGNITION_MAX_TOKENS,
            )

    def _awareness_lookback(self, watermark: int) -> list[dict[str, Any]]:
        """Recent already-processed events (id <= watermark) for trend context.

        Empty on the first run (no prior events) — the backlog itself supplies
        plenty of context then. Returned chronologically (oldest-first).
        """
        if watermark <= 0:
            return []
        recent = self._memory.query_events(limit=_AWARENESS_CONTEXT_LOOKBACK)
        prior = [item for item in recent if _coerce_int(item.get("id", 0)) <= watermark]
        prior.reverse()
        return prior

    async def _run_insight(self, state: dict[str, Any]) -> int:
        """Derive insights from awareness notes newer than the insight cursor.

        Cursor-based: reads ``awareness_notes[last_insight_awareness_index:]``
        (notes are append-only, so a positional index is a stable cursor)
        instead of the full awareness history — bounding the prompt. Processes
        in ``_INSIGHT_NOTE_BATCH_SIZE`` chunks, passing the current active
        hypotheses as read-only context so the LLM can refine rather than
        restate. Advances the cursor after each chunk.

        Returns the number of NEW hypotheses added across all chunks.
        """
        all_notes = self._load_awareness_notes()
        total_notes = len(all_notes)
        cursor = _coerce_int(state.get("last_insight_awareness_index", 0))
        if cursor > total_notes:
            # Notes shrank (unexpected — e.g. a future GC). Reprocess from 0.
            cursor = 0
        new_notes = all_notes[cursor:]
        if not new_notes:
            return 0
        if len(new_notes) > _INSIGHT_NOTE_BACKLOG_CAP:
            skipped = len(new_notes) - _INSIGHT_NOTE_BACKLOG_CAP
            logger.warning(
                "Insight note backlog exceeded cap %d; skipping %d older notes.",
                _INSIGHT_NOTE_BACKLOG_CAP,
                skipped,
            )
            new_notes = new_notes[-_INSIGHT_NOTE_BACKLOG_CAP:]
            cursor = total_notes - _INSIGHT_NOTE_BACKLOG_CAP

        preference = self._memory.get_layer("preference").data
        soul_profile_data = self._memory.get_layer("soul").data

        total_added = 0
        processed = cursor
        for batch in _chunk(new_notes, _INSIGHT_NOTE_BATCH_SIZE):
            existing = self._load_insights()
            new_insights = await self._insight_analyzer.analyze(
                awareness_notes=batch,
                preference=preference,
                soul_profile=soul_profile_data,
                existing_insights=existing,
                max_tokens=_COGNITION_MAX_TOKENS,
            )
            if new_insights:
                merged = self._insight_analyzer.merge_insights(existing, new_insights)
                total_added += max(0, len(merged) - len(existing))
                self._save_insights(merged)
            processed += len(batch)
            state["last_insight_awareness_index"] = processed
            self._save_state(state)
        return total_added

    def _sync_to_profile(self, result: CognitionCycleResult) -> None:
        """Copy the freshest awareness/insights into the OnionProfile.

        Reads the current soul layer, attaches the latest windowed notes
        and insights, and writes back. This makes them visible via
        ``profile.recent_awareness`` and ``profile.active_insights`` which
        is what the /api/profile-summary endpoint reads.
        """
        if result.awareness_generated == 0 and result.insight_generated == 0:
            # Nothing to sync, but still update the total counts for observability
            result.total_awareness_after = len(self._load_awareness_notes())
            result.total_insight_after = len(self._load_insights())
            return

        soul_layer = self._memory.get_layer("soul")
        if not soul_layer.data:
            # Profile has not been initialized yet — skip sync silently
            return

        try:
            profile = OnionProfile.from_dict(soul_layer.data)
        except Exception:
            logger.exception("Failed to load OnionProfile during cognition sync")
            return

        all_notes = self._load_awareness_notes()
        all_insights = self._load_insights()

        # Keep the most recent window slice. Order of notes is preserved by
        # the merge functions (append-only with dedup), so taking the tail
        # gives us the newest items.
        profile.recent_awareness = all_notes[-_PROFILE_AWARENESS_WINDOW:]
        profile.active_insights = all_insights[-_PROFILE_INSIGHT_WINDOW:]
        profile.updated_at = datetime.now().isoformat()

        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()

        # Also sync the markdown/json files so the filesystem-visible profile
        # reflects the new awareness/insights.
        try:
            self._memory.sync_profile_files(profile)
        except Exception:
            logger.debug("Failed to sync profile files after cognition cycle", exc_info=True)

        result.total_awareness_after = len(all_notes)
        result.total_insight_after = len(all_insights)

    # -- Memory layer helpers (mirrors SoulEngine's private helpers) ----------

    def _load_awareness_notes(self) -> list[AwarenessNote]:
        layer_data = self._memory.get_layer("awareness").data
        notes = layer_data.get("notes", [])
        return [awareness_note_from_dict(item) for item in notes if isinstance(item, dict)]

    def _save_awareness_notes(self, notes: list[AwarenessNote]) -> None:
        layer = self._memory.get_layer("awareness")
        layer.data.clear()
        layer.data.update(
            {
                "notes": [awareness_note_to_dict(item) for item in notes],
            }
        )
        layer.save()

    def _load_insights(self) -> list[InsightHypothesis]:
        layer_data = self._memory.get_layer("insight").data
        hypotheses = layer_data.get("hypotheses", [])
        return [insight_hypothesis_from_dict(item) for item in hypotheses if isinstance(item, dict)]

    def _save_insights(self, insights: list[InsightHypothesis]) -> None:
        layer = self._memory.get_layer("insight")
        layer.data.clear()
        layer.data.update(
            {
                "hypotheses": [insight_hypothesis_to_dict(item) for item in insights],
            }
        )
        layer.save()

    # -- State persistence ----------------------------------------------------

    def _state_path(self) -> Path | None:
        data_dir = getattr(self._memory, "_data_dir", None)
        if data_dir is None:
            return None
        return Path(data_dir) / "memory" / "cognition_cycle_state.json"

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if path is None or not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.debug("Failed to save cognition cycle state", exc_info=True)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _coerce_int(value: Any) -> int:
    """Best-effort int coercion for watermark/cursor values read from JSON state."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _chunk(items: list[Any], size: int) -> Iterator[list[Any]]:
    """Yield successive ``size``-length slices of ``items`` (last may be shorter)."""
    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield items[start : start + step]
