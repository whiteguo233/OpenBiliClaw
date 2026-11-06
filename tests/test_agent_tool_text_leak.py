"""Replay a real free-model reply that leaked a tool envelope into prose."""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tool_text import ToolTextGuard
from openbiliclaw.agent.tools import Tool, ToolRegistry
from openbiliclaw.llm.base import LLMResponse, LLMResponseError, LLMStreamChunk

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

ANSWER = "目前 core 层记忆尚未建立完整画像，兴趣信息不足。"
LEAKED_REPLY = ANSWER + '\n\n{"tool_call": {"name": "get_profile", "arguments": {}}}'


class ReplayLLM:
    """Keep real AgentLoop dispatch/stream handling around recorded model text."""

    def __init__(self, responses: list[LLMResponse], chunk_size: int = 3) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []
        self.chunk_size = chunk_size

    async def complete_with_native_tools(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return self.responses.popleft()

    async def stream_complete_with_native_tools(
        self, **kwargs: Any
    ) -> AsyncIterator[LLMStreamChunk]:
        response = await self.complete_with_native_tools(**kwargs)
        for start in range(0, len(response.content), self.chunk_size):
            yield LLMStreamChunk(delta=response.content[start : start + self.chunk_size])
        yield LLMStreamChunk(response=response)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 128])
async def test_recorded_tool_json_never_reaches_stream_or_final_reply(chunk_size: int) -> None:
    executions: list[str] = []
    registry = ToolRegistry(
        [
            Tool(
                name="read_memory",
                description="读取记忆",
                handler=lambda _args: executions.append("read_memory") or "尚未建立完整画像",
            ),
            Tool(
                name="get_profile",
                description="读取画像",
                handler=lambda _args: executions.append("get_profile") or "空画像",
            ),
        ],
    )
    llm = ReplayLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[{"id": "read-1", "name": "read_memory", "arguments": {}}],
                provider="siliconflow_free",
                model="Qwen/Qwen3.5-4B",
            ),
            LLMResponse(content=LEAKED_REPLY, provider="siliconflow_free", model="Qwen/Qwen3.5-4B"),
            LLMResponse(content=ANSWER, provider="siliconflow_free", model="Qwen/Qwen3.5-4B"),
        ],
        chunk_size=chunk_size,
    )
    events = [
        event
        async for event in AgentLoop(llm, registry).run(
            system_instruction="你是口味伙伴。",
            user_message="请先调用 read_memory 读取 core 记忆，再用一句话说明兴趣信息是否足够。",
        )
    ]

    assert executions == ["read_memory"]
    assert events[-1].type == "final"
    assert events[-1].text == ANSWER
    streamed = "".join(event.text for event in events if event.type == "delta")
    assert streamed.strip() == ANSWER


def test_plain_prose_is_available_before_the_hop_finishes() -> None:
    guard = ToolTextGuard({"get_profile"})
    assert guard.feed("正常") == "正常"
    assert guard.feed("的流式正文。") == "的流式正文。"
    assert guard.feed('\n\n{"tool_') == "\n\n"
    assert guard.feed('call":{"name":"get_profile","arguments":{}}}') == ""
    assert guard.finish() == ""
    assert guard.dropped


@pytest.mark.parametrize(
    "suffix",
    [
        json.dumps({"tool_call": {"name": "get_profile", "arguments": {"s": chr(34) + "{}"}}}),
        '{"tool_calls":[{"name":"get_profile","arguments":{}}]}',
        '{"tool_calls":[{"id":"a","type":"function",'
        '"function":{"name":"get_profile","arguments":"{}"}}]}',
        '```json\n{"tool_call":{"name":"get_profile","arguments":{}}}\n```',
        '~~~~\n{"tool_call":{"name":"get_profile","arguments":{}}}\n~~~~',
    ],
)
def test_tool_envelope_detection_is_independent_of_chunk_boundaries(suffix: str) -> None:
    text = ANSWER + "\n\n" + suffix
    for split in range(len(text) + 1):
        guard = ToolTextGuard({"get_profile"})
        actual = guard.feed(text[:split]) + guard.feed(text[split:]) + guard.finish()
        assert actual.strip() == ANSWER
        assert guard.dropped


