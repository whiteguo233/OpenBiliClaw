"""OpenRouter provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider with optional attribution headers."""

    # OpenRouter routes most chat models, but its embeddings coverage is
    # spotty per-route — better to fall back to ollama / gemini by default
    # than to surprise users with mid-pipeline 404s. Users who want
    # OpenRouter embedding can set ``[llm.embedding] provider="openrouter"``
    # with an explicit ``<vendor>/<model>`` (e.g.
    # ``google/gemini-embedding-2-preview``); that dedicated path lives in
    # ``registry._build_dedicated_embedding_provider`` and does not
    # consult this flag.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1",
        http_referer: str = "",
        x_title: str = "",
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="openrouter",
        )
        self._http_referer = http_referer
        self._x_title = x_title

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._http_referer.strip():
            headers["HTTP-Referer"] = self._http_referer
        if self._x_title.strip():
            headers["X-Title"] = self._x_title
        return headers
