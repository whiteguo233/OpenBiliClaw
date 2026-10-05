"""OrcaRouter provider built on the OpenAI-compatible client."""

from __future__ import annotations

from typing import Any

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class OrcaRouterProvider(OpenAIProvider):
    """OrcaRouter model-routing gateway provider.

    OrcaRouter exposes 150+ models from OpenAI, Anthropic, Google, DeepSeek,
    Qwen, MiniMax, xAI and others behind a single OpenAI-compatible endpoint
    and API key (``sk-orca-``).

    Reasoning params are intentionally never sent. The gateway forwards
    ``reasoning_effort`` (and OpenRouter's nested ``reasoning`` object)
    verbatim to the upstream route, and upstream models that don't expose
    reasoning reject the argument with HTTP 400 (verified against
    ``openai/gpt-4o``). Reasoning-capable routes pick up their own model
    default when the field is omitted, so leaving it off is safe for both
    families. ``_openai_reasoning_effort`` therefore always returns ``None``
    and ``_extra_body`` stays empty.
    """

    # OrcaRouter routes most chat models, but its embeddings coverage is
    # spotty per-route — fall back to ollama / gemini by default, matching
    # the OpenRouter adapter's behavior.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o",
        base_url: str = "https://api.orcarouter.ai/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="orcarouter",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Never emit ``reasoning_effort`` on the wire.

        OrcaRouter forwards the scalar to the upstream route, which rejects
        it (HTTP 400) when the model has no reasoning mode. Reasoning-capable
        models fall back to their own default when the field is omitted.
        """
        del model, effort
        return None

    def _extra_body(self, *, reasoning_effort: str | None = None) -> dict[str, Any]:
        """No provider-specific request body fields.

        OrcaRouter rejects OpenRouter's nested ``reasoning`` object on
        non-reasoning routes, so nothing is added here.
        """
        del reasoning_effort
        return {}
