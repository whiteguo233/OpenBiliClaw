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
def watch_later_client(
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

    db = Database(tmp_path / "watch_later.db")
    db.initialize()
    db.cache_content(
        "BV1WATCH",
        title="稍后再看测试视频",
        up_name="测试 UP",
        cover_url="https://i0.hdslb.com/bfs/archive/watch.jpg",
        content_url="https://www.bilibili.com/video/BV1WATCH",
        source="test",
        source_platform="bilibili",
    )
    db.cache_content(
        "BV2WATCH",
        title="第二条稍后再看",
        up_name="另一个 UP",
        source="test",
        source_platform="youtube",
        content_url="https://www.youtube.com/watch?v=watch",
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


def test_watch_later_endpoints_round_trip_with_metadata(
    watch_later_client: tuple[TestClient, Database],
) -> None:
    client, _db = watch_later_client

    assert client.get("/api/watch-later/BV1WATCH").json() == {"saved": False, "total": 0}

    response = client.post("/api/watch-later", json={"bvid": " BV1WATCH "})
    assert response.status_code == 200
    assert response.json() == {"saved": True, "total": 1}

    list_response = client.get("/api/watch-later?limit=20&offset=0")
    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [
            {
                "bvid": "BV1WATCH",
                "title": "稍后再看测试视频",
                "up_name": "测试 UP",
                "cover_url": "https://i0.hdslb.com/bfs/archive/watch.jpg",
                "content_url": "https://www.bilibili.com/video/BV1WATCH",
                "source_platform": "bilibili",
                "added_at": list_response.json()["items"][0]["added_at"],
            }
        ],
        "total": 1,
    }

    remove_response = client.delete("/api/watch-later/BV1WATCH")
    assert remove_response.status_code == 200
    assert remove_response.json() == {"saved": False, "total": 0}


def test_watch_later_list_paginates_newest_first(
    watch_later_client: tuple[TestClient, Database],
) -> None:
    client, db = watch_later_client
    db.add_to_watch_later("BV1WATCH")
    db.add_to_watch_later("BV2WATCH")
    db.conn.execute(
        "UPDATE watch_later SET added_at = ? WHERE bvid = ?",
        ("2026-05-28 09:00:00", "BV1WATCH"),
    )
    db.conn.execute(
        "UPDATE watch_later SET added_at = ? WHERE bvid = ?",
        ("2026-05-28 09:01:00", "BV2WATCH"),
    )
    db.conn.commit()

    response = client.get("/api/watch-later?limit=1&offset=0")

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["bvid"] == "BV2WATCH"


def test_watch_later_list_rejects_invalid_pagination(
    watch_later_client: tuple[TestClient, Database],
) -> None:
    client, _db = watch_later_client

    assert client.get("/api/watch-later?limit=0").status_code == 422
    assert client.get("/api/watch-later?offset=-1").status_code == 422
