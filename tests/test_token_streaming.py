"""Tests for token-level streaming end to end (issue #83).

Covers the LLM provider streaming contract (one-shot fallback and real
OpenAI-compatible streaming), registry pre-delta fallback semantics, the
service-layer native-tools streaming, agent-loop ``delta`` events,
``SocraticDialogue.respond_stream`` and both SSE chat endpoints. All LLM
calls are mocked; no real provider traffic happens here.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.api.app import create_app
from openbiliclaw.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMRegistry,
    LLMResponse,
    LLMStreamChunk,
    LLMToolCallUnsupportedError,
)
from openbiliclaw.llm.openai_provider import OpenAIProvider
from openbiliclaw.llm.service import LLMService
from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _chunk(text: str) -> Any:
    """Build a fake OpenAI streaming chunk carrying one content delta."""
    from types import SimpleNamespace

    return SimpleNamespace(
        model="gpt-4o",
        usage=None,
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))],
    )


class DummyProvider(LLMProvider):
    """Provider without a streaming override (base-class one-shot fallback)."""

    def __init__(self, content: str = "你好世界") -> None:
        self._content = content

    @property
    def name(self) -> str:
        return "dummy"

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content=self._content, model="dummy-model")


class ScriptedStreamProvider(LLMProvider):
    """Provider whose stream_complete replays a scripted chunk/error list."""

    def __init__(self, name: str, script: list[LLMStreamChunk | Exception]) -> None:
        self._name = name
        self.script = deque(script)

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="one-shot", model="m")

    async def stream_complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        while self.script:
            item = self.script.popleft()
            if isinstance(item, Exception):
                raise item
            yield item


async def _collect(stream: Any) -> list[Any]:
    return [item async for item in stream]


class TestProviderFallback:
    async def test_default_stream_complete_yields_one_shot(self) -> None:
        chunks = await _collect(
            DummyProvider().stream_complete([{"role": "user", "content": "hi"}])
        )

        assert [chunk.delta for chunk in chunks] == ["你好世界", ""]
        assert chunks[-1].response is not None
        assert chunks[-1].response.content == "你好世界"

    async def test_default_stream_complete_with_tools_unsupported(self) -> None:
        with pytest.raises(LLMToolCallUnsupportedError):
            await _collect(DummyProvider().stream_complete_with_tools([], []))


class TestOpenAIStreaming:
    async def test_stream_complete_yields_live_deltas_and_terminal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        provider = OpenAIProvider(api_key="k", model="gpt-4o")
        seen_kwargs: dict[str, Any] = {}

        async def fake_create(**kwargs: Any) -> Any:
            seen_kwargs.update(kwargs)

            async def gen() -> Any:
                yield _chunk("你")
                yield _chunk("好")
                yield SimpleNamespace(
                    model="gpt-4o",
                    usage=SimpleNamespace(
                        prompt_tokens=3,
                        completion_tokens=2,
                        total_tokens=5,
                        prompt_tokens_details=None,
                    ),
                    choices=[],
                )

            return gen()

        monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

        chunks = await _collect(provider.stream_complete([{"role": "user", "content": "hi"}]))

        assert seen_kwargs["stream"] is True
        assert seen_kwargs["stream_options"] == {"include_usage": True}
        assert [chunk.delta for chunk in chunks] == ["你", "好", ""]
        terminal = chunks[-1].response
        assert terminal is not None
        assert terminal.content == "你好"
        assert terminal.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    async def test_stream_complete_json_mode_keeps_one_shot_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        provider = OpenAIProvider(api_key="k", model="gpt-4o")
        seen_kwargs: dict[str, Any] = {}

        async def fake_create(**kwargs: Any) -> Any:
            seen_kwargs.update(kwargs)
            return SimpleNamespace(
                model="gpt-4o",
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"a": 1}'),
                        finish_reason="stop",
                    )
                ],
            )

        monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

        chunks = await _collect(
            provider.stream_complete([{"role": "user", "content": "hi"}], json_mode=True)
        )

        assert "stream" not in seen_kwargs
        assert [chunk.delta for chunk in chunks] == ['{"a": 1}', ""]
        assert chunks[-1].response is not None
        assert chunks[-1].response.content == '{"a": 1}'

    async def test_stream_complete_with_tools_accumulates_tool_call_deltas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        provider = OpenAIProvider(api_key="k", model="gpt-4o")

        async def fake_create(**kwargs: Any) -> Any:
            async def gen() -> Any:
                yield SimpleNamespace(
                    model="gpt-4o",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call_0",
                                        function=SimpleNamespace(
                                            name="get_profile", arguments='{"verbose": tr'
                                        ),
                                    )
                                ],
                            )
                        )
                    ],
                )
                yield SimpleNamespace(
                    model="gpt-4o",
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id=None,
                                        function=SimpleNamespace(name=None, arguments="ue}"),
                                    )
                                ],
                            )
                        )
                    ],
                )

            return gen()

        monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

        chunks = await _collect(
            provider.stream_complete_with_tools(
                [{"role": "user", "content": "看看我"}],
                [{"type": "function", "function": {"name": "get_profile"}}],
            )
        )

        assert len(chunks) == 1
        terminal = chunks[0].response
        assert terminal is not None
        assert terminal.content == ""
        assert terminal.tool_calls == [
            {
                "id": "call_0",
                "name": "get_profile",
                "arguments": {"verbose": True},
                "arguments_raw": '{"verbose": true}',
            }
        ]

    async def test_stream_complete_with_tools_responses_flavor_unsupported(self) -> None:
        provider = OpenAIProvider(api_key="k", model="gpt-5", api_flavor="responses")
        with pytest.raises(LLMToolCallUnsupportedError):
            await _collect(provider.stream_complete_with_tools([], []))

    async def test_stream_complete_midstream_error_is_mapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = OpenAIProvider(api_key="k", model="gpt-4o")

        async def fake_create(**kwargs: Any) -> Any:
            async def gen() -> Any:
                yield _chunk("半")
                raise TimeoutError("connection dropped")

            return gen()

        monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

        from openbiliclaw.llm.base import LLMTimeoutError

        with pytest.raises(LLMTimeoutError):
            await _collect(provider.stream_complete([{"role": "user", "content": "hi"}]))


class TestRegistryStreamingFallback:
    async def test_fallback_before_first_delta(self) -> None:
        primary = ScriptedStreamProvider("a", [LLMProviderError("boom")])
        fallback = ScriptedStreamProvider(
            "b",
            [LLMStreamChunk(delta="你"), LLMStreamChunk(response=LLMResponse(content="你好"))],
        )
        registry = LLMRegistry()
        registry.register(primary, default=True)
        registry.register(fallback)
        registry.fallback_provider = "b"

        chunks = await _collect(registry.stream_complete([{"role": "user", "content": "hi"}]))

        assert [chunk.delta for chunk in chunks] == ["你", ""]
        assert chunks[-1].response is not None
        assert chunks[-1].response.instance_id == "b"

    async def test_no_fallback_after_first_delta(self) -> None:
        primary = ScriptedStreamProvider(
            "a",
            [LLMStreamChunk(delta="半"), LLMProviderError("boom")],
        )
        fallback = ScriptedStreamProvider(
            "b",
            [LLMStreamChunk(response=LLMResponse(content="兜底"))],
        )
        registry = LLMRegistry()
        registry.register(primary, default=True)
        registry.register(fallback)
        registry.fallback_provider = "b"

        with pytest.raises(LLMProviderError):
            await _collect(registry.stream_complete([{"role": "user", "content": "hi"}]))
        # The fallback was never consulted: retrying would duplicate the
        # already-displayed fragment.
        assert len(fallback.script) == 1


class _FakeMemory:
    def get_layer(self, name: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(data={})

    def get_core_memory(self) -> dict[str, Any]:
        return {}


class TestServiceNativeToolStreaming:
    async def test_native_route_streams_deltas_and_terminal(self) -> None:
        provider = ScriptedStreamProvider("main", [])
        provider.supports_tool_calling = True

        async def stream_tools(messages: Any, tools: Any, **kwargs: Any) -> Any:
            yield LLMStreamChunk(delta="答")
            yield LLMStreamChunk(delta="案")
            yield LLMStreamChunk(response=LLMResponse(content="答案"))

        provider.stream_complete_with_tools = stream_tools  # type: ignore[method-assign]
        registry = LLMRegistry()
        registry.register(provider)
        service = LLMService(registry=registry, memory=_FakeMemory())  # type: ignore[arg-type]

        chunks = await _collect(
            service.stream_complete_with_native_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "t"}}],
                caller="agent.loop",
            )
        )

        assert [chunk.delta for chunk in chunks] == ["答", "案", ""]
        terminal = chunks[-1].response
        assert terminal is not None
        assert terminal.content == "答案"
        assert terminal.instance_id == "main"

    async def test_simulated_route_emits_single_buffered_delta(self) -> None:
        class SimulatedProvider(LLMProvider):
            @property
            def name(self) -> str:
                return "sim"

            async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
                return LLMResponse(content="最终回复", model="m")

        registry = LLMRegistry()
        registry.register(SimulatedProvider())
        service = LLMService(registry=registry, memory=_FakeMemory())  # type: ignore[arg-type]

        chunks = await _collect(
            service.stream_complete_with_native_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                caller="agent.loop",
            )
        )

        assert [chunk.delta for chunk in chunks] == ["最终回复", ""]
        assert chunks[-1].response is not None
        assert chunks[-1].response.content == "最终回复"


class StreamingAgentLLM:
    """Service-shaped double streaming scripted chunks per hop."""

    def __init__(self, scripts: list[list[LLMStreamChunk | Exception]]) -> None:
        self._scripts = deque(scripts)

    def stream_complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        caller: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        bypass_semaphore: bool = False,
    ) -> Any:
        script = self._scripts.popleft()

        async def gen() -> Any:
            for item in script:
                if isinstance(item, Exception):
                    raise item
                yield item

        return gen()


class TestAgentLoopTokenStreaming:
    async def test_final_answer_streams_token_deltas(self) -> None:
        llm = StreamingAgentLLM(
            [
                [
                    LLMStreamChunk(delta="我先看看"),
                    LLMStreamChunk(
                        response=LLMResponse(
                            content="我先看看",
                            tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "x"}}],
                        )
                    ),
                ],
                [
                    LLMStreamChunk(delta="你"),
                    LLMStreamChunk(delta="好"),
                    LLMStreamChunk(response=LLMResponse(content="你好")),
                ],
            ]
        )
        registry = ToolRegistry(
            [Tool(name="echo", description="回显", handler=lambda args: args.get("text", ""))]
        )
        loop = AgentLoop(llm, registry)  # type: ignore[arg-type]

        events = await _collect(loop.run(system_instruction="sys", user_message="你好"))

        assert [event.type for event in events] == [
            "delta",
            "thinking",
            "tool_call",
            "tool_result",
            "delta",
            "delta",
            "final",
        ]
        assert events[0].text == "我先看看"
        assert events[-1].text == "你好"

    async def test_service_without_streaming_still_works(self) -> None:
        class OneShotLLM:
            async def complete_with_native_tools(self, **kwargs: Any) -> LLMResponse:
                return LLMResponse(content="一次性回复")

        loop = AgentLoop(OneShotLLM(), ToolRegistry([]))  # type: ignore[arg-type]

        events = await _collect(loop.run(system_instruction="sys", user_message="hi"))

        assert [event.type for event in events] == ["final"]
        assert events[0].text == "一次性回复"


class FakeStreamingDialogueService:
    """Service double streaming socratic replies token by token."""

    def __init__(self, chunks: list[LLMStreamChunk | Exception]) -> None:
        self._chunks = deque(chunks)

    def stream_socratic_dialogue(
        self, *, user_message: str, history: list[dict[str, str]], caller: str = ""
    ) -> Any:
        async def gen() -> Any:
            while self._chunks:
                item = self._chunks.popleft()
                if isinstance(item, Exception):
                    raise item
                yield item

        return gen()


def _dialogue(service: Any) -> SocraticDialogue:
    return SocraticDialogue(
        llm=None,
        soul_engine=object(),
        llm_service=service,
        session="cli",
        learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
    )


class TestDialogueRespondStream:
    async def test_streams_deltas_and_records_history(self) -> None:
        service = FakeStreamingDialogueService(
            [
                LLMStreamChunk(delta="你"),
                LLMStreamChunk(delta="好"),
                LLMStreamChunk(response=LLMResponse(content="你好")),
            ]
        )
        dialogue = _dialogue(service)

        deltas = await _collect(dialogue.respond_stream("在吗"))

        assert deltas == ["你", "好"]
        history = dialogue.history
        assert [turn.role for turn in history] == ["user", "agent"]
        assert history[-1].content == "你好"

    async def test_failure_rolls_back_history(self) -> None:
        service = FakeStreamingDialogueService(
            [LLMStreamChunk(delta="半"), LLMProviderError("boom")]
        )
        dialogue = _dialogue(service)

        with pytest.raises(LLMProviderError):
            await _collect(dialogue.respond_stream("在吗"))
        assert dialogue.history == []

    async def test_service_without_streaming_falls_back_to_one_shot(self) -> None:
        class OneShotService:
            async def complete_socratic_dialogue(self, **kwargs: Any) -> LLMResponse:
                return LLMResponse(content="整段回复")

        dialogue = _dialogue(OneShotService())

        deltas = await _collect(dialogue.respond_stream("在吗"))

        assert deltas == ["整段回复"]
        assert dialogue.history[-1].content == "整段回复"


class FakeStreamAPIDialogue:
    """API-layer double with both legacy respond_stream and agent streaming."""

    def __init__(self, script: list[Any]) -> None:
        self._script = script

    async def respond(self, message: str, **kwargs: Any) -> str:
        return "一次性回复"

    def respond_stream(self, message: str, **kwargs: Any) -> Any:
        async def gen() -> Any:
            yield "你"
            yield "好"

        return gen()

    async def stream_agent_reply(self, agent_loop: Any, message: str, **kwargs: Any) -> Any:
        for item in self._script:
            if isinstance(item, Exception):
                raise item
            yield item


def _api_app(tmp_path: Path, dialogue: Any) -> Any:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue,
    )
    app.state.runtime_context.agent_loop = object()
    return app


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


class TestChatStreamEndpoints:
    def test_agent_stream_forwards_delta_events_but_does_not_persist_them(
        self, tmp_path: Path
    ) -> None:
        from openbiliclaw.agent.loop import AgentEvent

        dialogue = FakeStreamAPIDialogue(
            [
                AgentEvent(type="delta", step=1, text="你"),
                AgentEvent(type="delta", step=1, text="好"),
                AgentEvent(type="final", step=1, text="你好"),
            ]
        )
        app = _api_app(tmp_path, dialogue)

        with TestClient(app) as client:
            created = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "stream-turn-1",
                    "session": "popup",
                    "message": "在吗",
                    "streaming": True,
                },
            )
            assert created.status_code == 200
            response = client.post(
                "/api/chat/agent/stream",
                json={"turn_id": "stream-turn-1", "message": "在吗"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert [event for event, _data in events] == ["delta", "delta", "final", "done"]
        assert events[0][1]["text"] == "你"
        assert events[3][1]["reply"] == "你好"

        row = app.state.runtime_context.database.get_chat_turn("stream-turn-1")
        assert row is not None
        assert row["status"] == "completed"
        persisted = row["payload"]["agent_events"]
        assert [event["type"] for event in persisted] == ["final"]

    def test_legacy_chat_stream_sends_real_deltas(self, tmp_path: Path) -> None:
        dialogue = FakeStreamAPIDialogue([])
        app = _api_app(tmp_path, dialogue)

        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json={"message": "在吗"})

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert [event for event, _data in events] == ["phase", "content", "content", "done"]
        assert events[1][1] == {"delta": "你"}
        assert events[2][1] == {"delta": "好"}
        assert events[3][1]["reply"] == "你好"
