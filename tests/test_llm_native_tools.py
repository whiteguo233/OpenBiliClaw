"""Tests for native function calling (M1): provider, registry chain, service routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMRegistry,
    LLMResponse,
    LLMResponseError,
    LLMToolCallUnsupportedError,
    is_reasoning_budget_exhausted,
)
from openbiliclaw.llm.ollama_provider import OllamaProvider
from openbiliclaw.llm.openai_provider import DeepSeekProvider, OpenAIProvider
from openbiliclaw.llm.service import LLMService

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_sources",
            "description": "列出订阅",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]

MESSAGES = [{"role": "user", "content": "hi"}]


def _tool_call_message(
    calls: list[dict[str, str]],
    content: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        model="gpt-4o",
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=content,
                    tool_calls=[
                        SimpleNamespace(
                            id=call["id"],
                            type="function",
                            function=SimpleNamespace(
                                name=call["name"],
                                arguments=call["arguments"],
                            ),
                        )
                        for call in calls
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _length_exhausted_message() -> SimpleNamespace:
    """Reasoning-first model burned the whole output budget on thinking."""
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
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=512, total_tokens=522),
    )


class TestOpenAINativeToolCalling:
    async def test_request_construction_and_multi_tool_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OpenAIProvider(api_key="test-key")
        captured: dict[str, Any] = {}

        async def fake_request(**kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return _tool_call_message(
                [
                    {"id": "call_1", "name": "list_sources", "arguments": "{}"},
                    {
                        "id": "call_2",
                        "name": "toggle_source",
                        "arguments": '{"id": "r1", "enabled": false}',
                    },
                ],
                content="我来看看",
            )

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)

        response = await provider.complete_with_tools(MESSAGES, TOOLS)

        assert captured["tools"] == TOOLS
        assert captured["tool_choice"] == "auto"
        assert captured["messages"] == MESSAGES
        assert response.content == "我来看看"
        assert response.tool_calls == [
            {"id": "call_1", "name": "list_sources", "arguments": {}, "arguments_raw": "{}"},
            {
                "id": "call_2",
                "name": "toggle_source",
                "arguments": {"id": "r1", "enabled": False},
                "arguments_raw": '{"id": "r1", "enabled": false}',
            },
        ]
        assert response.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    async def test_empty_content_with_tool_calls_is_valid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OpenAIProvider(api_key="test-key")

        async def fake_request(**_: object) -> SimpleNamespace:
            return _tool_call_message([{"id": "call_1", "name": "list_sources", "arguments": "{}"}])

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)
        response = await provider.complete_with_tools(MESSAGES, TOOLS)
        assert response.content == ""
        assert response.tool_calls is not None

    async def test_malformed_arguments_keep_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = OpenAIProvider(api_key="test-key")

        async def fake_request(**_: object) -> SimpleNamespace:
            return _tool_call_message(
                [{"id": "call_1", "name": "list_sources", "arguments": "{not json"}]
            )

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)
        response = await provider.complete_with_tools(MESSAGES, TOOLS)
        assert response.tool_calls is not None
        assert response.tool_calls[0]["arguments"] == {}
        assert response.tool_calls[0]["arguments_raw"] == "{not json"

    async def test_empty_content_without_tool_calls_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OpenAIProvider(api_key="test-key")

        async def fake_request(**_: object) -> SimpleNamespace:
            return _tool_call_message([])

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)
        with pytest.raises(LLMResponseError):
            await provider.complete_with_tools(MESSAGES, TOOLS)

    async def test_no_tools_omits_tools_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = OpenAIProvider(api_key="test-key")
        captured: dict[str, Any] = {}

        async def fake_request(**kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return _tool_call_message([], content="plain")

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)
        response = await provider.complete_with_tools(MESSAGES, [])
        assert "tools" not in captured
        assert "tool_choice" not in captured
        assert response.content == "plain"
        assert response.tool_calls is None

    def test_supports_tool_calling_flags(self) -> None:
        assert OpenAIProvider(api_key="k").supports_tool_calling is True
        assert OpenAIProvider(api_key="k", api_flavor="responses").supports_tool_calling is False
        assert OllamaProvider(model="qwen3").supports_tool_calling is False

    async def test_responses_flavor_rejects_native_tools(self) -> None:
        provider = OpenAIProvider(api_key="k", api_flavor="responses")
        with pytest.raises(LLMToolCallUnsupportedError):
            await provider.complete_with_tools(MESSAGES, TOOLS)

    async def test_deepseek_tool_call_applies_thinking_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = DeepSeekProvider(api_key="test-key", reasoning_effort="max")
        captured: dict[str, Any] = {}

        async def fake_request(**kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return _tool_call_message([{"id": "call_1", "name": "list_sources", "arguments": "{}"}])

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)
        await provider.complete_with_tools(MESSAGES, TOOLS, max_tokens=4096)
        assert captured["max_tokens"] == 32768
        # DeepSeek thinking body fields ride along like the plain path.
        assert captured["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        }

    async def test_length_exhausted_retries_with_larger_budget_and_keeps_tools(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reasoning model that burned the whole budget on thinking (empty
        content, no tool_calls, finish_reason=length) gets one doubled-budget
        retry with the tools payload intact."""
        provider = OpenAIProvider(api_key="test-key", provider_name="openai_compatible")
        calls: list[dict[str, Any]] = []

        async def fake_request(**kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                return _length_exhausted_message()
            return _tool_call_message([{"id": "call_1", "name": "list_sources", "arguments": "{}"}])

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)

        response = await provider.complete_with_tools(MESSAGES, TOOLS, max_tokens=4096)

        assert response.tool_calls == [
            {"id": "call_1", "name": "list_sources", "arguments": {}, "arguments_raw": "{}"}
        ]
        assert len(calls) == 2
        assert calls[0]["max_tokens"] == 4096
        assert calls[1]["max_tokens"] == 8192
        assert calls[1]["tools"] == TOOLS
        assert calls[1]["tool_choice"] == "auto"

    async def test_length_exhausted_retry_exhausted_raises_reasoning_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the doubled budget still comes back length-truncated and empty,
        the original reasoning-budget error (and agent-loop failure
        semantics) is unchanged."""
        provider = OpenAIProvider(api_key="test-key", provider_name="openai_compatible")
        calls: list[dict[str, Any]] = []

        async def fake_request(**kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            return _length_exhausted_message()

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)

        with pytest.raises(LLMResponseError) as exc_info:
            await provider.complete_with_tools(MESSAGES, TOOLS, max_tokens=4096)

        message = str(exc_info.value)
        assert "returned reasoning but no final content" in message
        assert "finish_reason=length" in message
        assert is_reasoning_budget_exhausted(exc_info.value)
        assert len(calls) == 2
        assert calls[-1]["max_tokens"] == 8192

    async def test_tool_calls_response_skips_length_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The normal tool-calling path never enters the budget retry."""
        provider = OpenAIProvider(api_key="test-key")
        calls = {"count": 0}

        async def fake_request(**_: object) -> SimpleNamespace:
            calls["count"] += 1
            return _tool_call_message([{"id": "call_1", "name": "list_sources", "arguments": "{}"}])

        monkeypatch.setattr(provider, "_request_with_retry", fake_request)

        response = await provider.complete_with_tools(MESSAGES, TOOLS)

        assert response.tool_calls is not None
        assert calls["count"] == 1


