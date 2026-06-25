from __future__ import annotations

import asyncio

import pytest

from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler


class FakeSoulEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        self.calls += 1
        return {"triggered": True}


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_coalesces_burst() -> None:
    soul = FakeSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    for _ in range(5):
        scheduler.schedule()
    await scheduler.drain()

    assert soul.calls == 1


@pytest.mark.asyncio
async def test_feedback_batch_scheduler_runs_again_when_dirty_during_processing() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowSoulEngine(FakeSoulEngine):
        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                started.set()
                await release.wait()
            return {"triggered": True}

    soul = SlowSoulEngine()
    scheduler = FeedbackBatchScheduler(soul, debounce_seconds=0)

    scheduler.schedule()
    await started.wait()
    scheduler.schedule()
    release.set()
    await scheduler.drain()

    assert soul.calls == 2
