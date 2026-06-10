"""OpenAI-compatible LLM provider.

Supports OpenAI API and any compatible APIs (e.g. DeepSeek, local vLLM).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

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

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


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
        timeout: float = 300.0,
        embedding_output_dimensionality: int = 0,
    ) -> None:
        self._model = model
        self._provider_name = provider_name
        self.base_url = base_url or ""
        self._token_provider = token_provider
        self._timeout = timeout
        self._embedding_output_dimensionality = max(0, int(embedding_output_dimensionality or 0))
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=0,
            timeout=timeout,
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
        # ``reasoning_effort`` is consumed by ``DeepSeekProvider``; the
        # base OpenAI provider accepts it for signature compatibility
        # but doesn't act on it (vanilla GPT-4o has no thinking knob).
        del reasoning_effort
        effective_model = (model or "").strip() or self._model
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            fmt = self._json_response_format()
            if fmt is not None:
                kwargs["response_format"] = fmt
        extra_headers = self._extra_headers()
        if extra_headers:
            kwargs["extra_headers"] = extra_headers
        extra_body = self._extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            response = await self._request_with_retry(**kwargs)
        except LLMProviderError as exc:
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
                response = await self._request_with_retry(**kwargs)
            else:
                raise
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
                await self._apply_dynamic_token(force_refresh=False)
                return await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                if self._is_unauthorized(exc) and self._token_provider is not None:
                    try:
                        await self._apply_dynamic_token(force_refresh=True)
                        return await self._client.chat.completions.create(**kwargs)
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
        body_excerpt = self._provider_error_body_excerpt(exc)
        message = str(exc).lower()
        if status_code == 429 or "rate limit" in message or "too many requests" in message:
            return LLMRateLimitError(f"{self._provider_name} rate limit exceeded")
        if status_code and int(status_code) >= 500:
            return LLMProviderError(f"{self._provider_name} server error: {status_code}")
        if status_code and body_excerpt:
            logger.warning(
                "%s request failed with HTTP %s: %s",
                self._provider_name,
                status_code,
                body_excerpt,
            )
            return LLMProviderError(
                f"{self._provider_name} request failed: HTTP {status_code}: {body_excerpt}"
            )

        return LLMProviderError(f"{self._provider_name} request failed: {exc}")

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
        if isinstance(exc, LLMRateLimitError):
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

    def _supports_embedding_dimensions(self, model: str) -> bool:
        if not model.startswith("text-embedding-3-"):
            return False
        return self._provider_name == "openai"

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
        timeout: float = 300.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
            timeout=timeout,
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
        previous_effort = self._reasoning_effort
        applied_effort = reasoning_effort if reasoning_effort is not None else previous_effort
        # Temporarily mutate the instance attribute so ``_extra_body``
        # and the empty-content retry path see the per-call value.
        self._reasoning_effort = applied_effort
        try:
            effort = applied_effort
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
                        model=model,
                    )
                # Max-effort reasoning occasionally burns through the entire
                # output budget before the model emits any ``content``. Retry
                # once with thinking disabled so structured pipelines get a
                # usable response instead of hard-failing.
                logger.warning(
                    "deepseek: empty content with reasoning_effort=%s; "
                    "retrying with thinking disabled",
                    effort,
                )
                self._reasoning_effort = ""
                return await super().complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    model=model,
                )
        finally:
            self._reasoning_effort = previous_effort

    def _extra_body(self) -> dict[str, Any]:
        if not self._reasoning_effort:
            return {}
        return {
            "thinking": {"type": "enabled"},
            "reasoning_effort": self._reasoning_effort,
        }
