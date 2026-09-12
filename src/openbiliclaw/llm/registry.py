"""Factory helpers for building configured LLM registries."""

from __future__ import annotations

import logging
import os
from copy import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw import network

from .base import LLMProvider, LLMProviderError, LLMRegistry
from .claude_provider import ClaudeProvider
from .dashscope_provider import DashScopeEmbeddingProvider
from .gemini_provider import GeminiProvider, gemini_sdk_available
from .ollama_provider import OllamaProvider
from .openai_provider import DeepSeekProvider, OpenAIProvider
from .openrouter_provider import OpenRouterProvider
from .orcarouter_provider import OrcaRouterProvider

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
    if bool(getattr(config.llm, "instance_routing", False)):
        return _build_instance_llm_registry(
            config,
            provider_overrides=provider_overrides,
            fallback_order=fallback_order,
        )

    overrides = provider_overrides or {}
    registry = LLMRegistry()
    registry.fallback_provider = str(getattr(config.llm, "fallback_provider", "")).strip().lower()

    provider_specs = [
        ("openai", _maybe_openai_provider(config, overrides)),
        ("claude", _maybe_claude_provider(config, overrides)),
        ("gemini", _maybe_gemini_provider(config, overrides)),
        ("deepseek", _maybe_deepseek_provider(config, overrides)),
        ("ollama", _maybe_ollama_provider(config, overrides)),
        ("openrouter", _maybe_openrouter_provider(config, overrides)),
        ("orcarouter", _maybe_orcarouter_provider(config, overrides)),
        ("openai_compatible", _maybe_openai_compatible_provider(config, overrides)),
    ]

    for _name, provider in provider_specs:
        if provider is None:
            continue
        # Production Ollama registration already requires an explicit chat
        # model. Keep the capability check as a defensive boundary for
        # provider_overrides / legacy integrations that inject a non-chat
        # Ollama instance into this registry.
        chat_capable = True
        if _name == "ollama" and not _ollama_is_chat_capable(config):
            chat_capable = False
        registry.register(provider, default=False, chat_capable=chat_capable)

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
    chat_providers = [
        name for name in registry.available_providers if registry.is_chat_capable(name)
    ]
    if not chat_providers:
        raise RegistryBuildError(
            "No chat-capable LLM providers are available from the current configuration."
        )
    effective_default = (
        configured_default if configured_default in chat_providers else chat_providers[0]
    )
    registry._default = effective_default

    # Backstop for silently-dead fallback config: base.py `_fallback_order()`
    # deliberately drops an unusable fallback without any runtime signal
    # (correct — we can't spam every completion call). Surface the dead
    # state ONCE at build time instead. Config saves are also validated in
    # config.py `_collect_config_issues`, but env overrides / hand-edited
    # config.toml can still reach this point.
    fallback = registry.fallback_provider
    if fallback:
        if fallback == effective_default:
            logger.warning(
                "llm.fallback_provider=%r is the same as the effective default "
                "provider — the fallback will never be used.",
                fallback,
            )
        elif fallback not in registry.available_providers:
            logger.warning(
                "llm.fallback_provider=%r is not registered (likely missing "
                "credentials such as api_key / base_url / model) — the fallback will "
                "never be used.",
                fallback,
            )
        elif not registry.is_chat_capable(fallback):
            logger.warning(
                "llm.fallback_provider=%r is registered but not chat-capable "
                "(embedding-only) — the fallback will never be used.",
                fallback,
            )
    return registry


