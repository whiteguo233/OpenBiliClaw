"""Tests for CognitionCycle awareness retry + schedule-preservation policy.

The 6-hour MiMo log capture showed that a single AwarenessGenerationError
silently advanced ``last_awareness_at`` because the legacy ``except
Exception`` branch logged at ERROR and dropped through. The throttle window
is 12h, so one bad LLM response disabled the awareness pass for half a
day. The tests here lock in two behaviors:

1. ``_run_awareness`` retries once on ``AwarenessGenerationError`` so a
   transient JSON-shape glitch clears on the next call without ending the
   tick.
2. ``run_if_due`` distinguishes ``AwarenessGenerationError`` from generic
   ``Exception``: on the structured failure, ``last_awareness_at`` is NOT
   advanced, so the next tick re-attempts instead of waiting 12h.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.awareness_analyzer import AwarenessGenerationError
from openbiliclaw.soul.cognition_cycle import CognitionCycle

if TYPE_CHECKING:
    from pathlib import Path


class _FlakyAwarenessAnalyzer:
    """Counts analyze() calls; raises AwarenessGenerationError per scripted plan."""

    def __init__(self, *, fail_first_n: int, succeed_payload: list[dict[str, str]]) -> None:
        self._fail_first_n = fail_first_n
        self._succeed_payload = succeed_payload
        self.call_count = 0

    async def analyze(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        max_tokens: int = 0,
    ) -> list[Any]:
        self.call_count += 1
        if self.call_count <= self._fail_first_n:
            raise AwarenessGenerationError("simulated transient failure")
        from openbiliclaw.soul.profile import awareness_note_from_dict

        return [awareness_note_from_dict(item) for item in self._succeed_payload]

    def merge_notes(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


class _NoopInsightAnalyzer:
    """Insight analyzer stub that returns no hypotheses."""

    async def analyze(
        self,
        *,
        awareness_notes: list[Any],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        existing_insights: list[Any] | None = None,
        max_tokens: int = 0,
    ) -> list[Any]:
        return []

    def merge_insights(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


def _add_events(memory: MemoryManager, count: int, *, prefix: str = "事件") -> None:
    """Insert ``count`` view events so the awareness cursor has work to do.

    Inserts directly via the database (sync) so it can be called from inside an
    already-running test event loop without awaiting.
    """
    for i in range(count):
        memory._database.insert_event(  # noqa: SLF001 — test seeding
            "view",
            title=f"{prefix}{i}",
            context=f"在 B 站看了《{prefix}{i}》",
            metadata={"source_platform": "bilibili"},
        )


def _seed_memory(tmp_path: Path, *, event_count: int = 3) -> MemoryManager:
    """Build a MemoryManager with enough data to clear the empty-layers gate.

    Also seeds a few events so the cursor-based awareness pass has unprocessed
    rows to fold in (the analyzer is mocked, but the cycle now short-circuits
    when there are no events newer than the watermark).
    """
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").update("interests", ["深度内容"])
    memory.get_layer("soul").update("personality_portrait", "理性又敏感")
    if event_count:
        _add_events(memory, event_count)
    return memory


_SAMPLE_NOTE_PAYLOAD = [
    {
        "date": "2026-03-08",
        "observation": "最近连续浏览深度内容。",
        "trend": "更偏向深度解释。",
        "emotion_guess": "专注吸收信息。",
    }
]


@pytest.mark.asyncio
async def test_awareness_retries_once_and_succeeds(tmp_path: Path) -> None:
    """First call raises, second succeeds → note added, schedule advanced."""
    memory = _seed_memory(tmp_path)
    flaky = _FlakyAwarenessAnalyzer(fail_first_n=1, succeed_payload=_SAMPLE_NOTE_PAYLOAD)
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=flaky,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    now = datetime(2026, 5, 16, 12, 0, 0)
    result = await cycle.run_if_due(now=now)

    assert result.ran is True
    assert result.errors == []
    assert result.awareness_generated == 1
    assert flaky.call_count == 2  # one retry only

    # Schedule advanced — next tick within the throttle window will skip.
    state = cycle._load_state()  # noqa: SLF001 — internal contract is the test surface
    assert state.get("last_awareness_at") == now.isoformat()


@pytest.mark.asyncio
async def test_awareness_double_failure_preserves_schedule(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both attempts raise → record error, log WARNING, do NOT advance schedule."""
    memory = _seed_memory(tmp_path)
    flaky = _FlakyAwarenessAnalyzer(fail_first_n=10, succeed_payload=_SAMPLE_NOTE_PAYLOAD)
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=flaky,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    now = datetime(2026, 5, 16, 12, 0, 0)
    with caplog.at_level(logging.WARNING, logger="openbiliclaw.soul.cognition_cycle"):
        result = await cycle.run_if_due(now=now)

    assert result.ran is True
    assert result.awareness_generated == 0
    assert any("awareness" in err.lower() for err in result.errors)
    # Bounded retry: exactly one extra LLM call (2 total) even on persistent failure.
    assert flaky.call_count == 2

    # WARNING-level log, not ERROR — this is recoverable, not a bug.
    awareness_logs = [r for r in caplog.records if "awareness" in r.getMessage().lower()]
    assert awareness_logs, "expected an awareness-related log entry"
    assert all(r.levelno <= logging.WARNING for r in awareness_logs), (
        "awareness retry exhaustion should not log at ERROR level"
    )

    # Schedule NOT advanced — next tick after the throttle window will retry.
    state = cycle._load_state()  # noqa: SLF001
    assert "last_awareness_at" not in state or state.get("last_awareness_at") in (None, "")


