"""Tests for the TikTok bootstrap task queue and event-conversion helper.

Mirrors ``test_dy_tasks.py`` for the TikTok full-source upgrade
(``docs/platform-source-contract.tiktok.toml``): queue CRUD, scope→event
mapping (including the first history-type scope ``tt_history``), two-phase
staged completion, and the single-flight admission claim.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.sources import tiktok_tasks
from openbiliclaw.sources.event_format import SOURCE_TIKTOK
from openbiliclaw.sources.tiktok_tasks import (
    TiktokTaskQueue,
    tiktok_bootstrap_videos_to_events,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "openbiliclaw.db")
    db.initialize()
    return db


def test_tiktok_bootstrap_videos_to_events_maps_scopes_to_event_types() -> None:
    """Each TikTok scope maps to its canonical event_type."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_like",
                "title": "我点赞的",
                "url": "https://www.tiktok.com/@creator/video/aaa",
                "video_id": "aaa",
            },
            {
                "scope": "tt_collect",
                "title": "我收藏的",
                "url": "https://www.tiktok.com/@creator/video/bbb",
                "video_id": "bbb",
            },
            {
                "scope": "tt_history",
                "title": "我看过的",
                "url": "https://www.tiktok.com/@creator/video/ccc",
                "video_id": "ccc",
            },
            {
                "scope": "tt_follow",
                "title": "关注的创作者",
                "url": "https://www.tiktok.com/@ddd",
                "creator_sec_uid": "ddd",
            },
        ]
    )

    assert [e["event_type"] for e in events] == ["like", "favorite", "view", "follow"]
    for event in events:
        assert event["metadata"]["source_platform"] == SOURCE_TIKTOK


def test_tiktok_bootstrap_videos_to_events_signal_strength_per_scope() -> None:
    """Signal strengths: collect=1.0, like=0.85, follow=0.6, history=0.35."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_like",
                "title": "l",
                "url": "https://www.tiktok.com/@c/video/l",
                "video_id": "l",
            },
            {
                "scope": "tt_collect",
                "title": "c",
                "url": "https://www.tiktok.com/@c/video/c",
                "video_id": "c",
            },
            {
                "scope": "tt_history",
                "title": "h",
                "url": "https://www.tiktok.com/@c/video/h",
                "video_id": "h",
            },
            {
                "scope": "tt_follow",
                "title": "f",
                "url": "https://www.tiktok.com/@f",
                "creator_sec_uid": "f",
            },
        ]
    )
    strengths = [e["metadata"]["signal_strength"] for e in events]
    assert strengths == [0.85, 1.0, 0.35, 0.6]


def test_tiktok_bootstrap_videos_to_events_skips_blank_and_unknown() -> None:
    """Items missing both title and url are dropped; unknown scopes are dropped."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {"scope": "tt_collect", "title": "", "url": "", "video_id": "x"},  # blank — drop
            {"scope": "tt_unknown_scope", "title": "t", "url": "u"},  # unknown scope — drop
            "not-a-dict",  # invalid type — drop
            {
                "scope": "tt_history",
                "title": "valid",
                "url": "https://www.tiktok.com/@c/video/v",
                "video_id": "v",
            },
        ]
    )
    assert len(events) == 1
    assert events[0]["title"] == "valid"


def test_tiktok_bootstrap_videos_to_events_import_source_tag() -> None:
    """import_source metadata is namespaced per scope so analytics can split."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_collect",
                "title": "c",
                "url": "https://www.tiktok.com/@c/video/c",
                "video_id": "c",
            },
        ]
    )
    assert events[0]["metadata"]["import_source"] == "tt_bootstrap_collect"


def test_tiktok_bootstrap_videos_to_events_passes_through_video_metadata() -> None:
    """video_id, author, cover_url propagate so downstream curator can dedupe / render."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_like",
                "title": "demo title",
                "url": "https://www.tiktok.com/@c/video/zzz",
                "video_id": "zzz",
                "author": "creator nick",
                "author_sec_uid": "sec_uid_xyz",
                "cover_url": "https://example.com/cover.jpg",
            },
        ]
    )
    metadata = events[0]["metadata"]
    assert metadata["video_id"] == "zzz"
    assert metadata["cover_url"] == "https://example.com/cover.jpg"
    # build_event places author inside metadata.
    assert metadata["author"] == "creator nick"


