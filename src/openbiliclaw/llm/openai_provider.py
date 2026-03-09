"""OpenAI-compatible LLM provider.

Supports OpenAI API and any compatible APIs (e.g. DeepSeek, local vLLM).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI

from .base import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI and compatible API provider."""
    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = "",
        provider_name: str = "openai",
    ) -> None:
        self._model = model
        self._provider_name = provider_name
        self.base_url = base_url or ""
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
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
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        response = await self._request_with_retry(**kwargs)
        choice = response.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            raise LLMResponseError(f"{self._provider_name} returned empty content")

        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=content,
            model=response.model,
            provider=self._provider_name,
            usage=usage,
            raw=response,
        )

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        """Send a request with bounded retry for transient failures."""
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc

                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError(f"{self._provider_name} request failed")
        raise last_error

    def _map_error(self, exc: Exception) -> LLMProviderError:
        """Map provider or network exceptions into shared provider errors."""
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError(f"{self._provider_name} request timed out")

        status_code = getattr(exc, "status_code", None)
        message = str(exc).lower()
        if status_code == 429 or "rate limit" in message or "too many requests" in message:
            return LLMRateLimitError(f"{self._provider_name} rate limit exceeded")
        if status_code and int(status_code) >= 500:
            return LLMProviderError(f"{self._provider_name} server error: {status_code}")

        return LLMProviderError(f"{self._provider_name} request failed: {exc}")

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        """Whether a mapped exception should be retried."""
        return isinstance(exc, (LLMProviderError, LLMRateLimitError, LLMTimeoutError))

    def _extra_headers(self) -> dict[str, str]:
        """Return optional provider-specific request headers."""
        return {}


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek provider (OpenAI-compatible API)."""

    def __init__(self, api_key: str, model: str = "deepseek-chat") -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
        )
