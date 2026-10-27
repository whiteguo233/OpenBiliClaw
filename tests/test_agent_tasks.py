"""Durable background task center tests (「聊一聊」 M6).

Covers the ``agent_tasks`` table migration and CRUD (status CAS, step-log
caps, report/suggestions), the ``AgentTaskRunner`` full lifecycle against a
scripted fake LLM (multi-hop run → step persistence → completion → summary
message written back to the source session), cancellation, restart recovery,
suggestion/meta-tool validation, and the task-center API endpoints.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tasks import (
    PROPOSE_SUGGESTION_TOOL_NAME,
    START_BACKGROUND_TASK_TOOL_NAME,
    AgentTaskRunner,
    build_propose_suggestion_tool,
    build_start_background_task_tool,
)
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.api.app import create_app
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.storage.database import (
    AGENT_TASK_TERMINAL_STATUSES,
    DEFAULT_CHAT_SESSION_ID,
    MAX_AGENT_TASK_STEPS,
    MAX_AGENT_TASK_SUGGESTIONS,
    Database,
)

if TYPE_CHECKING:
    from pathlib import Path


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "") -> dict[str, Any]:
    return {"id": call_id or f"call-{name}", "name": name, "arguments": arguments}


class FakeTaskLLM:
    """Service-shaped double returning queued LLMResponses."""

    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        caller: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        bypass_semaphore: bool = False,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "caller": caller})
        item = self._responses.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def _read_registry() -> ToolRegistry:
    """One read tool plus one soft_write tool that must be filtered out."""
    return ToolRegistry(
        [
            Tool(
                name="get_profile",
                description="读取画像",
                handler=lambda _args: "画像：喜欢机械键盘",
            ),
            Tool(
                name="write_memory",
                description="写记忆",
                permission_level="soft_write",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
                handler=lambda _args: "已写入",
            ),
        ]
    )


def _runtime(llm: FakeTaskLLM, registry: ToolRegistry | None = None, **extra: Any) -> Any:
    return SimpleNamespace(
        llm_service=llm,
        agent_tool_registry=registry if registry is not None else _read_registry(),
        skill_catalog=extra.get("skill_catalog"),
        config=extra.get("config"),
    )


def _runner(database: Database, runtime: Any) -> AgentTaskRunner:
    return AgentTaskRunner(database, runtime=runtime)


async def _wait_for_task(runner: AgentTaskRunner, task_id: str) -> None:
    task = runner._local_tasks.get(task_id)
    assert task is not None
    await asyncio.wait_for(task, timeout=5)


# --- Storage: migration ---


def test_initialize_creates_agent_tasks_table(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tables = {
        str(row["name"])
        for row in database.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "agent_tasks" in tables
    columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(agent_tasks)").fetchall()
    }
    assert {
        "task_id",
        "session_id",
        "title",
        "prompt",
        "status",
        "skill",
        "progress",
        "report",
        "suggestions",
        "steps",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
    } <= columns


def test_migration_adds_agent_tasks_to_legacy_database(tmp_path: Path) -> None:
    """A pre-M6 database gains the agent_tasks table via the ensure path."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE chat_sessions (
            session_id      TEXT PRIMARY KEY,
            title           TEXT NOT NULL DEFAULT '',
            archived        INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    database = Database(db_path)
    database.initialize()

    row = database.create_agent_task(task_id="t-legacy", prompt="盘点我的订阅")
    assert row["status"] == "pending"


# --- Storage: CRUD ---


def test_agent_task_create_get_round_trip(tmp_path: Path) -> None:
    database = _database(tmp_path)
    row = database.create_agent_task(
        task_id="t1",
        session_id="s1",
        title="订阅盘点",
        prompt="帮我盘点订阅源",
        skill="taste-companion",
    )
    assert row["status"] == "pending"
    assert row["steps"] == []
    assert row["suggestions"] == []
    assert row["report"] == ""
    # Idempotent on task_id: a second create does not overwrite.
    again = database.create_agent_task(task_id="t1", prompt="别的指令")
    assert again["prompt"] == "帮我盘点订阅源"
    with pytest.raises(ValueError, match="prompt"):
        database.create_agent_task(task_id="t2", prompt="  ")