def _build_instance_llm_registry(
    config: Config,
    *,
    provider_overrides: dict[str, LLMProvider] | None = None,
    fallback_order: list[str] | None = None,
) -> LLMRegistry:
    """Build a registry keyed by stable configured endpoint instance IDs."""
    overrides = provider_overrides or {}
    registry = LLMRegistry()
    raw_instances = getattr(config.llm, "instances", {})
    instances = raw_instances if isinstance(raw_instances, dict) else {}
    configured_chain = [
        str(item).strip().lower()
        for item in (fallback_order or getattr(config.llm, "default_chain", []))
        if str(item).strip()
    ]
    build_order = [
        *configured_chain,
        *[
            str(instance_id).strip().lower()
            for instance_id in instances
            if str(instance_id).strip().lower() not in configured_chain
        ],
    ]

    for instance_id in build_order:
        instance = instances.get(instance_id)
        if instance is None or not bool(getattr(instance, "enabled", True)):
            continue
        provider_type = str(getattr(instance, "provider_type", "") or "").strip().lower()
        override = overrides.get(instance_id)
        if override is None and instance_id == provider_type:
            # Compatibility for tests/integrations that inject the historical
            # one-per-type key while using a migrated-but-equivalent instance.
            override = overrides.get(provider_type)
        provider = override or _build_instance_provider(config, provider_type, instance)
        if provider is None:
            logger.warning(
                "LLM instance %r (provider_type=%r) could not be constructed.",
                instance_id,
                provider_type,
            )
            continue
        registry.register(
            provider,
            name=instance_id,
            provider_type=provider_type,
            chat_capable=not (
                provider_type == "ollama" and not str(getattr(instance, "model", "") or "").strip()
            ),
        )

    if not registry.available_providers:
        raise RegistryBuildError("No LLM instances are available from the current configuration.")

    usable_chain = [
        instance_id for instance_id in configured_chain if registry.is_chat_capable(instance_id)
    ]
    if not usable_chain:
        raise RegistryBuildError(
            "The configured LLM default_chain has no available chat-capable instances."
        )
    for instance_id in configured_chain:
        if not registry.is_chat_capable(instance_id):
            logger.warning(
                "LLM default_chain instance %r is unavailable or not chat-capable.",
                instance_id,
            )
    registry.configure_chain(usable_chain)
    return registry


def _build_instance_provider(
    config: Config,
    provider_type: str,
    instance: Any,
) -> LLMProvider | None:
    """Reuse the mature per-adapter factories with one v2 endpoint config."""
    factories = {
        "openai": _maybe_openai_provider,
        "claude": _maybe_claude_provider,
        "gemini": _maybe_gemini_provider,
        "deepseek": _maybe_deepseek_provider,
        "ollama": _maybe_ollama_provider,
        "openrouter": _maybe_openrouter_provider,
        "orcarouter": _maybe_orcarouter_provider,
        "openai_compatible": _maybe_openai_compatible_provider,
    }
    factory = factories.get(provider_type)
    if factory is None:
        return None
    config_copy = copy(config)
    llm_copy = copy(config.llm)
    setattr(llm_copy, provider_type, instance)
    config_copy.llm = llm_copy
    return factory(config_copy, {})


_EMBEDDING_CAPABLE_PROVIDERS: tuple[str, ...] = (
    "openai",
    "gemini",
    "ollama",
    # Most OpenAI-protocol-compatible backends (Together, vLLM, Azure
    # OpenAI, ...) expose /v1/embeddings. Groq currently does not, but
    # users running a Groq + openai_compatible setup already have to
    # supply an explicit embedding provider in [llm.embedding] — this
    # candidate only kicks in when they actively requested it.
    "openai_compatible",
    # OpenRouter routes embeddings per ``<vendor>/<model>`` slug
    # (e.g. ``google/gemini-embedding-2-preview``,
    # ``openai/text-embedding-3-small``). Coverage is spotty per-route
    # so it stays out of the chat-side ``supports_embedding`` flag —
    # users must opt in by setting ``[llm.embedding].provider =
    # "openrouter"`` with an explicit ``model``.
    "openrouter",
    # Alibaba DashScope multimodal embedding (Qwen3-VL / Tongyi vision).
    # Native API — not OpenAI /v1/embeddings. Opt-in via
    # ``[llm.embedding].provider = "dashscope"``.
    "dashscope",
)
_DEFAULT_EMBEDDING_MODEL_BY_PROVIDER: dict[str, str] = {
    "gemini": "gemini-embedding-001",
    "openai": "text-embedding-3-small",
    "ollama": "bge-m3",
    # No safe default for openai_compatible — depends entirely on the
    # upstream service. Users must specify an explicit model.
    "openai_compatible": "text-embedding-3-small",
    "dashscope": "qwen3-vl-embedding",
}
# The OpenAI SDK refuses to construct a client without a non-empty api_key,
# but self-hosted / local OpenAI-compatible endpoints (vLLM, LM Studio, a
# user's own embedding bridge) usually don't check Authorization at all. Use
# this placeholder so a no-auth endpoint can be configured without inventing
# a real-looking key; it is never sent to api.openai.com because an empty key
# is only accepted when the caller supplied an explicit custom base_url.
_NO_AUTH_OPENAI_API_KEY = "not-needed"
# Module-level set so the back-compat WARNING fires once per provider per
# process (not once per build_embedding_service call — runtime_context
# rebuilds embedding on every PUT /api/config and we don't want to spam).
_embedding_compat_warned: set[str] = set()


