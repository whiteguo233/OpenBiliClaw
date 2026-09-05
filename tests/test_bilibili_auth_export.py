"""Tests for the mobile Bilibili cookie export endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    cookie: str,
) -> TestClient:
    cfg = Config()
    cfg.data_dir = str(tmp_path)
    cfg.bilibili.cookie = cookie
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)
    database = Database(tmp_path / "auth-export.db")
    database.initialize()
    return TestClient(
        create_app(
            memory_manager=object(),
            database=database,
            soul_engine=object(),
        )
    )


def test_bilibili_auth_export_returns_cookie_and_meta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cookie = (
        "SESSDATA=secret; bili_jct=csrf; "
        "buvid3=buvid-value-3; DedeUserID=123"
    )
    with _client(monkeypatch, tmp_path, cookie=cookie) as client:
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
    with _client(monkeypatch, tmp_path, cookie="") as client:
        response = client.post("/api/bilibili/auth/export", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "B站 Cookie 未配置或已失效"
