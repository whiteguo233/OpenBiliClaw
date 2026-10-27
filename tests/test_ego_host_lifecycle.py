"""Managed Ego host reclamation: leases, idle stop and shutdown escalation."""

import asyncio
import signal
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources import ego_setup
from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import EgoLiteWorkflowExecutor, EgoLiteWorkflowSession
from openbiliclaw.sources.ego_setup import EgoSetupManager


@pytest.fixture
def plan():
    return compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://books.example.com/",
            "allowed_origins": ["https://books.example.com"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )


def _fake_app(manager: EgoSetupManager) -> None:
    manager.executable.parent.mkdir(parents=True, exist_ok=True)
    manager.executable.touch()


def test_shared_lease_blocks_exclusive_reclamation(tmp_path):
    manager = EgoSetupManager(tmp_path)
    session = manager.acquire_session_lease()
    try:
        assert manager.try_exclusive_session_lease() is None
    finally:
        manager.release_session_lease(session)
    lease = manager.try_exclusive_session_lease()
    assert lease is not None
    manager.release_session_lease(lease)
    assert manager.try_exclusive_session_lease() is not None


async def test_last_session_close_schedules_idle_stop(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBILICLAW_EGO_IDLE_STOP_SECONDS", "0.05")
    executor = EgoLiteWorkflowExecutor(tmp_path)
    _fake_app(executor._setup)
    stop = AsyncMock(return_value={})
    monkeypatch.setattr(executor._setup, "stop", stop)
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(executor, plan, interactive=False, lock=lock)
    session._ensure_lease()
    session._started = True
    await session.close()
    await asyncio.sleep(0.2)
    stop.assert_awaited_once()
    assert str(executor._setup.root) not in ego_setup._IDLE_TASKS


async def test_idle_stop_keeps_host_while_another_session_lease_is_held(tmp_path, monkeypatch):
    executor = EgoLiteWorkflowExecutor(tmp_path)
    _fake_app(executor._setup)
    stop = AsyncMock(return_value={})
    monkeypatch.setattr(executor._setup, "stop", stop)
    other = executor._setup.acquire_session_lease()
    try:
        await executor._setup._stop_when_idle(str(executor._setup.root), 0.0)
    finally:
        executor._setup.release_session_lease(other)
    stop.assert_not_awaited()


async def test_external_host_is_never_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", str(tmp_path / "ego-browser"))
    executor = EgoLiteWorkflowExecutor(tmp_path)
    assert executor.managed is False
    executor.schedule_idle_stop(grace=0.0)
    assert str(executor._setup.root) not in ego_setup._IDLE_TASKS


async def test_stop_escalates_to_sigkill_for_a_hung_host(tmp_path, monkeypatch):
    manager = EgoSetupManager(tmp_path)
    manager.root.mkdir(parents=True)
    manager._write(pid=4242, initialized=True)
    alive = {"value": True}
    monkeypatch.setattr(manager, "_pid", lambda: 4242 if alive["value"] else None)
    signals = []

    def fake_kill(pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == signal.SIGKILL:
            alive["value"] = False

    monkeypatch.setattr("openbiliclaw.sources.ego_setup.os.kill", fake_kill)
    await manager._stop(exit_budget=0.1)
    assert signal.SIGTERM in signals and signal.SIGKILL in signals
    assert manager._state().get("pid") is None


async def test_stop_is_a_noop_for_uninitialized_or_external_hosts(tmp_path, monkeypatch):
    manager = EgoSetupManager(tmp_path)
    monkeypatch.setattr(manager, "_stop", AsyncMock())
    await manager.stop()
    manager._stop.assert_not_awaited()
    monkeypatch.setenv("OPENBILICLAW_EGO_BROWSER", str(tmp_path / "ego-browser"))
    manager.root.mkdir(parents=True)
    manager._write(pid=4242, initialized=True)
    await manager.stop()
    manager._stop.assert_not_awaited()


async def test_failed_native_close_shortens_idle_grace(plan, tmp_path, monkeypatch):
    executor = EgoLiteWorkflowExecutor(tmp_path)
    _fake_app(executor._setup)
    scheduled: list[float | None] = []
    monkeypatch.setattr(
        executor, "schedule_idle_stop", lambda *, grace=None: scheduled.append(grace)
    )
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(executor, plan, interactive=False, lock=lock)
    session._ensure_lease()
    session._started = True
    from types import SimpleNamespace
    from unittest.mock import Mock

    session._writer = SimpleNamespace(close=Mock())
    session.command = AsyncMock(side_effect=TimeoutError)
    await session.close()
    assert scheduled == [0.0]
