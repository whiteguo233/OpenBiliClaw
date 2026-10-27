"""Configuration API coverage for multi-instance LLM routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.config import (
    Config,
    LLMConfig,
    LLMInstanceConfig,
    LLMProviderConfig,
    ModuleLLMConfig,
    llm_migration_backup_path,
    load_config,
    save_config,
)
from openbiliclaw.llm.base import LLMResponse

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _instance(
    name: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> LLMInstanceConfig:
    return LLMInstanceConfig(
        name=name,
        provider_type="openai_compatible",
        enabled=True,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def _native_config() -> Config:
    return Config(
        llm=LLMConfig(
            instance_routing=True,
            instances={
                "gateway-a": _instance(
                    "网关 A",
                    api_key="secret-a",
                    model="model-a",
                    base_url="https://a.example/v1",
                ),
                "gateway-b": _instance(
                    "网关 B",
                    api_key="secret-b",
                    model="model-b",
                    base_url="https://b.example/v1",
                ),
            },
            default_chain=["gateway-a", "gateway-b"],
            discovery=ModuleLLMConfig(inherit=False, chain=["gateway-b", "gateway-a"]),
        )
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: Config,
) -> tuple[TestClient, Path]:
    path = tmp_path / "config.toml"
    save_config(config, path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

    async def _rebuild(self: RuntimeContext, new_config: Config) -> None:
        self.config = new_config

    async def _restart(
        self: RuntimeContext,
        app: object,  # noqa: ARG001
        *,
        run_post_reload_llm_work: bool = True,  # noqa: ARG001
    ) -> None:
        return None

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _rebuild)
    monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _restart)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8420"), path


def _native_payload_from_get(llm: dict[str, Any]) -> dict[str, Any]:
    return {
        "routing_version": 2,
        "instances": llm["instances"],
        "default_chain": llm["default_chain"],
        "routes": llm["routes"],
        "concurrency": llm["concurrency"],
        "timeout": llm["timeout"],
    }


def test_get_legacy_config_projects_masked_instances_without_rewriting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    legacy = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-legacy-secret", model="gpt-legacy"),
        )
    )
    client, path = _client(monkeypatch, tmp_path, legacy)
    before = path.read_bytes()

    response = client.get("/api/config")

    assert response.status_code == 200
    llm = response.json()["llm"]
    assert llm["routing_version"] == 2
    assert llm["default_chain"] == ["openai"]
    assert llm["instances"]["openai"]["provider_type"] == "openai"
    assert "*" in llm["instances"]["openai"]["api_key"]
    assert "sk-legacy-secret" not in response.text
    assert path.read_bytes() == before
    assert b"routing_version" not in before

    migrated = client.put(
        "/api/config",
        json={"llm": _native_payload_from_get(llm)},
    )

    assert migrated.status_code == 202
    assert migrated.json()["apply_state"] == "queued"
    assert load_config(path).llm.instance_routing is True
    assert llm_migration_backup_path(path).read_bytes() == before


def test_put_native_instances_replaces_deleted_entries_and_preserves_masked_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, path = _client(monkeypatch, tmp_path, _native_config())
    llm = client.get("/api/config").json()["llm"]
    payload = _native_payload_from_get(llm)
    payload["instances"] = {"gateway-a": llm["instances"]["gateway-a"]}
    payload["default_chain"] = ["gateway-a"]
    payload["routes"] = {
        name: {"inherit": True, "chain": []}
        for name in ("soul", "discovery", "recommendation", "evaluation")
    }

    response = client.put("/api/config", json={"llm": payload})

    assert response.status_code == 202
    assert response.json()["apply_state"] == "queued"
    stored = load_config(path)
    assert list(stored.llm.instances) == ["gateway-a"]
    assert stored.llm.instances["gateway-a"].api_key == "secret-a"
    assert stored.llm.default_chain == ["gateway-a"]
    assert stored.llm.discovery.inherit is True
    assert '[llm.instances."gateway-b"]' not in path.read_text(encoding="utf-8")


def test_put_native_invalid_chain_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, path = _client(monkeypatch, tmp_path, _native_config())
    before = path.read_bytes()
    llm = client.get("/api/config").json()["llm"]
    payload = _native_payload_from_get(llm)
    payload["default_chain"] = ["missing-instance"]

    response = client.put("/api/config", json={"llm": payload})

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert any(
        issue["field"] == "llm.default_chain"
        and issue["severity"] == "blocking"
        and "不存在" in issue["message"]
        for issue in body["config"]["issues"]
    )
    assert path.read_bytes() == before


def test_legacy_extension_update_targets_first_matching_instance_without_collapsing_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client, path = _client(monkeypatch, tmp_path, _native_config())

    response = client.put(
        "/api/config",
        json={
            "llm": {
                "default_provider": "openai_compatible",
                "openai_compatible": {
                    "api_key": "********",
                    "model": "updated-primary",
                },
            }
        },
    )

    assert response.status_code == 202
    assert response.json()["apply_state"] == "queued"
    stored = load_config(path)
    assert stored.llm.default_chain == ["gateway-a", "gateway-b"]
    assert stored.llm.instances["gateway-a"].model == "updated-primary"
    assert stored.llm.instances["gateway-a"].api_key == "secret-a"
    assert stored.llm.instances["gateway-b"].model == "model-b"


def test_probe_exact_instance_and_full_chain_use_distinct_paths_without_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeRegistry:
        default_provider = "gateway-a"

        def is_chat_capable(self, name: str) -> bool:
            return name in {"gateway-a", "gateway-b"}

        def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
            return "openai_compatible"

        def get(self, name: str) -> SimpleNamespace:  # noqa: ARG002
            return SimpleNamespace(_model="fake-model")

        async def complete_provider(
            self,
            provider_name: str,
            messages: list[dict[str, str]],  # noqa: ARG002
            **kwargs: Any,  # noqa: ARG002
        ) -> LLMResponse:
            calls.append(("instance", provider_name))
            return LLMResponse(
                content="OK",
                provider="openai_compatible",
                instance_id=provider_name,
                model="fake-model",
            )

        async def complete(
            self,
            messages: list[dict[str, str]],  # noqa: ARG002
            **kwargs: Any,  # noqa: ARG002
        ) -> LLMResponse:
            calls.append(("chain", "gateway-b"))
            return LLMResponse(
                content="OK",
                provider="openai_compatible",
                instance_id="gateway-b",
                model="fake-model",
            )

    monkeypatch.setattr(
        "openbiliclaw.llm.registry.build_llm_registry",
        lambda config: FakeRegistry(),
    )
    client, path = _client(monkeypatch, tmp_path, _native_config())
    before = path.read_bytes()
    llm = client.get("/api/config").json()["llm"]
    config_payload = {"llm": _native_payload_from_get(llm)}

    exact = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm_instance",
            "instance_id": "gateway-a",
            "config": config_payload,
        },
    )
    chain = client.post(
        "/api/config/probe-service",
        json={
            "kind": "llm_chain",
            "config": config_payload,
        },
    )

    assert exact.status_code == 200
    assert exact.json()["instance_id"] == "gateway-a"
    assert chain.status_code == 200
    assert chain.json()["instance_id"] == "gateway-b"
    assert calls == [("instance", "gateway-a"), ("chain", "gateway-b")]
    assert path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


def test_discover_models_uses_exact_draft_instance_and_preserves_masked_secret_without_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, str] = {}

    async def fake_list_models(self: Any) -> list[str]:
        observed["api_key"] = str(self._client.api_key)
        observed["base_url"] = str(self.base_url)
        return ["model-a", "model-b"]

    monkeypatch.setattr(
        "openbiliclaw.llm.openai_provider.OpenAIProvider.list_models",
        fake_list_models,
    )
    client, path = _client(monkeypatch, tmp_path, _native_config())
    before = path.read_bytes()
    llm = client.get("/api/config").json()["llm"]
    payload = _native_payload_from_get(llm)
    payload["instances"]["gateway-a"]["base_url"] = "https://draft.example/v1"

    response = client.post(
        "/api/config/discover-models",
        json={
            "instance_id": "gateway-a",
            "config": {"llm": payload},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["instance_id"] == "gateway-a"
    assert body["provider"] == "openai_compatible"
    assert body["models"] == ["model-a", "model-b"]
    assert body["reasoning_efforts"] == [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert body["reasoning_efforts_source"] == "local_advisory"
    assert observed == {
        "api_key": "secret-a",
        "base_url": "https://draft.example/v1",
    }
    assert path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


def test_discover_models_redacts_provider_secret_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_list_models(self: Any) -> list[str]:
        raise RuntimeError(f"gateway rejected {self._client.api_key}")

    monkeypatch.setattr(
        "openbiliclaw.llm.openai_provider.OpenAIProvider.list_models",
        fake_list_models,
    )
    client, path = _client(monkeypatch, tmp_path, _native_config())
    before = path.read_bytes()

    response = client.post(
        "/api/config/discover-models",
        json={
            "instance_id": "gateway-a",
            "config": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "[REDACTED]" in body["error"]
    assert "secret-a" not in response.text
    assert path.read_bytes() == before


def test_discover_models_reports_non_openai_protocol_provider_as_manual_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = Config(
        llm=LLMConfig(
            instance_routing=True,
            instances={
                "claude-main": LLMInstanceConfig(
                    name="Claude",
                    provider_type="claude",
                    enabled=True,
                    api_key="secret",
                    model="claude-sonnet-4-6",
                )
            },
            default_chain=["claude-main"],
        )
    )
    client, path = _client(monkeypatch, tmp_path, config)
    before = path.read_bytes()

    response = client.post(
        "/api/config/discover-models",
        json={"instance_id": "claude-main", "config": {}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["provider"] == "claude"
    assert body["models"] == []
    assert body["reasoning_efforts"] == ["low", "medium", "high"]
    assert "does not expose" in body["error"]
    assert path.read_bytes() == before
