from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.saved_sync.models import (
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    SavedItemInput,
    SavedMembershipResult,
)
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _FakeBilibiliAdapter:
    capability = NativeSaveCapability(
        platform="bilibili",
        supports_favorite=True,
        supports_watch_later=True,
        supports_named_collection=True,
    )

    def __init__(self, *, status: str = "synced") -> None:
        self.status = status
        self.calls: list[str] = []

    def target_label(self, action: str) -> str:
        return "B站稍后再看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(
        self,
        item: SavedItemInput,
        route: NativeSaveRoute,
    ) -> NativeSaveResult:
        self.calls.append(item.item_key)
        if self.status == "login_required":
            return NativeSaveResult(
                item_key=item.item_key,
                status="login_required",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
                error_code="bilibili_-101",
                error_message="Bilibili login required",
            )
        return NativeSaveResult(
            item_key=item.item_key,
            status="synced",
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class _BlockingBilibiliAdapter(_FakeBilibiliAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def save(
        self,
        item: SavedItemInput,
        route: NativeSaveRoute,
    ) -> NativeSaveResult:
        self.calls.append(item.item_key)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@pytest.fixture
def saved_sync_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Database, _FakeBilibiliAdapter]]:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    config = Config()
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    save_config(config, project_root / "config.toml")

    database = Database(tmp_path / "saved-sync.db")
    database.initialize()
    adapter = _FakeBilibiliAdapter()
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=database,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    context = app.state.runtime_context
    context.config = config
    context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    yield TestClient(app), database, adapter


def _saved_item(content_id: str, **overrides: str) -> dict[str, str]:
    payload = {
        "source_platform": "bilibili",
        "content_id": content_id,
        "content_url": f"https://www.bilibili.com/video/{content_id}",
        "content_type": "video",
        "title": content_id,
        "author_name": "测试 UP",
        "cover_url": "https://i0.hdslb.com/bfs/archive/test.jpg",
        "note": "local note",
    }
    payload.update(overrides)
    return payload


def test_save_defaults_to_local_pending(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client

    response = client.post("/api/saved/watch_later", json=_saved_item("BV1LOCAL"))

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "item_key": "bilibili:BV1LOCAL",
        "sync_status": "pending",
        "sync_task_id": "",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }
    row = database.get_saved_membership("watch_later", "bilibili:BV1LOCAL")
    assert row is not None
    assert row["note"] == "local note"
    assert adapter.calls == []


