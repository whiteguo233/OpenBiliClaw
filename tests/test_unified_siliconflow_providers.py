"""The protected free transport obeys the same route contract as user endpoints."""

from __future__ import annotations

import asyncio
import shlex
import sys
from dataclasses import replace
from typing import Any

import pytest

from openbiliclaw.config import (
    Config,
    EmbeddingConfig,
    LLMConfig,
    LLMInstanceConfig,
    ModuleLLMConfig,
    builtin_siliconflow_instance,
    effective_llm_instances,
    load_config,
    migrate_sponsored_provider_config,
    save_config,
)
from openbiliclaw.llm.base import LLMProvider, LLMRegistry, LLMResponse, LLMStreamChunk
from openbiliclaw.llm.registry import build_embedding_service, build_llm_registry
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMService,
    module_overrides_from_config,
)
from openbiliclaw.llm.sponsored_adapter import uses_sponsored_route
from openbiliclaw.llm.sponsored_errors import (
    SponsoredContractMismatchError,
    SponsoredUnavailableError,
)
from openbiliclaw.llm.sponsored_profiles import ALTERNATE_CHAT_MODEL, DEFAULT_CHAT_MODEL
from openbiliclaw.llm.sponsored_router import close_shared_provider
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
)


class OwnProvider(LLMProvider):
    name = "openai_compatible"

    async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="own", model="own-model", provider=self.name)


def config_at(tmp_path: Any) -> Config:
    return Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(
            instance_routing=True,
            builtin_providers_version=1,
            instances={
                "free8": builtin_siliconflow_instance(),
                "free4": builtin_siliconflow_instance(ALTERNATE_CHAT_MODEL),
                "own": LLMInstanceConfig(
                    name="自有",
                    provider_type="openai_compatible",
                    api_key="test",
                    base_url="https://example.invalid/v1",
                    model="own-model",
                ),
            },
            default_chain=["free8", "free4"],
            soul=ModuleLLMConfig(inherit=False, chain=["own"]),
            sponsored=SponsoredRuntimeSettings(
                runtime_path=shlex.join(
                    [
                        sys.executable,
                        "-m",
                        "openbiliclaw.llm.sponsored_mock_runtime",
                        "--approved",
                        "--stream",
                    ]
                )
            ),
        ),
    )


def service_for(cfg: Config) -> LLMService:
    return LLMService(
        registry=build_llm_registry(cfg, provider_overrides={"own": OwnProvider()}),
        memory=None,
        module_overrides=module_overrides_from_config(cfg),
    )


@pytest.mark.parametrize("transport", ["runtime", "relay", "unavailable"])
def test_cli_free_embedding_reuses_transport_and_preserves_config_on_install_failure(
    tmp_path: Any, monkeypatch: Any, transport: str
) -> None:
    from openbiliclaw import cli
    from openbiliclaw.runtime.sponsored_bundle import BundleError

    cfg = config_at(tmp_path)
    cfg.llm.sponsored = SponsoredRuntimeSettings(
        runtime_path="test-runtime" if transport == "runtime" else "",
        relay_url="https://relay.example.invalid" if transport == "relay" else "",
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_a, **_kw: "free")
    saved = []
    monkeypatch.setattr(cli, "_save_embedding_config", lambda **kw: saved.append(kw))

    def prepare() -> None:
        assert transport == "unavailable", "configured transports must not download an SDK"
        raise BundleError("test download unavailable")

    monkeypatch.setattr("openbiliclaw.runtime.sponsored_bundle.ensure_runtime", prepare)
    cli._interactive_embedding_setup("openai")
    assert saved == (
        []
        if transport == "unavailable"
        else [{"provider": "siliconflow_free", "model": "BAAI/bge-m3"}]
    )