@pytest.mark.asyncio
async def test_awareness_failure_does_not_block_subsequent_retry(tmp_path: Path) -> None:
    """After a failed cycle, the next run_if_due call still tries awareness
    even when the throttle window has not elapsed (because last_awareness_at
    was never advanced)."""
    memory = _seed_memory(tmp_path)

    # First call: persistent failure.
    flaky = _FlakyAwarenessAnalyzer(fail_first_n=10, succeed_payload=_SAMPLE_NOTE_PAYLOAD)
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=flaky,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=3600,  # 1h throttle
    )
    first_now = datetime(2026, 5, 16, 12, 0, 0)
    await cycle.run_if_due(now=first_now)
    failed_count = flaky.call_count
    assert failed_count == 2

    # Replace analyzer with a healthy one and tick again just one minute later
    # (well inside the 1h throttle window). It should still run.
    healthy = _FlakyAwarenessAnalyzer(fail_first_n=0, succeed_payload=_SAMPLE_NOTE_PAYLOAD)
    cycle._awareness_analyzer = healthy  # noqa: SLF001 — swap for test
    second_now = first_now + timedelta(minutes=1)
    result = await cycle.run_if_due(now=second_now)

    assert result.ran is True
    assert result.awareness_generated == 1
    assert healthy.call_count == 1  # one healthy call, no retry needed


# ---------------------------------------------------------------------------
# Cursor + batch incremental reads (Todo 2)
# ---------------------------------------------------------------------------