def build_embedding_service(
    config: Config,
    registry: LLMRegistry,  # noqa: ARG001 — kept for back-compat callers
) -> SupportsEmbeddingService | None:
    """Build an EmbeddingService from ``[llm.embedding]``.

    v0.3.32+ embedding owns its own ``api_key`` / ``base_url`` (see
    ``EmbeddingConfig``), so the embedding provider is constructed as a
    dedicated instance — completely decoupled from the chat-side
    LLMRegistry. The ``registry`` parameter is preserved only so existing
    call sites don't need to change; it is no longer consulted.

    Empty ``[llm.embedding].provider`` disables embedding; it no longer
    follows ``[llm].default_provider``. Provider fallback is opt-in via
    ``[llm.embedding].fallback_provider`` and only tries that one
    explicit backup provider. ``fallback_enabled`` remains as a legacy
    compatibility flag for borrowing chat-side credentials.
    """
    try:
        from typing import cast

        from openbiliclaw.llm.embedding import (
            EmbeddingCache,
            EmbeddingService,
            SupportsEmbed,
            build_embedding_provenance,
        )

        emb_cfg = config.llm.embedding
        requested_name = emb_cfg.provider.strip().lower()
        fallback_provider = str(getattr(emb_cfg, "fallback_provider", "")).strip().lower()

        # Build candidate ordering: requested first, then optional
        # explicit fallback provider. Empty provider no longer follows
        # [llm].default_provider; embedding is an independent config
        # surface.
        fallback_order: list[str] = []
        fallback_candidates: tuple[str, ...] = (fallback_provider,) if fallback_provider else ()
        for name in ((requested_name,) if requested_name else ()) + fallback_candidates:
            if name in _EMBEDDING_CAPABLE_PROVIDERS and name not in fallback_order:
                fallback_order.append(name)

        chosen_provider: LLMProvider | None = None
        chosen_name = ""
        chosen_model = ""
        for candidate in fallback_order:
            built = _build_dedicated_embedding_provider(candidate, emb_cfg, config, requested_name)
            if built is None:
                continue
            chosen_provider, chosen_model = built
            chosen_name = candidate
            break

        if chosen_provider is None:
            requested_label = requested_name or "(not configured)"
            logger.warning(
                "No embedding-capable provider available (requested=%r). "
                "Embedding service disabled — recommendation diversity and "
                "deduplication will degrade. Run 'openbiliclaw setup-embedding' "
                "to install local Ollama bge-m3, or configure a Gemini API key.",
                requested_label,
            )
            return None

        if chosen_name != requested_name:
            requested_label = requested_name or "(not configured)"
            logger.warning(
                "Embedding provider %r unavailable; falling back to %r. "
                "Set [llm.embedding] provider=%r explicitly in config.toml "
                "to silence this, or run 'openbiliclaw setup-embedding'.",
                requested_label,
                chosen_name,
                chosen_name,
            )

        # Persistent L2 cache: store embeddings in SQLite alongside main DB.
        # Vectors are stored as compact float32 blobs; the byte budget
        # (0 = unlimited) bounds disk growth once configured.
        l2_cache: EmbeddingCache | None = None
        try:
            cache_path = config.data_path / "embedding_cache.db"
            l2_cache = EmbeddingCache(
                cache_path,
                max_bytes=max(0, int(getattr(emb_cfg, "cache_max_bytes", 0) or 0)),
                high_watermark=float(getattr(emb_cfg, "cache_high_watermark", 0.9) or 0.9),
                low_watermark=float(getattr(emb_cfg, "cache_low_watermark", 0.7) or 0.7),
            )
            l2_cache.initialize()
        except Exception:
            logger.debug("Failed to init embedding L2 cache", exc_info=True)

        output_dimensionality = _embedding_output_dimensionality(emb_cfg)
        cache_model = _embedding_cache_model(
            chosen_name,
            chosen_model,
            output_dimensionality,
        )
        endpoint = _embedding_endpoint(chosen_name, emb_cfg, config, chosen_provider)
        # Only include a configured dimension when this provider/model actually
        # sends it on the wire. For fixed-dimension or generic compatible
        # backends, zero deliberately means "unknown"; the first observed
        # vector is tracked separately by EmbeddingService/database provenance.
        provenance_dimension = (
            output_dimensionality
            if _embedding_provider_honors_output_dimensionality(chosen_name, chosen_model)
            else 0
        )
        provenance = build_embedding_provenance(
            chosen_name,
            endpoint,
            chosen_model,
            provenance_dimension,
        )

        return EmbeddingService(
            cast("SupportsEmbed", chosen_provider),
            model=chosen_model,
            cache_model=cache_model,
            similarity_threshold=emb_cfg.similarity_threshold,
            persistent_cache=l2_cache,
            multimodal_enabled=bool(getattr(emb_cfg, "multimodal_enabled", False)),
            provenance=provenance,
            cache_max_bytes=max(0, int(getattr(emb_cfg, "cache_max_bytes", 0) or 0)),
            cache_high_watermark=float(getattr(emb_cfg, "cache_high_watermark", 0.9) or 0.9),
            cache_low_watermark=float(getattr(emb_cfg, "cache_low_watermark", 0.7) or 0.7),
        )
    except Exception:
        return None