def test_tiktok_bootstrap_videos_to_events_follow_uses_creator_sec_uid() -> None:
    """For tt_follow scope the metadata key is creator_sec_uid, not video_id."""
    events = tiktok_bootstrap_videos_to_events(
        [
            {
                "scope": "tt_follow",
                "title": "@someone",
                "url": "https://www.tiktok.com/@abc",
                "creator_sec_uid": "abc",
            },
        ]
    )
    metadata = events[0]["metadata"]
    assert metadata["creator_sec_uid"] == "abc"
    assert events[0]["event_type"] == "follow"


def test_tiktok_bootstrap_video_key_scopes_same_video_independently() -> None:
    """The same video_id in two scopes must not collapse into one key."""
    liked = {"scope": "tt_like", "video_id": "same"}
    watched = {"scope": "tt_history", "video_id": "same"}
    assert tiktok_tasks.tiktok_bootstrap_video_key(
        liked
    ) != tiktok_tasks.tiktok_bootstrap_video_key(watched)


def test_tiktok_result_merge_preserves_existing_semantic_status() -> None:
    """A metadata/count merge must not silently drop result_json.status."""
    merged, added = tiktok_tasks._merge_tiktok_result_payload(
        {
            "status": "degraded",
            "videos": [{"scope": "tt_collect", "video_id": "first"}],
            "scope_counts": {"tt_collect": 1},
        },
        scope_counts={"tt_collect": 1},
        debug={"retry": "late"},
    )

    assert added == []
    assert merged["status"] == "degraded"
    assert merged["debug"] == {"retry": "late"}


def test_tiktok_task_queue_ignores_stale_pending_failures_for_daily_budget(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)

    stale_id = queue.enqueue_with_id("search", {"keywords": ["旧任务"]}, daily_budget=2)
    assert stale_id is not None
    queue.fail(stale_id, error="stale_pending")
    assert queue.enqueue_with_id("search", {"keywords": ["有效任务"]}, daily_budget=2)

    assert queue.enqueue_with_id("search", {"keywords": ["补池任务"]}, daily_budget=2)


def test_tiktok_task_queue_counts_non_stale_failures_for_daily_budget(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)

    failed_id = queue.enqueue_with_id("search", {"keywords": ["超时任务"]}, daily_budget=2)
    assert failed_id is not None
    queue.fail(failed_id, error="task_timeout")
    assert queue.enqueue_with_id("search", {"keywords": ["有效任务"]}, daily_budget=2)

    assert queue.enqueue_with_id("search", {"keywords": ["第三个任务"]}, daily_budget=2) is None


def test_tiktok_task_queue_zero_daily_budget_disables_daily_cap(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)

    for i in range(5):
        assert queue.enqueue_with_id("search", {"keywords": [f"任务 {i}"]}, daily_budget=0)