@pytest.mark.asyncio
async def test_evaluation_harness_honors_its_native_instance_chain(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from types import SimpleNamespace

    from openbiliclaw.eval.sponsored_agents import evaluation_uses_sponsored, sponsored_agent_text

    cfg = config_at(tmp_path)
    cfg.llm.evaluation = ModuleLLMConfig(inherit=False, chain=["free4"])
    install_sponsored_settings(cfg.llm.sponsored, data_path=cfg.data_path)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    calls = []

    class Endpoint:
        async def complete(self, messages: Any, **kwargs: Any) -> LLMResponse:
            calls.append(kwargs["caller"])
            return LLMResponse(content='{"summary":"done"}', model=ALTERNATE_CHAT_MODEL)

    def endpoint(**kwargs: Any) -> Endpoint:
        assert kwargs["model"] == ALTERNATE_CHAT_MODEL
        return Endpoint()

    monkeypatch.setattr("openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider", endpoint)
    assert evaluation_uses_sponsored()
    result = await sponsored_agent_text(
        "evaluate", SimpleNamespace(system_prompt="review", max_turns=1)
    )
    assert result is not None and result[1] == '{"summary":"done"}'
    assert calls == ["eval.agent"]
    cfg.llm.evaluation = ModuleLLMConfig(inherit=False, chain=["own"])
    assert not evaluation_uses_sponsored()


@pytest.mark.asyncio
async def test_free_instance_obeys_module_override_and_can_be_only_embedding(tmp_path: Any) -> None:
    cfg = config_at(tmp_path)
    service = service_for(cfg)
    try:
        response = await service.complete_with_core_memory(
            system_instruction="Reply OK",
            user_input="test",
            caller="soul.profile_build",
        )
        assert response.content == "own" and response.instance_id == "own"
        assert not uses_sponsored_route(service, "soul.profile_build")
        assert uses_sponsored_route(service, "discovery.evaluate_batch")
        cfg.llm.default_chain = ["own"]
        cfg.llm.embedding = EmbeddingConfig(provider="siliconflow_free", model="BAAI/bge-m3")
        embedding = build_embedding_service(cfg, LLMRegistry())
        assert embedding is not None
        assert len(await embedding.embed("独立云端向量")) == 1024
        assert not cfg.llm.sponsored.enabled
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,instance_id", [(DEFAULT_CHAT_MODEL, "free8"), (ALTERNATE_CHAT_MODEL, "free4")]
)
async def test_real_ipc_stream_and_fixed_task_use_selected_instance(
    tmp_path: Any, model: str, instance_id: str
) -> None:
    cfg = config_at(tmp_path)
    cfg.llm.default_chain = [instance_id]
    cfg.llm.soul = ModuleLLMConfig()
    service = service_for(cfg)
    try:
        chunks = [
            chunk
            async for chunk in service.stream_complete_with_core_memory(
                system_instruction="Reply OK",
                user_input="你好",
                caller="agent.chat",
            )
        ]
        assert any(c.delta for c in chunks)
        assert chunks[-1].response is not None
        assert chunks[-1].response.model == model
        assert chunks[-1].response.instance_id == instance_id
        response = await service.execute_sponsored_task(
            caller="soul.consolidation",
            payload={"likes_clusters": [], "dislikes_clusters": []},
            fallback_system_instruction="fallback",
            fallback_user_input="fallback",
        )
        assert response.model == model and response.instance_id == instance_id
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_normal_chain_fallback_uses_second_instance_and_never_unlisted_paid(
    tmp_path: Any, monkeypatch: Any
) -> None:
    cfg = config_at(tmp_path)
    calls: list[str] = []

    class Endpoint:
        def __init__(self, model: str) -> None:
            self.model = model

        async def complete(self, *args: Any, **kwargs: Any) -> LLMResponse:
            calls.append(self.model)
            if self.model == DEFAULT_CHAT_MODEL:
                raise SponsoredUnavailableError("temporary")
            return LLMResponse(content="backup", model=self.model)

    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider",
        lambda *, model, settings: Endpoint(model),
    )
    service = service_for(cfg)
    response = await service.complete_with_core_memory(system_instruction="test", user_input="test")
    assert response.instance_id == "free4"
    assert calls == [DEFAULT_CHAT_MODEL, ALTERNATE_CHAT_MODEL]
    cfg.llm.default_chain = ["free8"]
    service = service_for(cfg)
    with pytest.raises(LLMProviderExecutionError):
        await service.complete_with_core_memory(system_instruction="test", user_input="test")
    cfg.llm.default_chain = ["free8", "own"]
    response = await service_for(cfg).complete_with_core_memory(
        system_instruction="test", user_input="test"
    )
    assert response.instance_id == "own"


@pytest.mark.asyncio
async def test_stream_failure_after_content_cannot_switch_instances(
    tmp_path: Any, monkeypatch: Any
) -> None:
    calls = []
    closed = []

    class Endpoint:
        async def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
            try:
                yield LLMStreamChunk(delta="已输出")
                raise SponsoredUnavailableError("stream dropped")
            finally:
                closed.append(True)

    def endpoint(**kwargs: Any) -> Any:
        calls.append(kwargs["model"])
        return Endpoint()

    monkeypatch.setattr("openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider", endpoint)
    chunks = []
    with pytest.raises(LLMProviderExecutionError):
        async for chunk in service_for(config_at(tmp_path)).stream_complete_with_core_memory(
            system_instruction="test", user_input="test"
        ):
            chunks.append(chunk)
    assert len(chunks) == 1 and calls == [DEFAULT_CHAT_MODEL] and closed == [True]


