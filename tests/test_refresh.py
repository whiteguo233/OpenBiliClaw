"""Tests for ContinuousRefreshController.run_init_backfill (gui-init plan B1)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.runtime.refresh import ContinuousRefreshController


class _FakeDisc:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock: asyncio.Lock | None = None
        self.locked_during: bool | None = None

    async def discover(
        self, profile: Any, *, strategies: Any, limit: int, fully_parallel: bool
    ) -> list[str]:
        self.calls.append(
            {"strategies": strategies, "limit": limit, "fully_parallel": fully_parallel}
        )
        if self.lock is not None:
            self.locked_during = self.lock.locked()
        return ["a", "b"]


class _FakeDB:
    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)

    def count_pool_candidates(self, **_kw: Any) -> int:
        return self._counts.pop(0) if self._counts else 999


def _ctrl(db: Any, disc: Any) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=SimpleNamespace(),
        database=db,
        soul_engine=SimpleNamespace(),
        discovery_engine=disc,
        recommendation_engine=SimpleNamespace(),
    )


async def test_run_init_backfill_discovers_with_expected_shape() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([0]), disc)
    n = await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert n == 2
    assert disc.calls == [
        {
            "strategies": ["search", "trending", "related_chain", "explore"],
            "limit": 20,  # max(20, target - current)
            "fully_parallel": True,
        }
    ]


async def test_run_init_backfill_skips_when_pool_already_full() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([50]), disc)  # already above target
    n = await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert n == 0
    assert disc.calls == []


async def test_run_init_backfill_holds_refresh_lock() -> None:
    disc = _FakeDisc()
    ctrl = _ctrl(_FakeDB([0]), disc)
    disc.lock = ctrl._refresh_lock
    await ctrl.run_init_backfill(object(), target_pool_count=15)
    assert disc.locked_during is True  # lock held while discovering
    assert ctrl._refresh_lock.locked() is False  # released after


async def test_run_init_backfill_releases_lock_on_cancel() -> None:
    class _SlowDisc:
        async def discover(self, *_a: Any, **_k: Any) -> list[str]:
            await asyncio.sleep(60)
            return []

    ctrl = _ctrl(_FakeDB([0]), _SlowDisc())
    task = asyncio.create_task(ctrl.run_init_backfill(object(), target_pool_count=15))
    await asyncio.sleep(0.05)  # let it acquire the lock + enter discover
    assert ctrl._refresh_lock.locked() is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctrl._refresh_lock.locked() is False


def test_llm_work_gate_blocks_while_init_active() -> None:
    """gui-init D1: the controller's background loops pause while a guided init
    is active (account_sync already gates on the same predicate)."""
    ctrl = _ctrl(_FakeDB([0]), _FakeDisc())
    baseline = ctrl._llm_work_allowed()  # no init check wired → underlying gate

    ctrl.init_active_check = lambda: True
    assert ctrl._llm_work_allowed() is False  # forced off regardless of baseline

    ctrl.init_active_check = lambda: False
    assert ctrl._llm_work_allowed() == baseline  # back to the underlying gate

    def _boom() -> bool:
        raise RuntimeError("boom")

    ctrl.init_active_check = _boom  # defensive: a raising check never crashes
    assert ctrl._llm_work_allowed() == baseline
