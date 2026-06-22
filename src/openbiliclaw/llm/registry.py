"""Factory helpers for building configured LLM registries."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    registry.fallback_enabled = bool(getattr(config.llm, "fallback_enabled", False))
    registry.fallback_provider = str(getattr(config.llm, "fallback_provider", "")).strip().lower()

    provider_specs = [
        ("openai", _maybe_openai_provider(config, overrides)),
        ("claude", _maybe_claude_provider(config, overrides)),
        ("gemini", _maybe_gemini_provider(config, overrides)),
        ("deepseek", _maybe_deepseek_provider(config, overrides)),
        ("ollama", _maybe_ollama_provider(config, overrides)),
        ("openrouter", _maybe_openrouter_provider(config, overrides)),
        ("openai_compatible", _maybe_openai_compatible_provider(config, overrides)),
    ]

    for _name, provider in provider_specs:
        if provider is None:
            continue
        # Ollama gets a special chat-capability check: the registry needs
        # it for embedding even when the user never configured a chat
        # model, but in that case it MUST stay out of the chat fallback
        # chain (see _ollama_is_chat_capable + base.py:_fallback_order).
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
    effective_default = (
        configured_default
        if configured_default in registry.available_providers
        else registry.available_providers[0]
    )
    registry._default = effective_default
    return registry


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
)
_DEFAULT_EMBEDDING_MODEL_BY_PROVIDER: dict[str, str] = {
    "gemini": "gemini-embedding-001",
    "openai": "text-embedding-3-small",
    "ollama": "bge-m3",
    # No safe default for openai_compatible — depends entirely on the
    # upstream service. Users must specify an explicit model.
    "openai_compatible": "text-embedding-3-small",
}
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

        from openbiliclaw.llm.embedding import EmbeddingCache, EmbeddingService, SupportsEmbed

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

        # Persistent L2 cache: store embeddings in SQLite alongside main DB
        l2_cache: EmbeddingCache | None = None
        try:
            cache_path = config.data_path / "embedding_cache.db"
            l2_cache = EmbeddingCache(cache_path)
            l2_cache.initialize()
        except Exception:
            logger.debug("Failed to init embedding L2 cache", exc_info=True)

        output_dimensionality = _embedding_output_dimensionality(emb_cfg)
        cache_model = _embedding_cache_model(
            chosen_name,
            chosen_model,
            output_dimensionality,
        )

        return EmbeddingService(
            cast("SupportsEmbed", chosen_provider),
            model=chosen_model,
            cache_model=cache_model,
            similarity_threshold=emb_cfg.similarity_threshold,
            persistent_cache=l2_cache,
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
        has_chat_ollama_config = bool(chat_ollama.model.strip() or chat_ollama.base_url.strip())
        if not use_embedding_creds and requested_name != "ollama" and not has_chat_ollama_config:
            return None
        if not base_url:
            base_url = "http://localhost:11434/v1"
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
        if not api_key:
            return None
        return (
            OpenAIProvider(
                api_key=api_key,
                model=effective_model,
                base_url=base_url,
                embedding_output_dimensionality=output_dimensionality,
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
            ),
            effective_model,
        )

    if candidate == "openai_compatible":
        # Strict — no api_key OR no base_url means we can't construct it.
        # Unlike "openai", there's no api.openai.com fallback because
        # this provider's whole reason to exist is the custom base_url.
        if not api_key or not base_url:
            return None
        return (
            OpenAIProvider(
                api_key=api_key,
                model=effective_model,
                base_url=base_url,
                provider_name="openai_compatible",
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
            ),
            effective_model,
        )

    return None


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
    if (
        output_dimensionality > 0
        and _embedding_provider_honors_output_dimensionality(provider_name, model)
    ):
        return f"{model}#dim={output_dimensionality}"
    return model


def _embedding_provider_honors_output_dimensionality(
    provider_name: str,
    model: str,
) -> bool:
    if provider_name == "gemini":
        return True
    if provider_name == "openai":
        return model.startswith("text-embedding-3-")
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
    return RegistrySummary(
        configured_default=config.llm.default_provider,
        effective_default=registry.default_provider,
        registered_providers=registry.available_providers,
    )


def _maybe_openai_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
    if "openai" in overrides:
        return overrides["openai"]
    auth_mode = config.llm.openai.auth_mode.strip().lower()
    if auth_mode == "codex_oauth":
        from openbiliclaw.llm.codex_auth import get_valid_codex_token, load_codex_credentials

        credentials = load_codex_credentials()
        if credentials is None:
            logger.warning("codex_oauth configured but no Codex credentials were found")
            return None

        async def _codex_token_provider(force_refresh: bool = False) -> str:
            return await get_valid_codex_token(force_refresh=force_refresh)

        return OpenAIProvider(
            api_key=credentials.access_token,
            model=config.llm.openai.model or "gpt-4o",
            base_url=config.llm.openai.base_url,
            token_provider=_codex_token_provider,
            timeout=float(config.llm.timeout),
        )
    if not config.llm.openai.api_key.strip():
        return None
    return OpenAIProvider(
        api_key=config.llm.openai.api_key,
        model=config.llm.openai.model or "gpt-4o",
        base_url=config.llm.openai.base_url,
        timeout=float(config.llm.timeout),
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
        model=config.llm.deepseek.model or "deepseek-v4-flash",
        reasoning_effort=config.llm.deepseek.reasoning_effort,
        timeout=float(config.llm.timeout),
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
    )


def _maybe_ollama_provider(config: Config, overrides: dict[str, LLMProvider]) -> LLMProvider | None:
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
    if not model and not raw_base_url:
        return None
    base_url = raw_base_url or "http://localhost:11434/v1"
    # Normalise: Ollama's OpenAI-compat shim lives at `/v1/...`. Older
    # config.example.toml shipped `http://localhost:11434` (no /v1),
    # which makes the OpenAI SDK call `/chat/completions` — Ollama 404s
    # those. Append /v1 defensively so existing users with stale configs
    # still get working chat completions after upgrade.
    if not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    return OllamaProvider(
        api_key=config.llm.ollama.api_key or "ollama",
        model=model or "llama3",
        base_url=base_url,
        timeout=float(config.llm.timeout),
        num_ctx=int(config.llm.ollama.num_ctx),
    )


def _ollama_is_chat_capable(config: Config) -> bool:
    """Decide whether the registered Ollama instance can serve chat
    completions, or only embedding requests.

    The user opts in to chat capability by any of:
      * setting ``[llm.ollama] model`` (their explicit chat model), or
      * picking ``ollama`` as ``[llm].default_provider``, or
      * naming ``ollama`` as ``[llm].fallback_provider`` — an explicit
        request to use local Ollama as the chat fallback, or
      * using it in any per-module override.

    If none of those are true and we only registered Ollama because the
    embedding section pointed there, treat it as embedding-only. The
    fallback chain will skip it for chat completions, avoiding the
    "All providers failed (..., ollama). Last error: ollama request
    failed: 404" path when the only model on disk is bge-m3.

    Note: when chat capability comes *solely* from ``fallback_provider``
    (no explicit ``[llm.ollama] model``), the provider is built with the
    ``llama3`` default — so the user must have a chat model pulled
    locally for the fallback to actually serve requests. That's the
    user's stated intent though, so a 404 at fallback time is a louder,
    more honest failure than silently dropping Ollama from the chain.
    """
    if config.llm.ollama.model.strip():
        return True
    if config.llm.default_provider.strip().lower() == "ollama":
        return True
    if config.llm.fallback_provider.strip().lower() == "ollama":
        return True
    for module in ("soul", "discovery", "recommendation", "evaluation"):
        module_cfg = getattr(config.llm, module, None)
        if module_cfg is None:
            continue
        if str(getattr(module_cfg, "provider", "")).strip().lower() == "ollama":
            return True
    return False


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
    )
