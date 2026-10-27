"""Blue Fish loop — discovery tick runs before the action pass."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from openbiliclaw.fish.config import FishConfig, save_fish_config
from openbiliclaw.fish.engine import run_fish_loop


class _StubEngine:
    """Only the slice ``run_fish_loop`` touches."""

    def __init__(
        self,
        calls: list[str],
        *,
        llm_available: bool = True,
        pending_plan: bool = False,
    ) -> None:
        self._calls = calls
        self.llm_available = llm_available
        self.has_pending_plan = pending_plan

    async def run_once(self, **kwargs: Any) -> Any:
        self._calls.append("run")
        return SimpleNamespace(status="published")


async def _one_tick(tmp_path: Any, calls: list[str], tick: Any) -> None:
    task = asyncio.create_task(
        run_fish_loop(
            _StubEngine(calls),
            data_dir=tmp_path,
            interval_seconds=3600,
            discovery_tick=tick,
        )
    )
    try:
        for _ in range(500):
            if "run" in calls:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_the_discovery_tick_runs_before_the_action_pass(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=True, dry_run=False))
    calls: list[str] = []

    async def _tick() -> None:
        calls.append("discover")

    await _one_tick(tmp_path, calls, _tick)

    assert calls == ["discover", "run"]


async def test_a_failing_discovery_tick_does_not_block_the_action_pass(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=True, dry_run=False))
    calls: list[str] = []

    async def _broken() -> None:
        calls.append("discover")
        raise RuntimeError("pool unavailable")

    await _one_tick(tmp_path, calls, _broken)

    assert calls == ["discover", "run"]


async def test_without_a_model_the_discovery_tick_is_skipped(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=True, dry_run=False))
    calls: list[str] = []

    async def _tick() -> None:
        calls.append("discover")

    task = asyncio.create_task(
        run_fish_loop(
            _StubEngine(calls, llm_available=False),
            data_dir=tmp_path,
            discovery_tick=_tick,
        )
    )
    for _ in range(200):
        if "run" in calls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert calls == ["run"]


async def test_the_loop_is_inert_when_the_lab_is_disabled(tmp_path) -> None:
    save_fish_config(tmp_path, FishConfig(enabled=False, dry_run=False))
    calls: list[str] = []

    async def _tick() -> None:
        calls.append("discover")

    task = asyncio.create_task(
        run_fish_loop(_StubEngine(calls), data_dir=tmp_path, discovery_tick=_tick)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert calls == []


async def test_a_pending_plan_stops_further_comment_scans(tmp_path) -> None:
    """One plan in hand is enough: the tick must not rescan while it waits."""

    save_fish_config(tmp_path, FishConfig(enabled=True, dry_run=False))
    calls: list[str] = []

    async def _tick() -> None:
        calls.append("discover")

    task = asyncio.create_task(
        run_fish_loop(
            _StubEngine(calls, pending_plan=True),
            data_dir=tmp_path,
            discovery_tick=_tick,
        )
    )
    for _ in range(200):
        if "run" in calls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert calls == ["run"]
