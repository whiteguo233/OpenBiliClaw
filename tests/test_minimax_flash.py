"""MiniMax presets through the real OpenAI SDK and an intercepted HTTP transport."""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.cli import _OPENAI_COMPAT_PRESETS
from openbiliclaw.config import Config
from openbiliclaw.llm.base import LLMProviderError
from openbiliclaw.llm.openai_provider import OpenAIProvider
from openbiliclaw.llm.pricing import estimate_cost
from openbiliclaw.llm.registry import (
    _maybe_openai_compatible_provider,
    _maybe_openrouter_provider,
)

FLASH = "MiniMax-M3.1-Flash"
PRESET = dict(_OPENAI_COMPAT_PRESETS)["minimax"]


@pytest.fixture
async def wire():
    requests = []
    clients = []

    def make(
        *,
        model=None,
        effort="medium",
        base_url=None,
        reply=None,
        status=200,
        gateway=False,
        api_flavor="",
    ):
        config = Config()
        settings = config.llm.openrouter if gateway else config.llm.openai_compatible
        settings.api_key = "sk-test-only"
        settings.base_url = base_url or PRESET["base_url"]
        settings.model = model or PRESET["default_model"]
        settings.reasoning_effort = effort
        settings.api_flavor = api_flavor
        factory = _maybe_openrouter_provider if gateway else _maybe_openai_compatible_provider
        provider = factory(config, {})
        assert isinstance(provider, OpenAIProvider)

        def handle(request):
            requests.append(request)
            assert request.headers["authorization"] == "Bearer sk-test-only"
            if isinstance(reply, str):
                return httpx.Response(
                    status, text=reply, headers={"content-type": "text/event-stream"}
                )
            return httpx.Response(
                status,
                json=reply
                or {
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": provider._model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                },
            )

        clients.append(provider._client)
        provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        provider._client = AsyncOpenAI(
            api_key="sk-test-only",
            base_url=provider.base_url,
            http_client=provider._http,
            max_retries=0,
        )
        clients.append(provider._client)
        return provider

    yield make, requests
    for client in clients:
        await client.close()


async def test_default_preset_reaches_sdk_http(wire):
    make, requests = wire
    provider = make()
    response = await provider.complete([{"role": "user", "content": "hello"}])
    assert response.content == "ok"
    assert response.model == FLASH
    assert str(requests[0].url) == "https://api.minimax.io/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == FLASH
    assert body["reasoning_effort"] == "medium"
    assert "thinking" not in body and "enable_thinking" not in body


async def test_explicit_responses_flavor_preserves_flash_effort_and_client_history(wire):
    make, requests = wire
    provider = make(
        api_flavor="responses",
        effort="max",
        reply={
            "id": "resp-test",
            "object": "response",
            "created_at": 0,
            "model": FLASH,
            "status": "completed",
            "output": [
                {
                    "id": "msg-test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        },
    )
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "continue"},
    ]
    assert (await provider.complete(messages)).content == "ok"
    body = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://api.minimax.io/v1/responses"
    assert body["reasoning"] == {"effort": "max"}
    assert body["input"] == messages
    assert body["store"] is False
    assert "previous_response_id" not in body and "service_tier" not in body
    with pytest.raises(LLMProviderError, match="requires adaptive reasoning"):
        await provider.complete(messages, reasoning_effort="none")
    assert len(requests) == 1


async def test_domestic_direct_endpoint_uses_same_flash_contract(wire):
    make, requests = wire
    await make(base_url="https://api.minimaxi.com/v1", effort="max").complete(
        [{"role": "user", "content": "hello"}]
    )
    assert str(requests[0].url) == "https://api.minimaxi.com/v1/chat/completions"
    assert json.loads(requests[0].content)["model"] == FLASH
    assert json.loads(requests[0].content)["reasoning_effort"] == "max"


async def test_openrouter_verified_flash_uses_gateway_reasoning_schema(wire):
    make, requests = wire
    provider = make(
        gateway=True,
        model="minimax/minimax-m3.1-flash",
        base_url="https://openrouter.ai/api/v1",
        effort="high",
    )
    await provider.complete([{"role": "user", "content": "hello"}])
    body = json.loads(requests[0].content)
    assert body["model"] == "minimax/minimax-m3.1-flash"
    assert body["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in body
    with pytest.raises(LLMProviderError, match="requires adaptive reasoning"):
        await provider.complete([{"role": "user", "content": "hello"}], reasoning_effort="none")
    assert len(requests) == 1


@pytest.mark.parametrize("effort", ["", "low", "medium", "high", "xhigh", "max"])
async def test_flash_adaptive_efforts(wire, effort):
    make, requests = wire
    await make(effort=effort).complete([{"role": "user", "content": "hello"}])
    body = json.loads(requests[0].content)
    assert body.get("reasoning_effort") == (effort or None)
    assert "thinking" not in body and "enable_thinking" not in body


@pytest.mark.parametrize("effort", ["none", "off", "disabled", "minimal", "invalid"])
async def test_flash_rejects_disabled_or_invalid_effort_before_http(wire, effort):
    make, requests = wire
    with pytest.raises(LLMProviderError, match="requires adaptive reasoning"):
        await make(effort=effort).complete([{"role": "user", "content": "hello"}])
    assert requests == []


async def test_flash_error_never_retries_with_thinking_disabled(wire):
    make, requests = wire
    provider = make(status=400, reply={"error": {"message": "invalid reasoning_effort"}})
    with pytest.raises(LLMProviderError):
        await provider.complete([{"role": "user", "content": "hello"}])
    assert len(requests) == 1
    assert "enable_thinking" not in json.loads(requests[0].content)


async def test_tool_response_reasoning_and_history_replay(wire):
    make, requests = wire
    call = {
        "id": "call-weather",
        "type": "function",
        "function": {"name": "weather", "arguments": '{"city":"Shanghai"}'},
    }
    provider = make(
        reply={
            "id": "chat-tool",
            "object": "chat.completion",
            "created": 0,
            "model": FLASH,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "Check the weather tool.",
                        "tool_calls": [call],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "Weather in Shanghai?"}]
    response = await provider.complete_with_tools(messages, tools=tools)
    assert response.tool_calls[0]["id"] == "call-weather"
    assert response.tool_calls[0]["arguments_raw"] == '{"city":"Shanghai"}'
    assert response.reasoning_content == "Check the weather tool."
    messages += [
        AgentLoop._assistant_message(
            response.content, AgentLoop._normalize_tool_calls(response), response.reasoning_content
        ),
        {"role": "tool", "tool_call_id": "call-weather", "content": "sunny"},
    ]
    await provider.complete_with_tools(messages, tools=tools)
    body = json.loads(requests[-1].content)
    assert body["tools"] == tools
    assert body["messages"] == messages
    assert body["messages"][1]["tool_calls"] == [call]
    assert body["messages"][1]["reasoning_content"] == response.reasoning_content


async def test_streamed_tool_reasoning_is_replayed_without_display(wire):
    make, requests = wire
    deltas = [
        {"role": "assistant", "reasoning_content": "Check "},
        {"reasoning_content": "the tool."},
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":'},
                }
            ]
        },
        {"tool_calls": [{"index": 0, "function": {"arguments": '"Shanghai"}'}}]},
    ]
    events = [
        {
            "id": "stream-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": FLASH,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        for delta in deltas
    ]
    stream = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    provider = make(reply=stream + "data: [DONE]\n\n")
    chunks = [
        chunk
        async for chunk in provider.stream_complete_with_tools(
            [{"role": "user", "content": "Weather?"}], tools=[]
        )
    ]
    assert all(not chunk.delta for chunk in chunks)
    response = chunks[-1].response
    assert response.reasoning_content == "Check the tool."
    message = AgentLoop._assistant_message(
        response.content, AgentLoop._normalize_tool_calls(response), response.reasoning_content
    )
    followup = make()
    await followup.complete_with_tools(
        [message, {"role": "tool", "tool_call_id": "call-1", "content": "sunny"}], tools=[]
    )
    body = json.loads(requests[-1].content)
    assert body["messages"][0]["reasoning_content"] == "Check the tool."
    assert body["messages"][0]["tool_calls"][0]["function"]["arguments"] == '{"city":"Shanghai"}'


async def test_reasoning_only_reply_never_uses_disabled_retry(wire):
    make, requests = wire
    provider = make(
        reply={
            "id": "reason-only",
            "object": "chat.completion",
            "created": 0,
            "model": FLASH,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Thinking...",
                    },
                }
            ],
        }
    )
    with pytest.raises(LLMProviderError):
        await provider.complete([{"role": "user", "content": "hello"}], reasoning_effort="")
    assert len(requests) == 1
    assert "thinking" not in json.loads(requests[0].content)