def test_tiktok_task_queue_claims_pending_task_until_terminal_status(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_collect"]})
    assert task_id is not None

    first = queue.next_pending()

    assert first is not None
    assert first["id"] == task_id
    assert first["status"] == "in_progress"
    assert queue.next_pending() is None

    queue.merge_result(task_id, videos=[], complete=True)
    assert queue.next_pending() is None


def test_tiktok_task_queue_does_not_claim_second_task_while_first_lease_is_active(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)
    first_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_like"]})
    second_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_history"]})
    assert first_id is not None
    assert second_id is not None

    first = queue.next_pending()
    assert first is not None
    assert first["id"] == first_id
    assert queue.next_pending() is None

    database.conn.execute(
        "UPDATE tiktok_tasks SET claimed_at = datetime('now', '-16 minutes') WHERE id = ?",
        (first_id,),
    )
    database.conn.commit()
    reclaimed = queue.next_pending()
    assert reclaimed is not None
    assert reclaimed["id"] == first_id
    assert reclaimed["status"] == "in_progress"


def test_tiktok_task_queue_finds_recent_bootstrap_task(
    database: Database,
) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_collect"]})
    assert task_id is not None

    recent = queue.find_recent_task("bootstrap_profile", recent_hours=6)

    assert recent is not None
    assert recent["id"] == task_id


def test_tiktok_task_queue_staged_completion_freezes_first_final_payload(
    database: Database,
) -> None:
    """First final wins: a staged canonical payload is immutable for later finals."""
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_like"]})
    assert task_id is not None
    assert queue.next_pending() is not None

    staged = queue.stage_final_result(
        task_id,
        terminal_status="degraded",
        videos=[{"scope": "tt_like", "video_id": "a", "title": "A"}],
        scope_counts={"tt_like": 1},
    )
    assert staged["_openbiliclaw_terminal_status"] == "degraded"

    # The task row stays nonterminal until projections replay.
    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == "in_progress"

    # A retried/later final does not replace the frozen payload.
    replayed = queue.stage_final_result(
        task_id,
        terminal_status="ok",
        videos=[{"scope": "tt_like", "video_id": "b", "title": "B"}],
    )
    assert replayed["_openbiliclaw_terminal_status"] == "degraded"
    assert [v["video_id"] for v in replayed["videos"]] == ["a"]

    # Late partial merges are rejected once a final is staged.
    assert queue.merge_result(task_id, videos=[{"scope": "tt_like", "video_id": "c"}]) == []

    assert queue.complete_staged_result(task_id) is True
    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == "completed"
    canonical = json.loads(str(row["result_json"]))
    assert canonical["_openbiliclaw_terminal_status"] == "degraded"


def test_tiktok_task_queue_staged_failed_completes_as_failed(
    database: Database,
) -> None:
    """A staged ``failed`` marker flips the row to failed, never completed."""
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_follow"]})
    assert task_id is not None
    assert queue.next_pending() is not None

    queue.stage_final_result(task_id, terminal_status="failed", debug={"error": "login wall"})

    # fail() must not overwrite a staged final.
    assert queue.fail(task_id, error="late_failure") is False

    assert queue.complete_staged_result(task_id) is True
    row = queue.get(task_id)
    assert row is not None
    assert row["status"] == "failed"


def test_tiktok_task_queue_expire_stale_pending_spares_staged_results(
    database: Database,
) -> None:
    """Staged rows keep their canonical payload when stale pending rows expire."""
    queue = TiktokTaskQueue(database)
    staged_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_collect"]})
    expired_id = queue.enqueue_with_id("bootstrap_profile", {"scopes": ["tt_like"]})
    assert staged_id is not None and expired_id is not None

    database.conn.execute(
        "UPDATE tiktok_tasks SET created_at = datetime('now', '-2 hours') WHERE id = ?",
        (staged_id,),
    )
    database.conn.execute(
        "UPDATE tiktok_tasks SET created_at = datetime('now', '-90 minutes') WHERE id = ?",
        (expired_id,),
    )
    database.conn.commit()
    claimed = queue.next_pending()
    assert claimed is not None and claimed["id"] == staged_id
    queue.stage_final_result(staged_id, terminal_status="ok")
    database.conn.execute(
        "UPDATE tiktok_tasks SET status = 'pending' WHERE id = ?",
        (staged_id,),
    )
    database.conn.commit()

    expired = queue.expire_stale_pending(["bootstrap_profile"], older_than_seconds=3600)

    assert expired == 1
    expired_row = queue.get(expired_id)
    assert expired_row is not None
    assert str(expired_row["status"]) == "failed"
    staged_row = queue.get(staged_id)
    assert staged_row is not None
    assert str(staged_row["status"]) == "pending"
    assert "_openbiliclaw_terminal_status" in str(staged_row["result_json"])


# ── API route tests (mirror tests/test_api_dy_ingest.py) ─────────────


class _RecordingMemoryManager:
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

    def update_source_bootstrap_state(
        self,
        mutator: Any,
    ) -> dict[str, object]:
        state = dict(self._source_bootstrap_state)
        result = mutator(state)
        self._source_bootstrap_state = state if result is None else result
        return dict(self._source_bootstrap_state)


class _RecordingProfilePipeline:
    def __init__(self, memory: _RecordingMemoryManager) -> None:
        self._memory = memory

    async def ingest_batch(self, signals: list[object]) -> object:
        from types import SimpleNamespace

        self._memory.profile_signals.extend(signals)
        return SimpleNamespace(layers_updated=[])


class _RecordingSoulEngine:
    def __init__(self, memory: _RecordingMemoryManager) -> None:
        self.pipeline = _RecordingProfilePipeline(memory)

    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def tiktok_task_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Database, _RecordingMemoryManager]:
    """Build an API client with an injectable memory manager for tiktok task tests."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    db = Database(tmp_path / "task.db")
    db.initialize()
    memory = _RecordingMemoryManager()

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
        soul_engine=_RecordingSoulEngine(memory),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    return TestClient(app), db, memory


def _enqueue_tiktok_bootstrap_task(db: Database, payload: dict[str, object] | None = None) -> str:
    queue = TiktokTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "bootstrap_profile",
        payload
        or {"scopes": ["tt_like", "tt_collect", "tt_history", "tt_follow"], "profile_update": True},
        daily_budget=10,
    )
    assert task_id is not None
    return task_id


def test_tiktok_next_task_returns_204_when_no_tasks_pending(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, _db, _memory = tiktok_task_client
    response = client.get("/api/sources/tiktok/next-task")
    assert response.status_code == 204


def test_tiktok_next_task_returns_pending_task_payload(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, db, _memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db)

    response = client.get("/api/sources/tiktok/next-task")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["type"] == "bootstrap_profile"
    assert body["scopes"] == ["tt_like", "tt_collect", "tt_history", "tt_follow"]

    # Single-flight: the active lease blocks a second claim.
    assert client.get("/api/sources/tiktok/next-task").status_code == 204


def test_tiktok_task_result_requires_task_id(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, _db, _memory = tiktok_task_client
    response = client.post("/api/sources/tiktok/task-result", json={"status": "ok"})
    assert response.status_code == 422


def test_tiktok_bootstrap_task_result_records_events_and_completes(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    """status=ok: staged completion freezes the payload, replays events, then flips terminal."""
    client, db, memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db)

    response = client.post(
        "/api/sources/tiktok/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "viewer_handle": "test_viewer",
            "videos": [
                {
                    "scope": "tt_collect",
                    "title": "fav",
                    "url": "https://www.tiktok.com/@c/video/aaa",
                    "video_id": "aaa",
                    "author": "creator",
                },
                {
                    "scope": "tt_history",
                    "title": "watched",
                    "url": "https://www.tiktok.com/@c/video/bbb",
                    "video_id": "bbb",
                },
                {
                    "scope": "tt_follow",
                    "title": "creator",
                    "url": "https://www.tiktok.com/@ccc",
                    "creator_sec_uid": "ccc",
                },
            ],
            "scope_counts": {"tt_collect": 1, "tt_history": 1, "tt_follow": 1, "tt_like": 0},
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    event_types = [e["event_type"] for e in memory.events]
    assert event_types == ["favorite", "view", "follow"]
    assert all(e["metadata"]["source_platform"] == "tiktok" for e in memory.events)
    assert memory.profile_signals == []

    task = TiktokTaskQueue(db).get(task_id)
    assert task is not None
    assert task["status"] == "completed"
    canonical = json.loads(str(task["result_json"]))
    assert canonical["_openbiliclaw_terminal_status"] == "ok"

    # A duplicate terminal callback is acknowledged without re-propagating.
    retry = client.post(
        "/api/sources/tiktok/task-result",
        json={"task_id": task_id, "status": "ok", "videos": []},
    )
    assert retry.status_code == 200
    assert retry.json() == {"ok": True, "ignored": True}
    assert len(memory.events) == 3


def test_tiktok_task_failure_marks_task_failed(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, db, memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db)

    response = client.post(
        "/api/sources/tiktok/task-result",
        json={"task_id": task_id, "status": "failed", "error": "login_wall"},
    )
    assert response.status_code == 200
    assert memory.events == []

    task = TiktokTaskQueue(db).get(task_id)
    assert task is not None
    assert task["status"] == "failed"


def test_tiktok_partial_then_degraded_preserves_items(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    """partial keeps the task open; degraded is terminal without success mislabeling."""
    client, db, memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db)

    partial = client.post(
        "/api/sources/tiktok/task-result",
        json={
            "task_id": task_id,
            "status": "partial",
            "viewer_handle": "test_viewer",
            "videos": [
                {
                    "scope": "tt_collect",
                    "title": "首屏收藏",
                    "url": "https://www.tiktok.com/@c/video/fav-first-page",
                    "video_id": "fav-first-page",
                }
            ],
            "scope_counts": {"tt_collect": 1},
        },
    )
    assert partial.status_code == 200
    task = TiktokTaskQueue(db).get(task_id)
    assert task is not None
    assert task["status"] == "pending"

    final = client.post(
        "/api/sources/tiktok/task-result",
        json={
            "task_id": task_id,
            "status": "degraded",
            "viewer_handle": "test_viewer",
            "videos": [],
            "scope_counts": {"tt_collect": 1},
            "debug": {"degraded_reasons": ["tt_collect:pagination_incomplete"]},
        },
    )
    assert final.status_code == 200

    task = TiktokTaskQueue(db).get(task_id)
    assert task is not None
    assert task["status"] == "completed"
    result = json.loads(str(task["result_json"]))
    assert result["status"] == "degraded"
    assert result["_openbiliclaw_terminal_status"] == "degraded"
    assert result["videos"][0]["video_id"] == "fav-first-page"
    assert [event["event_type"] for event in memory.events] == ["favorite"]


def test_tiktok_result_cannot_expand_frozen_scopes_or_budget(database: Database) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_profile", {"scopes": ["tt_like"], "max_items_per_scope": 1}
    )
    assert task_id
    queue.merge_result(task_id, videos=[{"scope": "tt_like", "video_id": "one", "title": "one"}])
    queue.stage_final_result(
        task_id,
        terminal_status="ok",
        videos=[
            {"scope": "tt_like", "video_id": "two", "title": "two"},
            {"scope": "tt_collect", "video_id": "three", "title": "three"},
        ],
        scope_counts={"tt_like": 9999, "tt_collect": 9999},
    )
    result = json.loads(queue.get(task_id)["result_json"])
    assert [row["video_id"] for row in result["videos"]] == ["one"]
    assert result["scope_counts"] == {"tt_like": 1}


def test_tiktok_event_normalization_rejects_nested_identity_and_author() -> None:
    events = tiktok_bootstrap_videos_to_events(
        [
            {"scope": "tt_like", "video_id": ["bad"], "title": None, "url": {}},
            {"scope": "tt_like", "video_id": "good", "title": "video", "author": {"name": "bad"}},
        ]
    )
    assert len(events) == 1
    assert events[0]["metadata"].get("author", "") == ""


def test_tiktok_account_binding_rejects_switch_and_keeps_first_result(database: Database) -> None:
    queue = TiktokTaskQueue(database)
    first = queue.enqueue_with_id("bootstrap_profile", {})
    second = queue.enqueue_with_id("bootstrap_profile", {})
    assert first and second
    key = queue.bind_account(first, viewer_handle="alice", viewer_sec_uid="", profile_update=True)
    assert key.startswith("sha256:")
    with pytest.raises(ValueError, match="task_account_mismatch"):
        queue.bind_account(first, viewer_handle="bob", viewer_sec_uid="", profile_update=True)
    with pytest.raises(ValueError, match="account_switch_requires_reset"):
        queue.bind_account(second, viewer_handle="bob", viewer_sec_uid="", profile_update=True)
    queue.stage_final_result(first, terminal_status="ok")
    assert (
        queue.bind_account(first, viewer_handle="bob", viewer_sec_uid="", profile_update=True)
        == key
    )
    assert json.loads(queue.get(first)["result_json"])["account_key"] == key


def test_tiktok_smoke_task_never_projects_profile(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, db, memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db, {"scopes": ["tt_like"], "profile_update": False})
    response = client.post(
        "/api/sources/tiktok/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "viewer_handle": "test_viewer",
            "videos": [{"scope": "tt_like", "video_id": "123456", "title": "test"}],
        },
    )
    assert response.status_code == 200
    assert memory.events == []
    assert memory.load_source_bootstrap_state() == {}
    assert (
        db.conn.execute(
            "SELECT 1 FROM auth_state WHERE key = 'tiktok_profile_account_key'"
        ).fetchone()
        is None
    )


def test_tiktok_result_rejects_missing_viewer_and_malformed_collection(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    client, db, memory = tiktok_task_client
    task_id = _enqueue_tiktok_bootstrap_task(db)
    assert (
        client.post(
            "/api/sources/tiktok/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "videos": [],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/sources/tiktok/task-result",
            json={
                "task_id": task_id,
                "status": "ok",
                "videos": None,
            },
        ).status_code
        == 422
    )
    assert memory.events == []


@pytest.mark.parametrize(("row_count", "expected_status"), [(1, "ok"), (2, "degraded")])
def test_real_tiktok_api_payload_executes_all_scopes_in_extension(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_count: int,
    expected_status: str,
) -> None:
    """Cross-language boundary: Python next-task -> actual TS dispatcher -> API ingress.

    Browser DOM responses are synthetic; this is a protocol integration test,
    not a claim about authenticated TikTok pages.
    """
    import shutil
    import subprocess
    from pathlib import Path

    from openbiliclaw.sources.source_bootstrap import enqueue_tiktok_bootstrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the extension protocol integration")
    client, db, memory = tiktok_task_client
    monkeypatch.setenv("OPENBILICLAW_TIKTOK_BOOTSTRAP_MAX_ITEMS", "1")
    queued = enqueue_tiktok_bootstrap(db, force=True)
    assert queued.task_id
    response = client.get("/api/sources/tiktok/next-task")
    assert response.status_code == 200
    root = Path(__file__).resolve().parents[1]
    dispatcher = (root / "extension/src/background/tiktok-task-dispatcher.ts").as_uri()
    mock = (root / "extension/tests/helpers/chrome-mock.ts").as_uri()
    runner = tmp_path / "tiktok-protocol.mts"
    runner.write_text(
        "import { executeTask, handleTiktokScopeResult, resetTiktokTaskStateForTest, "
        "computeTiktokTaskTimeoutMs } from "
        f"{json.dumps(dispatcher)};\n"
        f"import {{ installChromeMock }} from {json.dumps(mock)};\n"
        'let input=""; for await (const chunk of process.stdin) input += chunk;\n'
        "const task=JSON.parse(input); const state=installChromeMock();\n"
        "try {\n"
        '  console.log("OBC_DEADLINE="+computeTiktokTaskTimeoutMs(task));\n'
        "  await executeTask(task);\n"
        "  for (const scope of task.scopes) {\n"
        '    await handleTiktokScopeResult({task_id:task.id, scope, status:"ok",\n'
        '      viewer_handle:"test_viewer", scope_count:ROW_COUNT,'
        "      items:Array.from({length:ROW_COUNT},(_,i)=>i+1).map(n=>({\n"
        '        scope, video_id:scope==="tt_follow" ? "" : `${scope}${n}`,\n'
        '        creator_sec_uid:scope==="tt_follow" ? `creator${n}` : "",\n'
        '        author_handle:`creator${n}`, title:"Protocol fixture",\n'
        "        url:`https://www.tiktok.com/@creator/video/123456${n}`,\n"
        '        author:"creator", cover_url:""}))},42);\n'
        "  }\n"
        "  const posts=state.fetchCalls"
        '.filter(c=>c.url.endsWith("/tiktok/task-result")).map(c=>c.body);\n'
        '  console.log("OBC_RESULT="+JSON.stringify(posts));\n'
        "} finally { resetTiktokTaskStateForTest(); state.restore(); }\n"
    )
    runner.write_text(runner.read_text().replace("ROW_COUNT", str(row_count)))
    completed = subprocess.run(
        [node, "--experimental-strip-types", str(runner)],
        input=json.dumps(response.json()),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    from openbiliclaw.cli import _DEFAULT_TIKTOK_BOOTSTRAP_WAIT_SECONDS

    deadline_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("OBC_DEADLINE=")
    )
    assert int(deadline_line.split("=")[1]) / 1000 + 60 < _DEFAULT_TIKTOK_BOOTSTRAP_WAIT_SECONDS
    line = next(line for line in completed.stdout.splitlines() if line.startswith("OBC_RESULT="))
    posts = json.loads(line.removeprefix("OBC_RESULT="))
    assert posts and posts[-1]["status"] == expected_status
    assert {row["scope"] for row in posts[-1]["videos"]} == set(response.json()["scopes"])
    assert response.json()["scopes"] == ["tt_like", "tt_collect", "tt_follow"]
    assert len(posts[-1]["videos"]) == 3  # cumulative frozen cap of one per default scope
    for payload in posts:
        callback = client.post("/api/sources/tiktok/task-result", json=payload)
        assert callback.status_code == 200, callback.text
    assert TiktokTaskQueue(db).get(queued.task_id)["status"] == "completed"
    assert {event["event_type"] for event in memory.events} == {
        "like",
        "favorite",
        "follow",
    }
    assert len(memory.events) == 3


def test_tiktok_failure_keeps_previously_accepted_rows(database: Database) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {})
    assert task_id
    rows = [{"scope": "tt_like", "video_id": "123456", "title": "accepted"}]
    queue.merge_result(task_id, videos=rows)
    queue.fail(task_id, error="account_changed")
    result = json.loads(queue.get(task_id)["result_json"])
    assert result["videos"] == rows
    assert result["error"] == "account_changed"


def test_nonempty_profile_event_and_incremental_ingress_do_not_require_watch_history(
    tiktok_task_client: tuple[Any, Database, _RecordingMemoryManager],
) -> None:
    """Real API/queue/ingress with fixture browser rows; no live-model claim."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.source_bootstrap import enqueue_tiktok_bootstrap

    client, db, memory = tiktok_task_client
    load_config().scheduler.source_incremental_enabled = True
    first = {
        "scope": "tt_collect",
        "video_id": "7433494591833247008",
        "url": "https://www.tiktok.com/@fixture/video/7433494591833247008",
        "title": "Fixture favorite",
        "author": "fixture",
    }
    second = {**first, "video_id": "7433494591833247009", "url": first["url"][:-1] + "9"}
    for incremental, videos in [(False, [first]), (True, [first, second])]:
        queued = enqueue_tiktok_bootstrap(db, force=True, incremental=incremental)
        assert queued.task_id
        task = client.get("/api/sources/tiktok/next-task").json()
        assert task["id"] == queued.task_id
        assert task["scopes"] == ["tt_like", "tt_collect", "tt_follow"]
        result = {
            "task_id": queued.task_id,
            "status": "ok",
            "viewer_handle": "test_viewer",
            "videos": videos,
            "scope_counts": {"tt_like": 0, "tt_collect": len(videos), "tt_follow": 0},
        }
        assert client.post("/api/sources/tiktok/task-result", json=result).status_code == 200
        # A retried result must not reapply either the initial or new signal.
        assert client.post("/api/sources/tiktok/task-result", json=result).status_code == 200
        assert len(memory.events) == len(videos)
        assert all(
            event["metadata"]["profile_update_owner"] == "generic" for event in memory.events
        )
    assert [event["event_type"] for event in memory.events] == ["favorite", "favorite"]


