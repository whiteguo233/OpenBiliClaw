"""Approved workflow replacement recovers a changed feed without duplicate schedules."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.agent.approvals import ApprovalStore
from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tools import AgentToolContext, build_agent_tool_registry
from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from tests.test_agent_approvals import _wait_for_approval_status
from tests.test_agent_loop import FakeAgentLLM, _collect, _tool_call
from tests.test_custom_feed_workflow import FEED_URL, _feed, _service
from tests.test_custom_feed_workflow import database as database
from tests.test_custom_source_approvals import _set_loop, _setup, _submit_chat
from tests.test_custom_workflow_lifecycle import draft as saved_draft

NEW_ORIGIN = "https://newjournal.example.com"


@pytest.fixture
def prepared(database, monkeypatch):
    response = list(_feed("rss"))
    service = _service(
        database,
        monkeypatch,
        lambda _request: httpx.Response(
            200, content=response[0], headers={"Content-Type": response[1]}
        ),
    )
    return service, response


async def _initial(service, **kwargs):
    draft = await service.prepare(
        url=FEED_URL, intent="Follow the journal", executor="http", **kwargs
    )
    source = service.activate(draft["draft_id"], target_share=3, interval_minutes=5)
    return source, service.store.list_workflows(source["source_id"])[-1]


async def _update(service, source, workflow):
    return await service.prepare(
        url=FEED_URL,
        intent="Follow the journal",
        executor="http",
        source_id=source["source_id"],
        workflow_id=workflow["workflow_id"],
    )


def _changed_feed(response):
    response[:] = _feed(
        "rss",
        extra=(
            f"<item><title>New journal article</title><link>{NEW_ORIGIN}/articles/two</link>"
            "<description>Another journal article</description></item>"
        ),
    )


async def test_scope_recovery_replaces_version_and_preserves_seen_and_history(prepared):
    service, response = prepared
    source, workflow = await _initial(service)
    pipeline = DiscoveryCandidatePipeline(
        database=service.database, discovery_engine=SimpleNamespace()
    )
    producer = CustomWorkflowProducer(
        service.database,
        source["source_id"],
        pipeline,
        service=service,
        candidate_evaluation_owned_by_coordinator=True,
    )
    assert (await producer.produce_if_due())["enqueued"] == 1
    _changed_feed(response)
    assert (await producer.produce_if_due(force=True))["reason"] == "degraded"
    assert service.list_sources()[0]["health"]["last_error"] == "content_scope_changed"
    update = await _update(service, source, workflow)
    assert update["status"] == "ready" and update["base_version"] == 1
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 1

    # A worker already running when approval arrives cannot overwrite the replacement.
    service.database.conn.execute(
        "UPDATE custom_workflow_state SET next_run_at=0 WHERE workflow_id=?",
        (workflow["workflow_id"],),
    )
    service.database.conn.commit()
    old_run = service.store.claim(workflow["workflow_id"], {})
    assert old_run is not None
    activated = service.activate(update["draft_id"], target_share=1, interval_minutes=30)
    assert activated["source_id"] == source["source_id"]
    assert (activated["target_share"], activated["interval_minutes"]) == (3, 5)
    workflows = service.store.list_workflows(source["source_id"])
    assert len(workflows) == 1
    assert workflows[0]["version"] == 2 and workflows[0]["status"] == "ready"
    assert (
        service.database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0]
        == 1
    )
    assert {row["status"] for row in service.store.runs(source["source_id"])} == {
        "ok",
        "degraded",
        "superseded",
    }
    assert (await producer.produce_if_due())["enqueued"] == 1
    assert service.list_sources()[0]["health"]["status"] == "ready"
    assert service.list_sources()[0]["health"]["last_error"] == ""
    assert (
        service.database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0]
        == 2
    )
    late = service.store.complete(
        old_run, status="ok", items=[], enqueue=lambda _: 0, interval_minutes=5
    )
    assert late["status"] == "stale"
    assert service.store.get_workflow(workflow["workflow_id"])["status"] == "ok"
    service.activate(update["draft_id"])
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2


@pytest.mark.parametrize("decision", ["approve", "reject", "stale"])
def test_update_chat_approval_applies_once_or_preserves_current_version(tmp_path, decision):
    app, ctx, approvals, service = _setup(tmp_path)
    source = service.activate(saved_draft(service)["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    fields = dict(
        source_id=source["source_id"],
        workflow_id=workflow["workflow_id"],
        base_version=1,
        requires_activation=True,
    )
    update = saved_draft(service, **fields)
    _set_loop(ctx, approvals, "activate_discovery_source", {"draft_id": update["draft_id"]})
    with TestClient(app) as client:
        request = next(data for event, data in _submit_chat(client) if event == "approval_request")
        assert "v1 → v2" in request["summary"]
        assert service.store.get_workflow(workflow["workflow_id"])["version"] == 1
        if decision == "stale":
            service.activate(saved_draft(service, **fields)["draft_id"])
        approval_id = request["approval_id"]
        action = "reject" if decision == "reject" else "approve"
        assert client.post(f"/api/chat/approvals/{approval_id}/{action}").status_code == 200
        if decision == "reject":
            assert approvals.get(approval_id).status == "rejected"
            expected = 1
        else:
            settled = _wait_for_approval_status(client, approval_id, {"executed", "failed"})
            assert settled["status"] == ("failed" if decision == "stale" else "executed")
            expected = 2
        assert service.store.get_workflow(workflow["workflow_id"])["version"] == expected
        assert len(service.store.list_workflows(source["source_id"])) == 1
        if decision == "approve":
            assert client.post(f"/api/chat/approvals/{approval_id}/approve").status_code == 200
            tool = ctx.agent_tool_registry.get("activate_discovery_source")
            assert "草稿已应用" in tool.approval_summary({"draft_id": update["draft_id"]})
            assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2


@pytest.mark.parametrize("invalid", ["unknown", "other_source", "missing_source", "split"])
async def test_update_rejects_invalid_workflow_identity_before_saving_draft(prepared, invalid):
    service, _response = prepared
    source, workflow = await _initial(service)
    arguments = dict(source_id=source["source_id"], workflow_id=workflow["workflow_id"])
    if invalid == "unknown":
        arguments["workflow_id"] = "unknown-workflow"
    elif invalid == "other_source":
        other, _ = await _initial(service, separate_source=True)
        arguments["source_id"] = other["source_id"]
    elif invalid == "missing_source":
        arguments.pop("source_id")
    else:
        arguments["separate_source"] = True
    before = service.database.conn.execute(
        "SELECT COUNT(*) FROM custom_workflow_drafts"
    ).fetchone()[0]
    with pytest.raises(ValueError):
        await service.prepare(url=FEED_URL, intent="Follow journal", **arguments)
    assert (
        service.database.conn.execute("SELECT COUNT(*) FROM custom_workflow_drafts").fetchone()[0]
        == before
    )


async def test_approval_time_stale_version_is_rejected_without_duplicate_workflow(prepared):
    service, _response = prepared
    source, workflow = await _initial(service)
    first = await _update(service, source, workflow)
    second = await _update(service, source, workflow)
    service.activate(first["draft_id"])
    with pytest.raises(ValueError, match="workflow changed"):
        service.activate(second["draft_id"])
    assert len(service.store.list_workflows(source["source_id"])) == 1
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2


async def test_source_id_alone_still_adds_an_independent_workflow(prepared):
    service, _response = prepared
    source, _workflow = await _initial(service)
    await _initial(service, source_id=source["source_id"])
    assert len(service.store.list_workflows(source["source_id"])) == 2


async def test_api_update_resumes_trial_but_requires_approval_to_publish(prepared):
    service, response = prepared
    source, workflow = await _initial(service)
    _changed_feed(response)
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = {
            "url": FEED_URL,
            "intent": "Follow the journal",
            "executor": "http",
            "source_id": source["source_id"],
            "workflow_id": workflow["workflow_id"],
        }
        assert (
            await client.post("/api/custom-sources/drafts", json={**payload, "base_version": 9})
        ).status_code == 422
        assert (
            await client.post(
                "/api/custom-sources/drafts", json={**payload, "requires_activation": False}
            )
        ).status_code == 422
        created = await client.post("/api/custom-sources/drafts", json=payload)
        assert created.status_code == 200
        draft = created.json()
        assert draft["workflow_id"] == workflow["workflow_id"] and draft["base_version"] == 1
        assert draft["requires_activation"] is True
        path = f"/api/custom-sources/drafts/{draft['draft_id']}"
        resumed = await client.post(path + "/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "ready"
        assert resumed.json()["requires_activation"] is True
        assert service.store.get_workflow(workflow["workflow_id"])["version"] == 1
        assert (await client.post(path + "/activate", json={})).status_code == 200
        assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2


async def test_existing_login_resume_still_publishes_the_approved_workflow(prepared):
    service, _response = prepared
    source, workflow = await _initial(service)
    draft = service.login_draft(source["source_id"])
    assert "requires_activation" not in draft
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resumed = await client.post(f"/api/custom-sources/drafts/{draft['draft_id']}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "ready"
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2
    assert len(service.store.list_workflows(source["source_id"])) == 1


async def test_chat_lists_existing_flow_and_approval_describes_trusted_replacement(prepared):
    service, response = prepared
    source, workflow = await _initial(service)
    _changed_feed(response)
    draft = await _update(service, source, workflow)
    registry = build_agent_tool_registry(AgentToolContext(database=service.database))
    listed = await registry.dispatch("list_sources", {})
    assert workflow["workflow_id"] in listed.content and "版本: 1" in listed.content
    approvals = ApprovalStore()
    llm = FakeAgentLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    _tool_call("activate_discovery_source", {"draft_id": draft["draft_id"]})
                ],
            ),
            LLMResponse(content="请审批更新。"),
        ]
    )
    events = await _collect(
        AgentLoop(llm, registry, approval_gate=approvals),
        system_instruction="Manage sources",
        user_message="Update the journal feed scope",
    )
    request = next(event for event in events if event.type == "approval_request")
    assert "更新发现流程" in request.summary and "v1 → v2" in request.summary
    assert workflow["workflow_id"] in request.summary
    assert "https://journal.example.com" in request.summary and NEW_ORIGIN in request.summary
    assert "保留已处理" in request.summary
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 1
    # The same trusted resolver rejects an obsolete update before creating a new card.
    source_tool = registry.get("activate_discovery_source")
    competing = await _update(service, source, workflow)
    service.activate(competing["draft_id"])
    with pytest.raises(ValueError, match="workflow changed"):
        source_tool.approval_summary({"draft_id": draft["draft_id"]})
    assert service.store.get_workflow(workflow["workflow_id"])["version"] == 2
