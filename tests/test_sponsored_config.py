"""Tests for [llm.sponsored] configuration and LLMService resolution."""

from __future__ import annotations

import shlex
import sys
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.config import (
    ConfigError,
    _build_config,
    load_config_with_diagnostics,
    validate_runtime_config,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMService
from openbiliclaw.llm.sponsored_provider import (
    SPONSORED_ENABLED_ENV,
    SPONSORED_RUNTIME_ENV,
    SponsoredProvider,
)
from openbiliclaw.sponsored_runtime_state import (
    DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS,
    SponsoredRuntimeSettings,
    current_sponsored_settings,
    install_sponsored_settings,
    reset_sponsored_settings,
)

MOCK_COMMAND = f"{sys.executable} -m openbiliclaw.llm.sponsored_mock_runtime"


class FakeRegistry:
    async def complete(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="ok", provider="openai")


class FakeMemory:
    def render_core_memory_prompt(self) -> str:
        return ""


@pytest.fixture(autouse=True)
def _reset_sponsored_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SPONSORED_ENABLED_ENV, raising=False)
    monkeypatch.delenv(SPONSORED_RUNTIME_ENV, raising=False)
    reset_sponsored_settings()
    yield
    reset_sponsored_settings()


def test_build_config_parses_sponsored_table() -> None:
    config = _build_config(
        {
            "llm": {
                "sponsored": {
                    "enabled": True,
                    "runtime_path": "/opt/obc/obc-sponsored-runtime",
                    "fallback_to_user_provider": False,
                    "notify_on_fallback": False,
                    "request_timeout_seconds": 42,
                }
            }
        }
    )
    sponsored = config.llm.sponsored
    assert sponsored.enabled is True
    assert sponsored.runtime_path == "/opt/obc/obc-sponsored-runtime"
    assert sponsored.fallback_to_user_provider is False
    assert sponsored.notify_on_fallback is False
    assert sponsored.request_timeout_seconds == 42.0


def test_build_config_normalizes_invalid_values() -> None:
    config = _build_config(
        {
            "llm": {
                "sponsored": {
                    "runtime_path": 123,
                    "request_timeout_seconds": 0,
                }
            }
        }
    )
    sponsored = config.llm.sponsored
    assert sponsored.enabled is False
    assert sponsored.runtime_path == "123"
    assert sponsored.request_timeout_seconds == DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS


def test_load_config_installs_settings_and_warns_missing_runtime(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[llm]",
                'default_provider = "deepseek"',
                "[llm.sponsored]",
                "enabled = true",
                f'runtime_path = "{tmp_path / "missing-runtime"}"',
                "request_timeout_seconds = 33",
            ]
        ),
        encoding="utf-8",
    )
    config, diagnostics = load_config_with_diagnostics(config_path)
    assert current_sponsored_settings() == config.llm.sponsored
    assert config.llm.sponsored.request_timeout_seconds == 33.0
    assert any(issue.field == "llm.sponsored.runtime_path" for issue in diagnostics.issues)


def test_sponsor_only_instance_config_accepts_no_user_keys_and_a_command_with_arguments() -> None:
    from openbiliclaw.config import _collect_config_issues

    config = _build_config(
        {
            "llm": {
                "routing_version": 2,
                "instances": {},
                "default_chain": [],
                "sponsored": {
                    "enabled": True,
                    "runtime_path": shlex.join([sys.executable, "-m", "unused.module"]),
                },
            }
        },
        consult_environment=False,
    )
    assert _collect_config_issues(config) == []

    config.llm.soul.inherit = False
    config.llm.soul.chain = ["missing"]
    assert any(issue.field == "llm.routes.soul.chain" for issue in _collect_config_issues(config))


