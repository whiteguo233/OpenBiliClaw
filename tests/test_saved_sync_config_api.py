from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import ConfigUpdateIn
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, load_config, save_config

if TYPE_CHECKING:
    from pathlib import Path


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-test", model="gpt-4o-mini"),
        )
    )
    config_path = tmp_path / "config.toml"
    save_config(config, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420")


def test_config_api_exposes_and_updates_saved_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    assert client.get("/api/config").json()["saved_sync"] == {"auto_sync_enabled": False}

    response = client.put(
        "/api/config",
        json={"saved_sync": {"auto_sync_enabled": True}},
    )

    assert response.status_code == 202
    assert client.get("/api/config").json()["saved_sync"] == {"auto_sync_enabled": True}
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is True


def test_config_api_rejects_non_boolean_saved_auto_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/config",
        json={"saved_sync": {"auto_sync_enabled": "true"}},
    )

    assert response.status_code == 422
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is False


@pytest.mark.parametrize(
    "payload",
    [
        {"saved_sync": None},
        {"saved_sync": {"auto_sync_enabled": None}},
    ],
    ids=["null-section", "null-field"],
)
def test_config_update_model_rejects_explicit_saved_sync_nulls(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ConfigUpdateIn.model_validate(payload)


def test_config_update_model_allows_omitted_saved_sync() -> None:
    payload = ConfigUpdateIn.model_validate({"language": "en"})

    assert payload.saved_sync is None
    assert "saved_sync" not in payload.model_fields_set


@pytest.mark.parametrize(
    "payload",
    [
        {"saved_sync": None},
        {"saved_sync": {"auto_sync_enabled": None}},
    ],
    ids=["null-section", "null-field"],
)
def test_config_api_rejects_explicit_saved_sync_nulls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    response = client.put("/api/config", json=payload)

    assert response.status_code == 422
    assert load_config(tmp_path / "config.toml").saved_sync.auto_sync_enabled is False


def test_config_api_allows_omitted_saved_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _make_client(monkeypatch, tmp_path)

    response = client.put("/api/config", json={"language": "en"})

    assert response.status_code == 202
    assert response.json()["config"]["saved_sync"] == {"auto_sync_enabled": False}
    loaded = load_config(tmp_path / "config.toml")
    assert loaded.language == "en"
    assert loaded.saved_sync.auto_sync_enabled is False
