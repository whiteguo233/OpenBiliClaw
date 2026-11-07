"""Failure episodes use committed state across retries, versions and processes."""

import asyncio
import subprocess
import sys
import threading
from contextlib import suppress

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.diagnostics_alerts import DiagnosticsAlertBuffer
from openbiliclaw.sources.source_alerts import (
    HISTORY_LIMIT,
    alert_events,
    diagnostics_snapshot,
    record_transition,
    relay_alerts,
)
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database
from tests.test_source_attention import make_draft


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / "failures.db")
    db.initialize()
    yield WorkflowService(db)
    db.close()


def run_once(service, workflow_id, status="failed", error="timeout"):
    # Clock control belongs only to the test; production backoff is unchanged.
    service.database.conn.execute(
        "UPDATE custom_workflow_state SET next_run_at=0 WHERE workflow_id=?", (workflow_id,)
    )
    service.database.conn.commit()
    run = service.store.claim(workflow_id, {})
    assert run
    return service.store.complete(
        run,
        status=status,
        items=[],
        enqueue=lambda _: 0,
        interval_minutes=5,
        error=error,
    )


def activate(service):
    source = service.activate(make_draft(service)["draft_id"], target_share=3)
    return source, service.store.list_workflows(source["source_id"])[0]


def test_failure_episode_threshold_retry_recovery_and_recurrence(service):
    source, workflow = activate(service)
    for count in (1, 2):
        run_once(service, workflow["workflow_id"])
        assert service.attention() == [] and alert_events(service.database) == []
        assert f"{count} 次" in service.list_sources()[0]["health"]["message"]
    run_once(service, workflow["workflow_id"])
    warning = service.attention()[0]
    assert warning["action"] == "source_settings" and warning["failure_count"] == 3
    assert warning["code"] == "source_timeout" and warning["next_retry_at"] > 0
    assert service.store.claim(workflow["workflow_id"], {}, force=True) is None
    assert len(alert_events(service.database)) == 1
    for _ in range(2):
        run_once(service, workflow["workflow_id"])
    assert service.attention()[0]["failure_count"] == 5
    assert len(alert_events(service.database)) == 1, "retries must not flood event history"
    run_once(service, workflow["workflow_id"], "empty", "")
    assert service.attention() == []
    events = alert_events(service.database)
    assert [e["code"] for e in events] == ["source_recovered", "source_timeout"]
    assert service.sources.get_source(source["source_id"])["target_share"] == 3
    for _ in range(3):
        run_once(service, workflow["workflow_id"])
    assert len(alert_events(service.database)) == 3


def test_warning_survives_inflight_retry_and_disable_reenable(service):
    source, workflow = activate(service)
    for _ in range(3):
        run_once(service, workflow["workflow_id"])
    service.sources.update_source(source["source_id"], enabled=False)
    assert service.attention() == []
    service.sources.update_source(source["source_id"], enabled=True)
    assert len(service.attention()) == 1
    service.database.conn.execute("UPDATE custom_workflow_state SET next_run_at=0")
    service.database.conn.commit()
    run = service.store.claim(workflow["workflow_id"], {})
    assert run and len(service.attention()) == 1
    service.store.complete(run, status="ok", items=[], enqueue=lambda _: 0, interval_minutes=5)
    assert service.attention() == []


def test_router_lifespan_relays_a_separate_worker_process(service):
    _, workflow = activate(service)
    received = threading.Event()
    events = []

    async def publisher(event):
        events.append(event)
        received.set()

    app = FastAPI()
    app.include_router(create_custom_sources_router(lambda: service, event_publisher=publisher))
    script = """
import sys
from openbiliclaw.storage.database import Database
from openbiliclaw.sources.workflow_service import WorkflowService
db=Database(sys.argv[1]); db.initialize(); service=WorkflowService(db)
for _ in range(3):
    db.conn.execute('UPDATE custom_workflow_state SET next_run_at=0'); db.conn.commit()
    run=service.store.claim(sys.argv[2],{})
    service.store.complete(
        run,status='failed',items=[],enqueue=lambda _:0,interval_minutes=5,error='timeout'
    )
db.close()
"""
    with TestClient(app) as client:
        subprocess.run(
            [sys.executable, "-c", script, str(service.database._db_path), workflow["workflow_id"]],
            check=True,
            capture_output=True,
            timeout=30,
        )
        assert received.wait(5)
        assert len(events) == 1 and events[0]["type"] == "diagnostics.alert"
        assert (
            client.get("/api/custom-sources/attention").json()["items"][0]["code"]
            == "source_timeout"
        )
    assert len(alert_events(service.database)) == 1


