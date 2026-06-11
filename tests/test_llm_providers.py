"""Tests for LLM providers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.llm.base import (
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
    provider = OllamaProvider(base_url="http://localhost:11434/v1")
    assert provider._native_root() == "http://localhost:11434"
    # Trailing slash also handled
    provider2 = OllamaProvider(base_url="http://localhost:11434/v1/")
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

    provider = OllamaProvider(base_url="http://localhost:11434/v1")
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

    provider = OllamaProvider(base_url="http://localhost:11434/v1")
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

    provider = OllamaProvider(base_url="http://localhost:11434/v1", num_ctx=4096)
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

    provider = OllamaProvider(base_url="http://localhost:11434/v1", num_ctx=4096)
    response = await provider.complete([{"role": "user", "content": "hi"}], json_mode=True)

    assert len(captured_payload) == 2
    assert captured_payload[0]["format"] == "json"  # first attempt constrained
    assert "format" not in captured_payload[1]  # retry unconstrained
    assert response.content == "recovered"


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
    assert provider._model == "openai/default"


@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
def test_gemini_provider_defaults() -> None:
    provider = GeminiProvider(api_key="test-key")
    assert provider.name == "gemini"


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
    assert config.thinking_config.thinking_budget == 0  # type: ignore[attr-defined]
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
    assert config.thinking_config is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
    ],
)
async def test_gemini_reasoning_model_skips_thinking_budget_in_json_mode(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
) -> None:
    # Regression: gemini-3.x and 2.5-pro reject thinking_budget=0 with
    # 400 INVALID_ARGUMENT. json_mode must not attach the budget on them.
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
    assert config.thinking_config is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.skipif(not gemini_sdk_available(), reason="google-genai is not installed")
async def test_gemini_25_flash_still_sets_thinking_budget_in_json_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cost-saver path: 2.5-flash legitimately accepts thinking_budget=0.
    # Locks in the carve-out so the reasoning-first check doesn't widen.
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
    assert config.thinking_config.thinking_budget == 0  # type: ignore[attr-defined]


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
async def test_health_check_does_not_cap_completion_tokens(
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
    assert "max_tokens" not in captured


@pytest.mark.asyncio
async def test_health_check_returns_false_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OpenAIProvider(api_key="test-key")

    async def fake_complete(*_: object, **__: object):  # type: ignore[no-untyped-def]
        raise LLMProviderError("down")

    monkeypatch.setattr(provider, "complete", fake_complete)

    assert await provider.health_check() is False
