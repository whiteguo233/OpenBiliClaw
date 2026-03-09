"""Tests for LLM providers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.llm.base import (
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
)
from openbiliclaw.llm.claude_provider import ClaudeProvider
from openbiliclaw.llm.ollama_provider import OllamaProvider
from openbiliclaw.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from openbiliclaw.llm.openrouter_provider import OpenRouterProvider


def _openai_response(content: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        model="gpt-4o",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
    )


@pytest.mark.asyncio
async def test_openai_provider_normalizes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_create(**_: object) -> SimpleNamespace:
        return _openai_response("hello")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "hello"
    assert response.provider == "openai"
    assert response.model == "gpt-4o"
    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


@pytest.mark.asyncio
async def test_openai_provider_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")
    calls = {"count": 0}

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        if calls["count"] == 1:
            raise LLMProviderError("temporary")
        return _openai_response("retry-ok")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "retry-ok"
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_openai_provider_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_create(**_: object) -> SimpleNamespace:
        raise TimeoutError("slow")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMTimeoutError):
        await provider.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_openai_provider_rejects_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_create(**_: object) -> SimpleNamespace:
        return _openai_response("")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMResponseError):
        await provider.complete([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_claude_provider_normalizes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClaudeProvider(api_key="test-key")

    async def fake_create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            model="claude-sonnet",
            content=[SimpleNamespace(text="hello"), SimpleNamespace(text=" world")],
            usage=SimpleNamespace(input_tokens=12, output_tokens=8),
        )

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    response = await provider.complete(
        [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert response.content == "hello world"
    assert response.provider == "claude"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    }


@pytest.mark.asyncio
async def test_claude_provider_maps_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClaudeProvider(api_key="test-key")

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_create(**_: object) -> SimpleNamespace:
        raise RuntimeError("boom")

    monkeypatch.setattr(provider._client.messages, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.claude_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMProviderError):
        await provider.complete([{"role": "user", "content": "hi"}])


def test_deepseek_provider_defaults() -> None:
    provider = DeepSeekProvider(api_key="test-key")
    assert provider.name == "deepseek"


def test_ollama_provider_defaults() -> None:
    provider = OllamaProvider(model="llama3")
    assert provider.name == "ollama"


def test_openrouter_provider_defaults_and_headers() -> None:
    provider = OpenRouterProvider(
        api_key="test-key",
        model="openai/gpt-4o-mini",
        http_referer="https://example.com",
        x_title="OpenBiliClaw",
    )

    assert provider.name == "openrouter"
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider._extra_headers() == {
        "HTTP-Referer": "https://example.com",
        "X-Title": "OpenBiliClaw",
    }


@pytest.mark.asyncio
async def test_health_check_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_complete(*_: object, **__: object):  # type: ignore[no-untyped-def]
        return SimpleNamespace(content="ok")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_complete(*_: object, **__: object):  # type: ignore[no-untyped-def]
        raise LLMProviderError("down")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is False
