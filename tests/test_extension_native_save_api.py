from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.saved_sync.extension_broker import ExtensionNativeSaveBroker
from openbiliclaw.saved_sync.models import NativeSaveRoute, SavedItemInput
from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
from openbiliclaw.sources.xhs_tasks import XhsTaskQueue
from openbiliclaw.storage.database import Database

_NATIVE_CASES = {
    "xhs": (
        SavedItemInput(
            "xiaohongshu",
            "note-123",
            "https://www.xiaohongshu.com/explore/note-123?xsec_token=secret",
            "note",
        ),
        NativeSaveRoute("favorite", "favorite", "Xiaohongshu Favorites"),
    ),
    "dy": (
        SavedItemInput("douyin", "aweme-123", "https://www.douyin.com/video/aweme-123"),
        NativeSaveRoute("favorite", "favorite", "Douyin Favorites"),
    ),
    "yt": (
        SavedItemInput("youtube", "video-123", "https://www.youtube.com/watch?v=video-123"),
        NativeSaveRoute("watch_later", "watch_later", "YouTube Watch Later"),
    ),
    "x": (
        SavedItemInput("twitter", "123", "https://x.com/example/status/123", "post"),
        NativeSaveRoute("favorite", "favorite", "X Bookmarks"),
    ),
    "zhihu": (
        SavedItemInput(
            "zhihu", "answer:123", "https://www.zhihu.com/question/1/answer/123", "answer"
        ),
        NativeSaveRoute("favorite", "favorite", "Zhihu Favorites"),
    ),
    "reddit": (
        SavedItemInput(
            "reddit",
            "t3_abc",
            "https://www.reddit.com/r/test/comments/abc/demo/",
            "post",
        ),
        NativeSaveRoute("favorite", "favorite", "Reddit Saved"),
    ),
}


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "native-save-api.db")
    db.initialize()
    return db


@pytest.fixture
def event_hub() -> SimpleNamespace:
    return SimpleNamespace(publish=AsyncMock())


@pytest.fixture
def broker(database: Database) -> ExtensionNativeSaveBroker:
    return ExtensionNativeSaveBroker(database, wake_platform=AsyncMock())


@pytest.fixture
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database: Database,
    broker: ExtensionNativeSaveBroker,
    event_hub: SimpleNamespace,
) -> TestClient:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    config = Config()
    config.scheduler.enabled = False
    save_config(config, project_root / "config.toml")
    memory = SimpleNamespace(
        load_discovery_runtime_state=lambda: {},
        load_cognition_updates=lambda: [],
        propagate_event=AsyncMock(),
    )
    app = create_app(
        memory_manager=memory,
        database=database,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
        runtime_event_hub=event_hub,
    )
    app.state.runtime_context.extension_native_save_broker = broker
    return TestClient(app)


def enqueue_native_job(broker: ExtensionNativeSaveBroker, slug: str) -> str:
    item, route = _NATIVE_CASES[slug]
    return broker.enqueue(item, route)


@pytest.mark.parametrize("slug", tuple(_NATIVE_CASES))
def test_next_task_serves_exact_native_job_shape(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    slug: str,
) -> None:
    item, route = _NATIVE_CASES[slug]
    job_id = enqueue_native_job(broker, slug)

    response = client.get(f"/api/sources/{slug}/next-task")

    assert response.status_code == 200
    assert response.json() == {
        "id": job_id,
        "type": "native_save",
        "item_key": item.item_key,
        "platform": item.platform,
        "platform_slug": slug,
        "content_id": item.content_id,
        "content_url": item.content_url,
        "content_type": item.content_type,
        "requested_action": route.requested_action,
        "resolved_action": route.resolved_action,
        "target_label": route.resolved_target,
    }


@pytest.mark.parametrize("slug", tuple(_NATIVE_CASES))
def test_empty_source_queue_returns_bodyless_204(client: TestClient, slug: str) -> None:
    response = client.get(f"/api/sources/{slug}/next-task")

    assert response.status_code == 204
    assert response.content == b""


