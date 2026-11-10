"""Regression tests for delegated background-loop ownership and takeover.

Background: on 2026-10-15 a Windows desktop install stopped refreshing content.
The API had delegated every periodic loop to child workers, and the full worker
— spawned *with* ``OPENBILICLAW_FULL_WORKER=1`` — read that same flag to decide
whether it should start the loops, concluded "delegated", and started none.
The discovery/pool refresh loop then ran in no process for 23 days while the
API kept logging ``External full worker active``.

Two invariants keep that failure mode from coming back:

* loop ownership is passed in explicitly by the process that owns the loop,
  never inferred from the delegation flag inside the delegatee;
* the API re-checks that the delegated owner is actually heartbeating and
  takes the loop back if it is not.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.api.app import (
    _delegated_loop_takeover_loop,
    _schedule_delegated_loop_takeover,
    _take_over_delegated_loops,
)
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.config import Config

if TYPE_CHECKING:
    from pathlib import Path


class _RecordingLoop:
    """``run_forever`` stand-in that records how many times it was started."""

    def __init__(self) -> None:
        self.started = 0

    async def run_forever(self) -> None:
        self.started += 1
        await asyncio.sleep(3600)


def _context(tmp_path: Path, *, refresh: Any, sync: Any) -> RuntimeContext:
    cfg = Config()
    cfg.scheduler.enabled = False
    cfg.data_dir = str(tmp_path)
    return RuntimeContext(
        config=cfg,
        memory_manager=SimpleNamespace(),
        runtime_controller=refresh,
        account_sync_service=sync,
        auto_update_service=SimpleNamespace(),
        soul_engine=None,
        recommendation_engine=None,
    )


def _write_status(data_path: Path, filename: str, *, age_seconds: float) -> None:
    runtime_dir = data_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / filename).write_text(
        json.dumps(
            {
                "mode": "full",
                "pid": 1234,
                "started_at": time.time() - age_seconds,
                "heartbeat_at": time.time() - age_seconds,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_explicit_ownership_starts_refresh_loop_despite_delegation_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full worker must be able to start loops it is delegated *to* run."""

    monkeypatch.setenv("OPENBILICLAW_FULL_WORKER", "1")
    refresh = _RecordingLoop()
    sync = _RecordingLoop()
    ctx = _context(tmp_path, refresh=refresh, sync=sync)
    app = SimpleNamespace(state=SimpleNamespace())

    await ctx.restart_background_tasks(
        app,
        start_refresh_controller=True,
        start_account_sync=True,
    )

    assert app.state.refresh_task is not None
    assert app.state.account_sync_task is not None
    await asyncio.sleep(0)
    assert refresh.started == 1
    assert sync.started == 1