@pytest.mark.asyncio
async def test_contract_error_is_not_hidden_by_paid_fallback(
    tmp_path: Any, monkeypatch: Any
) -> None:
    class Endpoint:
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise SponsoredContractMismatchError("contract drift")

    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider", lambda **kwargs: Endpoint()
    )
    cfg = config_at(tmp_path)
    cfg.llm.default_chain = ["free8", "own"]
    with pytest.raises(SponsoredContractMismatchError):
        await service_for(cfg).complete_with_core_memory(
            system_instruction="test", user_input="test"
        )


def test_migration_preserves_own_routes_and_explicit_free_fallback(tmp_path: Any) -> None:
    cfg = config_at(tmp_path)
    cfg.llm.default_chain = ["own"]
    cfg.llm.sponsored = replace(cfg.llm.sponsored, enabled=True, model=ALTERNATE_CHAT_MODEL)
    migrate_sponsored_provider_config(cfg.llm)
    assert not cfg.llm.sponsored.enabled
    assert [cfg.llm.instances[i].model for i in cfg.llm.default_chain] == [
        ALTERNATE_CHAT_MODEL,
        DEFAULT_CHAT_MODEL,
    ]
    assert cfg.llm.soul.chain == ["own"]
    assert cfg.llm.instances["own"].api_key == "test"
    assert cfg.llm.embedding.provider == "siliconflow_free"
    path = save_config(cfg, tmp_path / "config.toml")
    loaded = load_config(path, consult_environment=False)
    assert loaded.llm.default_chain == cfg.llm.default_chain
    assert loaded.llm.instances["own"].api_key == "test"
    assert loaded.llm.embedding.provider == "siliconflow_free"
    assert "enabled = false" in path.read_text()


def test_preset_is_available_without_becoming_the_active_route() -> None:
    cfg = Config()
    assert effective_llm_instances(cfg.llm)["siliconflow-free"].provider_type == "siliconflow_free"
    assert cfg.llm.default_provider == "deepseek"


@pytest.mark.asyncio
async def test_parallel_instances_do_not_share_model_selection(tmp_path: Any) -> None:
    cfg = config_at(tmp_path)
    registry = build_llm_registry(cfg, provider_overrides={"own": OwnProvider()})
    try:
        responses = await asyncio.gather(
            *(
                registry.complete_provider(instance_id, [{"role": "user", "content": "你好"}])
                for instance_id in ["free8", "free4"] * 2
            )
        )
        assert [r.model for r in responses] == [DEFAULT_CHAT_MODEL, ALTERNATE_CHAT_MODEL] * 2
        assert [r.instance_id for r in responses] == ["free8", "free4"] * 2
    finally:
        await close_shared_provider()
        install_sponsored_settings(SponsoredRuntimeSettings())


def test_native_config_api_keeps_free_instances_and_embedding_independent(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from .test_api_llm_instance_routing import _client, _native_payload_from_get

    cfg = config_at(tmp_path)
    cfg.llm.embedding = EmbeddingConfig(
        provider="gemini", api_key="embedding-secret", model="gemini-embedding-2"
    )
    client, path = _client(monkeypatch, tmp_path, cfg)
    value = client.get("/api/config").json()["llm"]
    assert value["instances"]["free8"]["api_key"] == ""
    assert value["instances"]["free8"]["base_url"] == ""
    assert "runtime_path" not in value["sponsored"]
    payload = _native_payload_from_get(value)
    payload["default_chain"] = ["own"]
    payload["embedding"] = {"provider": "siliconflow_free", "model": "BAAI/bge-m3"}
    response = client.put("/api/config", json={"llm": payload})
    assert response.status_code == 202, response.text
    saved = load_config(path, consult_environment=False)
    assert saved.llm.default_chain == ["own"]
    assert saved.llm.soul.chain == ["own"]
    assert saved.llm.instances["own"].api_key == "test"
    assert saved.llm.embedding.provider == "siliconflow_free"
    assert saved.llm.embedding.api_key == ""
    assert not saved.llm.sponsored.enabled


@pytest.mark.parametrize(
    "invalid",
    [
        {"model": "arbitrary-model"},
        {"name": "edited"},
        {"enabled": False},
        {"provider_type": "ollama"},
        {"reasoning_effort": "high"},
        {"num_ctx": 4096},
        {"model": "Qwen/Qwen3.5-4B"},
        {"read_only": False, "model": "Qwen/Qwen3.5-4B"},
        {"api_key": "custom-key"},
        {"base_url": "https://example.invalid/v1"},
        {"auth_mode": "codex_oauth"},
        {"api_flavor": "responses"},
    ],
)
def test_free_instance_api_rejects_contract_override_atomically(
    tmp_path: Any, monkeypatch: Any, invalid: dict[str, Any]
) -> None:
    from .test_api_llm_instance_routing import _client, _native_payload_from_get

    client, path = _client(monkeypatch, tmp_path, config_at(tmp_path))
    before = path.read_bytes()
    payload = _native_payload_from_get(client.get("/api/config").json()["llm"])
    payload["instances"]["free8"].update(invalid)
    response = client.put("/api/config", json={"llm": payload})
    assert response.status_code == 400, response.text
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "invalid",
    [
        {"model": "arbitrary-model"},
        {"api_key": "custom-key"},
        {"base_url": "https://example.invalid/v1"},
        {"output_dimensionality": 768},
        {"multimodal_enabled": True},
    ],
)
def test_free_embedding_api_rejects_contract_override_atomically(
    tmp_path: Any, monkeypatch: Any, invalid: dict[str, Any]
) -> None:
    from .test_api_llm_instance_routing import _client

    client, path = _client(monkeypatch, tmp_path, config_at(tmp_path))
    before = path.read_bytes()
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "embedding": {"provider": "siliconflow_free", "model": "BAAI/bge-m3", **invalid}
            }
        },
    )
    assert response.status_code == 400, response.text
    assert path.read_bytes() == before


