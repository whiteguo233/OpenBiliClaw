"""Atlas Cloud provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class AtlasCloudProvider(OpenAIProvider):
    """Atlas Cloud LLM gateway provider.

    Atlas Cloud serves models from several labs behind one OpenAI-compatible
    endpoint. Model ids carry a vendor prefix (e.g.
    ``deepseek-ai/deepseek-v4-flash`` or ``moonshotai/kimi-k2.6``).

    Reasoning params are never sent: the gateway only accepts a subset of
    ``reasoning_effort`` values across its routes, and routes without a
    reasoning mode may reject the field.
    """

    # The gateway has no ``/embeddings`` endpoint; configure
    # ``[llm.embedding]`` separately, like the other gateway adapters.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-ai/deepseek-v4-flash",
        base_url: str = "https://api.atlascloud.ai/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="atlascloud",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Avoid sending a vendor-specific parameter to arbitrary routes."""
        del model, effort
        return None
