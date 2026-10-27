"""End-to-end unit coverage for v2 LLM endpoint-instance routing."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.config import (
    Config,
    LLMConfig,
    LLMInstanceConfig,
    LLMProviderConfig,
    ModuleLLMConfig,
    _collect_config_issues,
    effective_llm_default_chain,
    effective_llm_instances,
    effective_llm_routes,
    llm_migration_backup_path,
    load_config,
    load_config_with_diagnostics,
    project_config_to_legacy,
    save_config,
)
from openbiliclaw.llm.base import (
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
)
from openbiliclaw.llm.registry import build_llm_registry, summarize_registry
from openbiliclaw.llm.service import (
    LLMProviderExecutionError,
    LLMService,
    module_overrides_from_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _instance(
    name: str,
    *,
    provider_type: str = "openai_compatible",
    model: str = "model-a",
    api_key: str = "sk-test",
    base_url: str = "https://gateway.example/v1",
    enabled: bool = True,
) -> LLMInstanceConfig:
    return LLMInstanceConfig(
        name=name,
        provider_type=provider_type,
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def _native_config() -> Config:
    return Config(
        llm=LLMConfig(
            instance_routing=True,
            instances={
                "gateway-primary": _instance("主网关"),
                "gateway-backup": _instance(
                    "备网关",
                    model="model-b",
                    base_url="https://backup.example/v1",
                ),
            },
            default_chain=["gateway-primary", "gateway-backup"],
            soul=ModuleLLMConfig(
                inherit=False,
                chain=["gateway-backup", "gateway-primary"],
            ),
        )
    )


def test_native_instance_config_round_trips_without_collapsing_same_type(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"

    save_config(_native_config(), path)
    reloaded, diagnostics = load_config_with_diagnostics(path)
    text = path.read_text(encoding="utf-8")

    assert reloaded.llm.instance_routing is True
    assert list(reloaded.llm.instances) == ["gateway-primary", "gateway-backup"]
    assert reloaded.llm.instances["gateway-primary"].provider_type == "openai_compatible"
    assert reloaded.llm.instances["gateway-backup"].base_url == "https://backup.example/v1"
    assert reloaded.llm.default_chain == ["gateway-primary", "gateway-backup"]
    assert reloaded.llm.soul.inherit is False
    assert reloaded.llm.soul.chain == ["gateway-backup", "gateway-primary"]
    assert reloaded.llm.default_provider == "openai_compatible"
    assert reloaded.llm.fallback_provider == "openai_compatible"
    assert not [issue for issue in diagnostics.issues if issue.severity == "blocking"]
    assert "routing_version = 2" in text
    assert '[llm.instances."gateway-primary"]' in text
    assert "[llm.openai_compatible]" not in text


def test_legacy_projection_is_lossless_and_does_not_migrate_on_read(
    tmp_path: Path,
) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            fallback_provider="deepseek",
            openai=LLMProviderConfig(
                api_key="sk-openai",
                model="gpt-main",
            ),
            deepseek=LLMProviderConfig(
                api_key="sk-deepseek",
                model="deepseek-chat",
                base_url="https://api.deepseek.com",
            ),
            discovery=ModuleLLMConfig(provider="openai", model="gpt-cheap"),
        )
    )
    path = tmp_path / "config.toml"
    save_config(config, path)

    reloaded = load_config(path)
    instances = effective_llm_instances(reloaded.llm)
    routes = effective_llm_routes(reloaded.llm)

    assert reloaded.llm.instance_routing is False
    assert effective_llm_default_chain(reloaded.llm) == ["openai", "deepseek"]
    assert instances["openai"].api_key == "sk-openai"
    assert instances["legacy-discovery"].model == "gpt-cheap"
    assert routes["discovery"].chain == ["legacy-discovery"]
    assert routes["soul"].inherit is True
    assert "routing_version" not in path.read_text(encoding="utf-8")


def test_legacy_projection_omits_unused_remote_template_blocks() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai=LLMProviderConfig(
                model="gpt-5-nano",
                auth_mode="api_key",
            ),
            claude=LLMProviderConfig(model="claude-sonnet-4-6"),
            gemini=LLMProviderConfig(model="gemini-2.5-flash"),
            openrouter=LLMProviderConfig(
                model="openai/gpt-5-nano",
                base_url="https://openrouter.ai/api/v1",
                x_title="OpenBiliClaw",
            ),
            deepseek=LLMProviderConfig(
                api_key="sk-deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
            ),
            ollama=LLMProviderConfig(
                model="qwen2.5:7b",
                base_url="http://127.0.0.1:11434/v1",
            ),
            openai_compatible=LLMProviderConfig(
                api_key="sk-relay",
                model="deepseek-v4-flash",
                base_url="https://relay.example/v1",
            ),
        )
    )

    instances = effective_llm_instances(config.llm)

    assert list(instances) == ["deepseek", "ollama", "openai_compatible"]
    assert all(instance.enabled for instance in instances.values())
    assert effective_llm_default_chain(config.llm) == ["openai_compatible"]


def test_legacy_projection_omits_credentialed_provider_with_empty_model() -> None:
    """A legacy provider with an api_key but no model is a template block.

    Projecting it as an enabled v2 instance made otherwise valid legacy configs
    impossible to save (blocking ``model`` error). The default provider remains
    authoritative; the credentialed-but-empty provider is skipped with a hint.
    """
    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai=LLMProviderConfig(api_key="sk-openai", model=""),
            claude=LLMProviderConfig(api_key="sk-claude", model=""),
            gemini=LLMProviderConfig(model=""),
            deepseek=LLMProviderConfig(
                api_key="sk-deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
            ),
            openai_compatible=LLMProviderConfig(
                api_key="sk-relay",
                model="deepseek-v4-flash",
                base_url="https://relay.example/v1",
            ),
        )
    )

    instances = effective_llm_instances(config.llm)
    blocking = {
        issue.field: issue.message
        for issue in _collect_config_issues(config)
        if issue.severity == "blocking"
    }

    assert list(instances) == ["deepseek", "openai_compatible"]
    assert all(instance.model for instance in instances.values())
    assert "llm.instances.openai.model" not in blocking
    assert "llm.instances.claude.model" not in blocking
    assert "llm.instances.gemini.model" not in blocking


def test_legacy_projection_keeps_referenced_empty_model_provider_for_validation() -> None:
    """A referenced provider with empty model must still surface a blocking issue."""
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-openai", model=""),
        )
    )

    instances = effective_llm_instances(config.llm)

    assert list(instances) == ["openai"]
    assert instances["openai"].model == ""

    # The projected instance still lands in native v2 validation, so a
    # desktop/API save that routes through instance_routing reports the
    # missing model instead of silently dropping the provider.
    native = Config(
        llm=LLMConfig(
            instance_routing=True,
            instances=instances,
            default_chain=list(instances),
        )
    )
    blocking = {
        issue.field: issue.message
        for issue in _collect_config_issues(native)
        if issue.severity == "blocking"
    }
    assert "llm.instances.openai.model" in blocking
    assert blocking["llm.instances.openai.model"] == "启用的 LLM 实例必须明确填写模型。"


def test_native_route_validation_reports_all_reference_failures() -> None:
    config = Config(
        llm=LLMConfig(
            instance_routing=True,
            instances={
                "active": _instance("可用"),
                "disabled": _instance("停用", enabled=False),
            },
            default_chain=["active", "active", "missing", "disabled"],
            discovery=ModuleLLMConfig(inherit=False, chain=[]),
        )
    )

    blocking = [
        (issue.field, issue.message)
        for issue in _collect_config_issues(config)
        if issue.severity == "blocking"
    ]

    assert any(field == "llm.default_chain" and "重复" in message for field, message in blocking)
    assert any(field == "llm.default_chain" and "不存在" in message for field, message in blocking)
    assert any(field == "llm.default_chain" and "停用" in message for field, message in blocking)
    assert any(
        field == "llm.routes.discovery.chain" and "至少需要一个" in message
        for field, message in blocking
    )


def test_invalid_routing_version_is_safe_to_load_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
routing_version = "not-a-number"
default_provider = "openai"

[llm.openai]
api_key = "sk-test"
model = "gpt-test"
""".strip(),
        encoding="utf-8",
    )

    loaded = load_config(path)

    assert loaded.llm.instance_routing is False
    assert loaded.llm.default_provider == "openai"


