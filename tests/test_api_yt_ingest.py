"""Tests for the YouTube task-queue API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.storage.database import Database


class RecordingMemoryManager:
    """Captures every event propagated through the source task path."""

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
def yt_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, Database, RecordingMemoryManager]:
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


def _enqueue_yt_bootstrap_task(db: Database) -> str:
    from openbiliclaw.sources.yt_tasks import YtTaskQueue

    queue = YtTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_profile",
        {"scopes": ["yt_history", "yt_subscriptions", "yt_likes"]},
        daily_budget=10,
    )
    assert task_id is not None
    return task_id


def test_yt_bootstrap_skips_items_already_seen_in_previous_task(
    yt_task_client: tuple[TestClient, Database, RecordingMemoryManager],
) -> None:
    client, db, memory = yt_task_client

    for _ in range(2):
        task_id = _enqueue_yt_bootstrap_task(db)
        response = client.post(
            "/api/sources/yt/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "items": [
                    {
                        "scope": "yt_history",
                        "title": "重复 YouTube 历史",
                        "url": "https://www.youtube.com/watch?v=repeated-yt",
                        "video_id": "repeated-yt",
                        "channel": "频道",
                    }
                ],
                "scope_counts": {"yt_history": 1},
            },
        )
        assert response.status_code == 200

    assert [event["title"] for event in memory.events] == ["重复 YouTube 历史"]
    assert len(memory.profile_signals) == 1
    assert memory.load_source_bootstrap_state()["yt_seen_item_keys"] == ["yt_history:repeated-yt"]
