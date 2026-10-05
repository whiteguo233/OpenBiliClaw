"""Integration regressions for the Linux.do extension-backed API surface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.sources.linuxdo_tasks import LinuxdoTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

_ACCOUNT_KEY = "sha256:" + "a" * 64
_OTHER_ACCOUNT_KEY = "sha256:" + "b" * 64


def _topic(scope: str, topic_id: int, **extra: object) -> dict[str, object]:
    return {
        "scope": scope,
        "content_type": "post",
        "topic_id": topic_id,
        "content_id": f"topic:{topic_id}",
        "title": f"topic {topic_id}",
        **extra,
    }


class _ReadySoul:
    def is_profile_ready(self) -> bool:
        return True


@dataclass
class _EventHub:
    events: list[dict[str, object]]

    async def publish(self, payload: dict[str, object]) -> None:
        self.events.append(payload)


@pytest.fixture
def linuxdo_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Database, MemoryManager, _EventHub, Config]:
    cfg = Config(data_dir=str(tmp_path / "data"))
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    cfg.llm.ollama.base_url = "http://localhost:11434"
    cfg.sources.linuxdo.enabled = True
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr("openbiliclaw.config.save_config", lambda *_a, **_kw: None)

    database = Database(tmp_path / "linuxdo-api.db")
    database.initialize()
    memory = MemoryManager(tmp_path / "memory")
    memory.initialize()
    hub = _EventHub(events=[])
    app = create_app(
        database=database,
        memory_manager=memory,
        soul_engine=_ReadySoul(),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
        runtime_event_hub=hub,
    )
    runtime_context = app.state.runtime_context
    runtime_context.config = cfg

    async def _rebuild_from_config(new_config: Config) -> None:
        runtime_context.config = new_config

    async def _restart_background_tasks(
        _app: object,
        **_kwargs: object,
    ) -> None:
        return None

    runtime_context.rebuild_from_config = _rebuild_from_config
    runtime_context.restart_background_tasks = _restart_background_tasks
    return TestClient(app), database, memory, hub, cfg


def test_linuxdo_discovery_task_records_result_without_profile_propagation_and_ignores_retry(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)

    assert client.get("/api/sources/linuxdo/next-task").status_code == 204
    task_id = queue.enqueue_with_id(
        "search",
        {
            "keywords": ["本地大模型"],
            "max_items_per_keyword": 3,
            "source_keyword_ids": {"本地大模型": 17},
        },
        daily_budget=10,
    )
    assert task_id is not None

    claimed = client.get("/api/sources/linuxdo/next-task")
    assert claimed.status_code == 200
    claimed_payload = claimed.json()
    claim_token = claimed_payload.pop("claim_token")
    assert isinstance(claim_token, str) and claim_token
    assert claimed_payload == {
        "id": task_id,
        "type": "search",
        "keywords": ["本地大模型"],
        "max_items_per_keyword": 3,
        "source_keyword_ids": {"本地大模型": 17},
    }

    forged_keyword = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": claim_token,
            "status": "ok",
            "items": [
                {
                    "scope": "linuxdo_search",
                    "content_type": "post",
                    "topic_id": 100,
                    "search_keyword": "本地大模型",
                    "source_keyword_id": 999,
                }
            ],
            "scope_counts": {"linuxdo_search": 1},
            "response_observed": True,
            "complete_scopes": ["linuxdo_search"],
        },
    )
    assert forged_keyword.status_code == 422
    assert forged_keyword.json()["detail"] == "source_keyword_id_mismatch"

    first = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": claim_token,
            "status": "ok",
            "items": [
                {
                    "scope": "linuxdo_search",
                    "content_type": "post",
                    "topic_id": 101,
                    "title": "Linux.do discovery row",
                    "url": "https://linux.do/t/discovery-row/101",
                    "search_keyword": "本地大模型",
                    "source_keyword_id": 17,
                }
            ],
            "scope_counts": {"linuxdo_search": 1},
            "response_observed": True,
            "complete_scopes": ["linuxdo_search"],
        },
    )
    assert first.status_code == 200
    assert first.json() == {"ok": True}
    stored = queue.get(task_id)
    assert stored is not None
    assert stored["status"] == "completed"
    canonical = json.loads(str(stored["result_json"]))
    assert canonical["items"][0]["topic_id"] == 101
    assert memory.query_events(limit=20) == []

    retried = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": claim_token,
            "status": "failed",
            "items": [
                {
                    "scope": "linuxdo_search",
                    "topic_id": 999,
                    "title": "changed retry must be ignored",
                }
            ],
            "error": "late retry",
        },
    )
    assert retried.status_code == 200
    assert retried.json() == {"ok": True, "ignored": True}
    unchanged = json.loads(str(queue.get(task_id)["result_json"]))
    assert unchanged == canonical
    assert memory.query_events(limit=20) == []


@pytest.mark.parametrize("ownership_flag", ("profile_update", "incremental"))
def test_linuxdo_bootstrap_task_propagates_authorized_profile_signals(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
    ownership_flag: str,
) -> None:
    client, database, memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    topic_id = 201 if ownership_flag == "profile_update" else 202
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "scopes": ["linuxdo_bookmarks"],
            "max_items_per_scope": 20,
            ownership_flag: True,
        },
        daily_budget=10,
    )
    assert task_id is not None
    claimed = client.get("/api/sources/linuxdo/next-task")
    assert claimed.status_code == 200
    claimed_payload = claimed.json()
    assert claimed_payload[ownership_flag] is True
    claim_token = claimed_payload["claim_token"]

    response = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": claim_token,
            "status": "ok",
            "account_key": _ACCOUNT_KEY,
            "response_observed": True,
            "complete_scopes": ["linuxdo_bookmarks"],
            "items": [
                {
                    "scope": "linuxdo_bookmarks",
                    "content_type": "post",
                    "topic_id": topic_id,
                    "title": f"Linux.do bootstrap {ownership_flag}",
                    "author": "alice",
                    "views": 12,
                    "like_count": 3,
                    "reply_count": 2,
                }
            ],
            "scope_counts": {"linuxdo_bookmarks": 1},
        },
    )

    assert response.status_code == 200
    assert queue.get(task_id)["status"] == "completed"
    events = memory.query_events(limit=20)
    assert len(events) == 1
    assert events[0]["event_type"] == "favorite"
    assert events[0]["url"] == f"https://linux.do/t/{topic_id}"
    metadata = json.loads(str(events[0]["metadata"]))
    assert metadata["source_platform"] == "linuxdo"
    assert metadata["content_type"] == "post"
    assert metadata["content_id"] == f"topic:{topic_id}"
    assert metadata["source_account_key"] == _ACCOUNT_KEY
    assert metadata["profile_update_owner"] == "generic"
    assert memory.load_source_bootstrap_state()["linuxdo_seen_item_keys"] == [
        f"{_ACCOUNT_KEY}:linuxdo_bookmarks:topic:{topic_id}"
    ]
    assert memory.load_source_bootstrap_state()["linuxdo_account_key"] == _ACCOUNT_KEY


def test_linuxdo_kick_broadcasts_runtime_event(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, _database, _memory, hub, _cfg = linuxdo_api

    response = client.post("/api/sources/linuxdo/kick")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert hub.events == [{"type": "linuxdo_task_available", "source": "task_kick"}]


def test_linuxdo_result_rejects_scope_cap_and_false_empty(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, _memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"scopes": ["linuxdo_bookmarks"], "max_items_per_scope": 1},
        daily_budget=10,
    )
    assert task_id
    token = client.get("/api/sources/linuxdo/next-task").json()["claim_token"]

    unauthorized = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "ok",
            "account_key": _ACCOUNT_KEY,
            "response_observed": True,
            "complete_scopes": ["linuxdo_bookmarks"],
            "items": [_topic("linuxdo_likes", 1)],
            "scope_counts": {"linuxdo_likes": 1},
        },
    )
    assert unauthorized.status_code == 422
    assert unauthorized.json()["detail"] == "unauthorized_scope"

    over_cap = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "ok",
            "account_key": _ACCOUNT_KEY,
            "response_observed": True,
            "complete_scopes": ["linuxdo_bookmarks"],
            "items": [
                _topic("linuxdo_bookmarks", 1),
                _topic("linuxdo_bookmarks", 2),
            ],
            "scope_counts": {"linuxdo_bookmarks": 2},
        },
    )
    assert over_cap.status_code == 422
    assert over_cap.json()["detail"] == "task_result_cap_exceeded"

    wrong_action = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "ok",
            "account_key": _ACCOUNT_KEY,
            "response_observed": True,
            "complete_scopes": ["linuxdo_bookmarks"],
            "items": [
                _topic(
                    "linuxdo_bookmarks",
                    1,
                    interaction_action="like",
                )
            ],
            "scope_counts": {"linuxdo_bookmarks": 1},
        },
    )
    assert wrong_action.status_code == 422
    assert wrong_action.json()["detail"] == "interaction_action_mismatch"

    false_empty = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "empty",
            "account_key": _ACCOUNT_KEY,
            "items": [],
            "scope_counts": {},
        },
    )
    assert false_empty.status_code == 422
    assert false_empty.json()["detail"] == "response_not_observed"
    assert queue.get(task_id)["status"] == "in_progress"


def test_linuxdo_formal_paginated_result_requires_exact_valid_next_cursor(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, _memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "feed",
        {
            "max_items": 1,
            "cursor_contract": "page-offset-v1",
            "start_cursors": {"default": {"page": 2, "offset": 4}},
        },
        daily_budget=10,
    )
    assert task_id
    token = client.get("/api/sources/linuxdo/next-task").json()["claim_token"]
    base = {
        "task_id": task_id,
        "claim_token": token,
        "status": "empty",
        "items": [],
        "scope_counts": {},
        "response_observed": True,
        "complete_scopes": ["linuxdo_feed"],
    }

    missing = client.post("/api/sources/linuxdo/task-result", json=base)
    assert missing.status_code == 422
    assert missing.json()["detail"] == "incomplete_cursor_result"

    wrong_lane = client.post(
        "/api/sources/linuxdo/task-result",
        json={**base, "next_cursors": {"other": {"page": 3, "offset": 0}}},
    )
    assert wrong_lane.status_code == 422
    assert wrong_lane.json()["detail"] == "unauthorized_cursor_key"

    accepted = client.post(
        "/api/sources/linuxdo/task-result",
        json={**base, "next_cursors": {"default": {"page": 3, "offset": 0}}},
    )
    assert accepted.status_code == 200
    canonical = json.loads(str(queue.get(task_id)["result_json"]))
    assert canonical["next_cursors"] == {"default": {"page": 3, "offset": 0}}


def test_linuxdo_claim_fence_rejects_stale_owner_and_serializes_instances(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, _memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    first = queue.enqueue_with_id("feed", {"max_items": 1}, daily_budget=10)
    second = queue.enqueue_with_id("hot", {"max_items": 1}, daily_budget=10)
    assert first and second

    first_claim = client.get("/api/sources/linuxdo/next-task").json()
    assert first_claim["id"] == first
    assert client.get("/api/sources/linuxdo/next-task").status_code == 204

    completed = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": first,
            "claim_token": first_claim["claim_token"],
            "status": "empty",
            "items": [],
            "scope_counts": {},
            "response_observed": True,
            "complete_scopes": ["linuxdo_feed"],
        },
    )
    assert completed.status_code == 200
    second_claim = client.get("/api/sources/linuxdo/next-task").json()
    assert second_claim["id"] == second

    database.conn.execute(
        "UPDATE linuxdo_tasks SET claimed_at = datetime('now', '-36 minutes') WHERE id = ?",
        (second,),
    )
    database.conn.commit()
    replacement = client.get("/api/sources/linuxdo/next-task").json()
    assert replacement["id"] == second
    assert replacement["claim_token"] != second_claim["claim_token"]
    stale = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": second,
            "claim_token": second_claim["claim_token"],
            "status": "empty",
            "items": [],
            "scope_counts": {},
            "response_observed": True,
            "complete_scopes": ["linuxdo_hot"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "task_claim_conflict"


def test_linuxdo_disabled_source_does_not_claim_an_existing_pending_task(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, _memory, _hub, config = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    task_id = queue.enqueue_with_id("feed", {"max_items": 1}, daily_budget=10)
    assert task_id

    config.sources.linuxdo.enabled = False
    assert client.get("/api/sources/linuxdo/next-task").status_code == 204
    assert queue.get(task_id)["status"] == "pending"

    config.sources.linuxdo.enabled = True
    claimed = client.get("/api/sources/linuxdo/next-task")
    assert claimed.status_code == 200
    assert claimed.json()["id"] == task_id


def test_linuxdo_failed_final_preserves_partial_and_account_switch_is_blocked(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, memory, _hub, _cfg = linuxdo_api
    queue = LinuxdoTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "scopes": ["linuxdo_bookmarks"],
            "max_items_per_scope": 2,
            "profile_update": True,
        },
        daily_budget=10,
    )
    assert task_id
    token = client.get("/api/sources/linuxdo/next-task").json()["claim_token"]
    partial = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "partial",
            "account_key": _ACCOUNT_KEY,
            "items": [_topic("linuxdo_bookmarks", 71)],
            "scope_counts": {"linuxdo_bookmarks": 1},
        },
    )
    assert partial.status_code == 200
    failed = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": task_id,
            "claim_token": token,
            "status": "failed",
            "account_key": _ACCOUNT_KEY,
            "items": [],
            "scope_counts": {"linuxdo_bookmarks": 1},
            "error": "linuxdo_rate_limited",
        },
    )
    assert failed.status_code == 200
    stored = queue.get(task_id)
    assert stored["status"] == "failed"
    canonical = json.loads(stored["result_json"])
    assert canonical["items"] == [_topic("linuxdo_bookmarks", 71)]
    assert canonical["_openbiliclaw_terminal_status"] == "failed"
    assert canonical["error"] == "linuxdo_rate_limited"
    assert len(memory.query_events(limit=20)) == 1

    next_id = queue.enqueue_with_id(
        "bootstrap_events",
        {
            "scopes": ["linuxdo_bookmarks"],
            "max_items_per_scope": 1,
            "profile_update": True,
        },
        daily_budget=10,
    )
    assert next_id
    next_token = client.get("/api/sources/linuxdo/next-task").json()["claim_token"]
    switched = client.post(
        "/api/sources/linuxdo/task-result",
        json={
            "task_id": next_id,
            "claim_token": next_token,
            "status": "ok",
            "account_key": _OTHER_ACCOUNT_KEY,
            "response_observed": True,
            "complete_scopes": ["linuxdo_bookmarks"],
            "items": [_topic("linuxdo_bookmarks", 72)],
            "scope_counts": {"linuxdo_bookmarks": 1},
        },
    )
    assert switched.status_code == 409
    assert switched.json()["detail"] == "linuxdo_account_switch_requires_reset"


def test_linuxdo_status_credentials_login_state_and_config_round_trip(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
) -> None:
    client, database, _memory, _hub, cfg = linuxdo_api

    status = client.get("/api/sources/status")
    assert status.status_code == 200
    linuxdo = status.json()["linuxdo"]
    assert linuxdo["enabled"] is True
    assert linuxdo["state"] == "no_auth"
    assert linuxdo["logged_in"] is True
    assert linuxdo["auth"]["auth_required"] is False
    assert linuxdo["auth"]["credential"] == "none"
    assert linuxdo["auth"]["verification"] == "unverified"
    assert linuxdo["auth"]["verify_method"] == "none"

    credentials = client.get("/api/sources/credentials")
    assert credentials.status_code == 200
    credential = credentials.json()["linuxdo"]
    assert credential["available"] is False
    assert credential["form"]["kind"] == "extension_only"
    assert credential["form"]["required_keys"] == []
    assert {action["action"] for action in credential["form"]["actions"]} == {
        "verify",
        "open_login_window",
    }

    config_before = client.get("/api/config")
    assert config_before.status_code == 200
    assert config_before.json()["sources"]["linuxdo"] == {
        "recommendation_date_preset": "all",
        "recommendation_date_start": "",
        "recommendation_date_end": "",
        "recommendation_date_weight": 0.5,
        "enabled": True,
        "incremental_enabled": False,
        "source_modes": ["search", "hot", "feed", "creator", "related"],
        "daily_search_budget": 0,
        "daily_hot_budget": 0,
        "daily_feed_budget": 0,
        "daily_creator_budget": 0,
        "daily_related_budget": 0,
        "request_interval_seconds": 3,
        "min_interval_minutes": 3,
        "bootstrap_limit": 300,
    }

    heartbeat = client.post(
        "/api/sources/linuxdo/login-state",
        json={"logged_in": True},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["logged_in"] is True
    logged_in, updated_at = database.get_linuxdo_login_state()
    assert logged_in is True
    assert updated_at

    refreshed = client.get("/api/sources/status").json()["linuxdo"]
    assert refreshed["state"] == "no_auth"
    assert refreshed["logged_in"] is True
    assert refreshed["auth"]["credential"] == "present"
    assert refreshed["auth"]["credential_origin"] == "extension"
    assert refreshed["auth"]["verification"] == "unverified"
    assert refreshed["auth"]["verify_method"] == "browser_heartbeat"
    assert refreshed["auth"]["capabilities"]["discover"]["readiness"] == "ready"
    assert refreshed["auth"]["capabilities"]["profile"]["readiness"] == "ready"

    updated = client.put(
        "/api/config",
        json={
            "sources": {
                "linuxdo": {
                    "source_modes": ["search", "hot"],
                    "daily_search_budget": 42,
                    "daily_hot_budget": 24,
                    "request_interval_seconds": 5,
                    "min_interval_minutes": 7,
                    "bootstrap_limit": 123,
                }
            }
        },
    )
    assert updated.status_code == 202, updated.text
    updated_linuxdo = updated.json()["config"]["sources"]["linuxdo"]
    assert updated_linuxdo["source_modes"] == ["search", "hot"]
    assert updated_linuxdo["daily_search_budget"] == 42
    assert updated_linuxdo["daily_hot_budget"] == 24
    assert updated_linuxdo["request_interval_seconds"] == 5
    assert updated_linuxdo["min_interval_minutes"] == 7
    assert updated_linuxdo["bootstrap_limit"] == 123
    assert cfg.sources.linuxdo.source_modes == ("search", "hot")
    assert cfg.sources.linuxdo.request_interval_seconds == 5


@pytest.mark.parametrize(
    "linuxdo_update",
    (
        {"request_interval_seconds": 31},
        {"bootstrap_limit": 301},
    ),
)
def test_linuxdo_config_rejects_browser_task_limits_above_contract(
    linuxdo_api: tuple[TestClient, Database, MemoryManager, _EventHub, Config],
    linuxdo_update: dict[str, int],
) -> None:
    client, _database, _memory, _hub, _cfg = linuxdo_api

    response = client.put(
        "/api/config",
        json={"sources": {"linuxdo": linuxdo_update}},
    )

    assert response.status_code == 400
    assert response.json()["detail"].endswith("超出允许范围")
