"""Multi-hop agent loop for the 「聊一聊」 chat.

Extends the single-hop tool flow of ``SocraticDialogue._respond_with_tools``
into a bounded think → call → observe loop. Each hop is one LLM completion;
tool calls are dispatched through a ``ToolRegistry`` (with JSON Schema
validation) and results are fed back until the model answers in text or the
step budget is exhausted.

The loop is an async generator of ``AgentEvent`` so the API layer (M2) can
stream thinking / tool_call / tool_result / final over SSE without waiting
for the whole run. With an approval gate wired (M7), hard_write calls are
intercepted into pending approvals instead of being executed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from openbiliclaw.llm.base import LLMResponseError

from .tools.registry import validate_tool_arguments

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from openbiliclaw.config import Config
    from openbiliclaw.llm.base import LLMResponse, LLMStreamChunk

    from .tools import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_AGENT_LOOP_MAX_STEPS = 64
DEFAULT_TOOL_RESULT_MAX_CHARS = 4000

AgentEventType = Literal[
    "thinking",
    "tool_call",
    "tool_result",
    "approval_request",
    "delta",
    "final",
    "step_limit_reached",
]

_STEP_LIMIT_WRAP_UP_INSTRUCTION = (
    "你已达到本次任务的步数上限，不能再调用任何工具。"
    "请根据目前已经获得的信息，直接向用户汇报：完成了什么、发现了什么、"
    "哪些还没做完，以及你建议的下一步。"
)

_APPROVAL_PENDING_FEEDBACK = (
    "此操作属于高风险写入（hard_write），必须经用户逐项审批，本次未执行。"
    "已生成待批准动作卡片（审批 ID: {approval_id}）。不要重复调用该工具；"
    "请直接继续回答用户，说明该动作已提交审批、等待用户在对话中批准，"
    "批准后系统会自动执行并反馈结果。"
)


class SupportsNativeToolCompletion(Protocol):
    """The slice of ``LLMService`` the agent loop depends on."""

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
    ) -> LLMResponse: ...

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
    ) -> AsyncIterator[LLMStreamChunk]: ...


class SupportsApprovalGate(Protocol):
    """The slice of ``ApprovalStore`` the agent loop depends on (M7).

    ``submit`` registers one pending approval and returns a record carrying
    an ``approval_id`` attribute. Implementations must never execute the
    tool — execution happens only via the approval endpoints.
    """

    def submit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        summary: str,
        reason: str = "",
        impact: str = "",
        session: str = "",
        session_id: str = "",
        turn_id: str = "",
    ) -> Any: ...


@dataclass(frozen=True)
class AgentEvent:
    """One streamed step of the agent loop.

    ``type`` discriminates the payload: ``thinking`` (per-hop assistant
    text), ``tool_call`` (name + arguments + summary), ``tool_result``
    (truncated result text + ok flag), ``approval_request`` (M7: a
    hard_write call was intercepted and parked as a pending approval,
    carrying ``approval_id`` + ``impact``), ``delta`` (an incremental token
    fragment of the current hop's assistant text, for live rendering only —
    the authoritative text still arrives in ``thinking`` / ``final``),
    ``final`` (the reply text) and ``step_limit_reached`` (emitted once
    before the wrap-up ``final``).
    """

    type: AgentEventType
    step: int = 0
    text: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    ok: bool = True
    truncated: bool = False
    approval_id: str = ""
    impact: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for SSE / JSON transport (M2 wires this into /api/chat)."""
        data: dict[str, Any] = {"type": self.type, "step": self.step}
        if self.text:
            data["text"] = self.text
        if self.tool_name:
            data["tool_name"] = self.tool_name
        if self.arguments:
            data["arguments"] = self.arguments
        if self.summary:
            data["summary"] = self.summary
        if self.approval_id:
            data["approval_id"] = self.approval_id
        if self.impact:
            data["impact"] = self.impact
        if self.type == "tool_result":
            data["ok"] = self.ok
            data["truncated"] = self.truncated
        return data


@dataclass(frozen=True)
class _NormalizedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    arguments_raw: str


