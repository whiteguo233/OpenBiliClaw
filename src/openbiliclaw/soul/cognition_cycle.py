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
import math
import os
import re
import unicodedata
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.soul.awareness_analyzer import AwarenessGenerationError
from openbiliclaw.soul.confusion import ConfusionManager
from openbiliclaw.soul.ledger import ProfileLedger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

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
# Daily cleanup of stale duplicate hypotheses / open confusions.
CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

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

# Model-visible existing-hypothesis context is intentionally smaller than the
# durable insight ledger. Production measurement on 2026-08-06 found 441
# persisted hypotheses (79,558 rendered characters) while the latest twenty
# required only 3,724 characters. Twenty recent items preserve roughly ten
# days at the observed 1-4 new hypotheses/day cadence. A separate judged tail
# keeps older user-confirmed/rejected facts visible without letting prompt size
# grow forever. Re-open both caps if the hypothesis lifecycle or provider/model
# changes; storage and merge always retain the complete history.
_INSIGHT_CONTEXT_RECENT_CAP = 20
_INSIGHT_CONTEXT_JUDGED_CAP = 20

# Weighted prompt-view policy (2026-08-06 calibration). The total cap stays at
# the already-shipped Phase 3 worst case, but the membership is no longer a
# fixed recent/judged union. Eight recent + eight judged anchors are hard
# reserves; relevance and importance lanes use the remaining budget before a
# weighted/diverse fill. Re-open these constants if the hypothesis schema or
# observed generation cadence changes. None affects the durable ledger.
_INSIGHT_CONTEXT_TOTAL_CAP = 40
_INSIGHT_CONTEXT_RECENT_RESERVE = 8
_INSIGHT_CONTEXT_JUDGED_RESERVE = 8
_INSIGHT_CONTEXT_RELEVANCE_QUOTA = 16
_INSIGHT_CONTEXT_IMPORTANCE_QUOTA = 8

# General ranking weights. Relevance deliberately leads because the selector
# exists to recover older hypotheses that can explain *this* awareness batch;
# recency still has a 40-row half-life and a separate hard reserve.
_INSIGHT_RELEVANCE_WEIGHT = 0.35
_INSIGHT_RECENCY_WEIGHT = 0.25
_INSIGHT_VERDICT_WEIGHT = 0.20
_INSIGHT_QUALITY_WEIGHT = 0.15
_INSIGHT_RECURRENCE_WEIGHT = 0.05
_INSIGHT_RECENCY_HALF_LIFE = 40.0
_INSIGHT_AWARENESS_RELEVANCE_SHARE = 0.80
_INSIGHT_PROFILE_RELEVANCE_SHARE = 0.20
_INSIGHT_NEAR_DUPLICATE_THRESHOLD = 0.82
_INSIGHT_DIVERSITY_PENALTY = 0.18

_INSIGHT_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+.-]*|[\u3400-\u9fff]+")
_INSIGHT_GENERIC_FEATURES = frozenset(
    {
        "用户",
        "可能",
        "最近",
        "内容",
        "喜欢",
        "关注",
        "观看",
        "倾向",
        "表现",
        "通过",
        "说明",
        "偏好",
        "视频",
        "假设",
        "洞察",
        "这个",
        "一种",
        "the",
        "and",
        "that",
        "this",
        "user",
        "content",
        "recent",
        "likely",
        "may",
    }
)

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

# Early-trigger threshold (Phase 5): when this many events have accumulated
# past the awareness watermark, run awareness ahead of the 12h throttle so a
# heavy session doesn't wait half a day to be reflected on. Calibration
# (first-round, pitfall #3): ~30 events is roughly a single active session;
# re-tune after the first production month and after any event-schema change.
_EARLY_TRIGGER_EVENT_COUNT = 30