def test_native_job_has_priority_without_breaking_reddit_discovery(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
) -> None:
    queue = RedditTaskQueue(database)
    legacy_id = queue.enqueue_with_id("search", {"query": "python"}, daily_budget=0)
    native_id = enqueue_native_job(broker, "reddit")

    assert client.get("/api/sources/reddit/next-task").json()["id"] == native_id
    assert client.get("/api/sources/reddit/next-task").json() == {
        "id": legacy_id,
        "type": "search",
        "query": "python",
    }
    response = client.post(
        "/api/sources/reddit/task-result",
        json={"task_id": legacy_id, "status": "ok", "items": []},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert queue.get(str(legacy_id))["status"] == "completed"


@pytest.mark.parametrize("slug", tuple(_NATIVE_CASES))
def test_native_result_round_trips_for_every_source(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
    slug: str,
) -> None:
    job_id = enqueue_native_job(broker, slug)
    claimed = client.get(f"/api/sources/{slug}/next-task").json()

    response = client.post(
        f"/api/sources/{slug}/task-result",
        json={
            "task_id": job_id,
            "item_key": claimed["item_key"],
            "status": "already_synced",
            "error_code": "",
            "error_message": "",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    row = database.get_extension_native_save_job(job_id)
    assert row is not None
    assert row["status"] == "already_synced"


def test_xhs_native_rate_limit_pauses_follow_up_platform_tasks(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
) -> None:
    job_id = enqueue_native_job(broker, "xhs")
    claimed = client.get("/api/sources/xhs/next-task").json()

    response = client.post(
        "/api/sources/xhs/task-result",
        json={
            "task_id": job_id,
            "item_key": claimed["item_key"],
            "status": "rate_limited",
            "error_code": "",
            "error_message": "",
        },
    )

    assert response.status_code == 200
    assert XhsTaskQueue(database).runtime_state()["rate_limited"] is True
    enqueue_native_job(broker, "xhs")
    blocked = client.get("/api/sources/xhs/next-task")
    assert blocked.status_code == 204
    assert int(blocked.headers["retry-after"]) > 0


def test_owned_native_result_never_falls_through_to_legacy_queue(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
) -> None:
    job_id = enqueue_native_job(broker, "reddit")
    claimed = broker.claim_next("reddit")
    assert claimed is not None
    database.conn.execute(
        "INSERT INTO reddit_tasks (id, type, payload_json) VALUES (?, 'search', '{}')",
        (job_id,),
    )
    database.conn.commit()

    response = client.post(
        "/api/sources/reddit/task-result",
        json={
            "task_id": job_id,
            "item_key": "reddit:t3_wrong",
            "status": "already_synced",
            "error_code": "",
            "error_message": "",
        },
    )

    assert response.status_code == 409
    assert RedditTaskQueue(database).get(job_id)["status"] == "pending"


def test_wrong_item_key_and_late_callback_return_conflict(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
) -> None:
    job_id = enqueue_native_job(broker, "zhihu")
    claimed = broker.claim_next("zhihu")
    assert claimed is not None
    payload = {
        "task_id": job_id,
        "item_key": "zhihu:answer:wrong",
        "status": "synced",
        "error_code": "",
        "error_message": "",
    }
    assert client.post("/api/sources/zhihu/task-result", json=payload).status_code == 409

    payload["item_key"] = claimed.item_key
    assert client.post("/api/sources/zhihu/task-result", json=payload).status_code == 200
    assert client.post("/api/sources/zhihu/task-result", json=payload).status_code == 409


def test_unknown_result_reaches_legacy_only_when_queue_owns_id(
    client: TestClient,
    database: Database,
) -> None:
    queue = RedditTaskQueue(database)
    legacy_id = queue.enqueue_with_id("search", {}, daily_budget=0)
    assert legacy_id is not None

    owned = client.post(
        "/api/sources/reddit/task-result",
        json={"task_id": legacy_id, "status": "empty", "items": []},
    )
    unknown = client.post(
        "/api/sources/reddit/task-result",
        json={"task_id": str(uuid4()), "status": "empty", "items": []},
    )

    assert owned.status_code == 200
    assert unknown.status_code == 409


def test_x_rejects_malformed_result(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
) -> None:
    job_id = enqueue_native_job(broker, "x")
    claimed = broker.claim_next("x")
    assert claimed is not None

    response = client.post(
        "/api/sources/x/task-result",
        json={"task_id": job_id, "item_key": claimed.item_key, "status": "made_up"},
    )

    assert response.status_code == 422


def test_x_rejects_unknown_result(client: TestClient) -> None:
    response = client.post(
        "/api/sources/x/task-result",
        json={
            "task_id": str(uuid4()),
            "item_key": "twitter:1",
            "status": "synced",
            "error_code": "",
            "error_message": "",
        },
    )

    assert response.status_code == 409


def test_native_result_cannot_complete_job_through_wrong_source(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
) -> None:
    job_id = enqueue_native_job(broker, "reddit")
    claimed = broker.claim_next("reddit")
    assert claimed is not None

    response = client.post(
        "/api/sources/x/task-result",
        json={
            "task_id": job_id,
            "item_key": claimed.item_key,
            "status": "synced",
            "error_code": "",
            "error_message": "",
        },
    )

    assert response.status_code == 409
    row = database.get_extension_native_save_job(job_id)
    assert row is not None
    assert row["status"] == "in_progress"


def test_cross_slug_native_legacy_uuid_collision_mutates_neither_row(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    database: Database,
) -> None:
    job_id = enqueue_native_job(broker, "reddit")
    claimed = broker.claim_next("reddit")
    assert claimed is not None
    database.conn.execute(
        "INSERT INTO xhs_tasks (id, type, payload_json) VALUES (?, 'search', '{}')",
        (job_id,),
    )
    database.conn.commit()

    response = client.post(
        "/api/sources/xhs/task-result",
        json={
            "task_id": job_id,
            "item_key": claimed.item_key,
            "status": "ok",
            "notes": [],
        },
    )

    assert response.status_code == 409
    native_row = database.get_extension_native_save_job(job_id)
    legacy_row = database.conn.execute(
        "SELECT status, result_json FROM xhs_tasks WHERE id = ?", (job_id,)
    ).fetchone()
    assert native_row is not None
    assert native_row["status"] == "in_progress"
    assert legacy_row is not None
    assert legacy_row["status"] == "pending"
    assert legacy_row["result_json"] is None


@pytest.mark.parametrize(
    ("slug", "legacy_collection"),
    [
        ("xhs", "notes"),
        ("dy", "videos"),
        ("reddit", "items"),
        ("zhihu", "items"),
        ("yt", "items"),
    ],
)
def test_owned_native_result_validates_before_touching_legacy_collections(
    client: TestClient,
    broker: ExtensionNativeSaveBroker,
    slug: str,
    legacy_collection: str,
) -> None:
    job_id = enqueue_native_job(broker, slug)
    claimed = broker.claim_next(slug)
    assert claimed is not None

    response = client.post(
        f"/api/sources/{slug}/task-result",
        json={
            "task_id": job_id,
            "item_key": claimed.item_key,
            "status": "synced",
            "error_code": "",
            "error_message": "",
            legacy_collection: None,
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("slug", tuple(_NATIVE_CASES))
def test_kick_publishes_exact_source_event(
    client: TestClient,
    event_hub: SimpleNamespace,
    slug: str,
) -> None:
    event_hub.publish.reset_mock()

    response = client.post(f"/api/sources/{slug}/kick")

    assert response.json() == {"ok": True}
    event_hub.publish.assert_awaited_once_with(
        {"type": f"{slug}_task_available", "source": "task_kick"}
    )


def test_native_save_source_api_is_documented() -> None:
    required = {
        "docs/modules/runtime.md": "extension_native_save_broker",
        "docs/modules/saved-sync.md": "global native ownership",
        "docs/modules/init.md": "legacy discovery/bootstrap queues",
        "docs/modules/api-auth.md": "seven state-changing GET `/next-task`",
        "docs/architecture.md": "/api/sources/{xhs,dy,yt,x,zhihu,reddit}",
        "docs/spec.md": "native_save multiplex",
        "docs/architecture-overview.md": "七源 source task multiplex",
        "docs/architecture-overview.en.md": "seven-source task multiplex",
        "docs/changelog.md": "source task multiplex",
    }
    for path, marker in required.items():
        assert marker in Path(path).read_text(encoding="utf-8"), path
