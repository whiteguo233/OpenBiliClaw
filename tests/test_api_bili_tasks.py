"""Tests for the Bilibili extension-search task API endpoints."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.storage.database import Database


class RecordingRuntimeController:
    def __init__(self) -> None:
        self.memory_manager = SimpleNamespace(load_discovery_runtime_state=lambda: {})
        self.drain_calls: list[dict[str, object]] = []

    async def drain_discovery_candidates_once(self, **kwargs: object) -> dict[str, int]:
        self.drain_calls.append(dict(kwargs))
        return {"evaluated": 1, "cached": 1, "rejected": 0}


class RecordingEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


class ReadySoulEngine:
    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def bili_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub]:
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "bili-task.db")
    db.initialize()
    runtime = RecordingRuntimeController()
    hub = RecordingEventHub()

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            bilibili=SimpleNamespace(enabled=True),
            xiaohongshu=SimpleNamespace(
                enabled=False,
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
            douyin=SimpleNamespace(enabled=False),
            youtube=SimpleNamespace(enabled=False),
            twitter=SimpleNamespace(enabled=False),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    from openbiliclaw.api.app import create_app

    app = create_app(
        database=db,
        memory_manager=runtime.memory_manager,
        soul_engine=ReadySoulEngine(),
        runtime_controller=runtime,
        runtime_event_hub=hub,
        recommendation_engine=None,
    )
    return TestClient(app), db, runtime, hub


def _enqueue_bili_search_task(
    db: Database,
    payload: dict[str, object] | None = None,
) -> str:
    from openbiliclaw.sources.bili_tasks import BiliTaskQueue

    task_id = BiliTaskQueue(db).enqueue_with_id(
        "search",
        payload or {"query": "机械键盘", "limit": 10},
        daily_budget=10,
    )
    assert task_id is not None
    return task_id


def test_bili_next_task_returns_204_when_empty(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, _db, _runtime, _hub = bili_task_client

    response = client.get("/api/sources/bili/next-task")

    assert response.status_code == 204


def test_bili_next_task_returns_pending_task_payload(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, db, _runtime, _hub = bili_task_client
    task_id = _enqueue_bili_search_task(db)

    response = client.get("/api/sources/bili/next-task")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["type"] == "search"
    assert body["query"] == "机械键盘"
    assert body["limit"] == 10


def test_bili_task_result_rejects_missing_task_id(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, _db, _runtime, _hub = bili_task_client

    response = client.post("/api/sources/bili/task-result", json={"status": "ok"})

    assert response.status_code == 422


def test_bili_task_result_enqueues_videos_into_discovery_candidates(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db, runtime, _hub = bili_task_client
    task_id = _enqueue_bili_search_task(db, {"query": "机械键盘", "source_keyword_id": 7})
    scheduled: list[Any] = []

    def _capture_task(coro: Any) -> object:
        scheduled.append(coro)
        return SimpleNamespace()

    monkeypatch.setattr("asyncio.create_task", _capture_task)

    response = client.post(
        "/api/sources/bili/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "videos": [
                {
                    "bvid": "BV1abc",
                    "title": "客制化机械键盘入门",
                    "up_name": "键盘研究所",
                    "url": "https://www.bilibili.com/video/BV1abc",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/demo.jpg",
                    "duration": 321,
                    "view_count": 1234,
                    "like_count": 88,
                    "favorite_count": 77,
                    "danmaku_count": 66,
                    "comment_count": 55,
                    "share_count": 44,
                    "description": "轴体与配列",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "enqueued": 1}
    row = db.conn.execute(
        "SELECT * FROM discovery_candidates WHERE candidate_key = 'bilibili:BV1abc'"
    ).fetchone()
    assert row is not None
    assert row["source_platform"] == "bilibili"
    assert row["source_strategy"] == "bili-extension-search"
    assert row["source_context"] == "bili-extension-search"
    assert row["bvid"] == "BV1abc"
    assert row["content_id"] == "BV1abc"
    assert row["content_url"] == "https://www.bilibili.com/video/BV1abc"
    assert row["title"] == "客制化机械键盘入门"
    assert row["up_name"] == "键盘研究所"
    assert row["author_name"] == "键盘研究所"
    assert row["duration"] == 321
    assert row["view_count"] == 1234
    assert row["like_count"] == 88
    assert row["favorite_count"] == 77
    assert row["danmaku_count"] == 66
    assert row["comment_count"] == 55
    assert row["share_count"] == 44
    assert row["score_threshold"] == 0.65
    assert row["source_keyword_id"] == 7
    raw_payload = json.loads(str(row["raw_payload"]))
    assert raw_payload["query"] == "机械键盘"
    assert scheduled
    for coro in scheduled:
        asyncio.run(coro)
    assert runtime.drain_calls == [{"batch_size": 30}]


def test_bili_task_result_marks_keyword_used_on_terminal_ok(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, db, _runtime, _hub = bili_task_client
    db.insert_pending_keywords("bilibili", ["机械键盘"], "digest")
    claimed = db.claim_keywords("bilibili", 1)[0]
    db.mark_keyword_executing(int(claimed["id"]))
    task_id = _enqueue_bili_search_task(
        db,
        {"query": "机械键盘", "source_keyword_id": int(claimed["id"])},
    )

    response = client.post(
        "/api/sources/bili/task-result",
        json={"task_id": task_id, "status": "ok", "videos": []},
    )

    assert response.status_code == 200
    status = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE id = ?",
        (int(claimed["id"]),),
    ).fetchone()["status"]
    assert status == "used"


def test_bili_task_result_marks_failed_and_keyword_failed(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, db, _runtime, _hub = bili_task_client
    db.insert_pending_keywords("bilibili", ["机械键盘"], "digest")
    claimed = db.claim_keywords("bilibili", 1)[0]
    db.mark_keyword_executing(int(claimed["id"]))
    task_id = _enqueue_bili_search_task(
        db,
        {"query": "机械键盘", "source_keyword_id": int(claimed["id"])},
    )

    response = client.post(
        "/api/sources/bili/task-result",
        json={
            "task_id": task_id,
            "status": "failed",
            "error": "search_page_failed",
            "debug": {"stage": "dom"},
        },
    )

    assert response.status_code == 200
    task = db.conn.execute("SELECT status, result_json FROM bili_tasks WHERE id = ?", (task_id,))
    row = task.fetchone()
    assert row["status"] == "failed"
    payload = json.loads(str(row["result_json"]))
    assert payload["error"] == "search_page_failed"
    keyword = db.conn.execute(
        "SELECT status, attempts FROM discovery_keywords WHERE id = ?",
        (int(claimed["id"]),),
    ).fetchone()
    assert keyword["status"] == "failed"
    assert keyword["attempts"] == 1


def test_bili_kick_broadcasts_task_available(
    bili_task_client: tuple[TestClient, Database, RecordingRuntimeController, RecordingEventHub],
) -> None:
    client, _db, _runtime, hub = bili_task_client

    response = client.post("/api/sources/bili/kick")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert hub.events == [{"type": "bili_task_available", "source": "task_kick"}]