def test_status_only_reports_selected_free_capabilities(tmp_path: Any) -> None:
    from openbiliclaw.sponsored_status import (
        configure_status_directory,
        record_sponsored_outcome,
        sponsored_status,
    )

    cfg = config_at(tmp_path)
    cfg.llm.default_chain = ["own"]
    cfg.llm.embedding = EmbeddingConfig(provider="siliconflow_free", model="BAAI/bge-m3")
    command = shlex.split(cfg.llm.sponsored.runtime_path)
    configure_status_directory(cfg.data_path)
    try:
        record_sponsored_outcome(command, capability="chat", code="AUTH_FAILED")
        assert not sponsored_status(cfg)["notices"]
        record_sponsored_outcome(command, capability="embedding", code="AUTH_FAILED")
        assert sponsored_status(cfg)["notices"][0]["capabilities"] == ["embedding"]
    finally:
        configure_status_directory(None)


async def test_rate_limit_uses_normal_instance_cooldown(tmp_path: Any, monkeypatch: Any) -> None:
    from openbiliclaw.llm.sponsored_errors import SponsoredRateLimitedError

    class Limited:
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise SponsoredRateLimitedError()

    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider", lambda **kwargs: Limited()
    )
    cfg = config_at(tmp_path)
    cfg.llm.default_chain = ["free8", "own"]
    service = service_for(cfg)
    response = await service.complete_with_core_memory(system_instruction="test", user_input="test")
    assert response.instance_id == "own"
    assert service.registry._provider_on_cooldown("free8")


async def test_cancelled_service_stream_releases_transport_and_request_context(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from openbiliclaw.llm.sponsored_adapter import _CALLER

    closed = asyncio.Event()
    callers = []

    class Endpoint:
        async def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
            callers.append(kwargs["caller"])
            try:
                yield LLMStreamChunk(delta="first")
                await asyncio.Event().wait()
            finally:
                closed.set()

    monkeypatch.setattr(
        "openbiliclaw.llm.sponsored_adapter.shared_sponsored_provider", lambda **kwargs: Endpoint()
    )
    stream = service_for(config_at(tmp_path)).stream_complete_with_core_memory(
        system_instruction="test", user_input="test", caller="agent.chat"
    )
    try:
        assert (await anext(stream)).delta == "first"
        assert _CALLER.get() == "registry.direct"
    finally:
        await stream.aclose()
    await asyncio.wait_for(closed.wait(), timeout=1)
    assert callers == ["agent.chat"]


def test_builtin_catalog_survives_omission_and_rejects_copies(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from .test_api_llm_instance_routing import _client

    client, path = _client(monkeypatch, tmp_path, config_at(tmp_path))
    value = client.get("/api/config").json()["llm"]
    assert all(value["instances"][i]["read_only"] for i in ("free8", "free4"))
    assert not value["instances"]["own"]["read_only"]
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "instances": {"own": value["instances"]["own"]},
                "default_chain": ["own", "free4", "free8"],
                "routes": {"evaluation": {"inherit": False, "chain": ["free8", "free4"]}},
            }
        },
    )
    assert response.status_code == 202, response.text
    loaded = load_config(path, consult_environment=False)
    assert loaded.llm.instances["free8"].model == DEFAULT_CHAT_MODEL
    assert loaded.llm.instances["free4"].model == ALTERNATE_CHAT_MODEL
    assert loaded.llm.default_chain == ["own", "free4", "free8"]
    assert loaded.llm.evaluation.chain == ["free8", "free4"]
    before = path.read_bytes()
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "instances": {**value["instances"], "copy": value["instances"]["free8"]},
            }
        },
    )
    assert response.status_code == 400, response.text
    assert path.read_bytes() == before


