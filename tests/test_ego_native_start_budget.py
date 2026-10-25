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


@pytest.mark.parametrize("ready_after", [20.0, None])
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
    monkeypatch.setattr(executor, "_schedule_idle_stop", lambda **kwargs: None)
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
            assert clock["now"] <= 30.1
            session._open_entry.assert_not_awaited()
        else:
            await session.start(tmp_path / "state.json", state)
            assert state["task_id"] == 42
            session._open_entry.assert_awaited_once()
    finally:
        await session.close()
    assert process.returncode == 0
    assert not lock.locked()
    assert session._temporary is None or not Path(session._temporary.name).exists()
