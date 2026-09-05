"""Tests for LLM providers."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.llm.base import (
    LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from openbiliclaw.llm.claude_provider import ClaudeProvider
from openbiliclaw.llm.gemini_provider import GeminiProvider, gemini_sdk_available
from openbiliclaw.llm.ollama_provider import OllamaProvider
from openbiliclaw.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from openbiliclaw.llm.openrouter_provider import OpenRouterProvider
from openbiliclaw.llm.orcarouter_provider import OrcaRouterProvider


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


def test_connectivity_probe_token_budget_is_4096() -> None:
    assert LLM_CONNECTIVITY_PROBE_MAX_TOKENS == 4096


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
async def test_openai_reasoning_model_defaults_to_medium_and_channel_empty_omits_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", model="gpt-5.5")
    calls: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "deep"}])
    await provider.complete(
        [{"role": "user", "content": "channel"}],
        reasoning_effort="",
    )

    assert calls[0]["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in calls[1]


@pytest.mark.asyncio
async def test_openai_reasoning_model_preserves_explicit_documented_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", model="gpt-5.5")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete(
        [{"role": "user", "content": "deep"}],
        reasoning_effort="xhigh",
    )

    assert captured["reasoning_effort"] == "xhigh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "base_url", "provider_name", "reasoning_effort"),
    [
        ("gpt-4o", "", "openai", "medium"),
        ("gpt-5.5", "https://relay.example.com/v1", "openai_compatible", ""),
    ],
)
async def test_openai_does_not_send_effort_to_nonreasoning_or_compatible_routes(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    base_url: str,
    provider_name: str,
    reasoning_effort: str,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        model=model,
        base_url=base_url,
        provider_name=provider_name,
        reasoning_effort=reasoning_effort,
    )
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}])

    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_openai_compatible_passes_through_explicit_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        model="vendor-reasoning-model",
        base_url="https://relay.example.com/v1",
        provider_name="openai_compatible",
        reasoning_effort="high",
    )
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}])

    assert captured["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_openai_compatible_empty_reasoning_effort_stays_wire_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        model="vendor-model",
        base_url="https://relay.example.com/v1",
        provider_name="openai_compatible",
        reasoning_effort="",
    )
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}])

    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_openai_provider_lists_sorted_unique_model_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_list() -> SimpleNamespace:
        return SimpleNamespace(
            data=[
                SimpleNamespace(id="z-model"),
                SimpleNamespace(id="a-model"),
                SimpleNamespace(id="z-model"),
                SimpleNamespace(id=""),
            ]
        )

    monkeypatch.setattr(provider, "_create_model_list", fake_list)

    assert await provider.list_models() == ["a-model", "z-model"]


@pytest.mark.asyncio
async def test_openai_provider_accepts_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", model="default-model")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("override-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="override-model",
    )

    assert response.content == "override-ok"
    assert captured["model"] == "override-model"
    assert provider._model == "default-model"


@pytest.mark.asyncio
async def test_openai_provider_skips_response_format_for_lm_studio_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio loses content with both json_object and json_schema, so we skip response_format."""
    provider = OpenAIProvider(
        api_key="lm-studio",
        model="qwen3.5-9b",
        base_url="http://127.0.0.1:1234/v1",
        provider_name="openai_compatible",
    )
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response('{"ok": true}')

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert "response_format" not in captured


