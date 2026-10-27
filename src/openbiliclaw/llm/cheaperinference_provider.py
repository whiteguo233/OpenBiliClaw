"""Cheaper Inference provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class CheaperInferenceProvider(OpenAIProvider):
    """Cheaper Inference LLM gateway provider.

    Cheaper Inference serves models from several labs behind one
    OpenAI-compatible endpoint. Model ids are bare names without a vendor
    prefix (e.g. ``gpt-5.4-mini`` or ``claude-sonnet-5``).

    Reasoning params are never sent: the gateway routes to models with and
    without a reasoning mode, and a route without one may reject the field.
    """

    # The gateway does not double as the embedding backend; configure
    # ``[llm.embedding]`` separately, like the other gateway adapters.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-mini",
        base_url: str = "https://api.cheaperinference.com/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="cheaperinference",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Avoid sending a vendor-specific parameter to arbitrary routes."""
        del model, effort
        return None

    async def list_models(self) -> list[str]:
        """List chat model ids only.

        ``GET /models`` also lists image and video models. Their rows carry a
        ``type`` other than ``"text"`` and cannot serve chat, so they are
        dropped. Rows without a ``type`` are kept.
        """

        page = await self._send_with_retry(self._create_model_list)
        identifiers = {
            str(getattr(item, "id", "") or "").strip()
            for item in (getattr(page, "data", None) or [])
            if str(getattr(item, "type", None) or "text").strip().lower() == "text"
        }
        return sorted((identifier for identifier in identifiers if identifier), key=str.casefold)