def _build_dedicated_embedding_provider(
    candidate: str,
    emb_cfg: Any,
    config: Config,
    requested_name: str,
) -> tuple[LLMProvider, str] | None:
    """Construct a dedicated provider instance for embedding calls.

    Returns ``(provider, effective_model)`` or ``None`` if the candidate
    can't be constructed (missing api_key, missing SDK, ...).
    """
    emb_api_key = emb_cfg.api_key.strip()
    emb_base_url = emb_cfg.base_url.strip()
    fallback_enabled = bool(getattr(emb_cfg, "fallback_enabled", False))
    output_dimensionality = _embedding_output_dimensionality(emb_cfg)

    # First-class path: candidate matches what the user requested AND
    # they supplied credentials in [llm.embedding].
    use_embedding_creds = candidate == requested_name and bool(emb_api_key or emb_base_url)

    if use_embedding_creds:
        api_key = emb_api_key
        base_url = emb_base_url
    elif fallback_enabled:
        # Optional back-compat path: borrow from [llm.<candidate>] only
        # when embedding fallback is explicitly enabled.
        chat_cfg = getattr(config.llm, candidate, None)
        if bool(getattr(config.llm, "instance_routing", False)):
            instance_ids = [
                *getattr(config.llm, "default_chain", []),
                *[
                    instance_id
                    for instance_id in getattr(config.llm, "instances", {})
                    if instance_id not in getattr(config.llm, "default_chain", [])
                ],
            ]
            chat_cfg = next(
                (
                    config.llm.instances[instance_id]
                    for instance_id in instance_ids
                    if instance_id in config.llm.instances
                    and bool(getattr(config.llm.instances[instance_id], "enabled", True))
                    and str(getattr(config.llm.instances[instance_id], "provider_type", "") or "")
                    .strip()
                    .lower()
                    == candidate
                ),
                None,
            )
        api_key = (getattr(chat_cfg, "api_key", "") if chat_cfg is not None else "").strip()
        base_url = (getattr(chat_cfg, "base_url", "") if chat_cfg is not None else "").strip()
        borrowed_chat_credentials = (
            bool(api_key and base_url) if candidate == "openai_compatible" else bool(api_key)
        )
        if (
            emb_cfg.provider.strip().lower() == candidate
            and candidate == requested_name
            and candidate != "ollama"
            and borrowed_chat_credentials
        ):
            _emit_embedding_compat_warning(candidate)
    else:
        api_key = ""
        base_url = ""

    # Effective model: honour explicit emb_cfg.model only when we're
    # building the requested provider — fallback paths must use the
    # per-provider default (e.g. text-embedding-3-small on OpenAI is
    # meaningless when we fell back to Ollama).
    if candidate == requested_name and emb_cfg.model.strip():
        effective_model = emb_cfg.model.strip()
    else:
        effective_model = _DEFAULT_EMBEDDING_MODEL_BY_PROVIDER.get(
            candidate, "gemini-embedding-001"
        )

    if candidate == "ollama":
        # Ollama doesn't require an api_key, so without a gate the
        # constructor would always succeed and silently mask "user has no
        # embedding-capable provider" — which matters for the warning
        # path that tells users to set up Ollama or a Gemini key. Only
        # build it when the user actually opted in:
        #   - [llm.embedding] supplied its own ollama config, OR
        #   - the user requested Ollama for embedding, OR
        #   - [llm.ollama] is configured (back-compat — they run it locally).
        chat_ollama = config.llm.ollama
        if bool(getattr(config.llm, "instance_routing", False)):
            chat_ollama = next(
                (
                    instance
                    for instance in config.llm.instances.values()
                    if bool(getattr(instance, "enabled", True))
                    and str(getattr(instance, "provider_type", "") or "").strip().lower()
                    == "ollama"
                ),
                chat_ollama,
            )
        has_chat_ollama_config = bool(chat_ollama.model.strip() or chat_ollama.base_url.strip())
        if not use_embedding_creds and requested_name != "ollama" and not has_chat_ollama_config:
            return None
        if not base_url:
            base_url = "http://127.0.0.1:11434/v1"
        if not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        return (
            OllamaProvider(
                api_key=api_key or "ollama",
                model=effective_model,
                base_url=base_url,
            ),
            effective_model,
        )

    if candidate == "openai":
        # api.openai.com requires a key, but an explicit custom base_url may
        # point at a no-auth self-hosted gateway (vLLM / LM Studio / a local
        # bridge). Only relax the gate when the caller supplied that base_url
        # so an empty key can never silently target api.openai.com.
        if not api_key and not base_url:
            return None
        return (
            OpenAIProvider(
                api_key=api_key or _NO_AUTH_OPENAI_API_KEY,
                model=effective_model,
                base_url=base_url,
                embedding_output_dimensionality=output_dimensionality,
                proxy=_outbound_proxy(base_url),
                trust_env=_outbound_trust_env(base_url),
            ),
            effective_model,
        )

    if candidate == "gemini":
        if not api_key:
            api_key = _gemini_env_api_key()
        if not api_key or not gemini_sdk_available():
            return None
        return (
            GeminiProvider(
                api_key=api_key,
                model=effective_model,
                base_url=base_url,
                embedding_output_dimensionality=output_dimensionality,
                proxy=_outbound_proxy(base_url),
                trust_env=_outbound_trust_env(base_url),
            ),
            effective_model,
        )

    if candidate == "openai_compatible":
        # base_url is the whole point of this provider. A key stays optional:
        # self-hosted / local OpenAI-compatible endpoints frequently don't
        # authenticate, and the SDK is satisfied by the placeholder above.
        if not base_url:
            return None
        return (
            OpenAIProvider(
                api_key=api_key or _NO_AUTH_OPENAI_API_KEY,
                model=effective_model,
                base_url=base_url,
                provider_name="openai_compatible",
                proxy=_outbound_proxy(base_url),
                trust_env=_outbound_trust_env(base_url),
            ),
            effective_model,
        )

    if candidate == "openrouter":
        # OpenRouter requires an explicit ``<vendor>/<model>`` slug — no
        # safe default since routing depends on it. Refuse to construct
        # without one rather than 404 at first embed call.
        if not api_key:
            return None
        if candidate == requested_name and not emb_cfg.model.strip():
            return None
        # Pass through optional attribution headers from [llm.openrouter]
        # so the embedding traffic shows up under the same OpenRouter
        # account dashboard as chat traffic.
        chat_openrouter = config.llm.openrouter
        return (
            OpenRouterProvider(
                api_key=api_key,
                model=effective_model,
                base_url=base_url or "https://openrouter.ai/api/v1",
                http_referer=chat_openrouter.http_referer,
                x_title=chat_openrouter.x_title,
                proxy=_outbound_proxy(base_url or "https://openrouter.ai/api/v1"),
                trust_env=_outbound_trust_env(base_url or "https://openrouter.ai/api/v1"),
            ),
            effective_model,
        )

    if candidate == "dashscope":
        if not api_key:
            api_key = _dashscope_env_api_key()
        if not api_key:
            return None
        return (
            DashScopeEmbeddingProvider(
                api_key=api_key,
                model=effective_model,
                base_url=base_url,
                embedding_output_dimensionality=output_dimensionality,
            ),
            effective_model,
        )

    return None