def test_agent_task_status_cas_and_timestamps(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t1", prompt="p")
    assert database.update_agent_task_status("t1", "running", progress="进行中")
    row = database.get_agent_task("t1")
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"]
    assert not row["finished_at"]
    assert database.set_agent_task_report("t1", report="报告正文")
    row = database.get_agent_task("t1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["finished_at"]
    # Terminal rows reject further transitions and late reports.
    assert not database.update_agent_task_status("t1", "running")
    assert not database.set_agent_task_report("t1", report="迟到报告")
    assert database.get_agent_task("t1")["report"] == "报告正文"


def test_agent_task_list_filters_and_pagination(tmp_path: Path) -> None:
    database = _database(tmp_path)
    for index in range(5):
        database.create_agent_task(
            task_id=f"t{index}",
            session_id="s1" if index % 2 == 0 else "s2",
            prompt=f"p{index}",
        )
    database.update_agent_task_status("t0", "running")
    database.update_agent_task_status("t1", "cancelled")

    rows, total = database.list_agent_tasks()
    assert total == 5
    rows, total = database.list_agent_tasks(status="pending")
    assert total == 3
    rows, total = database.list_agent_tasks(session_id="s2")
    assert total == 2
    rows, total = database.list_agent_tasks(limit=2, offset=4)
    assert total == 5
    assert len(rows) == 1
    with pytest.raises(ValueError, match="status"):
        database.list_agent_tasks(status="bogus")


def test_append_agent_task_step_caps_and_terminal_guard(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t1", prompt="p")
    assert database.append_agent_task_step(
        "t1", step={"type": "thinking", "step": 1, "text": "长" * 5000}
    )
    row = database.get_agent_task("t1")
    assert row is not None
    entry = row["steps"][0]
    assert len(entry["text"]) < 5000
    assert entry["truncated"] is True
    # Terminal tasks stop accepting steps.
    database.update_agent_task_status("t1", "cancelled")
    assert not database.append_agent_task_step("t1", step={"type": "thinking"})


def test_append_agent_task_step_count_cap_marks_truncation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t1", prompt="p")
    for index in range(MAX_AGENT_TASK_STEPS + 10):
        database.append_agent_task_step("t1", step={"type": "thinking", "step": index})
    row = database.get_agent_task("t1")
    assert row is not None
    assert len(row["steps"]) == MAX_AGENT_TASK_STEPS
    assert row["steps"][-1]["type"] == "steps_truncated"


def test_set_agent_task_report_caps(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t1", prompt="p")
    suggestions = [
        {"action": "write_memory", "summary": f"s{index}", "payload": {}}
        for index in range(MAX_AGENT_TASK_SUGGESTIONS + 5)
    ]
    assert database.set_agent_task_report("t1", report="长" * 9000, suggestions=suggestions)
    row = database.get_agent_task("t1")
    assert row is not None
    assert len(row["report"]) < 9000
    assert len(row["suggestions"]) == MAX_AGENT_TASK_SUGGESTIONS


def test_interrupt_stale_agent_tasks(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t-pending", prompt="p")
    database.create_agent_task(task_id="t-running", prompt="p")
    database.create_agent_task(task_id="t-done", prompt="p")
    database.update_agent_task_status("t-running", "running")
    database.set_agent_task_report("t-done", report="ok")

    marked = database.interrupt_stale_agent_tasks()
    assert marked == 2
    assert database.get_agent_task("t-pending")["status"] == "interrupted"
    assert database.get_agent_task("t-running")["status"] == "interrupted"
    assert database.get_agent_task("t-done")["status"] == "completed"
    # Terminal states are stable: a second pass marks nothing.
    assert database.interrupt_stale_agent_tasks() == 0


# --- Runner lifecycle ---


async def test_runner_full_flow_persists_steps_and_writes_back(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_chat_session(session_id="s-chat", title="主会话")
    llm = FakeTaskLLM(
        [
            LLMResponse(content="先看看画像", tool_calls=[_tool_call("get_profile", {})]),
            LLMResponse(
                content="",
                tool_calls=[
                    _tool_call(
                        PROPOSE_SUGGESTION_TOOL_NAME,
                        {
                            "action": "write_memory",
                            "summary": "记住用户喜欢机械键盘",
                            "payload": {"layer": "preference", "text": "喜欢机械键盘"},
                        },
                        "c2",
                    )
                ],
            ),
            LLMResponse(content="盘点完成：你有 0 个订阅。"),
        ]
    )
    runner = _runner(database, _runtime(llm))

    row = runner.start(session_id="s-chat", prompt="帮我盘点", title="订阅盘点")
    task_id = row["task_id"]
    await _wait_for_task(runner, task_id)

    stored = database.get_agent_task(task_id)
    assert stored is not None
    assert stored["status"] == "completed"
    assert stored["report"] == "盘点完成：你有 0 个订阅。"
    assert stored["started_at"] and stored["finished_at"]
    step_types = [step["type"] for step in stored["steps"]]
    assert step_types == [
        "thinking",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "final",
    ]
    assert stored["suggestions"] == [
        {
            "action": "write_memory",
            "summary": "记住用户喜欢机械键盘",
            "payload": {"layer": "preference", "text": "喜欢机械键盘"},
        }
    ]
    # The background run never sees soft_write tools, only read + meta tools.
    offered = {schema["function"]["name"] for schema in llm.calls[0]["tools"]}
    assert "get_profile" in offered
    assert PROPOSE_SUGGESTION_TOOL_NAME in offered
    assert "write_memory" not in offered

    # Completion wrote a summary turn back into the source session.
    turns, total = database.list_chat_turns_by_session(session_id="s-chat")
    assert total == 1
    turn = turns[0]
    assert turn["turn_id"] == f"agent-task-{task_id}"
    assert turn["status"] == "completed"
    assert "盘点完成" in turn["reply"]
    assert "建议清单（1 项" in turn["reply"]
    payload = turn["payload"]
    assert payload["type"] == "agent_task_summary"
    assert payload["task_id"] == task_id
    assert payload["task_status"] == "completed"
    assert len(payload["suggestions"]) == 1


async def test_runner_cancel_lands_cancelled_without_write_back(tmp_path: Path) -> None:
    database = _database(tmp_path)
    gate = asyncio.Event()

    async def _blocking_complete(**kwargs: Any) -> LLMResponse:
        del kwargs
        await gate.wait()
        return LLMResponse(content="unreachable")

    llm = SimpleNamespace(complete_with_native_tools=_blocking_complete)
    runner = _runner(database, _runtime(llm))

    row = runner.start(session_id="", prompt="跑一个停不下来的任务")
    task_id = row["task_id"]
    # Wait until the task is actually running before cancelling.
    for _ in range(100):
        if database.get_agent_task(task_id)["status"] == "running":
            break
        await asyncio.sleep(0.01)

    assert await runner.cancel(task_id)
    stored = database.get_agent_task(task_id)
    assert stored is not None
    assert stored["status"] == "cancelled"
    assert stored["finished_at"]
    # No summary message for a user-cancelled task.
    _turns, total = database.list_chat_turns_by_session(session_id=DEFAULT_CHAT_SESSION_ID)
    assert total == 0
    # Cancelling a terminal task is a no-op.
    assert not await runner.cancel(task_id)


async def test_runner_llm_failure_lands_failed_and_writes_back(tmp_path: Path) -> None:
    database = _database(tmp_path)
    llm = FakeTaskLLM([RuntimeError("provider exploded")])
    runner = _runner(database, _runtime(llm))

    row = runner.start(session_id="", prompt="会失败的任务", title="失败任务")
    task_id = row["task_id"]
    await _wait_for_task(runner, task_id)

    stored = database.get_agent_task(task_id)
    assert stored is not None
    assert stored["status"] == "failed"
    assert "provider exploded" in stored["error"]
    turns, total = database.list_chat_turns_by_session(session_id=DEFAULT_CHAT_SESSION_ID)
    assert total == 1
    assert turns[0]["payload"]["task_status"] == "failed"
    assert "失败" in turns[0]["reply"]


async def test_runner_unconfigured_runtime_fails_fast(tmp_path: Path) -> None:
    database = _database(tmp_path)
    runner = _runner(database, SimpleNamespace(llm_service=None, agent_tool_registry=None))
    row = runner.start(prompt="没有运行时的任务")
    await _wait_for_task(runner, row["task_id"])
    stored = database.get_agent_task(row["task_id"])
    assert stored is not None
    assert stored["status"] == "failed"


async def test_runner_skill_whitelist_intersects_read_only(tmp_path: Path) -> None:
    database = _database(tmp_path)
    registry = _read_registry()
    from openbiliclaw.agent.skill import SkillCatalog, SkillDefinition

    catalog = SkillCatalog(
        [
            SkillDefinition(
                name="mini",
                description="只允许 get_profile",
                system_prompt="你是受限角色",
                tools=("get_profile", "write_memory"),
            )
        ]
    )
    llm = FakeTaskLLM([LLMResponse(content="完成")])
    runtime = SimpleNamespace(
        llm_service=llm,
        agent_tool_registry=registry,
        skill_catalog=catalog,
        config=None,
    )
    runner = _runner(database, runtime)

    row = runner.start(prompt="p", skill="mini")
    await _wait_for_task(runner, row["task_id"])
    offered = {schema["function"]["name"] for schema in llm.calls[0]["tools"]}
    # The skill's soft_write whitelist entry is cut by the read-only ceiling.
    assert offered == {"get_profile", PROPOSE_SUGGESTION_TOOL_NAME}


def test_runner_recover_interrupted(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="t1", prompt="p")
    database.update_agent_task_status("t1", "running")
    runner = _runner(database, SimpleNamespace())
    assert runner.recover_interrupted() == 1
    assert database.get_agent_task("t1")["status"] == "interrupted"


# --- Meta tools ---


def test_propose_suggestion_tool_validation() -> None:
    collector: list[dict[str, Any]] = []
    tool = build_propose_suggestion_tool(collector)
    registry = ToolRegistry([tool])

    async def _dispatch(args: dict[str, Any]) -> Any:
        return await registry.dispatch(PROPOSE_SUGGESTION_TOOL_NAME, args)

    # Schema layer: unknown actions and missing summary never reach the handler.
    rejected = asyncio.run(_dispatch({"action": "delete_everything", "summary": "x"}))
    assert not rejected.ok
    assert rejected.error == "invalid_arguments"
    rejected = asyncio.run(_dispatch({"action": "write_memory"}))
    assert not rejected.ok
    # Handler layer: payload shape is validated past the generic object schema.
    assert "payload 必须是对象" in tool.handler(
        {"action": "write_memory", "summary": "s", "payload": [1]}
    )
    result = asyncio.run(
        _dispatch(
            {"action": "write_memory", "summary": "记住偏好", "payload": {"layer": "preference"}}
        )
    )
    assert result.ok
    assert collector == [
        {"action": "write_memory", "summary": "记住偏好", "payload": {"layer": "preference"}}
    ]


def test_start_background_task_tool_validation() -> None:
    tool = build_start_background_task_tool(["taste-companion"])
    registry = ToolRegistry([tool])

    async def _dispatch(args: dict[str, Any]) -> Any:
        return await registry.dispatch(START_BACKGROUND_TASK_TOOL_NAME, args)

    assert tool.permission_level == "read"
    # Empty prompt passes the schema (no minLength) and is rejected in-handler.
    result = asyncio.run(_dispatch({"prompt": ""}))
    assert "prompt" in result.content
    # Unknown skill names are cut by the schema enum.
    rejected = asyncio.run(_dispatch({"prompt": "盘点订阅", "skill": "nope"}))
    assert not rejected.ok
    assert rejected.error == "invalid_arguments"
    result = asyncio.run(_dispatch({"prompt": "盘点订阅", "title": "订阅盘点"}))
    assert result.ok
    assert "POST /api/chat/tasks" in result.content


# --- API integration ---


def _api_app(tmp_path: Path, llm: Any) -> Any:
    database = _database(tmp_path)
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=object(),
    )
    ctx = app.state.runtime_context
    ctx.llm_service = llm
    ctx.agent_tool_registry = _read_registry()
    return app


def _wait_terminal(client: TestClient, task_id: str, attempts: int = 200) -> dict[str, Any]:
    import time

    for _ in range(attempts):
        row = client.get(f"/api/chat/tasks/{task_id}").json()
        if row["status"] in AGENT_TASK_TERMINAL_STATUSES:
            return row
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


def test_task_api_full_flow(tmp_path: Path) -> None:
    llm = FakeTaskLLM(
        [
            LLMResponse(content="", tool_calls=[_tool_call("get_profile", {})]),
            LLMResponse(content="任务报告：一切正常。"),
        ]
    )
    app = _api_app(tmp_path, llm)
    with TestClient(app) as client:
        created = client.post("/api/chat/tasks", json={"prompt": "帮我看看画像", "title": "看画像"})
        assert created.status_code == 200
        task = created.json()
        assert task["status"] in {"pending", "running"}
        assert task["session_id"] == ""
        assert task["steps"] == []

        final = _wait_terminal(client, task["task_id"])
        assert final["status"] == "completed"
        assert final["report"] == "任务报告：一切正常。"
        assert [step["type"] for step in final["steps"]] == [
            "tool_call",
            "tool_result",
            "final",
        ]

        listing = client.get("/api/chat/tasks").json()
        assert listing["total"] == 1
        assert listing["items"][0]["task_id"] == task["task_id"]
        assert listing["items"][0]["steps"] == []
        completed = client.get("/api/chat/tasks", params={"status": "completed"}).json()
        assert completed["total"] == 1
        running = client.get("/api/chat/tasks", params={"status": "running"}).json()
        assert running["total"] == 0

        # The completion summary landed in the default session.
        session = client.get(f"/api/chat/sessions/{DEFAULT_CHAT_SESSION_ID}").json()
        assert session["total"] == 1
        assert session["items"][0]["payload"]["type"] == "agent_task_summary"


def test_task_api_cancel(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def _blocking_complete(**kwargs: Any) -> LLMResponse:
        del kwargs
        await gate.wait()
        return LLMResponse(content="unreachable")

    app = _api_app(tmp_path, SimpleNamespace(complete_with_native_tools=_blocking_complete))
    with TestClient(app) as client:
        created = client.post("/api/chat/tasks", json={"prompt": "长跑任务"})
        task_id = created.json()["task_id"]
        cancelled = client.post(f"/api/chat/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        again = client.post(f"/api/chat/tasks/{task_id}/cancel")
        assert again.status_code == 409


def test_task_api_validation(tmp_path: Path) -> None:
    app = _api_app(tmp_path, FakeTaskLLM([LLMResponse(content="ok")]))
    with TestClient(app) as client:
        assert client.post("/api/chat/tasks", json={"prompt": "  "}).status_code == 422
        assert (
            client.post(
                "/api/chat/tasks", json={"prompt": "p", "session_id": "missing"}
            ).status_code
            == 404
        )
        assert (
            client.post("/api/chat/tasks", json={"prompt": "p", "skill": "nope"}).status_code == 422
        )
        assert client.get("/api/chat/tasks/missing").status_code == 404
        assert client.post("/api/chat/tasks/missing/cancel").status_code == 404
        assert client.get("/api/chat/tasks", params={"status": "bogus"}).status_code == 422


def test_task_api_marks_stale_running_interrupted_on_boot(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_agent_task(task_id="stale", prompt="p")
    database.update_agent_task_status("stale", "running")
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=object(),
    )
    ctx = app.state.runtime_context
    ctx.llm_service = FakeTaskLLM([LLMResponse(content="ok")])
    ctx.agent_tool_registry = _read_registry()
    with TestClient(app):
        row = database.get_agent_task("stale")
        assert row is not None
        assert row["status"] == "interrupted"
        assert "中断" in row["error"]


def test_agent_loop_default_budget_for_background_tasks(tmp_path: Path) -> None:
    """The runner builds loops with the smaller unattended step budget."""
    database = _database(tmp_path)
    config = SimpleNamespace(agent=SimpleNamespace(task_max_steps=7, tool_result_max_chars=1000))
    llm = FakeTaskLLM([LLMResponse(content="ok")])
    runner = _runner(database, _runtime(llm, config=config))
    stored = database.create_agent_task(task_id="t-budget", prompt="p")
    loop, _registry = runner._resolve_components(stored)
    assert isinstance(loop, AgentLoop)
    assert loop.max_steps == 7
