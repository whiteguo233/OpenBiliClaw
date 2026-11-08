"""Losing a workflow lease settles only that run's durable history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.custom_sources import CustomSourceStore
from openbiliclaw.sources.workflow_store import WorkflowStore
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def workflow_store(tmp_path: Path) -> Iterator[tuple[Database, WorkflowStore, dict[str, Any]]]:
    database = Database(tmp_path / "run-history.db")
    database.initialize()
    source = CustomSourceStore(database).upsert_source(
        name="Articles", entry_url="https://example.com/articles", intent="Follow articles"
    )
    store = WorkflowStore(database)
    draft = store.save_draft(
        {"intent": source["intent"], "executor": "http", "status": "ready", "trial_success": True}
    )
    workflow = store.publish(draft["draft_id"], source["source_id"])
    yield database, store, workflow
    database.close()


def _state(database: Database, workflow_id: str) -> dict[str, Any]:
    return dict(
        database.conn.execute(
            "SELECT * FROM custom_workflow_state WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
    )


def _history(database: Database, run_id: str) -> dict[str, Any]:
    return dict(
        database.conn.execute(
            "SELECT * FROM custom_workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    )


def _item(workflow: dict[str, Any]) -> DiscoveredContent:
    return DiscoveredContent(
        content_id="article",
        content_url="https://example.com/articles/one",
        title="Article",
        source_platform="web",
        source_id=workflow["source_id"],
        source_strategy="web-workflow",
    )


@pytest.mark.parametrize("cause", ["disabled", "archived", "expired"])
def test_lost_run_settles_history_without_touching_workflow_state(workflow_store, cause):
    database, store, workflow = workflow_store
    run = store.claim(workflow["workflow_id"], {})
    sources = CustomSourceStore(database)
    if cause == "disabled":
        sources.update_source(workflow["source_id"], enabled=False)
    elif cause == "archived":
        sources.archive_source(workflow["source_id"])
    else:
        database._execute_write(
            "UPDATE custom_workflow_state SET lease_until=0 WHERE workflow_id=?",
            (workflow["workflow_id"],),
        )
    state = _state(database, workflow["workflow_id"])
    enqueued = []
    outcome = store.complete(
        run,
        status="ok",
        items=[_item(workflow)],
        enqueue=lambda items: enqueued.extend(items) or len(items),
        interval_minutes=30,
        cursor={"etag": "stale"},
    )
    assert outcome == {"status": "stale", "discovered_count": 1, "enqueued_count": 0}
    assert enqueued == []
    history = _history(database, run["run_id"])
    assert history["status"] == "stale" and history["completed_at"] is not None
    assert history["discovered_count"] == 1 and history["enqueued_count"] == 0
    assert _state(database, workflow["workflow_id"]) == state
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 0


def test_publish_settles_superseded_run_and_late_completion_preserves_new_lease(workflow_store):
    database, store, workflow = workflow_store
    old_run = store.claim(workflow["workflow_id"], {})
    replacement = store.save_draft(
        {
            "workflow_id": workflow["workflow_id"],
            "base_version": workflow["version"],
            "intent": "Follow updated articles",
            "executor": "http",
            "status": "ready",
            "trial_success": True,
        }
    )
    store.publish(replacement["draft_id"], workflow["source_id"])
    settled = _history(database, old_run["run_id"])
    assert settled["status"] == "superseded" and settled["completed_at"] is not None

    new_run = store.claim(workflow["workflow_id"], {})
    state = _state(database, workflow["workflow_id"])
    outcome = store.complete(
        old_run, status="ok", items=[], enqueue=lambda _: 0, interval_minutes=30
    )
    assert outcome["status"] == "stale"
    assert _history(database, old_run["run_id"]) == settled
    assert _history(database, new_run["run_id"])["status"] == "running"
    assert _state(database, workflow["workflow_id"]) == state


def test_disable_during_durable_enqueue_settles_second_fence_without_advancing_seen(workflow_store):
    database, store, workflow = workflow_store
    run = store.claim(workflow["workflow_id"], {})
    snapshots = []

    def enqueue(items):
        count = database.enqueue_discovery_candidates(
            [discovered_content_to_candidate_write(item) for item in items]
        )
        CustomSourceStore(database).update_source(workflow["source_id"], enabled=False)
        snapshots.append(_state(database, workflow["workflow_id"]))
        return count

    outcome = store.complete(
        run,
        status="ok",
        items=[_item(workflow)],
        enqueue=enqueue,
        interval_minutes=30,
        cursor={"etag": "must-not-commit"},
    )
    assert outcome == {"status": "stale", "discovered_count": 1, "enqueued_count": 1}
    history = _history(database, run["run_id"])
    assert history["status"] == "stale" and history["completed_at"] is not None
    assert history["discovered_count"] == history["enqueued_count"] == 1
    assert _state(database, workflow["workflow_id"]) == snapshots[0]
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 0
    assert database.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0] == 1


def test_wrong_token_cannot_settle_the_actual_running_history(workflow_store):
    database, store, workflow = workflow_store
    run = store.claim(workflow["workflow_id"], {})
    history = _history(database, run["run_id"])
    state = _state(database, workflow["workflow_id"])
    outcome = store.complete(
        dict(run, lease_token="not-the-owner"),
        status="ok",
        items=[],
        enqueue=lambda _: 0,
        interval_minutes=30,
    )
    assert outcome["status"] == "stale"
    assert _history(database, run["run_id"]) == history
    assert _state(database, workflow["workflow_id"]) == state
