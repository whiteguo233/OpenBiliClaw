"""Tests for the LLM registry and fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig
from openbiliclaw.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponse,
    LLMResponseError,
)
from openbiliclaw.llm.registry import build_llm_registry


@dataclass
class FakeProvider(LLMProvider):
    """Simple fake provider for registry tests."""

    provider_name: str
    responses: list[LLMResponse] = field(default_factory=list)
    errors: list[Exception] = field(default_factory=list)
    health: bool = True

    @property
    def name(self) -> str:
        return self.provider_name

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        if self.errors:
            raise self.errors.pop(0)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="ok", provider=self.provider_name, model="fake")

    async def health_check(self) -> bool:
        return self.health


def test_build_llm_registry_registers_available_providers() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="openai-key"),
            deepseek=LLMProviderConfig(api_key="deepseek-key"),
            ollama=LLMProviderConfig(model="llama3"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openai"
    assert registry.available_providers == ["openai", "deepseek", "ollama"]


def test_build_llm_registry_registers_openrouter() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(
                api_key="openrouter-key",
                model="openai/gpt-4o-mini",
                base_url="https://openrouter.ai/api/v1",
            ),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openrouter"
    assert "openrouter" in registry.available_providers


def test_build_llm_registry_downgrades_default_provider() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="claude",
            openai=LLMProviderConfig(api_key="openai-key"),
            ollama=LLMProviderConfig(model="llama3"),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "openai"


def test_build_llm_registry_registers_ollama_as_local_default() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            ollama=LLMProviderConfig(model="", base_url=""),
        )
    )

    registry = build_llm_registry(config)

    assert registry.default_provider == "ollama"
    assert registry.available_providers == ["ollama"]


@pytest.mark.asyncio
async def test_registry_falls_back_on_retryable_errors() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMProviderError("down")]),
            "claude": FakeProvider(
                "claude",
                responses=[LLMResponse(content="ok", provider="claude")],
            ),
        },
        fallback_order=["openai", "claude"],
    )

    response = await registry.complete([{"role": "user", "content": "hi"}])

    assert response.provider == "claude"
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_registry_does_not_fallback_on_response_error() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", errors=[LLMResponseError("bad response")]),
            "claude": FakeProvider(
                "claude",
                responses=[LLMResponse(content="ok", provider="claude")],
            ),
        },
        fallback_order=["openai", "claude"],
    )

    with pytest.raises(LLMResponseError):
        await registry.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_registry_health_check_all() -> None:
    registry = build_llm_registry(
        Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="openai-key"),
            )
        ),
        provider_overrides={
            "openai": FakeProvider("openai", health=True),
            "ollama": FakeProvider("ollama", health=False),
        },
        fallback_order=["openai", "ollama"],
    )

    results = await registry.health_check_all()

    assert results["openai"].available is True
    assert results["openai"].is_default is True
    assert results["ollama"].available is False