def _dashscope_env_api_key() -> str:
    """Optional DASHSCOPE_API_KEY / ALIBABA_CLOUD env fallback."""
    import os

    for name in ("DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY_CN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _embedding_output_dimensionality(emb_cfg: Any) -> int:
    try:
        return max(0, int(getattr(emb_cfg, "output_dimensionality", 1024) or 0))
    except (TypeError, ValueError):
        return 1024


def _embedding_cache_model(
    provider_name: str,
    model: str,
    output_dimensionality: int,
) -> str:
    if output_dimensionality > 0 and _embedding_provider_honors_output_dimensionality(
        provider_name, model
    ):
        return f"{model}#dim={output_dimensionality}"
    return model


def _embedding_endpoint(
    provider_name: str,
    emb_cfg: Any,
    config: Config,
    provider: LLMProvider,
) -> str:
    """Return the endpoint actually used by a dedicated embedding provider."""
    for attr in ("base_url", "_base_url"):
        value = str(getattr(provider, attr, "") or "").strip()
        if value:
            return value

    requested_name = str(getattr(emb_cfg, "provider", "") or "").strip().lower()
    if provider_name == requested_name:
        configured = str(getattr(emb_cfg, "base_url", "") or "").strip()
        if configured:
            return configured

    # Fallback providers may borrow chat-side credentials. Resolve the same
    # provider config used by _build_dedicated_embedding_provider, but never
    # include its API key in provenance.
    if bool(getattr(emb_cfg, "fallback_enabled", False)):
        chat_cfg = getattr(config.llm, provider_name, None)
        if bool(getattr(config.llm, "instance_routing", False)):
            instance_ids = [
                *getattr(config.llm, "default_chain", []),
                *[
                    instance_id
                    for instance_id in getattr(config.llm, "instances", {})
                    if instance_id not in getattr(config.llm, "default_chain", [])
                ],
            ]
            chat_cfg = next(
                (
                    config.llm.instances[instance_id]
                    for instance_id in instance_ids
                    if instance_id in config.llm.instances
                    and bool(getattr(config.llm.instances[instance_id], "enabled", True))
                    and str(getattr(config.llm.instances[instance_id], "provider_type", "") or "")
                    .strip()
                    .lower()
                    == provider_name
                ),
                None,
            )
        configured = str(getattr(chat_cfg, "base_url", "") or "").strip()
        if configured:
            return configured

    defaults = {
        "openai": "https://api.openai.com/v1",
        "openai_compatible": "",
        "gemini": "https://generativelanguage.googleapis.com",
        "ollama": "http://127.0.0.1:11434/v1",
        "dashscope": "https://dashscope.aliyuncs.com",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    return defaults.get(provider_name, "")


def _embedding_provider_honors_output_dimensionality(
    provider_name: str,
    model: str,
) -> bool:
    if provider_name == "gemini":
        return True
    if provider_name == "openai":
        return model.startswith("text-embedding-3-")
    if provider_name == "dashscope":
        # qwen3-vl-embedding accepts dimension=; older tongyi fixed-dim
        # models ignore it — only claim honor when we actually pass it.
        name = (model or "").lower()
        return "qwen3-vl-embedding" in name or (
            "tongyi-embedding-vision" in name and "2026-03-06" in name
        )
    return False


def _emit_embedding_compat_warning(provider_name: str) -> None:
    """Emit at most one WARNING per provider per process for the
    embedding back-compat path."""
    if provider_name in _embedding_compat_warned:
        return
    _embedding_compat_warned.add(provider_name)
    logger.warning(
        "[llm.embedding] api_key/base_url is empty — falling back to "
        "[llm.%s] credentials. This back-compat path will be removed in a "
        "future release. Move the embedding credentials into "
        "[llm.embedding] in your config.toml.",
        provider_name,
    )


def summarize_registry(config: Config, registry: LLMRegistry) -> RegistrySummary:
    """Return registry summary details for CLI display."""
    if bool(getattr(config.llm, "instance_routing", False)):
        configured_chain = [
            str(item).strip().lower()
            for item in getattr(config.llm, "default_chain", [])
            if str(item).strip()
        ]
        configured_default = configured_chain[0] if configured_chain else ""
    else:
        configured_default = config.llm.default_provider
    return RegistrySummary(
        configured_default=configured_default,
        effective_default=registry.default_provider,
        registered_providers=registry.available_providers,
    )


def _outbound_proxy(base_url: str = "") -> str:
    """Outbound proxy for a provider endpoint from the process-level source of
    truth (or "").

    Domestic / local endpoints (DeepSeek / SenseNova / 通义 / self-hosted, …)
    always resolve to direct — the overseas proxy would route a domestic
    request out and back and time out. See ``network.is_domestic_endpoint``.
    """
    return network.proxy_for_endpoint(base_url) or ""


def _outbound_trust_env(base_url: str = "") -> bool:
    """Whether an SDK client for ``base_url`` should inherit env/system proxies.

    Domestic / local endpoints never inherit env proxies; overseas endpoints
    follow the process-wide ``system`` policy.
    """
    return network.trust_env_for_endpoint(base_url)


def _maybe_openai_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
    if "openai" in overrides:
        return overrides["openai"]
    auth_mode = config.llm.openai.auth_mode.strip().lower()
    if auth_mode == "codex_oauth":
        from openbiliclaw.llm.codex_auth import get_valid_codex_token, load_codex_credentials
        from openbiliclaw.llm.codex_chatgpt_provider import CodexChatGPTProvider

        credentials = load_codex_credentials()
        if credentials is None:
            logger.warning("codex_oauth configured but no Codex credentials were found")
            return None

        async def _codex_token_provider(force_refresh: bool = False) -> str:
            return await get_valid_codex_token(force_refresh=force_refresh)

        return CodexChatGPTProvider(
            access_token=credentials.access_token,
            account_id=credentials.account_id,
            model=config.llm.openai.model or "gpt-5.4",
            base_url=config.llm.openai.base_url,
            token_provider=_codex_token_provider,
            timeout=float(config.llm.timeout),
            reasoning_effort=config.llm.openai.reasoning_effort,
        )
    if not config.llm.openai.api_key.strip():
        return None
    return OpenAIProvider(
        api_key=config.llm.openai.api_key,
        model=config.llm.openai.model or "gpt-4o",
        base_url=config.llm.openai.base_url,
        timeout=float(config.llm.timeout),
        api_flavor=config.llm.openai.api_flavor,
        proxy=_outbound_proxy(config.llm.openai.base_url),
        trust_env=_outbound_trust_env(config.llm.openai.base_url),
        reasoning_effort=config.llm.openai.reasoning_effort,
    )


def _maybe_claude_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
    if "claude" in overrides:
        return overrides["claude"]
    if not config.llm.claude.api_key.strip():
        return None
    return ClaudeProvider(
        api_key=config.llm.claude.api_key,
        model=config.llm.claude.model or "claude-sonnet-4-20250514",
        timeout=float(config.llm.timeout),
        base_url=config.llm.claude.base_url,
        proxy=_outbound_proxy(config.llm.claude.base_url),
        trust_env=_outbound_trust_env(config.llm.claude.base_url),
        reasoning_effort=config.llm.claude.reasoning_effort,
    )


def _maybe_deepseek_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "deepseek" in overrides:
        return overrides["deepseek"]
    if not config.llm.deepseek.api_key.strip():
        return None
    base_url = config.llm.deepseek.base_url.strip() or "https://api.deepseek.com"
    return DeepSeekProvider(
        api_key=config.llm.deepseek.api_key,
        model=config.llm.deepseek.model or "deepseek-v4-flash",
        base_url=base_url,
        reasoning_effort=config.llm.deepseek.reasoning_effort,
        timeout=float(config.llm.timeout),
        proxy=_outbound_proxy(base_url),
        trust_env=_outbound_trust_env(base_url),
    )


def _gemini_env_api_key() -> str:
    return (
        os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    )


def _maybe_gemini_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
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
        timeout=float(config.llm.timeout),
        proxy=_outbound_proxy(config.llm.gemini.base_url),
        trust_env=_outbound_trust_env(config.llm.gemini.base_url),
        reasoning_effort=config.llm.gemini.reasoning_effort,
    )


def _maybe_ollama_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
    # Keep the explicit-model requirement in sync with config.py
    # `_collect_config_issues` (config cannot import the registry, cycle).
    if "ollama" in overrides:
        return overrides["ollama"]

    raw_base_url = config.llm.ollama.base_url.strip()
    model = config.llm.ollama.model.strip()

    # v0.3.32+ note: build_embedding_service now constructs its own Ollama
    # provider directly from [llm.embedding] (or back-compat from
    # [llm.ollama]) — it no longer goes through this registry. So we no
    # longer need the old ``embedding_wants_ollama`` auto-register hack:
    # the chat registry stays clean, and Ollama is only registered here
    # when the user actually wants chat completions through it.
    # A URL only identifies an Ollama server; it says nothing about which
    # chat model exists there. Historically this path substituted ``llama3``
    # for an empty model, so an embedding-only desktop config repeatedly
    # probed a model the user never selected. Chat now requires an explicit
    # model; embedding builds its own provider above.
    if not model:
        return None
    base_url = raw_base_url or "http://127.0.0.1:11434/v1"
    # Normalise: Ollama's OpenAI-compat shim lives at `/v1/...`. Older
    # config.example.toml shipped `http://localhost:11434` (no /v1),
    # which makes the OpenAI SDK call `/chat/completions` — Ollama 404s
    # those. Append /v1 defensively so existing users with stale configs
    # still get working chat completions after upgrade.
    if not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    return OllamaProvider(
        api_key=config.llm.ollama.api_key or "ollama",
        model=model,
        base_url=base_url,
        timeout=float(config.llm.timeout),
        num_ctx=int(config.llm.ollama.num_ctx),
    )


def _ollama_is_chat_capable(config: Config) -> bool:
    """Return whether Ollama has an explicitly configured chat model.

    Naming Ollama as a default, fallback, or module provider is not enough:
    unlike remote providers, its server URL cannot identify a model and the
    runtime must never invent one. Keep this rule in sync with config.py.
    """
    return bool(config.llm.ollama.model.strip())


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
        timeout=float(config.llm.timeout),
        proxy=_outbound_proxy(config.llm.openrouter.base_url or "https://openrouter.ai/api/v1"),
        trust_env=_outbound_trust_env(
            config.llm.openrouter.base_url or "https://openrouter.ai/api/v1"
        ),
        reasoning_effort=config.llm.openrouter.reasoning_effort,
    )