@pytest.mark.asyncio
async def test_openai_provider_retries_json_mode_with_schema_when_json_object_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="http://localhost:8000/v1",
        provider_name="openai_compatible",
    )
    response_formats: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        response_format = kwargs["response_format"]
        assert isinstance(response_format, dict)
        response_formats.append(response_format)
        if response_format["type"] == "json_object":
            raise LLMProviderError(
                "openai_compatible request failed: HTTP 400: "
                '"response_format.type" must be "json_schema" or "text"'
            )
        return _openai_response('{"ok": true}')

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert response.content == '{"ok": true}'
    assert [item["type"] for item in response_formats] == ["json_object", "json_schema"]


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
async def test_openai_provider_refreshes_token_once_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_calls: list[bool] = []

    async def token_provider(force_refresh: bool = False) -> str:
        token_calls.append(force_refresh)
        return "fresh-token" if force_refresh else "initial-token"

    provider = OpenAIProvider(api_key="stale-token", token_provider=token_provider)
    calls = {"count": 0}

    class UnauthorizedError(Exception):
        status_code = 401

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        if calls["count"] == 1:
            raise UnauthorizedError("unauthorized")
        assert provider._client.api_key == "fresh-token"
        return _openai_response("after-refresh")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "after-refresh"
    assert calls["count"] == 2
    assert token_calls == [False, True]


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
async def test_openai_provider_does_not_retry_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")
    calls = {"count": 0}

    class RateLimitError(Exception):
        status_code = 429

    async def fake_sleep(_: float) -> None:
        pytest.fail("rate-limited requests should not sleep for provider retries")

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise RateLimitError("too many requests")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_openai_provider_does_not_retry_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 is terminal until the user edits config.

    Retrying it three times per shard multiplied the rejected requests visible
    in the provider console — a reporter saw "my token has usage records" while
    init still failed with 401 — and stretched the perceived hang.
    """
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="https://api.sensenova.cn/compatible-mode/v1",
        provider_name="openai_compatible",
    )
    calls = {"count": 0}

    class UnauthorizedError(Exception):
        status_code = 401
        body = {"error": {"message": "invalid token", "type": "authentication_error"}}

    async def fake_sleep(_: float) -> None:
        pytest.fail("auth failures should not burn provider retries")

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise UnauthorizedError("Error code: 401")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMAuthError) as exc_info:
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1
    # The identity travels with the error so user-facing copy can name which
    # of several configured keys was rejected.
    assert exc_info.value.provider_name == "openai_compatible"
    assert exc_info.value.endpoint == "https://api.sensenova.cn/compatible-mode/v1"


@pytest.mark.asyncio
async def test_openai_provider_does_not_retry_missing_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="https://api.sensenova.cn/compatible-mode/v1",
        provider_name="openai_compatible",
    )
    calls = {"count": 0}

    class NotFoundError(Exception):
        status_code = 404
        body = {
            "error": {
                "message": "model route not found",
                "type": "not_found_error",
                "code": "5",
            }
        }

    async def fake_sleep(_: float) -> None:
        pytest.fail("a missing model route should fail fast until config changes")

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise NotFoundError("Error code: 404")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMProviderError, match="model route not found"):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_openai_provider_treats_insufficient_balance_as_provider_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key")
    calls = {"count": 0}

    class PaymentRequiredError(Exception):
        status_code = 402
        body = {
            "error": {
                "message": "Insufficient Balance",
                "type": "unknown_error",
                "code": "invalid_request_error",
            }
        }

    async def fake_sleep(_: float) -> None:
        pytest.fail("billing/quota failures should not burn provider retries")

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise PaymentRequiredError("Error code: 402 - Insufficient Balance")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.openai_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1


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
async def test_openai_provider_reports_reasoning_only_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        provider_name="openai_compatible",
    )

    async def fake_create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            model="reasoning-model",
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(
                        content="",
                        reasoning_content="reasoning tokens were returned",
                    ),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=4096,
                total_tokens=4106,
            ),
        )

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    with pytest.raises(LLMResponseError) as exc_info:
        await provider.complete([{"role": "user", "content": "hi"}])

    message = str(exc_info.value)
    assert "returned reasoning but no final content" in message
    assert "finish_reason=length" in message


@pytest.mark.asyncio
async def test_openai_compatible_retries_explicit_no_reasoning_with_disabled_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway that ignores an omitted effort gets one explicit disable retry."""
    provider = OpenAIProvider(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://relay.example.com/v1",
        provider_name="openai_compatible",
    )
    calls: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        if len(calls) < 3:
            return SimpleNamespace(
                model="deepseek-v4-flash",
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(
                            content="",
                            reasoning_content="reasoning exhausted the output budget",
                        ),
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=4096,
                    total_tokens=4106,
                ),
            )
        return _openai_response('{"keywords":["并发控制"]}')

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "return json"}],
        json_mode=True,
        reasoning_effort="",
    )

    assert response.content == '{"keywords":["并发控制"]}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert calls[2]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in calls[2]


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
async def test_claude_provider_accepts_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ClaudeProvider(api_key="test-key", model="claude-default")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            model="claude-override",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="claude-override",
    )

    assert response.content == "ok"
    assert captured["model"] == "claude-override"
    assert provider._model == "claude-default"


