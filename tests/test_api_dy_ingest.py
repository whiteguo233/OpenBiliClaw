"""Tests for the Douyin task-queue API endpoints.

Task 2 of the Douyin bootstrap import plan
(``docs/plans/2026-05-06-douyin-bootstrap-import.md``).

The endpoints mirror the XHS pattern in shape — separate table
``dy_tasks`` and separate route prefix ``/api/sources/dy/`` — but
share zero code with the XHS implementation per design-doc
"Module Isolation from XHS".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.storage.database import Database


class RecordingMemoryManager:
    """Captures every event propagated through the soul pipeline."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.profile_signals: list[object] = []
        self._discovery_runtime_state: dict[str, object] = {}
        self._source_bootstrap_state: dict[str, object] = {}

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self._discovery_runtime_state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self._discovery_runtime_state = dict(state)

    def load_source_bootstrap_state(self) -> dict[str, object]:
        return dict(self._source_bootstrap_state)

    def save_source_bootstrap_state(self, state: dict[str, object]) -> None:
        self._source_bootstrap_state = dict(state)


class RecordingProfilePipeline:
    def __init__(self, memory: RecordingMemoryManager) -> None:
        self._memory = memory

    async def ingest_batch(self, signals: list[object]) -> object:
        from types import SimpleNamespace

        self._memory.profile_signals.extend(signals)
        return SimpleNamespace(layers_updated=[])


class RecordingSoulEngine:
    def __init__(self, memory: RecordingMemoryManager) -> None:
        self.pipeline = RecordingProfilePipeline(memory)

    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def dy_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, RecordingMemoryManager]:
    """Build an API client with an injectable memory manager for dy task tests."""
    from types import SimpleNamespace

    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "task.db")
    db.initialize()
    memory = RecordingMemoryManager()

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

    from openbiliclaw.api.app import create_app

    app = create_app(
        database=db,
        memory_manager=memory,
        soul_engine=RecordingSoulEngine(memory),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    return TestClient(app), db, memory


def _enqueue_dy_bootstrap_task(db: Database, payload: dict[str, object] | None = None) -> str:
    """Enqueue a bootstrap_profile task via the DyTaskQueue and return its id."""
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    queue = DyTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_profile",
        payload or {"scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"]},
        daily_budget=10,
    )
    assert task_id is not None
    return task_id