def _maybe_orcarouter_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    if "orcarouter" in overrides:
        return overrides["orcarouter"]
    if not config.llm.orcarouter.api_key.strip():
        return None
    base_url = config.llm.orcarouter.base_url or "https://api.orcarouter.ai/v1"
    return OrcaRouterProvider(
        api_key=config.llm.orcarouter.api_key,
        model=config.llm.orcarouter.model or "openai/gpt-4o",
        base_url=base_url,
        timeout=float(config.llm.timeout),
        proxy=_outbound_proxy(base_url),
        trust_env=_outbound_trust_env(base_url),
        reasoning_effort=config.llm.orcarouter.reasoning_effort,
    )


def _maybe_openai_compatible_provider(
    config: Config, overrides: dict[str, LLMProvider]
) -> LLMProvider | None:
    """Generic OpenAI-protocol-compatible provider (Groq / Together / Azure
    OpenAI / vLLM / self-hosted, etc.).

    Distinct from ``[llm.openai]`` so users can run both in parallel and
    keep cost / model accounting separate. Refuses to register without a
    ``base_url`` — that's the whole point of this provider; without it
    the call would just hit api.openai.com and would be indistinguishable
    from ``[llm.openai]`` (and would 401 against the wrong key)."""
    if "openai_compatible" in overrides:
        return overrides["openai_compatible"]
    cfg = config.llm.openai_compatible
    if not cfg.api_key.strip():
        return None
    if not cfg.base_url.strip():
        # Surfaced as a ConfigIssue in _collect_config_issues; here we
        # just refuse to construct a misconfigured provider.
        return None
    return OpenAIProvider(
        api_key=cfg.api_key,
        model=cfg.model or "gpt-4o-mini",
        base_url=cfg.base_url,
        provider_name="openai_compatible",
        timeout=float(config.llm.timeout),
        api_flavor=cfg.api_flavor,
        proxy=_outbound_proxy(cfg.base_url),
        trust_env=_outbound_trust_env(cfg.base_url),
        reasoning_effort=cfg.reasoning_effort,
    )
