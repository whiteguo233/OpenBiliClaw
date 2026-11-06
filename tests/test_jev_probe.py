"""A user-triggered draft probe uses real native HTTP without saving settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.jev_fake_backend import FakeJevBackend

from .test_jev_evaluation_config import _client, _configured

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("mode", ["ok", "missing_answer", "wrong_model", "server_error"])
def test_jev_draft_probe_validates_full_evaluation_without_saving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    client = _client(monkeypatch, tmp_path, _configured())
    before = (tmp_path / "config.toml").read_bytes()
    with FakeJevBackend(model="jev-1.14.0", mode=mode) as backend:
        response = client.post(
            "/api/config/probe-service",
            json={
                "kind": "jev",
                "config": {
                    "decision": {
                        "providers": {
                            "jev": {
                                "enabled": True,
                                "base_url": backend.url.removesuffix("/v1/systemone"),
                                "api_key": "synthetic-test-key",
                                "model": "jev-1.14.0",
                                "max_attempts": 3,
                            }
                        }
                    }
                },
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["kind"] == "jev" and result["ok"] is (mode == "ok")
        assert len(backend.requests) == 1  # A click never silently repeats a billed request.
        assert len(backend.requests[0]["questions"]) == 10
        assert "synthetic-test-key" not in response.text
        if mode != "ok":
            assert result["error"]
    assert (tmp_path / "config.toml").read_bytes() == before


def test_jev_probe_rejects_invalid_capacity_before_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path, _configured())
    with FakeJevBackend(model="operator-fixed") as backend:
        response = client.post(
            "/api/config/probe-service",
            json={
                "kind": "jev",
                "config": {
                    "decision": {
                        "providers": {
                            "jev": {
                                "enabled": True,
                                "endpoint_url": backend.url,
                                "api_key": "synthetic-test-key",
                                "model": "operator-fixed",
                            }
                        }
                    }
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert response.json()["error"] == "invalid_config"
        assert not backend.requests