class _FakeProvider(LLMProvider):
    """Minimal provider double with queueable responses."""

    def __init__(
        self,
        name: str,
        responses: list[LLMResponse | Exception],
        *,
        supports_tools: bool = False,
    ) -> None:
        self._name = name
        self._responses = list(responses)
        self.supports_tool_calling = supports_tools
        self.tool_requests: list[dict[str, Any]] = []
        self.plain_requests: list[list[dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._name

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.plain_requests.append([dict(m) for m in messages])
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        self.tool_requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestRegistryNativeToolChain:
    def _registry(self, *providers: _FakeProvider, fallback: str = "") -> LLMRegistry:
        registry = LLMRegistry()
        for provider in providers:
            registry.register(provider)
        registry.fallback_provider = fallback
        return registry

    async def test_skips_providers_without_native_fc(self) -> None:
        plain = _FakeProvider("plain", [], supports_tools=False)
        native = _FakeProvider(
            "native",
            [LLMResponse(content="", tool_calls=[{"id": "c1", "name": "t", "arguments": {}}])],
            supports_tools=True,
        )
        registry = self._registry(plain, native, fallback="native")

        response = await registry.complete_with_tools(MESSAGES, TOOLS)

        assert response.instance_id == "native"
        assert response.tool_calls == [{"id": "c1", "name": "t", "arguments": {}}]
        assert plain.tool_requests == []
        assert len(native.tool_requests) == 1

    async def test_raises_unsupported_when_no_provider_has_fc(self) -> None:
        registry = self._registry(_FakeProvider("plain", [], supports_tools=False))
        with pytest.raises(LLMToolCallUnsupportedError):
            await registry.complete_with_tools(MESSAGES, TOOLS)

    async def test_falls_back_to_next_fc_provider_on_error(self) -> None:
        broken = _FakeProvider("broken", [LLMProviderError("boom")], supports_tools=True)
        healthy = _FakeProvider("healthy", [LLMResponse(content="ok")], supports_tools=True)
        registry = self._registry(broken, healthy, fallback="healthy")

        response = await registry.complete_with_tools(MESSAGES, TOOLS)
        assert response.instance_id == "healthy"
        assert response.content == "ok"

    async def test_complete_provider_with_tools_requires_support(self) -> None:
        plain = _FakeProvider("plain", [], supports_tools=False)
        registry = self._registry(plain)
        with pytest.raises(LLMToolCallUnsupportedError):
            await registry.complete_provider_with_tools("plain", MESSAGES, TOOLS)

    async def test_provider_supports_tool_calling_lookup(self) -> None:
        registry = self._registry(
            _FakeProvider("plain", []),
            _FakeProvider("native", [], supports_tools=True),
        )
        assert registry.provider_supports_tool_calling("native") is True
        assert registry.provider_supports_tool_calling("plain") is False
        assert registry.provider_supports_tool_calling("missing") is False


class TestServiceNativeTools:
    def _service(self, registry: LLMRegistry) -> LLMService:
        return LLMService(registry=registry, memory=object())  # type: ignore[arg-type]

    async def test_native_path_passes_tools_through(self) -> None:
        provider = _FakeProvider(
            "native",
            [
                LLMResponse(
                    content="",
                    tool_calls=[{"id": "c1", "name": "list_sources", "arguments": {}}],
                )
            ],
            supports_tools=True,
        )
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        response = await service.complete_with_native_tools(
            messages=[{"role": "system", "content": "sys"}, *MESSAGES],
            tools=TOOLS,
            caller="agent.loop",
        )

        assert response.tool_calls == [{"id": "c1", "name": "list_sources", "arguments": {}}]
        assert provider.tool_requests[0]["tools"] == TOOLS
        assert provider.plain_requests == []

    async def test_simulation_fallback_parses_tool_call(self) -> None:
        provider = _FakeProvider(
            "plain",
            [LLMResponse(content='{"tool_call": {"name": "list_sources", "arguments": {}}}')],
        )
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        response = await service.complete_with_native_tools(
            messages=[{"role": "system", "content": "sys"}, *MESSAGES],
            tools=TOOLS,
            caller="agent.loop",
        )

        assert response.content == ""
        assert response.tool_calls is not None
        assert response.tool_calls[0]["name"] == "list_sources"
        assert response.tool_calls[0]["id"].startswith("sim-")
        # The prompt-level fallback rendered the tool list into the system prompt.
        system_message = provider.plain_requests[0][0]
        assert "<available_tools>" in str(system_message["content"])
        assert "list_sources" in str(system_message["content"])

    async def test_simulation_parses_multiple_tool_calls(self) -> None:
        provider = _FakeProvider(
            "plain",
            [
                LLMResponse(
                    content=(
                        '{"tool_calls": ['
                        '{"name": "list_sources", "arguments": {}}, '
                        '{"name": "unknown_tool", "arguments": {}}, '
                        '{"name": "list_sources", "arguments": {"x": 1}}'
                        "]}"
                    )
                )
            ],
        )
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        response = await service.complete_with_native_tools(
            messages=MESSAGES, tools=TOOLS, caller="agent.loop"
        )

        assert response.tool_calls is not None
        # Unknown tool names are dropped during parsing.
        assert [call["name"] for call in response.tool_calls] == ["list_sources", "list_sources"]

    async def test_simulation_plain_text_stays_text(self) -> None:
        provider = _FakeProvider("plain", [LLMResponse(content="正常回复")])
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        response = await service.complete_with_native_tools(
            messages=MESSAGES, tools=TOOLS, caller="agent.loop"
        )
        assert response.content == "正常回复"
        assert response.tool_calls is None

    async def test_simulation_flattens_tool_history(self) -> None:
        provider = _FakeProvider("plain", [LLMResponse(content="继续答复")])
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "查一下"},
            {
                "role": "assistant",
                "content": "好的",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "list_sources", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "当前没有订阅"},
        ]
        response = await service.complete_with_native_tools(
            messages=messages, tools=TOOLS, caller="agent.loop"
        )

        assert response.content == "继续答复"
        sent = provider.plain_requests[0]
        assert sent[0]["role"] == "system"
        history_text = "\n".join(str(m["content"]) for m in sent[1:-1])
        assert "（调用了工具 list_sources({})）" in history_text
        # The trailing tool result becomes the user input of the flattened call.
        assert str(sent[-1]["content"]).startswith("[工具执行结果] 当前没有订阅")

    async def test_empty_tools_simulation_is_plain_completion(self) -> None:
        provider = _FakeProvider("plain", [LLMResponse(content="收尾汇报")])
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)

        response = await service.complete_with_native_tools(
            messages=MESSAGES, tools=[], caller="agent.loop"
        )
        assert response.content == "收尾汇报"
        assert response.tool_calls is None
        system_message = provider.plain_requests[0][0]
        assert "<available_tools>" not in str(system_message["content"])

    async def test_responses_flavor_simulation_recovers_via_budget_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Responses-flavor instances have no native FC, so the service runs
        prompt-simulated tool calling through ``complete()``; a reasoning
        model that truncates the simulated reply (incomplete /
        max_output_tokens) now recovers through the doubled-budget retry."""
        provider = OpenAIProvider(api_key="test-key", api_flavor="responses")
        registry = LLMRegistry()
        registry.register(provider)
        service = self._service(registry)
        calls: list[dict[str, Any]] = []

        async def fake_create(**kwargs: object) -> SimpleNamespace:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                return SimpleNamespace(
                    model="gpt-5-mini",
                    status="incomplete",
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                    output=[],
                    usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
                )
            return SimpleNamespace(
                model="gpt-5-mini",
                status="completed",
                output_text='{"tool_call": {"name": "list_sources", "arguments": {}}}',
                output=[],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
            )

        monkeypatch.setattr(provider._client.responses, "create", fake_create)

        response = await service.complete_with_native_tools(
            messages=MESSAGES, tools=TOOLS, caller="agent.loop"
        )

        assert response.tool_calls is not None
        assert response.tool_calls[0]["name"] == "list_sources"
        assert len(calls) == 2
        assert calls[1]["max_output_tokens"] == 2 * calls[0]["max_output_tokens"]
