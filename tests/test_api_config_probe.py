from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import ConfigServiceProbeIn, ConfigServiceProbeResponse
from openbiliclaw.config import Config, EmbeddingConfig, LLMConfig, LLMProviderConfig, save_config
from openbiliclaw.llm.base import LLMProviderError, LLMResponse

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_config_probe_models_accept_llm_request() -> None:
    payload = ConfigServiceProbeIn(
        kind="llm",
        config={"llm": {"default_provider": "openai"}},
    )

    assert payload.kind == "llm"
    assert payload.config["llm"]["default_provider"] == "openai"


def test_config_probe_response_defaults_to_inline_error_shape() -> None:
    result = ConfigServiceProbeResponse(ok=False, kind="embedding")

    assert result.provider == ""
    assert result.model == ""
    assert result.message == ""
    assert result.error == ""
    assert result.latency_ms == 0


def _client_for_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cfg: Config,
) -> tuple[TestClient, Path]:
    config_path = tmp_path / "config.toml"
    save_config(cfg, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app), config_path


def _probe_base_config() -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-old", model="gpt-old"),
            deepseek=LLMProviderConfig(api_key="sk-new", model="deepseek-chat"),
            embedding=EmbeddingConfig(
                provider="openai",
                model="text-embedding-3-small",
                api_key="sk-embedding-old",
            ),
        )
    )


def test_probe_llm_applies_unsaved_provider_payload_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str | None, dict[str, Any]]] = []

    class FakeRegistry:
        available_providers = ["openai", "deepseek"]
        default_provider = "deepseek"

        def is_chat_capable(self, name: str) -> bool:
            return name == "deepseek"

        async def complete_provider(
            self,
            provider_name: str,
            messages: list[dict[str, str]],  # noqa: ARG002
            **kwargs: Any,
        ) -> LLMResponse:
            calls.append((provider_name, kwargs.get("model"), kwargs))
            return LLMResponse(
                content="OK",
                provider=provider_name,
                model=str(kwargs.get("model") or ""),
            )

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry",
        lambda probe_cfg: FakeRegistry(),
    )
    client, config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())
    before = config_path.read_bytes()

    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm",
            "config": {
                "llm": {
                    "default_provider": "deepseek",
                    "deepseek": {"api_key": "sk-new", "model": "deepseek-chat"},
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["provider"] == "deepseek"
    assert body["model"] == "deepseek-chat"
    assert [(provider, model) for provider, model, _kwargs in calls] == [
        ("deepseek", "deepseek-chat")
    ]
    assert "max_tokens" not in calls[0][2]
    assert config_path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


def test_probe_llm_returns_inline_failure_for_unregistered_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeRegistry:
        available_providers = ["openai"]
        default_provider = "openai"

        def is_chat_capable(self, name: str) -> bool:  # noqa: ARG002
            return False

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry",
        lambda probe_cfg: FakeRegistry(),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {"llm": {"default_provider": "deepseek"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["provider"] == "deepseek"
    assert "not registered" in body["error"]


def test_probe_llm_returns_inline_failure_when_provider_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeRegistry:
        available_providers = ["deepseek"]
        default_provider = "deepseek"

        def is_chat_capable(self, name: str) -> bool:
            return name == "deepseek"

        async def complete_provider(self, *_args: object, **_kwargs: object) -> LLMResponse:
            raise LLMProviderError("bad key")

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry",
        lambda probe_cfg: FakeRegistry(),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "llm", "config": {"llm": {"default_provider": "deepseek"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "bad key" in body["error"]


def test_probe_embedding_returns_success_when_service_probe_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return True

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_embedding_service",
        lambda cfg, registry: FakeEmbeddingService(),
    )
    client, config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())
    before = config_path.read_bytes()

    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "embedding",
            "config": {
                "llm": {
                    "embedding": {
                        "provider": "openai",
                        "api_key": "sk-embedding-new",
                        "model": "text-embedding-3-small",
                    }
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == "embedding"
    assert body["provider"] == "openai"
    assert config_path.read_bytes() == before


def test_probe_embedding_returns_failure_when_provider_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "embedding", "config": {"llm": {"embedding": {"provider": ""}}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "not configured" in body["error"].lower()


def test_probe_embedding_returns_failure_when_service_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEmbeddingService:
        async def probe(self) -> bool:
            return False

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_embedding_service",
        lambda cfg, registry: FakeEmbeddingService(),
    )
    client, _config_path = _client_for_config(monkeypatch, tmp_path, _probe_base_config())

    response = client.post(
        "/api/config/probe-service",
        json={"kind": "embedding", "config": {"llm": {"embedding": {"provider": "openai"}}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "no vector" in body["error"].lower()
