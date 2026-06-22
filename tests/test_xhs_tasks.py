"""Tests for xhs task queue, creator subscriptions, and API endpoints.

The task queue is the backend side of the extension's background
dispatcher: the backend enqueues search/creator tasks, the extension
polls for the next pending one, executes it (no-scroll), and posts the
result back.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.sources.xhs_tasks import (
    XhsCreatorStore,
    XhsTaskQueue,
    xhs_bootstrap_notes_to_events,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


@pytest.fixture
def queue(db: Database) -> XhsTaskQueue:
    return XhsTaskQueue(db)


@pytest.fixture
def creator_store(db: Database) -> XhsCreatorStore:
    return XhsCreatorStore(db)


class TestXhsTaskQueue:
    def test_enqueue_and_next(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "机械键盘"})
        task = queue.next_pending()

        assert task is not None
        assert task["type"] == "search"
        payload = json.loads(task["payload_json"])
        assert payload["keyword"] == "机械键盘"
        assert task["status"] == "in_progress"

    def test_next_pending_claims_task_until_terminal_status(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("bootstrap_profile", {"scopes": ["saved", "liked"]})

        first = queue.next_pending()
        assert first is not None
        assert first["status"] == "in_progress"

        assert queue.next_pending() is None

        queue.complete(first["id"], urls=[])
        assert queue.next_pending() is None

    def test_next_returns_none_when_empty(self, queue: XhsTaskQueue) -> None:
        assert queue.next_pending() is None

    def test_next_returns_oldest_first(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "first"})
        queue.enqueue("search", {"keyword": "second"})

        task = queue.next_pending()
        assert task is not None
        payload = json.loads(task["payload_json"])
        assert payload["keyword"] == "first"

    def test_complete_marks_task_done(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "x"})
        task = queue.next_pending()
        assert task is not None

        queue.complete(task["id"], urls=["https://www.xiaohongshu.com/explore/abc"])

        # Should not return completed tasks
        assert queue.next_pending() is None

    def test_fail_marks_task_failed(self, queue: XhsTaskQueue) -> None:
        queue.enqueue("search", {"keyword": "x"})
        task = queue.next_pending()
        assert task is not None

        queue.fail(task["id"], error="timeout")

        assert queue.next_pending() is None

    def test_daily_budget_enforced(self, queue: XhsTaskQueue) -> None:
        budget = 3
        for i in range(budget):
            assert queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=budget)

        # Next enqueue should be rejected
        assert not queue.enqueue("search", {"keyword": "over"}, daily_budget=budget)

    def test_zero_daily_budget_disables_daily_cap(self, queue: XhsTaskQueue) -> None:
        for i in range(5):
            assert queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=0)

    def test_creator_tasks_have_separate_budget(self, queue: XhsTaskQueue) -> None:
        # Fill search budget
        for i in range(3):
            queue.enqueue("search", {"keyword": f"k{i}"}, daily_budget=3)

        # Creator budget should still be available
        assert queue.enqueue("creator", {"creator_url": "https://xhs.com/u/1"}, daily_budget=3)


def test_xhs_bootstrap_notes_to_events_maps_scopes() -> None:
    events = xhs_bootstrap_notes_to_events(
        [
            {
                "scope": "saved",
                "title": "收藏笔记",
                "url": "https://www.xiaohongshu.com/explore/a",
                "note_id": "a",
            },
            {
                "scope": "liked",
                "title": "点赞笔记",
                "url": "https://www.xiaohongshu.com/explore/b",
                "note_id": "b",
            },
            {
                "scope": "xhs_history",
                "title": "看过笔记",
                "url": "https://www.xiaohongshu.com/explore/c",
                "note_id": "c",
            },
        ]
    )

    assert [event["event_type"] for event in events] == ["favorite", "like", "view"]
    assert all(event["metadata"]["source_platform"] == "xiaohongshu" for event in events)


def test_xhs_bootstrap_notes_to_events_preserves_metadata_and_skips_empty() -> None:
    events = xhs_bootstrap_notes_to_events(
        [
            {
                "scope": "saved",
                "title": "手冲咖啡入门",
                "url": "https://www.xiaohongshu.com/explore/note-1",
                "note_id": "note-1",
                "xsec_token": "token-1",
                "author": "豆子老师",
                "cover_url": "https://example.com/cover.jpg",
            },
            {"scope": "liked", "title": "", "url": ""},
            {"scope": "unknown", "title": "未知", "url": "https://example.com/x"},
        ]
    )

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "favorite"
    assert event["title"] == "手冲咖啡入门"
    assert event["url"] == "https://www.xiaohongshu.com/explore/note-1"
    assert event["context"] == "小红书收藏：手冲咖啡入门 作者：豆子老师"
    assert event["metadata"] == {
        "source_platform": "xiaohongshu",
        "note_id": "note-1",
        "xsec_token": "token-1",
        "author": "豆子老师",
        "cover_url": "https://example.com/cover.jpg",
        "import_source": "xhs_bootstrap_saved",
        "signal_strength": 1.0,
    }


class TestXhsCreatorStore:
    def test_add_and_list(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add(
            creator_id="uid123",
            creator_url="https://www.xiaohongshu.com/user/profile/uid123",
            display_name="键圈老用户",
        )

        subs = creator_store.list_all()
        assert len(subs) == 1
        assert subs[0]["creator_id"] == "uid123"
        assert subs[0]["display_name"] == "键圈老用户"

    def test_add_duplicate_is_ignored(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")

        assert len(creator_store.list_all()) == 1

    def test_delete(self, creator_store: XhsCreatorStore) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")
        subs = creator_store.list_all()
        assert len(subs) == 1

        deleted = creator_store.delete(subs[0]["id"])
        assert deleted is True
        assert len(creator_store.list_all()) == 0

    def test_delete_nonexistent_returns_false(self, creator_store: XhsCreatorStore) -> None:
        assert creator_store.delete(9999) is False

    def test_due_for_fetch(self, creator_store: XhsCreatorStore, db: Database) -> None:
        creator_store.add("uid1", "https://xhs.com/u/uid1", "user1")

        # Fresh subscription should be due
        due = creator_store.due_for_fetch(hours=24)
        assert len(due) == 1

        # After marking fetched, should not be due
        creator_store.mark_fetched(due[0]["id"])
        assert len(creator_store.due_for_fetch(hours=24)) == 0


# ── API endpoint tests ────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = Database(tmp_path / "api.db")
    db.initialize()

    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


class TestXhsTaskApi:
    def test_next_task_returns_204_when_empty(self, api_client: TestClient) -> None:
        resp = api_client.get("/api/sources/xhs/next-task")
        assert resp.status_code == 204

    def test_task_result_completes_task(self, api_client: TestClient) -> None:
        # Enqueue via internal queue (simulating scheduler)
        api_client.post(
            "/api/sources/xhs/observed-urls",
            json={
                "urls": ["https://www.xiaohongshu.com/explore/abc"],
                "page_type": "search",
            },
        )

        # We can't easily enqueue via API (no public enqueue endpoint yet),
        # but we can test task-result handles missing task gracefully
        resp = api_client.post(
            "/api/sources/xhs/task-result",
            json={
                "task_id": "nonexistent",
                "status": "ok",
                "urls": ["https://www.xiaohongshu.com/explore/x"],
            },
        )
        assert resp.status_code == 200

    def test_creator_crud(self, api_client: TestClient) -> None:
        # Add
        resp = api_client.post(
            "/api/sources/xhs/creators",
            json={
                "creator_id": "uid123",
                "creator_url": "https://www.xiaohongshu.com/user/profile/uid123",
                "display_name": "键圈老用户",
            },
        )
        assert resp.status_code == 201

        # List
        resp = api_client.get("/api/sources/xhs/creators")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["creator_id"] == "uid123"

        # Delete
        sub_id = items[0]["id"]
        resp = api_client.delete(f"/api/sources/xhs/creators/{sub_id}")
        assert resp.status_code == 200

        # Verify deleted
        resp = api_client.get("/api/sources/xhs/creators")
        assert len(resp.json()["items"]) == 0

    def test_creator_add_requires_fields(self, api_client: TestClient) -> None:
        resp = api_client.post(
            "/api/sources/xhs/creators",
            json={"display_name": "x"},
        )
        assert resp.status_code == 422


def test_next_pending_only_ids_restricts_claim(queue: XhsTaskQueue) -> None:
    """gui-init: during init, next-task is restricted to init-owned ids so a
    stale pending task can't be claimed and starve the run."""
    stale_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": []}, daily_budget=0)
    owned_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": []}, daily_budget=0)
    assert stale_id and owned_id

    # Restricted to the owned id → returns the owned task even though the stale
    # one is older (would otherwise be claimed first).
    task = queue.next_pending(only_ids={owned_id})
    assert task is not None and str(task["id"]) == owned_id

    # Empty restriction → nothing claimable (init owns no task for this source).
    assert queue.next_pending(only_ids=set()) is None

    # No restriction (None) → normal behavior: the remaining pending task.
    remaining = queue.next_pending()
    assert remaining is not None and str(remaining["id"]) == stale_id