def test_first_legacy_to_native_save_keeps_exact_permanent_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    legacy_bytes = (
        b"# hand-written legacy config\n"
        b"[llm]\n"
        b'default_provider = "openai"\n\n'
        b"[llm.openai]\n"
        b'api_key = "sk-before-migration"\n'
        b'model = "gpt-old"\n'
    )
    path.write_bytes(legacy_bytes)
    path.chmod(0o640)

    save_config(_native_config(), path)

    backup = llm_migration_backup_path(path)
    assert backup.read_bytes() == legacy_bytes
    assert backup.stat().st_mode & 0o777 == 0o640
    assert load_config(path).llm.instance_routing is True

    # Even if an operator temporarily writes a legacy config and migrates again,
    # the original recovery point remains immutable.
    original_backup = backup.read_bytes()
    save_config(
        Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(api_key="sk-new", model="deepseek-chat"),
            )
        ),
        path,
    )
    save_config(_native_config(), path)
    assert backup.read_bytes() == original_backup


def test_migration_backup_is_not_created_for_fresh_native_or_legacy_save(
    tmp_path: Path,
) -> None:
    native_path = tmp_path / "fresh-native.toml"
    save_config(_native_config(), native_path)
    assert not llm_migration_backup_path(native_path).exists()

    legacy_path = tmp_path / "legacy.toml"
    legacy_path.write_text('[llm]\ndefault_provider = "ollama"\n', encoding="utf-8")
    save_config(Config(llm=LLMConfig(default_provider="ollama")), legacy_path)
    assert not llm_migration_backup_path(legacy_path).exists()


