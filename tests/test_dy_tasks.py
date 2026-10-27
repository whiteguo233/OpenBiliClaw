"""Tests for the Douyin bootstrap event-conversion helper.

Task 1 of the Douyin bootstrap import plan
(``docs/plans/2026-05-06-douyin-bootstrap-import.md``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.sources import dy_tasks
from openbiliclaw.sources.dy_tasks import DyTaskQueue, dy_bootstrap_videos_to_events
from openbiliclaw.sources.event_format import SOURCE_DOUYIN
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "openbiliclaw.db")
    db.initialize()
    return db


def test_recent_hot_choices_are_durable_scoped_and_expire(database: Database) -> None:
    queue = DyTaskQueue(database)
    payload = {"hot_items": [{"sentence_id": "one"}, {"sentence_id": "one"}, "malformed"]}
    hot = queue.enqueue_with_id("hot", payload)
    queue.enqueue_with_id("search", {"hot_items": [{"sentence_id": "not-hot"}]})
    assert DyTaskQueue(database).recent_hot_sentence_ids(within_seconds=6 * 3600) == ("one",)
    assert queue.recent_hot_sentence_ids(within_seconds=0) == ()
    database.conn.execute(
        "UPDATE dy_tasks SET created_at = datetime('now', '-7 hours') WHERE id = ?", (hot,)
    )
    database.conn.commit()
    assert queue.recent_hot_sentence_ids(within_seconds=6 * 3600) == ()


def test_discovery_failure_holds_queued_discovery_but_not_explicit_bootstrap(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    failed = queue.enqueue_with_id("hot", {"hot_items": [{"sentence_id": "one"}]})
    pending = queue.enqueue_with_id("search", {"keywords": ["next"]})
    bootstrap = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["dy_like"]})
    assert queue.next_pending()["id"] == failed
    queue.fail(failed, error="api_rate_limited")

    assert queue.next_pending()["id"] == bootstrap
    assert queue.get(pending)["status"] == "pending"


def test_claimed_discovery_failure_blocks_new_tasks_across_database_instances(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    failed = queue.enqueue_with_id("hot", {"hot_items": [{"sentence_id": "one"}]})
    assert queue.next_pending()["id"] == failed
    queue.fail(failed, error="api_timeout")
    reader = Database(database._db_path)
    reader.initialize()
    try:
        with pytest.raises(RuntimeError, match="cooldown"):
            DyTaskQueue(reader).enqueue_with_id("feed", {"max_items": 10})
    finally:
        reader.close()


def test_discovery_can_resume_after_the_durable_cooldown_expires(database: Database) -> None:
    queue = DyTaskQueue(database)
    failed = queue.enqueue_with_id("hot", {"hot_items": [{"sentence_id": "one"}]})
    assert queue.next_pending()["id"] == failed
    queue.fail(failed, error="api_rate_limited")
    assert queue.discovery_retry_at() is not None
    # Fixture time travel only: do not wait thirty minutes in the unit suite.
    database.conn.execute(
        "UPDATE dy_tasks SET completed_at = datetime('now', '-7 hours') WHERE id = ?", (failed,)
    )
    database.conn.commit()
    assert queue.discovery_retry_at() is None
    resumed = queue.enqueue_with_id("feed", {"max_items": 6})
    assert queue.next_pending()["id"] == resumed


def test_dy_bootstrap_videos_to_events_maps_scopes_to_event_types() -> None:
    """Each Douyin scope maps to its canonical event_type."""
    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_post",
                "title": "我发布的",
                "url": "https://www.douyin.com/video/aaa",
                "aweme_id": "aaa",
            },
            {
                "scope": "dy_collect",
                "title": "我收藏的",
                "url": "https://www.douyin.com/video/bbb",
                "aweme_id": "bbb",
            },
            {
                "scope": "dy_like",
                "title": "我点赞的",
                "url": "https://www.douyin.com/video/ccc",
                "aweme_id": "ccc",
            },
            {
                "scope": "dy_follow",
                "title": "关注的创作者",
                "url": "https://www.douyin.com/user/ddd",
                "creator_sec_uid": "ddd",
            },
        ]
    )

    assert [e["event_type"] for e in events] == ["view", "favorite", "like", "follow"]
    for event in events:
        assert event["metadata"]["source_platform"] == SOURCE_DOUYIN


def test_dy_bootstrap_videos_to_events_signal_strength_per_scope() -> None:
    """Signal strengths follow the design doc: collect=1.0, like=0.85, follow=0.6, post=0.4."""
    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_post",
                "title": "p",
                "url": "https://www.douyin.com/video/p",
                "aweme_id": "p",
            },
            {
                "scope": "dy_collect",
                "title": "c",
                "url": "https://www.douyin.com/video/c",
                "aweme_id": "c",
            },
            {
                "scope": "dy_like",
                "title": "l",
                "url": "https://www.douyin.com/video/l",
                "aweme_id": "l",
            },
            {
                "scope": "dy_follow",
                "title": "f",
                "url": "https://www.douyin.com/user/f",
                "creator_sec_uid": "f",
            },
        ]
    )
    strengths = [e["metadata"]["signal_strength"] for e in events]
    assert strengths == [0.4, 1.0, 0.85, 0.6]


def test_dy_bootstrap_videos_to_events_skips_blank_and_unknown() -> None:
    """Items missing both title and url are dropped; unknown scopes are dropped."""
    events = dy_bootstrap_videos_to_events(
        [
            {"scope": "dy_collect", "title": "", "url": "", "aweme_id": "x"},  # blank — drop
            {"scope": "dy_unknown_scope", "title": "t", "url": "u"},  # unknown scope — drop
            "not-a-dict",  # invalid type — drop
            {
                "scope": "dy_collect",
                "title": "valid",
                "url": "https://www.douyin.com/video/v",
                "aweme_id": "v",
            },
        ]
    )
    assert len(events) == 1
    assert events[0]["title"] == "valid"


def test_dy_bootstrap_videos_to_events_import_source_tag() -> None:
    """import_source metadata is namespaced per scope so analytics can split."""
    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_collect",
                "title": "c",
                "url": "https://www.douyin.com/video/c",
                "aweme_id": "c",
            },
        ]
    )
    assert events[0]["metadata"]["import_source"] == "dy_bootstrap_collect"


def test_dy_bootstrap_videos_to_events_passes_through_aweme_metadata() -> None:
    """aweme_id, author, cover_url propagate so downstream curator can dedupe / render."""
    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_collect",
                "title": "demo title",
                "url": "https://www.douyin.com/video/zzz",
                "aweme_id": "zzz",
                "author": "作者昵称",
                "author_sec_uid": "sec_uid_xyz",
                "cover_url": "https://example.com/cover.jpg",
            },
        ]
    )
    metadata = events[0]["metadata"]
    assert metadata["aweme_id"] == "zzz"
    assert metadata["cover_url"] == "https://example.com/cover.jpg"
    # build_event places author inside metadata (see event_format.py:200).
    assert metadata["author"] == "作者昵称"


def test_dy_bootstrap_videos_to_events_follow_uses_creator_sec_uid() -> None:
    """For dy_follow scope the metadata key is creator_sec_uid, not aweme_id."""
    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_follow",
                "title": "@老白",
                "url": "https://www.douyin.com/user/abc",
                "creator_sec_uid": "abc",
            },
        ]
    )
    metadata = events[0]["metadata"]
    assert metadata["creator_sec_uid"] == "abc"
    assert events[0]["event_type"] == "follow"


def test_dy_result_merge_preserves_existing_semantic_status() -> None:
    """A metadata/count merge must not silently drop result_json.status."""
    merged, added = dy_tasks._merge_dy_result_payload(
        {
            "status": "degraded",
            "videos": [{"scope": "dy_collect", "aweme_id": "first"}],
            "scope_counts": {"dy_collect": 1},
        },
        scope_counts={"dy_collect": 1},
        debug={"retry": "late"},
    )

    assert added == []
    assert merged["status"] == "degraded"
    assert merged["debug"] == {"retry": "late"}


def test_dy_task_queue_ignores_stale_pending_failures_for_daily_budget(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)

    stale_id = queue.enqueue_with_id("search", {"keywords": ["旧任务"]}, daily_budget=2)
    assert stale_id is not None
    queue.fail(stale_id, error="stale_pending")
    assert queue.enqueue_with_id("search", {"keywords": ["有效任务"]}, daily_budget=2)

    assert queue.enqueue_with_id("search", {"keywords": ["补池任务"]}, daily_budget=2)


def test_dy_task_queue_counts_non_stale_failures_for_daily_budget(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)

    failed_id = queue.enqueue_with_id("search", {"keywords": ["超时任务"]}, daily_budget=2)
    assert failed_id is not None
    queue.fail(failed_id, error="task_timeout")
    assert queue.enqueue_with_id("search", {"keywords": ["有效任务"]}, daily_budget=2)

    assert queue.enqueue_with_id("search", {"keywords": ["第三个任务"]}, daily_budget=2) is None


def test_dy_task_queue_zero_daily_budget_disables_daily_cap(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)

    for i in range(5):
        assert queue.enqueue_with_id("search", {"keywords": [f"任务 {i}"]}, daily_budget=0)


def test_dy_task_queue_claims_pending_task_until_terminal_status(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["dy_collect"]})
    assert task_id is not None

    first = queue.next_pending()

    assert first is not None
    assert first["id"] == task_id
    assert first["status"] == "in_progress"
    assert queue.next_pending() is None

    queue.merge_result(task_id, videos=[], complete=True)
    assert queue.next_pending() is None


def test_dy_task_queue_does_not_claim_second_task_while_first_lease_is_active(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    first_id = queue.enqueue_with_id("feed", {"max_items": 3})
    second_id = queue.enqueue_with_id("search", {"keywords": ["人工智能"]})
    assert first_id is not None
    assert second_id is not None

    first = queue.next_pending()
    assert first is not None
    assert first["id"] == first_id
    assert queue.next_pending() is None

    database.conn.execute(
        "UPDATE dy_tasks SET claimed_at = datetime('now', '-16 minutes') WHERE id = ?",
        (first_id,),
    )
    database.conn.commit()
    reclaimed = queue.next_pending()
    assert reclaimed is not None
    assert reclaimed["id"] == first_id
    assert reclaimed["status"] == "in_progress"


def test_dy_task_queue_finds_recent_bootstrap_task(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["dy_collect"]})
    assert task_id is not None

    recent = queue.find_recent_task("bootstrap_profile", recent_hours=6)

    assert recent is not None
    assert recent["id"] == task_id
