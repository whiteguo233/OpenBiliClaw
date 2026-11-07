"""Verification pauses survive polling/restart and clear only on actual recovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.custom_workflow_producer import CustomWorkflowProducer
from openbiliclaw.sources.protocol import SourceFetchResult
from openbiliclaw.sources.source_alerts import alert_events
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "attention.db")
    db.initialize()
    yield WorkflowService(db)
    db.close()


def make_draft(service, **changes):
    value = {
        "entry_url": "https://example.com/articles/",
        "origin": "https://example.com",
        "name": "Example articles",
        "intent": "Latest articles",
        "source_id": "",
        "session_id": "owned",
        "executor": "ego",
        "query_mode": "none",
        "query": "",
        "status": "ready",
        "trial_success": True,
        "workflow": {
            "version": 1,
            "entry_url": "https://example.com/articles/",
            "allowed_origins": ["https://example.com"],
            "steps": [{"action": "collect", "item_selector": "article", "link_selector": "a"}],
        },
    }
    return service.store.save_draft(value | changes)


async def test_late_verification_result_cannot_alert_over_a_newer_workflow(service):
    initial = make_draft(service)
    source = service.activate(initial["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]

    async def late_result(*args, **kwargs):
        replacement = make_draft(
            service,
            source_id=source["source_id"],
            workflow_id=workflow["workflow_id"],
            base_version=workflow["version"],
        )
        service.store.publish(replacement["draft_id"], source["source_id"])
        return SourceFetchResult(status="login_required", error_code="verification_required")

    service.fetch = AsyncMock(side_effect=late_result)
    pipeline = SimpleNamespace(enqueue_candidates=lambda _: pytest.fail("must not enqueue"))
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], pipeline, service=service
    )
    result = await producer.produce_if_due()
    assert result["reason"] == "stale"
    assert service.attention() == []
    assert alert_events(service.database) == []


@pytest.mark.parametrize("sink_error", [False, True])
async def test_partial_collection_queue_pressure_cannot_erase_verification_pause(
    service, sink_error
):
    draft = make_draft(service)
    source = service.activate(draft["draft_id"])
    article = DiscoveredContent(
        content_id="before-challenge",
        title="Real item before a later verification page",
        content_url="https://example.com/articles/one",
        source_platform="web",
        source_strategy="web-workflow",
    )
    service.fetch = AsyncMock(
        return_value=SourceFetchResult(
            status="login_required",
            error_code="verification_required",
            items=[article],
            scope_complete=False,
        )
    )

    def enqueue(items, **kwargs):
        if sink_error:
            raise RuntimeError("queue unavailable")
        return 0

    producer = CustomWorkflowProducer(
        service.database,
        source["source_id"],
        SimpleNamespace(enqueue_candidates=enqueue),
        service=service,
    )
    result = await producer.produce_if_due()
    assert result["reason"] == "login_required"
    assert result["enqueued"] == 0
    assert service.attention()[0]["code"] == "verification_required"
    assert len(alert_events(service.database)) == 1
    assert not (await producer.produce_if_due(force=True))["ran"]


@pytest.mark.parametrize("code", ["verification_required", "login_required"])
async def test_run_pauses_alerts_once_and_survives_service_restart(service, code):
    draft = make_draft(service)
    source = service.activate(draft["draft_id"], target_share=3, interval_minutes=5)
    service.fetch = AsyncMock(
        return_value=SourceFetchResult(
            status="login_required", error_code=code, detail="private-url?token=SECRET"
        )
    )
    pipeline = SimpleNamespace(enqueue_candidates=lambda _: pytest.fail("must not enqueue"))
    producer = CustomWorkflowProducer(
        service.database, source["source_id"], pipeline, service=service
    )
    first = await producer.produce_if_due()
    assert first["ran"] and first["reason"] == "login_required"
    second = await producer.produce_if_due(force=True)
    assert not second["ran"] and second["reason"] == "login_required"
    service.fetch.assert_awaited_once()
    alerts = alert_events(service.database)
    assert len(alerts) == 1 and alerts[0]["code"] == code and alerts[0]["count"] == 1
    assert alerts[0]["category"] == "discovery" and "SECRET" not in str(alerts)
    restarted = WorkflowService(service.database)
    attention = restarted.attention()
    assert len(attention) == 1 and attention[0]["source_id"] == source["source_id"]
    assert attention[0]["code"] == code and attention[0]["action"] == "source_login"
    assert "SECRET" not in str(attention)
    current = restarted.list_sources()[0]
    assert current["enabled"] and current["target_share"] == 3
    assert current["health"]["attention_required"] and current["health"]["next_run_at"] == 0
    recovery = restarted.login_draft(source["source_id"])
    assert len(restarted.attention()) == 1, "opening login does not resolve or duplicate an alert"
    with pytest.raises(ValueError, match="successful trial"):
        restarted.store.publish(recovery["draft_id"], source["source_id"])
    restarted.store.save_draft(
        dict(recovery, status="ready", trial_success=True), draft_id=recovery["draft_id"]
    )
    restarted.store.publish(recovery["draft_id"], source["source_id"])
    assert restarted.attention() == []
    assert restarted.store.claim(recovery["workflow_id"], {}) is not None


async def test_disabled_source_hides_attention_without_bypassing_pause_on_reenable(service):
    draft = make_draft(service)
    source = service.activate(draft["draft_id"])
    workflow = service.store.list_workflows(source["source_id"])[0]
    run = service.store.claim(workflow["workflow_id"], {})
    service.store.complete(
        run,
        status="login_required",
        items=[],
        enqueue=lambda _: 0,
        interval_minutes=5,
        error="verification_required",
    )
    service.sources.update_source(source["source_id"], enabled=False)
    assert service.attention() == []
    service.sources.update_source(source["source_id"], enabled=True)
    assert len(service.attention()) == 1
    assert service.store.claim(workflow["workflow_id"], {}, force=True) is None
    service.sources.archive_source(source["source_id"])
    assert service.attention() == []


def test_new_draft_attention_is_durable_bounded_and_does_not_expose_plan(service):
    draft = make_draft(
        service,
        status="needs_login",
        trial_success=False,
        attention_code="verification_required",
        detail="SECRET",
    )
    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service))
    with TestClient(app) as client:
        response = client.get("/api/custom-sources/attention")
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1 and items[0]["draft_id"] == draft["draft_id"]
        assert items[0]["action"] == "draft_login"
        assert items[0]["code"] == "verification_required"
        assert not any(s in response.text for s in ("SECRET", "owned", "allowed_origins"))
        service.store.save_draft(dict(draft, status="ready"), draft_id=draft["draft_id"])
        assert client.get("/api/custom-sources/attention").json()["items"] == []
