"""Tests for the Douyin bootstrap event-conversion helper.

Task 1 of the Douyin bootstrap import plan
(``docs/plans/2026-05-06-douyin-bootstrap-import.md``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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


def test_dy_task_queue_finds_recent_bootstrap_task(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["dy_collect"]})
    assert task_id is not None

    recent = queue.find_recent_task("bootstrap_profile", recent_hours=6)

    assert recent is not None
    assert recent["id"] == task_id