def test_native_config_projects_to_previous_schema_with_explicit_loss_report(
    tmp_path: Path,
) -> None:
    config = _native_config()
    config.llm.instances["deepseek-fallback"] = _instance(
        "跨类型备选",
        provider_type="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
    )
    config.llm.default_chain = [
        "gateway-primary",
        "gateway-backup",
        "deepseek-fallback",
    ]
    config.llm.discovery = ModuleLLMConfig(
        inherit=False,
        chain=["deepseek-fallback", "gateway-primary"],
    )
    config.llm.embedding.provider = "openai"
    config.llm.embedding.model = "text-embedding-3-small"
    config.llm.embedding.api_key = "sk-embedding"
    config.llm.embedding.base_url = "https://embedding.example/v1"

    legacy, report = project_config_to_legacy(config)

    assert legacy.llm.instance_routing is False
    assert legacy.llm.instances == {}
    assert legacy.llm.default_chain == []
    assert legacy.llm.default_provider == "openai_compatible"
    assert legacy.llm.fallback_provider == "deepseek"
    assert legacy.llm.openai_compatible.base_url == "https://gateway.example/v1"
    assert legacy.llm.soul.provider == "openai_compatible"
    assert legacy.llm.soul.model == "model-b"
    assert legacy.llm.discovery.provider == "deepseek"
    assert legacy.llm.embedding == config.llm.embedding
    assert report.primary_instance_id == "gateway-primary"
    assert report.fallback_instance_id == "deepseek-fallback"
    assert {issue.code for issue in report.issues} >= {
        "provider_instances_collapsed",
        "default_chain_truncated",
        "module_chain_truncated",
        "module_endpoint_rebound",
    }

    export_path = tmp_path / "config.legacy.toml"
    save_config(legacy, export_path)
    with export_path.open("rb") as handle:
        previous_raw = tomllib.load(handle)

    # Frozen contract of the parser from the version immediately before v2:
    # it reads fixed provider/module tables and knows none of the v2 keys.
    previous_llm = previous_raw["llm"]
    assert "routing_version" not in previous_llm
    assert "instances" not in previous_llm
    assert "default_chain" not in previous_llm
    assert "routes" not in previous_llm
    assert previous_llm["default_provider"] == "openai_compatible"
    assert previous_llm["fallback_provider"] == "deepseek"
    assert previous_llm["openai_compatible"]["api_key"] == "sk-test"
    assert previous_llm["openai_compatible"]["base_url"] == "https://gateway.example/v1"
    assert previous_llm["soul"] == {
        "provider": "openai_compatible",
        "model": "model-b",
    }
    assert previous_llm["embedding"]["api_key"] == "sk-embedding"
    assert load_config(export_path).llm.instance_routing is False


def test_legacy_projection_is_noop_and_native_export_requires_a_usable_chain() -> None:
    legacy = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="qwen2.5:7b"),
        )
    )

    exported, report = project_config_to_legacy(legacy)

    assert exported == legacy
    assert exported is not legacy
    assert report.source_was_native is False
    assert report.lossy is False

    with pytest.raises(ValueError, match="没有可用实例"):
        project_config_to_legacy(
            Config(
                llm=LLMConfig(
                    instance_routing=True,
                    instances={"disabled": _instance("停用", enabled=False)},
                    default_chain=["disabled"],
                )
            )
        )


