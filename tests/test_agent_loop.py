"""Tests for the multi-hop AgentLoop (M1)."""

from __future__ import annotations

from collections import deque
from typing import Any

from openbiliclaw.agent.loop import (
    DEFAULT_AGENT_LOOP_MAX_STEPS,
    AgentEvent,
    AgentLoop,
)
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.config import AgentConfig, Config
from openbiliclaw.llm.base import LLMProvider, LLMRegistry, LLMResponse
from openbiliclaw.llm.service import LLMService


class FakeAgentLLM:
    """Service-shaped double returning queued LLMResponses."""

    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete_with_native_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        caller: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        bypass_semaphore: bool = False,
    ) -> LLMResponse:
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "caller": caller,
            }
        )
        item = self._responses.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "") -> dict[str, Any]:
    return {"id": call_id or f"call-{name}", "name": name, "arguments": arguments}


def _registry_with(*tools: Tool) -> ToolRegistry:
    return ToolRegistry(tools)


def _echo_tool(name: str = "get_profile", result: str = "画像：喜欢机械键盘") -> Tool:
    return Tool(name=name, description=f"{name} desc", handler=lambda _args: result)


async def _collect(loop: AgentLoop, **kwargs: Any) -> list[AgentEvent]:
    return [event async for event in loop.run(**kwargs)]


class TestAgentLoopMultiHop:
    async def test_two_hops_then_final(self) -> None:
        saved: list[dict[str, Any]] = []
        registry = _registry_with(
            _echo_tool(),
            Tool(
                name="save_note",
                description="记下笔记",
                permission_level="soft_write",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=lambda args: saved.append(args) or "已记下",
            ),
        )
        llm = FakeAgentLLM(
            [
                LLMResponse(
                    content="我先看看你的画像",
                    tool_calls=[_tool_call("get_profile", {})],
                ),
                LLMResponse(
                    content="",
                    tool_calls=[_tool_call("save_note", {"text": "喜欢机械键盘"}, "c2")],
                ),
                LLMResponse(content="已经帮你记下来啦"),
            ]
        )
        loop = AgentLoop(llm, registry)

        events = await _collect(
            loop,
            system_instruction="你是口味伙伴",
            user_message="帮我记住我喜欢机械键盘",
        )

        assert [event.type for event in events] == [
            "thinking",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "final",
        ]
        assert events[0].text == "我先看看你的画像"
        assert events[1].tool_name == "get_profile"
        assert events[1].summary == "get_profile()"
        assert events[2].ok and events[2].text == "画像：喜欢机械键盘"
        assert events[3].tool_name == "save_note"
        assert events[3].arguments == {"text": "喜欢机械键盘"}
        assert events[4].ok and events[4].text == "已记下"
        assert events[5].text == "已经帮你记下来啦"
        assert saved == [{"text": "喜欢机械键盘"}]

        # The second hop sees the assistant tool-call message and the tool result.
        hop2_messages = llm.calls[1]["messages"]
        assistant_message = hop2_messages[-2]
        assert assistant_message["role"] == "assistant"
        assert assistant_message["tool_calls"][0]["function"]["name"] == "get_profile"
        tool_message = hop2_messages[-1]
        assert tool_message == {
            "role": "tool",
            "tool_call_id": "call-get_profile",
            "content": "画像：喜欢机械键盘",
        }

        # Every event serializes for the M2 SSE layer.
        for event in events:
            assert event.to_dict()["type"] == event.type

    async def test_parallel_tool_calls_in_one_hop(self) -> None:
        registry = _registry_with(_echo_tool("a", "A"), _echo_tool("b", "B"))
        llm = FakeAgentLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[_tool_call("a", {}, "c1"), _tool_call("b", {}, "c2")],
                ),
                LLMResponse(content="完成"),
            ]
        )
        loop = AgentLoop(llm, registry)
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert [event.type for event in events] == [
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "final",
        ]
        hop2_messages = llm.calls[1]["messages"]
        tool_messages = hop2_messages[-2:]
        assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]

    async def test_plain_text_first_hop_finals_immediately(self) -> None:
        llm = FakeAgentLLM([LLMResponse(content="直接回答")])
        loop = AgentLoop(llm, _registry_with(_echo_tool()))
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert [event.type for event in events] == ["final"]
        assert events[-1].text == "直接回答"


class TestAgentLoopStepLimit:
    async def test_step_limit_triggers_wrap_up(self) -> None:
        registry = _registry_with(_echo_tool())
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("get_profile", {})]),
                LLMResponse(content="", tool_calls=[_tool_call("get_profile", {})]),
                LLMResponse(content="进展汇报：已经查了两次画像"),
            ]
        )
        loop = AgentLoop(llm, registry, max_steps=2)

        events = await _collect(loop, system_instruction="s", user_message="u")

        assert [event.type for event in events] == [
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
            "step_limit_reached",
            "final",
        ]
        assert events[-2].step == 2
        assert events[-1].text == "进展汇报：已经查了两次画像"
        # The wrap-up call carries no tools, so the model cannot keep calling.
        assert llm.calls[2]["tools"] == []
        wrap_up_message = llm.calls[2]["messages"][-1]
        assert wrap_up_message["role"] == "user"
        assert "步数上限" in wrap_up_message["content"]

    async def test_wrap_up_failure_degrades_to_static_summary(self) -> None:
        registry = _registry_with(_echo_tool())
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("get_profile", {})]),
                RuntimeError("wrap-up exploded"),
            ]
        )
        loop = AgentLoop(llm, registry, max_steps=1)
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert [event.type for event in events] == [
            "tool_call",
            "tool_result",
            "step_limit_reached",
            "final",
        ]
        assert "步数上限" in events[-1].text


