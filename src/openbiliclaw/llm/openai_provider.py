"""OpenAI-compatible LLM provider.

Supports OpenAI API and any compatible APIs (e.g. DeepSeek, local vLLM).
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI

from .base import (
    DEFAULT_REASONING_EFFORT,
    LLMAuthError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMStreamChunk,
    LLMTimeoutError,
    LLMToolCallUnsupportedError,
)

logger = logging.getLogger(__name__)
_BILLING_BACKOFF_STATUS_CODES = {402}
_NON_RETRYABLE_REQUEST_STATUS_CODES = {400, 403, 404, 405, 422}
_BILLING_BACKOFF_MARKERS = (
    "insufficient balance",
    "payment required",
    "quota exceeded",
    "billing",
    "out of credit",
    "credit exhausted",
    "余额不足",
    "账户余额",
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

# A reasoning-first endpoint can burn the entire output budget on invisible
# thinking and finish truncated — ``finish_reason=length`` on chat
# completions, ``status="incomplete"`` with
# ``incomplete_details.reason="max_output_tokens"`` on the Responses API —
# either with empty ``content`` or with a JSON payload truncated mid-stream.
# Retrying with the same budget cannot succeed, so ``complete()`` reissues
# the request once with a doubled budget, capped here to stay within common
# provider ceilings (DeepSeek documents 64K; many gateways reject anything
# above 32K).
_LENGTH_FINISH_REASONS = frozenset({"length", "max_tokens"})
_LENGTH_RETRY_MAX_TOKENS_CAP = 32768


class _NonRetryableRequestError(LLMProviderError):
    """A request/configuration failure that cannot heal through immediate retry."""


def _generic_json_schema_response_format() -> dict[str, Any]:
    """OpenAI structured-output shape for arbitrary JSON object tasks."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_response",
            "strict": False,
            "schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        },
    }


