from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import RedditSourceConfigOut
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config
from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _FakeEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, payload: dict[str, object]) -> None:
        self.events.append(payload)


def _make_database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "reddit-api.db")
    db.initialize()
    return db


def test_reddit_api_schema_defaults_match_extension_discovery_defaults() -> None:
    cfg = RedditSourceConfigOut()

    assert cfg.backend == "extension"
    assert cfg.source_modes == ["search", "hot", "subreddit", "related"]
    assert cfg.daily_search_budget == 300
    assert cfg.daily_hot_budget == 300
    assert cfg.daily_subreddit_budget == 300
    assert cfg.daily_related_budget == 300


def test_reddit_task_api_claims_and_records_extension_result(tmp_path: Path) -> None:
    db = _make_database(tmp_path)
    queue = RedditTaskQueue(db)
    task_id = queue.enqueue_with_id(
        "search",
        {"keywords": ["local agents"], "max_items_per_keyword": 3},
        daily_budget=10,
    )

    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    client = TestClient(app)

    next_resp = client.get("/api/sources/reddit/next-task")
    assert next_resp.status_code == 200
    assert next_resp.json() == {
        "id": task_id,
        "type": "search",
        "keywords": ["local agents"],
        "max_items_per_keyword": 3,
    }

    result_resp = client.post(
        "/api/sources/reddit/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "items": [
                {
                    "id": "abc123",
                    "title": "Local-first agents",
                    "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
                    "subreddit": "LocalLLaMA",
                }
            ],
            "scope_counts": {"reddit_search": 1},
        },
    )
    assert result_resp.status_code == 200
    assert result_resp.json() == {"ok": True}

    stored = queue.get(str(task_id))
    assert stored is not None
    assert stored["status"] == "completed"
    payload = json.loads(str(stored["result_json"]))
    assert payload["scope_counts"] == {"reddit_search": 1}
    assert payload["items"][0]["title"] == "Local-first agents"


def test_reddit_kick_broadcasts_runtime_event(tmp_path: Path) -> None:
    db = _make_database(tmp_path)
    hub = _FakeEventHub()
    app = create_app(
        memory_manager=object(),
        database=db,
        soul_engine=object(),
        runtime_event_hub=hub,
    )
    client = TestClient(app)

    resp = client.post("/api/sources/reddit/kick")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert hub.events == [{"type": "reddit_task_available", "source": "task_kick"}]


def test_reddit_source_status_uses_extension_backend_without_command_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "runtime"
    cfg = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        )
    )
    cfg.sources.reddit.enabled = True
    cfg.sources.reddit.backend = "extension"
    save_config(cfg, project_root / "config.toml")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: pytest.fail("extension status must not probe command backends"),
    )

    db = _make_database(tmp_path)
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    client = TestClient(app)

    resp = client.get("/api/sources/status")

    assert resp.status_code == 200
    reddit = resp.json()["reddit"]
    assert reddit["enabled"] is True
    assert reddit["state"] == "unverified"
    assert "OpenBiliClaw 插件" in reddit["detail"]


def test_put_config_preserves_reddit_extension_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "runtime"
    cfg = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        )
    )
    save_config(cfg, project_root / "config.toml")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda c, path=None: save_config(c, project_root / "config.toml"),
    )

    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    client = TestClient(app)

    resp = client.put(
        "/api/config",
        json={"sources": {"reddit": {"enabled": True, "backend": "extension"}}},
    )

    assert resp.status_code == 200, resp.text
    assert cfg.sources.reddit.backend == "extension"
    assert resp.json()["config"]["sources"]["reddit"]["backend"] == "extension"
