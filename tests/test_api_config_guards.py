from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import (
    Config,
    EmbeddingConfig,
    LLMConfig,
    LLMProviderConfig,
    save_config,
)
from openbiliclaw.config import (
    load_config as load_config_from_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_client(
    monkeypatch,
    tmp_path: Path,
    initial_cfg: Config,
) -> tuple[TestClient, Config, Path]:
    config_path = tmp_path / "config.toml"
    save_config(initial_cfg, config_path)

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: initial_cfg)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        lambda cfg, path=None: save_config(cfg, config_path),
    )

    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), initial_cfg, config_path


def _base_config() -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="sk-real-key-1234567890abcdef",
                model="gpt-4o-mini",
            ),
            claude=LLMProviderConfig(api_key="claude-real-key", model="claude-3-5-haiku"),
            deepseek=LLMProviderConfig(api_key="deepseek-real-key", model="deepseek-chat"),
            openrouter=LLMProviderConfig(api_key="openrouter-real-key", model="openrouter/auto"),
            openai_compatible=LLMProviderConfig(
                api_key="compat-real-key",
                model="mimo-v2.5-pro",
                base_url="https://token-plan-sgp.xiaomimimo.com/v1",
            ),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-embedding-real-key",
                base_url="https://embed.example.com/v1",
            ),
        )
    )


def test_put_config_ignores_masked_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": "sk-d****cdef"}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_ignores_empty_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": ""}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_writes_real_new_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"api_key": "sk-new-real-key-fedcba0987654321"}}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == (
        "sk-new-real-key-fedcba0987654321"
    )


def test_put_config_ignores_empty_chat_provider_model(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"model": ""}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.model == "gpt-4o-mini"


def test_put_config_writes_real_new_chat_provider_model(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.model == "gpt-4.1-mini"


def test_put_config_round_trips_openai_auth_mode(monkeypatch, tmp_path) -> None:
    from openbiliclaw.llm.codex_auth import CodexCredentials

    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.load_codex_credentials",
        lambda: CodexCredentials("access-token", "refresh-token", 9999999999),
    )

    response = client.put(
        "/api/config",
        json={"llm": {"openai": {"auth_mode": "codex_oauth"}}},
    )

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.auth_mode == "codex_oauth"
    get_response = client.get("/api/config")
    assert get_response.status_code == 200
    assert get_response.json()["llm"]["openai"]["auth_mode"] == "codex_oauth"


def test_put_config_ignores_whitespace_only_chat_provider_api_key(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"llm": {"openai": {"api_key": "   "}}})

    assert response.status_code == 200
    assert load_config_from_path(config_path).llm.openai.api_key == "sk-real-key-1234567890abcdef"


def test_put_config_uses_same_guard_for_other_chat_providers(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    for provider_name in ("claude", "deepseek", "openrouter", "openai_compatible"):
        before = getattr(load_config_from_path(config_path).llm, provider_name).api_key
        masked = before[:2] + "****" + before[-2:]
        response = client.put(
            "/api/config",
            json={"llm": {provider_name: {"api_key": masked}}},
        )
        assert response.status_code == 200
        assert getattr(load_config_from_path(config_path).llm, provider_name).api_key == before

        response = client.put(
            "/api/config",
            json={"llm": {provider_name: {"api_key": ""}}},
        )
        assert response.status_code == 200
        assert getattr(load_config_from_path(config_path).llm, provider_name).api_key == before


def test_put_config_explicit_reset_clears_allowlisted_secret(monkeypatch, tmp_path) -> None:
    client, cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put("/api/config", json={"reset_fields": ["llm.openai.api_key"]})

    assert response.status_code == 200
    assert cfg.llm.openai.api_key == ""
    assert load_config_from_path(config_path).llm.openai.api_key == ""


def test_put_config_unknown_reset_is_rejected_without_mutation(monkeypatch, tmp_path) -> None:
    client, cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())
    before = config_path.read_text(encoding="utf-8")

    response = client.put(
        "/api/config",
        json={
            "reset_fields": ["storage.db_path"],
            "llm": {"openai": {"model": "gpt-4.1-mini"}},
        },
    )

    assert response.status_code == 400
    assert config_path.read_text(encoding="utf-8") == before
    assert cfg.llm.openai.model == "gpt-4o-mini"


def test_put_config_ignores_empty_embedding_model_and_base_url(monkeypatch, tmp_path) -> None:
    client, _cfg, config_path = _make_client(monkeypatch, tmp_path, _base_config())

    response = client.put(
        "/api/config",
        json={"llm": {"embedding": {"model": "", "base_url": ""}}},
    )

    assert response.status_code == 200
    embedding = load_config_from_path(config_path).llm.embedding
    assert embedding.model == "text-embedding-3-small"
    assert embedding.base_url == "https://embed.example.com/v1"
