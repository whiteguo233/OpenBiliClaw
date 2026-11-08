"""Native readiness may outlast 15s while remaining inside the bounded startup wait."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.sources import ego_executor
from openbiliclaw.sources.browser_workflow import compile_browser_workflow
from openbiliclaw.sources.ego_executor import EgoLiteWorkflowExecutor, EgoLiteWorkflowSession
from openbiliclaw.sources.workflow_executors import BrowserWorkflowExecutionError


@pytest.mark.parametrize("ready_after", [20.0, 40.0, None])
async def test_native_ready_waits_for_delayed_host_and_still_bounds_failure(
    tmp_path, monkeypatch, ready_after
):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://forum.example/",
            "allowed_origins": ["https://forum.example"],
            "budgets": {"timeout_seconds": 45},
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    monkeypatch.setattr(executor, "schedule_idle_stop", lambda **kwargs: None)
    monkeypatch.setattr(executor._setup, "_pid", lambda: None)
    monkeypatch.setattr(ego_executor, "_host_pid", lambda name: None)
    lock = asyncio.Lock()
    await lock.acquire()
    session = EgoLiteWorkflowSession(executor, plan, interactive=False, lock=lock)
    monkeypatch.setattr(session, "_open_entry", AsyncMock())
    process = SimpleNamespace(returncode=None, wait=AsyncMock(return_value=0))
    process.terminate = lambda: setattr(process, "returncode", 0)
    process.kill = process.terminate
    config = {}

    async def spawn(*args, **kwargs):
        config.update(json.loads((Path(session._temporary.name) / "config.json").read_text()))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    clock = {"now": 0.0}
    monkeypatch.setattr(ego_executor, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    real_sleep = asyncio.sleep

    async def tick(seconds):
        clock["now"] += seconds
        if clock["now"] >= 5:
            Path(config["identity"]).write_text(
                json.dumps(
                    {
                        "task_id": 42,
                        "task_name": "OpenBiliClaw-budget-test",
                    }
                )
            )
        if ready_after is not None and clock["now"] >= ready_after:
            Path(config["ready"]).write_text(json.dumps({"task_id": 42, "version": "0.6.0.20"}))
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", tick)
    reader = asyncio.StreamReader()

    class Writer:
        def write(self, data):
            message = json.loads(data)
            reader.feed_data(json.dumps({"id": message["id"], "result": True}).encode() + b"\n")

        async def drain(self):
            pass

        def is_closing(self):
            return False

        def close(self):
            reader.feed_eof()

        async def wait_closed(self):
            pass

    monkeypatch.setattr(asyncio, "open_unix_connection", AsyncMock(return_value=(reader, Writer())))
    state = {"task_id": None, "task_name": "OpenBiliClaw-budget-test", "profile": "Default"}
    try:
        if ready_after is None:
            with pytest.raises(BrowserWorkflowExecutionError, match="native connection"):
                await session.start(tmp_path / "state.json", state)
            budget = plan.budgets.timeout_seconds
            assert budget <= clock["now"] <= budget + 0.1
            session._open_entry.assert_not_awaited()
            assert json.loads((tmp_path / "state.json").read_text())["task_id"] == 42
        else:
            await session.start(tmp_path / "state.json", state)
            assert state["task_id"] == 42
            session._open_entry.assert_awaited_once()
    finally:
        await session.close()
    assert process.returncode == 0
    assert not lock.locked()
    assert session._temporary is None or not Path(session._temporary.name).exists()


@pytest.mark.parametrize(
    "managed,code",
    [
        (True, "ego_connection_timeout"),
        (False, "ego_connection_timeout"),
        (True, "ego_protocol_timeout"),
        (True, "ego_session_identity_changed"),
    ],
)
async def test_only_managed_unready_connection_retries_after_cleanup(
    tmp_path, monkeypatch, managed, code
):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://forum.example/",
            "allowed_origins": ["https://forum.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    executor.managed = managed
    monkeypatch.setattr(executor, "schedule_idle_stop", lambda **kwargs: None)
    monkeypatch.setattr(executor._setup, "status", lambda: {"available": True})
    monkeypatch.setattr(
        executor,
        "availability",
        lambda: {
            "installed": True,
            "platform_supported": True,
            "connection_state": "ready",
            "available": True,
        },
    )
    created = []
    identities = []

    async def start(session, state_file, state):
        created.append(session)
        identities.append(state["task_name"])
        if len(created) == 1:
            raise BrowserWorkflowExecutionError(code, "Native initialization failed before ready")
        assert created[0]._closed and not session._closed
        session._started = True

    monkeypatch.setattr(EgoLiteWorkflowSession, "start", start)
    if managed and code in {"ego_connection_timeout", "ego_protocol_timeout"}:
        session = await executor.open_session(plan, session_id="owned-session")
        assert len(created) == 2
        assert identities[0] == identities[1]
        await session.close()
    else:
        with pytest.raises(BrowserWorkflowExecutionError):
            await executor.open_session(plan, session_id="owned-session")
        assert len(created) == 1
    assert all(s._closed and not s.lock.locked() for s in created)


@pytest.mark.parametrize("failure", ["timeout", "cancelled"])
async def test_managed_native_retry_is_bounded_and_preserves_cancellation(
    tmp_path, monkeypatch, failure
):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://forum.example/",
            "allowed_origins": ["https://forum.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    executor.managed = True
    error = (
        asyncio.CancelledError()
        if failure == "cancelled"
        else BrowserWorkflowExecutionError("ego_connection_timeout", "Not ready")
    )
    start = AsyncMock(side_effect=error)
    monkeypatch.setattr(executor, "_open_session_once", start)
    with pytest.raises(type(error)):
        await executor.open_session(plan, session_id="bounded-session")
    assert start.await_count == (1 if failure == "cancelled" else 2)


async def test_python_command_timeout_is_a_native_protocol_timeout(tmp_path, monkeypatch):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://forum.example/",
            "allowed_origins": ["https://forum.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    session = EgoLiteWorkflowSession(
        EgoLiteWorkflowExecutor(tmp_path), plan, interactive=False, lock=asyncio.Lock()
    )
    session._writer = SimpleNamespace(write=lambda data: None, drain=AsyncMock())

    class Expired:
        async def __aenter__(self):
            raise TimeoutError()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(asyncio, "timeout", lambda seconds: Expired())
    with pytest.raises(BrowserWorkflowExecutionError) as caught:
        await session.command("navigate", url=plan.entry_url)
    assert caught.value.code == "ego_protocol_timeout"
    assert not session._pending


async def test_collection_protocol_timeout_does_not_replay_started_steps(tmp_path, monkeypatch):
    plan = compile_browser_workflow(
        {
            "version": 1,
            "entry_url": "https://forum.example/",
            "allowed_origins": ["https://forum.example"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        }
    )
    executor = EgoLiteWorkflowExecutor(tmp_path)
    executor.managed = True
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=BrowserWorkflowExecutionError(
                "ego_protocol_timeout", "Collection did not answer"
            )
        ),
        close=AsyncMock(),
    )
    open_once = AsyncMock(return_value=session)
    monkeypatch.setattr(executor, "_open_session_once", open_once)
    result = await executor.run(plan, session_id="bounded-collection")
    assert result.status == "failed" and result.error_code == "ego_protocol_timeout"
    open_once.assert_awaited_once()
    session.execute.assert_awaited_once()
    session.close.assert_awaited_once()
