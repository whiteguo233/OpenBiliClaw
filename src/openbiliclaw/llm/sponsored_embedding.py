"""Free BGE-M3 text vectors in one fixed space; image vectors are optional."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .sponsored_errors import SponsoredUnavailableError, SponsoredUnsupportedTaskError
from .sponsored_profiles import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from .sponsored_router import shared_sponsored_provider

if TYPE_CHECKING:
    from openbiliclaw.config import Config

    from .embedding import EmbeddingService


def select_local_embedding(config: Config) -> None:
    """Select existing local settings, or install safe Ollama/BGE defaults.

    The desktop's private 11435 endpoint and user-selected local model survive.
    Other provider credentials must not be sent to the new local endpoint.
    """
    from dataclasses import replace

    cfg = config.llm.embedding
    if cfg.provider != "ollama":
        config.llm.embedding = replace(
            cfg,
            provider="ollama",
            model="bge-m3",
            api_key="",
            base_url="http://127.0.0.1:11434/v1",
            output_dimensionality=1024,
            fallback_enabled=False,
            fallback_provider="",
            multimodal_enabled=False,
        )
    else:
        config.llm.embedding = replace(cfg, fallback_enabled=False, fallback_provider="")


class SponsoredEmbeddingAdapter:
    name = "sponsored"
    _base_url = "https://api.siliconflow.cn/v1"
    supports_image_embedding = False

    @staticmethod
    def is_multimodal_embedding_model(model: str) -> bool:
        return False

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
        raise SponsoredUnsupportedTaskError("BGE-M3 supports text embeddings only")


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
        multimodal_enabled=False,
        similarity_threshold=cfg.similarity_threshold,
        provenance=build_embedding_provenance(
            "sponsored", "https://api.siliconflow.cn/v1", EMBEDDING_MODEL, EMBEDDING_DIMENSIONS
        ),
    )
