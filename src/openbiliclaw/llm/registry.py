"""Factory helpers for building configured LLM registries."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import LLMProvider, LLMProviderError, LLMRegistry
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider, gemini_sdk_available
from .ollama_provider import OllamaProvider
from .openai_provider import DeepSeekProvider, OpenAIProvider
from .openrouter_provider import OpenRouterProvider

if TYPE_CHECKING:
    from openbiliclaw.config import Config
    from openbiliclaw.llm.embedding import SupportsEmbeddingService

logger = logging.getLogger(__name__)


class RegistryBuildError(LLMProviderError):
    """Raised when no usable providers can be created from config."""


@dataclass
class RegistrySummary:
    """Summary of registry construction details."""

    configured_default: str
    effective_default: str
    registered_providers: list[str]


def build_llm_registry(
    config: Config,
    *,
    provider_overrides: dict[str, LLMProvider] | None = None,
    fallback_order: list[str] | None = None,
) -> LLMRegistry:
    """Build an LLM registry from application config."""
    overrides = provider_overrides or {}
    registry = LLMRegistry()

    provider_specs = [
        ("openai", _maybe_openai_provider(config, overrides)),
        ("claude", _maybe_claude_provider(config, overrides)),
        ("gemini", _maybe_gemini_provider(config, overrides)),
        ("deepseek", _maybe_deepseek_provider(config, overrides)),
        ("ollama", _maybe_ollama_provider(config, overrides)),
        ("openrouter", _maybe_openrouter_provider(config, overrides)),
    ]

    for _name, provider in provider_specs:
        if provider is None:
            continue
        registry.register(provider, default=False)

    for name, provider in overrides.items():
        if name not in registry.available_providers:
            registry.register(provider, default=False)

    if fallback_order:
        reordered = [name for name in fallback_order if name in registry.available_providers]
        remainder = [name for name in registry.available_providers if name not in reordered]
        registry._providers = {name: registry._providers[name] for name in [*reordered, *remainder]}

    if not registry.available_providers:
        raise RegistryBuildError("No LLM providers are available from the current configuration.")

    configured_default = config.llm.default_provider
    effective_default = (
        configured_default
        if configured_default in registry.available_providers
        else registry.available_providers[0]
    )
    registry._default = effective_default
    return registry


def build_embedding_service(
    config: Config,
    registry: LLMRegistry,
) -> SupportsEmbeddingService | None:
    """Build an EmbeddingService from config, or None if unavailable.

    Uses ``[llm.embedding]`` config section for model and threshold.
    Falls back to the Gemini provider from the registry.
    """
    try:
        from openbiliclaw.llm.embedding import EmbeddingCache, EmbeddingService

        emb_cfg = config.llm.embedding
        provider_name = emb_cfg.provider.strip() or config.llm.default_provider
        provider = registry.get(provider_name)
        if not hasattr(provider, "embed"):
            return None

        # Persistent L2 cache: store embeddings in SQLite alongside main DB
        l2_cache: EmbeddingCache | None = None
        try:
            cache_path = config.data_path / "embedding_cache.db"
            l2_cache = EmbeddingCache(cache_path)
            l2_cache.initialize()
        except Exception:
            logger.debug("Failed to init embedding L2 cache", exc_info=True)

        return EmbeddingService(
            provider,
            model=emb_cfg.model or "gemini-embedding-001",
            similarity_threshold=emb_cfg.similarity_threshold,
            persistent_cache=l2_cache,
        )
    except Exception:
        return None


def summarize_registry(config: Config, registry: LLMRegistry) -> RegistrySummary:
    """Return registry summary details for CLI display."""
    return RegistrySummary(
        configured_default=config.llm.default_provider,
        effective_default=registry.default_provider,
        registered_providers=registry.available_providers,
    )


def _maybe_openai_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "openai" in overrides:
        return overrides["openai"]
    if not config.llm.openai.api_key.strip():
        return None
    return OpenAIProvider(
        api_key=config.llm.openai.api_key,
        model=config.llm.openai.model or "gpt-4o",
        base_url=config.llm.openai.base_url,
    )


def _maybe_claude_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "claude" in overrides:
        return overrides["claude"]
    if not config.llm.claude.api_key.strip():
        return None
    return ClaudeProvider(
        api_key=config.llm.claude.api_key,
        model=config.llm.claude.model or "claude-sonnet-4-20250514",
    )


def _maybe_deepseek_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "deepseek" in overrides:
        return overrides["deepseek"]
    if not config.llm.deepseek.api_key.strip():
        return None
    return DeepSeekProvider(
        api_key=config.llm.deepseek.api_key,
        model=config.llm.deepseek.model or "deepseek-chat",
    )


def _gemini_env_api_key() -> str:
    return os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get(
        "GEMINI_API_KEY", ""
    ).strip()


def _maybe_gemini_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "gemini" in overrides:
        return overrides["gemini"]
    api_key = config.llm.gemini.api_key.strip() or _gemini_env_api_key()
    if not api_key:
        return None
    if not gemini_sdk_available():
        return None
    return GeminiProvider(
        api_key=api_key,
        model=config.llm.gemini.model or "gemini-2.5-flash",
    )


def _maybe_ollama_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "ollama" in overrides:
        return overrides["ollama"]

    raw_base_url = config.llm.ollama.base_url.strip()
    model = config.llm.ollama.model.strip()
    if not model and not raw_base_url:
        return None
    base_url = raw_base_url or "http://localhost:11434/v1"
    return OllamaProvider(
        api_key=config.llm.ollama.api_key or "ollama",
        model=model or "llama3",
        base_url=base_url,
    )


def _maybe_openrouter_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "openrouter" in overrides:
        return overrides["openrouter"]
    if not config.llm.openrouter.api_key.strip():
        return None
    return OpenRouterProvider(
        api_key=config.llm.openrouter.api_key,
        model=config.llm.openrouter.model or "openai/gpt-4o-mini",
        base_url=config.llm.openrouter.base_url or "https://openrouter.ai/api/v1",
        http_referer=config.llm.openrouter.http_referer,
        x_title=config.llm.openrouter.x_title,
    )
