"""Durable preference freshness survives bounded prompt-history truncation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.recommendation.director.controller import plan_needs_replanning
from openbiliclaw.recommendation.director.store import (
    BatchInstance,
    DirectorStore,
    SessionSnapshot,
)
from tests.test_director_horizon_modes import _horizon, _request
from tests.test_director_runtime import seed_database
from tests.test_director_store import begin, commit

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from openbiliclaw.recommendation.director.models import CompositionRequest
    from openbiliclaw.storage.database import Database


@pytest.fixture
def durable_store(tmp_path: Path) -> Iterator[tuple[Database, DirectorStore]]:
    db = seed_database(tmp_path / "feedback-watermark.db", count=40)
    try:
        yield db, DirectorStore(db)
    finally:
        db.close()


def _batch(store: DirectorStore, *, stream: str = "s", offset: int = 0) -> BatchInstance:
    claim = begin(store, f"batch-{offset}", stream_ref=stream, target_count=12)
    return commit(store, claim, *range(offset, offset + 12))


def _snapshot_request(snapshot: SessionSnapshot) -> CompositionRequest:
    request = _request()
    fields = {
        "session_ref",
        "stream_ref",
        "state_revision",
        "feedback_cursor",
        "configuration_revision",
        "real_feedback",
        "recent_screens",
        "latest_preference_revision",
    }
    return request.model_copy(
        update={
            "session": request.session.model_copy(
                update=snapshot.model_dump(mode="python", include=fields)
            )
        }
    )


def test_ten_actual_acks_preserve_agent_plan_without_expanding_prompt_history(durable_store):
    _, store = durable_store
    batch = _batch(store)
    before = _snapshot_request(store.session("s"))
    plan = _horizon(before)
    assert before.session.latest_preference_revision == 0
    for index, item in enumerate(batch.items[:10]):
        store.present(batch.batch_ref, f"ack-{index}", [str(item["item_key"])])

    snapshot = store.session("s")
    assert snapshot.state_revision == 10
    assert snapshot.latest_preference_revision == 0
    assert len(snapshot.real_feedback) == 4
    assert all(event.kind == "presented" for event in snapshot.real_feedback)
    current = _snapshot_request(snapshot)
    assert not plan_needs_replanning(current, plan, strategy="agent_only")
    assert "latest_preference_revision" not in current.session.prompt_context()
    assert "latest_preference_revision" not in current.session.prompt_context(fast=True)


@pytest.mark.parametrize("kind", ["like", "dislike", "skip", "explicit_request"])
def test_preference_survives_weak_click_burst_and_invalidates_older_plan(durable_store, kind):
    _, store = durable_store
    batch = _batch(store)
    plan = _horizon(_snapshot_request(store.session("s")))
    strong = store.feedback(batch.batch_ref, "strong", str(batch.items[0]["item_key"]), kind)
    for index, item in enumerate(batch.items[1:8]):
        store.feedback(batch.batch_ref, f"click-{index}", str(item["item_key"]), "click")

    snapshot = store.session("s")
    assert snapshot.latest_preference_revision == strong.state_revision == 1
    assert snapshot.state_revision == 8
    assert len(snapshot.real_feedback) == 4
    assert sum(event.kind == "click" for event in snapshot.real_feedback) == 3
    assert (
        sum(event.event_ref == "strong" and event.kind == kind for event in snapshot.real_feedback)
        == 1
    )
    assert plan_needs_replanning(_snapshot_request(snapshot), plan, strategy="agent_only")


def test_replan_keeps_actual_request_semantics_even_when_same_card_history_loses_it(durable_store):
    _, store = durable_store
    batch = _batch(store)
    key = str(batch.items[0]["item_key"])
    store.feedback(
        batch.batch_ref, "explicit", key, "explicit_request", note="不要泛泛介绍，要真正落地的"
    )
    for i in range(7):
        store.feedback(batch.batch_ref, f"click-{i}", key, "click")
    snapshot = store.session("s")
    assert all(action.kind == "click" for action in snapshot.recent_screens[0].cards[0].actions)
    feedback = next(event for event in snapshot.real_feedback if event.event_ref == "explicit")
    assert feedback.description == "不要泛泛介绍，要真正落地的"
    assert feedback.occurred_at > 0
    assert feedback.content[0].title == batch.items[0]["title"]
    assert len(snapshot.real_feedback) == 4


def test_plan_created_after_preference_keeps_unseen_future_screens_on_ack_burst(durable_store):
    _, store = durable_store
    batch = _batch(store)
    strong = store.feedback(batch.batch_ref, "like", str(batch.items[0]["item_key"]), "like")
    plan = _horizon(_snapshot_request(store.session("s")))
    assert plan.planning_state_revision == strong.state_revision
    for index, item in enumerate(batch.items[1:11]):
        store.present(batch.batch_ref, f"ack-{index}", [str(item["item_key"])])

    snapshot = store.session("s")
    assert snapshot.state_revision == 11
    assert snapshot.latest_preference_revision == 1
    assert len(snapshot.real_feedback) == 5  # One preference plus the four newest ACKs.
    assert not plan_needs_replanning(_snapshot_request(snapshot), plan, strategy="agent_only")


def test_watermark_uses_stream_revision_not_global_event_cursor_and_deduplicates(durable_store):
    _, store = durable_store
    first = _batch(store)
    second = _batch(store, stream="other", offset=12)
    for index, item in enumerate(first.items[:5]):
        store.feedback(first.batch_ref, f"click-{index}", str(item["item_key"]), "click")
    first_preference = store.feedback(
        first.batch_ref, "like", str(first.items[5]["item_key"]), "like"
    )
    assert store.session("other").latest_preference_revision == 0
    second_preference = store.feedback(
        second.batch_ref, "dislike", str(second.items[0]["item_key"]), "dislike"
    )
    assert int(second_preference.feedback_cursor) > first_preference.state_revision
    assert store.session("other").latest_preference_revision == 1
    assert store.session("s").latest_preference_revision == 6
    assert (
        store.feedback(first.batch_ref, "like", str(first.items[5]["item_key"]), "like")
        == first_preference
    )
    assert store.session("s").state_revision == 6
    assert store.session("s").latest_preference_revision == 6


def test_additive_index_reads_existing_receipts_without_rewriting_them(durable_store):
    db, store = durable_store
    batch = _batch(store)
    store.feedback(batch.batch_ref, "preference", str(batch.items[0]["item_key"]), "like")
    before = tuple(db.conn.execute("SELECT * FROM director_feedback").fetchone())
    db.conn.execute("DROP INDEX idx_director_feedback_preference_revision")
    db.conn.commit()

    reopened = DirectorStore(db)
    assert reopened.session("s").latest_preference_revision == 1
    assert tuple(db.conn.execute("SELECT * FROM director_feedback").fetchone()) == before
    query_plan = db.conn.execute(
        "EXPLAIN QUERY PLAN SELECT "
        "CAST(json_extract(receipt_json, '$.state_revision') AS INTEGER) "
        "FROM director_feedback WHERE stream_ref = ? AND feedback_json <> '' "
        "AND json_extract(NULLIF(feedback_json, ''), '$.kind') NOT IN ('presented', 'click') "
        "ORDER BY CAST(json_extract(receipt_json, '$.state_revision') AS INTEGER) DESC LIMIT 1",
        ("s",),
    ).fetchall()
    assert any("idx_director_feedback_preference_revision" in str(row[3]) for row in query_plan)
    assert not any("TEMP B-TREE" in str(row[3]) for row in query_plan)


def test_legacy_constructed_snapshot_does_not_invent_authoritative_watermark():
    assert SessionSnapshot(session_ref="session", stream_ref="s").latest_preference_revision is None
