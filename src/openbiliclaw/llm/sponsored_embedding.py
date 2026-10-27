"""Text and image vectors in one signed model space; never mix fallback vectors."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from .sponsored_errors import SponsoredUnavailableError
from .sponsored_operations import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from .sponsored_router import shared_sponsored_provider

if TYPE_CHECKING:
    from openbiliclaw.config import Config

    from .embedding import EmbeddingService


class SponsoredEmbeddingAdapter:
    name = "sponsored"
    _base_url = "https://api.siliconflow.cn/v1"
    supports_image_embedding = True

    @staticmethod
    def is_multimodal_embedding_model(model: str) -> bool:
        return model == EMBEDDING_MODEL

    async def embed(self, text: str, *, model: str = EMBEDDING_MODEL) -> list[float]:
        if model != EMBEDDING_MODEL:
            raise ValueError("embedding model is fixed by the signed contract")
        provider = shared_sponsored_provider()
        if provider is None:
            raise SponsoredUnavailableError("sponsored embedding runtime unavailable")
        return list(await provider.embedding(text))

    async def embed_image(
        self, image_bytes: bytes, *, mime_type: str = "image/jpeg", model: str = EMBEDDING_MODEL
    ) -> list[float]:
        if model != EMBEDDING_MODEL:
            raise ValueError("embedding model is fixed by the signed contract")
        provider = shared_sponsored_provider()
        if provider is None:
            raise SponsoredUnavailableError("sponsored embedding runtime unavailable")
        image = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
        return list(await provider.embedding(image, image=True))


def build_sponsored_embedding_service(config: Config) -> EmbeddingService:
    """Use a distinct cache namespace even when switching an existing install."""
    from .embedding import EmbeddingCache, EmbeddingService, build_embedding_provenance

    cfg = config.llm.embedding
    cache: Any = EmbeddingCache(
        config.data_path / "embedding_cache.db",
        max_bytes=cfg.cache_max_bytes,
        high_watermark=cfg.cache_high_watermark,
        low_watermark=cfg.cache_low_watermark,
    )
    cache.initialize()
    return EmbeddingService(
        SponsoredEmbeddingAdapter(),
        model=EMBEDDING_MODEL,
        cache_model=EMBEDDING_MODEL,
        persistent_cache=cache,
        multimodal_enabled=cfg.multimodal_enabled,
        similarity_threshold=cfg.similarity_threshold,
        provenance=build_embedding_provenance(
            "sponsored", "https://api.siliconflow.cn/v1", EMBEDDING_MODEL, EMBEDDING_DIMENSIONS
        ),
    )
