"""Tests for the BackgroundTaskRegistry (v0.3.63+).

Covers the contract documented in ``src/openbiliclaw/runtime/task_registry.py``:

- ``track`` returns the spawned ``asyncio.Task`` and records it
- Completed tasks self-untrack via the ``add_done_callback`` hook
- ``cancel_all`` cancels every tracked task and reports the count
- A "stuck" task that ignores cancellation triggers a warning log and
  the registry is still cleared so the new runtime can start clean
- ``stats`` groups live tasks by name prefix

All tests are async — pytest's ``asyncio_mode = "auto"`` config applies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry


async def test_track_returns_task_and_registers_it() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    task = registry.track("hold", _hold())
    try:
        assert isinstance(task, asyncio.Task)
        assert len(registry._tasks) == 1
        assert registry._tasks[task] == "hold"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_completed_task_self_untracks() -> None:
    registry = BackgroundTaskRegistry()

    async def _quick() -> int:
        return 7

    task = registry.track("quick", _quick())
    result = await task
    # ``add_done_callback`` is scheduled separately from the awaited task,
    # so allow one event-loop turn for the callback to fire.
    await asyncio.sleep(0)
    assert result == 7
    assert len(registry._tasks) == 0


async def test_cancel_all_cancels_every_task_and_returns_count() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("a", _hold())
    registry.track("b", _hold())
    registry.track("c", _hold())

    cancelled = await registry.cancel_all()
    assert cancelled == 3
    assert len(registry._tasks) == 0


async def test_cancel_all_with_hung_task_logs_warning_and_clears(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the grace-window timeout path and verify the warning fires.

    Reliably constructing a "task that ignores cancellation" is brittle
    across asyncio versions — depending on the interpreter, ``shield``
    may or may not let cancellation propagate before the timer fires.
    The contract this test cares about is "if ``asyncio.wait_for`` raises
    ``TimeoutError``, log a warning and still clear the registry", so
    we patch ``asyncio.wait_for`` to raise unconditionally — the
    behaviour under the timeout path is what matters, not how realistic
    the underlying task is.
    """
    registry = BackgroundTaskRegistry()

    async def _quick_done() -> None:
        # An immediate-return coroutine — gather would normally finish
        # cleanly, but we patch wait_for so the timeout branch fires
        # regardless. Using a real, simple task ensures the cleanup
        # path (clearing ``_tasks``) actually has something to clear.
        return None

    task = registry.track("stuck", _quick_done())

    async def _fake_wait_for(_awaitable: object, *, timeout: float) -> object:
        raise TimeoutError

    # Patch on the registry's module so the import in cancel_all picks
    # up the fake implementation.
    import openbiliclaw.runtime.task_registry as registry_mod

    monkeypatch.setattr(registry_mod.asyncio, "wait_for", _fake_wait_for)

    try:
        with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.task_registry"):
            cancelled = await registry.cancel_all(grace_seconds=0.05)
        assert cancelled == 1
        assert len(registry._tasks) == 0
        assert any("did not exit within" in record.message for record in caplog.records), (
            f"expected warning log, got {[r.message for r in caplog.records]}"
        )
    finally:
        # Drain the underlying task so the loop closes cleanly.
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            await task


async def test_stats_groups_by_name_prefix() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("refresh.manual", _hold())
    registry.track("refresh.precompute", _hold())
    registry.track("delight.scoring", _hold())
    registry.track("plain", _hold())

    try:
        stats = registry.stats()
        assert stats == {"refresh": 2, "delight": 1, "plain": 1}
    finally:
        await registry.cancel_all()


async def test_cancel_all_exclude_keeps_named_task() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    keep = registry.track("guided_init", _hold())
    registry.track("a", _hold())
    registry.track("b", _hold())

    try:
        cancelled = await registry.cancel_all(exclude=frozenset({"guided_init"}))
        assert cancelled == 2  # a + b, not guided_init
        assert not keep.done()  # excluded task left running
        assert registry._tasks.get(keep) == "guided_init"  # still tracked
    finally:
        await registry.cancel_all()
        with pytest.raises(asyncio.CancelledError):
            await keep


async def test_cancel_by_name_stops_only_that_task() -> None:
    registry = BackgroundTaskRegistry()

    async def _hold() -> None:
        await asyncio.sleep(10)

    registry.track("x", _hold())
    registry.track("x", _hold())
    other = registry.track("y", _hold())

    try:
        cancelled = await registry.cancel("x")
        assert cancelled == 2
        assert not other.done()
        assert list(registry._tasks.values()) == ["y"]
    finally:
        await registry.cancel_all()
        with pytest.raises(asyncio.CancelledError):
            await other
