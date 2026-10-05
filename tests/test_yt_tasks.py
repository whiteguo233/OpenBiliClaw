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


def test_yt_task_queue_expire_stale_in_progress_fails_stale_leases(
    database: Database,
) -> None:
    import json
    from datetime import UTC, datetime, timedelta

    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None

    claimed = queue.next_pending()
    assert claimed is not None and claimed["status"] == "in_progress"

    stale_text = (datetime.now(UTC) - timedelta(seconds=900)).strftime("%Y-%m-%d %H:%M:%S")
    database.conn.execute(
        "UPDATE yt_tasks SET claimed_at = ? WHERE id = ?",
        (stale_text, task_id),
    )
    database.conn.commit()

    recovered = queue.expire_stale_in_progress(
        ("bootstrap_profile",),
        older_than_seconds=600,
    )

    assert recovered == 1
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "failed"
    assert json.loads(str(task["result_json"]))["error"] == "stale_in_progress"
    assert task["completed_at"]

    next_task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_likes"]})
    assert next_task_id is not None
    next_claimed = queue.next_pending()
    assert next_claimed is not None
    assert next_claimed["id"] == next_task_id


def test_yt_task_queue_expire_stale_in_progress_ignores_recent_claims(
    database: Database,
) -> None:
    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None
    assert queue.next_pending() is not None

    recovered = queue.expire_stale_in_progress(
        ("bootstrap_profile",),
        older_than_seconds=600,
    )

    assert recovered == 0
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "in_progress"


def test_yt_task_queue_next_pending_reclaims_in_progress_with_null_claimed_at(
    database: Database,
) -> None:
    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None
    assert queue.next_pending() is not None

    database.conn.execute(
        "UPDATE yt_tasks SET claimed_at = NULL WHERE id = ?",
        (task_id,),
    )
    database.conn.commit()

    reclaimed = queue.next_pending()
    assert reclaimed is not None
    assert reclaimed["id"] == task_id
    assert reclaimed["status"] == "in_progress"


def test_yt_task_queue_expire_stale_in_progress_preserves_staged_results(
    database: Database,
) -> None:
    from datetime import UTC, datetime, timedelta

    queue = YtTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["yt_history"]})
    assert task_id is not None
    assert queue.next_pending() is not None

    queue.stage_final_result(
        task_id,
        terminal_status="ok",
        items=[{"scope": "yt_history", "title": "Kept"}],
    )
    stale_text = (datetime.now(UTC) - timedelta(seconds=900)).strftime("%Y-%m-%d %H:%M:%S")
    database.conn.execute(
        "UPDATE yt_tasks SET claimed_at = ? WHERE id = ?",
        (stale_text, task_id),
    )
    database.conn.commit()

    recovered = queue.expire_stale_in_progress(
        ("bootstrap_profile",),
        older_than_seconds=600,
    )

    assert recovered == 0
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "in_progress"
    assert "_openbiliclaw_terminal_status" in str(task["result_json"])