class TestDyNextTask:
    def test_returns_204_when_no_tasks_pending(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        client, _db, _memory = dy_task_client
        response = client.get("/api/sources/dy/next-task")
        assert response.status_code == 204

    def test_returns_pending_task_payload(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        client, db, _memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)

        response = client.get("/api/sources/dy/next-task")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == task_id
        assert body["type"] == "bootstrap_profile"
        assert body["scopes"] == ["dy_post", "dy_collect", "dy_like", "dy_follow"]


class TestDyTaskResult:
    def test_rejects_missing_task_id(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        client, _db, _memory = dy_task_client
        response = client.post("/api/sources/dy/task-result", json={"status": "ok"})
        assert response.status_code == 422

    def test_dy_bootstrap_task_result_records_events(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """status=ok with bootstrap_profile videos: marks task complete and
        propagates events through memory."""
        client, db, memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)

        response = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "videos": [
                    {
                        "scope": "dy_collect",
                        "title": "demo",
                        "url": "https://www.douyin.com/video/aaa",
                        "aweme_id": "aaa",
                        "author": "作者",
                    },
                    {
                        "scope": "dy_like",
                        "title": "liked",
                        "url": "https://www.douyin.com/video/bbb",
                        "aweme_id": "bbb",
                    },
                    {
                        "scope": "dy_follow",
                        "title": "creator",
                        "url": "https://www.douyin.com/user/ccc",
                        "creator_sec_uid": "ccc",
                    },
                ],
                "scope_counts": {"dy_collect": 1, "dy_like": 1, "dy_follow": 1, "dy_post": 0},
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

        event_types = [e["event_type"] for e in memory.events]
        assert event_types == ["favorite", "like", "follow"]
        assert all(e["metadata"]["source_platform"] == "douyin" for e in memory.events)
        assert len(memory.profile_signals) == 3
        assert [signal.payload["event_type"] for signal in memory.profile_signals] == [
            "favorite",
            "like",
            "follow",
        ]
        assert all(
            signal.payload["metadata"]["source_platform"] == "douyin"
            for signal in memory.profile_signals
        )

        # Task is marked completed.
        from openbiliclaw.sources.dy_tasks import DyTaskQueue

        queue = DyTaskQueue(db)
        task = queue.get(task_id)
        assert task is not None
        assert task["status"] == "completed"

    def test_dy_task_failure_marks_task_failed(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        client, db, memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)

        response = client.post(
            "/api/sources/dy/task-result",
            json={"task_id": task_id, "status": "failed", "error": "captcha"},
        )
        assert response.status_code == 200
        assert memory.events == []  # no events on failure

        from openbiliclaw.sources.dy_tasks import DyTaskQueue

        queue = DyTaskQueue(db)
        task = queue.get(task_id)
        assert task is not None
        assert task["status"] == "failed"

    def test_dy_partial_result_does_not_mark_complete(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """status=partial keeps the task pending so the executor can keep posting."""
        client, db, memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)

        response = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "partial",
                "videos": [
                    {
                        "scope": "dy_collect",
                        "title": "v1",
                        "url": "https://www.douyin.com/video/v1",
                        "aweme_id": "v1",
                    },
                ],
                "scope_counts": {"dy_collect": 1},
            },
        )
        assert response.status_code == 200
        assert len(memory.events) == 1  # event still propagates incrementally

        from openbiliclaw.sources.dy_tasks import DyTaskQueue

        queue = DyTaskQueue(db)
        task = queue.get(task_id)
        assert task is not None
        assert task["status"] == "pending"  # direct POST did not claim it first

    def test_dy_scope_partials_then_empty_final_preserve_videos_and_dedup_events(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """Mirror the live dispatcher flow: per-scope partials carry videos,
        then the final ok payload carries only accumulated counts."""
        import json

        client, db, memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)

        first = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "partial",
                "videos": [
                    {
                        "scope": "dy_collect",
                        "title": "收藏视频",
                        "url": "https://www.douyin.com/video/fav-1",
                        "aweme_id": "fav-1",
                    },
                ],
                "scope_counts": {"dy_collect": 1, "dy_like": 0, "dy_post": 0, "dy_follow": 0},
            },
        )
        assert first.status_code == 200

        second = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "partial",
                "videos": [
                    {
                        "scope": "dy_collect",
                        "title": "收藏视频",
                        "url": "https://www.douyin.com/video/fav-1",
                        "aweme_id": "fav-1",
                    },
                    {
                        "scope": "dy_like",
                        "title": "点赞视频",
                        "url": "https://www.douyin.com/video/like-1",
                        "aweme_id": "like-1",
                    },
                ],
                "scope_counts": {"dy_collect": 1, "dy_like": 1, "dy_post": 0, "dy_follow": 0},
            },
        )
        assert second.status_code == 200

        final = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "videos": [],
                "scope_counts": {"dy_collect": 1, "dy_like": 1, "dy_post": 0, "dy_follow": 0},
            },
        )
        assert final.status_code == 200

        assert [event["event_type"] for event in memory.events] == ["favorite", "like"]

        from openbiliclaw.sources.dy_tasks import DyTaskQueue

        queue = DyTaskQueue(db)
        task = queue.get(task_id)
        assert task is not None
        assert task["status"] == "completed"
        result = json.loads(str(task["result_json"]))
        assert [video["aweme_id"] for video in result["videos"]] == ["fav-1", "like-1"]
        assert result["scope_counts"]["dy_collect"] == 1
        assert result["scope_counts"]["dy_like"] == 1

    def test_dy_search_task_result_does_not_propagate_memory_events(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """Search task results are discovery candidates, not bootstrap profile signals."""
        import json

        client, db, memory = dy_task_client
        from openbiliclaw.sources.dy_tasks import DyTaskQueue

        queue = DyTaskQueue(db)
        task_id = queue.enqueue_with_id(
            "search",
            {"keywords": ["猫"], "max_items_per_keyword": 5},
            daily_budget=10,
        )
        assert task_id is not None

        response = client.post(
            "/api/sources/dy/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "videos": [
                    {
                        "scope": "dy_search",
                        "title": "搜索结果",
                        "url": "https://www.douyin.com/video/search-1",
                        "aweme_id": "search-1",
                    },
                ],
                "scope_counts": {"dy_search": 1},
            },
        )
        assert response.status_code == 200
        assert memory.events == []

        task = queue.get(task_id)
        assert task is not None
        assert task["status"] == "completed"
        result = json.loads(str(task["result_json"]))
        assert result["scope_counts"]["dy_search"] == 1
        assert result["videos"][0]["aweme_id"] == "search-1"

    def test_dy_bootstrap_skips_videos_already_seen_in_previous_task(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        """A later bootstrap task must only propagate source items that
        have not already fed the profile update path."""
        client, db, memory = dy_task_client

        for _ in range(2):
            task_id = _enqueue_dy_bootstrap_task(db)
            response = client.post(
                "/api/sources/dy/task-result",
                json={
                    "task_id": task_id,
                    "status": "ok",
                    "videos": [
                        {
                            "scope": "dy_collect",
                            "title": "重复抖音收藏",
                            "url": "https://www.douyin.com/video/repeated-dy",
                            "aweme_id": "repeated-dy",
                        }
                    ],
                    "scope_counts": {"dy_collect": 1},
                },
            )
            assert response.status_code == 200

        assert [event["title"] for event in memory.events] == ["重复抖音收藏"]
        assert len(memory.profile_signals) == 1
        assert memory.load_source_bootstrap_state()["dy_seen_video_keys"] == [
            "dy_collect:repeated-dy"
        ]


class TestDyTaskKick:
    """`POST /api/sources/dy/kick` broadcasts `dy_task_available` over
    the runtime-stream so the extension dispatcher polls immediately
    instead of waiting up to 60s for the next chrome.alarms tick."""

    def test_kick_broadcasts_dy_task_available_event(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from openbiliclaw.runtime.events import RuntimeEventHub
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "task.db")
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

        hub = RuntimeEventHub()
        memory = RecordingMemoryManager()

        from openbiliclaw.api.app import create_app

        app = create_app(
            database=db,
            memory_manager=memory,
            soul_engine=SimpleNamespace(),
            runtime_controller=SimpleNamespace(memory_manager=memory),
            recommendation_engine=None,
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        # Subscribe to the hub BEFORE firing the kick — the publish
        # path is async/queue-based, so a slow subscriber would still
        # receive the event from its queue when it gets around to it.
        import asyncio

        async def collect_one_event() -> dict[str, object]:
            queue = await hub.subscribe()
            return await asyncio.wait_for(queue.get(), timeout=2.0)

        loop = asyncio.new_event_loop()
        queue = loop.run_until_complete(hub.subscribe())

        try:
            response = client.post("/api/sources/dy/kick")
            assert response.status_code == 200
            assert response.json() == {"ok": True}

            # The kick endpoint awaited publish() inside the request
            # handler, so by the time client.post returns the event is
            # already in the queue.
            event = loop.run_until_complete(asyncio.wait_for(queue.get(), timeout=2.0))
            assert event["type"] == "dy_task_available"
            assert event["source"] == "task_kick"
        finally:
            loop.run_until_complete(hub.unsubscribe(queue))
            loop.close()

    def test_kick_succeeds_even_when_event_hub_is_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the daemon was started without an event hub (degraded
        config) the kick endpoint must still return 200 — it's a
        best-effort wake-up, not a critical path."""
        from types import SimpleNamespace

        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "task.db")
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

        memory = RecordingMemoryManager()

        from openbiliclaw.api.app import create_app

        app = create_app(
            database=db,
            memory_manager=memory,
            soul_engine=SimpleNamespace(),
            runtime_controller=SimpleNamespace(memory_manager=memory),
            recommendation_engine=None,
            # No runtime_event_hub — simulating degraded daemon state.
        )
        client = TestClient(app)

        response = client.post("/api/sources/dy/kick")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        # Same for xhs.
        response = client.post("/api/sources/xhs/kick")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


class TestDyTaskDiscoverySeeds:
    def test_recent_dy_creator_sec_uids_prefers_interacted_creators(
        self,
        dy_task_client: tuple[TestClient, Database, RecordingMemoryManager],
    ) -> None:
        from openbiliclaw.sources.dy_tasks import DyTaskQueue, recent_dy_creator_sec_uids

        _client, db, _memory = dy_task_client
        task_id = _enqueue_dy_bootstrap_task(db)
        queue = DyTaskQueue(db)
        queue.merge_result(
            task_id,
            videos=[
                {"scope": "dy_post", "creator_sec_uid": "sec-own", "aweme_id": "post-1"},
                {"scope": "dy_like", "author_sec_uid": "sec-liked", "aweme_id": "like-1"},
                {
                    "scope": "dy_collect",
                    "creator_sec_uid": "sec-collected",
                    "aweme_id": "collect-1",
                },
                {"scope": "dy_follow", "creator_sec_uid": "sec-followed", "title": "关注作者"},
                {"scope": "dy_collect", "creator_sec_uid": "sec-liked", "aweme_id": "collect-2"},
            ],
            complete=True,
        )

        assert recent_dy_creator_sec_uids(db, limit=3) == (
            "sec-followed",
            "sec-collected",
            "sec-liked",
        )