class TestAgentLoopToolFailures:
    async def test_unknown_tool_is_fed_back_as_error(self) -> None:
        registry = _registry_with(_echo_tool())
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("hack_the_planet", {})]),
                LLMResponse(content="好吧，没有这个工具"),
            ]
        )
        loop = AgentLoop(llm, registry)
        events = await _collect(loop, system_instruction="s", user_message="u")
        result_event = events[1]
        assert result_event.type == "tool_result"
        assert not result_event.ok
        assert "未知工具" in result_event.text
        # The error result went back into the conversation.
        assert "未知工具" in llm.calls[1]["messages"][-1]["content"]
        assert events[-1].type == "final"

    async def test_invalid_arguments_are_fed_back(self) -> None:
        registry = _registry_with(
            Tool(
                name="save_note",
                description="记下笔记",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=lambda args: "已记下",
            )
        )
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("save_note", {})]),
                LLMResponse(content="参数没带对，重来"),
            ]
        )
        loop = AgentLoop(llm, registry)
        events = await _collect(loop, system_instruction="s", user_message="u")
        assert events[1].type == "tool_result"
        assert not events[1].ok
        assert "参数校验失败" in events[1].text

    async def test_long_tool_result_is_truncated(self) -> None:
        registry = _registry_with(_echo_tool(result="x" * 5000))
        llm = FakeAgentLLM(
            [
                LLMResponse(content="", tool_calls=[_tool_call("get_profile", {})]),
                LLMResponse(content="看完"),
            ]
        )
        loop = AgentLoop(llm, registry, tool_result_max_chars=500)
        events = await _collect(loop, system_instruction="s", user_message="u")
        result_event = events[1]
        assert result_event.truncated
        assert len(result_event.text) < 600
        # The truncated text, not the full one, is fed back to the model.
        assert llm.calls[1]["messages"][-1]["content"] == result_event.text

    async def test_per_run_tool_subset(self) -> None:
        registry = _registry_with(_echo_tool("a"), _echo_tool("b"))
        llm = FakeAgentLLM([LLMResponse(content="好")])
        loop = AgentLoop(llm, registry)
        await _collect(
            loop,
            system_instruction="s",
            user_message="u",
            tools=registry.subset(["a"]),
        )
        schema_names = [entry["function"]["name"] for entry in llm.calls[0]["tools"]]
        assert schema_names == ["a"]


class _SimulatedProvider(LLMProvider):
    """Non-FC provider double for the prompt-simulation loop path."""

    supports_tool_calling = False

    def __init__(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self.requests: list[list[dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return "simulated"

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
        self.requests.append([dict(m) for m in messages])
        return LLMResponse(content=self._responses.popleft())


class TestAgentLoopSimulatedFallback:
    async def test_end_to_end_prompt_simulation(self) -> None:
        provider = _SimulatedProvider(
            [
                '{"tool_call": {"name": "get_profile", "arguments": {}}}',
                "你的画像里全是机械键盘",
            ]
        )
        registry = LLMRegistry()
        registry.register(provider)
        service = LLMService(registry=registry, memory=object())  # type: ignore[arg-type]
        loop = AgentLoop(service, _registry_with(_echo_tool()))

        events = await _collect(loop, system_instruction="你是口味伙伴", user_message="看看我")

        assert [event.type for event in events] == [
            "tool_call",
            "tool_result",
            # The simulated final hop streams as one buffered delta chunk.
            "delta",
            "final",
        ]
        assert events[0].tool_name == "get_profile"
        assert events[-1].text == "你的画像里全是机械键盘"
        # Second hop flattened the tool result into the simulated prompt.
        second_call = provider.requests[1]
        assert any("[工具执行结果]" in str(m["content"]) for m in second_call)


class TestAgentLoopConfig:
    def test_from_config_reads_agent_section(self) -> None:
        config = Config()
        config.agent = AgentConfig(loop_max_steps=7, tool_result_max_chars=900)
        loop = AgentLoop.from_config(
            FakeAgentLLM([]),
            _registry_with(),
            config,
        )
        assert loop.max_steps == 7

    def test_default_max_steps(self) -> None:
        loop = AgentLoop(FakeAgentLLM([]), _registry_with())
        assert loop.max_steps == DEFAULT_AGENT_LOOP_MAX_STEPS == 64

    def test_max_steps_floor(self) -> None:
        loop = AgentLoop(FakeAgentLLM([]), _registry_with(), max_steps=0)
        assert loop.max_steps == 1
