"""Tests for the Zhihu task-queue API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from openbiliclaw.storage.database import Database


class RecordingMemoryManager:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self._source_bootstrap_state: dict[str, object] = {}

    async def propagate_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return {}

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        return None

    def load_source_bootstrap_state(self) -> dict[str, object]:
        return dict(self._source_bootstrap_state)

    def save_source_bootstrap_state(self, state: dict[str, object]) -> None:
        self._source_bootstrap_state = dict(state)

    def update_source_bootstrap_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]:
        state = dict(self._source_bootstrap_state)
        result = mutator(state)
        self._source_bootstrap_state = state if result is None else result
        return dict(self._source_bootstrap_state)


class RecordingProfilePipeline:
    def __init__(self) -> None:
        self.signals: list[object] = []

    async def ingest_batch(self, signals: list[object]) -> object:
        from types import SimpleNamespace

        self.signals.extend(signals)
        return SimpleNamespace(layers_updated=[])


class RecordingSoulEngine:
    def __init__(self) -> None:
        self.pipeline = RecordingProfilePipeline()

    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def zhihu_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, RecordingMemoryManager, RecordingSoulEngine]:
    from types import SimpleNamespace

    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "task.db")
    db.initialize()
    memory = RecordingMemoryManager()
    soul = RecordingSoulEngine()

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
            zhihu=SimpleNamespace(enabled=True),
        ),
        scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    from openbiliclaw.api.app import create_app

    app = create_app(
        database=db,
        memory_manager=memory,
        soul_engine=soul,
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    # Injection path intentionally leaves ctx.config unset; pin the fake config
    # so endpoint source-enabled guards behave like the full runtime path.
    app.state.runtime_context.config = fake_config
    return TestClient(app), db, memory, soul


def test_zhihu_next_task_and_result_record_without_profile_propagation(
    zhihu_task_client: tuple[TestClient, Database, RecordingMemoryManager, RecordingSoulEngine],
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    client, db, memory, soul = zhihu_task_client
    queue = ZhihuTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"scopes": ["zhihu_read_history"], "max_items_per_scope": 20},
        daily_budget=10,
    )
    assert task_id is not None

    next_response = client.get("/api/sources/zhihu/next-task")

    assert next_response.status_code == 200
    assert next_response.json()["id"] == task_id
    assert next_response.json()["type"] == "bootstrap_events"

    result_response = client.post(
        "/api/sources/zhihu/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "items": [
                {
                    "scope": "zhihu_read_history",
                    "title": "最近浏览回答",
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "content_type": "answer",
                    "content_id": "2",
                }
            ],
            "scope_counts": {"zhihu_read_history": 1},
        },
    )

    assert result_response.status_code == 200
    task = queue.get(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert memory.events == []
    assert soul.pipeline.signals == []


def test_zhihu_bootstrap_result_with_profile_update_propagates_to_memory_and_pipeline(
    zhihu_task_client: tuple[TestClient, Database, RecordingMemoryManager, RecordingSoulEngine],
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    client, db, memory, soul = zhihu_task_client
    queue = ZhihuTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "scopes": ["zhihu_read_history"],
            "max_items_per_scope": 20,
            "profile_update": True,
        },
        daily_budget=10,
    )
    assert task_id is not None

    result_response = client.post(
        "/api/sources/zhihu/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "items": [
                {
                    "scope": "zhihu_read_history",
                    "title": "最近浏览回答",
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "content_type": "answer",
                    "content_id": "2",
                    "summary": "真实回答摘要",
                }
            ],
            "scope_counts": {"zhihu_read_history": 1},
        },
    )

    assert result_response.status_code == 200
    assert len(memory.events) == 1
    event = memory.events[0]
    assert event["event_type"] == "view"
    assert event["title"] == "最近浏览回答"
    assert event["metadata"]["source_platform"] == "zhihu"  # type: ignore[index]
    assert soul.pipeline.signals == []


def test_zhihu_next_task_cancels_scheduler_incremental_when_source_disabled(
    zhihu_task_client: tuple[TestClient, Database, RecordingMemoryManager, RecordingSoulEngine],
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    client, db, memory, soul = zhihu_task_client
    ctx = client.app.state.runtime_context
    queue = ZhihuTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"incremental": True, "incremental_owner": "scheduler"},
        daily_budget=0,
    )
    assert task_id is not None

    ctx.config.sources.zhihu.enabled = False
    blocked = client.get("/api/sources/zhihu/next-task")

    assert blocked.status_code == 204
    stored = queue.get(task_id)
    assert stored is not None
    assert stored["status"] == "failed"
    assert memory.events == []
    assert soul.pipeline.signals == []


def test_zhihu_next_task_disabled_source_keeps_manual_task_pending_and_resumes(
    zhihu_task_client: tuple[TestClient, Database, RecordingMemoryManager, RecordingSoulEngine],
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    client, db, memory, soul = zhihu_task_client
    ctx = client.app.state.runtime_context
    queue = ZhihuTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"scopes": ["zhihu_read_history"], "max_items_per_scope": 20},
        daily_budget=0,
    )
    assert task_id is not None

    ctx.config.sources.zhihu.enabled = False
    blocked = client.get("/api/sources/zhihu/next-task")

    assert blocked.status_code == 204
    stored = queue.get(task_id)
    assert stored is not None
    assert stored["status"] == "pending"

    ctx.config.sources.zhihu.enabled = True
    resumed = client.get("/api/sources/zhihu/next-task")
    assert resumed.status_code == 200
    assert resumed.json()["id"] == task_id