def test_old_mutable_instances_migrate_without_changing_selected_models(tmp_path: Any) -> None:
    cfg = config_at(tmp_path)
    cfg.llm.instances["free4"].name = "old editable label"
    cfg.llm.instances["duplicate4"] = replace(cfg.llm.instances["free4"], enabled=False)
    cfg.llm.default_chain = ["free4", "free8"]
    cfg.llm.evaluation = ModuleLLMConfig(inherit=False, chain=["duplicate4", "free8"])
    migrate_sponsored_provider_config(cfg.llm)
    assert cfg.llm.builtin_providers_version == 2
    assert cfg.llm.default_chain == ["free4", "free8"]
    assert cfg.llm.evaluation.chain == ["free4", "free8"]
    assert "duplicate4" not in cfg.llm.instances
    assert cfg.llm.instances["free4"] == builtin_siliconflow_instance(ALTERNATE_CHAT_MODEL)
    cfg.llm.instances.pop("free8")
    migrate_sponsored_provider_config(cfg.llm)
    free = [i for i in cfg.llm.instances.values() if i.provider_type == "siliconflow_free"]
    assert {i.model for i in free} == {DEFAULT_CHAT_MODEL, ALTERNATE_CHAT_MODEL}
    assert len(free) == 2


def test_builtin_seed_preserves_colliding_user_instance_and_routing(tmp_path: Any) -> None:
    cfg = config_at(tmp_path)
    own = cfg.llm.instances["own"]
    cfg.llm.instances = {"siliconflow-free": own}
    cfg.llm.default_chain = ["siliconflow-free"]
    cfg.llm.soul = ModuleLLMConfig()
    path = save_config(cfg, tmp_path / "config.toml")
    loaded = load_config(path, consult_environment=False)
    assert loaded.llm.instances["siliconflow-free"] == own
    assert loaded.llm.default_chain == ["siliconflow-free"]
    assert loaded.llm.instances["siliconflow-free-2"].model == DEFAULT_CHAT_MODEL
    assert loaded.llm.instances["siliconflow-free-qwen35-4b"].model == ALTERNATE_CHAT_MODEL


def test_selecting_builtin_embedding_only_uses_its_fixed_parameters(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from .test_api_llm_instance_routing import _client

    cfg = config_at(tmp_path)
    cfg.llm.embedding = EmbeddingConfig(
        provider="gemini",
        model="gemini-embedding-2",
        api_key="old-secret",
        output_dimensionality=1536,
        multimodal_enabled=True,
    )
    client, path = _client(monkeypatch, tmp_path, cfg)
    response = client.put(
        "/api/config", json={"llm": {"embedding": {"provider": "siliconflow_free"}}}
    )
    assert response.status_code == 202, response.text
    selected = load_config(path, consult_environment=False).llm.embedding
    assert selected.model == "BAAI/bge-m3"
    assert selected.output_dimensionality == 1024
    assert not selected.api_key and not selected.base_url and not selected.multimodal_enabled


def test_legacy_module_model_choice_selects_a_fixed_instance_instead_of_copying(
    tmp_path: Any, monkeypatch: Any
) -> None:
    from .test_api_llm_instance_routing import _client

    client, path = _client(monkeypatch, tmp_path, config_at(tmp_path))
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "soul": {
                    "provider": "siliconflow_free",
                    "model": ALTERNATE_CHAT_MODEL,
                }
            }
        },
    )
    assert response.status_code == 202, response.text
    saved = load_config(path, consult_environment=False)
    assert saved.llm.soul.chain == ["free4"]
    assert set(saved.llm.instances) == {"free8", "free4", "own"}
    before = path.read_bytes()
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "soul": {
                    "provider": "siliconflow_free",
                    "model": "unknown-model",
                }
            }
        },
    )
    assert response.status_code == 400, response.text
    assert path.read_bytes() == before