def test_tiktok_queue_does_not_use_mutable_titles_as_item_identity(database: Database) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {})
    assert task_id
    queue.merge_result(task_id, videos=[{"scope": "tt_like", "title": "missing ID"}])
    assert json.loads(queue.get(task_id)["result_json"])["videos"] == []


@pytest.mark.parametrize("initial_uid, next_uid", [("", "SEC_same_user"), ("SEC_same_user", "")])
def test_tiktok_account_binding_survives_optional_uid_across_tasks(
    database: Database, initial_uid: str, next_uid: str
) -> None:
    queue = TiktokTaskQueue(database)
    first = queue.enqueue_with_id("bootstrap_profile", {})
    second = queue.enqueue_with_id("bootstrap_profile", {})
    assert first and second
    expected = queue.bind_account(
        first, viewer_handle="same_user", viewer_sec_uid=initial_uid, profile_update=True
    )
    assert (
        queue.bind_account(
            second, viewer_handle="same_user", viewer_sec_uid=next_uid, profile_update=True
        )
        == expected
    )
    uid_alias = database.conn.execute(
        "SELECT value FROM auth_state WHERE key = 'tiktok_profile_sec_uid_key'"
    ).fetchone()
    assert uid_alias and str(uid_alias["value"]).startswith("sha256:")
    assert "SEC_same_user" not in str(queue.get(second)["result_json"])
    # Learning an optional identity must still reject actual secUid conflicts.
    third = queue.enqueue_with_id("bootstrap_profile", {})
    assert third
    with pytest.raises(ValueError, match="account_switch_requires_reset"):
        queue.bind_account(
            third, viewer_handle="same_user", viewer_sec_uid="SEC_different", profile_update=True
        )


