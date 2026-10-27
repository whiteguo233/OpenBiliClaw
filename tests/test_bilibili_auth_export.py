"""Tests for the mobile Bilibili cookie export endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw import auth_core as ac
from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_SECRET = "fixed-test-session-secret-bbbbbbbbbbbb"
_LOOPBACK_ORIGIN = "http://127.0.0.1:8420"


def _make_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cookie: str,
    auth_enabled: bool = False,
) -> tuple[object, Database]:
    cfg = Config()
    cfg.data_dir = str(tmp_path)
    cfg.bilibili.cookie = cookie
    cfg.api.auth.enabled = auth_enabled
    cfg.api.auth.session_secret = _SECRET
    if auth_enabled:
        cfg.api.auth.password_hash = ac.hash_password("hunter2")
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "auth-export.db")
    database.initialize()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
    )
    return app, database


def _local(app: object) -> TestClient:
    # Loopback peer + loopback Host: the trusted-local bypass applies.
    return TestClient(app, client=("127.0.0.1", 5000), base_url=_LOOPBACK_ORIGIN)


def _remote(app: object) -> TestClient:
    return TestClient(app, client=("192.168.1.50", 5000))


def test_bilibili_auth_export_returns_cookie_and_meta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cookie = "SESSDATA=secret; bili_jct=csrf; buvid3=buvid-value-3; DedeUserID=123"
    app, _ = _make_app(monkeypatch, tmp_path, cookie=cookie)
    with _local(app) as client:
        response = client.post("/api/bilibili/auth/export", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cookie"] == cookie
    assert payload["cookies"]["SESSDATA"] == "secret"
    assert payload["cookies"]["bili_jct"] == "csrf"
    assert payload["buvid"] == "buvid-value-3"
    assert payload["user_agent"].startswith("Mozilla/5.0 (Macintosh")
    assert payload["user"] is None


def test_bilibili_auth_export_requires_cookie(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _ = _make_app(monkeypatch, tmp_path, cookie="")
    with _local(app) as client:
        response = client.post("/api/bilibili/auth/export", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "B站 Cookie 未配置或已失效"


# ── issue #299: credential routes stay privileged with the gate off ─────────


def test_bilibili_credential_routes_reject_remote_when_gate_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cookie = "SESSDATA=secret; bili_jct=csrf; DedeUserID=123"
    app, _ = _make_app(monkeypatch, tmp_path, cookie=cookie)
    with _remote(app) as client:
        export = client.post("/api/bilibili/auth/export", json={})
        assert export.status_code == 403
        assert export.json()["error"] == "local_only"
        assert "secret" not in export.text

        delete = client.delete("/api/bilibili/auth/session")
        assert delete.status_code == 403
        assert delete.json()["error"] == "local_only"

        # Import must be rejected before any credential write is attempted:
        # the stored cookie survives untouched.
        importer = client.post(
            "/api/bilibili/auth/import",
            json={"cookies": {"SESSDATA": "attacker"}},
        )
        assert importer.status_code == 403
        assert importer.json()["error"] == "local_only"

    with _local(app) as client:
        # the attacker's cookie never landed; the victim's cookie is intact
        still = client.post("/api/bilibili/auth/export", json={})
        assert still.status_code == 200
        assert still.json()["cookie"] == cookie


def test_bilibili_credential_routes_allow_local_when_gate_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _ = _make_app(monkeypatch, tmp_path, cookie="SESSDATA=secret")
    with _local(app) as client:
        response = client.delete("/api/bilibili/auth/session")
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_bilibili_auth_export_requires_auth_when_gate_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, _ = _make_app(monkeypatch, tmp_path, cookie="SESSDATA=secret", auth_enabled=True)
    with _remote(app) as client:
        response = client.post("/api/bilibili/auth/export", json={})
    assert response.status_code == 401
    assert response.json()["error"] == "auth_required"


def test_bilibili_auth_export_allows_authenticated_remote_when_gate_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cookie = "SESSDATA=secret; bili_jct=csrf; DedeUserID=123"
    app, database = _make_app(monkeypatch, tmp_path, cookie=cookie, auth_enabled=True)
    token = ac.sign_token(_SECRET, epoch=database.get_auth_epoch(), ttl_hours=1)
    with _remote(app) as client:
        response = client.post(
            "/api/bilibili/auth/export",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["cookie"] == cookie
