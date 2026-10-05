from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config

if TYPE_CHECKING:
    from pathlib import Path

_TEST_AUTH_CREDENTIAL = "tskey-" + "auth-privatebootstrap"
_TEST_OAUTH_CREDENTIAL = "tskey-" + "client-privatebootstrap"


def _build_app(
    monkeypatch,
    tmp_path: Path,
    config: Config,
):
    from openbiliclaw.config import save_config

    config.llm.deepseek.api_key = "test-deepseek-key"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda value, path=None, **kwargs: save_config(value, config_path, **kwargs),
    )
    return create_app(memory_manager=object(), database=object(), soul_engine=object())


def _local_client(app) -> TestClient:
    return TestClient(
        app,
        client=("127.0.0.1", 5000),
        base_url="http://127.0.0.1:8420",
    )


def test_config_get_exposes_safe_tailnet_shape(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.tailnet.enabled = True
    app = _build_app(monkeypatch, tmp_path, config)
    status_dir = config.data_path / "tailnet"
    status_dir.mkdir(mode=0o700, parents=True)
    (status_dir / "status.json").write_text(
        json.dumps(
            {
                "protocol": 1,
                "event": "ready",
                "dns_name": "openbiliclaw-host.example.ts.net",
                "ips": ["100.64.0.8"],
                "port": 8420,
                "auth_url": "https://login.tailscale.com/a/private-capability",
            }
        ),
        encoding="utf-8",
    )

    with _local_client(app) as client:
        tailnet = client.get("/api/config").json()["tailnet"]

    assert tailnet == {
        "enabled": True,
        "hostname": "openbiliclaw-host",
        "bootstrap_credential_staged": False,
        "state": "ready",
        "dns_name": "openbiliclaw-host.example.ts.net",
        "ips": ["100.64.0.8"],
        "port": 8420,
    }
    assert "auth_url" not in tailnet


def test_config_put_stages_oauth_secret_without_echo_or_config_persistence(
    monkeypatch, tmp_path: Path
) -> None:
    config = Config()
    app = _build_app(monkeypatch, tmp_path, config)
    secret = _TEST_OAUTH_CREDENTIAL
    status_dir = config.data_path / "tailnet"
    status_dir.mkdir(mode=0o700, parents=True)
    (status_dir / "status.json").write_text(
        json.dumps({"protocol": 1, "event": "ready", "port": 8420}),
        encoding="utf-8",
    )

    with _local_client(app) as client:
        response = client.put(
            "/api/config",
            json={
                "tailnet": {
                    "enabled": True,
                    "hostname": "My-OBC-Host",
                    "bootstrap_credential": secret,
                    "advertise_tags": ["tag:openbiliclaw"],
                }
            },
        )
        body = response.json()
        assert response.status_code == 202, body.get("config", {}).get("issues")
        assert body["restart_required"] is True
        assert body["config"]["tailnet"]["bootstrap_credential_staged"] is True
        assert body["config"]["tailnet"]["state"] == "credential_staged"
        assert secret not in json.dumps(body)

        staged_path = config.data_path / "tailnet" / ".bootstrap-credential.json"
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        assert staged["credential"] == secret
        assert staged["advertise_tags"] == ["tag:openbiliclaw"]
        if os.name != "nt":
            assert stat.S_IMODE(staged_path.stat().st_mode) == 0o600
        assert secret not in (tmp_path / "config.toml").read_text(encoding="utf-8")

        cleared = client.put(
            "/api/config",
            json={
                "tailnet": {
                    "enabled": True,
                    "hostname": "my-obc-host",
                    "clear_bootstrap_credential": True,
                }
            },
        )
        assert cleared.status_code == 202
        assert not staged_path.exists()

    assert config.tailnet.enabled is True
    assert config.tailnet.hostname == "my-obc-host"


def test_config_clear_removes_staged_secret_from_active_and_next_data_dirs(
    monkeypatch, tmp_path: Path
) -> None:
    from openbiliclaw.runtime.tailnet_supervisor import stage_tailnet_bootstrap

    config = Config()
    app = _build_app(monkeypatch, tmp_path, config)
    active_staged = stage_tailnet_bootstrap(config, _TEST_AUTH_CREDENTIAL)
    next_data_dir = tmp_path / "next-data"

    with _local_client(app) as client:
        response = client.put(
            "/api/config",
            json={
                "data_dir": str(next_data_dir),
                "tailnet": {
                    "enabled": False,
                    "hostname": "openbiliclaw-host",
                    "clear_bootstrap_credential": True,
                },
            },
        )

    assert response.status_code == 202
    assert response.json()["restart_required"] is True
    assert not active_staged.exists()
    assert not (next_data_dir / "tailnet" / ".bootstrap-credential.json").exists()


def test_config_put_accepts_auth_key_without_oauth_tag(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    app = _build_app(monkeypatch, tmp_path, config)

    with _local_client(app) as client:
        response = client.put(
            "/api/config",
            json={
                "tailnet": {
                    "enabled": True,
                    "hostname": "openbiliclaw-host",
                    "bootstrap_credential": _TEST_AUTH_CREDENTIAL,
                }
            },
        )

    assert response.status_code == 202, response.json().get("config", {}).get("issues")


def test_config_put_rejects_oauth_without_tag_before_saving(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    app = _build_app(monkeypatch, tmp_path, config)

    with _local_client(app) as client:
        response = client.put(
            "/api/config",
            json={
                "tailnet": {
                    "enabled": True,
                    "hostname": "openbiliclaw-host",
                    "bootstrap_credential": _TEST_OAUTH_CREDENTIAL,
                }
            },
        )

    assert response.status_code == 400
    assert "at least one allowed device tag" in str(response.json()["detail"])
    assert not (config.data_path / "tailnet" / ".bootstrap-credential.json").exists()


def test_remote_config_cannot_stage_tailnet_secret(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    app = _build_app(monkeypatch, tmp_path, config)
    remote = TestClient(
        app,
        client=("192.168.1.50", 5000),
        base_url="http://192.168.1.10:8420",
    )

    with remote:
        response = remote.put(
            "/api/config",
            json={
                "tailnet": {
                    "enabled": True,
                    "hostname": "openbiliclaw-host",
                    "bootstrap_credential": _TEST_AUTH_CREDENTIAL,
                }
            },
        )

    assert response.status_code == 403
    assert "只能在运行后端的电脑上" in str(response.json()["detail"])
    assert not (config.data_path / "tailnet" / ".bootstrap-credential.json").exists()