@pytest.mark.parametrize(
    "text",
    [
        '{"interests": ["Rust"]}',
        '{"tool_call":{"name":"other_service","arguments":{}}}',
        '{"tool_call":{"name":"get_profile","arguments":{}},"description":"data"}',
        '{"tool_calls":[]}',
        '示例：{"tool_call":{"name":"get_profile","arguments":{}}}',
        '```json\n\n{"description":"普通数据"}\n```',
        '~~~~json\n\n{"description":"普通数据"}\n~~~~',
        '```json\n{"tool_call":{"name":"get_profile","arguments":{}}}',
        '\n\n    {"tool_call":{"name":"get_profile","arguments":{}}}',
        '\n\n> {"tool_call":{"name":"get_profile","arguments":{}}}',
        '\n\n`{"tool_call":{"name":"get_profile","arguments":{}}}`',
        ANSWER + '\n\n{"tool_call": incomplete',
        '{"tool_call":{"name":"get_profile","arguments":{"value":"' + "a" * 32768,
    ],
)
def test_data_examples_unknown_and_incomplete_json_are_preserved(text: str) -> None:
    guard = ToolTextGuard({"get_profile"})
    output = "".join(guard.feed(text[start : start + 7]) for start in range(0, len(text), 7))
    assert output + guard.finish() == text
    assert not guard.dropped


@pytest.mark.parametrize(
    "text",
    [
        LEAKED_REPLY + "\n\n这是需要逐字保留的数据示例。",
        '```json\n{"tool_call":{"name":"get_profile","arguments":{}}}\n```\n这是示例。',
    ],
)
def test_explicit_data_policy_preserves_nonterminal_examples(text: str) -> None:
    guard = ToolTextGuard({"get_profile"}, preserve=True)
    output = "".join(guard.feed(text[start : start + 3]) for start in range(0, len(text), 3))
    assert output + guard.finish() == text
    assert not guard.dropped


