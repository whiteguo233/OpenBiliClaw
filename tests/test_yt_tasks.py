"""Tests for YouTube bootstrap task queue helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.sources.yt_tasks import YtTaskQueue, yt_bootstrap_items_to_events
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


def test_yt_bootstrap_items_to_events_maps_scopes() -> None:
    events = yt_bootstrap_items_to_events(
        [
            {
                "scope": "yt_history",
                "title": "History Video",
                "url": "https://www.youtube.com/watch?v=h1",
                "video_id": "h1",
            },
            {
                "scope": "yt_subscriptions",
                "title": "Channel Name",
                "url": "https://www.youtube.com/@channel",
                "channel_id": "c1",
            },
            {
                "scope": "yt_likes",
                "title": "Liked Video",
                "url": "https://www.youtube.com/watch?v=l1",
                "video_id": "l1",
            },
        ]
    )

    assert [event["event_type"] for event in events] == ["view", "follow", "like"]
    assert [event["metadata"]["import_source"] for event in events] == [
        "yt_bootstrap_history",
        "yt_bootstrap_subscriptions",
        "yt_bootstrap_likes",
    ]


def test_yt_task_queue_claims_pending_task_until_terminal_status(
    database: Database,
) -> None:
    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None

    first = queue.next_pending()

    assert first is not None
    assert first["id"] == task_id
    assert first["status"] == "in_progress"
    assert queue.next_pending() is None

    queue.merge_result(task_id, items=[], complete=True)
    assert queue.next_pending() is None


def test_yt_task_queue_finds_recent_bootstrap_task(database: Database) -> None:
    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None

    recent = queue.find_recent_task("bootstrap_profile", recent_hours=6)

    assert recent is not None
    assert recent["id"] == task_id
