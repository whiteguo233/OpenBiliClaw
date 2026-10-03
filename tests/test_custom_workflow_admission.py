"""Workflow frontiers distinguish permanent policy skips from retryable handoffs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import candidate_key_for
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
from openbiliclaw.sources.custom_sources import CustomSourceStore
from openbiliclaw.sources.workflow_store import WorkflowStore
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def handoff(tmp_path: Path) -> Iterator[tuple[Database, WorkflowStore, dict[str, Any], Any]]:
    database = Database(tmp_path / "handoff.db")
    database.initialize()
    source = CustomSourceStore(database).upsert_source(
        name="Official articles",
        entry_url="https://example.com/articles/",
        intent="Follow official articles",
    )
    store = WorkflowStore(database)
    draft = store.save_draft(
        {
            "source_id": source["source_id"],
            "name": source["name"],
            "intent": source["intent"],
            "executor": "http",
            "status": "ready",
            "trial_success": True,
        }
    )
    workflow = store.publish(draft["draft_id"], source["source_id"])
    pipeline = DiscoveryCandidatePipeline(database, SimpleNamespace())
    yield database, store, workflow, pipeline
    database.close()


def _item(workflow: dict[str, Any], content_id: str, published_at: str) -> DiscoveredContent:
    return DiscoveredContent(
        title=f"Article {content_id}",
        content_id=content_id,
        content_url=f"https://example.com/articles/{content_id}",
        source_platform="web",
        source_id=workflow["source_id"],
        source_name=workflow["name"],
        source_strategy="web-workflow",
        published_at=published_at,
    )


def _set_date_policy(database: Database, *, weight: float = 1) -> None:
    database.set_source_publication_date_preferences(
        {
            "web": PublicationDatePreference(
                preset="custom", start_date="2026-01-01", end_date="2026-12-31", weight=weight
            )
        }
    )


@pytest.mark.parametrize("published_at", ["2020-01-01T00:00:00Z", "", "not-a-date"])
def test_strict_date_exclusion_advances_frontier_without_admission(handoff, published_at):
    database, store, workflow, pipeline = handoff
    _set_date_policy(database)
    item = _item(workflow, "excluded", published_at)
    run = store.claim(workflow["workflow_id"], {}, force=True)
    outcome = store.complete(
        run,
        status="ok",
        items=[item],
        enqueue=pipeline.enqueue_candidates,
        interval_minutes=30,
        cursor={"etag": "fully-scanned"},
    )

    assert outcome == {"status": "ok", "discovered_count": 1, "enqueued_count": 0}
    assert database.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0] == 0
    assert database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0
    seen = database.conn.execute("SELECT item_key FROM custom_workflow_seen").fetchall()
    assert [row[0] for row in seen] == [candidate_key_for(item)]
    current = store.get_workflow(workflow["workflow_id"])
    assert current["cursor"] == {"etag": "fully-scanned"}
    assert current["consecutive_failures"] == 0
    assert current["last_error"] == ""
    assert current["last_success_at"] > 0


def test_mixed_date_exclusion_and_retained_candidate_complete_one_frontier(handoff):
    database, store, workflow, pipeline = handoff
    _set_date_policy(database)
    items = [
        _item(workflow, "old", "2020-01-01T00:00:00Z"),
        _item(workflow, "current", "2026-05-01T00:00:00Z"),
    ]
    outcome = store.complete(
        store.claim(workflow["workflow_id"], {}, force=True),
        status="ok",
        items=items,
        enqueue=pipeline.enqueue_candidates,
        interval_minutes=30,
        cursor={"etag": "mixed"},
    )

    assert outcome["status"] == "ok" and outcome["enqueued_count"] == 1
    rows = database.conn.execute("SELECT content_id FROM discovery_candidates").fetchall()
    assert [row[0] for row in rows] == ["current"]
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 2
    assert store.get_workflow(workflow["workflow_id"])["cursor"] == {"etag": "mixed"}


def test_queue_pressure_keeps_eligible_content_and_frontier_retryable(handoff):
    database, store, workflow, pipeline = handoff
    _set_date_policy(database)
    items = [_item(workflow, "retry", "2026-05-01T00:00:00Z")]
    first = store.complete(
        store.claim(workflow["workflow_id"], {}, force=True),
        status="ok",
        items=items,
        enqueue=lambda _: 0,
        interval_minutes=30,
        cursor={"etag": "must-not-commit"},
    )
    assert first["status"] == "degraded"
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 0
    current = store.get_workflow(workflow["workflow_id"])
    assert current["cursor"] == {} and current["last_error"] == "candidate_queue_full"

    second = store.complete(
        store.claim(workflow["workflow_id"], {}, force=True),
        status="ok",
        items=items,
        enqueue=pipeline.enqueue_candidates,
        interval_minutes=30,
        cursor={"etag": "retained-on-retry"},
    )
    assert second["status"] == "ok" and second["enqueued_count"] == 1
    assert database.conn.execute("SELECT COUNT(*) FROM custom_workflow_seen").fetchone()[0] == 1
    assert store.get_workflow(workflow["workflow_id"])["cursor"] == {"etag": "retained-on-retry"}


def test_soft_date_preference_still_enqueues_out_of_window_content(handoff):
    database, _store, workflow, pipeline = handoff
    _set_date_policy(database, weight=0.5)
    count = pipeline.enqueue_candidates([_item(workflow, "soft", "2020-01-01T00:00:00Z")])
    assert type(count) is int and count == 1
    assert database.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0] == 1


def test_policy_skip_does_not_hide_queue_pressure_for_another_item(handoff):
    database, store, workflow, _pipeline = handoff
    _set_date_policy(database)
    old = _item(workflow, "old", "2020-01-01T00:00:00Z")
    current = _item(workflow, "current", "2026-05-01T00:00:00Z")
    outcome = store.complete(
        store.claim(workflow["workflow_id"], {}, force=True),
        status="ok",
        items=[old, current],
        enqueue=lambda _: 0,
        interval_minutes=30,
        cursor={"etag": "must-not-commit"},
    )
    assert outcome["status"] == "degraded" and outcome["enqueued_count"] == 0
    seen = database.conn.execute("SELECT item_key FROM custom_workflow_seen").fetchall()
    assert [row[0] for row in seen] == [candidate_key_for(old)]
    assert store.get_workflow(workflow["workflow_id"])["cursor"] == {}
