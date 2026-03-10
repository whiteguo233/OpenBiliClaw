"""Gemini Developer API provider built on the official google-genai SDK."""

from __future__ import annotations

import asyncio
from typing import Any

from google import genai
from google.genai import errors, types

from .base import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)


class GeminiProvider(LLMProvider):
    """Gemini provider using the official Gemini Developer API client."""

    _MAX_RETRIES = 3
    _BASE_RETRY_DELAY = 0.25

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._model = model
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return "gemini"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
        )
        response = await self._request_with_retry(
            model=self._model,
            contents=self._render_messages(messages),
            config=config,
        )

        content = response.text or ""
        if not content.strip():
            raise LLMResponseError("gemini returned empty content")

        usage = None
        if response.usage_metadata is not None:
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                "total_tokens": response.usage_metadata.total_token_count or 0,
            }

        return LLMResponse(
            content=content,
            model=response.model_version or self._model,
            provider="gemini",
            usage=usage,
            raw=response,
        )

    async def _request_with_retry(self, **kwargs: Any) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await self._client.aio.models.generate_content(**kwargs)
            except Exception as exc:
                mapped = self._map_error(exc)
                last_error = mapped
                if not self._is_retryable(mapped) or attempt == self._MAX_RETRIES:
                    raise mapped from exc
                await asyncio.sleep(self._BASE_RETRY_DELAY * attempt)

        if last_error is None:
            raise LLMProviderError("gemini request failed")
        raise last_error

    def _map_error(self, exc: Exception) -> LLMProviderError:
        if isinstance(exc, LLMProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError("gemini request timed out")

        status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        message = (getattr(exc, "message", None) or str(exc)).lower()
        if status_code == 429 or "rate limit" in message or "resource_exhausted" in message:
            return LLMRateLimitError("gemini rate limit exceeded")
        if isinstance(exc, errors.ServerError) or (status_code and int(status_code) >= 500):
            return LLMProviderError(f"gemini server error: {status_code}")
        return LLMProviderError(f"gemini request failed: {exc}")

    def _is_retryable(self, exc: LLMProviderError) -> bool:
        return isinstance(exc, (LLMProviderError, LLMRateLimitError, LLMTimeoutError))

    def _render_messages(self, messages: list[dict[str, str]]) -> str:
        chunks: list[str] = []
        for message in messages:
            content = message["content"].strip()
            if not content:
                continue
            role = message["role"].upper()
            chunks.append(f"[{role}]\n{content}")
        return "\n\n".join(chunks)