@pytest.mark.parametrize(
    "image_url",
    ["https://example.org/photo.png", "data:image/png;base64,aGVsbG8="],
)
async def test_image_parts_survive_sdk_serialization(wire, image_url):
    make, requests = wire
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    await make().complete(messages)
    assert json.loads(requests[0].content)["messages"] == messages


@pytest.mark.parametrize("model", ["MiniMax-M3", "MiniMax-M2.7", "custom-text-model"])
async def test_saved_and_custom_model_ids_remain_usable(wire, model):
    make, requests = wire
    await make(model=model).complete([{"role": "user", "content": "hello"}])
    assert json.loads(requests[0].content)["model"] == model


async def test_unrelated_compatible_endpoint_keeps_reasoning_behavior(wire):
    make, requests = wire
    await make(base_url="https://relay.example/v1", effort="none").complete(
        [{"role": "user", "content": "hello"}]
    )
    assert json.loads(requests[0].content)["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    ("base_url", "advertised", "expected"),
    [
        (
            "https://api.minimax.io/v1",
            ["MiniMax-M2.7", "MiniMax-M3", "MiniMax-Music-2.0", FLASH, "speech-02-hd"],
            [FLASH, "MiniMax-M3", "MiniMax-Music-2.0", "speech-02-hd"],
        ),
        (
            "https://api.minimaxi.com/v1",
            ["MiniMax-M3", "MiniMax-M2.5", "MiniMax-Text-01", "abab6.5s-chat"],
            ["MiniMax-M3"],
        ),
        (
            "https://openrouter.ai/api/v1",
            [
                "minimax/minimax-m2.7",
                "minimax/minimax-01",
                "minimax/minimax-m3",
                "minimax/minimax-m3.1-flash",
                "other/model",
            ],
            ["minimax/minimax-m3.1-flash", "minimax/minimax-m3", "other/model"],
        ),
        (
            "https://relay.example/v1",
            ["MiniMax-M2.7", "other/model"],
            ["MiniMax-M2.7", "other/model"],
        ),
    ],
)
async def test_discovery_filters_text_only_and_never_invents_routes(
    wire, base_url, advertised, expected
):
    make, requests = wire
    provider = make(
        base_url=base_url,
        reply={"object": "list", "data": [{"id": model} for model in advertised]},
    )
    assert await provider.list_models() == expected
    assert requests[0].url.path.endswith("/models")


