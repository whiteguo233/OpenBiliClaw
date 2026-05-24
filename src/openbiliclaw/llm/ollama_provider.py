"""Ollama LLM provider via OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import logging

import httpx

from .base import LLMProviderError, LLMResponse, LLMTimeoutError
from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


class OllamaProvider(OpenAIProvider):
    """Ollama provider using the local OpenAI-compatible endpoint.

    Inherits chat-completions support from OpenAIProvider via Ollama's
    ``/v1/chat/completions`` shim. Adds an ``embed()`` method that hits
    Ollama's *native* ``/api/embeddings`` endpoint — that route is more
    direct than the OpenAI-compat embedding shim and is the canonical
    integration point recommended by the Ollama docs.
    """

    # v0.3.54+: Ollama-specific extended retry. Production logs (2026-05-05)
    # showed 9× 502 Bad Gateway in the daemon's first 90s while Ollama was
    # loading bge-m3 from disk. The base OpenAIProvider retry (3 × 0.25s
    # linear = 1.25s total) was way too short — by the time the model
    # finished loading, the request had long failed. These constants give
    # ~30s total wait via exponential backoff, which absorbs cold-load
    # without delaying the steady-state path (where retries don't fire).
    _OLLAMA_MAX_RETRIES = 5
    _OLLAMA_BASE_RETRY_DELAY = 1.0

    def __init__(
        self,
        api_key: str = "ollama",
        model: str = "llama3",
        base_url: str = "http://localhost:11434/v1",
        timeout: float = 300.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="ollama",
            timeout=timeout,
        )
        self._embed_timeout = timeout

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
        """Chat completion with extended retry for Ollama startup hiccups.

        v0.3.54+: when Ollama is still loading models (most often during
        the daemon's first 60-90 seconds), ``/v1/chat/completions``
        returns 502 / 503 or times out. The base 3-retry × 0.25s policy
        burns through retries before the runtime is ready. Override here
        adds an exponential backoff loop on top: 1s, 2s, 4s, 8s, 16s ≈
        31s wall time, which covers cold-load without slowing down
        normal operation (retries don't fire when the model is warm).
        """
        last_error: Exception | None = None
        for attempt in range(1, self._OLLAMA_MAX_RETRIES + 1):
            try:
                return await super().complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    reasoning_effort=reasoning_effort,
                    model=model,
                )
            except (LLMProviderError, LLMTimeoutError, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self._OLLAMA_MAX_RETRIES:
                    break
                delay = self._OLLAMA_BASE_RETRY_DELAY * (2 ** (attempt - 1))
                logger.info(
                    "Ollama complete attempt %d/%d failed (%s); "
                    "retrying in %.1fs (likely model still loading)",
                    attempt,
                    self._OLLAMA_MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        # Exhausted all attempts — re-raise the last error so the
        # registry's fallback chain can route to the next provider.
        if last_error is None:  # pragma: no cover — defensive
            raise LLMProviderError("ollama: complete failed without exception")
        raise last_error

    def _native_root(self) -> str:
        """Strip the OpenAI-compat ``/v1`` suffix to reach Ollama's native API root."""
        return self.base_url.rstrip("/").rsplit("/v1", 1)[0]

    async def embed(self, text: str, *, model: str = "bge-m3") -> list[float]:
        """Get text embedding via Ollama's native ``/api/embeddings`` endpoint.

        Recommended local fallback model is ``bge-m3`` (multilingual,
        1024-dim). Other Ollama embedding models also work — just pass
        ``model=...``.

        Retries once on transient errors (timeout / connection drop /
        Ollama runner restart). Returns an empty list only after both
        attempts fail. Callers (EmbeddingService) treat empty vectors
        as "no embedding" and skip caching them.
        """
        url = f"{self._native_root()}/api/embeddings"
        last_exc: Exception | None = None
        # 1 initial + 1 retry. The retry covers brief Ollama hiccups
        # (model swap, runner restart, momentary OOM) without making a
        # transient failure poison the user's experience for several
        # minutes. Two attempts is enough — if the second also fails,
        # something structural is wrong and adding more retries just
        # delays the inevitable WARN.
        for attempt in (1, 2):
            try:
                # trust_env=False bypasses the user's HTTP_PROXY / HTTPS_PROXY env
                # vars, which would otherwise route localhost embedding calls
                # through e.g. a 127.0.0.1:7897 VPN proxy and time out.
                #
                # 120s timeout absorbs (a) the initial bge-m3 cold-load (~10-30s
                # from disk on first call after Ollama wake) and (b) brief
                # request-queue backlog when EmbeddingService throttles to
                # concurrency=2 but the daemon enqueued >2 cache-miss texts
                # within seconds. 60s was too tight under the post-proxy-fix
                # cache-rebuild burst.
                async with httpx.AsyncClient(
                    timeout=self._embed_timeout,
                    trust_env=False,
                ) as client:
                    response = await client.post(
                        url,
                        json={"model": model, "prompt": text},
                    )
                    response.raise_for_status()
                    data = response.json()
                vec = data.get("embedding")
                if not isinstance(vec, list):
                    return []
                return [float(v) for v in vec if isinstance(v, int | float)]
            except Exception as exc:
                last_exc = exc
                if attempt == 1:
                    logger.debug(
                        "Ollama embedding attempt 1 failed (model=%s), retrying",
                        model,
                        exc_info=True,
                    )

        logger.warning(
            "Ollama embedding failed after 2 attempts (model=%s, url=%s)",
            model,
            url,
            exc_info=last_exc,
        )
        return []