@pytest.mark.asyncio
async def test_claude_supported_model_defaults_to_medium_and_channel_empty_maps_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ClaudeProvider(api_key="test-key", model="claude-sonnet-4-6")
    calls: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            model="claude-sonnet-4-6",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "deep"}])
    await provider.complete(
        [{"role": "user", "content": "channel"}],
        reasoning_effort="",
    )

    assert calls[0]["output_config"] == {"effort": "medium"}
    assert calls[1]["output_config"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_claude_provider_marks_system_with_ephemeral_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.3.29+: ``system`` must reach Anthropic as a list of typed
    blocks with ``cache_control: {"type": "ephemeral"}`` so prompt cache
    fires (90% off on cached input). Plain string ``system="..."`` is
    NEVER cached by Anthropic, regardless of length.
    """
    provider = ClaudeProvider(api_key="test-key")

    captured_kwargs: dict[str, object] = {}

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            model="claude-sonnet-4-6",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    await provider.complete(
        [
            {"role": "system", "content": "static rules text"},
            {"role": "user", "content": "hi"},
        ]
    )

    system_param = captured_kwargs["system"]
    # Must be the list-of-blocks form, not a plain string
    assert isinstance(system_param, list), (
        f"system must be list for cache_control, got {type(system_param).__name__}"
    )
    assert len(system_param) == 1
    block = system_param[0]
    assert block["type"] == "text"
    assert block["text"] == "static rules text"
    # The actual cache marker
    assert block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_claude_provider_extracts_cache_read_and_creation_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Anthropic reports cache hit/write tokens, normalize them
    under ``cached_input_tokens`` and ``cache_creation_input_tokens``."""
    provider = ClaudeProvider(api_key="test-key")

    async def fake_create(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            model="claude-sonnet-4-6",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(
                input_tokens=2000,
                output_tokens=300,
                cache_read_input_tokens=1500,
                cache_creation_input_tokens=400,
            ),
        )

    monkeypatch.setattr(provider._client.messages, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.usage["cached_input_tokens"] == 1500
    assert response.usage["cache_creation_input_tokens"] == 400


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


@pytest.mark.asyncio
async def test_claude_provider_does_not_retry_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ClaudeProvider(api_key="test-key")
    calls = {"count": 0}

    async def fake_sleep(_: float) -> None:
        pytest.fail("rate-limited requests should not sleep for provider retries")

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise RuntimeError("rate limit exceeded")

    monkeypatch.setattr(provider._client.messages, "create", fake_create)
    monkeypatch.setattr("openbiliclaw.llm.claude_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1


def test_deepseek_provider_defaults() -> None:
    provider = DeepSeekProvider(api_key="test-key")
    assert provider.name == "deepseek"
    assert provider._reasoning_effort == "medium"
    assert str(provider._client.base_url).rstrip("/") == "https://api.deepseek.com"


@pytest.mark.asyncio
async def test_deepseek_provider_sends_medium_thinking_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete([{"role": "user", "content": "hi"}])

    assert captured["max_tokens"] == 16384
    assert captured["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }


def test_deepseek_provider_accepts_custom_base_url() -> None:
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://deepseek-relay.example.com/v1",
    )

    assert provider.base_url == "https://deepseek-relay.example.com/v1"
    assert str(provider._client.base_url).rstrip("/") == "https://deepseek-relay.example.com/v1"


@pytest.mark.asyncio
async def test_deepseek_health_check_disables_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key", reasoning_effort="max")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    assert await provider.health_check() is True
    assert captured["max_tokens"] == LLM_CONNECTIVITY_PROBE_MAX_TOKENS
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert provider._reasoning_effort == "max"


@pytest.mark.asyncio
async def test_deepseek_provider_accepts_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key", model="deepseek-default")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("deepseek-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="deepseek-override",
        reasoning_effort="",
    )

    assert response.content == "deepseek-ok"
    assert captured["model"] == "deepseek-override"
    assert provider._model == "deepseek-default"


@pytest.mark.asyncio
async def test_deepseek_provider_sends_disabled_thinking_for_empty_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key", reasoning_effort="max")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("deepseek-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="",
    )

    assert response.content == "deepseek-ok"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert provider._reasoning_effort == "max"


@pytest.mark.asyncio
async def test_deepseek_provider_retries_empty_response_once_without_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeepSeekProvider(api_key="test-key")
    calls = {"count": 0}

    async def fake_create(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        if calls["count"] == 1:
            return _openai_response("")
        return _openai_response("retry-ok")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "retry-ok"
    assert calls["count"] == 2


def test_openai_provider_disables_sdk_retries() -> None:
    provider = OpenAIProvider(api_key="test-key")

    assert provider._client.max_retries == 0


def test_openai_provider_logs_http_400_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = OpenAIProvider(api_key="test-key", provider_name="openai_compatible")

    class BadRequestError(Exception):
        status_code = 400
        response = SimpleNamespace(
            text='{"error":{"message":"MiMo rejected request: invalid response_format"}}'
        )

    caplog.set_level("WARNING", logger="openbiliclaw.llm.openai_provider")

    mapped = provider._map_error(BadRequestError("Error code: 400"))

    assert "MiMo rejected request" in str(mapped)
    assert "MiMo rejected request" in caplog.text


def test_ollama_provider_defaults() -> None:
    provider = OllamaProvider(model="llama3")
    assert provider.name == "ollama"


def test_ollama_provider_requires_explicit_model() -> None:
    with pytest.raises(ValueError, match="explicitly configured"):
        OllamaProvider()


@pytest.mark.asyncio
async def test_ollama_provider_accepts_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaProvider(model="llama3")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ollama-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="llama3.1",
    )

    assert response.content == "ollama-ok"
    assert captured["model"] == "llama3.1"
    assert provider._model == "llama3"


def test_ollama_provider_native_root_strips_v1_suffix() -> None:
    provider = OllamaProvider(model="qwen2.5:7b", base_url="http://localhost:11434/v1")
    assert provider._native_root() == "http://localhost:11434"
    # Trailing slash also handled
    provider2 = OllamaProvider(model="qwen2.5:7b", base_url="http://localhost:11434/v1/")
    assert provider2._native_root() == "http://localhost:11434"


@pytest.mark.asyncio
async def test_ollama_provider_embed_calls_native_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify embed() POSTs to /api/embeddings (Ollama's native route),
    sends {model, prompt}, and returns the embedding vector."""
    import httpx

    captured_url: list[str] = []
    captured_payload: list[dict[str, object]] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {"embedding": [0.1, 0.2, 0.3, 0.4]}

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, object]) -> _FakeResponse:
            captured_url.append(url)
            captured_payload.append(json)
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    provider = OllamaProvider(model="bge-m3", base_url="http://localhost:11434/v1")
    result = await provider.embed("hello world", model="bge-m3")

    assert captured_url == ["http://localhost:11434/api/embeddings"]
    assert captured_payload == [{"model": "bge-m3", "prompt": "hello world"}]
    assert result == [0.1, 0.2, 0.3, 0.4]


@pytest.mark.asyncio
async def test_ollama_provider_embed_returns_empty_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Ollama isn't reachable, embed() should return [] not raise."""
    import httpx

    class _FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FailingClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> object:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingClient)

    provider = OllamaProvider(model="bge-m3", base_url="http://localhost:11434/v1")
    result = await provider.embed("hello", model="bge-m3")
    assert result == []


class _FakeChatResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = body
        self.status_code = 200

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, object]:
        return self._body