@dataclass
class _FakeProvider(LLMProvider):
    provider_name: str = "openai_compatible"
    errors: list[Exception] = field(default_factory=list)
    response_text: str = "ok"
    call_count: int = 0

    @property
    def name(self) -> str:
        return self.provider_name

    async def complete(
        self,
        messages: list[dict[str, str]],  # noqa: ARG002
        *,
        temperature: float = 0.7,  # noqa: ARG002
        max_tokens: int = 4096,  # noqa: ARG002
        json_mode: bool = False,  # noqa: ARG002
        reasoning_effort: str | None = None,  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
    ) -> LLMResponse:
        self.call_count += 1
        if self.errors:
            raise self.errors.pop(0)
        return LLMResponse(
            content=self.response_text,
            provider=self.provider_name,
            model="fake-model",
        )


@pytest.mark.asyncio
async def test_same_provider_type_instances_fallback_in_configured_order() -> None:
    first = _FakeProvider(errors=[LLMProviderError("primary down")])
    second = _FakeProvider(response_text="from backup")
    config = _native_config()

    registry = build_llm_registry(
        config,
        provider_overrides={
            "gateway-primary": first,
            "gateway-backup": second,
        },
    )
    response = await registry.complete([{"role": "user", "content": "hello"}])

    assert registry.available_providers == ["gateway-primary", "gateway-backup"]
    assert registry.default_provider == "gateway-primary"
    assert registry.provider_type("gateway-backup") == "openai_compatible"
    assert first.call_count == 1
    assert second.call_count == 1
    assert response.content == "from backup"
    assert response.provider == "openai_compatible"
    assert response.instance_id == "gateway-backup"


def test_registry_summary_reports_instance_id_as_native_configured_default() -> None:
    registry = build_llm_registry(
        _native_config(),
        provider_overrides={
            "gateway-primary": _FakeProvider(),
            "gateway-backup": _FakeProvider(),
        },
    )

    summary = summarize_registry(_native_config(), registry)

    assert summary.configured_default == "gateway-primary"
    assert summary.effective_default == "gateway-primary"
    assert summary.registered_providers == ["gateway-primary", "gateway-backup"]


@pytest.mark.asyncio
async def test_rate_limit_cooldown_is_scoped_to_instance_not_adapter_type() -> None:
    first = _FakeProvider(errors=[LLMRateLimitError("429")])
    second = _FakeProvider(response_text="healthy sibling")
    registry = build_llm_registry(
        _native_config(),
        provider_overrides={
            "gateway-primary": first,
            "gateway-backup": second,
        },
    )

    first_response = await registry.complete([{"role": "user", "content": "one"}])
    second_response = await registry.complete([{"role": "user", "content": "two"}])

    assert first.call_count == 1
    assert second.call_count == 2
    assert first_response.instance_id == "gateway-backup"
    assert second_response.instance_id == "gateway-backup"


class _RoutingRegistry:
    default_provider = "gateway-primary"

    def __init__(self) -> None:
        self.global_calls = 0
        self.chain_calls: list[list[str]] = []

    async def complete(self, *args: object, **kwargs: object) -> LLMResponse:  # noqa: ARG002
        self.global_calls += 1
        return LLMResponse(content="global", provider="openai_compatible")

    async def complete_chain(
        self,
        instance_ids: list[str] | tuple[str, ...],
        *args: object,
        **kwargs: object,  # noqa: ARG002
    ) -> LLMResponse:
        chain = list(instance_ids)
        self.chain_calls.append(chain)
        if not chain:
            raise LLMFallbackError("No provider was available to process the request.")
        return LLMResponse(
            content="custom",
            provider="openai_compatible",
            instance_id=chain[0],
        )

    async def complete_provider(self, *args: object, **kwargs: object) -> LLMResponse:  # noqa: ARG002
        raise AssertionError("legacy exact-provider routing should not be used")

    def is_chat_capable(self, name: str) -> bool:
        return name in {"gateway-primary", "gateway-backup"}

    def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
        return "openai_compatible"