def test_tiktok_task_uid_alias_survives_merges_and_missing_uid(database: Database) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_profile", {})
    assert task_id
    key = queue.bind_account(
        task_id, viewer_handle="same_user", viewer_sec_uid="SEC_same_user", profile_update=False
    )
    queue.merge_result(task_id, videos=[])
    assert (
        queue.bind_account(
            task_id, viewer_handle="same_user", viewer_sec_uid="", profile_update=False
        )
        == key
    )
    with pytest.raises(ValueError, match="task_account_mismatch"):
        queue.bind_account(
            task_id, viewer_handle="same_user", viewer_sec_uid="SEC_different", profile_update=False
        )


@pytest.mark.parametrize("clip_in_partial", [False, True])
def test_tiktok_clipped_result_stages_degraded_atomically_and_stays_immutable(
    database: Database, clip_in_partial: bool
) -> None:
    queue = TiktokTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_profile", {"scopes": ["tt_like"], "max_items_per_scope": 1}
    )
    assert task_id
    videos = [
        {"scope": "tt_like", "video_id": "123456", "title": "first"},
        {"scope": "tt_like", "video_id": "123457", "title": "over cap"},
    ]
    if clip_in_partial:
        queue.merge_result(task_id, videos=videos)
        videos = []
    canonical = queue.stage_final_result(task_id, terminal_status="ok", videos=videos)
    assert canonical["status"] == "degraded"
    assert canonical["_openbiliclaw_terminal_status"] == "degraded"
    assert canonical["scope_counts"] == {"tt_like": 1}
    # No terminal flip before event ingress repair; first frozen payload wins.
    assert queue.get(task_id)["status"] == "pending"
    assert queue.stage_final_result(task_id, terminal_status="ok", videos=[]) == canonical
    assert queue.complete_staged_result(task_id)
    assert json.loads(queue.get(task_id)["result_json"]) == canonical