def _install_fake_chat_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: dict[str, object],
    captured_url: list[str],
    captured_payload: list[dict[str, object]],
) -> None:
    import httpx

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, object]) -> _FakeChatResponse:
            captured_url.append(url)
            captured_payload.append(json)
            return _FakeChatResponse(body)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_ollama_provider_num_ctx_routes_chat_to_native_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With num_ctx>0, complete() must POST to native /api/chat with
    options.num_ctx so the context window actually applies (the /v1 shim
    silently ignores it), and map prompt_eval_count/eval_count to usage."""
    captured_url: list[str] = []
    captured_payload: list[dict[str, object]] = []
    _install_fake_chat_client(
        monkeypatch,
        body={
            "model": "qwen2.5:7b",
            "message": {"role": "assistant", "content": "好的"},
            "prompt_eval_count": 1234,
            "eval_count": 56,
        },
        captured_url=captured_url,
        captured_payload=captured_payload,
    )

    provider = OllamaProvider(
        model="qwen2.5:7b", base_url="http://localhost:11434/v1", num_ctx=8192
    )
    response = await provider.complete(
        [{"role": "user", "content": "hi"}], max_tokens=512, temperature=0.3
    )

    assert captured_url == ["http://localhost:11434/api/chat"]
    payload = captured_payload[0]
    assert payload["model"] == "qwen2.5:7b"
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0.3, "num_ctx": 8192, "num_predict": 512}
    assert "format" not in payload  # json_mode defaults to False
    assert response.content == "好的"
    assert response.provider == "ollama"
    assert response.usage == {
        "prompt_tokens": 1234,
        "completion_tokens": 56,
        "total_tokens": 1290,
    }


@pytest.mark.asyncio
async def test_ollama_provider_native_json_mode_sets_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """json_mode on the native path maps to Ollama's format='json'."""
    captured_payload: list[dict[str, object]] = []
    _install_fake_chat_client(
        monkeypatch,
        body={"message": {"content": "[]"}, "prompt_eval_count": 1, "eval_count": 1},
        captured_url=[],
        captured_payload=captured_payload,
    )

    provider = OllamaProvider(
        model="qwen2.5:7b", base_url="http://localhost:11434/v1", num_ctx=4096
    )
    await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert captured_payload[0]["format"] == "json"