class AgentLoop:
    """Bounded multi-hop tool-calling loop."""

    def __init__(
        self,
        llm: SupportsNativeToolCompletion,
        tools: ToolRegistry,
        *,
        max_steps: int = DEFAULT_AGENT_LOOP_MAX_STEPS,
        tool_result_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
        caller: str = "agent.loop",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        bypass_semaphore: bool = False,
        approval_gate: SupportsApprovalGate | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._max_steps = max(1, int(max_steps))
        self._tool_result_max_chars = max(200, int(tool_result_max_chars))
        self._caller = caller
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._bypass_semaphore = bypass_semaphore
        self._approval_gate = approval_gate

    @classmethod
    def from_config(
        cls,
        llm: SupportsNativeToolCompletion,
        tools: ToolRegistry,
        config: Config,
        **overrides: Any,
    ) -> AgentLoop:
        """Build a loop whose budgets come from the ``[agent]`` config section."""
        agent_config = getattr(config, "agent", None)
        max_steps = int(getattr(agent_config, "loop_max_steps", DEFAULT_AGENT_LOOP_MAX_STEPS))
        result_chars = int(
            getattr(agent_config, "tool_result_max_chars", DEFAULT_TOOL_RESULT_MAX_CHARS)
        )
        kwargs: dict[str, Any] = {
            "max_steps": max_steps,
            "tool_result_max_chars": result_chars,
        }
        kwargs.update(overrides)
        return cls(llm, tools, **kwargs)

    @property
    def max_steps(self) -> int:
        return self._max_steps

    async def run(
        self,
        *,
        system_instruction: str,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        tools: ToolRegistry | None = None,
        approval_context: Mapping[str, str] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run the loop, yielding one event per observable step.

        ``tools`` optionally overrides the registry for this run (e.g. a
        per-skill whitelist subset). LLM failures propagate to the caller;
        tool failures are fed back to the model as error results.

        M7 approval gate: when an ``approval_gate`` is wired, hard_write
        tool calls are NOT executed. Each one is submitted to the gate,
        streamed as an ``approval_request`` event, and answered to the
        model with an "awaiting approval" tool result so the turn can
        finish; the user then executes it via the approval endpoints.
        ``approval_context`` carries session/session_id/turn_id onto the
        approval record. Without a gate the legacy direct-execution
        behavior is preserved.
        """
        registry = tools if tools is not None else self._tools
        tool_schemas = registry.llm_schemas()
        approval_context = dict(approval_context or {})
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_instruction},
            *(dict(item) for item in (history or [])),
            {"role": "user", "content": user_message},
        ]

        step = 0
        while step < self._max_steps:
            step += 1
            terminal: list[LLMResponse] = []
            async for delta_event in self._stream_hop(
                messages, tool_schemas, step=step, terminal=terminal
            ):
                yield delta_event
            response = terminal[0]
            text = (response.content or "").strip()
            calls = self._normalize_tool_calls(response)
            if not calls:
                yield AgentEvent(type="final", step=step, text=text)
                return
            # Assistant text accompanying tool calls is intermediate
            # reasoning, not the reply — surface it as a thinking step.
            if text:
                yield AgentEvent(type="thinking", step=step, text=text)

            messages.append(self._assistant_message(text, calls))
            for call in calls:
                summary = _summarize_tool_call(call.name, call.arguments)
                yield AgentEvent(
                    type="tool_call",
                    step=step,
                    tool_name=call.name,
                    arguments=call.arguments,
                    summary=summary,
                )
                tool = registry.get(call.name)
                if (
                    tool is not None
                    and tool.permission_level == "hard_write"
                    and self._approval_gate is not None
                ):
                    errors = validate_tool_arguments(tool.parameters, call.arguments)
                    if errors:
                        content = "参数校验失败: " + "；".join(errors)
                        yield AgentEvent(
                            type="tool_result",
                            step=step,
                            tool_name=call.name,
                            text=content,
                            ok=False,
                        )
                        messages.append(
                            {"role": "tool", "tool_call_id": call.id, "content": content}
                        )
                        continue
                    content = ""
                    try:
                        record = self._approval_gate.submit(
                            tool_name=call.name,
                            arguments=call.arguments,
                            summary=summary,
                            reason=str(call.arguments.get("reason") or ""),
                            impact=tool.impact_hint,
                            session=str(approval_context.get("session", "")),
                            session_id=str(approval_context.get("session_id", "")),
                            turn_id=str(approval_context.get("turn_id", "")),
                        )
                        approval_id = str(getattr(record, "approval_id", "") or "")
                    except Exception as exc:
                        logger.exception("Approval gate submission failed: %s", call.name)
                        content = f"审批登记失败，操作未执行: {exc}"
                        yield AgentEvent(
                            type="tool_result",
                            step=step,
                            tool_name=call.name,
                            text=content,
                            ok=False,
                        )
                    else:
                        yield AgentEvent(
                            type="approval_request",
                            step=step,
                            tool_name=call.name,
                            arguments=call.arguments,
                            summary=summary,
                            approval_id=approval_id,
                            impact=tool.impact_hint,
                        )
                        content = _APPROVAL_PENDING_FEEDBACK.format(approval_id=approval_id)
                        yield AgentEvent(
                            type="tool_result",
                            step=step,
                            tool_name=call.name,
                            text=content,
                            ok=True,
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": content,
                        }
                    )
                    continue
                result = await registry.dispatch(call.name, call.arguments)
                content, truncated = self._truncate_result(result.content)
                if not result.ok:
                    logger.info(
                        "Agent loop tool failure (%s): %s -> %s",
                        result.error,
                        call.name,
                        content,
                    )
                yield AgentEvent(
                    type="tool_result",
                    step=step,
                    tool_name=call.name,
                    text=content,
                    ok=result.ok,
                    truncated=truncated,
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": content,
                    }
                )

        yield AgentEvent(
            type="step_limit_reached",
            step=self._max_steps,
            text=f"已达到本次任务的步数上限（{self._max_steps} 跳）。",
        )
        messages.append({"role": "user", "content": _STEP_LIMIT_WRAP_UP_INSTRUCTION})
        try:
            terminal = []
            async for delta_event in self._stream_hop(
                messages, [], step=self._max_steps, terminal=terminal
            ):
                yield delta_event
            wrap_up_text = (terminal[0].content or "").strip()
        except Exception:
            logger.exception("Agent loop wrap-up completion failed.")
            wrap_up_text = ""
        if not wrap_up_text:
            wrap_up_text = (
                "这次任务已经跑到了步数上限，我先把目前的进展同步给你："
                "上面的每一步工具调用和结果都在对话里，"
                "还没完成的部分建议你告诉我继续，我会接着做。"
            )
        yield AgentEvent(type="final", step=self._max_steps, text=wrap_up_text)

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        return await self._llm.complete_with_native_tools(
            messages=messages,
            tools=tools,
            caller=self._caller,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            bypass_semaphore=self._bypass_semaphore,
        )

    async def _stream_hop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        step: int,
        terminal: list[LLMResponse],
    ) -> AsyncIterator[AgentEvent]:
        """Run one loop hop, yielding ``delta`` events as content streams in.

        The aggregated response is deposited into ``terminal`` (one item);
        ``tool_calls`` deltas never surface as events — only visible content
        text does. Services without ``stream_complete_with_native_tools``
        (older duck-typed doubles) fall back to the one-shot ``_complete``.
        """
        stream_fn = getattr(self._llm, "stream_complete_with_native_tools", None)
        if not callable(stream_fn):
            terminal.append(await self._complete(messages, tools))
            return
        async for chunk in stream_fn(
            messages=messages,
            tools=tools,
            caller=self._caller,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            bypass_semaphore=self._bypass_semaphore,
        ):
            if chunk.delta:
                yield AgentEvent(type="delta", step=step, text=chunk.delta)
            if chunk.response is not None:
                terminal.append(chunk.response)
        if not terminal:
            raise LLMResponseError("Streaming hop ended without a terminal response.")

    @staticmethod
    def _normalize_tool_calls(response: LLMResponse) -> list[_NormalizedToolCall]:
        calls: list[_NormalizedToolCall] = []
        for index, raw in enumerate(response.tool_calls or []):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            arguments = raw.get("arguments")
            calls.append(
                _NormalizedToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=name,
                    arguments=arguments if isinstance(arguments, dict) else {},
                    arguments_raw=str(raw.get("arguments_raw") or ""),
                )
            )
        return calls

    @staticmethod
    def _assistant_message(
        text: str,
        calls: list[_NormalizedToolCall],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.arguments_raw
                        or json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    def _truncate_result(self, content: str) -> tuple[str, bool]:
        if len(content) <= self._tool_result_max_chars:
            return content, False
        return content[: self._tool_result_max_chars] + "…（结果已截断）", True


def _summarize_tool_call(name: str, arguments: dict[str, Any], *, max_chars: int = 120) -> str:
    """One-line human summary of a tool call for the collapsed step view."""
    if arguments:
        args_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        summary = f"{name}({args_text})"
    else:
        summary = f"{name}()"
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1] + "…"
    return summary
