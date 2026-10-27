"""Tests for the M7 L2 approval gate: store, loop interception, API, SSE."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.approvals import (
    ApprovalConflictError,
    ApprovalStore,
)
from openbiliclaw.agent.loop import AgentEvent, AgentLoop
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.api.app import create_app
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


# ── ApprovalStore ─────────────────────────────────────────────────────


def _submit(store: ApprovalStore, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "tool_name": "toggle_source",
        "arguments": {"id": "src-1", "enabled": False},
        "summary": "toggle_source(id='src-1', enabled=False)",
        "reason": "用户不想再看这个源",
        "impact": "改变该订阅的启用状态",
        "session": "desktop",
        "session_id": "sess-1",
        "turn_id": "turn-1",
    }
    kwargs.update(overrides)
    return store.submit(**kwargs)


class TestApprovalStore:
    def test_submit_and_get_roundtrip(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        assert record.status == "pending"
        assert record.approval_id.startswith("ap_")
        loaded = store.get(record.approval_id)
        assert loaded is not None
        assert loaded.tool_name == "toggle_source"
        assert loaded.arguments == {"id": "src-1", "enabled": False}
        assert loaded.turn_id == "turn-1"
        assert store.get("ap_missing") is None

    def test_list_filters_status_newest_first(self) -> None:
        store = ApprovalStore()
        first = _submit(store, summary="a")
        second = _submit(store, summary="b")
        store.reject(second.approval_id)
        pending = store.list(status="pending")
        assert [record.approval_id for record in pending] == [first.approval_id]
        rejected = store.list(status="rejected")
        assert [record.approval_id for record in rejected] == [second.approval_id]
        assert len(store.list()) == 2

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "chat_approvals.json"
        store = ApprovalStore(path)
        record = _submit(store)
        store.approve(record.approval_id)

        reloaded = ApprovalStore(path)
        loaded = reloaded.get(record.approval_id)
        assert loaded is not None
        assert loaded.status == "approved"
        assert loaded.arguments == {"id": "src-1", "enabled": False}

    def test_corrupt_file_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "chat_approvals.json"
        path.write_text("{not json", encoding="utf-8")
        store = ApprovalStore(path)
        assert store.list() == []

    def test_approve_pending_then_idempotent(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        approved = store.approve(record.approval_id)
        assert approved.status == "approved"
        assert approved.decided_at
        # Re-approve is a no-op at every post-pending state.
        assert store.approve(record.approval_id).status == "approved"
        executing = store.mark_executing(record.approval_id)
        assert executing.status == "executing"
        assert store.approve(record.approval_id).status == "executing"
        executed = store.mark_executed(record.approval_id, ok=True, result="done")
        assert executed.status == "executed"
        assert store.approve(record.approval_id).status == "executed"

    def test_mark_executing_requires_approved_and_is_idempotent(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        with pytest.raises(ApprovalConflictError):
            store.mark_executing(record.approval_id)
        store.approve(record.approval_id)
        executing = store.mark_executing(record.approval_id)
        assert executing.status == "executing"
        # Already executing returns the record unchanged (no double queue).
        assert store.mark_executing(record.approval_id).status == "executing"
        store.mark_executed(record.approval_id, ok=True)
        with pytest.raises(ApprovalConflictError):
            store.mark_executing(record.approval_id)

    def test_mark_executed_requires_executing(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        with pytest.raises(ApprovalConflictError):
            store.mark_executed(record.approval_id, ok=True)
        store.approve(record.approval_id)
        # approved alone is not enough: execution must have been queued.
        with pytest.raises(ApprovalConflictError):
            store.mark_executed(record.approval_id, ok=True)
        store.mark_executing(record.approval_id)
        failed = store.mark_executed(record.approval_id, ok=False, error="boom")
        assert failed.status == "failed"
        assert failed.error == "boom"
        assert failed.executed_at
        with pytest.raises(ApprovalConflictError):
            store.mark_executed(record.approval_id, ok=True)

    def test_failed_is_terminal_and_approve_idempotent(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        store.approve(record.approval_id)
        store.mark_executing(record.approval_id)
        store.mark_executed(record.approval_id, ok=False, error="dispatch down")
        assert store.approve(record.approval_id).status == "failed"
        with pytest.raises(ApprovalConflictError):
            store.reject(record.approval_id)

    def test_interrupted_executing_recovers_to_approved_on_load(self, tmp_path: Path) -> None:
        path = tmp_path / "chat_approvals.json"
        store = ApprovalStore(path)
        record = _submit(store)
        store.approve(record.approval_id)
        store.mark_executing(record.approval_id)
        # Simulate a crash mid-execution: the file persists "executing".

        reloaded = ApprovalStore(path)
        loaded = reloaded.get(record.approval_id)
        assert loaded is not None
        assert loaded.status == "approved"
        # The demotion is persisted, and the user can retry the execution.
        assert ApprovalStore(path).get(record.approval_id).status == "approved"  # type: ignore[union-attr]
        reloaded.mark_executing(record.approval_id)
        settled = reloaded.mark_executed(record.approval_id, ok=True, result="done")
        assert settled.status == "executed"

    def test_stale_instance_reads_do_not_clobber_newer_state(self, tmp_path: Path) -> None:
        path = tmp_path / "chat_approvals.json"
        store = ApprovalStore(path)
        record = _submit(store)
        store.approve(record.approval_id)
        # A second instance (e.g. mid-hot-reload) loads the "approved" state...
        stale = ApprovalStore(path)
        assert stale.get(record.approval_id).status == "approved"  # type: ignore[union-attr]
        # ...while the live instance drives the record to a terminal state.
        store.mark_executing(record.approval_id)
        store.mark_executed(record.approval_id, ok=True, result="done")
        # Reads on the stale instance must not rewrite the file.
        stale.get(record.approval_id)
        stale.list()
        reloaded = ApprovalStore(path)
        assert reloaded.get(record.approval_id).status == "executed"  # type: ignore[union-attr]

    def test_reject_then_approve_conflicts(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        rejected = store.reject(record.approval_id, reason="不需要")
        assert rejected.status == "rejected"
        assert "不需要" in rejected.reason
        # Idempotent re-reject; approve after reject conflicts.
        assert store.reject(record.approval_id).status == "rejected"
        with pytest.raises(ApprovalConflictError):
            store.approve(record.approval_id)

    def test_approve_after_executed_is_idempotent_but_reject_conflicts(self) -> None:
        store = ApprovalStore()
        record = _submit(store)
        store.approve(record.approval_id)
        store.mark_executing(record.approval_id)
        store.mark_executed(record.approval_id, ok=True, result="ok")
        with pytest.raises(ApprovalConflictError):
            store.reject(record.approval_id)

    def test_expiry_marks_pending_records(self) -> None:
        clock = [datetime(2026, 9, 23, 12, 0, tzinfo=UTC)]
        store = ApprovalStore(ttl_hours=1, now=lambda: clock[0])
        record = _submit(store)

        clock[0] += timedelta(hours=2)
        expired = store.get(record.approval_id)
        assert expired is not None
        assert expired.status == "expired"
        with pytest.raises(ApprovalConflictError):
            store.approve(record.approval_id)

    def test_submit_requires_tool_name(self) -> None:
        store = ApprovalStore()
        with pytest.raises(ValueError, match="tool name"):
            store.submit(tool_name="  ", arguments={}, summary="x")


# ── AgentLoop interception ────────────────────────────────────────────


class FakeAgentLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
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
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self._responses.popleft()


def _hard_write_tool(executed: list[dict[str, Any]]) -> Tool:
    return Tool(
        name="toggle_source",
        description="启用或禁用订阅",
        permission_level="hard_write",
        impact_hint="改变订阅启用状态",
        parameters={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        handler=lambda args: executed.append(args) or "已禁用订阅",
    )


def _hard_write_call(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "call-toggle",
        "name": "toggle_source",
        "arguments": arguments if arguments is not None else {"id": "src-1"},
    }


async def _collect(loop: AgentLoop, **kwargs: Any) -> list[AgentEvent]:
    return [event async for event in loop.run(**kwargs)]


class TestLoopApprovalInterception:
    async def test_invalid_hard_write_arguments_do_not_create_approval(self) -> None:
        executed: list[dict[str, Any]] = []
        store = ApprovalStore()
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_hard_write_call({"id": 123})]),
                LLMResponse(content="请提供正确的订阅 ID"),
            ]
        )
        loop = AgentLoop(llm, ToolRegistry([_hard_write_tool(executed)]), approval_gate=store)
        events = await _collect(loop, system_instruction="s", user_message="禁用订阅")
        assert [event.type for event in events] == ["tool_call", "tool_result", "final"]
        assert not events[1].ok
        assert "参数校验失败" in events[1].text
        assert executed == []
        assert store.list() == []

    async def test_hard_write_is_parked_not_executed(self) -> None:
        executed: list[dict[str, Any]] = []
        store = ApprovalStore()
        registry = ToolRegistry([_hard_write_tool(executed)])
        llm = FakeAgentLLM(
            [
                LLMResponse(content="我来帮你禁用", tool_calls=[_hard_write_call()]),
                LLMResponse(content="已提交审批，等你确认"),
            ]
        )
        loop = AgentLoop(llm, registry, approval_gate=store)

        events = await _collect(
            loop,
            system_instruction="s",
            user_message="禁用这个订阅",
            approval_context={"session": "desktop", "session_id": "sess-1", "turn_id": "t-1"},
        )

        assert [event.type for event in events] == [
            "thinking",
            "tool_call",
            "approval_request",
            "tool_result",
            "final",
        ]
        assert executed == [], "hard_write handler must not run inside the loop"
        approval_event = events[2]
        assert approval_event.tool_name == "toggle_source"
        assert approval_event.approval_id
        assert approval_event.impact == "改变订阅启用状态"
        assert approval_event.arguments == {"id": "src-1"}
        # The model is told the action awaits approval; the turn ends normally.
        feedback = events[3]
        assert feedback.ok
        assert approval_event.approval_id in feedback.text
        assert "审批" in feedback.text
        assert events[4].text == "已提交审批，等你确认"
        # The feedback, not an execution result, goes back to the model.
        assert llm.calls[1]["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call-toggle",
            "content": feedback.text,
        }
        # The approval record carries the conversation context.
        record = store.get(approval_event.approval_id)
        assert record is not None
        assert record.status == "pending"
        assert record.session_id == "sess-1"
        assert record.turn_id == "t-1"
        assert record.tool_name == "toggle_source"
        # Every event serializes for SSE.
        payload = approval_event.to_dict()
        assert payload["type"] == "approval_request"
        assert payload["approval_id"] == approval_event.approval_id

    async def test_without_gate_hard_write_executes_directly(self) -> None:
        executed: list[dict[str, Any]] = []
        registry = ToolRegistry([_hard_write_tool(executed)])
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_hard_write_call()]),
                LLMResponse(content="已禁用"),
            ]
        )
        loop = AgentLoop(llm, registry)  # legacy: no gate wired
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert [event.type for event in events] == ["tool_call", "tool_result", "final"]
        assert executed == [{"id": "src-1"}]

    async def test_read_and_soft_write_not_intercepted(self) -> None:
        store = ApprovalStore()
        ran: list[str] = []
        registry = ToolRegistry(
            [
                Tool(
                    name="get_profile",
                    description="读画像",
                    handler=lambda _a: ran.append("get_profile") or "画像",
                ),
                Tool(
                    name="save_note",
                    description="记笔记",
                    permission_level="soft_write",
                    handler=lambda _a: ran.append("save_note") or "已记",
                ),
            ]
        )
        llm = FakeAgentLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[
                        {"id": "c1", "name": "get_profile", "arguments": {}},
                        {"id": "c2", "name": "save_note", "arguments": {}},
                    ],
                ),
                LLMResponse(content="完成"),
            ]
        )
        loop = AgentLoop(llm, registry, approval_gate=store)
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert ran == ["get_profile", "save_note"]
        assert [event.type for event in events] == [
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "final",
        ]
        assert store.list() == []

    async def test_gate_failure_is_fed_back_as_error(self) -> None:
        class _BrokenGate:
            def submit(self, **kwargs: Any) -> Any:
                raise RuntimeError("store offline")

        executed: list[dict[str, Any]] = []
        registry = ToolRegistry([_hard_write_tool(executed)])
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_hard_write_call()]),
                LLMResponse(content="审批系统挂了，稍后重试"),
            ]
        )
        loop = AgentLoop(llm, registry, approval_gate=_BrokenGate())
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert [event.type for event in events] == ["tool_call", "tool_result", "final"]
        assert not events[1].ok
        assert "审批登记失败" in events[1].text
        assert executed == []


# ── API endpoints + SSE integration ──────────────────────────────────


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


class _RunningDialogue:
    """Dialogue double that actually runs the injected loop."""

    async def stream_agent_reply(
        self,
        agent_loop: Any,
        message: str,
        *,
        session: str = "",
        scope: str = "chat",
        turn_id: str = "",
        session_id: str = "",
        skill: Any = None,
        tools: Any = None,
        skill_switch_guide: str = "",
    ) -> Any:
        async for event in agent_loop.run(
            system_instruction="s",
            user_message=message,
            tools=tools,
            approval_context={
                "session": session,
                "session_id": session_id,
                "turn_id": turn_id,
            },
        ):
            yield event

    async def respond(self, message: str, **kwargs: Any) -> str:
        del kwargs
        return "legacy"


def _approval_app(
    tmp_path: Path,
    *,
    loop: AgentLoop | None = None,
    registry: ToolRegistry | None = None,
    store: ApprovalStore | None = None,
) -> Any:
    database = _database(tmp_path)
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=_RunningDialogue(),
    )
    ctx = app.state.runtime_context
    ctx.chat_approval_store = store or ApprovalStore()
    ctx.agent_tool_registry = registry
    ctx.agent_loop = loop if loop is not None else object()
    return app


def _wait_for_approval_status(
    client: Any,
    approval_id: str,
    statuses: set[str],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll the list endpoint until the record reaches one of ``statuses``."""
    deadline = time.monotonic() + timeout
    item: dict[str, Any] | None = None
    while True:
        payload = client.get("/api/chat/approvals", params={"limit": 100}).json()
        item = next(
            (entry for entry in payload["items"] if entry["approval_id"] == approval_id),
            None,
        )
        if item is not None and item["status"] in statuses:
            return item
        if time.monotonic() > deadline:
            raise AssertionError(f"approval {approval_id} did not reach {statuses}: {item}")
        time.sleep(0.01)