@pytest.mark.asyncio
async def test_ollama_provider_default_num_ctx_uses_openai_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """num_ctx=0 (default) keeps the OpenAI-compat /v1 path untouched —
    the native /api/chat client must never be constructed."""
    import httpx

    class _ExplodingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("native /api/chat client built when num_ctx=0")

    monkeypatch.setattr(httpx, "AsyncClient", _ExplodingClient)

    provider = OllamaProvider(model="llama3", base_url="http://localhost:11434/v1")

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        return _openai_response("shim-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete([{"role": "user", "content": "hi"}])
    assert response.content == "shim-ok"


@pytest.mark.asyncio
async def test_ollama_provider_native_retries_without_format_on_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty content under format=json triggers one unconstrained retry —
    parity with the OpenAI-shim empty-content recovery."""
    captured_payload: list[dict[str, object]] = []

    class _TwoShotClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _TwoShotClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, object]) -> _FakeChatResponse:
            captured_payload.append(dict(json))
            if len(captured_payload) == 1:
                return _FakeChatResponse({"message": {"content": "   "}})
            return _FakeChatResponse(
                {"message": {"content": "recovered"}, "prompt_eval_count": 5, "eval_count": 2}
            )

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _TwoShotClient)

    provider = OllamaProvider(
        model="qwen2.5:7b", base_url="http://localhost:11434/v1", num_ctx=4096
    )
    response = await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert len(captured_payload) == 2
    assert captured_payload[0]["format"] == "json"  # first attempt constrained
    assert "format" not in captured_payload[1]  # retry unconstrained
    assert response.content == "recovered"


@pytest.mark.asyncio
async def test_ollama_provider_native_reports_thinking_only_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OllamaProvider(
        model="qwen2.5:7b", base_url="http://localhost:11434/v1", num_ctx=4096
    )

    async def fake_post_chat(_: dict[str, object]) -> dict[str, object]:
        return {
            "model": "qwen3",
            "done_reason": "length",
            "message": {
                "content": "",
                "thinking": "reasoning tokens were returned",
            },
        }

    monkeypatch.setattr(provider, "_post_chat", fake_post_chat)

    with pytest.raises(LLMResponseError) as exc_info:
        await provider.complete([{"role": "user", "content": "hi"}])

    message = str(exc_info.value)
    assert "returned reasoning but no final content" in message
    assert "finish_reason=length" in message


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
async def test_openrouter_provider_inherits_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenRouterProvider(api_key="test-key", model="openai/default")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("openrouter-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="anthropic/claude-sonnet-4.5",
    )

    assert response.content == "openrouter-ok"
    assert captured["model"] == "anthropic/claude-sonnet-4.5"
    assert captured["extra_body"] == {"reasoning": {"effort": "medium"}}
    assert provider._model == "openai/default"


@pytest.mark.asyncio
async def test_openrouter_channel_empty_omits_unsafe_disable_for_mandatory_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenRouterProvider(api_key="test-key", model="google/gemini-3.1-pro-preview")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="",
    )

    assert "extra_body" not in captured


def test_orcarouter_provider_defaults() -> None:
    provider = OrcaRouterProvider(api_key="test-key", model="openai/gpt-4o")

    assert provider.name == "orcarouter"
    assert provider.base_url == "https://api.orcarouter.ai/v1"
    # OrcaRouter does not use OpenRouter attribution headers.
    assert provider._extra_headers() == {}
    # OrcaRouter forwards reasoning args to the upstream route, which
    # rejects them on non-reasoning models (HTTP 400 against gpt-4o), so
    # the adapter never sends reasoning_effort or a provider-specific body.
    assert provider._extra_body(reasoning_effort="medium") == {}
    assert provider._openai_reasoning_effort("openai/gpt-4o", "high") is None


@pytest.mark.asyncio
async def test_orcarouter_provider_omits_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OrcaRouter never sends ``reasoning_effort`` or a nested ``reasoning``
    object: the gateway forwards both to the upstream model, which rejects
    them on non-reasoning routes (verified against ``openai/gpt-4o``)."""
    provider = OrcaRouterProvider(api_key="test-key", model="openai/gpt-4o")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("orcarouter-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="high",
    )

    assert response.content == "orcarouter-ok"
    assert "reasoning_effort" not in captured
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_orcarouter_channel_empty_omits_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OrcaRouterProvider(api_key="test-key", model="openai/gpt-4o")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    await provider.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="",
    )

    assert "reasoning_effort" not in captured
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_orcarouter_provider_inherits_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OrcaRouterProvider(api_key="test-key", model="openai/gpt-4o")
    captured: dict[str, object] = {}

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _openai_response("orcarouter-ok")

    monkeypatch.setattr(provider, "_request_with_retry", fake_request)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        model="anthropic/claude-opus-4.8",
    )

    assert response.content == "orcarouter-ok"
    assert captured["model"] == "anthropic/claude-opus-4.8"
    assert provider._model == "openai/gpt-4o"


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
def test_gemini_provider_defaults() -> None:
    provider = GeminiProvider(api_key="test-key")
    assert provider.name == "gemini"


def test_cli_import_survives_broken_gemini_sdk_native_deps(tmp_path: Path) -> None:
    """Issue #80: google-genai installed but raising plain ImportError at load
    time (e.g. cryptography's native wheel failing to dlopen on Termux/Android)
    must degrade the Gemini provider instead of crashing CLI startup."""
    fake_google = tmp_path / "google"
    fake_genai = fake_google / "genai"
    fake_genai.mkdir(parents=True)
    (fake_google / "__init__.py").write_text("", encoding="utf-8")
    (fake_genai / "__init__.py").write_text(
        "raise ImportError('dlopen failed: cannot locate symbol \"PyExc_Warning\"')\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import openbiliclaw.cli; "
            "from openbiliclaw.llm.gemini_provider import gemini_sdk_available; "
            "assert not gemini_sdk_available(); "
            "print('degraded-ok')",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "degraded-ok" in result.stdout


def test_gemini_missing_sdk_error_includes_import_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openbiliclaw.llm.gemini_provider as gemini_provider_mod

    monkeypatch.setattr(gemini_provider_mod, "genai", None)
    monkeypatch.setattr(gemini_provider_mod, "types", None)
    monkeypatch.setattr(
        gemini_provider_mod,
        "_SDK_IMPORT_ERROR",
        'dlopen failed: cannot locate symbol "PyExc_Warning"',
    )

    # Use the class off the just-patched module object, NOT the top-level
    # `GeminiProvider` binding: test_gemini_optional_import does a delete+reimport
    # of this module, which can leave the top-level binding pointing at a
    # different module object than sys.modules — then __init__ would read a
    # DIFFERENT module's (unpatched) genai/types and spuriously not raise when
    # the whole suite runs (green in isolation, red in CI).
    with pytest.raises(LLMProviderError, match="PyExc_Warning"):
        gemini_provider_mod.GeminiProvider(api_key="test-key")


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
async def test_gemini_provider_normalizes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GeminiProvider(api_key="test-key")
    captured: dict[str, object] = {}

    async def fake_generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            text="hello from gemini",
            model_version="gemini-2.5-flash",
            usage_metadata=SimpleNamespace(
                prompt_token_count=12,
                candidates_token_count=8,
                total_token_count=20,
            ),
        )

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    response = await provider.complete(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ],
        json_mode=True,
    )

    assert response.content == "hello from gemini"
    assert response.provider == "gemini"
    assert response.model == "gemini-2.5-flash"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
    }
    assert captured["model"] == "gemini-2.5-flash"
    assert "[SYSTEM]" in str(captured["contents"])
    assert "[USER]" in str(captured["contents"])
    config = captured["config"]
    assert config.response_mime_type == "application/json"  # type: ignore[attr-defined]
    assert config.thinking_config is not None  # type: ignore[attr-defined]
    assert config.thinking_config.thinking_budget == 2048  # type: ignore[attr-defined]
    assert config.automatic_function_calling is not None  # type: ignore[attr-defined]
    assert config.automatic_function_calling.disable is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