@pytest.mark.asyncio
async def test_module_custom_chain_and_global_inheritance_are_distinct() -> None:
    config = _native_config()
    registry = _RoutingRegistry()
    service = LLMService(
        registry=registry,
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(config),
    )

    discovery = await service.complete_with_core_memory(
        system_instruction="system",
        user_input="discover",
        caller="soul.preference",
    )
    inherited = await service.complete_with_core_memory(
        system_instruction="system",
        user_input="recommend",
        caller="recommendation.write_expression",
    )

    assert discovery.content == "custom"
    assert discovery.instance_id == "gateway-backup"
    assert registry.chain_calls == [["gateway-backup", "gateway-primary"]]
    assert inherited.content == "global"
    assert registry.global_calls == 1


@pytest.mark.asyncio
async def test_broken_explicit_module_chain_never_spills_to_global() -> None:
    config = _native_config()
    config.llm.soul.chain = ["not-registered"]
    registry = _RoutingRegistry()
    service = LLMService(
        registry=registry,
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(config),
    )

    with pytest.raises(LLMProviderExecutionError, match="No provider was available"):
        await service.complete_with_core_memory(
            system_instruction="system",
            user_input="profile",
            caller="soul.preference",
        )

    assert registry.chain_calls == [[]]
    assert registry.global_calls == 0
    assert service.supports_image_input("soul.preference") is False


@pytest.mark.asyncio
async def test_broken_module_route_warns_loudly_with_remediation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale explicit route must be visible at WARNING level, not INFO.

    Issue #213: a soul route referencing deleted/disabled instances parked
    durable chat turns on ``no_provider`` forever while the only trace was an
    INFO line operators never saw. The WARNING must name the bucket, the
    unavailable instance, and the remediation.
    """
    config = _native_config()
    config.llm.soul.chain = ["not-registered"]
    registry = _RoutingRegistry()
    service = LLMService(
        registry=registry,
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(config),
    )

    with (
        caplog.at_level("WARNING", logger="openbiliclaw.llm.service"),
        pytest.raises(LLMProviderExecutionError, match="No provider was available"),
    ):
        await service.complete_with_core_memory(
            system_instruction="system",
            user_input="profile",
            caller="soul.preference",
        )

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "LLM module route contains unavailable instance" in record.getMessage()
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "bucket=soul" in message
    assert "instance=not-registered" in message
    assert "keep failing until" in message


def test_supports_image_input_recognizes_orcarouter_vision_route() -> None:
    """OrcaRouter routes OpenAI-protocol models, so the vision-capable
    heuristic must cover it just like openai/openrouter/openai_compatible."""

    class OrcaRegistry:
        default_provider = "orca-main"

        def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
            return "orcarouter"

        def get(self, name: str) -> object:  # noqa: ARG002
            return _ModelStub("openai/gpt-4o")

    service = LLMService(
        registry=OrcaRegistry(),  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(_native_config()),
    )

    assert service.supports_image_input("discovery.evaluate_batch") is True


def test_supports_image_input_recognizes_requesty_vision_route() -> None:
    class RequestyRegistry:
        default_provider = "requesty-main"

        def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
            return "requesty"

        def get(self, name: str) -> object:  # noqa: ARG002
            return _ModelStub("openai/gpt-4o-mini")

    service = LLMService(
        registry=RequestyRegistry(),  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(_native_config()),
    )

    assert service.supports_image_input("discovery.evaluate_batch") is True


def test_supports_image_input_recognizes_api_route_vision_route() -> None:
    class ApiRouteRegistry:
        default_provider = "api-route-main"

        def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
            return "api_route"

        def get(self, name: str) -> object:  # noqa: ARG002
            return _ModelStub("openai/gpt-4o-mini")

    service = LLMService(
        registry=ApiRouteRegistry(),  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(_native_config()),
    )

    assert service.supports_image_input("discovery.evaluate_batch") is True


def test_supports_image_input_recognizes_cheaperinference_vision_route() -> None:
    class CheaperInferenceRegistry:
        default_provider = "cheaperinference-main"

        def provider_type(self, name: str | None = None) -> str:  # noqa: ARG002
            return "cheaperinference"

        def get(self, name: str) -> object:  # noqa: ARG002
            return _ModelStub("gpt-5.4-mini")

    service = LLMService(
        registry=CheaperInferenceRegistry(),  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        module_overrides=module_overrides_from_config(_native_config()),
    )

    assert service.supports_image_input("discovery.evaluate_batch") is True


class _ModelStub:
    def __init__(self, model: str) -> None:
        self._model = model
