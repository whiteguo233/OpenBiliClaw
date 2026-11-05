"""Runcrate provider built on the OpenAI-compatible client."""

from __future__ import annotations

from .base import DEFAULT_REASONING_EFFORT
from .openai_provider import OpenAIProvider


class RuncrateProvider(OpenAIProvider):
    """Runcrate inference provider.

    Runcrate serves open-weight models from several labs behind one
    OpenAI-compatible endpoint. Model ids carry the vendor prefix
    (e.g. ``deepseek-ai/DeepSeek-V4.1-Flash`` or ``zai-org/GLM-5.3``).

    Reasoning params are never sent: the catalog mixes models with and
    without a reasoning mode, and a model without one may reject the field.
    """

    # Chat instances do not double as the embedding backend; configure
    # ``[llm.embedding]`` separately, like the other gateway adapters.
    supports_embedding = False

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-ai/DeepSeek-V4.1-Flash",
        base_url: str = "https://api.runcrate.ai/v1",
        timeout: float = 1200.0,
        proxy: str = "",
        trust_env: bool = True,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider_name="runcrate",
            timeout=timeout,
            proxy=proxy,
            trust_env=trust_env,
            reasoning_effort=reasoning_effort,
        )

    def _openai_reasoning_effort(self, model: str, effort: str) -> str | None:
        """Avoid sending a model-specific parameter to arbitrary models."""
        del model, effort
        return None

    async def list_models(self) -> list[str]:
        """List chat model ids only.

        ``GET /models`` also lists image, video, audio and embedding models.
        Their rows carry ``output_modalities`` without ``"text"`` and cannot
        serve chat, so they are dropped. Rows without the field are kept.
        """

        page = await self._send_with_retry(self._create_model_list)
        identifiers = {
            str(getattr(item, "id", "") or "").strip()
            for item in (getattr(page, "data", None) or [])
            if "text"
            in [
                str(modality).strip().lower()
                for modality in (getattr(item, "output_modalities", None) or ["text"])
            ]
        }
        return sorted((identifier for identifier in identifiers if identifier), key=str.casefold)