async def test_gemini_provider_accepts_per_call_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")
    captured: dict[str, object] = {}

    async def fake_generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"ok": true}',
            model_version="gemini-3.1-pro-preview",
            usage_metadata=None,
        )

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    response = await provider.complete(
        [{"role": "user", "content": "hi"}],
        json_mode=True,
        model="gemini-3.1-pro-preview",
    )

    assert response.content == '{"ok": true}'
    assert captured["model"] == "gemini-3.1-pro-preview"
    assert provider._model == "gemini-2.5-flash"
    config = captured["config"]
    assert config.thinking_config is not None  # type: ignore[attr-defined]
    assert config.thinking_config.thinking_level.value == "MEDIUM"  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
@pytest.mark.parametrize(
    ("model", "field_name", "expected"),
    [
        ("gemini-3.1-pro-preview", "thinking_level", "MEDIUM"),
        ("gemini-2.5-pro", "thinking_budget", 2048),
    ],
)
async def test_gemini_reasoning_model_uses_medium_without_invalid_zero_budget(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    field_name: str,
    expected: object,
) -> None:
    # Regression: Gemini 3.x and 2.5-pro reject thinking_budget=0.  Their
    # medium mapping must use thinking_level or a valid positive budget.
    provider = GeminiProvider(api_key="test-key", model=model)
    captured: dict[str, object] = {}

    async def fake_generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"ok": true}',
            model_version=model,
            usage_metadata=None,
        )

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    config = captured["config"]
    assert config.response_mime_type == "application/json"  # type: ignore[attr-defined]
    assert config.thinking_config is not None  # type: ignore[attr-defined]
    actual = getattr(config.thinking_config, field_name)  # type: ignore[attr-defined]
    if hasattr(actual, "value"):
        actual = actual.value
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
async def test_gemini_25_flash_defaults_to_medium_budget_in_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The portable medium default maps to half of the 4096 output window.
    provider = GeminiProvider(api_key="test-key", model="gemini-2.5-flash")
    captured: dict[str, object] = {}

    async def fake_generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            text='{"ok": true}',
            model_version="gemini-2.5-flash",
            usage_metadata=None,
        )

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    config = captured["config"]
    assert config.thinking_config is not None  # type: ignore[attr-defined]
    assert config.thinking_config.thinking_budget == 2048  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