def test_replaced_run_cannot_add_or_resolve_warning_and_successful_publish_recovers(service):
    source, workflow = activate(service)
    for _ in range(3):
        run_once(service, workflow["workflow_id"])
    service.database.conn.execute("UPDATE custom_workflow_state SET next_run_at=0")
    service.database.conn.commit()
    stale = service.store.claim(workflow["workflow_id"], {})
    draft = make_draft(
        service,
        source_id=source["source_id"],
        workflow_id=workflow["workflow_id"],
        base_version=workflow["version"],
    )
    service.store.publish(draft["draft_id"], source["source_id"])
    assert service.attention() == []
    assert alert_events(service.database)[0]["code"] == "source_recovered"
    result = service.store.complete(
        stale,
        status="login_required",
        items=[],
        enqueue=lambda _: 0,
        interval_minutes=5,
        error="verification_required",
    )
    assert result["status"] == "stale" and len(alert_events(service.database)) == 2


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        ("rate_limited", "rate_limited", "source_rate_limited"),
        ("failed", "ego_navigation_failed (net::ERR_CONNECTION_RESET)", "source_network_error"),
        ("degraded", "extraction_unverified", "source_parse_error"),
        ("failed", "executor_unavailable", "source_runtime_unavailable"),
        ("degraded", "candidate_queue_full", "source_queue_error"),
        ("failed", "not_found", "source_not_found"),
        ("failed", "access_denied", "source_access_denied"),
        ("failed", "invalid_document", "source_parse_error"),
        ("failed", "SECRET=https://private.invalid/?token=secret", "source_fetch_failed"),
    ],
)
def test_safe_reasons_cover_web_feed_and_partial_errors(service, status, error, expected):
    _, workflow = activate(service)
    for _ in range(3):
        run_once(service, workflow["workflow_id"], status, error)
    assert service.attention()[0]["code"] == expected
    assert "SECRET" not in str(service.attention()) + str(alert_events(service.database))
    assert "private.invalid" not in str(alert_events(service.database))


async def test_independent_connection_and_reopened_store_reach_existing_event_stream(service):
    _, workflow = activate(service)
    reader = Database(service.database._db_path)
    reader.initialize()
    queue = asyncio.Queue()

    async def publish(event):
        queue.put_nowait(event)

    task = asyncio.create_task(relay_alerts(lambda: reader, publish, after_id=0, interval=0.01))
    try:
        for _ in range(3):
            run_once(service, workflow["workflow_id"])
        event = await asyncio.wait_for(queue.get(), 3)
        assert event["type"] == "diagnostics.alert" and event["code"] == "source_timeout"
        assert alert_events(reader) == alert_events(service.database)
        memory = DiagnosticsAlertBuffer()
        memory.record(category="llm", code="timeout", message="LLM timeout")
        snapshot = diagnostics_snapshot(reader, memory.snapshot())
        assert len(snapshot["alerts"]) == 2
        assert snapshot["source_cursor"] == event["source_event_id"]
        assert (
            diagnostics_snapshot(
                reader, memory.snapshot(), source_since_id=snapshot["source_cursor"]
            )["alerts"]
            == memory.snapshot()["alerts"]
        )
        reopened = WorkflowService(reader)
        assert len(reopened.attention()) == 1
        run_once(service, workflow["workflow_id"], "not_modified", "")
        recovered = await asyncio.wait_for(queue.get(), 3)
        assert recovered["code"] == "source_recovered" and recovered["severity"] == "info"
        assert reopened.attention() == []
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        reader.close()


def test_one_workflow_recovery_preserves_other_issue_and_login_priority(service):
    source, first = activate(service)
    second_draft = make_draft(service, source_id=source["source_id"], intent="Another section")
    second = service.store.publish(second_draft["draft_id"], source["source_id"])
    for _ in range(3):
        run_once(service, first["workflow_id"])
        run_once(service, second["workflow_id"])
    assert len(service.attention()) == 1
    assert service.list_sources()[0]["health"]["attention_count"] == 2
    run_once(service, first["workflow_id"], "ok", "")
    assert len(service.attention()) == 1
    assert service.list_sources()[0]["health"]["attention_count"] == 1
    run_once(service, first["workflow_id"], "login_required", "verification_required")
    assert service.attention()[0]["action"] == "source_login"
    assert alert_events(service.database)[0]["code"] == "verification_required"


def test_history_retention_is_bounded_with_monotonic_resume_cursor(service):
    _, workflow = activate(service)
    before = dict(workflow, status="ok", consecutive_failures=0)
    after = dict(workflow, status="failed", consecutive_failures=3, last_error="timeout")
    for i in range(HISTORY_LIMIT + 2):
        record_transition(service.database.conn, before, after, cause=f"test:{i}", now=float(i))
    service.database.conn.commit()
    rows = alert_events(service.database)
    assert len(rows) == HISTORY_LIMIT
    assert rows[-1]["source_event_id"] == 3
    cursor = rows[0]["source_event_id"]
    assert alert_events(service.database, after_id=cursor) == []
    record_transition(service.database.conn, after, before, cause="recovered", now=9999)
    service.database.conn.commit()
    resumed = alert_events(service.database, after_id=cursor)
    assert len(resumed) == 1 and resumed[0]["code"] == "source_recovered"
    assert len(alert_events(service.database)) == HISTORY_LIMIT