@pytest.mark.parametrize("instance_routing", [False, True])
@pytest.mark.parametrize("embedding_mode", ["cloud", "local"])
def test_free_only_saved_config_passes_cli_runtime_validation(
    tmp_path: Path, instance_routing: bool, embedding_mode: str
) -> None:
    from openbiliclaw.config import _render_config_toml
    from openbiliclaw.llm.sponsored_embedding import select_local_embedding

    raw: dict[str, Any] = {
        "sponsored": {
            "enabled": True,
            "runtime_path": shlex.join([sys.executable, "-m", "unused.module"]),
            "fallback_to_user_provider": False,
            "embedding_mode": embedding_mode,
        },
    }
    if instance_routing:
        raw.update(routing_version=2, instances={}, default_chain=[])
    config = _build_config({"llm": raw}, consult_environment=False)
    if embedding_mode == "local":
        select_local_embedding(config)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        _render_config_toml(
            config, consult_local=False, consult_environment=False, include_api_auth=False
        ),
        encoding="utf-8",
    )
    loaded, _ = load_config_with_diagnostics(config_path, consult_environment=False)
    validate_runtime_config(loaded)


@pytest.mark.parametrize("enabled,fallback", [(False, False), (True, True)])
def test_cli_still_requires_user_key_when_user_provider_is_active(
    enabled: bool, fallback: bool
) -> None:
    config = _build_config(
        {
            "llm": {
                "sponsored": {
                    "enabled": enabled,
                    "runtime_path": shlex.join([sys.executable, "-m", "unused.module"]),
                    "fallback_to_user_provider": fallback,
                }
            }
        },
        consult_environment=False,
    )
    with pytest.raises(ConfigError, match=r"deepseek\.(api_key|model)"):
        validate_runtime_config(config)


def test_free_only_cli_keeps_non_provider_validation() -> None:
    config = _build_config(
        {
            "llm": {
                "sponsored": {
                    "enabled": True,
                    "runtime_path": shlex.join([sys.executable, "-m", "unused.module"]),
                    "fallback_to_user_provider": False,
                }
            },
        },
        consult_environment=False,
    )
    config.scheduler.eval_min_batch_size = 0
    with pytest.raises(ConfigError, match="scheduler.eval_min_batch_size"):
        validate_runtime_config(config)


@pytest.mark.parametrize("instance_routing", [False, True])
def test_sponsored_settings_survive_editing_and_serializing_other_configuration(
    instance_routing: bool,
) -> None:
    import tomllib

    from openbiliclaw.config import _render_config_toml

    raw: dict[str, Any] = {
        "sponsored": {
            "enabled": True,
            "runtime_path": shlex.join([sys.executable, "-m", "unused.module"]),
            "fallback_to_user_provider": False,
            "notify_on_fallback": False,
            "request_timeout_seconds": 75.0,
        }
    }
    if instance_routing:
        raw.update(routing_version=2, instances={}, default_chain=[])
    config = _build_config({"llm": raw}, consult_environment=False)
    config.llm.concurrency = 4
    serialized = _render_config_toml(
        config, consult_local=False, consult_environment=False, include_api_auth=False
    )
    loaded = _build_config(tomllib.loads(serialized), consult_environment=False)
    assert loaded.llm.sponsored == config.llm.sponsored
    assert loaded.llm.concurrency == 4
    assert loaded.llm.instance_routing == instance_routing


async def test_service_builds_provider_from_installed_settings() -> None:
    install_sponsored_settings(
        SponsoredRuntimeSettings(
            enabled=True,
            runtime_path=MOCK_COMMAND,
            request_timeout_seconds=15.0,
        )
    )
    service = LLMService(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
    )
    try:
        provider = service._resolve_sponsored_provider()
        assert isinstance(provider, SponsoredProvider)
        assert service._resolve_sponsored_provider() is provider
    finally:
        await service.aclose()


async def test_service_falls_back_to_env_when_config_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SPONSORED_ENABLED_ENV, "1")
    monkeypatch.setenv(SPONSORED_RUNTIME_ENV, MOCK_COMMAND)
    service = LLMService(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
    )
    try:
        assert isinstance(service._resolve_sponsored_provider(), SponsoredProvider)
    finally:
        await service.aclose()


async def test_malformed_config_command_falls_through_to_disabled() -> None:
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True, runtime_path="'"))
    service = LLMService(
        registry=FakeRegistry(),  # type: ignore[arg-type]
        memory=FakeMemory(),  # type: ignore[arg-type]
    )
    assert service._resolve_sponsored_provider() is None