@pytest.mark.parametrize(
    ("model", "field_name", "expected"),
    [
        ("gemini-2.5-flash", "thinking_budget", 0),
        ("gemini-2.5-pro", "thinking_budget", 128),
        ("gemini-3.1-pro-preview", "thinking_level", "LOW"),
        ("gemini-3-flash-preview", "thinking_level", "MINIMAL"),
    ],
)
async def test_gemini_channel_empty_uses_lowest_supported_thinking(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    field_name: str,
    expected: object,
) -> None:
    provider = GeminiProvider(api_key="test-key", model=model)
    captured: dict[str, object] = {}

    async def fake_generate_content(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(text="ok", model_version=model, usage_metadata=None)

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)

    await provider.complete(
        [{"role": "user", "content": "hi"}],
        reasoning_effort="",
    )

    config = captured["config"]
    actual = getattr(config.thinking_config, field_name)  # type: ignore[attr-defined]
    if hasattr(actual, "value"):
        actual = actual.value
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
async def test_gemini_provider_does_not_retry_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GeminiProvider(api_key="test-key")
    calls = {"count": 0}

    class RateLimitError(Exception):
        status_code = 429

    async def fake_sleep(_: float) -> None:
        pytest.fail("rate-limited requests should not sleep for provider retries")

    async def fake_generate_content(**_: object) -> SimpleNamespace:
        calls["count"] += 1
        raise RateLimitError("too many requests")

    monkeypatch.setattr(provider._client.aio.models, "generate_content", fake_generate_content)
    monkeypatch.setattr("openbiliclaw.llm.gemini_provider.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMRateLimitError):
        await provider.complete([{"role": "user", "content": "hi"}])

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_health_check_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_complete(*_: object, **__: object):  # type: ignore[no-untyped-def]
        return SimpleNamespace(content="ok")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_uses_connectivity_probe_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")
    captured: dict[str, object] = {}

    async def fake_complete(
        messages: list[dict[str, str]],
        **kwargs: object,
    ) -> SimpleNamespace:
        assert messages == [{"role": "user", "content": "hi"}]
        captured.update(kwargs)
        return SimpleNamespace(content="ok")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is True
    assert captured["max_tokens"] == LLM_CONNECTIVITY_PROBE_MAX_TOKENS
    assert captured["reasoning_effort"] == ""


@pytest.mark.asyncio
async def test_health_check_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_complete(*_: object, **__: object):  # type: ignore[no-untyped-def]
        raise LLMProviderError("down")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is False


# --- issue #72: third-party gateway adaptation ---


def test_claude_provider_accepts_custom_base_url() -> None:
    provider = ClaudeProvider(api_key="sk-test", base_url="https://relay.example.com/api")
    # The Anthropic SDK normalizes the URL with a trailing slash.
    assert str(provider._client.base_url).rstrip("/") == "https://relay.example.com/api"


def test_claude_provider_defaults_to_official_base_url() -> None:
    provider = ClaudeProvider(api_key="sk-test")
    assert "api.anthropic.com" in str(provider._client.base_url)


def _responses_response(text: str = "ok", *, with_output_text: bool = True) -> SimpleNamespace:
    response = SimpleNamespace(
        model="gpt-5-mini",
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=text)],
            )
        ],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            input_tokens_details=SimpleNamespace(cached_tokens=4),
        ),
    )
    if with_output_text:
        response.output_text = text
    return response


@pytest.mark.asyncio
async def test_openai_provider_responses_flavor_maps_params_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        model="gpt-5-mini",
        base_url="https://relay.example.com/v1",
        provider_name="openai_compatible",
        api_flavor="responses",
    )
    captured: dict[str, object] = {}

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _responses_response('{"ok": true}')

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.complete(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        max_tokens=512,
        json_mode=True,
    )

    assert captured["instructions"] == "be terse"
    assert captured["input"] == [{"role": "user", "content": "hi"}]
    assert captured["max_output_tokens"] == 512
    assert captured["text"] == {"format": {"type": "json_object"}}
    assert captured["store"] is False
    assert "messages" not in captured and "max_tokens" not in captured
    assert response.content == '{"ok": true}'
    assert response.provider == "openai_compatible"
    assert response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cached_input_tokens": 4,
    }


@pytest.mark.asyncio
async def test_official_openai_responses_flavor_sends_medium_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(
        api_key="test-key",
        model="gpt-5.5",
        api_flavor="responses",
    )
    captured: dict[str, object] = {}

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return _responses_response("ok")

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    await provider.complete([{"role": "user", "content": "hi"}])

    assert captured["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_openai_provider_responses_flavor_walks_output_without_output_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", api_flavor="responses")

    async def fake_create(**_: object) -> SimpleNamespace:
        return _responses_response("fallback-text", with_output_text=False)

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "fallback-text"


@pytest.mark.asyncio
async def test_openai_provider_responses_flavor_drops_rejected_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", model="gpt-5.4", api_flavor="responses")
    calls: list[dict[str, object]] = []

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        if "temperature" in kwargs:
            raise LLMProviderError(
                "openai request failed: HTTP 400: Unsupported parameter: 'temperature'"
            )
        return _responses_response("ok")

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "ok"
    # The rejection first exhausts the generic retry loop (mapped
    # LLMProviderError is retryable), then the flavor-level fallback
    # re-sends without temperature — so only the final call drops it.
    assert "temperature" in calls[0]
    assert "temperature" not in calls[-1]
    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_openai_provider_responses_flavor_retries_without_format_on_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key", api_flavor="responses")
    calls: list[dict[str, object]] = []

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        if "text" in kwargs:
            return _responses_response("")
        return _responses_response('{"ok": true}')

    monkeypatch.setattr(provider._client.responses, "create", fake_create)

    response = await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert response.content == '{"ok": true}'
    assert "text" in calls[0]
    assert "text" not in calls[1]


@pytest.mark.asyncio
async def test_openai_provider_default_flavor_still_uses_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_chat_create(**_: object) -> SimpleNamespace:
        return _openai_response("chat-path")

    async def fail_responses_create(**_: object) -> SimpleNamespace:
        raise AssertionError("default flavor must not call /v1/responses")

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_chat_create)
    monkeypatch.setattr(provider._client.responses, "create", fail_responses_create)

    response = await provider.complete([{"role": "user", "content": "hi"}])

    assert response.content == "chat-path"


# ── [network] routing policy → overseas SDK client wiring ───────────────────


