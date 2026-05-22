from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import build_runtime_context
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config
from openbiliclaw.llm.registry import RegistryBuildError


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _invalid_config(tmp_path) -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="", model="gpt-4o-mini"),
            ollama=LLMProviderConfig(model="", base_url=""),
        ),
        data_dir=str(tmp_path / "data"),
    )


def _valid_config(tmp_path) -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-valid-openai-key", model="gpt-4o-mini"),
        ),
        data_dir=str(tmp_path / "data"),
    )


def _save_project_config(monkeypatch: pytest.MonkeyPatch, tmp_path, cfg: Config) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(cfg, tmp_path / "config.toml")


def test_build_runtime_context_stays_strict_for_invalid_llm_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)

    with pytest.raises(RegistryBuildError):
        build_runtime_context(_invalid_config(tmp_path))


def test_create_app_boots_degraded_when_registry_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))

    app = create_app()
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["reason"] == "llm_registry_unavailable"
    assert body["issues"]
    assert body["issues"][0]["severity"] == "blocking"


def test_degraded_config_get_includes_recovery_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["degraded_reason"] == "llm_registry_unavailable"
    assert any(issue["severity"] == "blocking" for issue in body["issues"])


def test_degraded_config_put_saves_recovery_config_and_requires_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"api_key": "sk-new-valid-key"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is False
    assert body["restart_required"] is True
    assert "restart" in body["message"].lower()
    assert "sk-new-valid-key" in (tmp_path / "config.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("method", "path", "json_payload"),
    [
        ("get", "/api/recommendations", None),
        ("get", "/api/profile-summary", None),
        ("post", "/api/events", {"events": []}),
        ("post", "/api/sources/xhs/observed-urls", {"items": []}),
    ],
)
def test_degraded_non_config_endpoints_return_503(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    method: str,
    path: str,
    json_payload: dict[str, object] | None,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    request = getattr(client, method)
    response = request(path, json=json_payload) if json_payload is not None else request(path)

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["reason"] == "llm_registry_unavailable"


def test_degraded_runtime_stream_sends_degraded_event_and_stays_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    client = TestClient(create_app())

    with client.websocket_connect("/api/runtime-stream") as websocket:
        event = websocket.receive_json()
        assert event["type"] == "degraded"
        assert event["reason"] == "llm_registry_unavailable"
        assert event["issues"]


def test_normal_boot_health_payload_reports_profile_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _valid_config(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "openbiliclaw-api"
    assert body["profile_ready"] is False


def test_restart_after_degraded_recovery_config_boots_normal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_llm_env(monkeypatch)
    _save_project_config(monkeypatch, tmp_path, _invalid_config(tmp_path))
    degraded_client = TestClient(create_app())

    response = degraded_client.put(
        "/api/config",
        json={"llm": {"openai": {"api_key": "sk-new-valid-key"}}},
    )

    assert response.status_code == 200
    normal_client = TestClient(create_app())
    health = normal_client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["service"] == "openbiliclaw-api"
    assert health["profile_ready"] is False
