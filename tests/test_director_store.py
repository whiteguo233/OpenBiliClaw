"""Public Interface checks for durable, atomic director screens and feedback."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.recommendation.director.models import ScreenConstraints, TopicQuota
from openbiliclaw.recommendation.director.store import (
    BatchInstance,
    DirectorStore,
    GenerationClaim,
    StoreConflict,
    candidate_fingerprint,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "director.db")
    database.initialize()
    for index in range(12):
        database.cache_content(
            f"BV{index:010d}",
            title=f"Content {index}",
            source="search",
            source_platform="bilibili",
            content_id=f"BV{index:010d}",
            content_url=f"https://www.bilibili.com/video/BV{index:010d}",
            relevance_score=0.9,
            pool_expression=f"Reason {index}",
            pool_topic_label="Testing",
            style_key="tutorial",
            topic_group="alpha" if index % 2 == 0 else "beta",
        )
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> DirectorStore:
    return DirectorStore(db)


def items(*indices: int) -> list[dict[str, Any]]:
    return [
        {"bvid": f"BV{index:010d}", "item_key": f"bilibili:BV{index:010d}"} for index in indices
    ]


def begin(store: DirectorStore, request: str = "r1", **kwargs: Any) -> GenerationClaim:
    result = store.begin(
        kwargs.pop("stream_ref", "s1"),
        request,
        context_digest=kwargs.pop("context_digest", "cfg"),
        direction_ref=kwargs.pop("direction_ref", "d1"),
        direction=kwargs.pop("direction", "Learn"),
        target_count=kwargs.pop("target_count", 2),
        **kwargs,
    )
    assert isinstance(result, GenerationClaim)
    return result


def commit(
    store: DirectorStore, claim: GenerationClaim, *indices: int, **kwargs: Any
) -> BatchInstance:
    return store.commit(
        claim,
        items(*indices),
        requested_strategy=kwargs.pop("requested_strategy", "jev_recurrent"),
        realized_strategy="deterministic",
        **kwargs,
    )


def test_commit_is_atomic_unpresented_and_restore_is_exact(
    store: DirectorStore, db: Database
) -> None:
    claim = begin(store)
    assert store.session("s1").real_feedback == ()
    batch = commit(store, claim, 0, 1)
    assert batch.execution_binding == "SESSION_STEP"
    assert batch.plan_id is None
    assert batch.root_batch_ref == batch.batch_ref
    assert batch.committed_batch_seq == batch.experience_step_seq == 1
    assert (
        db.conn.execute("SELECT COUNT(*) FROM recommendations WHERE presented = 0").fetchone()[0]
        == 2
    )
    assert (
        db.conn.execute("SELECT COUNT(*) FROM content_cache WHERE pool_status='shown'").fetchone()[
            0
        ]
        == 2
    )
    before = store.session("s1")
    assert before.state_revision == 0
    assert before.real_feedback == ()
    db.conn.execute("UPDATE content_cache SET title='changed after commit'")
    db.conn.commit()
    assert store.restore("s1") == batch
    assert store.restore("s1", batch.batch_ref) == batch
    assert store.session("s1") == before
    reopened = DirectorStore(db)
    assert reopened.restore("s1") == batch


def test_commit_retry_and_generation_request_replay(store: DirectorStore) -> None:
    claim = begin(store)
    assert begin(store) == claim
    batch = commit(store, claim, 0, 1)
    assert commit(store, claim, 0, 1) == batch
    assert (
        store.begin(
            "s1", "r1", context_digest="cfg", direction_ref="d1", direction="Learn", target_count=2
        )
        == batch
    )
    with pytest.raises(StoreConflict, match="request_id_reused"):
        begin(store, target_count=3)
    with pytest.raises(StoreConflict, match="committed_screen_immutable"):
        commit(store, claim, 2, 3)


def test_batch_immutable_before_and_after_presentation(store: DirectorStore) -> None:
    claim = begin(store)
    batch = commit(store, claim, 0, 1)
    for _ in range(2):
        with pytest.raises(StoreConflict, match="committed_screen_immutable"):
            commit(store, claim, 1, 0)
        store.present(batch.batch_ref, "ack", [str(batch.items[0]["item_key"])])
    assert store.restore("s1") == batch


@pytest.mark.parametrize(
    "mutation", ["delete", "shown", "dislike", "score", "copy", "identity", "seen", "temporal"]
)
def test_latest_canonical_eligibility_rejects_whole_screen(
    store: DirectorStore, db: Database, mutation: str
) -> None:
    claim = begin(store)
    if mutation == "delete":
        db.conn.execute("DELETE FROM content_cache WHERE bvid='BV0000000001'")
    elif mutation == "seen":
        db.mark_items_seen("bilibili", ["BV0000000001"])
    elif mutation == "identity":
        db.conn.execute(
            "UPDATE content_cache SET item_key='bilibili:other' WHERE bvid='BV0000000001'"
        )
    elif mutation == "temporal":
        db.conn.execute(
            "UPDATE content_cache SET temporal_class='breaking', temporal_confidence=0.99, "
            "temporal_policy_version='v1', published_at='2020-01-01T00:00:00Z' "
            "WHERE bvid='BV0000000001'"
        )
    else:
        assignments = {
            "shown": "pool_status='shown'",
            "dislike": "feedback_type='dislike'",
            "score": "relevance_score=0.01",
            "copy": "pool_expression=''",
        }
        db.conn.execute(
            f"UPDATE content_cache SET {assignments[mutation]} WHERE bvid='BV0000000001'"
        )
    db.conn.commit()
    with pytest.raises(StoreConflict, match="candidate_"):
        commit(store, claim, 0, 1)
    assert db.count_recommendations() == 0
    assert store.restore("s1") is None
    assert (
        db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid='BV0000000000'"
        ).fetchone()[0]
        == "fresh"
    )
    assert store.session("s1").committed_batch_seq == 0


def test_sql_failure_after_insert_rolls_back_every_effect(
    store: DirectorStore, db: Database
) -> None:
    claim = begin(store)
    db.conn.execute(
        "CREATE TRIGGER refuse_batch BEFORE INSERT ON director_batches "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        commit(store, claim, 0, 1)
    assert db.count_recommendations() == 0
    assert (
        db.conn.execute("SELECT COUNT(*) FROM content_cache WHERE pool_status='shown'").fetchone()[
            0
        ]
        == 0
    )
    assert store.restore("s1") is None
    db.conn.execute("DROP TRIGGER refuse_batch")
    db.conn.commit()
    assert commit(store, claim, 0, 1).committed_batch_seq == 1


def test_new_request_fences_previous_request(store: DirectorStore) -> None:
    first = begin(store)
    second = begin(store, "r2")
    with pytest.raises(StoreConflict, match="generation_stale"):
        commit(store, first, 0, 1)
    assert commit(store, second, 2, 3).request_id == "r2"


def test_claim_cannot_be_forged(store: DirectorStore) -> None:
    claim = begin(store)
    with pytest.raises(StoreConflict, match="claim_not_owned"):
        commit(store, claim.model_copy(update={"direction": "forged"}), 0, 1)


def test_expired_lease_does_not_commit(
    store: DirectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim = begin(store)
    monkeypatch.setattr(
        "openbiliclaw.recommendation.director.store.time.time", lambda: claim.expires_at
    )
    with pytest.raises(StoreConflict, match="lease_expired"):
        commit(store, claim, 0, 1)


def test_retry_can_reclaim_crashed_worker_lease_without_reusing_old_fence(
    store: DirectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim = begin(store)
    monkeypatch.setattr(
        "openbiliclaw.recommendation.director.store.time.time", lambda: claim.expires_at + 1
    )
    resumed = begin(store)
    assert resumed.fence > claim.fence
    assert resumed.expires_at > claim.expires_at
    with pytest.raises(StoreConflict, match="claim_not_owned"):
        commit(store, claim, 0, 1)
    assert commit(store, resumed, 0, 1).committed_batch_seq == 1


def test_configuration_change_fences_claim_and_old_plan(store: DirectorStore) -> None:
    claim = begin(store)
    store.save_plan("s1", {"drafts": [[1, 2]]})
    changed = store.invalidate("s1", "new", configuration_revision=1)
    assert changed.runtime_epoch > claim.runtime_epoch
    assert store.load_plan("s1") is None
    assert store.load_plan("s1", include_stale=True) is not None
    with pytest.raises(StoreConflict, match="generation_stale"):
        commit(store, claim, 0, 1)
    with pytest.raises(StoreConflict, match="configuration_revision_stale"):
        begin(store, "r2")


def test_invalidate_all_fences_every_existing_stream(store: DirectorStore) -> None:
    first = begin(store)
    second = begin(store, stream_ref="s2")
    assert store.invalidate_all("off") == 2
    for claim in (first, second):
        with pytest.raises(StoreConflict, match="generation_stale"):
            commit(store, claim, 0, 1)


def test_presentation_ack_is_exact_subset_and_idempotent(
    store: DirectorStore, db: Database
) -> None:
    batch = commit(store, begin(store), 0, 1)
    first_key, second_key = [str(item["item_key"]) for item in batch.items]
    receipt = store.present(batch.batch_ref, "ack1", [first_key])
    assert receipt.state_revision == 1
    assert len(receipt.event_ids) == 1
    assert store.present(batch.batch_ref, "ack1", [first_key]) == receipt
    noop = store.present(batch.batch_ref, "ack2", [first_key])
    assert noop.item_keys == noop.event_ids == ()
    assert noop.state_revision == 1
    assert len(store.session("s1").real_feedback) == 1
    assert (
        db.conn.execute("SELECT COUNT(*) FROM recommendations WHERE presented=1").fetchone()[0] == 1
    )
    remaining = store.present(batch.batch_ref, "ack3", [first_key, second_key])
    assert remaining.item_keys == (second_key,)
    assert remaining.state_revision == 2
    assert len(store.session("s1").real_feedback) == 2
    with pytest.raises(StoreConflict, match="feedback_id_reused"):
        store.present(batch.batch_ref, "ack1", [second_key])


def test_wrong_batch_item_rejects_entire_ack(store: DirectorStore, db: Database) -> None:
    batch = commit(store, begin(store), 0, 1)
    with pytest.raises(StoreConflict, match="feedback_item_not_in_batch"):
        store.present(batch.batch_ref, "bad", [str(batch.items[0]["item_key"]), "bilibili:other"])
    assert store.session("s1").state_revision == 0
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_real_feedback_fences_inflight_generation_and_attributes_exact_batch(
    store: DirectorStore, db: Database
) -> None:
    batch = commit(store, begin(store), 0, 1)
    next_claim = begin(store, "r2")
    key = str(batch.items[1]["item_key"])
    receipt = store.feedback(batch.batch_ref, "like1", key, "like", "Useful")
    assert receipt.state_revision == 1
    assert store.feedback(batch.batch_ref, "like1", key, "like", "Useful") == receipt
    assert store.lookup_recommendation(int(batch.items[1]["recommendation_id"])) == (
        batch.batch_ref,
        key,
    )
    assert store.lookup_recommendation(999999) is None
    with pytest.raises(StoreConflict, match="generation_stale"):
        commit(store, next_claim, 2, 3)
    # Explicit action proves exposure even if the ACK races behind it.
    ack = store.present(batch.batch_ref, "lateack", [key])
    assert ack.event_ids == ()
    assert (
        db.conn.execute(
            "SELECT feedback_type FROM recommendations WHERE id=?",
            (batch.items[1]["recommendation_id"],),
        ).fetchone()[0]
        == "like"
    )
    assert store.session("s1").real_feedback[-1].batch_ref == batch.batch_ref


@pytest.mark.parametrize("kind", ["like", "dislike"])
def test_ten_presentation_acks_cannot_evict_new_preference_from_summary(
    store: DirectorStore, kind: str
) -> None:
    first = commit(store, begin(store), 0, 1)
    store.feedback(first.batch_ref, "preference", str(first.items[0]["item_key"]), kind)
    next_screen = commit(store, begin(store, "next", target_count=10), *range(2, 12))
    for index, item in enumerate(next_screen.items):
        store.present(next_screen.batch_ref, f"ack-{index}", [str(item["item_key"])])
    summary = store.session("s1").real_feedback
    assert [event.event_ref for event in summary] == [
        "preference",
        "ack-6",
        "ack-7",
        "ack-8",
        "ack-9",
    ]
    assert summary[0].kind == kind
    assert len(summary) <= 8


def test_stay_preserves_root_direction_excludes_chain_and_does_not_advance_step(
    store: DirectorStore,
) -> None:
    root = commit(
        store, begin(store), 0, 1, direction_ref="agent-direction", direction="Dive deeper"
    )
    claim = begin(store, "r2", intent="STAY", direction="Ignored caller direction")
    assert claim.direction_ref == root.direction_ref
    assert claim.direction == root.direction
    assert claim.excluded_item_keys == tuple(str(item["item_key"]) for item in root.items)
    patch = commit(store, claim, 2, 3)
    assert patch.execution_binding == "SESSION_STAY"
    assert patch.parent_batch_ref == root.batch_ref
    assert patch.root_batch_ref == root.batch_ref
    assert patch.committed_batch_seq == 2
    assert patch.experience_step_seq == 1
    last_claim = begin(store, "r3", intent="STAY")
    assert len(last_claim.excluded_item_keys) == 4
    with pytest.raises(StoreConflict, match="stay_direction_immutable"):
        commit(store, last_claim, 4, 5, direction_ref="new", direction="Wrong")
    last = commit(store, last_claim, 4, 5)
    assert last.patch_seq == 2
    next_batch = commit(store, begin(store, "r4"), 6, 7)
    assert next_batch.root_batch_ref == next_batch.batch_ref
    assert next_batch.experience_step_seq == 2


def test_stay_without_previous_batch_and_wrong_stream_restore_rejected(
    store: DirectorStore,
) -> None:
    with pytest.raises(StoreConflict, match="stay_without_batch"):
        begin(store, intent="STAY")
    batch = commit(store, begin(store), 0, 1)
    with pytest.raises(StoreConflict, match="batch_stream_mismatch"):
        store.restore("wrong", batch.batch_ref)
    assert store.restore("unknown") is None


def test_final_quota_exclusion_and_scope_checks(store: DirectorStore) -> None:
    claim = begin(store)
    constraints = ScreenConstraints(
        version="v1", topic_quotas=(TopicQuota(topic_group="alpha", minimum=2, maximum=2),)
    )
    with pytest.raises(StoreConflict, match="screen_quota_failed"):
        commit(store, claim, 0, 1, constraints=constraints)
    with pytest.raises(StoreConflict, match="screen_exclusion_failed"):
        commit(
            store,
            claim,
            0,
            1,
            constraints=ScreenConstraints(
                version="v1", excluded_item_keys=("bilibili:BV0000000000",)
            ),
        )
    scoped = begin(store, "r2", source_platform="xiaohongshu")
    with pytest.raises(StoreConflict, match="candidate_scope_changed"):
        commit(store, scoped, 0, 1)


@pytest.mark.parametrize("field", ["title", "body_text", "content_type", "source_metadata"])
def test_candidate_feature_change_rejects_old_evidence(
    store: DirectorStore, db: Database, field: str
) -> None:
    fingerprints = {
        str(row["item_key"]): candidate_fingerprint(dict(row))
        for row in db.conn.execute("SELECT * FROM content_cache")
    }
    claim = begin(store)
    db.conn.execute(
        f"UPDATE content_cache SET {field}=? WHERE bvid='BV0000000001'",
        ("new semantic content",),
    )
    db.conn.commit()
    with pytest.raises(StoreConflict, match="candidate_features_changed"):
        commit(store, claim, 0, 1, candidate_fingerprints=fingerprints)
    assert db.count_recommendations() == 0


def test_activate_same_context_keeps_pending_claim_and_future_horizon(store: DirectorStore) -> None:
    assert store.activate_context("cfg") == 0
    claim = begin(store)
    plan = store.save_plan("s1", {"drafts": [[2, 3]]})
    before = store.session("s1")
    assert store.activate_context("cfg") == 0
    assert store.session("s1") == before
    assert store.load_plan("s1") == plan
    assert commit(store, claim, 0, 1).committed_batch_seq == 1


def test_activate_new_context_fences_old_claims_plans_and_new_streams(store: DirectorStore) -> None:
    store.activate_context("cfg")
    claim = begin(store)
    store.save_plan("s1", {"drafts": [[2, 3]]})
    assert store.activate_context("new-config") == 1
    assert store.load_plan("s1") is None
    with pytest.raises(StoreConflict, match="generation_stale"):
        commit(store, claim, 0, 1)
    for stream in ("s1", "old-worker-new-stream"):
        with pytest.raises(StoreConflict, match="runtime_context_stale"):
            begin(store, "stale", stream_ref=stream)
    current = begin(store, "current", context_digest="new-config")
    assert commit(store, current, 0, 1).committed_batch_seq == 1


def test_begin_cannot_change_an_existing_stream_configuration(store: DirectorStore) -> None:
    claim = begin(store)
    with pytest.raises(StoreConflict, match="runtime_context_stale"):
        begin(store, "old-process-reconfigure", context_digest="other")
    assert commit(store, claim, 0, 1).committed_batch_seq == 1


def test_forced_off_fence_rejects_old_worker_on_new_stream(store: DirectorStore) -> None:
    store.invalidate_all("director-off")
    with pytest.raises(StoreConflict, match="runtime_context_stale"):
        begin(store, stream_ref="not-created-yet")


def test_committed_request_identity_ignores_execution_context_not_user_payload(
    store: DirectorStore,
) -> None:
    original = commit(store, begin(store, request_digest="same-user-payload"), 0, 1)
    store.activate_context("changed")
    assert (
        store.begin(
            "s1",
            "r1",
            context_digest="changed",
            direction_ref="d1",
            direction="Learn",
            target_count=2,
            configuration_revision=9,
            request_digest="same-user-payload",
        )
        == original
    )
    # Even the obsolete worker may replay an immutable committed result.
    assert (
        store.begin(
            "s1",
            "r1",
            context_digest="cfg",
            direction_ref="d1",
            direction="Learn",
            target_count=2,
            request_digest="same-user-payload",
        )
        == original
    )
    with pytest.raises(StoreConflict, match="request_id_reused"):
        store.begin(
            "s1",
            "r1",
            context_digest="changed",
            direction_ref="d1",
            direction="Learn",
            target_count=2,
            request_digest="changed-user-payload",
        )


@pytest.mark.parametrize("banned", ["alpha", "Content", "unrelated"])
def test_latest_dislike_read_inside_commit_validates_whole_screen(
    store: DirectorStore, db: Database, banned: str
) -> None:
    claim = begin(store)
    latest = [banned]
    if banned != "unrelated":
        with pytest.raises(StoreConflict, match="latest_dislike_changed"):
            commit(store, claim, 0, 1, latest_disliked_topics=lambda: latest)
        assert db.count_recommendations() == 0
        assert store.restore("s1") is None
        assert store.session("s1").experience_step_seq == 0
        assert (
            db.conn.execute(
                "SELECT COUNT(*) FROM content_cache WHERE pool_status='shown'"
            ).fetchone()[0]
            == 0
        )
        latest.clear()
    assert (
        commit(store, claim, 0, 1, latest_disliked_topics=lambda: latest).committed_batch_seq == 1
    )


def test_unavailable_latest_dislike_reader_fails_closed(store: DirectorStore, db: Database) -> None:
    def unavailable() -> list[str]:
        raise RuntimeError("profile store unavailable")

    with pytest.raises(StoreConflict, match="latest_dislike_unavailable"):
        commit(store, begin(store), 0, 1, latest_disliked_topics=unavailable)
    assert db.count_recommendations() == 0


def test_plan_cas_ttl_and_feedback_staleness_are_visible(
    store: DirectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = store.session("s1")
    plan = store.save_plan(
        "s1", {"drafts": [[1, 2], [3, 4]]}, expected_state_revision=initial.state_revision
    )
    assert store.load_plan("s1") == plan
    with pytest.raises(StoreConflict, match="plan_revision_stale"):
        store.save_plan("s1", {}, expected_revision=0)
    with pytest.raises(StoreConflict, match="plan_state_stale"):
        store.save_plan("s1", {}, expected_revision=1, expected_state_revision=999)
    with pytest.raises(StoreConflict, match="plan_epoch_stale"):
        store.save_plan("s1", {}, expected_revision=1, expected_runtime_epoch=999)
    updated = store.save_plan("s1", {"drafts": [[5, 6]]}, expected_revision=1)
    assert updated.revision == 2
    monkeypatch.setattr(
        "openbiliclaw.recommendation.director.store.time.time", lambda: updated.expires_at
    )
    assert store.load_plan("s1") is None
    assert store.load_plan("s1", include_stale=True) == updated


def test_plan_clear_keeps_monotonic_revision(store: DirectorStore) -> None:
    store.save_plan("s1", {"drafts": [[1, 2]]})
    store.clear_plan("s1", expected_revision=1)
    assert store.load_plan("s1") is None
    tombstone = store.load_plan("s1", include_stale=True)
    assert tombstone is not None and tombstone.revision == 2 and tombstone.plan == {}
    assert store.save_plan("s1", {"new": True}, expected_revision=2).revision == 3


def test_plan_consumption_is_atomic_with_commit(store: DirectorStore, db: Database) -> None:
    claim = begin(store)
    store.save_plan("s1", {"drafts": [[0, 1], [2, 3]]})
    db.conn.execute(
        "CREATE TRIGGER refuse_batch BEFORE INSERT ON director_batches "
        "BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        commit(
            store,
            claim,
            0,
            1,
            requested_strategy="agent_only",
            plan_id="p1",
            plan_revision=1,
            slot_seq=0,
            expected_plan_revision=1,
            plan_update={"drafts": [[2, 3]]},
        )
    assert store.load_plan("s1").revision == 1
    db.conn.execute("DROP TRIGGER refuse_batch")
    db.conn.commit()
    batch = commit(
        store,
        claim,
        0,
        1,
        requested_strategy="agent_only",
        plan_id="p1",
        plan_revision=1,
        slot_seq=0,
        expected_plan_revision=1,
        plan_update={"drafts": [[2, 3]]},
    )
    assert batch.execution_binding == "PLAN_SLOT"
    assert store.load_plan("s1").revision == 2
    assert store.load_plan("s1").plan == {"drafts": [[2, 3]]}


def test_no_fictitious_or_partial_plan_binding(store: DirectorStore) -> None:
    claim = begin(store)
    with pytest.raises(ValueError, match="partial plan"):
        commit(store, claim, 0, 1, plan_id="p")
    with pytest.raises(ValueError, match="fictitious"):
        commit(store, claim, 0, 1, plan_id="p", plan_revision=1, slot_seq=0)


def test_abandon_permits_same_payload_retry_and_rejects_changed_payload(
    store: DirectorStore,
) -> None:
    claim = begin(store)
    assert store.abandon(claim, "cancelled")
    assert not store.abandon(claim, "cancelled")
    with pytest.raises(StoreConflict, match="claim_abandoned"):
        commit(store, claim, 0, 1)
    with pytest.raises(StoreConflict, match="request_id_reused"):
        begin(store, target_count=4)
    retry = begin(store)
    assert retry.fence > claim.fence
    assert commit(store, retry, 0, 1).committed_batch_seq == 1
    assert not store.abandon(retry, "too_late")


def test_concurrent_same_claim_commit_is_exactly_once(store: DirectorStore, db: Database) -> None:
    claim = begin(store)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: commit(store, claim, 0, 1), range(2)))
    assert results[0] == results[1]
    assert db.count_recommendations() == 2
    assert store.session("s1").committed_batch_seq == 1


def test_concurrent_streams_cannot_commit_same_inventory(
    store: DirectorStore, db: Database
) -> None:
    claims = [begin(store, stream_ref=stream) for stream in ("a", "b")]

    def attempt(claim: GenerationClaim) -> BatchInstance | StoreConflict:
        try:
            return commit(store, claim, 0, 1)
        except StoreConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, claims))
    assert sum(isinstance(value, BatchInstance) for value in results) == 1
    assert sum(isinstance(value, StoreConflict) for value in results) == 1
    assert db.count_recommendations() == 2


def test_returned_mutable_json_cannot_mutate_persisted_batch_or_plan(store: DirectorStore) -> None:
    batch = commit(store, begin(store), 0, 1)
    batch.items[0]["title"] = "tampered"
    assert store.restore("s1").items[0]["title"] != "tampered"
    plan = store.save_plan("s1", {"nested": [1, 2]})
    plan.plan["nested"] = "tampered"
    assert store.load_plan("s1").plan["nested"] == [1, 2]


def test_caller_request_digest_binds_excluded_or_explicit_intent(store: DirectorStore) -> None:
    begin(store, request_digest="exclusions-one")
    with pytest.raises(StoreConflict, match="request_id_reused"):
        begin(store, request_digest="exclusions-two")


def test_commit_can_create_plan_or_leave_no_plan_tombstone(store: DirectorStore) -> None:
    batch = commit(store, begin(store), 0, 1, expected_plan_revision=0, plan_update={})
    assert batch.plan_id is None
    assert store.load_plan("s1", include_stale=True) is None
    claim = begin(store, "r2")
    commit(store, claim, 2, 3, expected_plan_revision=0, plan_update={"drafts": [[4, 5]]})
    saved = store.load_plan("s1")
    assert saved is not None and saved.revision == 1
    commit(store, begin(store, "r3"), 4, 5, expected_plan_revision=1, plan_update={})
    assert store.load_plan("s1") is None
    assert store.load_plan("s1", include_stale=True).revision == 2


def test_consumed_new_plan_expiry_is_not_extended(store: DirectorStore) -> None:
    claim = begin(store)
    expiry = claim.expires_at - 1
    commit(
        store,
        claim,
        0,
        1,
        expected_plan_revision=0,
        plan_update={"expires_at": expiry, "drafts": [[2, 3]]},
    )
    assert store.load_plan("s1").expires_at == expiry


def test_fresh_seen_gate_ignores_process_local_seen_snapshot_cache(
    store: DirectorStore, db: Database
) -> None:
    claim = begin(store)
    # Prime the process-local cache at latest event ID=0.
    assert not db.get_seen_content_keys()
    connection = db.open_connection()
    try:
        connection.execute(
            "INSERT INTO seen_items VALUES (?, 'bilibili', ?, 0, 0, CURRENT_TIMESTAMP, "
            "CURRENT_TIMESTAMP)",
            ("bilibili:BV0000000000", "BV0000000000"),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(StoreConflict, match="candidate_ineligible"):
        commit(store, claim, 0, 1)
    assert db.count_recommendations() == 0


@pytest.mark.parametrize(
    "kind,stored_kind",
    [
        ("like", "like"),
        ("dislike", "dislike"),
        ("skip", "dismiss"),
        ("explicit_request", "comment"),
    ],
)
def test_ingress_feedback_projection_does_not_duplicate_learning_event(
    store: DirectorStore, db: Database, kind: str, stored_kind: str
) -> None:
    batch = commit(store, begin(store), 0, 1)
    item = batch.items[0]
    event_id = db.insert_event(
        "feedback",
        metadata={
            "recommendation_id": item["recommendation_id"],
            "bvid": item["bvid"],
            "feedback_type": stored_kind,
            "feedback_note": "User note",
        },
    )
    db.update_recommendation_feedback(
        int(item["recommendation_id"]), feedback_type=stored_kind, feedback_note="User note"
    )
    receipt = store.feedback(
        batch.batch_ref,
        "project",
        str(item["item_key"]),
        kind,  # type: ignore[arg-type]
        "User note",
        existing_event_id=event_id,
    )
    assert receipt.event_ids == (event_id,)
    assert receipt.state_revision == 1
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert (
        db.conn.execute(
            "SELECT feedback_type FROM recommendations WHERE id=?", (item["recommendation_id"],)
        ).fetchone()[0]
        == stored_kind
    )
    # Even a caller with a different projection ID cannot apply one event twice.
    assert (
        store.feedback(
            batch.batch_ref,
            "project-again",
            str(item["item_key"]),
            kind,  # type: ignore[arg-type]
            "User note",
            existing_event_id=event_id,
        )
        == receipt
    )
    assert store.session("s1").state_revision == 1


@pytest.mark.parametrize("mismatch", ["id", "kind", "note", "bvid", "key", "missing", "event_type"])
def test_ingress_projection_validates_persisted_attribution(
    store: DirectorStore, db: Database, mismatch: str
) -> None:
    batch = commit(store, begin(store), 0, 1)
    item = batch.items[0]
    metadata = {
        "recommendation_id": item["recommendation_id"],
        "bvid": item["bvid"],
        "feedback_type": "like",
        "feedback_note": "note",
    }
    if mismatch == "id":
        metadata["recommendation_id"] = 999
    elif mismatch == "kind":
        metadata["feedback_type"] = "dislike"
    elif mismatch == "note":
        metadata["feedback_note"] = "other"
    elif mismatch == "bvid":
        metadata["bvid"] = "other"
    elif mismatch == "key":
        metadata["item_key"] = "other"
    event_id = db.insert_event(
        "click" if mismatch == "event_type" else "feedback", metadata=metadata
    )
    with pytest.raises(StoreConflict, match="feedback_event_"):
        store.feedback(
            batch.batch_ref,
            "project",
            str(item["item_key"]),
            "like",
            "note",
            existing_event_id=9999 if mismatch == "missing" else event_id,
        )
    assert store.session("s1").state_revision == 0


def test_delayed_projection_cannot_regress_feedback_cursor(
    store: DirectorStore, db: Database
) -> None:
    batch = commit(store, begin(store), 0, 1)
    item = batch.items[0]
    event_id = db.insert_event(
        "feedback",
        metadata={
            "recommendation_id": item["recommendation_id"],
            "feedback_type": "like",
            "feedback_note": "",
        },
    )
    ack = store.present(batch.batch_ref, "ack", [str(batch.items[1]["item_key"])])
    assert int(ack.feedback_cursor) > event_id
    projected = store.feedback(
        batch.batch_ref, "later", str(item["item_key"]), "like", existing_event_id=event_id
    )
    assert projected.feedback_cursor == ack.feedback_cursor


def test_real_click_projects_existing_event_and_fences_without_inventing_like(
    store: DirectorStore, db: Database
) -> None:
    batch = commit(store, begin(store), 0, 1)
    inflight = begin(store, "next")
    item = batch.items[0]
    event_id = db.insert_event(
        "click", metadata={"recommendation_id": item["recommendation_id"], "bvid": item["bvid"]}
    )
    receipt = store.feedback(
        batch.batch_ref, "clicked", str(item["item_key"]), "click", existing_event_id=event_id
    )
    assert receipt.state_revision == 1
    assert receipt.event_ids == (event_id,)
    assert len(db.get_recent_events()) == 1
    assert store.session("s1").real_feedback[-1].kind == "click"
    assert (
        store.feedback(
            batch.batch_ref, "retry", str(item["item_key"]), "click", existing_event_id=event_id
        )
        == receipt
    )
    assert (
        db.conn.execute(
            "SELECT feedback_type FROM recommendations WHERE id=?", (item["recommendation_id"],)
        ).fetchone()[0]
        is None
    )
    with pytest.raises(StoreConflict, match="generation_stale"):
        commit(store, inflight, 2, 3)
    assert store.present(batch.batch_ref, "lateack", [str(item["item_key"])]).event_ids == ()


def test_click_projection_rejects_feedback_event_even_with_matching_id(
    store: DirectorStore, db: Database
) -> None:
    batch = commit(store, begin(store), 0, 1)
    item = batch.items[0]
    event_id = db.insert_event(
        "feedback",
        metadata={"recommendation_id": item["recommendation_id"], "feedback_type": "like"},
    )
    with pytest.raises(StoreConflict, match="feedback_event_not_found"):
        store.feedback(
            batch.batch_ref,
            "wrong-type",
            str(item["item_key"]),
            "click",
            existing_event_id=event_id,
        )
    assert store.session("s1").state_revision == 0
