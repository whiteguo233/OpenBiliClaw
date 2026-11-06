"""Tool examples must remain data through the free provider's text protocol."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMResponse, LLMStreamChunk
from openbiliclaw.llm.service import _parse_simulated_tool_calls
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider

CALL = {"tool_call": {"name": "get_profile", "arguments": {}}}
LITERAL = json.dumps(CALL)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("wire", "expected", "executes"),
    [
        (json.dumps({"reply": LITERAL}), LITERAL, False),
        (json.dumps({**CALL, "description": "示例数据"}), None, False),
        (json.dumps({"reply": LITERAL, "other": 1}), None, False),
        (LITERAL, "", True),
    ],
)
async def test_free_provider_keeps_literal_reply_separate_from_calls(
    streaming: bool, wire: str, expected: str | None, executes: bool
) -> None:
    class RecordedReply(SponsoredProvider):
        async def _ensure_started(self) -> None:
            self._streaming = True

        async def _execute_task_once(self, **kwargs: Any) -> LLMResponse:
            return LLMResponse(content=wire)

        async def _stream_task_once(self, contract: Any, payload: Any) -> Any:
            for offset in range(0, len(wire), 3):
                yield LLMStreamChunk(delta=wire[offset : offset + 3])
            yield LLMStreamChunk(response=LLMResponse(content=wire))

    sdk = RecordedReply(["unused"], registry=approved_registry())
    tools = [{"type": "function", "function": {"name": "get_profile", "parameters": {}}}]
    messages = [{"role": "user", "content": "请原样给出 JSON 示例，不调用工具。"}]
    visible = ""
    try:
        if streaming:
            response = None
            async for chunk in sdk.stream_complete_with_tools(messages, tools, caller="agent.chat"):
                visible += chunk.delta
                response = chunk.response or response
            assert response is not None
        else:
            response = await sdk.complete_with_tools(messages, tools, caller="agent.chat")
        assert bool(response.tool_calls) is executes
        assert response.content == (wire if expected is None else expected)
        if streaming:
            assert visible == response.content
    finally:
        await sdk.aclose()


def test_simulated_calls_reject_objects_with_literal_answer_metadata() -> None:
    assert (
        _parse_simulated_tool_calls(json.dumps({**CALL, "reply": "示例"}), ["get_profile"]) is None
    )


def test_runtime_cause_lookup_handles_context_cycles_and_unrelated_messages() -> None:
    from openbiliclaw.llm.sponsored_errors import SponsoredAuthError, find_sponsored_error

    inner = SponsoredAuthError()
    outer = RuntimeError("wrapper")
    outer.__context__ = inner
    assert find_sponsored_error(outer) is inner
    outer.__context__ = outer
    assert find_sponsored_error(outer) is None
    assert find_sponsored_error(RuntimeError("UNAVAILABLE / AUTH_FAILED")) is None
