"""Tests for the X (Twitter) cookie-sync endpoint and reader helper.

Mirrors the Douyin cookie bridge: the browser extension POSTs the live
``x.com`` Cookie header to ``/api/sources/x/cookie``; the backend persists
it to ``data/x_cookie.json``. ``has_cookie`` is true only when BOTH
``auth_token`` and ``ct0`` are present. The reader helper lets the env var
``OPENBILICLAW_X_COOKIE`` take precedence over the persisted file.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TestXCookieEndpoint:
    def test_x_cookie_endpoint_persists_when_both_required_cookies_present(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        cookie_value = "auth_token=at123; ct0=ct456; guest_id=gx"
        response = client.post(
            "/api/sources/x/cookie",
            json={"cookie": cookie_value, "source": "extension"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["has_cookie"] is True

        cookie_file = tmp_path / "data" / "x_cookie.json"
        assert cookie_file.exists()
        payload = json.loads(cookie_file.read_text(encoding="utf-8"))
        assert payload["cookie"] == cookie_value
        assert payload["source"] == "extension"
        # Never mirror secrets into config.toml.
        assert cookie_value not in (tmp_path / "config.toml").read_text(encoding="utf-8")

    def test_x_cookie_endpoint_clears_relogin_block_on_valid_cookie(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A valid cookie sync must lift a missing_cookie / expired_cookie block
        so discovery's is_ready() gate reopens — otherwise the producer stays
        dead-locked even after the user re-logs in on x.com."""
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config
        from openbiliclaw.sources.x_client import XMissingCookieError
        from openbiliclaw.storage.database import Database
        from openbiliclaw.storage.x_health import XSourceHealthStore

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        db = Database(tmp_path / "x.db")
        db.initialize()
        health = XSourceHealthStore(db)
        health.record_error(XMissingCookieError("missing auth_token / ct0"), strategy="search")
        assert health.is_ready() is False  # parked before the sync

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/sources/x/cookie",
            json={"cookie": "auth_token=at123; ct0=ct456", "source": "extension"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["has_cookie"] is True
        # The endpoint must have cleared the re-login block.
        assert XSourceHealthStore(db).is_ready() is True

    def test_x_cookie_endpoint_keeps_block_when_cookie_incomplete(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """An incomplete jar (no ct0) is not a valid re-login, so the block stays."""
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config
        from openbiliclaw.sources.x_client import XMissingCookieError
        from openbiliclaw.storage.database import Database
        from openbiliclaw.storage.x_health import XSourceHealthStore

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        db = Database(tmp_path / "x.db")
        db.initialize()
        XSourceHealthStore(db).record_error(
            XMissingCookieError("missing auth_token / ct0"), strategy="search"
        )

        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/sources/x/cookie",
            json={"cookie": "auth_token=at123; guest_id=gx", "source": "extension"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["has_cookie"] is False
        assert XSourceHealthStore(db).is_ready() is False  # still parked

    def test_x_cookie_endpoint_reports_has_cookie_false_without_ct0(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/sources/x/cookie",
            json={"cookie": "auth_token=at123; guest_id=gx", "source": "extension"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["has_cookie"] is False

    def test_x_cookie_endpoint_rejects_empty_cookie(self, monkeypatch, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/sources/x/cookie",
            json={"cookie": "   ", "source": "extension"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is False
        assert body["has_cookie"] is False
        assert body["error_code"] == "empty_cookie"


class TestXCookieReader:
    def test_resolve_x_cookie_prefers_env_over_persisted(self, monkeypatch, tmp_path: Path) -> None:
        from openbiliclaw.api.app import XCookieManager, resolve_x_cookie

        XCookieManager(tmp_path).set_cookie("auth_token=file; ct0=file", source="file")
        monkeypatch.setenv("TEST_X_COOKIE", "auth_token=env; ct0=env")

        assert (
            resolve_x_cookie(data_dir=tmp_path, cookie_env="TEST_X_COOKIE")
            == "auth_token=env; ct0=env"
        )

    def test_resolve_x_cookie_falls_back_to_persisted_file(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from openbiliclaw.api.app import XCookieManager, resolve_x_cookie

        XCookieManager(tmp_path).set_cookie("auth_token=file; ct0=file", source="file")
        monkeypatch.delenv("TEST_X_COOKIE", raising=False)

        assert (
            resolve_x_cookie(data_dir=tmp_path, cookie_env="TEST_X_COOKIE")
            == "auth_token=file; ct0=file"
        )

    def test_resolve_x_cookie_empty_when_nothing_present(self, monkeypatch, tmp_path: Path) -> None:
        from openbiliclaw.api.app import resolve_x_cookie

        monkeypatch.delenv("TEST_X_COOKIE", raising=False)
        assert resolve_x_cookie(data_dir=tmp_path, cookie_env="TEST_X_COOKIE") == ""


class TestSourcesStatusCookieGating:
    """The unified status chip must not report a logged-in source without a
    credential actually present (the x_source_health row defaults to ``ok``
    before any fetch has run)."""

    def _client(self, monkeypatch, tmp_path: Path):
        from fastapi.testclient import TestClient

        from openbiliclaw.api.app import create_app
        from openbiliclaw.config import Config, save_config
        from openbiliclaw.storage.database import Database

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.delenv("OPENBILICLAW_X_COOKIE", raising=False)
        monkeypatch.delenv("OPENBILICLAW_DOUYIN_COOKIE", raising=False)
        save_config(Config(), tmp_path / "config.toml")

        db = Database(tmp_path / "x.db")
        db.initialize()
        app = create_app(memory_manager=object(), database=db, soul_engine=object())
        return TestClient(app)

    def test_x_reports_missing_cookie_when_health_ok_but_no_cookie(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        client = self._client(monkeypatch, tmp_path)

        body = client.get("/api/sources/status").json()

        assert body["twitter"]["state"] == "missing_cookie"
        assert body["twitter"]["logged_in"] is False

    def test_x_reports_ok_when_health_ok_and_cookie_present(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from openbiliclaw.sources.x_auth import XCookieManager

        XCookieManager(tmp_path / "data").set_cookie("auth_token=at; ct0=ct", source="test")
        client = self._client(monkeypatch, tmp_path)

        body = client.get("/api/sources/status").json()

        assert body["twitter"]["state"] == "ok"
        assert body["twitter"]["logged_in"] is True

    def test_bilibili_status_falls_back_to_cookie_file(self, monkeypatch, tmp_path: Path) -> None:
        """CLI QR login writes only data/bilibili_cookie.json; the status
        chip must count that as ready even with config.toml cookie empty."""
        from openbiliclaw.bilibili.auth import AuthManager

        AuthManager(data_dir=tmp_path / "data").set_cookie("SESSDATA=s; bili_jct=j; DedeUserID=1")
        client = self._client(monkeypatch, tmp_path)

        body = client.get("/api/sources/status").json()

        assert body["bilibili"]["state"] == "ready"
        assert body["bilibili"]["logged_in"] is True