@pytest.mark.asyncio
async def test_delegation_flag_still_skips_loops_when_ownership_is_unspecified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API must keep deferring when it says nothing about ownership."""

    monkeypatch.setenv("OPENBILICLAW_FULL_WORKER", "1")
    refresh = _RecordingLoop()
    sync = _RecordingLoop()
    ctx = _context(tmp_path, refresh=refresh, sync=sync)
    app = SimpleNamespace(state=SimpleNamespace())

    await ctx.restart_background_tasks(app)

    assert app.state.refresh_task is None
    assert app.state.account_sync_task is None
    await asyncio.sleep(0)
    assert refresh.started == 0
    assert sync.started == 0


@pytest.mark.asyncio
async def test_single_process_mode_starts_both_loops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENBILICLAW_FULL_WORKER", raising=False)
    monkeypatch.delenv("OPENBILICLAW_WORKER", raising=False)
    refresh = _RecordingLoop()
    sync = _RecordingLoop()
    ctx = _context(tmp_path, refresh=refresh, sync=sync)
    app = SimpleNamespace(state=SimpleNamespace())

    await ctx.restart_background_tasks(app)

    await asyncio.sleep(0)
    assert refresh.started == 1
    assert sync.started == 1


@pytest.mark.asyncio
async def test_delegated_owner_health_flags_stale_heartbeats(tmp_path: Path) -> None:
    """A worker that never reported, or stopped heartbeating, is not alive."""

    ctx = _context(tmp_path, refresh=_RecordingLoop(), sync=_RecordingLoop())

    # No status file at all: the owner never reported. The 10-minute grace
    # period in app.py is what protects a slow cold start, not this probe.
    assert ctx.delegated_owner_health() == {"refresh": False, "account_sync": False}

    _write_status(tmp_path, "discovery_worker_status.json", age_seconds=1)
    _write_status(tmp_path, "worker_status.json", age_seconds=1)
    assert ctx.delegated_owner_health() == {"refresh": True, "account_sync": True}

    _write_status(tmp_path, "discovery_worker_status.json", age_seconds=3600)
    assert ctx.delegated_owner_health() == {"refresh": False, "account_sync": True}


@pytest.mark.asyncio
async def test_takeover_starts_only_the_loops_whose_owner_is_dead() -> None:
    calls: list[dict[str, Any]] = []

    class _FakeContext:
        async def restart_background_tasks(self, app: Any, **kwargs: Any) -> None:
            calls.append(kwargs)

    app = SimpleNamespace(state=SimpleNamespace())

    await _take_over_delegated_loops(_FakeContext(), app, {"refresh": False, "account_sync": True})

    assert calls == [{"start_refresh_controller": True, "start_account_sync": False}]
    assert app.state.delegated_takeover_active is True


@pytest.mark.asyncio
async def test_takeover_loop_takes_over_once_the_owner_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    slept: list[float] = []

    class _FakeContext:
        def delegated_owner_health(self) -> dict[str, bool]:
            return {"refresh": False, "account_sync": True}

        async def restart_background_tasks(self, app: Any, **kwargs: Any) -> None:
            calls.append(kwargs)

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("openbiliclaw.api.app._DELEGATED_TAKEOVER_GRACE_SECONDS", 0.0)

    app = SimpleNamespace(state=SimpleNamespace())
    await _delegated_loop_takeover_loop(_FakeContext(), app)

    # Grace period first, then the takeover — never an immediate pre-emption.
    assert slept == [0.0]
    assert calls == [{"start_refresh_controller": True, "start_account_sync": False}]


@pytest.mark.asyncio
async def test_takeover_loop_keeps_polling_while_the_owner_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    probes = 0

    class _FakeContext:
        def delegated_owner_health(self) -> dict[str, bool]:
            nonlocal probes
            probes += 1
            if probes == 1:
                return {"refresh": True, "account_sync": True}
            raise RuntimeError("stop polling")

        async def restart_background_tasks(self, app: Any, **kwargs: Any) -> None:
            calls.append(kwargs)

    async def _no_sleep(_seconds: float) -> None:
        return

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("openbiliclaw.api.app._DELEGATED_TAKEOVER_GRACE_SECONDS", 0.0)

    await _delegated_loop_takeover_loop(_FakeContext(), SimpleNamespace(state=SimpleNamespace()))

    assert probes == 2
    assert calls == []


@pytest.mark.asyncio
async def test_takeover_grace_is_anchored_to_process_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config hot reload must not hand a dead owner a fresh grace period.

    Each reload rebuilds the runtime and reschedules the watchdog; timing the
    grace from watchdog creation would let a user who saves settings every few
    minutes postpone the takeover forever.
    """

    calls: list[dict[str, Any]] = []
    slept: list[float] = []

    class _FakeContext:
        def delegated_owner_health(self) -> dict[str, bool]:
            return {"refresh": False, "account_sync": False}

        async def restart_background_tasks(self, app: Any, **kwargs: Any) -> None:
            calls.append(kwargs)

    async def _no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("openbiliclaw.api.app._DELEGATED_TAKEOVER_GRACE_SECONDS", 600.0)
    monkeypatch.setattr(
        "openbiliclaw.api.app._DELEGATED_OWNER_GRACE_STARTED_AT",
        time.monotonic() - 10_000.0,
    )

    await _delegated_loop_takeover_loop(_FakeContext(), SimpleNamespace(state=SimpleNamespace()))

    assert slept == [0.0]
    assert calls == [{"start_refresh_controller": True, "start_account_sync": True}]


@pytest.mark.asyncio
async def test_takeover_watchdog_is_scheduled_once(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []

    class _Registry:
        def track(self, name: str, awaitable: Any) -> asyncio.Task[Any]:
            created.append(name)
            return asyncio.ensure_future(awaitable)

    class _FakeContext:
        task_registry = _Registry()

    app = SimpleNamespace(state=SimpleNamespace())
    ctx = _FakeContext()

    _schedule_delegated_loop_takeover(ctx, app)
    _schedule_delegated_loop_takeover(ctx, app)

    assert created == ["delegated_loop_takeover"]

    task = app.state.delegated_takeover_task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
