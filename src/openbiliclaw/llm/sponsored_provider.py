"""IPC client for the closed-source ``obc-sponsored-runtime`` child process.

The provider speaks a deliberately narrow protocol: spawn the runtime, say
hello, execute one contract-validated task at a time, and map runtime error
codes to typed exceptions. It never sees the sponsored API key and never
opens a TCP port of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shlex
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from openbiliclaw import __version__
from openbiliclaw.proc import no_window_kwargs
from openbiliclaw.sponsored_runtime_state import (
    DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS,
    SPONSORED_EMBEDDING_TIMEOUT_SECONDS,
)

from .base import LLMResponse, LLMStreamChunk
from .concurrency import is_interactive_caller
from .sponsored_errors import (
    SponsoredAuthError,
    SponsoredContractMismatchError,
    SponsoredError,
    SponsoredPolicyInvalidError,
    SponsoredProtocolError,
    SponsoredRequestTooLargeError,
    SponsoredStartupBusyError,
    SponsoredUnavailableError,
    SponsoredUnsupportedTaskError,
    error_from_payload,
    is_free_model_fallback_allowed,
)
from .sponsored_protocol import (
    PROTOCOL_VERSION,
    encode_frame,
    read_frame_async,
    write_frame_async,
)
from .sponsored_tasks import DEFAULT_SPONSORED_REGISTRY

if TYPE_CHECKING:
    from .sponsored_contracts import SponsoredContractRegistry, SponsoredTaskContract

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_SECONDS = DEFAULT_SPONSORED_REQUEST_TIMEOUT_SECONDS
DEFAULT_HANDSHAKE_TIMEOUT_SECONDS = 10.0
# Nov 8: 30s rejected the already-observed 27–85s cold first responses.
# Allow queued/cold chat five minutes within the configured overall deadline.
# Explicit thinking keeps the full request budget. Recheck after model swaps.
INTERACTIVE_FIRST_DELTA_TIMEOUT_SECONDS = 300.0
_STDERR_TAIL_BYTES = 4096

SPONSORED_ENABLED_ENV = "OPENBILICLAW_SPONSORED_ENABLED"
SPONSORED_RUNTIME_ENV = "OPENBILICLAW_SPONSORED_RUNTIME"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class SponsoredProvider:
    """Execute Sponsored tasks through a policy-validating local runtime.

    One provider instance owns one long-lived child process. Requests are
    serialized per process; callers that need concurrency should run several
    provider instances, each with its own child.
    """

    def __init__(
        self,
        runtime_command: Sequence[str],
        *,
        registry: SponsoredContractRegistry = DEFAULT_SPONSORED_REGISTRY,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
        model: str | None = None,
        fallback_to_other_free_model: bool = True,
    ) -> None:
        if not runtime_command:
            raise ValueError("runtime_command must not be empty")
        from .sponsored_profiles import CHAT_MODELS

        if model is not None and model not in CHAT_MODELS:
            raise ValueError("unsupported free chat model")
        if any(not math.isfinite(t) or t <= 0 for t in (request_timeout, handshake_timeout)):
            raise ValueError("timeouts must be positive")
        self._runtime_command = tuple(str(part) for part in runtime_command)
        self._registry = registry
        self._chat_model = model
        self._free_fallback = fallback_to_other_free_model
        self._streaming = False
        self._request_timeouts = False
        self._request_timeout = float(request_timeout)
        self._handshake_timeout = float(handshake_timeout)
        self._extra_env = dict(env) if env is not None else None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._start_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._policy_version = 0

    @property
    def policy_version(self) -> int:
        return self._policy_version

    @property
    def runtime_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def _operation_timeout(self, operation: str) -> float:
        """Keep embedding latency bounded independently of long text jobs."""
        return (
            min(self._request_timeout, SPONSORED_EMBEDDING_TIMEOUT_SECONDS)
            if operation.startswith("embedding_")
            else self._request_timeout
        )

    def _transport_budget(self, seconds: float) -> dict[str, int]:
        # Old signed SDKs reject unknown frame fields; negotiate the extension.
        return (
            {"timeout_ms": max(1, min(3_600_000, int(seconds * 1000)))}
            if self._request_timeouts
            else {}
        )

    async def execute_task(
        self,
        *,
        caller: str = "",
        task_id: str = "",
        contract_version: int | None = None,
        user_payload: Mapping[str, Any],
        user_text: str | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Choose only signed model variants; never fall through to a paid provider."""
        contracts = self._candidate_contracts(caller, task_id, contract_version)
        for index, contract in enumerate(contracts):
            try:
                payload = self._bounded_operation_payload(contract, user_payload)
                self._check_context(contract, payload)
                response = await self._execute_task_once(
                    task_id=contract.task_id,
                    contract_version=contract.contract_version,
                    user_payload=payload,
                    user_text=user_text,
                    system_prompt=system_prompt,
                )
                response.raw = dict(response.raw or {})
                response.raw["requested_model"] = contracts[0].model
                response.raw["free_model_fallback"] = index > 0
                return response
            except SponsoredError as exc:
                if index + 1 == len(contracts) or not is_free_model_fallback_allowed(exc):
                    raise
                logger.warning("free model fallback: %s (%s)", contract.model, exc.code)
        raise SponsoredUnavailableError("no signed free model is available")

    @property
    def supports_image_input(self) -> bool:
        return self._registry.get("llm.vision") is not None

    def _candidate_contracts(
        self, caller: str, task_id: str, version: int | None
    ) -> list[SponsoredTaskContract]:
        from .sponsored_profiles import ALTERNATE_CHAT_MODEL, ALTERNATE_TASK_SUFFIX

        contract = self._resolve_contract(caller=caller, task_id=task_id, contract_version=version)
        alternate = self._registry.get(contract.task_id + ALTERNATE_TASK_SUFFIX)
        if alternate is None:
            return [contract]
        candidates = (
            [alternate, contract]
            if self._chat_model == ALTERNATE_CHAT_MODEL
            else [contract, alternate]
        )
        return candidates if self._free_fallback else candidates[:1]

    @staticmethod
    def _bounded_operation_payload(
        contract: SponsoredTaskContract, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Keep implicit free-model thinking inside the normal task deadline.

        Live full-profile calls exceeded 180s with the upstream's unspecified
        thinking default. 8B structured tasks retain a 1024-token reasoning
        budget when the output allowance leaves room for an answer; shorter
        requests, 4B and conversational/tool replies default to no thinking.
        Explicit caller choices, including high/max and disabled, stay intact.
        Resolve after choosing the contract so free fallback uses its own model.
        """
        from .sponsored_profiles import CHAT_MODELS, DEFAULT_CHAT_MODEL

        if (
            contract.model not in CHAT_MODELS
            or contract.operation not in {"structured", "text", "tools", "vision"}
            or payload.get("reasoning_effort") is not None
        ):
            return payload
        max_tokens = payload.get("max_tokens", 0)
        effort = (
            "low"
            if (
                contract.model == DEFAULT_CHAT_MODEL
                and contract.operation == "structured"
                and isinstance(max_tokens, int)
                and max_tokens >= 2048
            )
            else ""
        )
        return {**payload, "reasoning_effort": effort}

    @staticmethod
    def _check_context(contract: SponsoredTaskContract, payload: Mapping[str, Any]) -> None:
        from .sponsored_profiles import CONTEXT_WINDOWS, EMBEDDING_MODEL, context_input_bound

        window = CONTEXT_WINDOWS.get(contract.model)
        if window is not None:
            output = int(payload.get("max_tokens", contract.max_output_tokens))
            bound = context_input_bound(payload, vision=contract.operation == "vision")
            if bound + output > window:
                raise SponsoredRequestTooLargeError(
                    f"{contract.model} context budget exceeded; split the task or shorten history",
                    task_id=contract.task_id,
                )
        elif (
            contract.model == EMBEDDING_MODEL
            and len(str(payload.get("text", "")).encode("utf-8")) > 8192
        ):
            raise SponsoredRequestTooLargeError("BGE-M3 input exceeds the conservative text budget")

    async def _execute_task_once(
        self,
        *,
        caller: str = "",
        task_id: str = "",
        contract_version: int | None = None,
        user_payload: Mapping[str, Any],
        user_text: str | None = None,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """Run one Sponsored task and return the standardized response.

        Fixed v2 tasks send only structured input. Optional user_text and
        system_prompt are local drift checks against the legacy builder;
        neither can override the runtime's signed/fixed renderer.
        """

        contract = self._resolve_contract(
            caller=caller,
            task_id=task_id,
            contract_version=contract_version,
        )
        if system_prompt is not None and system_prompt != contract.system_prompt:
            raise SponsoredContractMismatchError(
                "system prompt does not match the sponsored contract",
                task_id=contract.task_id,
            )
        payload_text = self._serialize_payload(user_payload, contract, user_text)

        async with self._observe_task(contract), self._request_lock:
            await self._ensure_started()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise SponsoredUnavailableError("runtime pipes are unavailable")

            request_id = uuid.uuid4().hex
            request = {
                "type": "execute",
                "id": request_id,
                "task_id": contract.task_id,
                "contract_version": contract.contract_version,
                "input": json.loads(payload_text),
                **self._transport_budget(self._operation_timeout(contract.operation)),
            }

            try:
                await write_frame_async(process.stdin, request)
            except (SponsoredError, OSError) as exc:
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "failed to write to sponsored runtime",
                    task_id=contract.task_id,
                ) from exc

            try:
                response = await asyncio.wait_for(
                    read_frame_async(process.stdout),
                    self._operation_timeout(contract.operation),
                )
            except TimeoutError as exc:
                self._cancel_in_flight(process, request_id)
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "sponsored runtime timed out",
                    task_id=contract.task_id,
                ) from exc
            except SponsoredError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError(
                    "sponsored runtime IPC failed",
                    task_id=contract.task_id,
                ) from exc
            except asyncio.CancelledError:
                self._abort_process_sync(process)
                await self._stop_process()
                raise

            if response.get("id") != request_id:
                await self._stop_process()
                raise SponsoredProtocolError(
                    "sponsored runtime returned a mismatched response id",
                    task_id=contract.task_id,
                )
            if response.get("ok") is not True:
                error_payload = response.get("error")
                if not isinstance(error_payload, Mapping):
                    error_payload = {}
                raise error_from_payload(error_payload, task_id=contract.task_id)

            return LLMResponse(
                content=str(response.get("content") or ""),
                model=str(response.get("model") or contract.model),
                instance_id="sponsored",
                provider="sponsored",
                usage=_coerce_usage(response.get("usage")),
                raw=response,
            )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        caller: str = "",
    ) -> LLMResponse:
        """Use fixed SDK operations while preserving the application's full messages."""
        if json_mode and messages and messages[0].get("role") == "system":
            from .prompt_contracts import ensure_json_mode_contract

            messages = [dict(m) for m in messages]
            if isinstance(messages[0].get("content"), str):
                messages[0]["content"] = ensure_json_mode_contract(messages[0]["content"])
        vision = any(
            isinstance(m.get("content"), list)
            and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        operation = (
            "vision"
            if vision
            else "tools"
            if caller.endswith(".tools")
            else "structured"
            if json_mode
            else "text"
        )
        response = await self.execute_task(
            task_id=f"llm.{operation}",
            user_payload={
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
                "caller": caller,
            },
        )
        if json_mode:
            # The signed upstream format permits arrays. Some free replies
            # still add a whole Markdown fence (Nov 5 live search repro). Strip
            # only a complete, strictly valid container; never repair its data.
            fenced = re.fullmatch(
                r"\s*```(?:json)?\s*\n(.*?)\n```\s*",
                response.content,
                flags=re.DOTALL | re.IGNORECASE,
            )
            if fenced:
                body = fenced.group(1).strip()
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(parsed, (dict, list)):
                        response.content = body
        return response

    async def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        caller: str = "",
    ) -> LLMResponse:
        """Adapt the project's tool interface using Qwen's tagged text contract.

        The upstream sponsor model need not support native HTTP tool fields.
        Tool schemas and results remain prompt data; only Core executes tools.
        """
        from .sponsored_tools import parse_tool_calls, split_tool_content

        canonical, names = self._tool_messages(messages, tools)
        payload = {
            "messages": canonical,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "caller": caller,
        }
        response = await self.execute_task(task_id="llm.tools", user_payload=payload)
        response = await self._repair_tool_json(response, payload, names)
        calls = parse_tool_calls(response.content, names)
        if calls:
            response.tool_calls = calls
            response.content = split_tool_content(response.content)[0]
        return response

    async def _repair_tool_json(
        self, response: LLMResponse, payload: Mapping[str, Any], names: list[str]
    ) -> LLMResponse:
        """Correct one marked syntax error; literal JSON is never an operation."""
        from .sponsored_tools import InvalidToolActionError, parse_tool_calls, split_tool_content

        if not names or not split_tool_content(response.content)[1]:
            return response
        try:
            parse_tool_calls(response.content, names)
        except InvalidToolActionError:
            # Valid JSON naming unknown tools or changing argument types is not
            # a formatting error; never ask the model to guess another action.
            raise
        except SponsoredProtocolError:
            pass
        else:
            return response
        repaired = await self.execute_task(
            task_id="llm.tools",
            user_payload={
                **payload,
                "messages": [
                    *payload["messages"],
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": (
                            "上一条工具标记或 JSON 语法无效，工具尚未执行。"
                            "只修正格式，保留原工具名和参数含义，不得添加新操作。"
                            '每个调用使用 <tool_call>{"name":"工具名","arguments":{}}</tool_call>，'
                            "不要解释、代码围栏或 reply 封套。"
                        ),
                    },
                ],
                "temperature": 0.0,
            },
        )
        if not parse_tool_calls(repaired.content, names):
            raise SponsoredProtocolError("invalid tool JSON after one format correction")
        first_usage, next_usage = response.usage or {}, repaired.usage or {}
        repaired.usage = {
            key: first_usage.get(key, 0) + next_usage.get(key, 0)
            for key in first_usage.keys() | next_usage.keys()
        }
        repaired.raw = {**(repaired.raw or {}), "tool_format_repaired": True}
        return repaired

    @staticmethod
    def _tool_messages(
        messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[list[dict[str, str]], list[str]]:
        from .sponsored_tools import tool_messages

        return tool_messages(messages, tools)

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Forward Rust SSE deltas; never retry after emitting any answer text."""
        await self._ensure_started()
        if not self._streaming:
            response = await self.complete(messages, **kwargs)
            if response.content:
                yield LLMStreamChunk(delta=response.content)
            yield LLMStreamChunk(response=response)
            return
        if kwargs.get("json_mode") and messages and messages[0].get("role") == "system":
            from .prompt_contracts import ensure_json_mode_contract

            messages = [dict(m) for m in messages]
            if isinstance(messages[0].get("content"), str):
                messages[0]["content"] = ensure_json_mode_contract(messages[0]["content"])
        vision = any(
            isinstance(m.get("content"), list)
            and any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        operation = "vision" if vision else "structured" if kwargs.get("json_mode") else "text"
        payload = {
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "reasoning_effort": kwargs.get("reasoning_effort"),
            "caller": kwargs.get("caller", ""),
        }
        stream = self._stream_operation(operation, payload)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    async def _stream_operation(
        self, operation: str, payload: Mapping[str, Any]
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        # One first-output budget for the logical request, including backoff
        # and same/free-model retries. A per-attempt timer can double the wait.
        deadline = None
        if is_interactive_caller(str(payload.get("caller") or "")) and payload.get(
            "reasoning_effort"
        ) in {None, ""}:
            deadline = asyncio.get_running_loop().time() + min(
                self._request_timeout, INTERACTIVE_FIRST_DELTA_TIMEOUT_SECONDS
            )
        stream = self._stream_operation_attempts(operation, payload, first_deadline=deadline)
        try:
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        chunk = await anext(stream)
                except StopAsyncIteration:
                    return
                if chunk.delta.strip() or chunk.response is not None:
                    deadline = None
                yield chunk
        except TimeoutError as exc:
            raise SponsoredUnavailableError("sponsored first output timed out") from exc
        finally:
            await stream.aclose()

    async def _stream_operation_attempts(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        first_deadline: float | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        contracts = self._candidate_contracts("", f"llm.{operation}", None)
        emitted = False
        attempts = 2 if is_interactive_caller(str(payload.get("caller") or "")) else 1
        for index, contract in enumerate(contracts):
            # Same-model retries share this slice. Reserve an equal portion
            # for each remaining candidate, within the original total budget.
            model_deadline = None
            if first_deadline is not None:
                now = asyncio.get_running_loop().time()
                model_deadline = now + (first_deadline - now) / (len(contracts) - index)
            for attempt in range(attempts):
                try:
                    bounded = self._bounded_operation_payload(contract, payload)
                    self._check_context(contract, bounded)
                    stream = self._stream_task_once(contract, bounded)
                    try:
                        while True:
                            try:
                                async with asyncio.timeout_at(model_deadline):
                                    chunk = await anext(stream)
                            except StopAsyncIteration:
                                break
                            except TimeoutError as exc:
                                raise SponsoredUnavailableError(
                                    "sponsored first output timed out", task_id=contract.task_id
                                ) from exc
                            emitted = emitted or bool(chunk.delta) or chunk.response is not None
                            if chunk.delta.strip() or chunk.response is not None:
                                model_deadline = None
                            if chunk.response is not None:
                                chunk.response.raw = dict(chunk.response.raw or {})
                                chunk.response.raw.update(
                                    requested_model=contracts[0].model,
                                    free_model_fallback=index > 0,
                                )
                            yield chunk
                    finally:
                        await stream.aclose()
                    return
                except SponsoredError as exc:
                    if emitted or not is_free_model_fallback_allowed(exc):
                        raise
                    remaining = (
                        model_deadline - asyncio.get_running_loop().time()
                        if model_deadline is not None
                        else self._request_timeout
                    )
                    if (
                        attempt + 1 < attempts
                        and exc.code in {"UPSTREAM_ERROR", "UNAVAILABLE", "RATE_LIMITED"}
                        and remaining > 0
                    ):
                        logger.warning("Retrying free stream before any output: %s", exc.code)
                        await asyncio.sleep(min(0.25, remaining))
                        continue
                    if index + 1 == len(contracts):
                        raise
                    logger.warning("free stream fallback: %s (%s)", contract.model, exc.code)
                    break

    async def _stream_task_once(
        self, contract: SponsoredTaskContract, payload: Mapping[str, Any]
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        payload_text = self._serialize_payload(payload, contract, None)
        async with self._observe_task(contract), self._request_lock:
            await self._ensure_started()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise SponsoredUnavailableError("runtime pipes are unavailable")
            request_id = uuid.uuid4().hex
            try:
                async with asyncio.timeout(self._operation_timeout(contract.operation)):
                    await write_frame_async(
                        process.stdin,
                        {
                            "type": "execute_stream",
                            "id": request_id,
                            "task_id": contract.task_id,
                            "contract_version": contract.contract_version,
                            "input": json.loads(payload_text),
                            **self._transport_budget(self._operation_timeout(contract.operation)),
                        },
                    )
                    while True:
                        frame = await read_frame_async(process.stdout)
                        if frame.get("id") != request_id:
                            raise SponsoredProtocolError("mismatched stream response id")
                        if frame.get("type") == "delta":
                            delta = frame.get("content")
                            if not isinstance(delta, str):
                                raise SponsoredProtocolError("invalid stream delta")
                            yield LLMStreamChunk(delta=delta)
                            continue
                        if frame.get("type") != "result":
                            raise SponsoredProtocolError("invalid stream terminal")
                        if frame.get("ok") is not True:
                            error = frame.get("error")
                            if not isinstance(error, Mapping):
                                raise SponsoredProtocolError("invalid stream error")
                            raise error_from_payload(error, task_id=contract.task_id)
                        yield LLMStreamChunk(
                            response=LLMResponse(
                                content=str(frame.get("content") or ""),
                                model=str(frame.get("model") or contract.model),
                                instance_id="sponsored",
                                provider="sponsored",
                                usage=_coerce_usage(frame.get("usage")),
                                raw=frame,
                            )
                        )
                        return
            except TimeoutError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError("sponsored stream timed out") from exc
            except (asyncio.CancelledError, GeneratorExit):
                self._abort_process_sync(process)
                await self._stop_process()
                raise
            except (SponsoredError, OSError) as exc:
                await self._stop_process()
                if isinstance(exc, SponsoredError):
                    raise
                raise SponsoredUnavailableError("sponsored stream transport failed") from exc

    async def stream_complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream ordinary text/JSON while hiding a marked action suffix.

        Whole marked actions are parsed only after the terminal response.
        Core owns tool execution; transport never replays a started stream.
        """
        from .sponsored_tools import ToolStreamPrefix, parse_tool_calls, split_tool_content

        await self._ensure_started()
        if not self._streaming:
            response = await self.complete_with_tools(messages, tools, **kwargs)
            if response.content:
                yield LLMStreamChunk(delta=response.content)
            yield LLMStreamChunk(response=response)
            return
        canonical, names = self._tool_messages(messages, tools)
        payload = {
            "messages": canonical,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "reasoning_effort": kwargs.get("reasoning_effort"),
            "caller": kwargs.get("caller", ""),
        }
        prefix = ToolStreamPrefix()
        visible_emitted = False
        stream = self._stream_operation("tools", payload)
        try:
            async for chunk in stream:
                if chunk.delta:
                    visible = prefix.feed(chunk.delta)
                    if visible:
                        visible_emitted = True
                        yield LLMStreamChunk(delta=visible)
                if chunk.response is not None:
                    response = chunk.response
                    if not visible_emitted:
                        # The terminal frame still owns the serial child's
                        # request lock until this generator is closed.
                        await stream.aclose()
                        response = await self._repair_tool_json(response, payload, names)
                    calls = parse_tool_calls(response.content, names)
                    if calls:
                        response.tool_calls = calls
                        response.content = split_tool_content(response.content)[0]
                    else:
                        pending = prefix.finish()
                        if pending:
                            yield LLMStreamChunk(delta=pending)
                    yield LLMStreamChunk(response=response)
        finally:
            await stream.aclose()

    async def embedding(self, value: str, *, image: bool = False) -> list[float]:
        """Return a validated vector in the signed model's fixed embedding space."""
        import math

        task_id = "llm.embedding_image" if image else "llm.embedding_text"
        contract = self._resolve_contract(caller="", task_id=task_id, contract_version=3)
        response = await self.execute_task(
            task_id=task_id,
            user_payload={"image" if image else "text": value},
        )
        raw = response.raw or {}
        vector = raw.get("embedding")
        if (
            response.model != contract.model
            or contract.embedding_dimensions <= 0
            or not isinstance(vector, list)
            or len(vector) != contract.embedding_dimensions
            or any(
                isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
                for x in vector
            )
            or not any(vector)
        ):
            raise SponsoredProtocolError("invalid embedding model/dimension/vector")
        return [float(x) for x in vector]

    async def aclose(self) -> None:
        """Terminate the runtime child and release IPC resources."""

        async with self._request_lock:
            await self._stop_process()

    async def __aenter__(self) -> SponsoredProvider:
        await self._ensure_started()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _resolve_contract(
        self,
        *,
        caller: str,
        task_id: str,
        contract_version: int | None,
    ) -> SponsoredTaskContract:
        by_task = self._registry.get(task_id) if task_id else None
        by_caller = self._registry.resolve_caller(caller) if caller else None
        if (
            (task_id and by_task is None)
            or (caller and by_caller is None)
            or (by_task is None and by_caller is None)
        ):
            raise SponsoredUnsupportedTaskError(
                "task is not present in the sponsored policy",
                task_id=task_id or caller,
            )
        if by_task is not None and by_caller is not None and by_task is not by_caller:
            raise SponsoredContractMismatchError(
                "caller and task_id resolve to different contracts",
                task_id=task_id,
            )
        contract = by_task or by_caller
        assert contract is not None
        if contract_version is not None and contract_version != contract.contract_version:
            raise SponsoredUnsupportedTaskError(
                f"contract version {contract_version} is not supported",
                task_id=contract.task_id,
            )
        return contract

    @staticmethod
    def _serialize_payload(
        payload: Mapping[str, Any],
        contract: SponsoredTaskContract,
        user_text: str | None = None,
    ) -> str:
        from .consolidation_prompt import render_consolidation_user
        from .sponsored_tasks import validate_task_input

        if contract.operation != "consolidation":
            from .sponsored_operations import validate_operation_input

            try:
                data = dict(payload)
                text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(text.encode()) > contract.max_input_bytes:
                    raise SponsoredRequestTooLargeError("SDK input exceeds signed ceiling")
                validate_operation_input(
                    data, contract.operation, max_tokens=contract.max_output_tokens
                )
                return text
            except (TypeError, ValueError, KeyError) as exc:
                raise SponsoredContractMismatchError("invalid fixed SDK operation input") from exc
        if (
            contract.task_id not in {"soul.consolidation", "soul.consolidation.qwen35_4b"}
            or contract.contract_version != 2
        ):
            raise SponsoredUnsupportedTaskError(
                "unsupported fixed task/version", task_id=contract.task_id
            )
        try:
            data = dict(payload)
            text = json.dumps(
                data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
            )
            if len(text.encode("utf-8")) > contract.max_input_bytes:
                raise SponsoredRequestTooLargeError(
                    "task input exceeds budget", task_id=contract.task_id
                )
            # Check the exact JSON representation that crosses the IPC boundary.
            data = json.loads(text)
            validate_task_input(data)
            canonical_user = render_consolidation_user(**data)
            if len(canonical_user.encode("utf-8")) > contract.max_input_bytes:
                raise SponsoredRequestTooLargeError(
                    "rendered input exceeds budget", task_id=contract.task_id
                )
            if user_text is not None and user_text != canonical_user:
                raise ValueError("caller text differs from the fixed task renderer")
        except (TypeError, ValueError, KeyError) as exc:
            raise SponsoredContractMismatchError(
                "invalid fixed task input", task_id=contract.task_id
            ) from exc
        if len(text.encode("utf-8")) > contract.max_input_bytes:
            raise SponsoredRequestTooLargeError(
                "sponsored user payload exceeds the contract input budget",
                task_id=contract.task_id,
            )
        return text

    @asynccontextmanager
    async def _observe_task(self, contract: SponsoredTaskContract) -> AsyncGenerator[None, None]:
        from openbiliclaw.sponsored_status import record_sponsored_outcome

        capability = "embedding" if contract.operation == "embedding_text" else "chat"
        try:
            yield
        except SponsoredError as exc:
            record_sponsored_outcome(self._runtime_command, capability=capability, code=exc.code)
            raise
        else:
            record_sponsored_outcome(self._runtime_command, capability=capability)

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self.runtime_running:
                return
            await self._stop_process()
            self._stderr_tail.clear()
            try:
                process = await asyncio.create_subprocess_exec(
                    *self._runtime_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._process_env(),
                    **no_window_kwargs(),
                )
            except (OSError, ValueError) as exc:
                raise SponsoredUnavailableError("sponsored runtime is not available") from exc
            self._process = process
            if process.stderr is not None:
                self._stderr_task = asyncio.create_task(self._drain_stderr(process.stderr))
            try:
                await self._handshake(process)
            except (SponsoredAuthError, SponsoredPolicyInvalidError) as exc:
                self._record_startup_failure(exc)
                await self._stop_process()
                raise
            except (SponsoredUnavailableError, SponsoredContractMismatchError):
                await self._stop_process()
                raise
            except SponsoredError as exc:
                await self._stop_process()
                if b"another runtime owns this installation ledger" in self._stderr_tail:
                    raise SponsoredStartupBusyError("sponsored ledger startup is busy") from exc
                # Older signed runtimes report an expired policy before IPC hello.
                # Match their stable code/prefix, never classify generic startup failures as expiry.
                if re.search(
                    rb"(?m)^obc-sponsored-runtime: (?:ipc error: )?POLICY_INVALID:",
                    self._stderr_tail,
                ):
                    policy_error = error_from_payload({"code": "POLICY_INVALID"})
                    self._record_startup_failure(policy_error)
                    raise policy_error from exc
                raise SponsoredUnavailableError("sponsored runtime handshake failed") from exc
            except TimeoutError as exc:
                await self._stop_process()
                raise SponsoredUnavailableError("sponsored runtime handshake timed out") from exc

    def _record_startup_failure(self, error: SponsoredError) -> None:
        from openbiliclaw.sponsored_status import record_sponsored_outcome

        for capability in ("chat", "embedding"):
            record_sponsored_outcome(self._runtime_command, capability=capability, code=error.code)

    async def _handshake(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.stdout is None:
            raise SponsoredUnavailableError("runtime pipes are unavailable")
        await write_frame_async(
            process.stdin,
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "core": __version__,
            },
        )
        hello = await asyncio.wait_for(
            read_frame_async(process.stdout),
            self._handshake_timeout,
        )
        if hello.get("ok") is False and isinstance(hello.get("error"), Mapping):
            raise error_from_payload(hello["error"])
        if (
            hello.get("type") != "hello"
            or hello.get("protocol") != PROTOCOL_VERSION
            or hello.get("ok") is not True
        ):
            raise SponsoredUnavailableError(
                "runtime handshake was rejected (protocol/policy mismatch)"
            )
        contracts = hello.get("contracts")
        self._streaming = hello.get("streaming") is True
        self._request_timeouts = hello.get("request_timeouts") is True
        if not isinstance(contracts, Mapping) or any(
            contracts.get(contract.policy_key) != contract.system_prompt_sha256
            for contract in self._registry
        ):
            raise SponsoredContractMismatchError(
                "runtime policy does not match Core prompt contracts"
            )
        try:
            self._policy_version = int(hello.get("policy_version") or 0)
        except (TypeError, ValueError):
            self._policy_version = 0

    async def _stop_process(self) -> None:
        process, self._process = self._process, None
        stderr_task, self._stderr_task = self._stderr_task, None
        if process is not None:
            if process.stdin is not None:
                with suppress(Exception):
                    process.stdin.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except (TimeoutError, ProcessLookupError):
                    with suppress(ProcessLookupError):
                        process.kill()
                    with suppress(Exception):
                        await process.wait()
        if stderr_task is not None:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > _STDERR_TAIL_BYTES:
                    del self._stderr_tail[: len(self._stderr_tail) - _STDERR_TAIL_BYTES]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive pipe cleanup
            logger.debug("sponsored runtime stderr drain stopped", exc_info=True)

    def _cancel_in_flight(
        self,
        process: asyncio.subprocess.Process,
        request_id: str,
    ) -> None:
        if process.stdin is None or process.stdin.is_closing():
            return
        with suppress(Exception):
            process.stdin.write(encode_frame({"type": "cancel", "id": request_id}))

    @staticmethod
    def _abort_process_sync(process: asyncio.subprocess.Process) -> None:
        with suppress(ProcessLookupError):
            process.kill()

    def _process_env(self) -> dict[str, str] | None:
        if self._extra_env is None:
            return None
        env = {key: str(value) for key, value in os.environ.items()}
        env.update({key: str(value) for key, value in self._extra_env.items()})
        return env


_USAGE_KEY_ALIASES = {
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
}


def _coerce_usage(raw: Any) -> dict[str, int] | None:
    """Normalize runtime usage keys to the project's provider convention."""

    if not isinstance(raw, Mapping):
        return None
    usage: dict[str, int] = {}
    for key, value in raw.items():
        normalized_key = _USAGE_KEY_ALIASES.get(str(key), str(key))
        try:
            usage[normalized_key] = int(value)
        except (TypeError, ValueError):
            continue
    if "prompt_tokens" in usage and "completion_tokens" in usage and "total_tokens" not in usage:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage or None


def build_sponsored_provider_from_env(
    env: Mapping[str, str] | None = None,
) -> SponsoredProvider | None:
    """Build a provider from environment variables, default-off.

    This is the bootstrap seam until ``[llm.sponsored]`` config lands. Both
    variables are required:

    - ``OPENBILICLAW_SPONSORED_ENABLED``: ``1`` / ``true`` / ``yes`` / ``on``;
    - ``OPENBILICLAW_SPONSORED_RUNTIME``: command line for the runtime binary
      (or the Python mock runtime during development).
    """

    source = env if env is not None else os.environ
    enabled = str(source.get(SPONSORED_ENABLED_ENV) or "").strip().lower()
    if enabled not in _TRUTHY_ENV_VALUES:
        return None
    command_text = str(source.get(SPONSORED_RUNTIME_ENV) or "").strip()
    if not command_text:
        return None
    try:
        command = shlex.split(command_text)
    except ValueError:
        logger.warning("invalid %s command line", SPONSORED_RUNTIME_ENV)
        return None
    if not command:
        return None
    try:
        return SponsoredProvider(command)
    except ValueError:
        logger.warning("could not build sponsored provider from environment", exc_info=True)
        return None
