"""Tests for SourceRecipe database CRUD and API endpoints (Phase 1)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _make_cross_thread_db(tmp_path: Path) -> Database:
    """Create a Database that works across threads (for TestClient)."""
    db = Database(tmp_path / "api_test.db")
    db._db_path.parent.mkdir(parents=True, exist_ok=True)
    db._conn = sqlite3.connect(str(db._db_path), timeout=30.0, check_same_thread=False)
    db._conn.row_factory = sqlite3.Row
    db._conn.execute("PRAGMA journal_mode=WAL")
    db._conn.execute("PRAGMA busy_timeout = 30000")
    # Run full initialization using the cross-thread connection
    from openbiliclaw.storage.database import _SCHEMA_SQL

    db._conn.executescript(_SCHEMA_SQL)
    db._ensure_recommendation_feedback_columns()
    db._ensure_content_cache_runtime_columns()
    db._ensure_content_cache_relevance_columns()
    db._ensure_content_cache_topic_columns()
    db._ensure_content_cache_pool_copy_columns()
    db._ensure_content_cache_delight_columns()
    db._ensure_content_cache_multisource_columns()
    db._ensure_source_recipes_table()
    db._conn.commit()
    return db


class TestSourceRecipeCRUD:
    """Database-level CRUD for source_recipes table."""

    def _make_db(self, tmp_path: Path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    def test_save_and_get_all(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "B站搜索",
                "strategy": "search",
                "config": {"query": "rust"},
                "target_share": 4,
            }
        )
        recipes = db.get_all_recipes()
        assert len(recipes) == 1
        assert recipes[0]["id"] == "r1"
        assert recipes[0]["source_type"] == "bilibili"
        assert recipes[0]["name"] == "B站搜索"
        assert recipes[0]["strategy"] == "search"
        assert recipes[0]["config"] == {"query": "rust"}
        assert recipes[0]["target_share"] == 4
        assert recipes[0]["enabled"] is True
        assert recipes[0]["created_by"] == "system"

    def test_get_enabled_filters_disabled(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "启用的",
                "strategy": "search",
            }
        )
        db.save_source_recipe(
            {
                "id": "r2",
                "source_type": "bilibili",
                "name": "禁用的",
                "strategy": "trending",
                "enabled": False,
            }
        )
        enabled = db.get_enabled_recipes()
        assert len(enabled) == 1
        assert enabled[0]["id"] == "r1"

    def test_update_recipe(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "原名",
                "strategy": "search",
                "target_share": 4,
            }
        )
        updated = db.update_recipe("r1", name="新名", target_share=8)
        assert updated is True
        recipes = db.get_all_recipes()
        assert recipes[0]["name"] == "新名"
        assert recipes[0]["target_share"] == 8

    def test_update_recipe_nonexistent_returns_false(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        assert db.update_recipe("nonexistent", name="x") is False

    def test_delete_recipe(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "待删",
                "strategy": "search",
            }
        )
        deleted = db.delete_recipe("r1")
        assert deleted is True
        assert db.get_all_recipes() == []

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        assert db.delete_recipe("nope") is False

    def test_upsert_on_conflict(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "原名",
                "strategy": "search",
            }
        )
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "覆盖名",
                "strategy": "trending",
            }
        )
        recipes = db.get_all_recipes()
        assert len(recipes) == 1
        assert recipes[0]["name"] == "覆盖名"
        assert recipes[0]["strategy"] == "trending"

    def test_toggle_enabled(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "测试",
                "strategy": "search",
            }
        )
        db.update_recipe("r1", enabled=False)
        assert db.get_enabled_recipes() == []

        db.update_recipe("r1", enabled=True)
        assert len(db.get_enabled_recipes()) == 1


class TestSourceRecipeAPI:
    """API endpoint tests for /api/sources."""

    def test_list_sources_empty(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/sources")
        assert response.status_code == 200
        assert response.json() == {"items": []}

    def test_create_and_list_source(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/sources",
            json={
                "source_type": "xiaohongshu",
                "name": "小红书-机械键盘",
                "strategy": "search",
                "config": {"query": "机械键盘"},
                "created_by": "agent",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["ok"] is True
        assert data["recipe"]["source_type"] == "xiaohongshu"
        assert data["recipe"]["name"] == "小红书-机械键盘"

        # Verify it shows up in list
        recipes = client.get("/api/sources").json()["items"]
        assert len(recipes) == 1
        assert recipes[0]["source_type"] == "xiaohongshu"

    def test_create_source_missing_fields(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/sources", json={"source_type": "web"})
        assert response.status_code == 422

    def test_update_source(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        client.post(
            "/api/sources",
            json={
                "id": "test-id",
                "source_type": "web",
                "name": "原名",
                "strategy": "feed",
            },
        )

        response = client.put("/api/sources/test-id", json={"name": "新名", "enabled": False})
        assert response.status_code == 200

        recipes = client.get("/api/sources").json()["items"]
        assert recipes[0]["name"] == "新名"
        assert recipes[0]["enabled"] is False

    def test_delete_source(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        client.post(
            "/api/sources",
            json={
                "id": "del-me",
                "source_type": "web",
                "name": "待删",
                "strategy": "feed",
                "created_by": "user",
            },
        )

        response = client.delete("/api/sources/del-me")
        assert response.status_code == 200
        assert client.get("/api/sources").json()["items"] == []

    def test_delete_system_recipe_forbidden(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app

        db = _make_cross_thread_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "sys-recipe",
                "source_type": "bilibili",
                "name": "系统内置",
                "strategy": "search",
                "created_by": "system",
            }
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.delete("/api/sources/sys-recipe")
        assert response.status_code == 403
        # Recipe should still exist
        assert len(client.get("/api/sources").json()["items"]) == 1