class _RecordingAwarenessAnalyzer:
    """Records the events / max_tokens of each analyze call; one note per call.

    ``fail_after_success`` makes analyze raise once it has already succeeded
    that many times — used to assert per-batch watermark durability.
    """

    def __init__(self, *, fail_after_success: int | None = None) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.max_tokens_seen: list[int] = []
        self._fail_after_success = fail_after_success
        self._success_count = 0

    async def analyze(
        self,
        *,
        events: list[dict[str, object]],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        max_tokens: int = 0,
    ) -> list[Any]:
        self.calls.append([dict(e) for e in events])
        self.max_tokens_seen.append(max_tokens)
        if self._fail_after_success is not None and self._success_count >= self._fail_after_success:
            raise AwarenessGenerationError("simulated failure after N successes")
        self._success_count += 1
        from openbiliclaw.soul.profile import AwarenessNote

        return [
            AwarenessNote(
                date="2026-06-15",
                observation=f"obs-{len(self.calls)}",
                trend="",
                emotion_guess="",
            )
        ]

    def merge_notes(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


class _RecordingInsightAnalyzer:
    """Records the awareness notes + existing hypotheses passed to each call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, list[str]]] = []
        self.max_tokens_seen: list[int] = []

    async def analyze(
        self,
        *,
        awareness_notes: list[Any],
        preference: dict[str, object],
        soul_profile: dict[str, object],
        existing_insights: list[Any] | None = None,
        max_tokens: int = 0,
    ) -> list[Any]:
        self.calls.append(
            {
                "notes": [getattr(n, "observation", "") for n in awareness_notes],
                "existing": [getattr(h, "hypothesis", "") for h in (existing_insights or [])],
            }
        )
        self.max_tokens_seen.append(max_tokens)
        from openbiliclaw.soul.profile import InsightHypothesis

        return [
            InsightHypothesis(hypothesis=f"hyp-{len(self.calls)}", evidence=["e"], confidence=0.5)
        ]

    def merge_insights(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


def _event_ids(call_events: list[dict[str, Any]]) -> set[int]:
    return {int(e["id"]) for e in call_events}


@pytest.mark.asyncio
async def test_awareness_cursor_covers_backlog_and_batches(tmp_path: Path) -> None:
    """A backlog larger than one batch is fully covered across batches.

    The old fixed ``limit=50`` would silently drop the oldest events; the cursor
    path processes the whole backlog (batching only when it exceeds the batch
    size, which is sized so normal windows are a single call) and advances the
    watermark to the newest id. Referenced off the constant so it survives
    future batch-size tweaks. Also asserts the larger cognition max_tokens.
    """
    import math

    from openbiliclaw.soul.cognition_cycle import (
        _AWARENESS_EVENT_BATCH_SIZE,
        _COGNITION_MAX_TOKENS,
    )

    # One batch + a remainder → forces exactly two batches regardless of size.
    count = _AWARENESS_EVENT_BATCH_SIZE + 30
    expected_batches = math.ceil(count / _AWARENESS_EVENT_BATCH_SIZE)
    assert expected_batches == 2

    memory = _seed_memory(tmp_path, event_count=0)
    _add_events(memory, count)
    rec = _RecordingAwarenessAnalyzer()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=rec,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    now = datetime(2026, 6, 15, 12, 0, 0)
    result = await cycle.run_if_due(now=now)

    assert result.ran is True
    assert result.errors == []
    assert len(rec.calls) == expected_batches
    # No event dropped: every id 1..count reached the analyzer.
    seen = set().union(*(_event_ids(c) for c in rec.calls))
    assert seen == set(range(1, count + 1))
    # Larger token budget honoured on every call.
    assert rec.max_tokens_seen == [_COGNITION_MAX_TOKENS] * expected_batches
    # Watermark advanced to the newest event.
    assert cycle._load_state()["last_awareness_event_id"] == count  # noqa: SLF001


@pytest.mark.asyncio
async def test_awareness_single_call_for_normal_window(tmp_path: Path) -> None:
    """A normal-sized window (≤ batch size) is one LLM call, not split.

    Locks in the "don't force batching" intent: 60 events used to split 50+10;
    with the large batch size it must be a single call.
    """
    from openbiliclaw.soul.cognition_cycle import _AWARENESS_EVENT_BATCH_SIZE

    memory = _seed_memory(tmp_path, event_count=0)
    _add_events(memory, 60)
    assert _AWARENESS_EVENT_BATCH_SIZE >= 60  # guards the premise
    rec = _RecordingAwarenessAnalyzer()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=rec,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    await cycle.run_if_due(now=datetime(2026, 6, 15, 12, 0, 0))

    assert len(rec.calls) == 1
    assert _event_ids(rec.calls[0]) == set(range(1, 61))
    assert cycle._load_state()["last_awareness_event_id"] == 60  # noqa: SLF001


@pytest.mark.asyncio
async def test_awareness_watermark_prevents_reprocessing(tmp_path: Path) -> None:
    """A second window only re-reads the new events (plus a bounded lookback)."""
    memory = _seed_memory(tmp_path, event_count=0)
    _add_events(memory, 130)
    rec = _RecordingAwarenessAnalyzer()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=rec,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )
    await cycle.run_if_due(now=datetime(2026, 6, 15, 12, 0, 0))
    calls_after_first = len(rec.calls)

    # Five new events arrive; tick again after the throttle window.
    _add_events(memory, 5, prefix="新事件")
    await cycle.run_if_due(now=datetime(2026, 6, 16, 12, 0, 0))

    # Exactly one more analyze call (5 new events < one batch).
    assert len(rec.calls) == calls_after_first + 1
    last_call_ids = _event_ids(rec.calls[-1])
    # The 5 genuinely-new events were processed...
    assert {131, 132, 133, 134, 135}.issubset(last_call_ids)
    # ...and old events are NOT fully reprocessed — only the bounded lookback
    # (10 most recent already-seen) rides along; id 100 is well outside it.
    assert 100 not in last_call_ids
    assert min(last_call_ids) >= 121  # lookback window floor
    assert cycle._load_state()["last_awareness_event_id"] == 135  # noqa: SLF001


@pytest.mark.asyncio
async def test_awareness_skips_when_no_new_events(tmp_path: Path) -> None:
    """No events newer than the watermark → no LLM call, no waste."""
    memory = _seed_memory(tmp_path, event_count=0)  # gate cleared, zero events
    rec = _RecordingAwarenessAnalyzer()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=rec,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    result = await cycle.run_if_due(now=datetime(2026, 6, 15, 12, 0, 0))

    assert result.ran is True
    assert result.awareness_generated == 0
    assert rec.calls == []  # analyzer never invoked


@pytest.mark.asyncio
async def test_awareness_partial_progress_survives_midbatch_failure(tmp_path: Path) -> None:
    """First batch's watermark persists even when a later batch fails twice."""
    from openbiliclaw.soul.cognition_cycle import _AWARENESS_EVENT_BATCH_SIZE

    batch = _AWARENESS_EVENT_BATCH_SIZE
    memory = _seed_memory(tmp_path, event_count=0)
    _add_events(memory, batch + 60)  # → batches of `batch`, then 60
    rec = _RecordingAwarenessAnalyzer(fail_after_success=1)  # batch 1 ok, batch 2 fails
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=rec,  # type: ignore[arg-type]
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    result = await cycle.run_if_due(now=datetime(2026, 6, 15, 12, 0, 0))

    # Batch 1 (call 1) ok; batch 2 fails on call 2 and its single retry (call 3).
    assert len(rec.calls) == 3
    assert any("awareness" in err.lower() for err in result.errors)
    state = cycle._load_state()  # noqa: SLF001
    # Watermark advanced to the end of batch 1 only, so the next tick resumes
    # from there rather than dropping or reprocessing batch 1.
    assert state["last_awareness_event_id"] == batch
    # The failed window will be retried: last_awareness_at was NOT advanced.
    assert not state.get("last_awareness_at")


def _set_awareness_notes(memory: MemoryManager, count: int) -> None:
    notes = [
        {"date": "2026-06-15", "observation": f"觉察{i}", "trend": "", "emotion_guess": ""}
        for i in range(count)
    ]
    memory.get_layer("awareness").update("notes", notes)


@pytest.mark.asyncio
async def test_insight_cursor_processes_only_new_notes_with_existing_context(
    tmp_path: Path,
) -> None:
    """Insight reads notes after its cursor and gets current hypotheses as context."""
    memory = _seed_memory(tmp_path, event_count=0)
    _set_awareness_notes(memory, 3)
    memory.get_layer("insight").update(
        "hypotheses",
        [
            {
                "hypothesis": "已有假设",
                "evidence": ["e"],
                "confidence": 0.6,
                "validated": False,
                "created_at": "2026-06-14",
            }
        ],
    )
    rec = _RecordingInsightAnalyzer()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=_FlakyAwarenessAnalyzer(fail_first_n=0, succeed_payload=[]),  # type: ignore[arg-type]
        insight_analyzer=rec,  # type: ignore[arg-type]
        min_interval_seconds=60,
    )

    state: dict[str, Any] = {}
    added = await cycle._run_insight(state)  # noqa: SLF001

    assert added == 1
    assert len(rec.calls) == 1
    # Saw all 3 unprocessed notes + the existing hypothesis as context.
    assert rec.calls[0]["notes"] == ["觉察0", "觉察1", "觉察2"]
    assert rec.calls[0]["existing"] == ["已有假设"]
    assert state["last_insight_awareness_index"] == 3
    from openbiliclaw.soul.cognition_cycle import _COGNITION_MAX_TOKENS

    assert rec.max_tokens_seen == [_COGNITION_MAX_TOKENS]

    # Two more awareness notes arrive → only those two are reprocessed.
    _set_awareness_notes(memory, 5)
    added2 = await cycle._run_insight(state)  # noqa: SLF001

    assert added2 == 1
    assert len(rec.calls) == 2
    assert rec.calls[1]["notes"] == ["觉察3", "觉察4"]  # cursor skipped 0..2
    assert state["last_insight_awareness_index"] == 5
