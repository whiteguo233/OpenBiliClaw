"""Opper provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class OpperProvider(OpenAIProvider):
    """Opper LLM gateway provider.

    Opper serves models from many providers behind one OpenAI-compatible
    endpoint. Model ids are pool names without a vendor prefix (e.g.
    ``claude-sonnet-4-6`` or ``gpt-5.5``); ``<provider>/<model>`` ids pin one
    route.

    Reasoning params are never sent: the gateway routes to models with and
    without a reasoning mode, and a route without one may reject the field.
    """

    # The adapter does not double as the embedding backend; configure
    # ``[llm.embedding]`` separately, like the other gateway adapters.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        base_url: str = "https://api.opper.ai/v3/compat",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="opper",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Avoid sending a vendor-specific parameter to arbitrary routes."""
        del model, effort
        return None