# Event types whose presence is a "strong signal" worth an early awareness
# pass even below the count threshold (explicit user intent / authored text).
_STRONG_EVENT_TYPES = frozenset({"comment", "danmaku", "reply", "feedback"})


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
        awareness_event_batch_size: int = _AWARENESS_EVENT_BATCH_SIZE,
        insight_note_batch_size: int = _INSIGHT_NOTE_BATCH_SIZE,
        cognition_max_tokens: int = _COGNITION_MAX_TOKENS,
        pending_rebuild_hook: Callable[[], Awaitable[Any]] | None = None,
        confusion_replay_hook: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._memory = memory
        self._awareness_analyzer = awareness_analyzer
        self._insight_analyzer = insight_analyzer
        self._min_interval_seconds = int(min_interval_seconds)
        # Configurable cognition budgets (issue #169). The module constants are
        # 256k-provider defaults; small-context local models can lower these
        # through ``[soul]`` without patching the source.
        self._awareness_event_batch_size = max(1, int(awareness_event_batch_size))
        self._insight_note_batch_size = max(1, int(insight_note_batch_size))
        self._cognition_max_tokens = max(1, int(cognition_max_tokens))
        # 12h-loop fallback trigger for the SoulEngine's debounced confirmed-
        # hypotheses rebuild (spec invariant 4). Optional; best-effort.
        self._pending_rebuild_hook = pending_rebuild_hook
        # 12h crash-recovery fallback for completed confusion replies whose
        # attribution did not reach the serial dialogue-learning owner.
        self._confusion_replay_hook = confusion_replay_hook
        # Single-flight guard (Phase 5): the due-check + watermark consumption
        # must run under one lock so overlapping ticks (or an early trigger
        # racing the 12h tick) never double-process the same events.
        self._run_lock = asyncio.Lock()

    def _profile_ledger(self) -> ProfileLedger:
        """Best-effort audit ledger over the memory manager's database."""
        return ProfileLedger(getattr(self._memory, "_database", None))

    def _confusion_manager(self) -> ConfusionManager:
        """Confusion state machine over the memory manager's database."""
        return ConfusionManager(
            getattr(self._memory, "_database", None),
            ledger=self._profile_ledger(),
        )

    # -- Public API -----------------------------------------------------------

    async def run_if_due(self, *, now: datetime | None = None) -> CognitionCycleResult:
        """Run awareness+insight generation if the throttle interval has elapsed.

        Returns a result describing what happened. On throttle skip, returns
        ``CognitionCycleResult(ran=False, throttled=True)``.
        """
        # Single-flight: if a run is already in progress (long awareness call,
        # or an overlapping tick), skip this invocation rather than block —
        # exactly one runner consumes the watermark at a time.
        if self._run_lock.locked():
            return CognitionCycleResult(throttled=True)
        async with self._run_lock:
            return await self._run_if_due_locked(now=now)

    async def _run_if_due_locked(self, *, now: datetime | None) -> CognitionCycleResult:
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

        cleanup_due = self._is_due(
            _parse_iso(state.get("last_cleanup_at")),
            current_time,
        )
        if cleanup_due:
            try:
                self._cleanup_stale_pending()
                state["last_cleanup_at"] = current_time.isoformat()
                self._save_state(state)
            except Exception:
                logger.debug("Stale pending cleanup failed", exc_info=True)

        awareness_due = self._is_due(last_awareness_at, current_time)
        insight_due = self._is_due(last_insight_at, current_time)

        # Early trigger (Phase 5): a backlog of unrefined events or a strong
        # signal (authored comment / explicit feedback) pulls the awareness pass
        # ahead of the 12h throttle. The 12h fallback still fires via ``_is_due``.
        if not awareness_due and self._should_early_trigger(state):
            awareness_due = True

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

        # 4. Confusion TTL maintenance (Phase 2): expire wait-parked confusions
        # past their TTL. Best-effort — never breaks the cognition cycle.
        try:
            self._confusion_manager().expire_due(now=current_time)
        except Exception:
            logger.debug("Confusion TTL sweep failed", exc_info=True)

        # 5. Enumerate any completed clarifying reply that missed its durable
        # attribution receipt. The hook only submits the dedicated typed replay
        # command; analysis and mutation remain in the settlement worker.
        if self._confusion_replay_hook is not None:
            try:
                await self._confusion_replay_hook()
            except Exception:
                logger.debug("Confusion attribution replay failed", exc_info=True)

        # 6. 12h-loop fallback: trigger the debounced confirmed-hypotheses
        # rebuild (spec invariant 4). Best-effort — never breaks the cycle.
        if self._pending_rebuild_hook is not None:
            try:
                await self._pending_rebuild_hook()
            except Exception:
                logger.debug("Pending rebuild hook failed during cognition cycle", exc_info=True)

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

    def _should_early_trigger(self, state: dict[str, Any]) -> bool:
        """True when unrefined events warrant an awareness pass before 12h.

        Reads the newest events past the awareness watermark once (bounded by
        the count threshold) and fires when either there are enough of them or
        any is a strong signal. Best-effort — a query failure never triggers.
        """
        watermark = _coerce_int(state.get("last_awareness_event_id", 0))
        try:
            rows = self._memory.query_events(
                after_event_id=watermark,
                limit=_EARLY_TRIGGER_EVENT_COUNT,
            )
        except Exception:
            logger.debug("early-trigger event probe failed", exc_info=True)
            return False
        if len(rows) >= _EARLY_TRIGGER_EVENT_COUNT:
            return True
        return any(_is_strong_signal_event(row) for row in rows)

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
        for batch_index, batch in enumerate(_chunk(rows, self._awareness_event_batch_size)):
            events_for_call = (lookback + batch) if batch_index == 0 else batch
            # Evidence chain: attribute produced notes to THIS round's consumed
            # events (the batch), not the read-only lookback context.
            batch_event_ids = [_coerce_int(item.get("id", 0)) for item in batch]
            batch_event_ids = [eid for eid in batch_event_ids if eid > 0]
            new_notes, confusion_candidates = await self._awareness_with_retry(
                events_for_call, preference, soul_profile_data, batch_event_ids
            )
            if new_notes:
                existing = self._load_awareness_notes()
                merged = self._awareness_analyzer.merge_notes(existing, new_notes)
                total_added += max(0, len(merged) - len(existing))
                self._save_awareness_notes(merged)
            if confusion_candidates:
                try:
                    self._confusion_manager().create_from_awareness_candidates(confusion_candidates)
                except Exception:
                    logger.debug("Failed to persist confusion candidates", exc_info=True)
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
        source_event_ids: list[int],
    ) -> tuple[list[AwarenessNote], list[dict[str, Any]]]:
        """One awareness+confusions call with a single retry on structured failure.

        Switching to ``analyze_with_confusions`` (new independent builder) is an
        intentional behaviour change vs the legacy ``analyze()`` path — recorded
        via A/B in the PR (quality guardrail). Returns ``(notes, confusions)``.
        """
        existing_confusions = self._confusion_manager().list_for_generation_context()
        try:
            return await self._awareness_analyzer.analyze_with_confusions(
                events=events,
                preference=preference,
                soul_profile=soul_profile_data,
                existing_confusions=existing_confusions,
                max_tokens=self._cognition_max_tokens,
                source_event_ids=source_event_ids,
            )
        except AwarenessGenerationError:
            await asyncio.sleep(_AWARENESS_RETRY_BACKOFF_SECONDS)
            return await self._awareness_analyzer.analyze_with_confusions(
                events=events,
                preference=preference,
                soul_profile=soul_profile_data,
                existing_confusions=existing_confusions,
                max_tokens=self._cognition_max_tokens,
                source_event_ids=source_event_ids,
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
        for batch in _chunk(new_notes, self._insight_note_batch_size):
            existing = self._load_insights()
            try:
                prompt_context = _select_insight_prompt_context(
                    existing,
                    awareness_notes=batch,
                    preference=preference,
                    soul_profile=soul_profile_data,
                )
            except Exception:
                # Prompt selection is an optimization, never the owner of the
                # durable cognition cycle. Preserve the previously shipped
                # bounded view if malformed legacy text surprises the scorer.
                logger.exception(
                    "Weighted insight context selection failed; using fixed bounded fallback"
                )
                prompt_context = _select_fixed_insight_prompt_context(existing)
            new_insights = await self._insight_analyzer.analyze(
                awareness_notes=batch,
                preference=preference,
                soul_profile=soul_profile_data,
                existing_insights=prompt_context,
                max_tokens=self._cognition_max_tokens,
            )
            if new_insights:
                merged = self._insight_analyzer.merge_insights(existing, new_insights)
                total_added += max(0, len(merged) - len(existing))
                self._save_insights(merged)
            processed += len(batch)
            state["last_insight_awareness_index"] = processed
            self._save_state(state)
        return total_added

    def _cleanup_stale_pending(self) -> None:
        """Daily dedup of hypotheses and duplicate open confusions.

        Runs regardless of whether awareness/insight LLM work is due, so the
        queue stays small even on quiet days.  Confirmation history is only
        downgraded (dismissed), never deleted.
        """
        # 1. Collapse duplicate hypotheses in the insight layer.
        try:
            insights = self._load_insights()
            from openbiliclaw.soul.insight_analyzer import InsightAnalyzer

            deduped = InsightAnalyzer.dedupe_hypotheses(insights)
            if len(deduped) != len(insights):
                self._save_insights(deduped)
        except Exception:
            logger.debug("Daily insight dedup sweep failed", exc_info=True)

        # 2. Dismiss duplicate open confusions, keeping the newest/first row.
        try:
            db = getattr(self._memory, "_database", None)
            if db is None:
                return
            manager = self._confusion_manager()
            rows = db.list_confusions(statuses=["open"], limit=10000)
            active: list[dict[str, Any]] = [
                {
                    "id": int(row.get("id", 0) or 0),
                    "status": str(row.get("status", "") or "").strip().lower(),
                    "topic": str(row.get("topic", "") or "").strip(),
                    "observation": str(row.get("observation", "") or "").strip(),
                    "interpretation": str(row.get("interpretation", "") or "").strip(),
                }
                for row in rows
            ]
            kept: list[dict[str, Any]] = []
            for cand in active:
                if any(manager._same_confusion(cand, item) for item in kept):
                    db.update_confusion(
                        int(cand["id"]),
                        status="dismissed",
                        resolved_at=datetime.now().astimezone().isoformat(),
                        resolution_note="daily cleanup duplicate",
                    )
                else:
                    kept.append(cand)
        except Exception:
            logger.debug("Daily confusion dedup sweep failed", exc_info=True)


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

        # Ledger write point D5 #8: cognition sync (awareness/insight → soul).
        with self._profile_ledger().action(
            write_point="cognition_sync",
            source="cognition_cycle",
            before={
                "awareness_generated": result.awareness_generated,
                "insight_generated": result.insight_generated,
            },
            source_refs=[
                f"awareness_generated:{result.awareness_generated}",
                f"insight_generated:{result.insight_generated}",
            ],
        ) as _entry:
            soul_layer.data.clear()
            soul_layer.data.update(profile.to_dict())
            soul_layer.save()
            _entry.after = {
                "awareness_after": len(all_notes),
                "insight_after": len(all_insights),
            }

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
        # Atomic write (Phase 5): serialize to a sibling temp file, then rename
        # over the target. A crash mid-write leaves the previous state intact
        # instead of a truncated/corrupt JSON that would reset the watermark.
        tmp_path = path.with_name(f"{path.name}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except OSError:
            logger.debug("Failed to save cognition cycle state", exc_info=True)
            with suppress(OSError):
                if tmp_path.exists():
                    tmp_path.unlink()


def _select_fixed_insight_prompt_context(
    insights: list[InsightHypothesis],
) -> list[InsightHypothesis]:
    """Return the shipped Phase 3 fixed recent/judged view (fallback/control)."""
    if not insights:
        return []
    recent_start = max(0, len(insights) - _INSIGHT_CONTEXT_RECENT_CAP)
    selected_indices = set(range(recent_start, len(insights)))
    judged_indices = [
        index
        for index, item in enumerate(insights)
        if bool(item.validated) or bool(str(item.user_verdict or "").strip())
    ]
    selected_indices.update(judged_indices[-_INSIGHT_CONTEXT_JUDGED_CAP:])
    return [item for index, item in enumerate(insights) if index in selected_indices]


def _insight_text_features(value: object) -> frozenset[str]:
    """Return deterministic tokenizer/provider-independent lexical features."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    features: set[str] = set()
    for match in _INSIGHT_WORD_RE.finditer(normalized):
        token = match.group(0).strip("._+-")
        if not token:
            continue
        if any("\u3400" <= char <= "\u9fff" for char in token):
            chars = [char for char in token if "\u3400" <= char <= "\u9fff"]
            if len(chars) == 1:
                features.add(chars[0])
            else:
                features.update(
                    "".join(chars[index : index + 2]) for index in range(len(chars) - 1)
                )
        elif len(token) >= 2 or token.isdigit():
            features.add(token)
    return frozenset(feature for feature in features if feature not in _INSIGHT_GENERIC_FEATURES)


def _insight_context_strings(value: object) -> list[str]:
    """Flatten text values only; mapping keys are schema, not user context."""
    strings: list[str] = []

    def _visit(item: object) -> None:
        if isinstance(item, str):
            if item.strip():
                strings.append(item)
            return
        if isinstance(item, dict):
            for key in sorted(item, key=str):
                _visit(item[key])
            return
        if isinstance(item, list | tuple):
            for child in item:
                _visit(child)

    _visit(value)
    return strings


def _insight_overlap(
    candidate: frozenset[str],
    context: frozenset[str],
) -> float:
    if not candidate or not context:
        return 0.0
    return len(candidate & context) / min(len(candidate), len(context))


def _insight_similarity(
    left: frozenset[str],
    right: frozenset[str],
) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(left), len(right))
    return max(jaccard, containment * 0.9)


def _insight_semantic_state(item: InsightHypothesis) -> str:
    verdict = str(item.user_verdict or "").strip().lower()
    if verdict == "rejected":
        return "rejected"
    if item.validated or verdict == "confirmed":
        return "confirmed"
    return "unjudged"


def _select_insight_prompt_context(
    insights: list[InsightHypothesis],
    *,
    awareness_notes: list[AwarenessNote] | None = None,
    preference: dict[str, object] | None = None,
    soul_profile: dict[str, object] | None = None,
) -> list[InsightHypothesis]:
    """Select a bounded importance/relevance/diversity prompt view.

    Membership is ranked, but returned rows are the original objects in source
    order. The caller still merges against ``insights`` in full; this function
    never rewrites or summarizes durable hypotheses.
    """
    if not insights:
        return []

    count = len(insights)
    eligible_indices = [
        index for index, item in enumerate(insights) if str(item.hypothesis or "").strip()
    ]
    if not eligible_indices:
        return []
    hypothesis_features = [_insight_text_features(item.hypothesis) for item in insights]
    candidate_features = []
    for item in insights:
        evidence = item.evidence if isinstance(item.evidence, list) else []
        candidate_features.append(
            _insight_text_features(
                " ".join(
                    [
                        str(item.hypothesis or ""),
                        *(str(value) for value in evidence if str(value).strip()),
                    ]
                )
            )
        )
    states = [_insight_semantic_state(item) for item in insights]

    awareness_text = " ".join(
        text
        for note in awareness_notes or []
        for text in (note.observation, note.trend, note.emotion_guess)
        if str(text or "").strip()
    )
    profile_text = " ".join(
        [
            *_insight_context_strings(preference or {}),
            *_insight_context_strings(soul_profile or {}),
        ]
    )
    awareness_features = _insight_text_features(awareness_text)
    profile_features = _insight_text_features(profile_text)

    relevance: list[float] = []
    recency: list[float] = []
    verdict_scores: list[float] = []
    quality: list[float] = []
    recurrence: list[float] = []
    for index, item in enumerate(insights):
        awareness_match = _insight_overlap(candidate_features[index], awareness_features)
        profile_match = _insight_overlap(candidate_features[index], profile_features)
        if awareness_features:
            relevance.append(
                _INSIGHT_AWARENESS_RELEVANCE_SHARE * awareness_match
                + _INSIGHT_PROFILE_RELEVANCE_SHARE * profile_match
            )
        else:
            relevance.append(profile_match)
        age = count - 1 - index
        recency.append(math.pow(0.5, age / _INSIGHT_RECENCY_HALF_LIFE))
        verdict_scores.append(1.0 if states[index] != "unjudged" else 0.0)
        try:
            raw_confidence = float(item.confidence)
        except (TypeError, ValueError):
            raw_confidence = 0.0
        confidence = max(0.0, min(1.0, raw_confidence)) if math.isfinite(raw_confidence) else 0.0
        evidence_score = (
            min(len(item.evidence), 3) / 3.0 if isinstance(item.evidence, list) else 0.0
        )
        quality.append(0.6 * confidence + 0.4 * evidence_score)

    # Recurrence is a small support signal, so an exact normalized-feature
    # signature is sufficient and keeps selection linear before the bounded
    # diversity pass. Near-duplicate prompt competition below still uses the
    # richer similarity metric; durable-ledger growth must not create O(n^2)
    # work every cognition cycle.
    signature_counts = Counter(
        (states[index], hypothesis_features[index])
        for index in eligible_indices
        if hypothesis_features[index]
    )
    for index in range(count):
        related = (
            signature_counts[(states[index], hypothesis_features[index])] - 1
            if hypothesis_features[index]
            else 0
        )
        recurrence.append(min(max(related, 0), 4) / 4.0)

    general_scores = [
        _INSIGHT_RELEVANCE_WEIGHT * relevance[index]
        + _INSIGHT_RECENCY_WEIGHT * recency[index]
        + _INSIGHT_VERDICT_WEIGHT * verdict_scores[index]
        + _INSIGHT_QUALITY_WEIGHT * quality[index]
        + _INSIGHT_RECURRENCE_WEIGHT * recurrence[index]
        for index in range(count)
    ]

    selected: list[int] = []
    selected_set: set[int] = set()
    similarity_cache: dict[tuple[int, int], float] = {}

    def _pair_similarity(left: int, right: int) -> float:
        key = (left, right) if left < right else (right, left)
        cached = similarity_cache.get(key)
        if cached is not None:
            return cached
        similarity = _insight_similarity(
            hypothesis_features[left],
            hypothesis_features[right],
        )
        similarity_cache[key] = similarity
        return similarity

    def _max_selected_similarity(index: int) -> float:
        return max(
            (
                _pair_similarity(index, other)
                for other in selected
                if states[other] == states[index]
            ),
            default=0.0,
        )

    def _same_state_duplicate(index: int) -> bool:
        return any(
            states[other] == states[index]
            and _pair_similarity(index, other) >= _INSIGHT_NEAR_DUPLICATE_THRESHOLD
            for other in selected
        )

    def _add(index: int) -> bool:
        if (
            index in selected_set
            or len(selected) >= _INSIGHT_CONTEXT_TOTAL_CAP
            or _same_state_duplicate(index)
        ):
            return False
        selected.append(index)
        selected_set.add(index)
        return True

    def _take_ordered(indices: list[int], quota: int) -> None:
        added = 0
        for index in indices:
            if _add(index):
                added += 1
                if added >= quota:
                    break

    def _take_ranked(indices: list[int], quota: int, lane_scores: list[float]) -> None:
        candidates = {index for index in indices if index not in selected_set}
        added = 0
        while candidates and added < quota and len(selected) < _INSIGHT_CONTEXT_TOTAL_CAP:
            best = max(
                candidates,
                key=lambda index: (
                    lane_scores[index]
                    - _INSIGHT_DIVERSITY_PENALTY * _max_selected_similarity(index),
                    general_scores[index],
                    index,
                ),
            )
            candidates.remove(best)
            if _add(best):
                added += 1

    judged_newest = [index for index in reversed(eligible_indices) if states[index] != "unjudged"]
    _take_ordered(judged_newest, _INSIGHT_CONTEXT_JUDGED_RESERVE)
    _take_ordered(
        list(reversed(eligible_indices)),
        _INSIGHT_CONTEXT_RECENT_RESERVE,
    )

    relevance_lane = [
        0.75 * relevance[index] + 0.25 * general_scores[index] for index in range(count)
    ]
    _take_ranked(
        [index for index in eligible_indices if relevance[index] > 0.0],
        _INSIGHT_CONTEXT_RELEVANCE_QUOTA,
        relevance_lane,
    )

    importance_lane = [
        0.50 * quality[index] + 0.30 * verdict_scores[index] + 0.20 * recurrence[index]
        for index in range(count)
    ]
    _take_ranked(
        eligible_indices,
        _INSIGHT_CONTEXT_IMPORTANCE_QUOTA,
        importance_lane,
    )
    _take_ranked(
        eligible_indices,
        _INSIGHT_CONTEXT_TOTAL_CAP - len(selected),
        general_scores,
    )

    return [insights[index] for index in sorted(selected)]


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_strong_signal_event(event: dict[str, Any]) -> bool:
    """A single event carrying explicit intent worth an early awareness pass.

    Strong = an authored comment/danmaku/reply with non-empty text, an explicit
    feedback event, or any event the satisfaction classifier marked
    positive/negative (an explicit like/dislike-shaped signal).
    """
    etype = str(event.get("event_type", "")).strip().lower()
    if etype in _STRONG_EVENT_TYPES:
        if etype in {"feedback", "reply"}:
            return True
        # comment / danmaku only count when they carry text (not an empty ping).
        text = str(event.get("title", "") or event.get("comment_text", "") or "").strip()
        if text:
            return True
    sat = str(event.get("inferred_satisfaction", "")).strip().lower()
    return sat in {"positive", "negative"}


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