class TestApprovalApi:
    @pytest.mark.parametrize("changed_before_approval", [False, True])
    def test_delete_memory_rechecks_saved_value_when_approval_executes(
        self, tmp_path: Path, changed_before_approval: bool
    ) -> None:
        from openbiliclaw.agent.tools import AgentToolContext, build_agent_tool_registry
        from openbiliclaw.memory.manager import MemoryManager

        memory = MemoryManager(tmp_path)
        memory.initialize()
        registry = build_agent_tool_registry(AgentToolContext(memory_manager=memory))
        saved = asyncio.run(
            registry.dispatch(
                "write_memory", {"layer": "preference", "key": "snack", "value": "清淡粥"}
            )
        )
        assert saved.ok, saved.content
        app = _approval_app(tmp_path, registry=registry)
        store = app.state.runtime_context.chat_approval_store
        approval = store.submit(
            tool_name="delete_memory",
            arguments={"layer": "preference", "key": "snack", "expected_value": "清淡粥"},
            summary="删除夜宵聊天笔记",
            impact="只删除一条明确保存的笔记",
        )
        assert "snack" in memory.get_layer("preference").data["agent_notes"]
        if changed_before_approval:
            changed = asyncio.run(
                registry.dispatch(
                    "write_memory",
                    {
                        "layer": "preference",
                        "key": "snack",
                        "value": "番茄面",
                        "expected_value": "清淡粥",
                    },
                )
            )
            assert changed.ok, changed.content
        with TestClient(app) as client:
            response = client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
            assert response.status_code == 200
            settled = _wait_for_approval_status(
                client, approval.approval_id, {"executed", "failed"}
            )
            assert settled["status"] == ("failed" if changed_before_approval else "executed")
            # Duplicate approval never re-dispatches or erases a changed note.
            repeated = client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
            assert repeated.status_code == 200
        restored = MemoryManager(tmp_path)
        restored.initialize()
        notes = restored.get_layer("preference").data["agent_notes"]
        if changed_before_approval:
            assert notes["snack"]["value"] == "番茄面"
        else:
            assert "snack" not in notes

    def _seed_recipe(self, app: Any) -> str:
        database = app.state.runtime_context.database
        recipe = {
            "id": "src-9",
            "source_type": "web",
            "name": "测试源",
            "strategy": "feed",
            "config": {"url": "https://example.com/feed"},
            "enabled": True,
            "created_by": "test",
        }
        database.save_source_recipe(recipe)
        return "src-9"

    def test_list_empty_and_status_validation(self, tmp_path: Path) -> None:
        app = _approval_app(tmp_path)
        with TestClient(app) as client:
            response = client.get("/api/chat/approvals")
            assert response.status_code == 200
            assert response.json() == {"count": 0, "items": []}
            bad = client.get("/api/chat/approvals", params={"status": "bogus"})
            assert bad.status_code == 422

    def test_approve_executes_once_and_audits(self, tmp_path: Path) -> None:
        from openbiliclaw.agent.tools.source_tools import build_source_tool_registry

        app = _approval_app(tmp_path)
        ctx = app.state.runtime_context
        database = ctx.database
        # Use the production tool so approval executes a real write.
        ctx.agent_tool_registry = build_source_tool_registry(database)
        recipe_id = self._seed_recipe(app)
        store: ApprovalStore = ctx.chat_approval_store
        record = store.submit(
            tool_name="toggle_source",
            arguments={"id": recipe_id, "enabled": False},
            summary=f"toggle_source(id={recipe_id!r}, enabled=False)",
            impact="改变该订阅的启用状态",
            turn_id="turn-9",
        )

        with TestClient(app) as client:
            listing = client.get("/api/chat/approvals", params={"status": "pending"})
            assert listing.json()["count"] == 1
            item = listing.json()["items"][0]
            assert item["approval_id"] == record.approval_id
            assert item["tool_name"] == "toggle_source"
            assert item["arguments"]["id"] == recipe_id

            # Approve returns immediately with the execution queued, not the
            # outcome — the dispatch runs as a background task.
            approved = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
            assert approved.status_code == 200
            body = approved.json()
            assert body["executed"] is False
            assert body["queued"] is True
            assert body["ok"] is None
            assert body["approval"]["status"] == "executing"

            settled = _wait_for_approval_status(client, record.approval_id, {"executed", "failed"})
            assert settled["status"] == "executed"
            assert "已禁用" in settled["result"]
            assert settled["error"] == ""

            recipe = database.get_all_recipes()[0]
            assert recipe["enabled"] is False or recipe["enabled"] == 0

            # Idempotent: a second approve returns the stored result and
            # does NOT re-run the handler (recipe stays disabled, no error).
            again = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
            assert again.status_code == 200
            assert again.json()["already_executed"] is True
            assert again.json()["executed"] is False
            assert again.json()["queued"] is False

        ledger_rows = database.query_profile_ledger(write_point="agent.approval.toggle_source")
        assert len(ledger_rows) == 1
        row = ledger_rows[0]
        assert row["gate_verdict"] == "approved"
        assert row["held_id"] == record.approval_id
        assert row["outcome"] == "success"
        assert recipe_id in row["before_summary"]

    def test_approve_during_executing_does_not_double_execute(self, tmp_path: Path) -> None:
        release = threading.Event()
        started = threading.Event()
        calls: list[dict[str, Any]] = []

        async def _blocking_handler(args: dict[str, Any]) -> str:
            calls.append(args)
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return "已禁用订阅"

        registry = ToolRegistry(
            [
                Tool(
                    name="toggle_source",
                    description="启用或禁用订阅",
                    permission_level="hard_write",
                    parameters={"type": "object", "properties": {"id": {"type": "string"}}},
                    handler=_blocking_handler,
                )
            ]
        )
        app = _approval_app(tmp_path, registry=registry)
        store: ApprovalStore = app.state.runtime_context.chat_approval_store
        record = store.submit(
            tool_name="toggle_source",
            arguments={"id": "src-1"},
            summary="toggle_source(id='src-1')",
        )

        try:
            with TestClient(app) as client:
                first = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
                assert first.status_code == 200
                assert first.json()["queued"] is True
                assert started.wait(timeout=2), "background execution should start"

                # The HTTP request returned while the tool is still running.
                in_flight = client.get("/api/chat/approvals").json()["items"][0]
                assert in_flight["status"] == "executing"

                # A duplicate approve during execution queues nothing.
                duplicate = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
                assert duplicate.status_code == 200
                assert duplicate.json()["queued"] is True
                assert duplicate.json()["already_queued"] is True

                release.set()
                settled = _wait_for_approval_status(client, record.approval_id, {"executed"})
                assert settled["result"] == "已禁用订阅"
        finally:
            release.set()
        assert calls == [{"id": "src-1"}]

    def test_approve_execution_failure_settles_failed(self, tmp_path: Path) -> None:
        def _failing_handler(args: dict[str, Any]) -> str:
            raise RuntimeError("dispatch exploded")

        registry = ToolRegistry(
            [
                Tool(
                    name="toggle_source",
                    description="启用或禁用订阅",
                    permission_level="hard_write",
                    parameters={"type": "object", "properties": {"id": {"type": "string"}}},
                    handler=_failing_handler,
                )
            ]
        )
        app = _approval_app(tmp_path, registry=registry)
        ctx = app.state.runtime_context
        database = ctx.database
        store: ApprovalStore = ctx.chat_approval_store
        record = store.submit(
            tool_name="toggle_source",
            arguments={"id": "src-1"},
            summary="toggle_source(id='src-1')",
            turn_id="turn-fail",
        )

        with TestClient(app) as client:
            approved = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
            assert approved.status_code == 200
            settled = _wait_for_approval_status(client, record.approval_id, {"failed"})
            assert settled["error"]
            assert settled["executed_at"]
            # failed is terminal: re-approve is idempotent, never re-runs.
            again = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
            assert again.json()["already_executed"] is True
            assert again.json()["ok"] is False

        ledger_rows = database.query_profile_ledger(write_point="agent.approval.toggle_source")
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["outcome"] == "failed"

    def test_approve_unknown_and_conflict_paths(self, tmp_path: Path) -> None:
        app = _approval_app(tmp_path)
        store: ApprovalStore = app.state.runtime_context.chat_approval_store
        record = store.submit(tool_name="toggle_source", arguments={"id": "x"}, summary="s")
        store.reject(record.approval_id)
        with TestClient(app) as client:
            assert client.post("/api/chat/approvals/ap_missing/approve").status_code == 404
            conflict = client.post(f"/api/chat/approvals/{record.approval_id}/approve")
            assert conflict.status_code == 409

    def test_reject_marks_record_and_audits(self, tmp_path: Path) -> None:
        app = _approval_app(tmp_path)
        database = app.state.runtime_context.database
        store: ApprovalStore = app.state.runtime_context.chat_approval_store
        record = store.submit(
            tool_name="update_config",
            arguments={"key": "language", "value": "en-US"},
            summary="update_config(key='language')",
            turn_id="turn-10",
        )
        with TestClient(app) as client:
            response = client.post(
                f"/api/chat/approvals/{record.approval_id}/reject",
                json={"reason": "先不改"},
            )
            assert response.status_code == 200
            approval = response.json()["approval"]
            assert approval["status"] == "rejected"
            assert "先不改" in approval["reason"]
            # Idempotent re-reject stays 200.
            again = client.post(f"/api/chat/approvals/{record.approval_id}/reject")
            assert again.status_code == 200
            # Rejected records can no longer be approved.
            assert (
                client.post(f"/api/chat/approvals/{record.approval_id}/approve").status_code == 409
            )

        ledger_rows = database.query_profile_ledger(write_point="agent.approval.update_config")
        assert len(ledger_rows) == 1
        assert ledger_rows[0]["gate_verdict"] == "rejected"

    def test_sse_approval_request_then_approve_roundtrip(self, tmp_path: Path) -> None:
        """Loop parks the call during the stream; approve later executes it."""
        from openbiliclaw.agent.tools.source_tools import build_source_tool_registry

        store = ApprovalStore()
        app = _approval_app(tmp_path, store=store)
        ctx = app.state.runtime_context
        database = ctx.database
        registry = build_source_tool_registry(database)
        ctx.agent_tool_registry = registry
        llm = FakeAgentLLM(
            [
                LLMResponse(
                    content="好的",
                    tool_calls=[_hard_write_call({"id": "src-9", "enabled": False})],
                ),
                LLMResponse(content="已提交审批，请在卡片中确认"),
            ]
        )
        ctx.agent_loop = AgentLoop(llm, registry, approval_gate=store)
        self._seed_recipe(app)

        with TestClient(app) as client:
            created = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "approval-turn-1",
                    "session": "desktop",
                    "scope": "chat",
                    "message": "禁用这个源",
                    "streaming": True,
                    "skill": "system-steward",
                },
            )
            assert created.status_code == 200

            response = client.post(
                "/api/chat/agent/stream",
                json={
                    "turn_id": "approval-turn-1",
                    "session": "desktop",
                    "message": "禁用这个源",
                    "skill": "system-steward",
                },
            )
            assert response.status_code == 200
            events = _parse_sse(response.text)
            event_names = [name for name, _data in events]
            assert "approval_request" in event_names
            approval_data = next(data for name, data in events if name == "approval_request")
            approval_id = approval_data["approval_id"]
            assert approval_data["tool_name"] == "toggle_source"
            assert approval_data["impact"]
            assert event_names[-1] == "done"

            # Nothing executed yet.
            recipe = database.get_all_recipes()[0]
            assert recipe["enabled"] in (True, 1)

            approved = client.post(f"/api/chat/approvals/{approval_id}/approve")
            assert approved.status_code == 200
            assert approved.json()["queued"] is True
            settled = _wait_for_approval_status(client, approval_id, {"executed", "failed"})
            assert settled["status"] == "executed"
            recipe = database.get_all_recipes()[0]
            assert recipe["enabled"] in (False, 0)

        # The originating turn's replay stream ends with the outcome.
        row = database.get_chat_turn("approval-turn-1")
        assert row is not None
        agent_events = row["payload"].get("agent_events", [])
        event_types = [event["type"] for event in agent_events]
        assert "approval_request" in event_types
        result_events = [e for e in agent_events if e["type"] == "approval_result"]
        assert len(result_events) == 1
        assert result_events[0]["decision"] == "approved"
        assert result_events[0]["ok"] is True
        assert result_events[0]["approval_id"] == approval_id
