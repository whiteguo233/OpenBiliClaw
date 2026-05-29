from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def favorites_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Database]:
    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.scheduler.enabled = False
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")

    db = Database(tmp_path / "favorites.db")
    db.initialize()
    db.cache_content(
        "BV1FAV",
        title="收藏测试视频",
        up_name="测试 UP",
        cover_url="https://i0.hdslb.com/bfs/archive/fav.jpg",
        content_url="https://www.bilibili.com/video/BV1FAV",
        source="test",
        source_platform="bilibili",
    )
    db.cache_content(
        "BV2FAV",
        title="第二条收藏",
        up_name="另一个 UP",
        source="test",
        source_platform="youtube",
        content_url="https://www.youtube.com/watch?v=fav",
    )
    app = create_app(
        memory_manager=SimpleNamespace(
            load_discovery_runtime_state=lambda: {},
            load_cognition_updates=lambda: [],
        ),
        database=db,
        soul_engine=SimpleNamespace(get_profile=lambda: None),
    )
    return TestClient(app), db


def test_favorites_endpoints_round_trip_with_metadata(
    favorites_client: tuple[TestClient, Database],
) -> None:
    client, _db = favorites_client

    assert client.get("/api/favorites/BV1FAV").json() == {"saved": False, "total": 0}

    response = client.post("/api/favorites", json={"bvid": " BV1FAV "})
    assert response.status_code == 200
    assert response.json() == {"saved": True, "total": 1}

    list_response = client.get("/api/favorites?limit=20&offset=0")
    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [
            {
                "bvid": "BV1FAV",
                "title": "收藏测试视频",
                "up_name": "测试 UP",
                "cover_url": "https://i0.hdslb.com/bfs/archive/fav.jpg",
                "content_url": "https://www.bilibili.com/video/BV1FAV",
                "source_platform": "bilibili",
                "added_at": list_response.json()["items"][0]["added_at"],
            }
        ],
        "total": 1,
    }

    remove_response = client.delete("/api/favorites/BV1FAV")
    assert remove_response.status_code == 200
    assert remove_response.json() == {"saved": False, "total": 0}


def test_favorites_list_paginates_newest_first(
    favorites_client: tuple[TestClient, Database],
) -> None:
    client, db = favorites_client
    db.add_to_favorites("BV1FAV")
    db.add_to_favorites("BV2FAV")
    db.conn.execute(
        "UPDATE favorites SET added_at = ? WHERE bvid = ?",
        ("2026-05-28 09:00:00", "BV1FAV"),
    )
    db.conn.execute(
        "UPDATE favorites SET added_at = ? WHERE bvid = ?",
        ("2026-05-28 09:01:00", "BV2FAV"),
    )
    db.conn.commit()

    response = client.get("/api/favorites?limit=1&offset=0")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["bvid"] == "BV2FAV"


def test_favorites_list_rejects_invalid_pagination(
    favorites_client: tuple[TestClient, Database],
) -> None:
    client, _db = favorites_client

    assert client.get("/api/favorites?limit=0").status_code == 422
    assert client.get("/api/favorites?offset=-1").status_code == 422


def test_favorites_and_watch_later_are_independent(
    favorites_client: tuple[TestClient, Database],
) -> None:
    """Favoriting a video must not add it to watch-later, and vice versa."""
    client, _db = favorites_client

    client.post("/api/favorites", json={"bvid": "BV1FAV"})

    assert client.get("/api/favorites/BV1FAV").json()["saved"] is True
    assert client.get("/api/watch-later/BV1FAV").json()["saved"] is False

    client.post("/api/watch-later", json={"bvid": "BV2FAV"})
    assert client.get("/api/watch-later/BV2FAV").json()["saved"] is True
    assert client.get("/api/favorites/BV2FAV").json()["saved"] is False
