"""Anthropic Claude LLM provider."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

import httpx
from anthropic import AsyncAnthropic

from .base import (
    DEFAULT_REASONING_EFFORT,
    LLMAuthError,
    LLMInvalidRequestError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)

if TYPE_CHECKING:
    from anthropic.types import Message, MessageParam

logger = logging.getLogger(__name__)

# Claude's 5.5 generation dropped sampling controls (``temperature`` /
# ``top_p`` / ``top_k``): sending ``temperature`` fails with HTTP 400
# "``temperature`` is deprecated for this model" and the whole call is lost
# (issue #355). Markers rather than an allowlist, so every legacy model keeps
# its historical request body. A model missing here is learned at call time —
# see ``ClaudeProvider._create_message``.
_SAMPLING_UNSUPPORTED_MARKERS: tuple[str, ...] = (
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-5",
    "claude-mythos-5-5",
)
# Normalized model names that already answered 400 for sampling parameters in
# this process. Bounded by the number of distinct models a user configures.
_SAMPLING_UNSUPPORTED_MODELS: set[str] = set()


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider."""

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 1200.0,
        base_url: str = "",
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort.strip()
        self.base_url = base_url.strip()
        # Overseas routing policy mirrors OpenAIProvider.
        self._proxy = proxy.strip()
        self._trust_env = bool(trust_env and not self._proxy)
        client_kwargs: dict[str, Any] = {}
        if self._proxy or not self._trust_env:
            httpx_kwargs: dict[str, Any] = {"timeout": timeout, "trust_env": self._trust_env}
            if self._proxy:
                httpx_kwargs["proxy"] = self._proxy
            client_kwargs["http_client"] = httpx.AsyncClient(**httpx_kwargs)
        # Empty base_url → SDK default (https://api.anthropic.com). A custom
        # value points at any Anthropic-protocol gateway serving /v1/messages
        # (third-party relays, LiteLLM, etc.) — see issue #72.
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=timeout,
            base_url=self.base_url or None,
            **client_kwargs,
        )

    @property
    def name(self) -> str:
        return "claude"

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
        effective_model = (model or "").strip() or self._model
        # Extract system message if present
        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        # v0.3.29+: Anthropic prompt-cache integration. Claude requires
        # explicit ``cache_control: {"type": "ephemeral"}`` markers on
        # the message blocks we want cached — pure plain-string
        # ``system="..."`` is never cached. We always mark the system
        # block as cacheable; Anthropic silently ignores the marker if
        # the system text is below the per-model min (1024 tok Sonnet,
        # 2048 tok Haiku/Opus), so this is safe for short prompts too.
        # Cache hit gets billed at 10% of input rate; the first call
        # writes cache at +25% surcharge, then 5min TTL on reads. The
        # system_param goes through ``_render_system_param`` which the
        # tests can override.
        system_text = system or "You are a helpful assistant."
        system_param: Any = self._render_system_param(system_text)

        request_kwargs: dict[str, Any] = {
            "model": effective_model,
            "max_tokens": max_tokens,
            "system": system_param,
            "messages": chat_messages,
            # Portable intent only: ``_build_request_kwargs`` drops it for
            # models whose generation rejects sampling controls (issue #355).
            "temperature": temperature,
        }
        effort = self._claude_effort(effective_model, reasoning_effort)
        if effort is not None:
            request_kwargs["output_config"] = {"effort": effort}

        response = cast(
            "Message",
            await self._request_with_retry(**request_kwargs),
        )

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        if not content.strip():
            raise LLMResponseError("claude returned empty content")

        # Claude exposes cache fields when prompt-cache is in use:
        # cache_read_input_tokens (90% off) + cache_creation_input_tokens
        # (+25% surcharge). We surface them under the universal
        # ``cached_input_tokens`` / ``cache_creation_input_tokens`` keys
        # so downstream pricing / observability is provider-agnostic.
        cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        cache_create = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
        usage_dict = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }
        if cache_read:
            usage_dict["cached_input_tokens"] = cache_read
        if cache_create:
            usage_dict["cache_creation_input_tokens"] = cache_create
        return LLMResponse(
            content=content,
            model=response.model,
            provider="claude",
            usage=usage_dict,
            raw=response,
        )

    def _render_system_param(self, system_text: str) -> Any:
        """Wrap the system prompt in Anthropic's prompt-cache shape.

        The Claude API accepts ``system`` as either a plain string or a
        list of typed blocks; only the latter form supports
        ``cache_control``. We always emit the list form with an
        ``ephemeral`` cache marker on the system block. If the system
        text is below the per-model minimum (1024 tok Sonnet / 2048
        tok Haiku/Opus), Anthropic silently ignores the marker rather
        than erroring, so this is safe regardless of size.
        """
        return [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _claude_effort(self, model: str, requested: str | None) -> str | None:
        """Map portable effort onto Claude models that expose output_config."""

        if not self._supports_effort(model):
            return None
        effort = self._reasoning_effort if requested is None else requested.strip().lower()
        if effort in {"", "none", "minimal"}:
            return "low"
        # ``xhigh`` is not present in every installed Anthropic SDK/model and
        # ``max`` is model-specific.  High is the safe portable upper level.
        if effort in {"max", "xhigh"}:
            return "high"
        if effort in {"low", "medium", "high"}:
            return effort
        return DEFAULT_REASONING_EFFORT

    @staticmethod
    def _normalized_model_name(model: str) -> str:
        """Normalize provider model ids before capability lookups."""
        return model.strip().lower().replace(".", "-")

    @staticmethod
    def _supports_effort(model: str) -> bool:
        name = ClaudeProvider._normalized_model_name(model)
        return any(
            marker in name
            for marker in (
                "claude-fable-5",
                "claude-mythos-5",
                "claude-mythos-preview",
                "claude-opus-4-5",
                "claude-opus-4-6",
                "claude-opus-4-7",
                "claude-opus-4-8",
                "claude-sonnet-4-6",
                "claude-sonnet-5",
            )
        )

    @staticmethod
    def _supports_sampling(model: str) -> bool:
        """Whether ``temperature`` may be sent for this model.

        The 5.5 generation rejects sampling controls outright (issue #355).
        Unknown names keep the historical behaviour — temperature is sent —
        and a 400 at call time flips the model off for the rest of the
        process, so a brand-new family only pays one failed request.
        """
        name = ClaudeProvider._normalized_model_name(model)
        if name in _SAMPLING_UNSUPPORTED_MODELS:
            return False
        return not any(marker in name for marker in _SAMPLING_UNSUPPORTED_MARKERS)

    @staticmethod
    def _is_sampling_rejection(exc: Exception) -> bool:
        """Whether ``exc`` is Anthropic's 400 for deprecated sampling params."""
        message = str(exc).lower()
        if "temperature" not in message and "sampling" not in message:
            return False
        # Official wording is "deprecated"; relays (LiteLLM and friends)
        # often say "unsupported parameter" instead.
        return any(
            marker in message
            for marker in ("deprecated", "unsupported", "not supported", "does not support")
        )

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        """Send a request with bounded retry for transient failures."""
        last_error: Exception | None = None
        model = cast("str", kwargs["model"])

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._create_message(kwargs, model=model)
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc

                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError("claude request failed")
        raise last_error

    def _build_request_kwargs(
        self,
        kwargs: dict[str, Any],
        *,
        include_temperature: bool,
    ) -> dict[str, Any]:
        """Assemble one Anthropic request body.

        Single source of truth for the first attempt and the retry path:
        they used to be rebuilt in two places, which is how ``temperature``
        stayed unconditional on both of them (issue #355).
        """
        request_kwargs: dict[str, Any] = {
            "model": cast("str", kwargs["model"]),
            "max_tokens": cast("int", kwargs["max_tokens"]),
            "system": kwargs["system"],
            "messages": cast("list[MessageParam]", kwargs["messages"]),
        }
        if include_temperature:
            request_kwargs["temperature"] = cast("float", kwargs["temperature"])
        if "output_config" in kwargs:
            request_kwargs["output_config"] = kwargs["output_config"]
        return request_kwargs

    async def _create_message(self, kwargs: dict[str, Any], *, model: str) -> Any:
        """Send one Messages request, dropping sampling controls if refused.

        ``_supports_sampling`` covers the documented generations; this
        fallback covers a model released after this build. The rejection is
        remembered in ``_SAMPLING_UNSUPPORTED_MODELS`` so later calls skip
        the doomed first attempt entirely.
        """
        include_temperature = self._supports_sampling(model)
        try:
            return await self._client.messages.create(
                **self._build_request_kwargs(kwargs, include_temperature=include_temperature)
            )
        except Exception as exc:
            if not include_temperature or not self._is_sampling_rejection(exc):
                raise
            _SAMPLING_UNSUPPORTED_MODELS.add(self._normalized_model_name(model))
            logger.warning(
                "claude model %r rejected sampling parameters; retrying without temperature: %s",
                model,
                exc,
            )
            return await self._client.messages.create(
                **self._build_request_kwargs(kwargs, include_temperature=False)
            )

    def _map_error(self, exc: Exception) -> LLMProviderError:
        """Map Anthropic or network errors into shared provider errors."""
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError("claude request timed out")

        message = str(exc).lower()
        if "rate limit" in message or "too many requests" in message:
            return LLMRateLimitError("claude rate limit exceeded")
        status_code = getattr(exc, "status_code", None)
        if status_code == 401:
            logger.warning("claude rejected our credentials with HTTP 401: %s", exc)
            return LLMAuthError(
                f"claude authentication failed: HTTP 401: {exc}",
                provider_name="claude",
                endpoint=self.base_url,
            )
        # 4xx other than 408/409/429 means the request itself is wrong
        # (bad model id, deprecated parameter, context too long). Retrying
        # the identical body cannot succeed, so keep it terminal instead of
        # burning three round trips. 429 is already handled above; 408/409
        # stay transient.
        if (
            isinstance(status_code, int)
            and 400 <= status_code < 500
            and status_code not in {408, 409, 429}
        ):
            logger.warning("claude rejected the request with HTTP %s: %s", status_code, exc)
            return LLMInvalidRequestError(f"claude rejected the request: HTTP {status_code}: {exc}")

        return LLMProviderError(f"claude request failed: {exc}")

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        """Whether a mapped exception should be retried."""
        # See OpenAIProvider._is_retryable: a 401 is terminal until the user
        # fixes their key, and a 4xx means the request body is wrong, so
        # retrying only delays the actionable error.
        if isinstance(exc, (LLMRateLimitError, LLMAuthError, LLMInvalidRequestError)):
            return False
        return isinstance(exc, (LLMProviderError, LLMTimeoutError))
