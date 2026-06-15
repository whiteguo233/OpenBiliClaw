"""Tests for the Bilibili extension search task queue."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.sources.bili_tasks import (
    BiliTaskQueue,
    bili_search_video_key,
    source_keyword_id_from_bili_task,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "openbiliclaw.db")
    db.initialize()
    return db


def test_bili_task_queue_claims_oldest_pending_task(database: Database) -> None:
    queue = BiliTaskQueue(database)
    first_id = queue.enqueue_with_id("search", {"query": "键盘"}, daily_budget=0)
    second_id = queue.enqueue_with_id("search", {"query": "咖啡"}, daily_budget=0)

    task = queue.next_pending()

    assert task is not None
    assert task["id"] == first_id
    assert task["status"] == "in_progress"
    assert json.loads(str(task["payload_json"])) == {"query": "键盘"}
    assert queue.next_pending() is not None
    assert queue.next_pending() is None
    assert second_id is not None


def test_bili_task_queue_respects_daily_budget(database: Database) -> None:
    queue = BiliTaskQueue(database)

    assert queue.enqueue_with_id("search", {"query": "一"}, daily_budget=2)
    assert queue.enqueue_with_id("search", {"query": "二"}, daily_budget=2)
    assert queue.enqueue_with_id("search", {"query": "三"}, daily_budget=2) is None


def test_bili_task_queue_zero_daily_budget_disables_cap(database: Database) -> None:
    queue = BiliTaskQueue(database)

    for i in range(5):
        assert queue.enqueue_with_id("search", {"query": f"kw-{i}"}, daily_budget=0)


def test_bili_task_queue_merge_result_dedupes_videos(database: Database) -> None:
    queue = BiliTaskQueue(database)
    task_id = queue.enqueue_with_id("search", {"query": "键盘"}, daily_budget=0)
    assert task_id is not None

    added_first = queue.merge_result(
        task_id,
        videos=[
            {"bvid": "BV1", "title": "第一条", "url": "https://www.bilibili.com/video/BV1"},
            {"bvid": "BV2", "title": "第二条"},
        ],
        complete=False,
    )
    added_second = queue.merge_result(
        task_id,
        videos=[
            {"bvid": "BV1", "title": "重复条"},
            {"content_id": "BV3", "title": "第三条"},
        ],
        complete=True,
    )

    assert [item["bvid"] for item in added_first] == ["BV1", "BV2"]
    assert [item.get("content_id") or item.get("bvid") for item in added_second] == ["BV3"]
    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == "completed"
    payload = json.loads(str(row["result_json"]))
    assert [item.get("bvid") or item.get("content_id") for item in payload["videos"]] == [
        "BV1",
        "BV2",
        "BV3",
    ]


def test_bili_task_queue_fail_persists_error(database: Database) -> None:
    queue = BiliTaskQueue(database)
    task_id = queue.enqueue_with_id("search", {"query": "键盘"}, daily_budget=0)
    assert task_id is not None

    queue.fail(task_id, error="search_page_failed", debug={"stage": "dom"})

    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == "failed"
    payload = json.loads(str(row["result_json"]))
    assert payload["error"] == "search_page_failed"
    assert payload["debug"] == {"stage": "dom"}


def test_bili_search_video_key_uses_scope_free_video_identity() -> None:
    assert bili_search_video_key({"bvid": "BV1", "title": "t"}) == "BV1"
    assert bili_search_video_key({"content_id": "BV2", "title": "t"}) == "BV2"
    assert (
        bili_search_video_key({"url": "https://www.bilibili.com/video/BV3", "title": "t"})
        == "https://www.bilibili.com/video/BV3"
    )
    assert bili_search_video_key({"title": "fallback"}) == "fallback"
    assert bili_search_video_key({"title": ""}) == ""


def test_source_keyword_id_from_bili_task_tolerates_missing_or_malformed_payload() -> None:
    assert source_keyword_id_from_bili_task(None) is None
    assert source_keyword_id_from_bili_task("{bad json") is None
    assert source_keyword_id_from_bili_task(json.dumps({"query": "kw"})) is None
    assert source_keyword_id_from_bili_task(json.dumps({"source_keyword_id": "42"})) == 42
