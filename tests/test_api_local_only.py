"""Remote access must be explicitly authenticated even before password setup."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .test_api_auth import _build_app, _loopback, _remote


@pytest.mark.parametrize("path", ["/api/favorites/BV1AUTH", "/api/chat/sessions", "/api/config"])
def test_disabled_password_does_not_make_private_reads_public(tmp_path, monkeypatch, path):
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    response = _remote(app).get(path)
    assert response.status_code == 401
    assert response.json()["error"] == "auth_setup_required"


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream", "/api/chat/turns"])
def test_disabled_password_blocks_remote_model_work(tmp_path, monkeypatch, path):
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    response = _remote(app).post(path, json={"message": "must not execute"})
    assert response.status_code == 401


def test_disabled_password_reports_remote_setup_requirement(tmp_path, monkeypatch):
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    remote = _remote(app).get("/api/auth/status").json()
    assert remote["authenticated"] is False
    assert remote["setup_required"] is True
    local = _loopback(app).get("/api/auth/status").json()
    assert local["authenticated"] is True
    assert local["setup_required"] is False
    assert _loopback(app).get("/api/favorites/BV1AUTH").status_code == 200


def test_disabled_password_still_rejects_cross_origin_loopback(tmp_path, monkeypatch):
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    assert (
        _loopback(app)
        .get("/api/favorites/BV1AUTH", headers={"Origin": "https://untrusted.invalid"})
        .status_code
        == 401
    )


def test_disabled_password_blocks_remote_websocket(tmp_path, monkeypatch):
    app, _ = _build_app(tmp_path, monkeypatch, enabled=False)
    client = TestClient(app, client=("192.168.1.50", 5000))
    with (
        pytest.raises(WebSocketDisconnect) as rejected,
        client.websocket_connect("/api/runtime-stream"),
    ):
        pass
    assert rejected.value.code == 4401
