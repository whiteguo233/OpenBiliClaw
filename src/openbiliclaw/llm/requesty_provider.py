"""Requesty provider built on the OpenAI-compatible client."""

from __future__ import annotations

import logging
from typing import Any

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


class RequestyProvider(OpenAIProvider):
    """Requesty LLM gateway provider.

    Requesty routes models from OpenAI, Anthropic, Google, DeepSeek and others
    behind a single OpenAI-compatible endpoint. Model ids use the
    ``<vendor>/<model>`` form (e.g. ``openai/gpt-4o-mini``) or a managed
    policy id from ``GET /v1/models/managed`` (e.g. ``claude-sonnet-4-5``).

    Reasoning params are intentionally never sent: the gateway forwards
    ``reasoning_effort`` verbatim to the upstream route, where models without
    a reasoning mode may reject it. Reasoning-capable routes pick up their
    own model default when the field is omitted, so leaving it off is safe
    for both families. ``_openai_reasoning_effort`` therefore always returns
    ``None``.
    """

    # Requesty chat traffic does not double as the embedding backend; fall
    # back to ollama / gemini by default, matching the OpenRouter adapter.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://router.requesty.ai/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="requesty",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Never emit ``reasoning_effort`` on the wire.

        Requesty forwards the scalar to the upstream route, which can reject
        it when the model has no reasoning mode. Reasoning-capable models
        fall back to their own default when the field is omitted.
        """
        del model, effort
        return None

    async def _create_managed_model_list(self) -> Any:
        return await self._client.get("/models/managed", cast_to=object)

    async def list_models(self) -> list[str]:
        """List managed policy ids first, then the rest of the model catalog.

        ``GET /models/managed`` is Requesty's curated list of routing policies.
        It is best effort: when it fails, the regular ``GET /models`` catalog
        (which also validates the key) is still returned.
        """

        managed: list[str] = []
        try:
            payload = await self._send_with_retry(self._create_managed_model_list)
        except Exception:
            logger.warning("requesty managed model listing failed", exc_info=True)
        else:
            data = payload.get("data") if isinstance(payload, dict) else None
            managed = sorted(
                {
                    str(item.get("id") or "").strip()
                    for item in (data or [])
                    if isinstance(item, dict) and item.get("api", "chat") == "chat"
                }
                - {""},
                key=str.casefold,
            )
        catalog = await super().list_models()
        seen = set(managed)
        return [*managed, *(model for model in catalog if model not in seen)]
