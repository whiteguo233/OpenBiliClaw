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
    ) -> list[Any]:
        return []

    def merge_insights(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


def _seed_memory(tmp_path: Path) -> MemoryManager:
    """Build a MemoryManager with enough data to clear the empty-layers gate."""
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").update("interests", ["深度内容"])
    memory.get_layer("soul").update("personality_portrait", "理性又敏感")
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