def test_saved_item_normalizes_protocol_relative_urls(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    """Issue #237: protocol-relative upstream URLs must be absorbed at the API boundary."""
    client, database, _adapter = saved_sync_client
    relative_cover = "//i2.hdslb.com/bfs/archive/d242044db2f93cb56ec32f8e94bcefddb9187eb1.png"

    response = client.post(
        "/api/saved/watch_later",
        json=_saved_item(
            "BV1RELATIVE",
            content_url="//www.bilibili.com/video/BV1RELATIVE",
            cover_url=relative_cover,
        ),
    )

    assert response.status_code == 200
    assert response.json()["item_key"] == "bilibili:BV1RELATIVE"
    row = database.get_saved_membership("watch_later", "bilibili:BV1RELATIVE")
    assert row is not None
    assert row["cover_url"] == f"https:{relative_cover}"
    assert row["content_url"] == "https://www.bilibili.com/video/BV1RELATIVE"

    listing = client.get("/api/saved/watch_later?limit=20&offset=0").json()["items"]
    assert listing[0]["cover_url"] == f"https:{relative_cover}"
    assert listing[0]["content_url"] == "https://www.bilibili.com/video/BV1RELATIVE"


@pytest.mark.parametrize("content_kind", ["question", "answer", "article"])
def test_save_accepts_real_zhihu_typed_content_ids(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
    content_kind: str,
) -> None:
    client, database, adapter = saved_sync_client
    content_id = f"{content_kind}:2053077287700883000"
    item_key = f"zhihu:{content_id}"

    response = client.post(
        "/api/saved/favorite",
        json=_saved_item(
            content_id,
            source_platform="zhihu",
            content_url=f"https://www.zhihu.com/{content_kind}/2053077287700883000",
            content_type=content_kind,
            cover_url="",
        ),
    )

    assert response.status_code == 200
    assert response.json()["item_key"] == item_key
    assert response.json()["sync_status"] == "pending"
    assert database.get_saved_membership("favorite", item_key) is not None
    assert (
        client.get(
            "/api/saved/favorite/status",
            params={"item_key": item_key},
        ).json()["saved"]
        is True
    )
    assert adapter.calls == []


def test_weibo_save_is_terminal_local_only_without_native_task(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    content_id = "5023456789012345"
    item_key = f"weibo:{content_id}"

    saved = client.post(
        "/api/saved/favorite",
        json=_saved_item(
            content_id,
            source_platform="weibo",
            content_url=f"https://m.weibo.cn/detail/{content_id}",
            content_type="post",
            cover_url="",
        ),
    )

    assert saved.status_code == 200
    assert saved.json()["item_key"] == item_key
    assert saved.json()["sync_status"] == "unsupported"
    assert saved.json()["sync_task_id"] == ""
    assert saved.json()["error_code"] == "local_only_source"
    membership = database.get_saved_membership("favorite", item_key)
    assert membership is not None
    assert membership["sync_status"] == "unsupported"
    assert membership["last_error_code"] == "local_only_source"
    assert adapter.calls == []

    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": [item_key]},
    )
    assert created.status_code == 422
    assert created.json()["detail"] == "invalid sync selection"
    task_count = database.conn.execute("SELECT COUNT(*) FROM native_save_tasks").fetchone()
    assert task_count is not None
    assert task_count[0] == 0
    assert database.get_saved_membership("favorite", item_key) is not None
    assert adapter.calls == []


def test_github_repository_save_is_terminal_local_only_without_native_task(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    content_id = "repository:1296269"
    item_key = f"github:{content_id}"

    saved = client.post(
        "/api/saved/favorite",
        json=_saved_item(
            content_id,
            source_platform="github",
            content_url="https://github.com/octocat/Hello-World",
            content_type="repository",
            cover_url="",
        ),
    )

    assert saved.status_code == 200
    assert saved.json()["item_key"] == item_key
    assert saved.json()["sync_status"] == "unsupported"
    assert saved.json()["sync_task_id"] == ""
    assert saved.json()["error_code"] == "local_only_source"
    membership = database.get_saved_membership("favorite", item_key)
    assert membership is not None
    assert membership["sync_status"] == "unsupported"
    assert membership["last_error_code"] == "local_only_source"

    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": [item_key]},
    )
    assert created.status_code == 422
    assert created.json()["detail"] == "invalid sync selection"
    task_count = database.conn.execute("SELECT COUNT(*) FROM native_save_tasks").fetchone()
    assert task_count is not None
    assert task_count[0] == 0
    assert adapter.calls == []


@pytest.mark.parametrize(
    "content_id",
    ["repository:0", "repository:-1", "repo:1", "repository:1:extra"],
)
def test_github_repository_save_rejects_noncanonical_typed_ids(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
    content_id: str,
) -> None:
    client, database, adapter = saved_sync_client

    response = client.post(
        "/api/saved/favorite",
        json=_saved_item(
            content_id,
            source_platform="github",
            content_url="https://github.com/octocat/Hello-World",
            content_type="repository",
            cover_url="",
        ),
    )

    assert response.status_code == 422
    assert database.conn.execute("SELECT COUNT(*) FROM saved_memberships").fetchone()[0] == 0
    assert adapter.calls == []


def test_linuxdo_topic_save_accepts_typed_content_id(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    content_id = "topic:4242"
    item_key = f"linuxdo:{content_id}"

    saved = client.post(
        "/api/saved/watch_later",
        json=_saved_item(
            content_id,
            source_platform="linuxdo",
            content_url="https://linux.do/t/4242",
            content_type="post",
            cover_url="",
        ),
    )

    assert saved.status_code == 200
    assert saved.json()["item_key"] == item_key
    assert saved.json()["sync_status"] == "pending"
    assert database.get_saved_membership("watch_later", item_key) is not None
    assert adapter.calls == []


@pytest.mark.parametrize(
    "content_id",
    ["topic:0", "topic:-1", "post:1", "topic:1:extra", "topic:"],
)
def test_linuxdo_topic_save_rejects_noncanonical_typed_ids(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
    content_id: str,
) -> None:
    client, database, adapter = saved_sync_client

    response = client.post(
        "/api/saved/watch_later",
        json=_saved_item(
            content_id,
            source_platform="linuxdo",
            content_url="https://linux.do/t/4242",
            content_type="post",
            cover_url="",
        ),
    )

    assert response.status_code == 422
    assert database.conn.execute("SELECT COUNT(*) FROM saved_memberships").fetchone()[0] == 0
    assert adapter.calls == []


def test_auto_sync_returns_pending_task_without_waiting_for_platform_io(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    context = client.app.state.runtime_context
    context.config.saved_sync.auto_sync_enabled = True
    captured: list[object] = []

    def capture_task(_name: str, coro: object) -> object:
        captured.append(coro)
        return SimpleNamespace()

    context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
        task_starter=capture_task,  # type: ignore[arg-type]
    )

    response = client.post("/api/saved/favorite", json=_saved_item("BV1AUTO"))

    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    assert body["sync_status"] == "pending"
    assert body["sync_task_id"]
    assert adapter.calls == []
    assert len(captured) == 1
    captured[0].close()  # type: ignore[union-attr]


def test_resave_during_active_auto_sync_returns_new_coherent_noop_snapshot(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    context = client.app.state.runtime_context
    context.config.saved_sync.auto_sync_enabled = True
    captured: list[object] = []

    def capture_task(_name: str, coro: object) -> object:
        captured.append(coro)
        return SimpleNamespace()

    context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
        task_starter=capture_task,  # type: ignore[arg-type]
    )
    first = client.post("/api/saved/favorite", json=_saved_item("BV1ACTIVEAUTO"))
    assert first.json()["sync_status"] == "pending"

    repeated = client.post("/api/saved/favorite", json=_saved_item("BV1ACTIVEAUTO"))
    assert len(captured) == 1
    captured[0].close()  # type: ignore[union-attr]

    assert repeated.status_code == 200
    body = repeated.json()
    assert body["sync_task_id"] != first.json()["sync_task_id"]
    assert body["sync_status"] == "failed"
    assert body["error_code"] == "sync_already_in_progress"
    polled = client.get(f"/api/saved-sync/tasks/{body['sync_task_id']}").json()["items"][0]
    assert {
        "item_key": body["item_key"],
        "status": body["sync_status"],
        "resolved_action": body["resolved_action"],
        "resolved_target": body["resolved_target"],
        "error_code": body["error_code"],
        "error_message": body["error_message"],
    } == polled


def test_auto_sync_response_stays_pending_if_background_result_wins_race(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, _adapter = saved_sync_client
    context = client.app.state.runtime_context
    context.config.saved_sync.auto_sync_enabled = True

    class _RacingService:
        def save_local(
            self,
            list_kind: str,
            item: SavedItemInput,
            note: str,
            auto_sync: bool,
        ) -> SavedMembershipResult:
            assert auto_sync is True
            database.upsert_saved_membership(list_kind, item, note)
            database.upsert_native_save_state(
                list_kind,
                item.item_key,
                requested_action=list_kind,
                resolved_action=list_kind,
                resolved_target="B站 OpenBiliClaw 收藏夹",
                status="login_required",
                task_id="old-terminal-task",
                last_error_code="bilibili_-101",
                last_error_message="Bilibili login required",
            )
            return SavedMembershipResult(
                saved=True,
                item_key=item.item_key,
                sync_status="pending",
                sync_task_id="11111111-1111-4111-8111-111111111111",
            )

    context.saved_sync_service = _RacingService()

    response = client.post("/api/saved/favorite", json=_saved_item("BV1RACE"))

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "item_key": "bilibili:BV1RACE",
        "sync_status": "pending",
        "sync_task_id": "11111111-1111-4111-8111-111111111111",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }


def test_adapter_controlled_result_text_is_sanitized_on_every_saved_surface(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, _adapter = saved_sync_client
    controls = "\u202e\u200b\u0085"
    dirty_target = f"tar{controls}get"
    dirty_code = "\u200b" * 128 + "co\u202e\u0085de"
    dirty_message = f"mes{controls}sage"

    for list_kind in ("favorite", "watch_later"):
        item = SavedItemInput("bilibili", f"BV1SANITIZE{list_kind[0]}")
        database.upsert_saved_membership(list_kind, item)
        database.upsert_native_save_state(
            list_kind,
            item.item_key,
            requested_action=list_kind,
            resolved_action=list_kind,
            resolved_target=dirty_target,
            status="already_synced",
            last_error_code=dirty_code,
            last_error_message=dirty_message,
        )

    def assert_safe_fields(payload: dict[str, object]) -> None:
        assert payload["resolved_target"] == "target"
        assert payload["error_code"] == "code"
        assert payload["error_message"] == "message"
        serialized = str(payload)
        assert all(character not in serialized for character in controls)

    item_key = "bilibili:BV1SANITIZEf"
    assert_safe_fields(
        client.get("/api/saved/favorite/status", params={"item_key": item_key}).json()
    )
    assert_safe_fields(client.get("/api/saved/favorite").json()["items"][0])
    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": [item_key]},
    ).json()
    assert_safe_fields(created["items"][0])
    assert_safe_fields(client.get(f"/api/saved-sync/tasks/{created['task_id']}").json()["items"][0])

    assert_safe_fields(client.get("/api/favorites/BV1SANITIZEf").json())
    assert_safe_fields(client.get("/api/favorites").json()["items"][0])
    assert_safe_fields(client.get("/api/watch-later/BV1SANITIZEw").json())
    assert_safe_fields(client.get("/api/watch-later").json()["items"][0])


def test_manual_sync_ignores_auto_sync_toggle_and_poll_exposes_terminal_result(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, adapter = saved_sync_client
    assert client.app.state.runtime_context.config.saved_sync.auto_sync_enabled is False
    client.post("/api/saved/watch_later", json=_saved_item("BV1SYNC"))

    response = client.post(
        "/api/saved/watch_later/sync",
        json={"item_keys": ["bilibili:BV1SYNC"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"]
    assert body["items"] == [
        {
            "item_key": "bilibili:BV1SYNC",
            "status": "pending",
            "resolved_action": "watch_later",
            "resolved_target": "",
            "error_code": "",
            "error_message": "",
        }
    ]
    asyncio.run(client.app.state.runtime_context.saved_sync_service.run_sync_task(body["task_id"]))

    polled = client.get(f"/api/saved-sync/tasks/{body['task_id']}")
    assert polled.status_code == 200
    assert polled.json()["items"][0] == {
        "item_key": "bilibili:BV1SYNC",
        "status": "synced",
        "resolved_action": "watch_later",
        "resolved_target": "B站稍后再看",
        "error_code": "",
        "error_message": "",
    }
    assert adapter.calls == ["bilibili:BV1SYNC"]


def test_task_poll_preserves_login_required_instead_of_generic_success(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, _adapter = saved_sync_client
    adapter = _FakeBilibiliAdapter(status="login_required")
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    client.post("/api/saved/favorite", json=_saved_item("BV1LOGIN"))
    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1LOGIN"]},
    ).json()

    asyncio.run(
        client.app.state.runtime_context.saved_sync_service.run_sync_task(created["task_id"])
    )

    item = client.get(f"/api/saved-sync/tasks/{created['task_id']}").json()["items"][0]
    assert item["status"] == "login_required"
    assert item["error_code"] == "bilibili_-101"
    assert item["error_message"] == "Bilibili login required"


def test_manual_sync_reports_missing_memberships_per_item_without_claiming_them(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, _adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1PRESENT"))

    response = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1PRESENT", "bilibili:BV1MISSING"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"]
    assert {item["item_key"]: item["status"] for item in body["items"]} == {
        "bilibili:BV1PRESENT": "pending",
        "bilibili:BV1MISSING": "failed",
    }
    missing = next(item for item in body["items"] if item["item_key"].endswith("MISSING"))
    assert missing["error_code"] == "not_saved_locally"
    assert missing["error_message"] == "Item is not saved locally"


def test_partial_missing_batch_poll_matches_creation_after_service_reconstruction(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1DURABLE"))

    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1DURABLE", "bilibili:BV1ABSENT"]},
    )
    assert created.status_code == 200
    assert created.json()["task_id"]

    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    polled = client.get(f"/api/saved-sync/tasks/{created.json()['task_id']}")

    assert polled.status_code == 200
    assert polled.json() == created.json()


def test_all_missing_batch_has_stable_uuid_and_pollable_item_set(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client

    created = client.post(
        "/api/saved/watch_later/sync",
        json={"item_keys": ["bilibili:BV1MISS1", "youtube:missing-2"]},
    )

    assert created.status_code == 200
    uuid.UUID(created.json()["task_id"])
    assert [item["error_code"] for item in created.json()["items"]] == [
        "not_saved_locally",
        "not_saved_locally",
    ]
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    assert client.get(f"/api/saved-sync/tasks/{created.json()['task_id']}").json() == created.json()


def test_empty_all_batch_is_pollable_and_distinct_from_unknown_uuid(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client

    created = client.post("/api/saved/favorite/sync", json={"item_keys": []})

    assert created.status_code == 200
    uuid.UUID(created.json()["task_id"])
    assert created.json()["items"] == []
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    assert client.get(f"/api/saved-sync/tasks/{created.json()['task_id']}").json() == created.json()
    assert client.get(f"/api/saved-sync/tasks/{uuid.uuid4()}").status_code == 404


def test_already_synced_selection_is_a_durable_terminal_noop(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1DONE"))
    database.upsert_native_save_state(
        "favorite",
        "bilibili:BV1DONE",
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status="already_synced",
        task_id="older-task",
    )

    created = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1DONE"]},
    )

    assert created.status_code == 200
    uuid.UUID(created.json()["task_id"])
    assert created.json()["items"][0]["status"] == "already_synced"
    assert adapter.calls == []
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    assert client.get(f"/api/saved-sync/tasks/{created.json()['task_id']}").json() == created.json()


def test_all_eligible_with_only_terminal_memberships_is_a_pollable_empty_task(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1NOELIGIBLE"))
    database.upsert_native_save_state(
        "favorite",
        "bilibili:BV1NOELIGIBLE",
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status="already_synced",
    )

    created = client.post("/api/saved/favorite/sync", json={"item_keys": []})

    assert created.status_code == 200
    uuid.UUID(created.json()["task_id"])
    assert created.json()["items"] == []
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    assert client.get(f"/api/saved-sync/tasks/{created.json()['task_id']}").json() == created.json()


def test_active_owner_selection_is_a_durable_terminal_noop(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1ACTIVE"))
    owner = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1ACTIVE"]},
    ).json()

    duplicate = client.post(
        "/api/saved/favorite/sync",
        json={"item_keys": ["bilibili:BV1ACTIVE"]},
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["task_id"] != owner["task_id"]
    assert duplicate.json()["items"][0]["status"] == "failed"
    assert duplicate.json()["items"][0]["error_code"] == "sync_already_in_progress"
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    assert (
        client.get(f"/api/saved-sync/tasks/{duplicate.json()['task_id']}").json()
        == duplicate.json()
    )


def test_claimed_batch_survives_membership_removal_and_service_reconstruction(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, database, adapter = saved_sync_client
    client.post("/api/saved/watch_later", json=_saved_item("BV1REMOVED"))
    created = client.post(
        "/api/saved/watch_later/sync",
        json={"item_keys": ["bilibili:BV1REMOVED"]},
    ).json()

    client.post(
        "/api/saved/watch_later/remove",
        json={"item_key": "bilibili:BV1REMOVED"},
    )
    client.app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
    )
    polled = client.get(f"/api/saved-sync/tasks/{created['task_id']}")

    assert polled.status_code == 200
    assert [item["item_key"] for item in polled.json()["items"]] == ["bilibili:BV1REMOVED"]
    assert polled.json()["items"][0]["status"] == "failed"
    assert polled.json()["items"][0]["error_code"] == "not_saved_locally"


def test_saved_list_status_and_local_only_remove_round_trip(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, adapter = saved_sync_client
    client.post("/api/saved/favorite", json=_saved_item("BV1CRUD"))

    status = client.get(
        "/api/saved/favorite/status",
        params={"item_key": "bilibili:BV1CRUD"},
    )
    listing = client.get("/api/saved/favorite?limit=20&offset=0")
    removed = client.post(
        "/api/saved/favorite/remove",
        json={"item_key": "bilibili:BV1CRUD"},
    )

    assert status.status_code == 200
    assert status.json()["saved"] is True
    assert status.json()["sync_status"] == "pending"
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0] == {
        "item_key": "bilibili:BV1CRUD",
        "source_platform": "bilibili",
        "content_id": "BV1CRUD",
        "content_url": "https://www.bilibili.com/video/BV1CRUD",
        "content_type": "video",
        "title": "BV1CRUD",
        "author_name": "测试 UP",
        "cover_url": "https://i0.hdslb.com/bfs/archive/test.jpg",
        "note": "local note",
        "added_at": listing.json()["items"][0]["added_at"],
        "sync_status": "pending",
        "sync_task_id": "",
        "requested_action": "favorite",
        "resolved_action": "",
        "resolved_target": "",
        "error_code": "",
        "error_message": "",
    }
    assert removed.status_code == 200
    assert removed.json()["saved"] is False
    assert (
        client.get(
            "/api/saved/favorite/status",
            params={"item_key": "bilibili:BV1CRUD"},
        ).json()["saved"]
        is False
    )
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/api/saved/queue", {"json": _saved_item("BV1BAD")}),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": " ", "content_id": "BV1BAD"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bad platform", "content_id": "BV1BAD"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bilibili", "content_id": ""}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {**_saved_item("BV1BAD"), "unexpected": "field"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "not-an-item-key"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "bilibili:extra:colon"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "zhihu:question:"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "zhihu:unknown:123"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "bilibili:url:ABCDEF0123456789ABCDEF01"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "bilibili:url:0123456789abcdef0123456"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "bilibili:space id"}},
        ),
        (
            "post",
            "/api/saved/favorite/remove",
            {"json": {"item_key": "bilibili:hidden\u200bid"}},
        ),
        (
            "get",
            "/api/saved/favorite/status",
            {"params": {"item_key": " "}},
        ),
        (
            "post",
            "/api/saved/favorite/sync",
            {"json": {"item_keys": ["bilibili:BV1OK", " "]}},
        ),
        ("get", "/api/saved-sync/tasks/not-a-uuid", {}),
        ("get", "/api/favorites/%20", {}),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bilibili", "content_id": " leading"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bilibili", "content_id": "bad id"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {"json": {"source_platform": "bilibili", "content_id": "bad\u0085id"}},
        ),
        (
            "post",
            "/api/saved/favorite",
            {
                "json": {
                    "source_platform": "bilibili",
                    "content_id": "",
                    "content_url": "https://user:secret@example.com/video",
                }
            },
        ),
        (
            "post",
            "/api/saved/favorite",
            {
                "json": {
                    "source_platform": "bilibili",
                    "content_id": "",
                    "content_url": "https://example.com:99999/video",
                }
            },
        ),
        (
            "post",
            "/api/saved/favorite",
            {
                "json": {
                    "source_platform": "bilibili",
                    "content_id": "",
                    "content_url": "https://bad..example/video",
                }
            },
        ),
        (
            "post",
            "/api/saved/favorite",
            {
                "json": {
                    "source_platform": "bilibili",
                    "content_id": "",
                    "content_url": "https://example.com/white space",
                }
            },
        ),
    ],
)
def test_saved_routes_reject_invalid_identifiers_and_selections(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
    method: str,
    path: str,
    kwargs: dict[str, object],
) -> None:
    client, database, adapter = saved_sync_client

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 422
    assert database.count_favorites() == 0
    assert database.count_watch_later() == 0
    assert adapter.calls == []


def test_saved_url_fallback_emits_exact_lowercase_hash_key(
    saved_sync_client: tuple[TestClient, Database, _FakeBilibiliAdapter],
) -> None:
    client, _database, _adapter = saved_sync_client

    response = client.post(
        "/api/saved/favorite",
        json={
            "source_platform": "reddit",
            "content_id": "",
            "content_url": "https://www.reddit.com/r/python/comments/abc/example",
            "content_type": "post",
        },
    )

    assert response.status_code == 200
    item_key = response.json()["item_key"]
    assert item_key.startswith("reddit:url:")
    assert len(item_key) == len("reddit:url:") + 24
    assert item_key.removeprefix("reddit:url:") == item_key.removeprefix("reddit:url:").lower()


async def test_runtime_rebuild_cancels_inflight_saved_sync_before_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config

    database = Database(tmp_path / "runtime-cancel.db")
    database.initialize()
    context = RuntimeContext(database=database)
    adapter = _BlockingBilibiliAdapter()
    old_service = SavedSyncService(
        database,
        NativeSaveRouter([adapter]),
        task_starter=lambda name, coro: context.task_registry.track(name, coro),
    )
    context.saved_sync_service = old_service
    old_service.save_local("favorite", SavedItemInput("bilibili", "BV1CANCEL"))
    created = old_service.create_sync_task(
        "favorite",
        ["bilibili:BV1CANCEL"],
        "manual_single",
    )
    await adapter.started.wait()
    replacement = object()

    def fake_rebuild(self: RuntimeContext, config: Config) -> None:
        del config
        assert adapter.cancelled.is_set()
        self.saved_sync_service = replacement

    monkeypatch.setattr(RuntimeContext, "_rebuild_components", fake_rebuild)

    await context.rebuild_from_config(Config())

    assert context.saved_sync_service is replacement
    assert old_service.get_sync_task(created.task_id).items[0].status == "failed"
    assert old_service.get_sync_task(created.task_id).items[0].error_code == "interrupted"


async def test_runtime_rebuild_hands_claimed_extension_job_to_detached_watchdog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext
    from openbiliclaw.config import Config
    from openbiliclaw.saved_sync.adapters.extension import (
        build_extension_native_save_adapters,
    )
    from openbiliclaw.saved_sync.extension_broker import (
        ExtensionNativeSaveBroker,
        ExtensionNativeSaveResultIn,
    )

    database = Database(tmp_path / "runtime-claimed-extension.db")
    database.initialize()
    context = RuntimeContext(database=database)
    broker = ExtensionNativeSaveBroker(
        database,
        wake_platform=AsyncMock(),
        dispatch_deadline_seconds=1.0,
        execution_deadline_seconds=1.0,
        poll_interval_seconds=0.001,
    )
    context.extension_native_save_broker = broker
    old_service = SavedSyncService(
        database,
        NativeSaveRouter(build_extension_native_save_adapters(broker)),
        task_starter=lambda name, coro: context.task_registry.track(name, coro),
        claim_heartbeat_interval_seconds=0.005,
    )
    context.saved_sync_service = old_service
    item = SavedItemInput("reddit", "t3_rebuild", "https://www.reddit.com/comments/rebuild/")
    old_service.save_local("favorite", item)
    created = old_service.create_sync_task("favorite", [item.item_key], "manual_single")

    claimed = None
    for _ in range(100):
        claimed = broker.claim_next("reddit")
        if claimed is not None:
            break
        await asyncio.sleep(0.001)
    assert claimed is not None

    replacement: SavedSyncService | None = None

    def fake_rebuild(self: RuntimeContext, config: Config) -> None:
        nonlocal replacement
        del config
        assert self.extension_native_save_broker is broker
        replacement = SavedSyncService(
            database,
            NativeSaveRouter(build_extension_native_save_adapters(broker)),
        )
        self.saved_sync_service = replacement

    monkeypatch.setattr(RuntimeContext, "_rebuild_components", fake_rebuild)

    await context.rebuild_from_config(Config())

    assert context.saved_sync_service is replacement
    assert len(old_service._detached_attempts) == 1
    assert broker.submit_result(
        "reddit",
        ExtensionNativeSaveResultIn(claimed.job_id, item.item_key, "already_synced"),
    )
    for _ in range(100):
        persisted = old_service.get_sync_task(created.task_id)
        if persisted.items[0].status == "already_synced":
            break
        await asyncio.sleep(0.001)
    assert persisted.items[0].status == "already_synced"
    assert persisted.items[0].error_code == ""
    assert (
        database.conn.execute("SELECT COUNT(*) FROM extension_native_save_jobs").fetchone()[0] == 1
    )
    assert broker.claim_next("reddit") is None


def test_runtime_construction_failure_keeps_existing_saved_sync_and_bilibili_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config
    from openbiliclaw.recommendation import engine as recommendation_module

    config = Config(data_dir=str(tmp_path / "runtime-atomic"))
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    context = build_runtime_context(config)
    old_service = context.saved_sync_service
    old_client = context.bilibili_client

    class _ConstructionFailureError(RuntimeError):
        pass

    def fail_recommendation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise _ConstructionFailureError("late component failed")

    monkeypatch.setattr(recommendation_module, "RecommendationEngine", fail_recommendation)

    with pytest.raises(_ConstructionFailureError, match="late component failed"):
        context._rebuild_components(config)

    assert context.saved_sync_service is old_service
    assert context.bilibili_client is old_client


async def test_runtime_context_rebuilds_tracked_bilibili_saved_sync_service(
    tmp_path: Path,
) -> None:
    from openbiliclaw.api.runtime_context import build_runtime_context
    from openbiliclaw.config import Config

    config = Config(data_dir=str(tmp_path / "runtime-data"))
    config.scheduler.enabled = False
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    context = build_runtime_context(config)
    first_service = context.saved_sync_service
    first_client = context.bilibili_client
    first_broker = context.extension_native_save_broker

    local = first_service.save_local(
        "favorite",
        SavedItemInput("bilibili", "BV1RUNTIME"),
        auto_sync=False,
    )
    created = first_service.create_sync_task(
        "favorite",
        [local.item_key],
        "manual_single",
    )
    for _ in range(100):
        result = first_service.get_sync_task(created.task_id)
        if (
            result.items
            and result.items[0].status not in {"pending", "syncing"}
            and context.task_registry.stats() == {}
        ):
            break
        await asyncio.sleep(0.01)

    assert result.items[0].status == "login_required"
    assert context.task_registry.stats() == {}

    await context.rebuild_from_config(config)

    assert context.saved_sync_service is not first_service
    assert context.bilibili_client is not first_client
    assert context.extension_native_save_broker is first_broker
    bilibili_adapter, _ = context.saved_sync_service._router.route("bilibili", "favorite")
    assert bilibili_adapter.__class__.__name__ == "BilibiliNativeSaveAdapter"
    extension_adapter, _ = context.saved_sync_service._router.route("youtube", "favorite")
    assert extension_adapter.capability.requires_extension is True


async def test_local_runtime_registers_extension_adapters_without_event_hub(
    tmp_path: Path,
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    database = Database(tmp_path / "local-extension.db")
    database.initialize()

    context = RuntimeContext(database=database)

    adapter, route = context.saved_sync_service._router.route("twitter", "watch_later")
    assert context.extension_native_save_broker is not None
    assert adapter.capability.requires_extension is True
    assert route.resolved_action == "favorite"


async def test_runtime_broker_wake_publishes_source_task_available_best_effort(
    tmp_path: Path,
) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    database = Database(tmp_path / "wake-extension.db")
    database.initialize()
    event_hub = SimpleNamespace(publish=AsyncMock())
    context = RuntimeContext(database=database, event_hub=event_hub)

    await context.extension_native_save_broker._wake_platform("yt")

    event_hub.publish.assert_awaited_once_with({"type": "yt_task_available"})