def test_openai_provider_injects_proxy_into_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import openai_provider as mod

    sdk_kwargs: dict[str, object] = {}
    httpx_kwargs: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(mod, "AsyncOpenAI", lambda **kw: sdk_kwargs.update(kw))

    def _fake_client(**kw: object) -> object:
        httpx_kwargs.update(kw)
        return sentinel

    monkeypatch.setattr(mod.httpx, "AsyncClient", _fake_client)

    provider = OpenAIProvider(api_key="k", proxy="socks5://127.0.0.1:1080")

    assert provider._proxy == "socks5://127.0.0.1:1080"
    assert httpx_kwargs.get("proxy") == "socks5://127.0.0.1:1080"
    assert httpx_kwargs.get("trust_env") is False
    assert sdk_kwargs.get("http_client") is sentinel


def test_openai_provider_empty_proxy_is_zero_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import openai_provider as mod

    sdk_kwargs: dict[str, object] = {}
    httpx_called = False

    monkeypatch.setattr(mod, "AsyncOpenAI", lambda **kw: sdk_kwargs.update(kw))

    def _fake_client(**kw: object) -> object:
        nonlocal httpx_called
        httpx_called = True
        return object()

    monkeypatch.setattr(mod.httpx, "AsyncClient", _fake_client)

    OpenAIProvider(api_key="k", proxy="")

    assert httpx_called is False
    assert "http_client" not in sdk_kwargs


def test_openai_provider_direct_mode_disables_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import openai_provider as mod

    httpx_kwargs: dict[str, object] = {}
    monkeypatch.setattr(mod, "AsyncOpenAI", lambda **_kw: None)
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: httpx_kwargs.update(kw))

    OpenAIProvider(api_key="k", trust_env=False)

    assert httpx_kwargs["trust_env"] is False
    assert "proxy" not in httpx_kwargs


def test_claude_provider_injects_proxy_into_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import claude_provider as mod

    sdk_kwargs: dict[str, object] = {}
    httpx_kwargs: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(mod, "AsyncAnthropic", lambda **kw: sdk_kwargs.update(kw))
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kw: httpx_kwargs.update(kw) or sentinel)

    provider = ClaudeProvider(api_key="k", proxy="http://127.0.0.1:7890")

    assert provider._proxy == "http://127.0.0.1:7890"
    assert httpx_kwargs.get("proxy") == "http://127.0.0.1:7890"
    assert httpx_kwargs.get("trust_env") is False
    assert sdk_kwargs.get("http_client") is sentinel


def test_claude_provider_empty_proxy_is_zero_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import claude_provider as mod

    sdk_kwargs: dict[str, object] = {}
    monkeypatch.setattr(mod, "AsyncAnthropic", lambda **kw: sdk_kwargs.update(kw))

    ClaudeProvider(api_key="k", proxy="")

    assert "http_client" not in sdk_kwargs


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai not installed")
def test_gemini_provider_injects_proxy_into_http_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import gemini_provider as mod

    sdk_kwargs: dict[str, object] = {}
    monkeypatch.setattr(mod.genai, "Client", lambda **kw: sdk_kwargs.update(kw))

    provider = GeminiProvider(api_key="k", proxy="socks5://127.0.0.1:1080")

    http_options = sdk_kwargs.get("http_options")
    assert isinstance(http_options, dict)
    expected = {"proxy": "socks5://127.0.0.1:1080", "trust_env": False}
    assert http_options.get("client_args") == expected
    assert http_options.get("async_client_args") == expected
    assert provider._proxy == "socks5://127.0.0.1:1080"


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai not installed")
def test_gemini_provider_empty_proxy_is_zero_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import gemini_provider as mod

    sdk_kwargs: dict[str, object] = {}
    monkeypatch.setattr(mod.genai, "Client", lambda **kw: sdk_kwargs.update(kw))

    GeminiProvider(api_key="k", proxy="")

    http_options = sdk_kwargs.get("http_options")
    assert isinstance(http_options, dict)
    assert "client_args" not in http_options
    assert "async_client_args" not in http_options


@pytest.mark.asyncio
async def test_openai_chat_retries_when_temperature_must_be_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SenseNova's Kimi route requires temperature=1; retry instead of 400."""
    provider = object.__new__(OpenAIProvider)
    provider._provider_name = "openai_compatible"
    calls: list[dict[str, object]] = []

    async def fake_request(**kwargs: object) -> SimpleNamespace:
        if not calls:
            calls.append(dict(kwargs))
            raise LLMProviderError(
                'openai_compatible request failed: HTTP 400: '
                'field Temperature invalid, only 1 is allowed for this model'
            )
        calls.append(dict(kwargs))
        return _openai_response("ok")

    provider._request_with_retry = fake_request  # type: ignore[method-assign]

    response = await provider._chat_request_with_temperature_compat(
        model="kimi-k3",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=16,
    )

    assert response.choices[0].message.content == "ok"
    assert calls[0]["temperature"] == 0
    assert calls[1]["temperature"] == 1