@pytest.mark.parametrize(
    "user_request",
    [
        "请输出工具调用的JSON示例。",
        "请展示 tool_call 的结构。",
        "读取该字段的原值，不要更改。",
        "Return this object verbatim.",
    ],
)
@pytest.mark.parametrize("fenced", [False, True])
async def test_explicit_data_requests_can_return_protocol_text(
    user_request: str, fenced: bool
) -> None:
    reply = f"```json\n{LEAKED_REPLY.split(chr(10) * 2)[1]}\n```" if fenced else LEAKED_REPLY
    llm = ReplayLLM([LLMResponse(content=reply)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    events = [
        event
        async for event in AgentLoop(llm, tools).run(
            system_instruction="s", user_message=user_request
        )
    ]
    assert events[-1].text == reply
    assert "".join(e.text for e in events if e.type == "delta") == reply
    assert len(llm.calls) == 1


@pytest.mark.parametrize("fence", ["", "```json", "~~~~"])
async def test_protocol_only_reply_retries_once_without_executing_its_text(fence: str) -> None:
    executions: list[str] = []
    tools = ToolRegistry(
        [
            Tool(
                name="get_profile",
                description="Read",
                handler=lambda _: executions.append("read") or "没有兴趣资料",
            )
        ]
    )
    protocol = LEAKED_REPLY.split("\n\n")[1]
    if fence:
        protocol = f"{fence}\n{protocol}\n{fence.rstrip('json')}"
    llm = ReplayLLM(
        [
            LLMResponse(content=protocol),
            LLMResponse(tool_calls=[{"name": "get_profile", "arguments": {}}]),
            LLMResponse(content=ANSWER),
        ]
    )
    events = [
        event
        async for event in AgentLoop(llm, tools).run(
            system_instruction="s", user_message="我喜欢什么？"
        )
    ]
    assert executions == ["read"]
    assert events[-1].text == ANSWER
    assert "".join(e.text for e in events if e.type == "delta") == ANSWER
    assert len(llm.calls) == 3
    assert "没有执行" in llm.calls[1]["messages"][-1]["content"]


async def test_fenced_unexecuted_tool_cannot_claim_a_successful_read_afterwards() -> None:
    executed: list[str] = []
    tools = ToolRegistry(
        [
            Tool(
                name="read_memory",
                description="Read",
                handler=lambda _: executed.append("read") or "尚未建立画像",
            )
        ]
    )
    bad_reply = (
        '```json\n{"tool_calls":[{"name":"read_memory","arguments":{"layer":"core"}}]}\n```'
        "\n\n已读取核心记忆，目前已具备足够信息提供推荐。"
    )
    llm = ReplayLLM(
        [
            LLMResponse(content=bad_reply),
            LLMResponse(tool_calls=[{"name": "read_memory", "arguments": {"layer": "core"}}]),
            LLMResponse(content=ANSWER),
        ]
    )
    events = [
        e
        async for e in AgentLoop(llm, tools).run(
            system_instruction="s", user_message="请读取记忆。"
        )
    ]
    assert executed == ["read"]
    assert events[-1].text == ANSWER
    assert "".join(e.text for e in events if e.type == "delta") == ANSWER


async def test_correction_without_tool_evidence_cannot_claim_success() -> None:
    tools = ToolRegistry([Tool(name="read_memory", description="Read", handler=lambda _: "")])
    llm = ReplayLLM(
        [
            LLMResponse(
                content='```json\n{"tool_call":{"name":"read_memory","arguments":{}}}\n```'
            ),
            LLMResponse(content="已读取 core 记忆，当前兴趣信息充足，可直接给出建议。"),
        ]
    )
    events = []
    with pytest.raises(LLMResponseError, match="tool evidence"):
        async for event in AgentLoop(llm, tools).run(
            system_instruction="s", user_message="请先读取核心记忆。"
        ):
            events.append(event)
    assert events == []  # Neither display nor history receives the unsupported claim.
    assert len(llm.calls) == 2


async def test_repeated_protocol_only_reply_fails_without_a_successful_empty_reply() -> None:
    protocol = LEAKED_REPLY.split("\n\n")[1]
    llm = ReplayLLM([LLMResponse(content=protocol), LLMResponse(content=protocol)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    events = []
    with pytest.raises(LLMResponseError, match="tool protocol"):
        async for event in AgentLoop(llm, tools).run(system_instruction="s", user_message="你好"):
            events.append(event)
    assert events == []
    assert len(llm.calls) == 2


async def test_one_shot_reply_and_durable_dialogue_history_share_the_guard() -> None:
    from tests.test_agent_loop import FakeAgentLLM
    from tests.test_dialogue_agent_stream import _dialogue

    llm = FakeAgentLLM([LLMResponse(content=LEAKED_REPLY)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    dialogue = _dialogue(object())
    events = [
        event async for event in dialogue.stream_agent_reply(AgentLoop(llm, tools), "我喜欢什么？")
    ]
    assert events[-1].text == ANSWER
    assert dialogue.history[-1].content == ANSWER


@pytest.mark.parametrize("locale", ["zh-Hans", "en"])
@pytest.mark.parametrize(
    ("user_request", "expected"),
    [("请报告当前情况。", ANSWER), ("请原样给出 JSON 示例。", LEAKED_REPLY)],
)
async def test_localized_dialogue_uses_raw_user_text_for_output_policy(
    locale: str, user_request: str, expected: str
) -> None:
    from types import SimpleNamespace

    from openbiliclaw.soul.dialogue import DialogueLearningMode
    from tests.test_dialogue_agent_stream import RecordingSettlementQueue, _dialogue

    llm = ReplayLLM([LLMResponse(content=LEAKED_REPLY)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    queue = RecordingSettlementQueue()
    service = SimpleNamespace(
        output_language=locale,
        memory=SimpleNamespace(render_agent_notes_prompt=lambda: "参考笔记：用户曾学习 JSON。"),
    )
    dialogue = _dialogue(service, mode=DialogueLearningMode.QUEUED, queue=queue)
    events = [
        event async for event in dialogue.stream_agent_reply(AgentLoop(llm, tools), user_request)
    ]
    assert events[-1].text == expected
    assert "".join(e.text for e in events if e.type == "delta").strip() == expected
    assert dialogue.history[-1].content == expected
    assert queue.submissions[-1][1]["assistant_reply"] == expected
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "<output_language>" in prompt and "Keep JSON keys" in prompt
    assert "参考笔记" in prompt


async def test_a_previous_json_request_does_not_disable_the_next_normal_reply_guard() -> None:
    llm = ReplayLLM([LLMResponse(content=LEAKED_REPLY)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    events = [
        event
        async for event in AgentLoop(llm, tools).run(
            system_instruction="s",
            user_message="现在告诉我兴趣信息是否足够。",
            history=[{"role": "user", "content": "上一轮请给JSON示例"}],
        )
    ]
    assert events[-1].text == ANSWER


async def test_corrective_attempt_still_uses_the_native_approval_gate() -> None:
    from types import SimpleNamespace

    submitted: list[dict[str, Any]] = []
    executed: list[str] = []

    def submit(**kwargs: Any) -> Any:
        submitted.append(kwargs)
        return SimpleNamespace(approval_id="approval-1")

    llm = ReplayLLM(
        [
            LLMResponse(content='{"tool_call":{"name":"update_config","arguments":{}}}'),
            LLMResponse(tool_calls=[{"id": "real-1", "name": "update_config", "arguments": {}}]),
            LLMResponse(content="操作已提交审批，尚未执行。"),
        ]
    )
    tools = ToolRegistry(
        [
            Tool(
                name="update_config",
                description="Update",
                permission_level="hard_write",
                handler=lambda _: executed.append("write"),
            )
        ]
    )
    events = [
        event
        async for event in AgentLoop(llm, tools, approval_gate=SimpleNamespace(submit=submit)).run(
            system_instruction="s", user_message="请更新设置。"
        )
    ]
    assert executed == []
    assert len(submitted) == 1 and submitted[0]["tool_name"] == "update_config"
    assert [e.type for e in events].count("approval_request") == 1
    assert len(llm.calls) == 3


async def test_correction_does_not_repeat_an_already_completed_tool() -> None:
    executed: list[str] = []
    tools = ToolRegistry(
        [
            Tool(
                name="save_item",
                description="Save",
                permission_level="soft_write",
                handler=lambda _: executed.append("save") or "已保存",
            )
        ]
    )
    llm = ReplayLLM(
        [
            LLMResponse(tool_calls=[{"name": "save_item", "arguments": {}}]),
            LLMResponse(content='{"tool_call":{"name":"save_item","arguments":{}}}'),
            LLMResponse(content="已保存。"),
        ]
    )
    events = [
        event
        async for event in AgentLoop(llm, tools).run(
            system_instruction="s", user_message="保存它。"
        )
    ]
    assert executed == ["save"]
    assert events[-1].text == "已保存。"
    assert len(llm.calls) == 3


@pytest.mark.parametrize("changing", [False, True])
async def test_unchanged_read_loop_stops_before_a_third_identical_execution(changing: bool) -> None:
    executed: list[int] = []

    def read(_args: Any) -> str:
        executed.append(len(executed))
        return str(len(executed)) if changing else "尚未建立画像"

    tools = ToolRegistry([Tool(name="read_memory", description="Read", handler=read)])
    llm = ReplayLLM(
        [
            *[
                LLMResponse(tool_calls=[{"name": "read_memory", "arguments": {"layer": "core"}}])
                for _ in range(3)
            ],
            LLMResponse(content=ANSWER),
        ]
    )
    events = [
        e
        async for e in AgentLoop(llm, tools).run(system_instruction="s", user_message="读取记忆。")
    ]
    assert len(executed) == (3 if changing else 2)
    assert bool(llm.calls[-1]["tools"]) is changing
    assert events[-1].text == ANSWER


@pytest.mark.parametrize("case", ["different_arguments", "write", "error"])
async def test_read_loop_guard_does_not_block_other_progress(case: str) -> None:
    executed: list[dict[str, Any]] = []

    def handler(args: dict[str, Any]) -> str:
        executed.append(args)
        if case == "error":
            raise RuntimeError("temporarily unavailable")
        return "unchanged"

    tool = Tool(
        name="check_item",
        description="Check",
        handler=handler,
        permission_level="soft_write" if case == "write" else "read",
    )
    llm = ReplayLLM(
        [
            *[
                LLMResponse(
                    tool_calls=[
                        {
                            "name": tool.name,
                            "arguments": {
                                "item": 2 if index == 2 and case == "different_arguments" else 1
                            },
                        }
                    ]
                )
                for index in range(3)
            ],
            LLMResponse(content="已结束。"),
        ]
    )
    _ = [
        e
        async for e in AgentLoop(llm, ToolRegistry([tool])).run(
            system_instruction="s", user_message="检查项目。"
        )
    ]
    assert len(executed) == 3
    assert llm.calls[-1]["tools"]


def test_http_stream_and_persisted_turn_use_the_sanitized_reply(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from tests.test_chat_agent_stream_api import _app, _parse_sse
    from tests.test_dialogue_agent_stream import _dialogue

    llm = ReplayLLM([LLMResponse(content=LEAKED_REPLY)])
    tools = ToolRegistry([Tool(name="get_profile", description="Read", handler=lambda _: "")])
    app = _app(tmp_path, _dialogue(object()))
    app.state.runtime_context.agent_loop = AgentLoop(llm, tools)
    body = {
        "turn_id": "protocol-guard-turn",
        "session": "desktop",
        "message": "我喜欢什么？",
        "streaming": True,
    }
    with TestClient(app) as client:
        assert client.post("/api/chat/turns", json=body).status_code == 200
        streamed = client.post("/api/chat/agent/stream", json=body)
        assert streamed.status_code == 200
        events = _parse_sse(streamed.text)
        assert events[-1][0] == "done" and events[-1][1]["reply"] == ANSWER
        assert "".join(
            data.get("text", "") for name, data in events if name == "delta"
        ).strip() == (ANSWER)
        saved = client.get("/api/chat/turns/protocol-guard-turn").json()
        assert saved["status"] == "completed" and saved["reply"] == ANSWER
        assert '"tool_call"' not in json.dumps(saved["payload"]["agent_events"])


def test_http_rejects_unexecuted_correction_without_persisting_false_success(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from tests.test_chat_agent_stream_api import _app, _parse_sse
    from tests.test_dialogue_agent_stream import _dialogue

    claim = "已读取 core 记忆，当前兴趣信息充足。"
    llm = ReplayLLM(
        [
            LLMResponse(
                content='```json\n{"tool_call":{"name":"read_memory","arguments":{}}}\n```'
            ),
            LLMResponse(content=claim),
        ]
    )
    tools = ToolRegistry([Tool(name="read_memory", description="Read", handler=lambda _: "")])
    dialogue = _dialogue(object())
    app = _app(tmp_path, dialogue)
    app.state.runtime_context.agent_loop = AgentLoop(llm, tools)
    body = {
        "turn_id": "protocol-evidence-turn",
        "session": "desktop",
        "message": "请先读取核心记忆。",
        "streaming": True,
    }
    with TestClient(app) as client:
        assert client.post("/api/chat/turns", json=body).status_code == 200
        streamed = client.post("/api/chat/agent/stream", json=body)
        assert streamed.status_code == 200
        events = _parse_sse(streamed.text)
        assert any(kind == "error" for kind, _ in events)
        assert not any(kind in {"delta", "final", "done", "tool_call"} for kind, _ in events)
        saved = client.get("/api/chat/turns/protocol-evidence-turn").json()
        assert saved["status"] == "failed"
        assert claim not in json.dumps(saved, ensure_ascii=False)
        assert not dialogue.history
        assert len(llm.calls) == 2
