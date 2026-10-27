"""API Route provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class ApiRouteProvider(OpenAIProvider):
    """Route multiple model vendors through API Route's OpenAI API."""

    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.5",
        base_url: str = "https://global.api-route.com/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="api_route",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Avoid sending a vendor-specific parameter to arbitrary routes."""
        del model, effort
        return None
