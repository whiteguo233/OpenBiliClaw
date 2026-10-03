"""Trusted source summaries survive the real chat approval lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.approvals import ApprovalStore
from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.agent.tools.custom_source_tools import build_custom_source_tools
from openbiliclaw.config import Config
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.sources.workflow_service import WorkflowService
from tests.test_agent_approvals import (
    _approval_app,
    _parse_sse,
    _wait_for_approval_status,
)
from tests.test_agent_loop import FakeAgentLLM, _tool_call
from tests.test_custom_workflow_lifecycle import draft

if TYPE_CHECKING:
    from pathlib import Path


def _set_loop(ctx: Any, store: ApprovalStore, tool_name: str, arguments: dict[str, Any]) -> None:
    ctx.agent_loop = AgentLoop(
        FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call(tool_name, arguments)]),
                LLMResponse(content="请在来源卡片中确认。"),
            ]
        ),
        ctx.agent_tool_registry,
        approval_gate=store,
    )


def _submit_chat(
    client: TestClient, turn_id: str = "source-approval"
) -> list[tuple[str, dict[str, Any]]]:
    created = client.post(
        "/api/chat/turns",
        json={
            "turn_id": turn_id,
            "session": "desktop",
            "scope": "chat",
            "message": "添加这个来源",
            "streaming": True,
            "skill": "system-steward",
        },
    )
    assert created.status_code == 200
    response = client.post(
        "/api/chat/agent/stream",
        json={
            "turn_id": turn_id,
            "session": "desktop",
            "message": "添加这个来源",
            "skill": "system-steward",
        },
    )
    assert response.status_code == 200
    return _parse_sse(response.text)


def _setup(tmp_path: Path) -> tuple[Any, Any, ApprovalStore, WorkflowService]:
    store = ApprovalStore(tmp_path / "approvals.json")
    app = _approval_app(tmp_path, store=store)
    ctx = app.state.runtime_context
    ctx.agent_tool_registry = ToolRegistry(
        build_custom_source_tools(
            SimpleNamespace(
                database=ctx.database,
                config=Config(),
                llm_service=None,
            )
        )
    )
    return app, ctx, store, WorkflowService(ctx.database)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_source_chat_approval_is_readable_durable_and_execution_is_gated(
    tmp_path: Path,
    decision: str,
) -> None:
    app, ctx, store, service = _setup(tmp_path)
    ready = draft(service, name="Django updates", intent="Python release notes")
    _set_loop(
        ctx,
        store,
        "activate_discovery_source",
        {
            "draft_id": ready["draft_id"],
            "target_share": 3,
            "interval_minutes": 45,
        },
    )
    with TestClient(app) as client:
        events = _submit_chat(client)
        request = next(data for event, data in events if event == "approval_request")
        approval_id = request["approval_id"]
        summary = request["summary"]
        assert "Django updates" in summary
        assert ready["entry_url"] in summary and "Python release notes" in summary
        assert "推荐比例：3" in summary and "抓取间隔：45 分钟" in summary
        assert ready["draft_id"] not in summary and "session-a" not in summary
        assert ApprovalStore(tmp_path / "approvals.json").get(approval_id).summary == summary
        assert service.sources.list_sources() == []
        assert not service.store.get_draft(ready["draft_id"]).get("published_workflow_id")

        response = client.post(f"/api/chat/approvals/{approval_id}/{decision}")
        assert response.status_code == 200
        if decision == "approve":
            settled = _wait_for_approval_status(client, approval_id, {"executed", "failed"})
            assert settled["status"] == "executed", settled
            sources = service.sources.list_sources()
            assert len(sources) == 1 and sources[0]["enabled"]
            assert sources[0]["target_share"] == 3 and sources[0]["interval_minutes"] == 45
            assert len(service.store.list_workflows(sources[0]["source_id"])) == 1
            assert client.post(f"/api/chat/approvals/{approval_id}/approve").status_code == 200
            assert len(service.sources.list_sources()) == 1
            assert len(service.store.list_workflows(sources[0]["source_id"])) == 1
        else:
            assert store.get(approval_id).status == "rejected"
            assert service.sources.list_sources() == []
            assert service.store.get_draft(ready["draft_id"])["status"] == "ready"
            assert client.post(f"/api/chat/approvals/{approval_id}/approve").status_code == 409
    replay = ctx.database.get_chat_turn("source-approval")["payload"]["agent_events"]
    assert next(row for row in replay if row["type"] == "approval_request")["summary"] == summary


def test_existing_source_approval_uses_retained_policy_and_current_name(tmp_path: Path) -> None:
    app, ctx, store, service = _setup(tmp_path)
    source = service.activate(draft(service)["draft_id"], target_share=7, interval_minutes=90)
    service.sources.update_source(source["source_id"], name="Release monitor", enabled=False)
    another = draft(
        service,
        source_id=source["source_id"],
        name="Untrusted draft display",
        intent="Security release notes",
    )
    _set_loop(
        ctx,
        store,
        "activate_discovery_source",
        {
            "draft_id": another["draft_id"],
            "target_share": 1,
            "interval_minutes": 5,
        },
    )
    with TestClient(app) as client:
        request = next(data for event, data in _submit_chat(client) if event == "approval_request")
        assert "Release monitor" in request["summary"]
        assert "Untrusted draft display" not in request["summary"]
        assert "推荐比例：7" in request["summary"] and "抓取间隔：90 分钟" in request["summary"]
        assert "保持停用" in request["summary"]
        client.post(f"/api/chat/approvals/{request['approval_id']}/approve")
        settled = _wait_for_approval_status(client, request["approval_id"], {"executed", "failed"})
        assert settled["status"] == "executed"
        final = service.sources.get_source(source["source_id"])
        assert final["target_share"] == 7 and final["interval_minutes"] == 90
        assert not final["enabled"]


def test_manage_summary_names_real_source_and_describes_changes(tmp_path: Path) -> None:
    app, ctx, store, service = _setup(tmp_path)
    source = service.activate(draft(service, name="Original articles")["draft_id"])
    _set_loop(
        ctx,
        store,
        "manage_discovery_source",
        {
            "source_id": source["source_id"],
            "name": "New name",
            "target_share": 4,
            "interval_minutes": 60,
            "enabled": False,
        },
    )
    with TestClient(app) as client:
        request = next(data for event, data in _submit_chat(client) if event == "approval_request")
        assert (
            "Original articles" in request["summary"] and source["entry_url"] in request["summary"]
        )
        assert "名称：Original articles → New name" in request["summary"]
        assert "推荐比例：1 → 4" in request["summary"] and "状态：停用" in request["summary"]
        assert service.sources.get_source(source["source_id"])["name"] == "Original articles"


@pytest.mark.parametrize("arguments", [{"draft_id": "missing"}, {"draft_id": 42}])
async def test_invalid_source_approval_does_not_persist_a_card(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    _app, ctx, store, service = _setup(tmp_path)
    _set_loop(ctx, store, "activate_discovery_source", arguments)
    events = [event async for event in ctx.agent_loop.run(system_instruction="s", user_message="u")]
    assert not any(event.type == "approval_request" for event in events)
    assert any(event.type == "tool_result" and not event.ok for event in events)
    assert store.list() == [] and service.sources.list_sources() == []


@pytest.mark.parametrize("summary", ["", None])
async def test_empty_trusted_summary_fails_closed(summary: Any) -> None:
    store = ApprovalStore()
    executed = []
    registry = ToolRegistry(
        [
            Tool(
                name="write",
                description="write",
                permission_level="hard_write",
                handler=lambda args: executed.append(args),
                approval_summary=lambda _: summary,
            )
        ]
    )
    loop = AgentLoop(
        FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("write", {})]),
                LLMResponse(content="失败"),
            ]
        ),
        registry,
        approval_gate=store,
    )
    events = [event async for event in loop.run(system_instruction="s", user_message="u")]
    assert store.list() == [] and executed == []
    assert any(event.type == "tool_result" and not event.ok for event in events)
