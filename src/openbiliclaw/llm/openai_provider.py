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
    ) -> None:
        self._model = model
        self._provider_name = provider_name
        self.base_url = base_url or ""
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=0,
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
        extra_body = self._extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

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
        if isinstance(exc, LLMRateLimitError):
            return False
        return isinstance(exc, (LLMProviderError, LLMTimeoutError))

    async def embed(self, text: str, *, model: str = "text-embedding-3-small") -> list[float]:
        """Get text embedding via OpenAI's ``/v1/embeddings`` endpoint.

        Returns an empty list on failure so callers can degrade
        gracefully (the embedding service treats empty vectors as
        "no embedding"). This matches the contract Gemini/Ollama
        providers already follow.
        """
        try:
            response = await self._client.embeddings.create(
                model=model,
                input=text,
            )
            return list(response.data[0].embedding)
        except Exception:
            logger.warning(
                "%s embedding failed (model=%s)",
                self._provider_name,
                model,
                exc_info=True,
            )
            return []

    def _extra_headers(self) -> dict[str, str]:
        """Return optional provider-specific request headers."""
        return {}

    def _extra_body(self) -> dict[str, Any]:
        """Return optional provider-specific request body fields.

        Used for non-standard keys like DeepSeek's ``thinking`` and
        ``reasoning_effort``. Keys returned here are passed verbatim via
        ``extra_body`` of the OpenAI SDK.
        """
        return {}


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
    ``reasoning_effort`` is set (``"high"`` or ``"max"``), requests are
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
        reasoning_effort: str = "",
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
        )
        self._reasoning_effort = reasoning_effort.strip()

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        effort = self._reasoning_effort
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
            )
        except LLMResponseError:
            if not effort:
                raise
            # Max-effort reasoning occasionally burns through the entire
            # output budget before the model emits any ``content``. Retry
            # once with thinking disabled so structured pipelines get a
            # usable response instead of hard-failing.
            logger.warning(
                "deepseek: empty content with reasoning_effort=%s; retrying with thinking disabled",
                effort,
            )
            self._reasoning_effort = ""
            try:
                return await super().complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            finally:
                self._reasoning_effort = effort

    def _extra_body(self) -> dict[str, Any]:
        if not self._reasoning_effort:
            return {}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": self._reasoning_effort,
        }
