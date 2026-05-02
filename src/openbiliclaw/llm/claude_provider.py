"""Anthropic Claude LLM provider."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from anthropic import AsyncAnthropic

from .base import (
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


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider."""
    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514") -> None:
        self._model = model
        self._client = AsyncAnthropic(api_key=api_key)

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
    ) -> LLMResponse:
        # Extract system message if present
        system = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)

        response = cast(
            "Message",
            await self._request_with_retry(
                model=self._model,
                max_tokens=max_tokens,
                system=system or "You are a helpful assistant.",
                messages=chat_messages,
                temperature=temperature,
            ),
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

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        """Send a request with bounded retry for transient failures."""
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._client.messages.create(
                    model=cast("str", kwargs["model"]),
                    max_tokens=cast("int", kwargs["max_tokens"]),
                    system=cast("str", kwargs["system"]),
                    messages=cast("list[MessageParam]", kwargs["messages"]),
                    temperature=cast("float", kwargs["temperature"]),
                )
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc

                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError("claude request failed")
        raise last_error

    def _map_error(self, exc: Exception) -> LLMProviderError:
        """Map Anthropic or network errors into shared provider errors."""
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError("claude request timed out")

        message = str(exc).lower()
        if "rate limit" in message or "too many requests" in message:
            return LLMRateLimitError("claude rate limit exceeded")

        return LLMProviderError(f"claude request failed: {exc}")

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        """Whether a mapped exception should be retried."""
        if isinstance(exc, LLMRateLimitError):
            return False
        return isinstance(exc, (LLMProviderError, LLMTimeoutError))