class OpenAIProvider(LLMProvider):
    """OpenAI and compatible API provider."""

    # OpenAI's API has a working embeddings endpoint
    # (text-embedding-3-small / -large). Subclasses pointing at backends
    # that don't expose embeddings (DeepSeek, OpenRouter, etc.) override
    # this back to False — see DeepSeekProvider / OpenRouterProvider.
    supports_embedding = True

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = "",
        provider_name: str = "openai",
        token_provider: Callable[[bool], Awaitable[str]] | None = None,
        timeout: float = 1200.0,
        embedding_output_dimensionality: int = 0,
        api_flavor: str = "",
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        self._model = model
        self._provider_name = provider_name
        self.base_url = base_url or ""
        # "" / "chat_completions" → /v1/chat/completions (default).
        # "responses" → /v1/responses — needed by third-party gateways that
        # expose GPT models only through the Responses API (issue #72).
        self._api_flavor = api_flavor.strip().lower()
        # Native function calling is implemented on the chat-completions
        # flavor only (M1); responses-flavor instances use the service
        # layer's prompt-simulation fallback.
        self.supports_tool_calling = self._api_flavor != "responses"
        self._token_provider = token_provider
        self._timeout = timeout
        self._embedding_output_dimensionality = max(0, int(embedding_output_dimensionality or 0))
        self._reasoning_effort = reasoning_effort.strip()
        # Volatile, successful wire adaptations only. Provider replacement
        # clears these; model and reasoning/JSON modes must never share hints.
        self._compatibility_hints: dict[tuple[str, str, bool, bool], tuple[bool, bool]] = {}
        # Overseas routing policy: custom injects a proxy, direct injects a
        # proxy-env-immune client, and system leaves SDK construction untouched.
        self._proxy = proxy.strip()
        self._trust_env = bool(trust_env and not self._proxy)
        client_kwargs: dict[str, Any] = {}
        if self._proxy or not self._trust_env:
            httpx_kwargs: dict[str, Any] = {"timeout": timeout, "trust_env": self._trust_env}
            if self._proxy:
                httpx_kwargs["proxy"] = self._proxy
            client_kwargs["http_client"] = httpx.AsyncClient(**httpx_kwargs)
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=0,
            timeout=timeout,
            **client_kwargs,
        )

    @property
    def name(self) -> str:
        return self._provider_name

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
        if self._api_flavor == "responses":
            return await self._complete_via_responses(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                model=model,
            )
        effective_model = (model or "").strip() or self._model
        effective_reasoning_effort = self._effective_reasoning_effort(reasoning_effort)
        hint_key = (
            effective_model,
            effective_reasoning_effort,
            reasoning_effort is not None,
            json_mode,
        )
        omit_format, disable_thinking = self._compatibility_hints.get(hint_key, (False, False))
        learned_omit_format = False
        learned_disable_thinking = False
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and not omit_format:
            fmt = self._json_response_format()
            if fmt is not None:
                kwargs["response_format"] = fmt
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        openai_effort = self._openai_reasoning_effort(
            effective_model,
            effective_reasoning_effort,
        )
        if openai_effort is not None:
            kwargs["reasoning_effort"] = openai_effort
        extra_body = self._extra_body(reasoning_effort=effective_reasoning_effort)
        if disable_thinking:
            extra_body = {**(extra_body or {}), "thinking": {"type": "disabled"}}
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            response = await self._chat_request_with_temperature_compat(**kwargs)
        except LLMProviderError as exc:
            self._compatibility_hints.pop(hint_key, None)
            # Retry at most once: after replacement kwargs["response_format"]
            # is no longer json_object, so _uses_json_object returns False.
            if (
                json_mode
                and self._uses_json_object(kwargs.get("response_format"))
                and self._json_object_response_format_rejected(exc)
            ):
                logger.info(
                    "%s rejected json_object response_format; retrying with json_schema",
                    self._provider_name,
                )
                kwargs["response_format"] = _generic_json_schema_response_format()
                response = await self._chat_request_with_temperature_compat(**kwargs)
            else:
                raise
        choice = response.choices[0]
        content = choice.message.content or ""
        if json_mode and content.strip() and self._length_truncated(choice):
            # The model was cut off mid-JSON by the output cap; the partial
            # payload is unparseable for structured callers. Retry once with
            # a doubled budget instead of handing truncated text downstream.
            retried = await self._retry_with_larger_budget(
                kwargs,
                max_tokens=max_tokens,
                send=lambda kw: self._chat_request_with_temperature_compat(**kw),
            )
            if retried is not None:
                response, max_tokens = retried
                choice = response.choices[0]
                content = choice.message.content or ""
        if not content.strip():
            # Some OpenAI-compatible backends return HTTP 200 and report
            # completion_tokens > 0, yet ``message.content`` is empty when
            # ``response_format`` is set. Retry once without the constraint;
            # the prompt itself already asks for JSON.
            if json_mode and "response_format" in kwargs:
                logger.warning(
                    "%s returned empty content with response_format=%s; "
                    "retrying without response_format constraint",
                    self._provider_name,
                    kwargs["response_format"].get("type", "?"),
                )
                kwargs.pop("response_format")
                learned_omit_format = True
                response = await self._chat_request_with_temperature_compat(**kwargs)
                choice = response.choices[0]
                content = choice.message.content or ""
            if (
                not content.strip()
                and self._provider_name == "openai_compatible"
                and reasoning_effort is not None
                and not effective_reasoning_effort
                and self._reasoning_like_content(getattr(choice, "message", None))
                and kwargs.get("extra_body", {}).get("thinking") != {"type": "disabled"}
            ):
                # Generic OpenAI-compatible gateways do not share one
                # portable switch for disabling thinking. Omitting
                # ``reasoning_effort`` is wire-compatible, but some DeepSeek
                # relays interpret omission as "use the model default" and
                # can spend the whole output budget on reasoning. Only after
                # observing that exact failure for an explicit no-reasoning
                # call, retry once with DeepSeek's disable body.
                logger.warning(
                    "%s ignored explicit no-reasoning request; retrying with thinking disabled",
                    self._provider_name,
                )
                retry_extra_body = dict(kwargs.get("extra_body") or {})
                retry_extra_body["thinking"] = {"type": "disabled"}
                kwargs["extra_body"] = retry_extra_body
                learned_disable_thinking = True
                response = await self._chat_request_with_temperature_compat(**kwargs)
                choice = response.choices[0]
                content = choice.message.content or ""
            if not content.strip() and self._length_truncated(choice):
                # A reasoning-first endpoint spent the whole budget on thinking
                # and returned reasoning-only output; only a larger budget lets
                # the final answer through. This is the generic recovery for
                # callers that keep the provider's configured effort (e.g. the
                # keyword planner's merged generation).
                retried = await self._retry_with_larger_budget(
                    kwargs,
                    max_tokens=max_tokens,
                    send=lambda kw: self._chat_request_with_temperature_compat(**kw),
                )
                if retried is not None:
                    response, max_tokens = retried
                    choice = response.choices[0]
                    content = choice.message.content or ""
            if not content.strip():
                self._compatibility_hints.pop(hint_key, None)
                raise self._empty_content_error(choice)

        if self._provider_name == "openai_compatible" and (
            learned_omit_format or learned_disable_thinking
        ):
            valid = True
            if json_mode:
                try:
                    valid = isinstance(json.loads(content), (dict, list))
                except (TypeError, ValueError):
                    valid = False
            if valid:
                if (
                    len(self._compatibility_hints) >= 32
                    and hint_key not in self._compatibility_hints
                ):
                    self._compatibility_hints.pop(next(iter(self._compatibility_hints)))
                self._compatibility_hints[hint_key] = (
                    omit_format or learned_omit_format,
                    disable_thinking or learned_disable_thinking,
                )

        usage = self._chat_usage(response)

        return LLMResponse(
            content=content,
            model=response.model,
            provider=self._provider_name,
            usage=usage,
            raw=response,
        )

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
        """Chat completion with OpenAI-native function calling.

        Sends ``tools=[{"type": "function", ...}]`` plus ``tool_choice="auto"``
        and normalizes ``message.tool_calls`` into ``LLMResponse.tool_calls``
        entries of ``{"id", "name", "arguments", "arguments_raw"}``. A response
        with tool calls and no text content is a valid result (unlike
        ``complete()``, which raises on empty content).

        The ``/v1/responses`` flavor is not wired for tool calling in M1;
        those instances report ``supports_tool_calling = False`` and the
        service layer falls back to prompt simulation.
        """
        if self._api_flavor == "responses":
            raise LLMToolCallUnsupportedError(
                f"{self._provider_name} (api_flavor=responses) has no native tool calling."
            )
        effective_model = (model or "").strip() or self._model
        effective_reasoning_effort = self._effective_reasoning_effort(reasoning_effort)
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        openai_effort = self._openai_reasoning_effort(
            effective_model,
            effective_reasoning_effort,
        )
        if openai_effort is not None:
            kwargs["reasoning_effort"] = openai_effort
        extra_body = self._extra_body(reasoning_effort=effective_reasoning_effort)
        if extra_body:
            kwargs["extra_body"] = extra_body

        response = await self._chat_request_with_temperature_compat(**kwargs)
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
        tool_calls = self._parse_native_tool_calls(message)
        if not content.strip() and not tool_calls and self._length_truncated(choice):
            # A reasoning-first endpoint burned the whole output budget on
            # thinking before emitting content or tool calls; only a larger
            # budget lets the turn complete. The retry keeps the tools
            # payload intact; a response with tool calls never enters here.
            retried = await self._retry_with_larger_budget(
                kwargs,
                max_tokens=max_tokens,
                send=lambda kw: self._chat_request_with_temperature_compat(**kw),
            )
            if retried is not None:
                response, max_tokens = retried
                choice = response.choices[0]
                message = choice.message
                content = message.content or ""
                tool_calls = self._parse_native_tool_calls(message)
        if not content.strip() and not tool_calls:
            raise self._empty_content_error(choice)

        return LLMResponse(
            content=content,
            model=response.model,
            provider=self._provider_name,
            usage=self._chat_usage(response),
            raw=response,
            tool_calls=tool_calls or None,
        )

    def _chat_completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        model: str | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any]:
        """Assemble the shared chat-completions request kwargs."""
        effective_model = (model or "").strip() or self._model
        effective_reasoning_effort = self._effective_reasoning_effort(reasoning_effort)
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        openai_effort = self._openai_reasoning_effort(
            effective_model,
            effective_reasoning_effort,
        )
        if openai_effort is not None:
            kwargs["reasoning_effort"] = openai_effort
        extra_body = self._extra_body(reasoning_effort=effective_reasoning_effort)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    async def _chat_stream_request(self, **kwargs: Any) -> Any:
        """Open a streaming chat request, adapting to backend quirks.

        Reuses the temperature-compat retry of the one-shot path; older
        gateways that reject ``stream_options`` get one retry without it
        (usage accounting is then simply absent for that stream).
        """
        try:
            return await self._chat_request_with_temperature_compat(**kwargs)
        except LLMProviderError as exc:
            if "stream_options" in kwargs and "stream_options" in str(exc).lower():
                logger.info(
                    "%s rejected stream_options; retrying the stream without it",
                    self._provider_name,
                )
                kwargs.pop("stream_options")
                return await self._chat_request_with_temperature_compat(**kwargs)
            raise

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a chat completion token by token (chat-completions flavor).

        The Responses-API flavor and ``json_mode`` deliberately keep the
        base class's one-shot fallback over ``complete()``: structured
        callers depend on the json-format rejection retries, and the
        Responses flavor's streaming events are not wired here. Unlike
        ``complete()``, the streaming path does not reissue truncated or
        empty requests with a larger budget — mid-stream retries would
        duplicate already-displayed text; the registry's pre-delta fallback
        still covers failures before the first token.
        """
        if self._api_flavor == "responses" or json_mode:
            async for chunk in super().stream_complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=reasoning_effort,
                model=model,
            ):
                yield chunk
            return
        kwargs = self._chat_completion_kwargs(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream = await self._chat_stream_request(**kwargs)
        parts: list[str] = []
        model_seen = ""
        usage: dict[str, int] | None = None
        try:
            async for event in stream:
                model_seen = str(getattr(event, "model", "") or model_seen)
                if getattr(event, "usage", None):
                    usage = self._chat_usage(event)
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = str(getattr(delta, "content", "") or "")
                if content:
                    parts.append(content)
                    yield LLMStreamChunk(delta=content)
        except Exception as exc:
            raise self._map_error(exc) from exc
        content = "".join(parts)
        if not content.strip():
            raise LLMResponseError(f"{self._provider_name} returned an empty streamed response")
        yield LLMStreamChunk(
            response=LLMResponse(
                content=content,
                model=model_seen,
                provider=self._provider_name,
                usage=usage,
            )
        )

    async def stream_complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a chat completion with native function calling.

        Content deltas flow live; ``tool_calls`` deltas are accumulated
        silently and surface only on the terminal chunk's response, so the
        agent loop can decide per hop whether the streamed text was the
        final reply or intermediate reasoning.
        """
        if self._api_flavor == "responses":
            raise LLMToolCallUnsupportedError(
                f"{self._provider_name} (api_flavor=responses) has no native tool calling."
            )
        kwargs = self._chat_completion_kwargs(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        stream = await self._chat_stream_request(**kwargs)
        parts: list[str] = []
        pending_calls: dict[int, dict[str, Any]] = {}
        model_seen = ""
        usage: dict[str, int] | None = None
        try:
            async for event in stream:
                model_seen = str(getattr(event, "model", "") or model_seen)
                if getattr(event, "usage", None):
                    usage = self._chat_usage(event)
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                content = str(getattr(delta, "content", "") or "")
                if content:
                    parts.append(content)
                    yield LLMStreamChunk(delta=content)
                for raw_call in getattr(delta, "tool_calls", None) or []:
                    index = int(getattr(raw_call, "index", 0) or 0)
                    entry = pending_calls.setdefault(
                        index, {"id": "", "name": "", "arguments_parts": []}
                    )
                    call_id = getattr(raw_call, "id", None)
                    if call_id:
                        entry["id"] = str(call_id)
                    function = getattr(raw_call, "function", None)
                    if function is not None:
                        name = getattr(function, "name", None)
                        if name:
                            entry["name"] += str(name)
                        arguments = getattr(function, "arguments", None)
                        if arguments:
                            entry["arguments_parts"].append(str(arguments))
        except Exception as exc:
            raise self._map_error(exc) from exc
        content = "".join(parts)
        normalized_message = SimpleNamespace(
            tool_calls=[
                {
                    "id": entry["id"],
                    "function": {
                        "name": entry["name"],
                        "arguments": "".join(entry["arguments_parts"]),
                    },
                }
                for _index, entry in sorted(pending_calls.items())
            ]
        )
        tool_calls = self._parse_native_tool_calls(normalized_message)
        if not content.strip() and not tool_calls:
            raise LLMResponseError(
                f"{self._provider_name} returned an empty streamed response "
                "(no content, no tool calls)"
            )
        yield LLMStreamChunk(
            response=LLMResponse(
                content=content,
                model=model_seen,
                provider=self._provider_name,
                usage=usage,
                tool_calls=tool_calls or None,
            )
        )

    @staticmethod
    def _parse_native_tool_calls(message: Any) -> list[dict[str, Any]]:
        """Normalize OpenAI ``message.tool_calls`` into plain dicts.

        ``arguments`` arrives as a JSON string on the wire; parse failures
        keep the raw payload in ``arguments_raw`` (with ``arguments={}``) so
        the agent loop can feed the malformed call back to the model.
        """
        raw_calls = OpenAIProvider._read_message_field(message, "tool_calls")
        if not isinstance(raw_calls, (list, tuple)):
            return []
        calls: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_calls):
            function = (
                raw.get("function") if isinstance(raw, dict) else getattr(raw, "function", None)
            )
            name = str(OpenAIProvider._read_message_field(function, "name") or "")
            arguments_raw = str(OpenAIProvider._read_message_field(function, "arguments") or "")
            raw_id = raw.get("id") if isinstance(raw, dict) else getattr(raw, "id", None)
            call_id = str(raw_id or f"call_{index}")
            arguments: dict[str, Any] = {}
            if arguments_raw.strip():
                try:
                    parsed = json.loads(arguments_raw)
                    if isinstance(parsed, dict):
                        arguments = parsed
                except json.JSONDecodeError:
                    logger.warning(
                        "Tool call %s returned malformed arguments JSON", name or call_id
                    )
            calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "arguments_raw": arguments_raw,
                }
            )
        return calls

    def _chat_usage(self, response: Any) -> dict[str, int] | None:
        """Normalize a chat-completions usage object into the shared dict shape."""
        if not response.usage:
            return None
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        # Normalize cache fields across the OpenAI-protocol family.
        # OpenAI exposes `prompt_tokens_details.cached_tokens` since
        # GPT-4o; DeepSeek injects `prompt_cache_hit_tokens` /
        # `prompt_cache_miss_tokens` on the same usage object;
        # Kimi / 通义 / 中转站 vary. We probe known fields and
        # surface whichever the backend sent under the universal
        # ``cached_input_tokens`` key. Downstream pricing /
        # observability code reads only this normalized field.
        cached = 0
        details = getattr(response.usage, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        if not cached:
            # DeepSeek explicit fields
            cached = int(getattr(response.usage, "prompt_cache_hit_tokens", 0) or 0)
        if cached:
            usage["cached_input_tokens"] = cached
        return usage

    async def _complete_via_responses(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        reasoning_effort: str | None,
        model: str | None,
    ) -> LLMResponse:
        """Serve ``complete()`` through the ``/v1/responses`` endpoint.

        Parameter mapping: the first system message → ``instructions``,
        remaining messages → ``input``; ``max_tokens`` →
        ``max_output_tokens``; ``json_mode`` → ``text.format``.
        """
        effective_model = (model or "").strip() or self._model
        effective_reasoning_effort = self._effective_reasoning_effort(reasoning_effort)
        instructions = ""
        input_messages: list[dict[str, str]] = []
        for msg in messages:
            if msg["role"] == "system" and not instructions:
                instructions = msg["content"]
            else:
                input_messages.append(msg)
        # Some Responses endpoints validate only input, excluding instructions.
        # Keep the cached system prefix and caller-owned messages intact (#265).
        if json_mode and not any("json" in msg["content"].lower() for msg in input_messages):
            input_messages.append({"role": "user", "content": "Return valid json."})
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "store": False,
        }
        if instructions:
            kwargs["instructions"] = instructions
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        openai_effort = self._openai_reasoning_effort(
            effective_model,
            effective_reasoning_effort,
        )
        if openai_effort is not None:
            kwargs["reasoning"] = {"effort": openai_effort}
        extra_body = self._extra_body(reasoning_effort=effective_reasoning_effort)
        if extra_body:
            kwargs["extra_body"] = extra_body

        response = await self._responses_request_dropping_rejected_temperature(kwargs)
        content = self._responses_output_text(response)
        if json_mode and content.strip() and self._responses_output_truncated(response):
            # The model was cut off mid-JSON by the output cap; the partial
            # payload is unparseable for structured callers. Retry once with
            # a doubled budget instead of handing truncated text downstream.
            retried = await self._retry_with_larger_budget(
                kwargs,
                max_tokens=max_tokens,
                send=self._responses_request_dropping_rejected_temperature,
                budget_key="max_output_tokens",
                truncation="status=incomplete (max_output_tokens)",
            )
            if retried is not None:
                response, max_tokens = retried
                content = self._responses_output_text(response)
        if not content.strip():
            if json_mode and "text" in kwargs:
                # Same backend quirk as the chat-completions path: HTTP 200
                # with empty content when an output-format constraint is set.
                # The prompt already demands JSON, so drop the constraint and
                # retry.
                logger.warning(
                    "%s returned empty content with text.format=json_object; "
                    "retrying without the format constraint",
                    self._provider_name,
                )
                kwargs.pop("text")
                response = await self._responses_request_dropping_rejected_temperature(kwargs)
                content = self._responses_output_text(response)
            if not content.strip() and self._responses_output_truncated(response):
                # A reasoning-first model spent the whole output budget on
                # thinking and the response stayed incomplete; only a larger
                # budget lets the final answer through.
                retried = await self._retry_with_larger_budget(
                    kwargs,
                    max_tokens=max_tokens,
                    send=self._responses_request_dropping_rejected_temperature,
                    budget_key="max_output_tokens",
                    truncation="status=incomplete (max_output_tokens)",
                )
                if retried is not None:
                    response, max_tokens = retried
                    content = self._responses_output_text(response)
        if not content.strip():
            raise self._responses_empty_content_error(response)

        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            input_tokens = int(getattr(raw_usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(raw_usage, "output_tokens", 0) or 0)
            total_tokens = int(
                getattr(raw_usage, "total_tokens", 0) or (input_tokens + output_tokens)
            )
            usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
            # Responses API nests the cache counter under
            # ``input_tokens_details.cached_tokens``; normalize to the
            # universal ``cached_input_tokens`` key (see chat path above).
            details = getattr(raw_usage, "input_tokens_details", None)
            cached = int(getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
            if cached:
                usage["cached_input_tokens"] = cached

        return LLMResponse(
            content=content,
            model=str(getattr(response, "model", "") or effective_model),
            provider=self._provider_name,
            usage=usage,
            raw=response,
        )

    async def _responses_request_dropping_rejected_temperature(self, kwargs: dict[str, Any]) -> Any:
        """Send a Responses request, dropping ``temperature`` if rejected.

        Reasoning-first models (gpt-5 family, o-series) reject the
        ``temperature`` parameter outright. Rather than maintaining a
        model allowlist, probe optimistically and retry once without it.
        ``kwargs`` is mutated on purpose so a later json-mode retry with
        the same dict doesn't reintroduce the rejected parameter.
        """
        try:
            return await self._responses_request_with_retry(**kwargs)
        except LLMProviderError as exc:
            if "temperature" in kwargs and self._temperature_rejected(exc):
                logger.info(
                    "%s rejected temperature on /v1/responses; retrying without it",
                    self._provider_name,
                )
                kwargs.pop("temperature")
                return await self._responses_request_with_retry(**kwargs)
            raise

    async def _chat_request_with_temperature_compat(self, **kwargs: Any) -> Any:
        """Send a chat request, adapting temperature when the backend rejects it.

        Some OpenAI-compatible providers (for example SenseNova's Kimi route)
        either reject ``temperature`` entirely or require a specific value.
        Retry once with the portable fix instead of surfacing a 400 to users.
        """
        try:
            return await self._request_with_retry(**kwargs)
        except LLMProviderError as exc:
            if "temperature" in kwargs and self._temperature_rejected(exc):
                message = str(exc).lower()
                if "only 1 is allowed" in message:
                    kwargs["temperature"] = 1
                else:
                    kwargs.pop("temperature", None)
                logger.info(
                    "%s rejected temperature on chat completion; retrying with compatible value",
                    self._provider_name,
                )
                return await self._request_with_retry(**kwargs)
            raise

    @staticmethod
    def _temperature_rejected(exc: LLMProviderError) -> bool:
        message = str(exc).lower()
        if "temperature" not in message:
            return False
        return (
            "unsupported" in message
            or "not supported" in message
            or "does not support" in message
            or "only 1 is allowed" in message
            or "invalid" in message
            or "not allowed" in message
        )

    @staticmethod
    def _responses_output_text(response: Any) -> str:
        """Extract assistant text from a Responses API payload.

        Prefer the SDK's aggregated ``output_text``; fall back to walking
        ``output`` message items for gateways whose SDK objects (or raw
        namespaces) don't provide the convenience property.
        """
        text = getattr(response, "output_text", None)
        if text:
            return str(text)
        parts: list[str] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", "") != "message":
                continue
            for block in getattr(item, "content", None) or []:
                block_text = getattr(block, "text", "")
                if block_text:
                    parts.append(str(block_text))
        return "".join(parts)

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        return await self._send_with_retry(self._create_chat_completion, **kwargs)

    async def _responses_request_with_retry(self, **kwargs: Any) -> Any:
        return await self._send_with_retry(self._create_response, **kwargs)

    async def _create_chat_completion(self, **kwargs: Any) -> Any:
        return await self._client.chat.completions.create(**kwargs)

    async def _create_response(self, **kwargs: Any) -> Any:
        return await self._client.responses.create(**kwargs)

    async def _send_with_retry(self, send: Callable[..., Awaitable[Any]], **kwargs: Any) -> Any:
        """Send a request with bounded retry for transient failures."""
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                await self._apply_dynamic_token(force_refresh=False)
                return await send(**kwargs)
            except Exception as exc:
                if self._is_unauthorized(exc) and self._token_provider is not None:
                    try:
                        await self._apply_dynamic_token(force_refresh=True)
                        return await send(**kwargs)
                    except Exception as refresh_exc:
                        mapped_refresh = self._map_error(refresh_exc)
                        raise mapped_refresh from refresh_exc
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc

                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError(f"{self._provider_name} request failed")
        raise last_error

    async def _apply_dynamic_token(self, *, force_refresh: bool) -> None:
        if self._token_provider is None:
            return
        try:
            token = await self._token_provider(force_refresh)
        except Exception as exc:
            raise LLMProviderError(
                f"{self._provider_name} token refresh failed; run `openbiliclaw login codex` again."
            ) from exc
        if token:
            self._client.api_key = token

    @staticmethod
    def _is_unauthorized(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code == 401
        if isinstance(status_code, str):
            return status_code.strip() == "401"
        return False

    def _map_error(self, exc: Exception) -> LLMProviderError:
        """Map provider or network exceptions into shared provider errors."""
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError(f"{self._provider_name} request timed out")

        status_code = getattr(exc, "status_code", None)
        status_code_int = self._status_code_int(status_code)
        body_excerpt = self._provider_error_body_excerpt(exc)
        message = f"{exc} {body_excerpt}".lower()
        if status_code_int == 429 or "rate limit" in message or "too many requests" in message:
            return LLMRateLimitError(f"{self._provider_name} rate limit exceeded")
        if status_code_int in _BILLING_BACKOFF_STATUS_CODES or any(
            marker in message for marker in _BILLING_BACKOFF_MARKERS
        ):
            detail = body_excerpt or str(exc)
            return LLMRateLimitError(
                f"{self._provider_name} provider backoff: HTTP {status_code_int or status_code}: "
                f"{detail}"
            )
        if status_code_int == 401:
            detail = body_excerpt or str(exc)
            logger.warning(
                "%s rejected our credentials with HTTP 401 (base_url=%s): %s",
                self._provider_name,
                self.base_url or "<default>",
                detail,
            )
            return LLMAuthError(
                f"{self._provider_name} authentication failed: HTTP 401: {detail}",
                provider_name=self._provider_name,
                endpoint=self.base_url,
            )
        if status_code_int and status_code_int >= 500:
            return LLMProviderError(f"{self._provider_name} server error: {status_code}")
        if status_code and body_excerpt:
            logger.warning(
                "%s request failed with HTTP %s: %s",
                self._provider_name,
                status_code,
                body_excerpt,
            )
            error_type = (
                _NonRetryableRequestError
                if status_code_int in _NON_RETRYABLE_REQUEST_STATUS_CODES
                else LLMProviderError
            )
            return error_type(
                f"{self._provider_name} request failed: HTTP {status_code}: {body_excerpt}"
            )

        return LLMProviderError(f"{self._provider_name} request failed: {exc}")

    @staticmethod
    def _status_code_int(status_code: object) -> int | None:
        if isinstance(status_code, int):
            return status_code
        if isinstance(status_code, str):
            try:
                return int(status_code.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _provider_error_body_excerpt(exc: Exception) -> str:
        """Extract a compact provider response body from SDK exceptions."""

        candidates: list[object] = []
        body = getattr(exc, "body", None)
        if body:
            candidates.append(body)
        response = getattr(exc, "response", None)
        if response is not None:
            text = getattr(response, "text", None)
            if text:
                candidates.append(text)
            content = getattr(response, "content", None)
            if content:
                candidates.append(content)

        for candidate in candidates:
            if isinstance(candidate, bytes):
                text = candidate.decode("utf-8", errors="replace")
            elif isinstance(candidate, (dict, list)):
                text = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            else:
                text = str(candidate)
            text = " ".join(text.split())
            if text:
                return text[:1000] + ("..." if len(text) > 1000 else "")
        return ""

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        """Whether a mapped exception should be retried."""
        # A 401 stays a 401 until the user edits config. Retrying only
        # multiplies rejected requests in the provider console and drags out
        # the wait before the actionable error reaches the user.
        if isinstance(exc, (LLMRateLimitError, LLMAuthError, _NonRetryableRequestError)):
            return False
        return isinstance(exc, (LLMProviderError, LLMTimeoutError))

    def _json_response_format(self) -> dict[str, Any] | None:
        if self._is_lm_studio():
            # LM Studio's OpenAI-compat layer loses ``message.content``
            # with both ``json_object`` and ``json_schema`` response
            # formats (HTTP 200, completion_tokens > 0, but content is
            # empty). Skip ``response_format`` entirely; the prompt
            # already asks for JSON so the model still produces it.
            return None
        return {"type": "json_object"}

    def _is_lm_studio(self) -> bool:
        """Detect LM Studio by URL heuristics (name or default port)."""
        raw_base_url = self.base_url.strip()
        if not raw_base_url:
            return False
        normalized = raw_base_url.lower()
        if "lmstudio" in normalized or "lm-studio" in normalized:
            return True
        parsed_url = raw_base_url if "://" in raw_base_url else f"http://{raw_base_url}"
        parsed = urlparse(parsed_url)
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            return False
        if host in {"localhost", "127.0.0.1", "::1"} and port == 1234:
            logger.debug("treating %s as LM Studio (default port 1234)", raw_base_url)
            return True
        return False

    @staticmethod
    def _uses_json_object(response_format: object) -> bool:
        return isinstance(response_format, dict) and response_format.get("type") == "json_object"

    @staticmethod
    def _json_object_response_format_rejected(exc: LLMProviderError) -> bool:
        # The field path "response_format.type" is lowercase in all known
        # OpenAI-protocol implementations, so .lower() + literal match is safe.
        message = str(exc).lower()
        return "response_format.type" in message and "json_schema" in message and "text" in message

    async def embed(self, text: str, *, model: str = "text-embedding-3-small") -> list[float]:
        """Get text embedding via OpenAI's ``/v1/embeddings`` endpoint.

        Returns an empty list on failure so callers can degrade
        gracefully (the embedding service treats empty vectors as
        "no embedding"). This matches the contract Gemini/Ollama
        providers already follow.
        """
        try:
            kwargs: dict[str, Any] = {"model": model, "input": text}
            if (
                self._supports_embedding_dimensions(model)
                and self._embedding_output_dimensionality > 0
            ):
                kwargs["dimensions"] = self._embedding_output_dimensionality
            response = await self._client.embeddings.create(**kwargs)
            return list(response.data[0].embedding)
        except Exception:
            logger.warning(
                "%s embedding failed (model=%s)",
                self._provider_name,
                model,
                exc_info=True,
            )
            return []

    async def list_models(self) -> list[str]:
        """List model IDs advertised by the OpenAI-compatible endpoint.

        ``GET /models`` is part of the OpenAI wire protocol, but it only
        standardizes basic model metadata. Callers must not infer chat,
        embedding, or reasoning capabilities from presence in this list.
        """

        page = await self._send_with_retry(self._create_model_list)
        identifiers = {
            str(getattr(item, "id", "") or "").strip()
            for item in (getattr(page, "data", None) or [])
        }
        return sorted((identifier for identifier in identifiers if identifier), key=str.casefold)

    async def _create_model_list(self) -> Any:
        return await self._client.models.list()

    def _supports_embedding_dimensions(self, model: str) -> bool:
        if not model.startswith("text-embedding-3-"):
            return False
        return self._provider_name == "openai"

    def _extra_headers(self) -> dict[str, str]:
        """Return optional provider-specific request headers."""
        return {}

    def _effective_reasoning_effort(self, requested: str | None) -> str:
        """Resolve a per-call override without mutating shared provider state."""

        return self._reasoning_effort if requested is None else requested.strip()

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Return an explicit OpenAI-wire effort when the route can honor it.

        Official OpenAI reasoning models receive documented values verbatim.
        A generic ``openai_compatible`` endpoint receives a non-empty,
        user-selected value as an advisory pass-through; unknown gateways can
        reject it, which is why the settings UI keeps the field optional and
        pairs it with a real connection probe. Other adapters use their own
        provider-specific mapping.
        """

        normalized = effort.strip().lower()
        if not normalized:
            return None
        if self._provider_name == "openai_compatible":
            return normalized
        if not self._is_official_openai_endpoint() or not self._is_openai_reasoning_model(model):
            return None
        if normalized in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            return normalized
        return DEFAULT_REASONING_EFFORT

    def _is_official_openai_endpoint(self) -> bool:
        if not self.base_url.strip():
            return self._provider_name == "openai"
        parsed = urlparse(self.base_url if "://" in self.base_url else f"https://{self.base_url}")
        return (
            self._provider_name == "openai" and (parsed.hostname or "").lower() == "api.openai.com"
        )

    @staticmethod
    def _is_openai_reasoning_model(model: str) -> bool:
        name = model.strip().lower()
        return name.startswith("gpt-5") or (len(name) > 1 and name[0] == "o" and name[1].isdigit())

    def _extra_body(self, *, reasoning_effort: str | None = None) -> dict[str, Any]:
        """Return optional provider-specific request body fields.

        Used for non-standard keys like DeepSeek's ``thinking`` and
        ``reasoning_effort``. Keys returned here are passed verbatim via
        ``extra_body`` of the OpenAI SDK.
        """
        del reasoning_effort
        return {}

    @staticmethod
    def _length_truncated(choice: Any) -> bool:
        """Whether the choice was cut off by the output-token cap."""
        return str(getattr(choice, "finish_reason", "") or "") in _LENGTH_FINISH_REASONS

    @staticmethod
    def _responses_output_truncated(response: Any) -> bool:
        """Whether a Responses payload was cut off by the output-token cap.

        The Responses API signals output truncation with
        ``status="incomplete"`` and
        ``incomplete_details.reason="max_output_tokens"``.
        """
        if str(getattr(response, "status", "") or "") != "incomplete":
            return False
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if reason is None and isinstance(details, dict):
            reason = details.get("reason")
        return str(reason or "") == "max_output_tokens"

    @staticmethod
    def _responses_reasoning_output(response: Any) -> bool:
        """Whether a Responses payload contains reasoning-phase output items."""
        for item in getattr(response, "output", None) or []:
            item_type = getattr(item, "type", None)
            if item_type is None and isinstance(item, dict):
                item_type = item.get("type")
            if str(item_type or "") == "reasoning":
                return True
        return False

    def _responses_empty_content_error(self, response: Any) -> LLMResponseError:
        """Empty-content error for the Responses flavor.

        When the payload carries reasoning-phase output but no final message,
        mirror the chat path's marker pair (``returned reasoning but no final
        content`` + ``finish_reason=length``) so
        ``is_reasoning_budget_exhausted()`` — and the evaluation
        batch-halving self-heal built on it — recognizes the failure. The
        Responses API expresses chat's ``finish_reason=length`` as
        ``status="incomplete"`` with reason ``max_output_tokens``; any other
        terminal status maps to its own name and stays unrecognized, exactly
        like a non-length finish reason on the chat path.
        """
        if self._responses_reasoning_output(response):
            finish_reason = (
                "length"
                if self._responses_output_truncated(response)
                else str(getattr(response, "status", "") or "unknown")
            )
            return LLMResponseError(
                f"{self._provider_name} returned reasoning but no final content "
                f"(finish_reason={finish_reason}); "
                "disable thinking/reasoning or increase max_tokens"
            )
        return LLMResponseError(f"{self._provider_name} returned empty content")

    async def _retry_with_larger_budget(
        self,
        kwargs: dict[str, Any],
        *,
        max_tokens: int,
        send: Callable[[dict[str, Any]], Awaitable[Any]],
        budget_key: str = "max_tokens",
        truncation: str = "finish_reason=length",
    ) -> tuple[Any, int] | None:
        """Reissue a length-truncated request once with a doubled output budget.

        ``budget_key`` is ``max_tokens`` for chat completions and
        ``max_output_tokens`` for the Responses API; ``truncation`` is the
        wire-level truncation marker for the log line. Returns
        ``(response, new_max_tokens)`` when a retry was sent, or ``None``
        when the budget is already at the cap (a retry would send the same
        request). ``kwargs`` is updated in place so any later retry in the
        same call keeps the enlarged budget.
        """
        new_budget = min(max(max_tokens, 1) * 2, _LENGTH_RETRY_MAX_TOKENS_CAP)
        if new_budget <= max_tokens:
            return None
        logger.warning(
            "%s hit %s with %s=%s; retrying with %s=%s",
            self._provider_name,
            truncation,
            budget_key,
            max_tokens,
            budget_key,
            new_budget,
        )
        kwargs[budget_key] = new_budget
        return await send(kwargs), new_budget

    def _empty_content_error(self, choice: Any) -> LLMResponseError:
        reasoning = self._reasoning_like_content(getattr(choice, "message", None))
        if reasoning:
            finish_reason = str(getattr(choice, "finish_reason", "") or "unknown")
            return LLMResponseError(
                f"{self._provider_name} returned reasoning but no final content "
                f"(finish_reason={finish_reason}); "
                "disable thinking/reasoning or increase max_tokens"
            )
        return LLMResponseError(f"{self._provider_name} returned empty content")

    @classmethod
    def _reasoning_like_content(cls, message: object) -> str:
        for field_name in ("reasoning_content", "reasoning", "thinking"):
            value = cls._read_message_field(message, field_name)
            if str(value or "").strip():
                return str(value)
        return ""

    @staticmethod
    def _read_message_field(message: object, field_name: str) -> object:
        if isinstance(message, dict):
            return message.get(field_name)
        value = getattr(message, field_name, None)
        if value is not None:
            return value
        extra = getattr(message, "model_extra", None)
        if isinstance(extra, dict):
            return extra.get(field_name)
        return None


# DeepSeek's ``max_tokens`` caps thinking + response combined. With
# ``reasoning_effort="max"`` the thinking stream alone can burn tens of
# thousands of tokens before any ``content`` is emitted, which causes the
# response to end with ``content=""`` and our provider to raise
# LLMResponseError. These floors ensure callers that passed a small
# ``max_tokens`` (our codebase default is 4096) still leave enough
# headroom for the reasoning phase to finish. DeepSeek's documented
# ceiling is 64K.
_DEEPSEEK_THINKING_MAX_TOKENS_FLOOR = {
    "max": 32768,
    "high": 16384,
}


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek provider (OpenAI-compatible API).

    Supports the v4 ``thinking`` mode via ``reasoning_effort``. When
    ``reasoning_effort`` is set (``"medium"``, ``"high"`` or ``"max"``), requests are
    sent with ``thinking={"type": "enabled"}`` and the requested effort
    level as top-level body fields (the DeepSeek API accepts both
    schemas).
    """

    # DeepSeek's API does not expose an embeddings endpoint. The
    # inherited ``embed()`` would 404 at call time, which used to
    # silently break the recommendation pipeline for DeepSeek users
    # who never ran ``setup-embedding``. Marking it False makes
    # ``build_embedding_service`` fall back to ollama / gemini.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        *,
        base_url: str = "https://api.deepseek.com",
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="deepseek",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
        )
        self._reasoning_effort = reasoning_effort.strip()

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
        # v0.3.51+: per-call ``reasoning_effort`` override. ``None`` =
        # use provider default (configured in config.toml). Empty
        # string = explicitly disable thinking for this call (used by
        # structured tasks like discovery's eval_batch — observed in
        # 2026-05-05 logs as 8-16 min/batch with reasoning, expected
        # ~30s without).
        requested_effort = (
            reasoning_effort if reasoning_effort is not None else self._reasoning_effort
        ).strip()
        effort = self._normalize_deepseek_effort(requested_effort)
        if effort:
            floor = _DEEPSEEK_THINKING_MAX_TOKENS_FLOOR.get(effort, 16384)
            if max_tokens < floor:
                logger.debug(
                    "deepseek: bumping max_tokens from %s to %s for effort=%s",
                    max_tokens,
                    floor,
                    effort,
                )
                max_tokens = floor
        try:
            return await super().complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort=effort,
                model=model,
            )
        except LLMResponseError:
            if not effort:
                logger.warning("deepseek: empty content; retrying once")
                return await super().complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort="",
                    model=model,
                )
            # Max-effort reasoning occasionally burns through the entire
            # output budget before the model emits any ``content``. Retry
            # once with thinking disabled so structured pipelines get a
            # usable response instead of hard-failing.
            logger.warning(
                "deepseek: empty content with reasoning_effort=%s; retrying with thinking disabled",
                effort,
            )
            return await super().complete(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                reasoning_effort="",
                model=model,
            )

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
        # Same thinking-budget floor as ``complete``: DeepSeek's max_tokens
        # caps thinking + response combined, and a tool-calling turn still
        # needs headroom for the reasoning phase before it can emit
        # ``tool_calls``.
        requested_effort = (
            reasoning_effort if reasoning_effort is not None else self._reasoning_effort
        ).strip()
        effort = self._normalize_deepseek_effort(requested_effort)
        if effort:
            floor = _DEEPSEEK_THINKING_MAX_TOKENS_FLOOR.get(effort, 16384)
            if max_tokens < floor:
                logger.debug(
                    "deepseek: bumping max_tokens from %s to %s for effort=%s",
                    max_tokens,
                    floor,
                    effort,
                )
                max_tokens = floor
        return await super().complete_with_tools(
            messages,
            tools,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=effort,
            model=model,
        )

    def _extra_body(self, *, reasoning_effort: str | None = None) -> dict[str, Any]:
        requested = self._reasoning_effort if reasoning_effort is None else reasoning_effort
        effort = self._normalize_deepseek_effort(requested)
        if not effort:
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": effort,
        }

    @staticmethod
    def _normalize_deepseek_effort(effort: str) -> str:
        """Map portable levels to DeepSeek V4's native high/max ladder."""

        normalized = effort.strip().lower()
        if normalized in {"", "none"}:
            return ""
        if normalized in {"max", "xhigh"}:
            return "max"
        # DeepSeek documents low/medium as compatibility aliases for high.
        # Treat minimal and unknown future aliases as high rather than sending
        # a value that a strict relay may reject before it reaches DeepSeek.
        return "high"