@pytest.mark.parametrize("provider", ["openai", "openai_compatible"])
def test_flash_cost_uses_direct_list_and_cache_prices(provider):
    assert estimate_cost(provider, FLASH, 1_000_000, 1_000_000) == 5.4
    assert estimate_cost(provider, FLASH, 1_000_000, 0, 1_000_000) == 0.108


def test_openrouter_flash_route_reports_the_advertised_list_price():
    # The live OpenRouter catalog (2026-10-23) advertises only the paid
    # ``minimax/minimax-m3.1-flash`` route at $0.15/$0.60 per M, so the
    # generic OpenRouter fallback would overstate the real bill.
    assert estimate_cost("openrouter", "minimax/minimax-m3.1-flash", 1_000_000, 1_000_000) == 5.4
    assert (
        estimate_cost("openrouter", "minimax/minimax-m3.1-flash", 1_000_000, 0, 1_000_000) == 0.108
    )


async def test_openrouter_discovery_surfaces_whichever_flash_route_is_advertised(wire):
    # Regression: the first draft prioritized a ``:free`` route that the live
    # catalog never listed, then hid the real paid route as a "historical
    # alias" — users saw only MiniMax-M3 in the picker.
    make, _requests = wire
    advertised = [
        "minimax/minimax-m2.7",
        "minimax/minimax-m2",
        "minimax/minimax-01",
        "minimax/minimax-m3",
        "minimax/minimax-m3.1-flash",
        "other/model",
    ]
    provider = make(
        base_url="https://openrouter.ai/api/v1",
        reply={"object": "list", "data": [{"id": model} for model in advertised]},
    )
    assert await provider.list_models() == [
        "minimax/minimax-m3.1-flash",
        "minimax/minimax-m3",
        "other/model",
    ]
