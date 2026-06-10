"""Tests for X (Twitter) account subscriptions, store + API endpoints.

X account subscriptions track handles the user wants discovery to follow.
Unlike XHS, there is NO extension round-trip — the producer (Task 10) fetches
each subscription server-side via ``XCreatorStrategy``. This module only owns
the ``x_creator_subscriptions`` table + CRUD and the
``/api/sources/x/creators`` endpoints (mirroring the XHS creators pattern).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.sources.x_tasks import XCreatorStore
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    d.initialize()
    return d


@pytest.fixture
def creator_store(db: Database) -> XCreatorStore:
    return XCreatorStore(db)


class TestXCreatorStore:
    def test_add_and_list(self, creator_store: XCreatorStore) -> None:
        creator_store.add(handle="paulg")

        subs = creator_store.list_all()
        assert len(subs) == 1
        assert subs[0]["handle"] == "paulg"

    def test_add_normalizes_leading_at(self, creator_store: XCreatorStore) -> None:
        creator_store.add(handle="@paulg")

        subs = creator_store.list_all()
        assert len(subs) == 1
        assert subs[0]["handle"] == "paulg"

    def test_add_duplicate_is_idempotent(self, creator_store: XCreatorStore) -> None:
        creator_store.add("paulg")
        creator_store.add("paulg")
        # A leading @ on the second insert must collapse to the same row.
        creator_store.add("@paulg")

        assert len(creator_store.list_all()) == 1

    def test_delete(self, creator_store: XCreatorStore) -> None:
        creator_store.add("paulg")
        subs = creator_store.list_all()
        assert len(subs) == 1

        deleted = creator_store.delete(subs[0]["id"])
        assert deleted is True
        assert len(creator_store.list_all()) == 0

    def test_delete_nonexistent_returns_false(self, creator_store: XCreatorStore) -> None:
        assert creator_store.delete(9999) is False

    def test_due_for_fetch_and_mark_fetched(self, creator_store: XCreatorStore) -> None:
        creator_store.add("paulg")

        # Fresh subscription should be due (last_fetched_at IS NULL).
        due = creator_store.due_for_fetch(hours=24)
        assert len(due) == 1

        # After marking fetched, should no longer be due.
        creator_store.mark_fetched(due[0]["id"])
        assert len(creator_store.due_for_fetch(hours=24)) == 0


# ── API endpoint tests ────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db = Database(tmp_path / "api.db")
    db.initialize()

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
    monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
    monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")

    from openbiliclaw.api.app import create_app

    app = create_app(database=db)
    return TestClient(app)


class TestXCreatorApi:
    def test_creator_crud(self, api_client: TestClient) -> None:
        # Add (a leading @ is normalized away)
        resp = api_client.post(
            "/api/sources/x/creators",
            json={"handle": "@paulg"},
        )
        assert resp.status_code == 201

        # List
        resp = api_client.get("/api/sources/x/creators")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["handle"] == "paulg"

        # Delete
        sub_id = items[0]["id"]
        resp = api_client.delete(f"/api/sources/x/creators/{sub_id}")
        assert resp.status_code == 200

        # Verify deleted
        resp = api_client.get("/api/sources/x/creators")
        assert len(resp.json()["items"]) == 0

    def test_creator_add_is_idempotent(self, api_client: TestClient) -> None:
        api_client.post("/api/sources/x/creators", json={"handle": "paulg"})
        api_client.post("/api/sources/x/creators", json={"handle": "@paulg"})

        resp = api_client.get("/api/sources/x/creators")
        assert len(resp.json()["items"]) == 1

    def test_creator_add_requires_handle(self, api_client: TestClient) -> None:
        resp = api_client.post("/api/sources/x/creators", json={})
        assert resp.status_code == 422

    def test_creator_delete_nonexistent_returns_404(self, api_client: TestClient) -> None:
        resp = api_client.delete("/api/sources/x/creators/9999")
        assert resp.status_code == 404
