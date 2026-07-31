"""Configuration management for OpenBiliClaw.

Loads configuration from TOML files with environment variable overrides.
SchedulerConfig.enabled is the authoritative gate for background LLM loops.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# A per-day task-count cap in this range almost always means the user mistook
# ``daily_*_budget`` for an on/off toggle (typed ``1`` to "enable" a source,
# which actually throttles it to one task per day). ``0`` = unlimited.
_SUSPICIOUS_BUDGET_LOW = 1
_SUSPICIOUS_BUDGET_HIGH = 4
# Guards the once-per-process warning so repeated config reloads don't spam.
_warned_budget_keys: set[str] = set()

# Default config search paths
_CONFIG_FILENAMES = ["config.toml", "config.local.toml"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT_ENV = "OPENBILICLAW_PROJECT_ROOT"
_SUPPORTED_AUTH_METHODS = {"cookie", "qrcode", "none"}
_SUPPORTED_OPENAI_AUTH_MODES = {"", "api_key", "codex_oauth"}
_SUPPORTED_OPENAI_API_FLAVORS = {"", "chat_completions", "responses"}
# Keep in sync with llm/registry.py `_EMBEDDING_CAPABLE_PROVIDERS` (config
# cannot import the registry — cycle). An unknown name silently disables the
# embedding service, so saves are validated as blocking (field 2026-07-05:
# browser page-translation rewrote '奥拉玛' into config via value-less
# <option> elements).
_SUPPORTED_EMBEDDING_PROVIDERS = {
    "",
    "ollama",
    "openai",
    "gemini",
    "openai_compatible",
    # Alibaba DashScope native multimodal embedding (qwen3-vl). Must stay in
    # sync with the registry's dedicated embedding providers — otherwise the
    # backend can build it but config-save validation rejects it (drift caught
    # by the multimodal cover-embedding E2E, 2026-07-14).
    "dashscope",
}
# Keep in sync with llm/registry.py `build_llm_registry` provider_specs
# (config cannot import the registry — cycle). Used to validate
# `[llm].fallback_provider`: an unknown name is silently dropped by the
# chat fallback chain (`base.py:_fallback_order`), so saves are validated
# as blocking.
_SUPPORTED_CHAT_PROVIDERS = {
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
    "openai_compatible",
}
_LLM_INSTANCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LLM_PROVIDER_DISPLAY_NAMES = {
    "openai": "OpenAI",
    "claude": "Claude",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openrouter": "OpenRouter",
    "openai_compatible": "OpenAI-compatible",
}
_MIN_POOL_TARGET_COUNT = 1
_MAX_POOL_TARGET_COUNT = 600
_DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS = 90
_DEFAULT_EXTENSION_TOKEN_TTL_HOURS = 24
_DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS = 60
_DEFAULT_SIGNAL_EVENT_THRESHOLD = 6
_DEFAULT_TRENDING_REFRESH_MINUTES = 3
_DEFAULT_EXPLORE_REFRESH_MINUTES = 3
_DEFAULT_DISCOVERY_LIMIT = 30
_DEFAULT_DELIGHT_QUEUE_LIMIT = 20
_DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS = 120
_DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES = 30
_DEFAULT_FEEDBACK_BATCH_THRESHOLD = 3
_MIN_AUTO_UPDATE_CHECK_INTERVAL_HOURS = 1
_DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_HOURS = 6
# Unified keyword planner (Discover backpressure refactor P1, spec §6).
# All defaults are the owner-approved starting baseline; see
# docs/plans/2026-06-14-discover-backpressure-refactor-design.md §6 and
# docs/plans/2026-06-14-discover-backpressure-P1-plan.md §P1.0.
_DEFAULT_UNIFIED_KEYWORD_PLANNER_ENABLED = True
_DEFAULT_KW_CACHE_HIGH = 30
_DEFAULT_KW_CACHE_LOW = 10
_DEFAULT_GEN_BATCH = 30
_DEFAULT_FETCH_BATCH = 5
_DEFAULT_HISTORY_WINDOW_SIZE = 150
_DEFAULT_HISTORY_WINDOW_HOURS = 48
_DEFAULT_CLAIM_LEASE_MINUTES = 10
_DEFAULT_PLANNER_POLL_SECONDS = 120
_DEFAULT_PLAN_TTL_HOURS = 12
# Phase-2 config collapse: these constants are the ``medium`` breadth tier
# (the pre-collapse per-knob defaults, item-identical — a table-driven test
# guards the equality so upgrading is zero behavior drift).
_DEFAULT_INSPIRATION_ASPECT_WINDOW_SIZE = 32
_DEFAULT_INSPIRATION_INTEREST_SAMPLE_SIZE = 6
_DEFAULT_INSPIRATION_MAX_PROBE_SEARCHES_PER_STAGE = 12
_DEFAULT_INSPIRATION_PLATFORMS_PER_PROBE = 2
_DEFAULT_INSPIRATION_RISKCONTROLLED_PROBE_BUDGET = 4
_DEFAULT_INSPIRATION_SEARCH_PAGES_PER_PROBE = 1
_DEFAULT_INSPIRATION_SEARCH_RESULTS_PER_QUERY = 5
_DEFAULT_INSPIRATION_MAX_SEEDS_PER_ASPECT = 3
_DEFAULT_INSPIRATION_MAX_KEYWORDS_PER_PLATFORM = 12
_DEFAULT_INSPIRATION_BREADTH = "high"
_DEFAULT_INSPIRATION_SEARCH_BACKENDS: tuple[str, ...] = (
    "local_cache",
    "platform_sources",
    "exa",
    "you",
)
_DEFAULT_ADMISSION_MIN_SCORE = 0.60
_DEFAULT_CANDIDATE_EVAL_CONCURRENCY = 3
_DEFAULT_MULTIMODAL_BATCH_SIZE = 8
_DEFAULT_MULTIMODAL_IMAGE_MAX_PX = 384
_DEFAULT_MULTIMODAL_IMAGE_QUALITY = 72
_DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS = 6
DEFAULT_LLM_CONCURRENCY = 4
_MIN_LLM_CONCURRENCY = 1
_MAX_LLM_CONCURRENCY = 16
# Slow reasoning / OpenAI-compatible relays can legitimately take well over
# five minutes for one long response; 20 minutes is the product request ceiling.
_DEFAULT_LLM_TIMEOUT = 1200
_MIN_LLM_TIMEOUT = 10
_MAX_LLM_TIMEOUT = 1200
_DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 5,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
    "twitter": 1,
    "zhihu": 1,
    "reddit": 1,
    "bangumi": 1,
}
_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES = [
    "https://github.com/whiteguo233/OpenBiliClaw.git",
    "git@github.com:whiteguo233/OpenBiliClaw.git",
]
_REMOTE_PROVIDER_FIELDS = {
    "openai": "llm.openai.api_key",
    "claude": "llm.claude.api_key",
    "gemini": "llm.gemini.api_key",
    "deepseek": "llm.deepseek.api_key",
    "openrouter": "llm.openrouter.api_key",
    # v0.3.32+ — generic OpenAI-protocol-compatible provider (Groq /
    # Together / Azure OpenAI / vLLM / self-hosted, etc.). Distinct from
    # ``openai`` so users can run both in parallel (chat = openai for
    # gpt-5-nano, openai_compatible = Groq for fast Llama drafting).
    "openai_compatible": "llm.openai_compatible.api_key",
}


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class ConfigIssue:
    """A user-facing configuration problem."""

    field: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class LegacyConfigExportIssue:
    """One semantic loss required when projecting v2 LLM routing to v1."""

    code: str
    message: str


@dataclass(frozen=True)
class LegacyConfigExportReport:
    """Compatibility details for an explicit legacy-config export."""

    source_was_native: bool
    primary_instance_id: str = ""
    fallback_instance_id: str = ""
    issues: tuple[LegacyConfigExportIssue, ...] = ()

    @property
    def lossy(self) -> bool:
        """Return whether the old schema cannot express the full v2 intent."""
        return bool(self.issues)


@dataclass
class ConfigDiagnostics:
    """Supplementary information collected during config loading."""

    config_path: Path | None = None
    created_default_config: bool = False
    messages: list[str] = field(default_factory=list)
    issues: list[ConfigIssue] = field(default_factory=list)


@dataclass(frozen=True)
class InspirationBreadthParams:
    """Effective keyword-inspiration knobs derived from ``inspiration_breadth``.

    Phase-2 config collapse (13 → 4): the ten per-knob ``inspiration_*`` config
    fields were removed; consumers read this derived view instead. CLI one-shot
    overrides (``--limit`` / ``--interest-limit``) are applied on a copy of this
    object and injected via planner construction — never through config fields.
    """

    aspect_window_size: int
    interest_sample_size: int
    max_probe_searches_per_stage: int
    platforms_per_probe: int
    riskcontrolled_probe_budget: int
    search_pages_per_probe: int
    search_results_per_query: int
    max_seeds_per_aspect: int
    max_keywords_per_platform: int


_INSPIRATION_BREADTH_TIERS: dict[str, InspirationBreadthParams] = {
    "low": InspirationBreadthParams(
        aspect_window_size=16,
        interest_sample_size=3,
        max_probe_searches_per_stage=6,
        platforms_per_probe=1,
        riskcontrolled_probe_budget=2,
        search_pages_per_probe=1,
        search_results_per_query=3,
        max_seeds_per_aspect=2,
        max_keywords_per_platform=8,
    ),
    "medium": InspirationBreadthParams(
        aspect_window_size=_DEFAULT_INSPIRATION_ASPECT_WINDOW_SIZE,
        interest_sample_size=_DEFAULT_INSPIRATION_INTEREST_SAMPLE_SIZE,
        max_probe_searches_per_stage=_DEFAULT_INSPIRATION_MAX_PROBE_SEARCHES_PER_STAGE,
        platforms_per_probe=_DEFAULT_INSPIRATION_PLATFORMS_PER_PROBE,
        riskcontrolled_probe_budget=_DEFAULT_INSPIRATION_RISKCONTROLLED_PROBE_BUDGET,
        search_pages_per_probe=_DEFAULT_INSPIRATION_SEARCH_PAGES_PER_PROBE,
        search_results_per_query=_DEFAULT_INSPIRATION_SEARCH_RESULTS_PER_QUERY,
        max_seeds_per_aspect=_DEFAULT_INSPIRATION_MAX_SEEDS_PER_ASPECT,
        max_keywords_per_platform=_DEFAULT_INSPIRATION_MAX_KEYWORDS_PER_PLATFORM,
    ),
    "high": InspirationBreadthParams(
        aspect_window_size=48,
        interest_sample_size=8,
        max_probe_searches_per_stage=20,
        platforms_per_probe=3,
        riskcontrolled_probe_budget=8,
        search_pages_per_probe=2,
        search_results_per_query=8,
        max_seeds_per_aspect=5,
        max_keywords_per_platform=16,
    ),
}

# The ten collapsed ``[discovery]`` keys (hard-removed, no compat shim). A
# raw-config scan surfaces a removal notice through the diagnostics channel.
_REMOVED_INSPIRATION_DISCOVERY_KEYS: tuple[str, ...] = (
    "inspiration_aspect_window_size",
    "inspiration_interest_sample_size",
    "inspiration_max_probe_searches_per_stage",
    "inspiration_platforms_per_probe",
    "inspiration_riskcontrolled_probe_budget",
    "inspiration_search_pages_per_probe",
    "inspiration_search_results_per_query",
    "inspiration_max_seeds_per_aspect",
    "inspiration_max_expansions_per_seed",
    "inspiration_max_keywords_per_platform",
)


def derive_inspiration_breadth_params(breadth: object) -> InspirationBreadthParams:
    """Return the effective inspiration knobs for a breadth tier.

    Raises :class:`ConfigError` for anything but ``low`` / ``medium`` / ``high``.
    """

    tier = str(breadth or "").strip().lower()
    params = _INSPIRATION_BREADTH_TIERS.get(tier)
    if params is None:
        raise ConfigError(
            f"discovery.inspiration_breadth 必须是 low / medium / high，收到 {breadth!r}。"
        )
    return params


def _removed_discovery_key_issues(raw: dict[str, Any]) -> list[ConfigIssue]:
    discovery_raw = raw.get("discovery")
    if not isinstance(discovery_raw, dict):
        return []
    return [
        ConfigIssue(
            field=f"discovery.{key}",
            message=(
                f"`{key}` 已移除，值被忽略，请改用 `inspiration_breadth`（low / medium / high）。"
            ),
        )
        for key in _REMOVED_INSPIRATION_DISCOVERY_KEYS
        if key in discovery_raw
    ]


@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    auth_mode: str = ""
    # OpenAI-protocol endpoint selector: "" / "chat_completions" →
    # /v1/chat/completions (default); "responses" → /v1/responses. Some
    # third-party gateways expose GPT models only via the Responses API
    # (issue #72). Honored by [llm.openai] and [llm.openai_compatible];
    # ignored by all other providers.
    api_flavor: str = ""
    http_referer: str = ""
    x_title: str = ""
    # Balanced provider-native reasoning default.  LLMService overrides channel
    # extraction/scoring/copy callers to ""; adapters disable thinking or use
    # the cheapest supported approximation. Generic OpenAI-compatible routes
    # honor an explicitly configured non-empty value as an advisory
    # pass-through; configurations that predate that behavior are loaded with
    # an empty value so upgrades do not start sending a new request field.
    # Ollama ignores this field.
    reasoning_effort: str = "medium"
    # Ollama-only: context window (tokens). 0 = use Ollama's server default
    # (usually 4096) via the OpenAI-compat ``/v1`` shim. When >0, chat routes
    # through Ollama's native ``/api/chat`` so ``options.num_ctx`` actually
    # applies — the ``/v1`` shim silently ignores it, truncating large batch
    # prompts and breaking structured-JSON output. Ignored by all other
    # providers. See OllamaProvider._complete_native.
    num_ctx: int = 0


@dataclass
class LLMInstanceConfig(LLMProviderConfig):
    """One independently callable chat endpoint.

    The mapping key in ``LLMConfig.instances`` is the stable routing identity.
    ``provider_type`` selects the wire adapter while ``name`` is presentation
    only, so multiple instances may share one provider type without colliding.
    """

    name: str = ""
    provider_type: str = ""
    enabled: bool = True


@dataclass
class EmbeddingConfig:
    """Embedding model configuration.

    v0.3.32+ owns its own ``api_key`` / ``base_url`` so the embedding
    provider is fully independent from ``[llm].default_provider`` and the
    chat-side ``[llm.<name>]`` blocks. Fallback to other embedding
    providers or chat-side credentials is opt-in via ``fallback_enabled``.
    """

    provider: str = ""  # Empty = embedding disabled until explicitly configured
    model: str = "gemini-embedding-001"
    api_key: str = ""
    base_url: str = ""
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    fallback_enabled: bool = False
    fallback_provider: str = ""
    # Optional cover image embedding (image-only vectors in the same space
    # as text). Requires a multimodal embedding model such as
    # gemini-embedding-2 or dashscope qwen3-vl-embedding. Default off so
    # local bge-m3 / text-only paths pay zero extra cost.
    multimodal_enabled: bool = False


@dataclass
class ModuleLLMConfig:
    """Per-module LLM route.

    ``inherit`` / ``chain`` are the v2 instance-routing contract. ``provider``
    / ``model`` remain for reading and serving legacy configurations.
    """

    provider: str = ""
    model: str = ""
    inherit: bool = True
    chain: list[str] = field(default_factory=list)


@dataclass
class LLMConfig:
    """LLM configuration with global defaults and per-module overrides."""

    default_provider: str = "deepseek"
    concurrency: int = DEFAULT_LLM_CONCURRENCY
    timeout: int = _DEFAULT_LLM_TIMEOUT
    # Non-empty = chat fallback on. There is no separate enable flag: the
    # legacy ``fallback_enabled`` bool was never consulted by the fallback
    # chain and has been removed (stale keys in old config.toml are ignored).
    fallback_provider: str = ""
    # v2 routing is opt-in on load and becomes authoritative after the new
    # settings UI saves ``instances`` / ``default_chain``. Keeping an explicit
    # marker distinguishes a deliberately empty v2 draft from a legacy config
    # whose fixed provider tables should be projected into instances.
    instance_routing: bool = False
    instances: dict[str, LLMInstanceConfig] = field(default_factory=dict)
    default_chain: list[str] = field(default_factory=list)
    openai: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    claude: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    gemini: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    deepseek: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    ollama: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    openrouter: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    # v0.3.32+ generic OpenAI-protocol-compatible provider. Always
    # requires an explicit base_url (otherwise it would just be ``openai``).
    openai_compatible: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    # Per-module overrides (empty = use global default)
    soul: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    discovery: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    recommendation: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    evaluation: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)


_LLM_PROVIDER_CONFIG_FIELDS = tuple(field_.name for field_ in fields(LLMProviderConfig))
_LLM_INSTANCE_CONFIG_FIELDS = tuple(field_.name for field_ in fields(LLMInstanceConfig))
_LLM_MODULE_BUCKETS = ("soul", "discovery", "recommendation", "evaluation")


def _copy_provider_to_instance(
    provider_type: str,
    provider: LLMProviderConfig,
    *,
    name: str | None = None,
    model: str | None = None,
) -> LLMInstanceConfig:
    values = {
        field_name: getattr(provider, field_name) for field_name in _LLM_PROVIDER_CONFIG_FIELDS
    }
    if model is not None:
        values["model"] = model
    return LLMInstanceConfig(
        **values,
        name=name or _LLM_PROVIDER_DISPLAY_NAMES.get(provider_type, provider_type),
        provider_type=provider_type,
        enabled=True,
    )


def _legacy_provider_is_visible(
    llm: LLMConfig,
    provider_type: str,
    provider: LLMProviderConfig,
) -> bool:
    """Return whether a fixed legacy provider is a real endpoint candidate.

    Historical example configs wrote a default model into every fixed
    provider block. Treating those template-only blocks as enabled v2
    instances makes an otherwise valid legacy config impossible to save:
    every unused remote template then fails API-key validation. Project only
    providers that legacy routing actually references or that carry usable
    credentials. Ollama is the credential-free exception.
    """
    referenced = {
        str(llm.default_provider or "").strip().lower(),
        str(llm.fallback_provider or "").strip().lower(),
    }
    for bucket in _LLM_MODULE_BUCKETS:
        route = getattr(llm, bucket)
        referenced.add(str(route.provider or "").strip().lower())
    if provider_type in referenced:
        return True
    if str(provider.api_key or "").strip():
        return True
    if provider_type == "openai" and str(provider.auth_mode or "").strip().lower() == "codex_oauth":
        return True
    if provider_type == "gemini" and bool(
        os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    ):
        return True
    return provider_type == "ollama" and bool(
        str(provider.model or "").strip() or str(provider.base_url or "").strip()
    )


def effective_llm_instances(llm: LLMConfig) -> dict[str, LLMInstanceConfig]:
    """Return authoritative v2 instances or a lossless legacy projection.

    This helper never mutates ``llm``. Merely loading an old config therefore
    does not rewrite it; the desktop settings page receives the projection and
    explicitly opts into v2 when it submits the new shape.
    """
    if llm.instance_routing:
        return dict(llm.instances)

    projected: dict[str, LLMInstanceConfig] = {}
    for provider_type in sorted(_SUPPORTED_CHAT_PROVIDERS):
        provider = getattr(llm, provider_type)
        if _legacy_provider_is_visible(llm, provider_type, provider):
            projected[provider_type] = _copy_provider_to_instance(provider_type, provider)

    default_provider = str(llm.default_provider or "").strip().lower()
    if default_provider in _SUPPORTED_CHAT_PROVIDERS and default_provider not in projected:
        projected[default_provider] = _copy_provider_to_instance(
            default_provider,
            getattr(llm, default_provider),
        )

    # A legacy module may pin a different model on the same provider. In v2 an
    # instance is a complete endpoint (including model), so preserve that intent
    # as a derived endpoint instead of smuggling a per-call model override into
    # the new chain.
    for bucket in _LLM_MODULE_BUCKETS:
        route = getattr(llm, bucket)
        provider_type = str(route.provider or default_provider).strip().lower()
        model = str(route.model or "").strip()
        if not model or provider_type not in _SUPPORTED_CHAT_PROVIDERS:
            continue
        base = getattr(llm, provider_type)
        existing = projected.get(provider_type)
        if existing is not None and existing.model.strip() == model:
            continue
        instance_id = f"legacy-{bucket}"
        projected[instance_id] = _copy_provider_to_instance(
            provider_type,
            base,
            name=f"{bucket} · {_LLM_PROVIDER_DISPLAY_NAMES.get(provider_type, provider_type)}",
            model=model,
        )
    return projected


def effective_llm_default_chain(llm: LLMConfig) -> list[str]:
    """Return the ordered global instance chain for native or legacy config."""
    if llm.instance_routing:
        return [str(item).strip().lower() for item in llm.default_chain if str(item).strip()]
    chain: list[str] = []
    for candidate in (llm.default_provider, llm.fallback_provider):
        instance_id = str(candidate or "").strip().lower()
        if instance_id and instance_id not in chain:
            chain.append(instance_id)
    return chain


def effective_llm_routes(llm: LLMConfig) -> dict[str, ModuleLLMConfig]:
    """Return module routes expressed with v2 instance chains."""
    if llm.instance_routing:
        return {bucket: getattr(llm, bucket) for bucket in _LLM_MODULE_BUCKETS}

    routes: dict[str, ModuleLLMConfig] = {}
    default_provider = str(llm.default_provider or "").strip().lower()
    projected = effective_llm_instances(llm)
    for bucket in _LLM_MODULE_BUCKETS:
        legacy = getattr(llm, bucket)
        provider_type = str(legacy.provider or default_provider).strip().lower()
        model = str(legacy.model or "").strip()
        if not legacy.provider.strip() and not model:
            routes[bucket] = ModuleLLMConfig(inherit=True)
            continue
        instance_id = provider_type
        derived_id = f"legacy-{bucket}"
        if model and derived_id in projected:
            instance_id = derived_id
        routes[bucket] = ModuleLLMConfig(
            provider=legacy.provider,
            model=legacy.model,
            inherit=False,
            chain=[instance_id] if instance_id else [],
        )
    return routes


def _copy_instance_to_provider(instance: LLMInstanceConfig) -> LLMProviderConfig:
    """Drop v2 identity fields while preserving the legacy endpoint fields."""
    return LLMProviderConfig(
        **{
            field_name: deepcopy(getattr(instance, field_name))
            for field_name in _LLM_PROVIDER_CONFIG_FIELDS
        }
    )


def _legacy_endpoint_signature(instance: LLMInstanceConfig) -> tuple[object, ...]:
    """Return fields a legacy per-module model override cannot change."""
    return tuple(
        getattr(instance, field_name)
        for field_name in _LLM_PROVIDER_CONFIG_FIELDS
        if field_name != "model"
    )


def project_config_to_legacy(config: Config) -> tuple[Config, LegacyConfigExportReport]:
    """Project native instance routing into the last fixed-provider schema.

    Legacy chat routing can express one endpoint per provider type, one global
    fallback of a *different* provider type, and one provider/model override per
    module. The projection is deterministic and reports every semantic collapse
    instead of silently claiming a lossless downgrade.
    """
    projected = deepcopy(config)
    llm = config.llm
    if not llm.instance_routing:
        return projected, LegacyConfigExportReport(
            source_was_native=False,
            primary_instance_id=str(llm.default_provider or "").strip().lower(),
            fallback_instance_id=str(llm.fallback_provider or "").strip().lower(),
        )

    issues: list[LegacyConfigExportIssue] = []
    enabled: dict[str, tuple[str, LLMInstanceConfig]] = {}
    disabled_ids: list[str] = []
    unsupported_ids: list[str] = []
    for raw_instance_id, instance in llm.instances.items():
        instance_id = str(raw_instance_id).strip().lower()
        provider_type = str(instance.provider_type or "").strip().lower()
        if not instance.enabled:
            disabled_ids.append(instance_id)
            continue
        if provider_type not in _SUPPORTED_CHAT_PROVIDERS:
            unsupported_ids.append(instance_id)
            continue
        enabled[instance_id] = (str(raw_instance_id), instance)

    if disabled_ids:
        issues.append(
            LegacyConfigExportIssue(
                code="disabled_instances_omitted",
                message=f"旧格式没有停用实例草稿，已省略：{', '.join(disabled_ids)}。",
            )
        )
    if unsupported_ids:
        issues.append(
            LegacyConfigExportIssue(
                code="unsupported_instances_omitted",
                message=f"旧格式无法保存这些未知 Provider 实例：{', '.join(unsupported_ids)}。",
            )
        )

    ordered_ids: list[str] = []
    for candidate in [
        *llm.default_chain,
        *llm.instances.keys(),
    ]:
        instance_id = str(candidate).strip().lower()
        if instance_id and instance_id not in ordered_ids:
            ordered_ids.append(instance_id)

    representatives: dict[str, tuple[str, LLMInstanceConfig]] = {}
    same_type_ids: dict[str, list[str]] = {}
    for instance_id in ordered_ids:
        entry = enabled.get(instance_id)
        if entry is None:
            continue
        _, instance = entry
        provider_type = str(instance.provider_type or "").strip().lower()
        same_type_ids.setdefault(provider_type, []).append(instance_id)
        representatives.setdefault(provider_type, (instance_id, instance))

    for provider_type, instance_ids in same_type_ids.items():
        if len(instance_ids) < 2:
            continue
        selected_id, _ = representatives[provider_type]
        omitted = [instance_id for instance_id in instance_ids if instance_id != selected_id]
        issues.append(
            LegacyConfigExportIssue(
                code="provider_instances_collapsed",
                message=(
                    f"旧格式每种 Provider 只能保留一个端点；`{provider_type}` 保留 "
                    f"`{selected_id}`，折叠：{', '.join(omitted)}。"
                ),
            )
        )

    valid_default_ids: list[str] = []
    invalid_default_ids: list[str] = []
    for candidate in llm.default_chain:
        instance_id = str(candidate).strip().lower()
        if instance_id in enabled:
            valid_default_ids.append(instance_id)
        elif instance_id:
            invalid_default_ids.append(instance_id)
    if invalid_default_ids:
        issues.append(
            LegacyConfigExportIssue(
                code="invalid_default_entries_omitted",
                message=(
                    "全局调用链中不存在、停用或不支持的实例已省略："
                    f"{', '.join(invalid_default_ids)}。"
                ),
            )
        )
    if not valid_default_ids:
        raise ValueError("无法导出旧格式：全局 LLM 调用链中没有可用实例。")

    primary_id = valid_default_ids[0]
    primary_instance = enabled[primary_id][1]
    primary_type = str(primary_instance.provider_type or "").strip().lower()
    fallback_id = ""
    for instance_id in valid_default_ids[1:]:
        instance = enabled[instance_id][1]
        if str(instance.provider_type or "").strip().lower() != primary_type:
            fallback_id = instance_id
            break

    representable_default_ids = {primary_id}
    if fallback_id:
        representable_default_ids.add(fallback_id)
    omitted_default_ids = [
        instance_id
        for instance_id in valid_default_ids
        if instance_id not in representable_default_ids
    ]
    if omitted_default_ids:
        issues.append(
            LegacyConfigExportIssue(
                code="default_chain_truncated",
                message=(
                    "旧格式的全局链最多只有“主 Provider + 一个不同类型备选”；"
                    f"已从全局链省略：{', '.join(omitted_default_ids)}。"
                ),
            )
        )

    projected_llm = projected.llm
    projected_llm.instance_routing = False
    projected_llm.instances = {}
    projected_llm.default_chain = []
    projected_llm.default_provider = primary_type
    projected_llm.fallback_provider = (
        str(enabled[fallback_id][1].provider_type or "").strip().lower() if fallback_id else ""
    )
    for provider_type in _SUPPORTED_CHAT_PROVIDERS:
        setattr(projected_llm, provider_type, LLMProviderConfig())
    for provider_type, (_, instance) in representatives.items():
        setattr(projected_llm, provider_type, _copy_instance_to_provider(instance))

    for bucket in _LLM_MODULE_BUCKETS:
        route = getattr(llm, bucket)
        if route.inherit:
            setattr(projected_llm, bucket, ModuleLLMConfig())
            continue

        route_ids = [
            str(candidate).strip().lower() for candidate in route.chain if str(candidate).strip()
        ]
        valid_route_ids = [instance_id for instance_id in route_ids if instance_id in enabled]
        invalid_route_ids = [instance_id for instance_id in route_ids if instance_id not in enabled]
        if invalid_route_ids:
            issues.append(
                LegacyConfigExportIssue(
                    code="invalid_module_entries_omitted",
                    message=(
                        f"`{bucket}` 模块链中不存在、停用或不支持的实例已省略："
                        f"{', '.join(invalid_route_ids)}。"
                    ),
                )
            )
        if not valid_route_ids:
            setattr(projected_llm, bucket, ModuleLLMConfig())
            issues.append(
                LegacyConfigExportIssue(
                    code="module_route_dropped",
                    message=f"`{bucket}` 模块没有可导出的实例，旧格式将改为继承全局链。",
                )
            )
            continue

        selected_route_id = valid_route_ids[0]
        selected_route_instance = enabled[selected_route_id][1]
        selected_route_type = str(selected_route_instance.provider_type or "").strip().lower()
        setattr(
            projected_llm,
            bucket,
            ModuleLLMConfig(
                provider=selected_route_type,
                model=str(selected_route_instance.model or ""),
            ),
        )
        if len(valid_route_ids) > 1:
            issues.append(
                LegacyConfigExportIssue(
                    code="module_chain_truncated",
                    message=(
                        f"旧格式的 `{bucket}` 模块没有独立 fallback；保留 "
                        f"`{selected_route_id}`，省略：{', '.join(valid_route_ids[1:])}。"
                    ),
                )
            )
        representative_id, representative = representatives[selected_route_type]
        if _legacy_endpoint_signature(selected_route_instance) != _legacy_endpoint_signature(
            representative
        ):
            issues.append(
                LegacyConfigExportIssue(
                    code="module_endpoint_rebound",
                    message=(
                        f"`{bucket}` 原本使用 `{selected_route_id}`，但旧格式只能复用 "
                        f"`{selected_route_type}` 的 `{representative_id}` 端点；"
                        "模块模型名会保留，Base URL / Token / 协议参数将随代表端点。"
                    ),
                )
            )

    return projected, LegacyConfigExportReport(
        source_was_native=True,
        primary_instance_id=primary_id,
        fallback_instance_id=fallback_id,
        issues=tuple(issues),
    )


def _gemini_api_key_from_env() -> str:
    """Return Gemini API key from official environment variables."""
    google_api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    return google_api_key or gemini_api_key


@dataclass
class BilibiliConfig:
    """Bilibili connection configuration."""

    auth_method: str = "cookie"
    cookie: str = ""
    # Explicit proxy for Bilibili requests only. Empty (default) means
    # direct connection: the client ignores env/system proxies because
    # they routinely trip B站 risk control (valid cookie shows as "not
    # logged in"). Set only if your network cannot reach B站 directly.
    proxy: str = ""
    browser_executable: str = ""
    browser_headed: bool = False


@dataclass
class NetworkConfig:
    """Outbound proxy for OVERSEAS clients only.

    Applies to the LLM SDKs (OpenAI/Claude/Gemini/DeepSeek/OpenRouter/
    openai_compatible chat+embedding), YouTube (yt-dlp), Bangumi (api.bgm.tv
    is Cloudflare-fronted and resolves overseas), the GitHub updater, and
    Codex OAuth token refresh. Bangumi's cover CDN (lain.bgm.tv) is overseas
    too but rides the image cache's own ``trust_env`` path rather than this
    setting. CN-direct clients (bilibili / douyin /
    ollama / CN-CDN image cache) never consume it — that isolation is pinned
    by tests/test_network_proxy_isolation.py. This is deliberately distinct
    from ``[bilibili].proxy`` (which routes B站 requests and is rarely set).

    ``mode`` is one of ``system`` (default; inherit HTTP(S)_PROXY / OS
    settings), ``direct`` (ignore env/system proxies), or ``custom`` (use
    ``proxy`` explicitly). Accepted proxy schemes: http / https / socks5 /
    socks5h.

    The default is ``system`` because every consumer listed above is an
    overseas-only service. Under ``direct`` a mainland-China user's first
    run dies on an opaque timeout — before they could plausibly find this
    setting — even though their machine already has a working proxy. With
    no proxy configured, ``system`` behaves exactly like a direct
    connection, so users outside that situation lose nothing.

    Only the "never configured" case moves. An explicit ``mode`` on disk is
    always honored verbatim, including ``mode = "direct"``; the explicit /
    defaulted split is decided by key presence in ``_build_network_config``.

    See docs/plans/2026-07-11-network-proxy-config-spec.md.
    """

    mode: str = "system"
    proxy: str = ""


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""

    enabled: bool = True
    pause_on_extension_disconnect: bool = False
    extension_disconnect_grace_seconds: int = _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    discovery_cron: str = "0 */8 * * *"
    pool_target_count: int = 300
    pool_source_shares: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_POOL_SOURCE_SHARES)
    )
    account_sync_interval_hours: int = 6
    refresh_check_interval_seconds: int = _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS
    signal_event_threshold: int = _DEFAULT_SIGNAL_EVENT_THRESHOLD
    # 2026-07-26: unit changed hours → minutes and both aligned to 3, so the
    # Bilibili main-discovery cadence matches every source producer's
    # ``min_interval_minutes``. A pool deficit is still the gate in front of
    # these; the interval is a floor, not a schedule. Legacy
    # ``*_refresh_hours`` keys are still read and converted on load.
    trending_refresh_minutes: int = _DEFAULT_TRENDING_REFRESH_MINUTES
    explore_refresh_minutes: int = _DEFAULT_EXPLORE_REFRESH_MINUTES
    discovery_limit: int = _DEFAULT_DISCOVERY_LIMIT
    delight_queue_limit: int = _DEFAULT_DELIGHT_QUEUE_LIMIT
    proactive_push_interval_seconds: int = _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS
    speculator_idle_interval_minutes: int = _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES
    # LLM-judged like/dislike topic consolidation (soul/consolidator.py).
    # Runs from the pipeline tick at most once per interval; dirty-check
    # and no-merge pair memory make steady-state runs nearly free.
    profile_consolidation_enabled: bool = True
    profile_consolidation_interval_hours: int = 12
    profile_consolidation_like_target_upper: int = 512
    profile_consolidation_like_target_soft: int = 450
    profile_consolidation_archive_enabled: bool = True
    speculation_interval_minutes: int = 10
    speculation_ttl_days: int = 3
    speculation_cooldown_days: int = 7
    speculation_confirmation_threshold: int = 3
    speculation_max_active: int = 5
    speculation_max_primary_interests: int = 15
    speculation_max_secondary_interests: int = 60
    avoidance_speculation_interval_minutes: int = 10
    avoidance_speculation_ttl_days: int = 3
    avoidance_speculation_cooldown_days: int = 7
    avoidance_speculation_confirmation_threshold: int = 3
    avoidance_speculation_max_active: int = 5
    feedback_batch_threshold: int = _DEFAULT_FEEDBACK_BATCH_THRESHOLD
    # 统一兴趣更新线（docs/plans/2026-07-27-unified-interest-line-spec.md）。
    # true 时 /api/feedback 把反馈喂进认知流水线快线，并让含 FEEDBACK 信号的
    # INTEREST 缓冲在达到 ``feedback_batch_threshold`` 时绕过最短间隔立即消费。
    # 2026-07-28 经真实 A/B 三道门与 A/A 噪声控制后默认开启；false 保留为
    # 逐字节回退到旧反馈批线的紧急开关。
    unified_interest_line: bool = True
    # Default off. The auto-updater pulls from GitHub releases and
    # restarts the backend when a newer version is detected, but it has
    # historically caused restart loops when the local
    # ``openbiliclaw.__version__`` drifts from the published release
    # tag. Opt-in only — set ``true`` in config.toml after the release
    # pipeline is reliable.
    auto_update_enabled: bool = False
    auto_update_check_interval_hours: int = _DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_HOURS
    auto_update_allow_prerelease: bool = False
    auto_update_allowed_remotes: list[str] = field(
        default_factory=lambda: list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)
    )


@dataclass
class DiscoveryConfig:
    """Unified keyword planner configuration (Discover backpressure, P1).

    Governs the double-buffered keyword store + merged keyword planner that
    replaces the per-platform search keyword generators. All knobs are gated
    behind ``unified_keyword_planner_enabled`` (default ON as of v0.3.124; set
    it ``false`` to fall back, byte-for-byte, to the legacy per-platform LLM
    generation path). See
    ``docs/plans/2026-06-14-discover-backpressure-refactor-design.md`` §6 for
    the parameter table these defaults come from. ``fetch_floor`` is NOT a
    field here — the planner reuses each platform's existing ``min_interval``.
    """

    # Master feature flag. True (default, v0.3.124+) runs the merged planner /
    # keyword store; False falls back to the legacy per-platform search keyword
    # generators (the path stays dormant and the fallback is byte-identical).
    unified_keyword_planner_enabled: bool = _DEFAULT_UNIFIED_KEYWORD_PLANNER_ENABLED
    # Per-platform keyword cache high/low watermarks. Generation fires when
    # pending < low and a real deficit exists; it refills up to high.
    kw_cache_high: int = _DEFAULT_KW_CACHE_HIGH
    kw_cache_low: int = _DEFAULT_KW_CACHE_LOW
    # Keywords generated per platform per merged LLM call.
    gen_batch: int = _DEFAULT_GEN_BATCH
    # Keywords atomically claimed per fetch.
    fetch_batch: int = _DEFAULT_FETCH_BATCH
    # Dedup history window: at most this many recent keywords, within this many
    # hours, are surfaced to the planner as "don't repeat".
    history_window_size: int = _DEFAULT_HISTORY_WINDOW_SIZE
    history_window_hours: int = _DEFAULT_HISTORY_WINDOW_HOURS
    # Claim lease: a claimed/executing keyword older than this is reclaimed to
    # pending (guards loop/task crashes leaking in-flight rows).
    claim_lease_minutes: int = _DEFAULT_CLAIM_LEASE_MINUTES
    # Keyword planner poll interval (seconds). Idle polls are near-zero cost.
    planner_poll_seconds: int = _DEFAULT_PLANNER_POLL_SECONDS
    # Plan staleness backstop: pending keywords older than this expire even if
    # the profile digest hasn't changed.
    plan_ttl_hours: int = _DEFAULT_PLAN_TTL_HOURS
    # Optional search-inspired query brainstorming stage. Default off: when
    # enabled, the keyword planner may use an injected search provider to mine
    # adjacent concepts and insert metadata-bearing keywords.
    inspiration_search_enabled: bool = False
    inspiration_search_backends: tuple[str, ...] = _DEFAULT_INSPIRATION_SEARCH_BACKENDS
    # Optional experiment mode: when true and inspiration search is available,
    # due platforms skip the legacy merged keyword planner and are filled only
    # through the search-inspired flow.
    inspiration_replace_merged_keywords: bool = False
    # Breadth tier (low / medium / high) replacing the ten per-knob fields —
    # effective values come from ``derive_inspiration_breadth_params``.
    inspiration_breadth: str = _DEFAULT_INSPIRATION_BREADTH
    # Unified recommendation-pool admission floor. Source/provenance metadata
    # must never bypass this; explicit strategy thresholds live on candidates.
    admission_min_score: float = _DEFAULT_ADMISSION_MIN_SCORE
    # Desired candidate-evaluation workers. The approved inventory-safe
    # 3×30 design caps this at three (90 raw candidates in flight); runtime
    # also reserves one global LLM slot, so the effective count may be lower.
    candidate_eval_concurrency: int = _DEFAULT_CANDIDATE_EVAL_CONCURRENCY
    # Optional cover-image evaluation. Kept off by default because it changes
    # LLM cost/latency and requires a vision-capable evaluation model.
    multimodal_evaluation_enabled: bool = False
    # Smaller batch for image-bearing evaluation calls.
    multimodal_batch_size: int = _DEFAULT_MULTIMODAL_BATCH_SIZE
    # Cover-image preprocessing bounds before sending to the evaluator.
    multimodal_image_max_px: int = _DEFAULT_MULTIMODAL_IMAGE_MAX_PX
    multimodal_image_quality: int = _DEFAULT_MULTIMODAL_IMAGE_QUALITY
    multimodal_image_timeout_seconds: int = _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS


@dataclass
class AutostartConfig:
    """Boot autostart configuration."""

    enabled: bool = False
    manage_ollama: bool = True


@dataclass
class XiaohongshuSourceConfig:
    """Xiaohongshu source-specific configuration.

    Content discovery and metadata extraction happens entirely in the
    user's browser via the Chrome extension (passive collection +
    background-tab tasks). No sidecar or backend crawling needed.
    """

    # XHS is opt-in because it requires the browser extension and a logged-in
    # browser session. Init --yes-xhs or the settings page can enable it later.
    enabled: bool = False
    # Max Soul-driven search tasks the backend may enqueue per day.
    daily_search_budget: int = 0
    # Max creator-subscription fetch tasks per day.
    daily_creator_budget: int = 0
    # Minimum seconds the backend permits between extension-dispatched
    # search/creator task claims. Persisted centrally so MV3/browser restarts
    # and multiple extension profiles cannot bypass the pacing floor.
    task_interval_seconds: int = 300
    # Minimum gap between two producer runs for this source. Aligned to 3
    # minutes across every source (2026-07-26) so pool replenishment has one
    # cadence instead of eight; the per-run size is still bounded by
    # ``[scheduler].discovery_limit`` and each branch's daily budget.
    min_interval_minutes: int = 3


@dataclass
class DouyinSourceConfig:
    """Douyin direct-cookie discovery configuration.

    Initialization bootstrap still uses the browser extension. These
    settings only control optional backend discovery jobs that read a
    user-supplied Douyin cookie from the environment.
    """

    enabled: bool = False
    mode: str = "direct"
    cookie_env: str = "OPENBILICLAW_DOUYIN_COOKIE"
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    request_interval_seconds: int = 2
    # Minimum gap between two producer runs for this source. Aligned to 3
    # minutes across every source (2026-07-26) so pool replenishment has one
    # cadence instead of eight; the per-run size is still bounded by
    # ``[scheduler].discovery_limit`` and each branch's daily budget.
    min_interval_minutes: int = 3


@dataclass
class YoutubeSourceConfig:
    """YouTube source-specific configuration.

    YouTube steady-state discovery runs through a backend-direct runtime
    producer. The budget knobs cap per-day execution units: search
    queries, trending fetch breadth, and subscribed-channel breadth.
    """

    enabled: bool = False
    daily_search_budget: int = 0
    daily_trending_budget: int = 0
    daily_channel_budget: int = 0
    request_interval_seconds: int = 2
    min_interval_minutes: int = 3


@dataclass
class TwitterSourceConfig:
    """X (Twitter) direct-cookie discovery configuration.

    Steady-state discovery is server-side cookie replay (search / For-You /
    creator), mirroring the Douyin-direct path. The X producer reads the
    budget / interval knobs below to throttle the three strategies and to
    keep the high-visibility For-You feed to a low daily cadence. ``0`` daily
    budgets mean "no per-day cap" (each due run is bounded by the runtime
    deficit), matching the Douyin / YouTube producer convention.
    """

    enabled: bool = False
    mode: str = "cookie"
    cookie_env: str = "OPENBILICLAW_X_COOKIE"
    daily_search_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


@dataclass
class ZhihuSourceConfig:
    """Zhihu plugin-backed discovery configuration.

    Zhihu discovery runs in the browser extension so it can reuse the user's
    logged-in browser session. The backend only enqueues search tasks and stores
    returned candidates in the unified discovery pool.
    """

    enabled: bool = False
    source_modes: tuple[str, ...] = ("search", "hot", "feed", "creator", "related")
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    daily_related_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


@dataclass
class RedditSourceConfig:
    """Reddit discovery configuration.

    Reddit currently depends on a logged-in session instead of a reliable
    anonymous API. ``backend="rdt"`` is the default steady-state discovery and
    event-smoke backend; ``extension`` remains available for OpenBiliClaw
    browser-plugin tasks and is still required for bootstrap saved / upvoted /
    subscribed initialization signals.
    """

    enabled: bool = False
    backend: str = "rdt"
    source_modes: tuple[str, ...] = ("search", "hot", "subreddit", "related")
    daily_search_budget: int = 300
    daily_hot_budget: int = 300
    daily_subreddit_budget: int = 300
    daily_related_budget: int = 300
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


@dataclass
class BangumiSourceConfig:
    """Bangumi official anonymous API discovery configuration."""

    enabled: bool = False
    username: str = ""
    # Optional Bangumi personal access token (https://next.bgm.tv/demo/access-token).
    # When set, discovery/init resolve the account via /v0/me and read private
    # collections with a Bearer header. Empty means the historical anonymous
    # public-username path. Never log the value — only presence/length.
    access_token: str = ""
    subject_types: tuple[str, ...] = ("anime", "book", "game")
    source_modes: tuple[str, ...] = ("search", "ranked", "latest")
    daily_search_budget: int = 300
    daily_ranked_budget: int = 100
    daily_latest_budget: int = 100
    request_interval_seconds: int = 1
    min_interval_minutes: int = 3
    bootstrap_limit: int = 300


@dataclass
class BilibiliSourceConfig:
    """Bilibili discovery source switch."""

    enabled: bool = True
    # Minimum gap between two producer runs for this source. Aligned to 3
    # minutes across every source (2026-07-26) so pool replenishment has one
    # cadence instead of eight; the per-run size is still bounded by
    # ``[scheduler].discovery_limit`` and each branch's daily budget.
    min_interval_minutes: int = 3


@dataclass
class SourcesConfig:
    """Multi-source content adapters configuration.

    Contains platform-level discovery switches and the generic browser options
    for non-Bilibili web adapters. The browser options here are independent of
    ``bilibili.browser`` (which controls the agent-browser CLI used by
    Bilibili login/QR flows).
    """

    # URL of a pre-launched Chrome DevTools endpoint, e.g.
    # ``http://127.0.0.1:9222``. When set, the web adapter connects via
    # Playwright ``chromium.connect_over_cdp`` and reuses that Chrome's
    # logged-in session. When empty, falls back to agent-browser CLI.
    browser_cdp_url: str = ""
    # Whether to launch a headed agent-browser (fallback path only).
    browser_headed: bool = False
    bilibili: BilibiliSourceConfig = field(default_factory=BilibiliSourceConfig)
    xiaohongshu: XiaohongshuSourceConfig = field(default_factory=XiaohongshuSourceConfig)
    douyin: DouyinSourceConfig = field(default_factory=DouyinSourceConfig)
    youtube: YoutubeSourceConfig = field(default_factory=YoutubeSourceConfig)
    twitter: TwitterSourceConfig = field(default_factory=TwitterSourceConfig)
    zhihu: ZhihuSourceConfig = field(default_factory=ZhihuSourceConfig)
    reddit: RedditSourceConfig = field(default_factory=RedditSourceConfig)
    bangumi: BangumiSourceConfig = field(default_factory=BangumiSourceConfig)


@dataclass
class StorageConfig:
    """Storage configuration."""

    db_path: str = "data/openbiliclaw.db"


@dataclass
class SavedSyncConfig:
    """External platform save synchronization."""

    auto_sync_enabled: bool = False


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "INFO"
    file_level: str = "DEBUG"
    directory: str = "logs"
    filename: str = "openbiliclaw.log"
    # v0.3.30+ 默认 100 MB(从 1024 降下来)。daemon 长跑场景历史 1 GB 太大,
    # 本机磁盘动辄被占几 GB。100 MB × 2 备份 = 200 MB,足够 1-2 周的 INFO 级日志。
    # 调试时可调高到 500-1024;>0 时启用轮转,设为 0 表示不轮转(仅调试用)。
    max_file_size_mb: int = 100
    # 保留的历史日志份数;至少为 1 才会真正轮转(0 会让 RotatingFileHandler 完全不轮转)。
    # 默认 1:每个 file_path 磁盘占用封顶在 `max_file_size_mb * 2`。
    backup_count: int = 1
    # v0.3.30+: ``logs/`` 目录里的 *unmanaged* 文件(start 脚本 stdout
    # redirect / 一次性 init 日志 / 旧版本残留 等)的总磁盘预算(MB)。启动
    # 时如果整个 logs/ 目录(含 unmanaged)超过这个值,从最老的 unmanaged
    # 文件开始删,直到回到预算内。设 0 关闭。默认 500 MB。
    aggregate_budget_mb: int = 500
    # 单个 unmanaged 日志文件超过这个 MB 数,启动时直接 truncate 到 0。
    # 抓 ``backend-restart.log`` 这类被脚本无限 append 但项目代码控制不到的
    # 文件。设 0 关闭。默认 200 MB。
    unmanaged_truncate_mb: int = 200
    # ``logs/`` 目录里超过这个天数的 *unmanaged* 文件,启动时直接删除。
    # 设 0 关闭。默认 30 天。
    unmanaged_max_age_days: int = 30

    @property
    def directory_path(self) -> Path:
        """Resolved log directory path."""
        path = Path(self.directory)
        if not path.is_absolute():
            path = _project_root() / path
        return path

    @property
    def file_path(self) -> Path:
        """Resolved full log file path."""
        return self.directory_path / self.filename


@dataclass
class SoulPreferenceConfig:
    """Preference-layer toggles.

    ``satisfaction_filter_enabled``: v0.3.x event-satisfaction signal —
    when True, the preference analyzer ignores passive negative events
    such as quick-exit while retaining explicit dislike feedback as
    disliked_topics evidence.
    """

    satisfaction_filter_enabled: bool = True


# Posture-gate save-time enforce readiness thresholds (spec §Phase 3, r4/R3-3).
# Calibrated to the single-user shadow cadence (~1-3 gate calls/day): 14 days of
# observation, ≥10 valid judgements, and recent coverage in the last 7 days.
# Revisit after any provider/model swap (pitfall #3).
POSTURE_GATE_ENFORCE_MIN_OBSERVATION_DAYS = 14
POSTURE_GATE_ENFORCE_MIN_VALID_JUDGEMENTS = 10
# Recent-coverage gate: ≥1 valid judgement within the last 7 days.
POSTURE_GATE_ENFORCE_RECENT_WINDOW_DAYS = 7
POSTURE_GATE_ENFORCE_MIN_RECENT_COUNT = 1
_POSTURE_GATE_MODES = frozenset({"shadow", "enforce", "off"})
_TOPIC_LIFECYCLE_SERIALIZATION_MODES = frozenset({"off", "on"})


@dataclass
class SoulConfig:
    """Soul engine knobs.

    ``posture_gate_mode``: deep-write consistency gate (spec §Phase 3).
    ``shadow`` (default) judges deep writes on an async side-channel without
    blocking; ``enforce`` gates synchronously; ``off`` is a full bypass whose
    behaviour is byte-identical to the pre-gate pipeline. ``enforce`` is only
    savable once shadow data proves ≥14 days of observation (save-time guard),
    unless ``posture_gate_force_enforce`` overrides it (documented risk).
    """

    preference: SoulPreferenceConfig = field(default_factory=SoulPreferenceConfig)
    posture_gate_mode: str = "shadow"
    posture_gate_force_enforce: bool = False
    # Topic-lifecycle serialization (spec §Phase 4). ``off`` (default) keeps the
    # LLM-facing profile serialization byte-identical to the pre-lifecycle shape
    # (回放门); ``on`` excludes archived topics from that serialization. This is
    # the only "minimal consumption" of the topic state machine in this version.
    topic_lifecycle_serialization: str = "off"


@dataclass
class ApiAuthConfig:
    """Optional password gate for LAN / remote access (see
    ``docs/plans/2026-05-30-web-password-auth-design.md``).

    Only takes effect when ``enabled`` is true *and* the request is not a
    trusted-local request (loopback without forwarding headers, see §4.1).
    ``session_secret`` is auto-generated on first enable. The revocation epoch
    (``auth_epoch``) and password fingerprint live in SQLite, not here (§4.7).
    """

    enabled: bool = False
    password_hash: str = ""
    session_secret: str = ""
    session_ttl_hours: int = 0
    trust_loopback: bool = True
    trusted_proxies: list[str] = field(default_factory=list)
    allowed_bearer_origins: list[str] = field(default_factory=list)
    extension_access_enabled: bool = False
    extension_access_keys: list[str] = field(default_factory=list)
    extension_token_ttl_hours: int = _DEFAULT_EXTENSION_TOKEN_TTL_HOURS


@dataclass
class TlsProxyConfig:
    """Optional TLS reverse proxy for remote device access.

    When ``enabled`` is true and ``cryptography`` is installed,
    ``serve-api`` starts a background thread that terminates TLS
    on ``port`` and forwards to the API.

    ``cert_dir`` defaults to ``{data_dir}/certs`` at runtime;
    leave empty to use the default.
    """

    enabled: bool = False
    port: int = 2119
    cert_dir: str = ""
    san_names: list[str] = field(default_factory=list)


@dataclass
class ApiConfig:
    """Backend API server settings.

    ``host`` controls which network interface the server binds to.
    ``0.0.0.0`` (default) binds all interfaces so mobile devices on the
    same LAN can reach the ``/m/`` mobile web.  ``127.0.0.1`` restricts
    access to this machine only.
    """

    host: str = "0.0.0.0"
    port: int = 8420
    auth: ApiAuthConfig = field(default_factory=ApiAuthConfig)


@dataclass
class Config:
    """Root configuration for OpenBiliClaw."""

    language: str = "zh"
    data_dir: str = "data"
    api: ApiConfig = field(default_factory=ApiConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)
    # Overseas-outbound proxy (LLM SDKs / YouTube / Bangumi / updater).
    # CN-direct clients never use it — see NetworkConfig docstring.
    network: NetworkConfig = field(default_factory=NetworkConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    # Top-level `[discovery]` carries the unified keyword planner / backpressure
    # knobs (P1). Distinct from `[llm.discovery]` (per-module provider override).
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    autostart: AutostartConfig = field(default_factory=AutostartConfig)
    saved_sync: SavedSyncConfig = field(default_factory=SavedSyncConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Top-level `[soul]` is distinct from `[llm.soul]` (per-module
    # provider override): this carries soul-engine behavior toggles.
    soul: SoulConfig = field(default_factory=SoulConfig)
    tls_proxy: TlsProxyConfig = field(default_factory=TlsProxyConfig)

    @property
    def data_path(self) -> Path:
        """Resolved data directory path."""
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = _project_root() / p
        return p


def _project_root() -> Path:
    """Return the runtime project root used for config, data, and logs."""
    env_root = os.environ.get(_PROJECT_ROOT_ENV, "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    if _looks_like_project_root(_PROJECT_ROOT):
        return _PROJECT_ROOT

    cwd = Path.cwd().resolve()
    if any((cwd / filename).exists() for filename in [*_CONFIG_FILENAMES, "config.example.toml"]):
        return cwd

    return _PROJECT_ROOT


def _looks_like_project_root(path: Path) -> bool:
    """Return whether a path resembles the repository/runtime root."""
    return any(
        (path / marker).exists()
        for marker in ["pyproject.toml", "config.example.toml", "config.toml"]
    )


def _default_config_path() -> Path:
    """Return the default config.toml path."""
    return _project_root() / "config.toml"


def _config_example_path() -> Path:
    """Return the repository config example path."""
    return _project_root() / "config.example.toml"


def _ensure_default_config_file(diagnostics: ConfigDiagnostics) -> None:
    """Create config.toml from the example file when it is missing."""
    config_path = _default_config_path()
    diagnostics.config_path = config_path

    if config_path.exists():
        return

    example_path = _config_example_path()
    if not example_path.exists():
        diagnostics.messages.append(
            "未检测到 config.toml，且缺少 config.example.toml，当前使用内置默认配置。"
        )
        return

    shutil.copyfile(example_path, config_path)
    diagnostics.created_default_config = True
    diagnostics.messages.append(f"未检测到 config.toml，已自动生成模板文件：{config_path}。")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts, override values take precedence."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides.

    Environment variables follow the pattern: OPENBILICLAW_SECTION_KEY
    e.g. OPENBILICLAW_LLM_DEFAULT_PROVIDER=claude
    """
    prefix = "OPENBILICLAW_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        # Auth vars are multi-word (PASSWORD_HASH, SESSION_TTL_HOURS, …); the naive
        # `_` split would mis-nest them — e.g. PASSWORD_HASH → api.auth.password.hash,
        # injecting a dict at auth.password (later hashed as its repr) or raising
        # TypeError when an on-disk plaintext `password` string is descended into.
        # `_build_api_auth` reads every API_AUTH_ENV_VARS var explicitly, so skip
        # them here entirely (review r7#1).
        if env_key in API_AUTH_ENV_VARS:
            continue
        parts = env_key[len(prefix) :].lower().split("_")
        current = raw
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = env_value
    return raw


def _filter_dataclass_kwargs(
    cls: type[Any],
    raw: dict[str, Any],
    *,
    section: str,
) -> dict[str, Any]:
    """Drop unknown keys before ``cls(**kwargs)`` so a newer-branch field
    (or a typo) cannot crash ``load_config`` with a bare TypeError.

    Logs one WARNING per ignored key, naming the TOML section and key so
    the user can clean up after a version rollback. Known fields keep their
    raw values; callers still apply type coercion after this filter.
    """
    if not isinstance(raw, dict):
        return {}
    known = {item.name for item in fields(cls)}
    unknown = sorted(key for key in raw if key not in known)
    for key in unknown:
        logger.warning(
            "config: [%s].%s ignored (unknown key for this version)",
            section,
            key,
        )
    return {key: value for key, value in raw.items() if key in known}


def _warn_suspicious_budgets(sources: SourcesConfig) -> None:
    """Warn once per process for per-source budgets that look like misused toggles.

    ``daily_*_budget`` is a per-UTC-day task-count cap, not an on/off switch; ``0``
    means unlimited. A value of 1–4 almost always means the user typed ``1`` to
    "enable" a source and unknowingly throttled it to a single task per day.
    """
    source_configs: list[tuple[str, Any]] = [
        ("xiaohongshu", sources.xiaohongshu),
        ("douyin", sources.douyin),
        ("youtube", sources.youtube),
        ("twitter", sources.twitter),
        ("zhihu", sources.zhihu),
        ("reddit", sources.reddit),
        ("bangumi", sources.bangumi),
    ]
    for source_name, source_config in source_configs:
        for source_field in fields(source_config):
            name = source_field.name
            if not (name.startswith("daily_") and name.endswith("_budget")):
                continue
            value = getattr(source_config, name)
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            if not (_SUSPICIOUS_BUDGET_LOW <= value <= _SUSPICIOUS_BUDGET_HIGH):
                continue
            key = f"sources.{source_name}.{name}"
            if key in _warned_budget_keys:
                continue
            _warned_budget_keys.add(key)
            logger.warning(
                "config: %s=%d — 这是每日任务次数上限,不是开关;想不限次数请设为 0",
                key,
                value,
            )


# Whitelist for [network].proxy. httpx[socks] (pyproject) covers socks5/socks5h;
# http/https cover CONNECT proxies. Anything else (ftp, socks4, bare host) is a
# user error we reject at save time rather than silently ignore (pitfall rule 7).
_OUTBOUND_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
_OUTBOUND_PROXY_MODES = frozenset({"direct", "system", "custom"})


def normalize_outbound_proxy_mode(value: str) -> str:
    """Normalize an overseas routing mode, or raise a user-facing error."""
    mode = value.strip().lower()
    if mode not in _OUTBOUND_PROXY_MODES:
        raise ValueError("网络代理模式仅支持 direct / system / custom")
    return mode


def normalize_outbound_proxy(value: str) -> str:
    """Normalize an overseas-outbound proxy URL, or raise ``ValueError``.

    Returns ``""`` for empty/whitespace input (proxy disabled). Otherwise
    strips surrounding whitespace, lowercases the scheme, and validates that
    the scheme is whitelisted and a host is present. The raise message is a
    user-facing Chinese reason surfaced directly in the settings UI.
    """
    text = value.strip()
    if not text:
        return ""
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    if scheme not in _OUTBOUND_PROXY_SCHEMES:
        raise ValueError(
            f"代理协议不支持:{parsed.scheme or '(缺少协议)'};仅支持 http / https / socks5 / socks5h"
        )
    if not parsed.hostname:
        raise ValueError("代理地址缺少主机名,请填写形如 socks5://127.0.0.1:1080 的地址")
    # Preserve userinfo/host/port/path verbatim; only the scheme is lowercased.
    return f"{scheme}{text[len(parsed.scheme) :]}"


def _build_network_config(raw: dict[str, Any]) -> NetworkConfig:
    """Assemble ``NetworkConfig`` from the raw ``[network]`` table.

    The ``mode`` **key's presence** — not its resolved value — separates a
    deliberate choice from an unset one. Absent means the user never
    configured overseas routing, so it takes the ``system`` default (see
    :class:`NetworkConfig`); present is honored verbatim, so an explicit
    ``mode = "direct"`` keeps ignoring env/system proxies. ``mode`` reaches
    here from ``config.toml`` and from ``OPENBILICLAW_NETWORK_MODE``, which
    ``_apply_env_overrides`` injects into this same table — an env var is
    an explicit choice too, and lands on the present branch for free.

    Invalid on-disk values are logged at WARNING and dropped to the empty
    default rather than crashing load (pitfall rule 4 clamp-to-default); the
    save-time API guard is what rejects invalid *writes* with a 400. Both
    invalid-value clamps below deliberately land on ``direct`` rather than
    on the ``system`` field default: the user did write *something* there,
    and refusing to silently start routing their traffic through an
    inherited env proxy is the conservative reading of a broken value.
    """
    network_raw = raw.get("network", {})
    if not isinstance(network_raw, dict):
        network_raw = {}
    proxy_raw = str(network_raw.get("proxy", "") or "")
    try:
        proxy = normalize_outbound_proxy(proxy_raw)
    except ValueError as exc:
        logger.warning("config: [network].proxy 非法已忽略(%s):%s", proxy_raw, exc)
        proxy = ""
    mode_present = "mode" in network_raw
    mode_raw = str(network_raw.get("mode", "") or "")
    if not mode_present:
        # Backward-compatible migration: legacy non-empty [network].proxy was
        # explicitly configured by the user and therefore remains custom.
        # Everything else is genuinely unconfigured and takes the default.
        mode = "custom" if proxy else "system"
    else:
        try:
            mode = normalize_outbound_proxy_mode(mode_raw)
        except ValueError as exc:
            logger.warning("config: [network].mode 非法已回退 direct(%s):%s", mode_raw, exc)
            mode = "direct"
    if mode == "custom" and not proxy:
        logger.warning("config: [network].mode=custom 但 proxy 为空,已回退 direct")
        mode = "direct"
    return NetworkConfig(mode=mode, proxy=proxy)


def _build_config(raw: dict[str, Any]) -> Config:
    """Build a Config dataclass from raw dict."""
    general = raw.get("general", {})
    api_raw = raw.get("api", {}) if isinstance(raw.get("api"), dict) else {}
    llm_raw = raw.get("llm", {})
    if not isinstance(llm_raw, dict):
        raise ConfigError("`[llm]` 必须是 TOML table，不能是字符串或其它标量。")
    bili_raw = raw.get("bilibili", {})
    sources_raw = raw.get("sources", {})
    sched_raw = dict(raw.get("scheduler", {}))
    # ``SchedulerConfig(**sched_raw)`` below splats the raw table, so a key the
    # dataclass no longer declares is a hard TypeError at load time. The
    # 2026-07-26 hours → minutes rename would therefore have bricked startup for
    # every existing config.toml. Resolve the legacy keys first, then drop them.
    _legacy_refresh_minutes = {
        "trending_refresh": _legacy_hours_to_minutes(sched_raw, "trending_refresh"),
        "explore_refresh": _legacy_hours_to_minutes(sched_raw, "explore_refresh"),
    }
    for _legacy_key in ("trending_refresh_hours", "explore_refresh_hours"):
        sched_raw.pop(_legacy_key, None)
    discovery_raw = raw.get("discovery", {})
    if not isinstance(discovery_raw, dict):
        discovery_raw = {}
    autostart_raw = raw.get("autostart", {})
    if not isinstance(autostart_raw, dict):
        autostart_raw = {}
    saved_sync_raw = raw.get("saved_sync", {})
    if not isinstance(saved_sync_raw, dict):
        saved_sync_raw = {}
    store_raw = raw.get("storage", {})
    logging_raw = raw.get("logging", {})

    embedding_raw = llm_raw.get("embedding", {})
    if not isinstance(embedding_raw, dict):
        embedding_raw = {}
    instances_raw = llm_raw.get("instances", {})
    if not isinstance(instances_raw, dict):
        instances_raw = {}
    try:
        routing_version = int(llm_raw.get("routing_version", 0) or 0)
    except (TypeError, ValueError):
        routing_version = 0
        logger.warning("config: [llm].routing_version 非法，已按旧版配置读取")
    instance_routing = bool(
        routing_version >= 2
        or "instances" in llm_raw
        or "default_chain" in llm_raw
        or "routes" in llm_raw
    )
    instances: dict[str, LLMInstanceConfig] = {}
    for raw_instance_id, raw_instance in instances_raw.items():
        if not isinstance(raw_instance, dict):
            continue
        instance_id = str(raw_instance_id).strip()
        values = {
            key: value for key, value in raw_instance.items() if key in _LLM_INSTANCE_CONFIG_FIELDS
        }
        for string_field in (
            "name",
            "provider_type",
            "api_key",
            "model",
            "base_url",
            "auth_mode",
            "api_flavor",
            "http_referer",
            "x_title",
            "reasoning_effort",
        ):
            if string_field in values:
                values[string_field] = str(values[string_field] or "")
        for normalized_field in ("provider_type", "auth_mode", "api_flavor"):
            if normalized_field in values:
                values[normalized_field] = str(values[normalized_field]).strip().lower()
        if "enabled" in values and not isinstance(values["enabled"], bool):
            logger.warning(
                "config: [llm.instances.%s].enabled 必须是布尔值，已安全停用该实例",
                instance_id,
            )
            values["enabled"] = False
        if "num_ctx" in values:
            try:
                values["num_ctx"] = max(0, int(values["num_ctx"] or 0))
            except (TypeError, ValueError):
                logger.warning(
                    "config: [llm.instances.%s].num_ctx 非法，已回退为 0",
                    instance_id,
                )
                values["num_ctx"] = 0
        values.setdefault("name", instance_id)
        values.setdefault(
            "provider_type",
            instance_id if instance_id in _SUPPORTED_CHAT_PROVIDERS else "",
        )
        if (
            "reasoning_effort" not in values
            and str(values["provider_type"]).strip().lower() == "openai_compatible"
        ):
            # Before model discovery / editable effort landed, compatible
            # routes ignored this field entirely. Preserve that wire behavior
            # for existing v2 files unless the user explicitly opts in.
            values["reasoning_effort"] = ""
        instances[instance_id] = LLMInstanceConfig(**values)

    default_chain_raw = llm_raw.get("default_chain", [])
    default_chain = (
        [str(item).strip() for item in default_chain_raw if str(item).strip()]
        if isinstance(default_chain_raw, list)
        else []
    )
    routes_raw = llm_raw.get("routes", {})
    if not isinstance(routes_raw, dict):
        routes_raw = {}

    def _module_config(bucket: str) -> ModuleLLMConfig:
        if instance_routing:
            raw_route = routes_raw.get(bucket, {})
            if not isinstance(raw_route, dict):
                raw_route = {}
            chain_raw = raw_route.get("chain", [])
            chain = (
                [str(item).strip() for item in chain_raw if str(item).strip()]
                if isinstance(chain_raw, list)
                else []
            )
            return ModuleLLMConfig(
                inherit=bool(raw_route.get("inherit", True)),
                chain=chain,
            )
        raw_route = llm_raw.get(bucket, {})
        if not isinstance(raw_route, dict):
            raw_route = {}
        return ModuleLLMConfig(
            **{key: value for key, value in raw_route.items() if key in ("provider", "model")}
        )

    def _provider_config(provider_name: str) -> LLMProviderConfig:
        raw_provider = llm_raw.get(provider_name, {})
        if not isinstance(raw_provider, dict):
            raw_provider = {}
        values = dict(raw_provider)
        if provider_name == "openai_compatible" and (
            not instance_routing or "reasoning_effort" not in values
        ):
            # Backward compatibility: every legacy-schema compatible route
            # ignored this field, including config files that an older
            # ``save_config`` had materialized as ``"medium"``. Do not start
            # sending a new wire parameter merely because that package was
            # upgraded. Native v2 instances can opt in explicitly.
            values["reasoning_effort"] = ""
        # Unknown keys are dropped rather than splatted: a config.toml written
        # by a newer build must not brick an older one (see
        # ``_filter_dataclass_kwargs``).
        return LLMProviderConfig(
            **_filter_dataclass_kwargs(
                LLMProviderConfig,
                values,
                section=f"llm.{provider_name}",
            )
        )

    llm = LLMConfig(
        default_provider=llm_raw.get("default_provider", "deepseek"),
        concurrency=_normalize_llm_concurrency(llm_raw.get("concurrency")),
        timeout=_normalize_llm_timeout(llm_raw.get("timeout")),
        fallback_provider=llm_raw.get("fallback_provider", ""),
        instance_routing=instance_routing,
        instances=instances,
        default_chain=default_chain,
        openai=_provider_config("openai"),
        claude=_provider_config("claude"),
        gemini=_provider_config("gemini"),
        deepseek=_provider_config("deepseek"),
        ollama=_provider_config("ollama"),
        openrouter=_provider_config("openrouter"),
        openai_compatible=_provider_config("openai_compatible"),
        embedding=EmbeddingConfig(
            **_filter_dataclass_kwargs(
                EmbeddingConfig,
                embedding_raw,
                section="llm.embedding",
            )
        ),
        soul=_module_config("soul"),
        discovery=_module_config("discovery"),
        recommendation=_module_config("recommendation"),
        evaluation=_module_config("evaluation"),
    )
    if instance_routing:
        ordered_instance_ids = [
            *default_chain,
            *[instance_id for instance_id in instances if instance_id not in default_chain],
        ]
        projected_types: set[str] = set()
        for instance_id in ordered_instance_ids:
            instance = instances.get(instance_id)
            if instance is None:
                continue
            provider_type = str(instance.provider_type or "").strip().lower()
            if provider_type not in _SUPPORTED_CHAT_PROVIDERS or provider_type in projected_types:
                continue
            projected_types.add(provider_type)
            setattr(
                llm,
                provider_type,
                LLMProviderConfig(
                    **{
                        field_name: getattr(instance, field_name)
                        for field_name in _LLM_PROVIDER_CONFIG_FIELDS
                    }
                ),
            )
        first = instances.get(default_chain[0]) if default_chain else None
        second = instances.get(default_chain[1]) if len(default_chain) > 1 else None
        if first is not None:
            llm.default_provider = str(first.provider_type or "").strip().lower()
        if second is not None:
            llm.fallback_provider = str(second.provider_type or "").strip().lower()
        else:
            llm.fallback_provider = ""

    browser_raw = bili_raw.pop("browser", {})
    bilibili = BilibiliConfig(
        auth_method=bili_raw.get("auth_method", "cookie"),
        cookie=bili_raw.get("cookie", ""),
        proxy=bili_raw.get("proxy", ""),
        browser_executable=browser_raw.get("executable", ""),
        browser_headed=browser_raw.get("headed", False),
    )

    sources_browser_raw = sources_raw.get("browser", {})
    bilibili_source_raw = sources_raw.get("bilibili", {})
    xhs_raw = sources_raw.get("xiaohongshu", {})
    douyin_raw = sources_raw.get("douyin", {})
    youtube_raw = sources_raw.get("youtube", {})
    twitter_raw = sources_raw.get("twitter", {})
    zhihu_raw = sources_raw.get("zhihu", {})
    reddit_raw = sources_raw.get("reddit", {})
    bangumi_raw = sources_raw.get("bangumi", {})
    sources = SourcesConfig(
        browser_cdp_url=sources_browser_raw.get("cdp_url", ""),
        browser_headed=sources_browser_raw.get("headed", False),
        bilibili=BilibiliSourceConfig(
            enabled=bool(bilibili_source_raw.get("enabled", True)),
            min_interval_minutes=max(0, int(bilibili_source_raw.get("min_interval_minutes", 3))),
        ),
        xiaohongshu=XiaohongshuSourceConfig(
            enabled=bool(xhs_raw.get("enabled", False)),
            daily_search_budget=int(xhs_raw.get("daily_search_budget", 0)),
            daily_creator_budget=int(xhs_raw.get("daily_creator_budget", 0)),
            task_interval_seconds=int(xhs_raw.get("task_interval_seconds", 300)),
            min_interval_minutes=max(0, int(xhs_raw.get("min_interval_minutes", 3))),
        ),
        douyin=DouyinSourceConfig(
            enabled=bool(douyin_raw.get("enabled", False)),
            mode=str(douyin_raw.get("mode", "direct")),
            cookie_env=str(douyin_raw.get("cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")),
            daily_search_budget=int(douyin_raw.get("daily_search_budget", 0)),
            daily_hot_budget=int(douyin_raw.get("daily_hot_budget", 0)),
            daily_feed_budget=int(douyin_raw.get("daily_feed_budget", 0)),
            request_interval_seconds=int(douyin_raw.get("request_interval_seconds", 2)),
            min_interval_minutes=max(0, int(douyin_raw.get("min_interval_minutes", 3))),
        ),
        youtube=YoutubeSourceConfig(
            enabled=bool(youtube_raw.get("enabled", False)),
            daily_search_budget=int(youtube_raw.get("daily_search_budget", 0)),
            daily_trending_budget=int(youtube_raw.get("daily_trending_budget", 0)),
            daily_channel_budget=int(youtube_raw.get("daily_channel_budget", 0)),
            request_interval_seconds=int(youtube_raw.get("request_interval_seconds", 2)),
            min_interval_minutes=max(0, int(youtube_raw.get("min_interval_minutes", 3))),
        ),
        twitter=TwitterSourceConfig(
            enabled=bool(twitter_raw.get("enabled", False)),
            mode=str(twitter_raw.get("mode", "cookie")),
            cookie_env=str(twitter_raw.get("cookie_env", "OPENBILICLAW_X_COOKIE")),
            daily_search_budget=int(twitter_raw.get("daily_search_budget", 0)),
            daily_feed_budget=int(twitter_raw.get("daily_feed_budget", 0)),
            daily_creator_budget=int(twitter_raw.get("daily_creator_budget", 0)),
            request_interval_seconds=int(twitter_raw.get("request_interval_seconds", 3)),
            min_interval_minutes=max(0, int(twitter_raw.get("min_interval_minutes", 3))),
        ),
        zhihu=ZhihuSourceConfig(
            enabled=bool(zhihu_raw.get("enabled", False)),
            source_modes=tuple(
                mode
                for mode in _coerce_str_list(
                    zhihu_raw.get("source_modes", ["search", "hot", "feed", "creator", "related"])
                )
                if mode in {"search", "hot", "feed", "creator", "related"}
            )
            or ("search",),
            daily_search_budget=int(zhihu_raw.get("daily_search_budget", 0)),
            daily_hot_budget=int(zhihu_raw.get("daily_hot_budget", 0)),
            daily_feed_budget=int(zhihu_raw.get("daily_feed_budget", 0)),
            daily_creator_budget=int(zhihu_raw.get("daily_creator_budget", 0)),
            daily_related_budget=int(zhihu_raw.get("daily_related_budget", 0)),
            request_interval_seconds=int(zhihu_raw.get("request_interval_seconds", 3)),
            min_interval_minutes=max(0, int(zhihu_raw.get("min_interval_minutes", 3))),
        ),
        reddit=RedditSourceConfig(
            enabled=bool(reddit_raw.get("enabled", False)),
            backend=str(reddit_raw.get("backend", "rdt") or "rdt"),
            source_modes=tuple(
                mode
                for mode in _coerce_str_list(
                    reddit_raw.get("source_modes", ["search", "hot", "subreddit", "related"])
                )
                if mode in {"search", "hot", "subreddit", "related"}
            )
            or ("search",),
            daily_search_budget=int(reddit_raw.get("daily_search_budget", 300)),
            daily_hot_budget=int(reddit_raw.get("daily_hot_budget", 300)),
            daily_subreddit_budget=int(reddit_raw.get("daily_subreddit_budget", 300)),
            daily_related_budget=int(reddit_raw.get("daily_related_budget", 300)),
            request_interval_seconds=int(reddit_raw.get("request_interval_seconds", 3)),
            min_interval_minutes=max(0, int(reddit_raw.get("min_interval_minutes", 3))),
        ),
        bangumi=BangumiSourceConfig(
            enabled=bool(bangumi_raw.get("enabled", False)),
            username=str(bangumi_raw.get("username", "") or "").strip(),
            access_token=str(bangumi_raw.get("access_token", "") or "").strip(),
            subject_types=tuple(
                value
                for value in _coerce_str_list(
                    bangumi_raw.get("subject_types", ["anime", "book", "game"])
                )
                if value in {"book", "anime", "music", "game", "real"}
            )
            or ("anime", "book", "game"),
            source_modes=tuple(
                value
                for value in _coerce_str_list(
                    bangumi_raw.get("source_modes", ["search", "ranked", "latest"])
                )
                if value in {"search", "ranked", "latest"}
            )
            or ("search",),
            daily_search_budget=max(0, int(bangumi_raw.get("daily_search_budget", 300))),
            daily_ranked_budget=max(0, int(bangumi_raw.get("daily_ranked_budget", 100))),
            daily_latest_budget=max(0, int(bangumi_raw.get("daily_latest_budget", 100))),
            request_interval_seconds=max(0, int(bangumi_raw.get("request_interval_seconds", 1))),
            min_interval_minutes=max(0, int(bangumi_raw.get("min_interval_minutes", 3))),
            bootstrap_limit=min(1000, max(1, int(bangumi_raw.get("bootstrap_limit", 300)))),
        ),
    )
    _warn_suspicious_budgets(sources)

    soul_raw = raw.get("soul", {}) if isinstance(raw.get("soul"), dict) else {}
    soul_preference_raw = (
        soul_raw.get("preference", {}) if isinstance(soul_raw.get("preference"), dict) else {}
    )
    raw_gate_mode = str(soul_raw.get("posture_gate_mode", "shadow") or "shadow").strip().lower()
    raw_lifecycle = (
        str(soul_raw.get("topic_lifecycle_serialization", "off") or "off").strip().lower()
    )
    soul = SoulConfig(
        preference=SoulPreferenceConfig(
            satisfaction_filter_enabled=bool(
                soul_preference_raw.get("satisfaction_filter_enabled", True)
            ),
        ),
        posture_gate_mode=raw_gate_mode if raw_gate_mode in _POSTURE_GATE_MODES else "shadow",
        posture_gate_force_enforce=bool(soul_raw.get("posture_gate_force_enforce", False)),
        topic_lifecycle_serialization=(
            raw_lifecycle if raw_lifecycle in _TOPIC_LIFECYCLE_SERIALIZATION_MODES else "off"
        ),
    )

    api_auth = _build_api_auth(api_raw)

    return Config(
        language=general.get("language", "zh"),
        data_dir=general.get("data_dir", "data"),
        api=ApiConfig(
            host=str(api_raw.get("host", "0.0.0.0") or "0.0.0.0").strip() or "0.0.0.0",
            port=_normalize_api_port(api_raw.get("port", 8420)),
            auth=api_auth,
        ),
        llm=llm,
        bilibili=bilibili,
        network=_build_network_config(raw),
        sources=sources,
        scheduler=SchedulerConfig(
            **_filter_dataclass_kwargs(
                SchedulerConfig,
                {
                    **sched_raw,
                    # Environment overrides arrive as strings. Leaving these raw
                    # made ``OPENBILICLAW_SCHEDULER_ENABLED=`` look false in the
                    # CLI while still being the string ``""``; the typed config
                    # API then rejected that same value. Normalize every scheduler
                    # boolean at the boundary so all consumers see a real bool.
                    "enabled": _coerce_bool(sched_raw.get("enabled"), default=True),
                    "pause_on_extension_disconnect": _coerce_bool(
                        sched_raw.get("pause_on_extension_disconnect"),
                        default=False,
                    ),
                    "profile_consolidation_enabled": _coerce_bool(
                        sched_raw.get("profile_consolidation_enabled"),
                        default=True,
                    ),
                    "extension_disconnect_grace_seconds": _normalize_extension_disconnect_grace(
                        sched_raw.get("extension_disconnect_grace_seconds")
                    ),
                    "pool_source_shares": _normalize_pool_source_shares(
                        sched_raw.get("pool_source_shares")
                    ),
                    "refresh_check_interval_seconds": _normalize_scheduler_int(
                        sched_raw.get("refresh_check_interval_seconds"),
                        default=_DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
                        min_value=15,
                    ),
                    "signal_event_threshold": _normalize_scheduler_int(
                        sched_raw.get("signal_event_threshold"),
                        default=_DEFAULT_SIGNAL_EVENT_THRESHOLD,
                        min_value=1,
                    ),
                    "trending_refresh_minutes": _normalize_scheduler_int(
                        _legacy_refresh_minutes["trending_refresh"],
                        default=_DEFAULT_TRENDING_REFRESH_MINUTES,
                        min_value=1,
                    ),
                    "explore_refresh_minutes": _normalize_scheduler_int(
                        _legacy_refresh_minutes["explore_refresh"],
                        default=_DEFAULT_EXPLORE_REFRESH_MINUTES,
                        min_value=1,
                    ),
                    "discovery_limit": _normalize_scheduler_int(
                        sched_raw.get("discovery_limit"),
                        default=_DEFAULT_DISCOVERY_LIMIT,
                        min_value=1,
                        max_value=60,
                    ),
                    "delight_queue_limit": _normalize_scheduler_int(
                        sched_raw.get("delight_queue_limit"),
                        default=_DEFAULT_DELIGHT_QUEUE_LIMIT,
                        min_value=1,
                        max_value=100,
                    ),
                    "proactive_push_interval_seconds": _normalize_scheduler_int(
                        sched_raw.get("proactive_push_interval_seconds"),
                        default=_DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
                        min_value=30,
                    ),
                    "speculator_idle_interval_minutes": _normalize_scheduler_int(
                        sched_raw.get("speculator_idle_interval_minutes"),
                        default=_DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
                        min_value=5,
                    ),
                    "profile_consolidation_interval_hours": _normalize_scheduler_int(
                        sched_raw.get("profile_consolidation_interval_hours"),
                        default=12,
                        min_value=1,
                    ),
                    "profile_consolidation_like_target_upper": _normalize_scheduler_int(
                        sched_raw.get("profile_consolidation_like_target_upper"),
                        default=512,
                        min_value=1,
                    ),
                    "profile_consolidation_like_target_soft": _normalize_scheduler_int(
                        sched_raw.get("profile_consolidation_like_target_soft"),
                        default=450,
                        min_value=1,
                    ),
                    "profile_consolidation_archive_enabled": _coerce_bool(
                        sched_raw.get("profile_consolidation_archive_enabled"),
                        default=True,
                    ),
                    "auto_update_enabled": _coerce_bool(
                        sched_raw.get("auto_update_enabled"),
                        default=False,
                    ),
                    "auto_update_allow_prerelease": _coerce_bool(
                        sched_raw.get("auto_update_allow_prerelease"),
                        default=False,
                    ),
                    "avoidance_speculation_interval_minutes": _normalize_scheduler_int(
                        sched_raw.get("avoidance_speculation_interval_minutes"),
                        default=10,
                        min_value=1,
                    ),
                    "avoidance_speculation_ttl_days": _normalize_scheduler_int(
                        sched_raw.get("avoidance_speculation_ttl_days"),
                        default=3,
                        min_value=1,
                    ),
                    "avoidance_speculation_cooldown_days": _normalize_scheduler_int(
                        sched_raw.get("avoidance_speculation_cooldown_days"),
                        default=7,
                        min_value=1,
                    ),
                    "avoidance_speculation_confirmation_threshold": _normalize_scheduler_int(
                        sched_raw.get("avoidance_speculation_confirmation_threshold"),
                        default=3,
                        min_value=1,
                    ),
                    "avoidance_speculation_max_active": _normalize_scheduler_int(
                        sched_raw.get("avoidance_speculation_max_active"),
                        default=5,
                        min_value=1,
                    ),
                    "auto_update_check_interval_hours": _normalize_scheduler_int(
                        sched_raw.get("auto_update_check_interval_hours"),
                        default=_DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_HOURS,
                        min_value=_MIN_AUTO_UPDATE_CHECK_INTERVAL_HOURS,
                    ),
                    "auto_update_allowed_remotes": _normalize_auto_update_allowed_remotes(
                        sched_raw.get("auto_update_allowed_remotes")
                    ),
                    "unified_interest_line": _coerce_bool(
                        sched_raw.get("unified_interest_line"),
                        # Must match the dataclass default. The E2E on
                        # 2026-07-28 caught them drifting: the dataclass said
                        # True but every real config.toml (no key) loaded
                        # through here and got False, so the unified line
                        # never engaged on a live backend.
                        default=True,
                    ),
                },
                section="scheduler",
            )
        ),
        discovery=_build_discovery(discovery_raw),
        autostart=AutostartConfig(
            enabled=_coerce_bool(autostart_raw.get("enabled"), default=False),
            manage_ollama=_coerce_bool(autostart_raw.get("manage_ollama"), default=True),
        ),
        saved_sync=SavedSyncConfig(
            auto_sync_enabled=_coerce_bool(saved_sync_raw.get("auto_sync_enabled"), default=False),
        ),
        storage=StorageConfig(
            **_filter_dataclass_kwargs(
                StorageConfig,
                store_raw if isinstance(store_raw, dict) else {},
                section="storage",
            )
        ),
        logging=LoggingConfig(
            **_filter_dataclass_kwargs(
                LoggingConfig,
                logging_raw if isinstance(logging_raw, dict) else {},
                section="logging",
            )
        ),
        soul=soul,
        tls_proxy=_build_tls_proxy(raw),
    )


def _build_discovery(discovery_raw: dict[str, Any]) -> DiscoveryConfig:
    """Assemble ``DiscoveryConfig`` from the raw ``[discovery]`` table.

    Every numeric knob goes through ``_normalize_scheduler_int`` (the same
    bounded-positive-int coercion the scheduler fields use), so a bad / missing
    / out-of-range value falls back to its spec §6 default. ``_coerce_bool``
    handles the feature flag, which means env-string overrides
    (``OPENBILICLAW_DISCOVERY_*``) normalize identically to TOML values.
    """
    return DiscoveryConfig(
        unified_keyword_planner_enabled=_coerce_bool(
            discovery_raw.get("unified_keyword_planner_enabled"),
            default=_DEFAULT_UNIFIED_KEYWORD_PLANNER_ENABLED,
        ),
        kw_cache_high=_normalize_scheduler_int(
            discovery_raw.get("kw_cache_high"),
            default=_DEFAULT_KW_CACHE_HIGH,
            min_value=1,
        ),
        kw_cache_low=_normalize_scheduler_int(
            discovery_raw.get("kw_cache_low"),
            default=_DEFAULT_KW_CACHE_LOW,
            min_value=1,
        ),
        gen_batch=_normalize_scheduler_int(
            discovery_raw.get("gen_batch"),
            default=_DEFAULT_GEN_BATCH,
            min_value=1,
        ),
        fetch_batch=_normalize_scheduler_int(
            discovery_raw.get("fetch_batch"),
            default=_DEFAULT_FETCH_BATCH,
            min_value=1,
        ),
        history_window_size=_normalize_scheduler_int(
            discovery_raw.get("history_window_size"),
            default=_DEFAULT_HISTORY_WINDOW_SIZE,
            min_value=1,
        ),
        history_window_hours=_normalize_scheduler_int(
            discovery_raw.get("history_window_hours"),
            default=_DEFAULT_HISTORY_WINDOW_HOURS,
            min_value=1,
        ),
        claim_lease_minutes=_normalize_scheduler_int(
            discovery_raw.get("claim_lease_minutes"),
            default=_DEFAULT_CLAIM_LEASE_MINUTES,
            min_value=1,
        ),
        planner_poll_seconds=_normalize_scheduler_int(
            discovery_raw.get("planner_poll_seconds"),
            default=_DEFAULT_PLANNER_POLL_SECONDS,
            min_value=1,
        ),
        plan_ttl_hours=_normalize_scheduler_int(
            discovery_raw.get("plan_ttl_hours"),
            default=_DEFAULT_PLAN_TTL_HOURS,
            min_value=1,
        ),
        inspiration_search_enabled=_coerce_bool(
            discovery_raw.get("inspiration_search_enabled"),
            default=False,
        ),
        inspiration_search_backends=_normalize_inspiration_search_backends(
            discovery_raw.get("inspiration_search_backends")
        ),
        inspiration_replace_merged_keywords=_coerce_bool(
            discovery_raw.get("inspiration_replace_merged_keywords"),
            default=False,
        ),
        inspiration_breadth=_normalize_inspiration_breadth(
            discovery_raw.get("inspiration_breadth")
        ),
        admission_min_score=_normalize_probability(
            discovery_raw.get("admission_min_score"),
            default=_DEFAULT_ADMISSION_MIN_SCORE,
        ),
        candidate_eval_concurrency=_normalize_scheduler_int(
            discovery_raw.get("candidate_eval_concurrency"),
            default=_DEFAULT_CANDIDATE_EVAL_CONCURRENCY,
            min_value=1,
            max_value=3,
        ),
        multimodal_evaluation_enabled=_coerce_bool(
            discovery_raw.get("multimodal_evaluation_enabled"),
            default=False,
        ),
        multimodal_batch_size=_normalize_scheduler_int(
            discovery_raw.get("multimodal_batch_size"),
            default=_DEFAULT_MULTIMODAL_BATCH_SIZE,
            min_value=1,
            max_value=12,
        ),
        multimodal_image_max_px=_normalize_scheduler_int(
            discovery_raw.get("multimodal_image_max_px"),
            default=_DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
            min_value=128,
            max_value=768,
        ),
        multimodal_image_quality=_normalize_scheduler_int(
            discovery_raw.get("multimodal_image_quality"),
            default=_DEFAULT_MULTIMODAL_IMAGE_QUALITY,
            min_value=40,
            max_value=90,
        ),
        multimodal_image_timeout_seconds=_normalize_scheduler_int(
            discovery_raw.get("multimodal_image_timeout_seconds"),
            default=_DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
            min_value=1,
            max_value=20,
        ),
    )


def _normalize_probability(value: object, *, default: float) -> float:
    """Normalize a TOML probability in the open interval ``(0, 1]``."""
    if isinstance(value, bool):
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score <= 0.0 or score > 1.0:
        return default
    return score


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    """Coerce TOML/env values to bool. Env values arrive as strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
        return default
    if isinstance(value, int | float):
        return bool(value)
    return default


def _coerce_ttl_hours(value: object) -> int:
    """Coerce a session TTL (TOML int / float or env string) to a non-negative
    int, falling back to 0 on missing or malformed input.

    Shared by ``_build_api_auth`` (load) and ``_api_auth_lines`` (env-managed
    save preservation) so a preserved on-disk value round-trips to exactly what
    the loader would compute.
    """
    if isinstance(value, int | float):  # bool is an int subclass: int(True) == 1
        try:
            return max(0, int(value))  # int(nan) → ValueError, int(inf) → OverflowError
        except (ValueError, OverflowError):
            return 0
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def _coerce_extension_token_ttl_hours(value: object) -> int:
    """Normalize extension token TTL to the supported 1..168 hour range."""
    if isinstance(value, bool):
        return _DEFAULT_EXTENSION_TOKEN_TTL_HOURS
    try:
        ttl = int(value) if isinstance(value, int | str) else -1
    except ValueError:
        return _DEFAULT_EXTENSION_TOKEN_TTL_HOURS
    if 1 <= ttl <= 168:
        return ttl
    return _DEFAULT_EXTENSION_TOKEN_TTL_HOURS


def config_local_auth_keys() -> set[str]:
    """``[api.auth]`` keys pinned in ``config.local.toml`` (the override layer that
    ``load_config`` merges OVER ``config.toml``, local winning).

    A write to ``config.toml`` (admin endpoint / ``set-password``) can't change a
    field that ``config.local.toml`` shadows — the value silently reverts on the
    next restart. Callers use this to refuse such a write loudly instead of
    reporting a false success (review r9). Empty when there is no local file or no
    ``[api.auth]`` section.
    """
    local = _project_root() / "config.local.toml"
    if not local.exists():
        return set()
    try:
        with local.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    api = data.get("api")
    auth = api.get("auth") if isinstance(api, dict) else None
    return set(auth) if isinstance(auth, dict) else set()


def _hash_matches_plaintext(plaintext: object, password_hash: str) -> bool:
    """True iff ``password_hash`` is a scrypt hash of ``plaintext``.

    Used on save to decide whether an on-disk plaintext ``password`` key still
    represents the current credential (so it can be preserved verbatim, keeping
    the reconcile fingerprint basis stable) or was deliberately changed in memory
    (so the stale plaintext must be dropped for the new hash). Defensive: a
    malformed hash never raises, it just means "no match" → write the hash.
    """
    text = str(plaintext) if plaintext is not None else ""
    if not text.strip() or not password_hash.strip():
        return False
    from openbiliclaw.auth_core import verify_password

    try:
        return verify_password(text, password_hash)
    except Exception:
        return False


def _coerce_str_list(value: object) -> list[str]:
    """Coerce a TOML list (or comma string) of strings into a clean list."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_inspiration_search_backends(value: object) -> tuple[str, ...]:
    """Normalize inspiration search backend names for the mcporter provider chain."""

    raw_values = (
        list(_DEFAULT_INSPIRATION_SEARCH_BACKENDS) if value is None else _coerce_str_list(value)
    )
    aliases = {
        "exa": "exa",
        "local": "local_cache",
        "cache": "local_cache",
        "local_cache": "local_cache",
        "local-cache": "local_cache",
        "platform-source": "platform_sources",
        "platform_sources": "platform_sources",
        "platform": "platform_sources",
        "you": "you",
        "you.com": "you",
        "youcom": "you",
        "you-search": "you",
        "you_search": "you",
    }
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        backend = aliases.get(raw.strip().lower())
        if backend is None or backend in seen:
            continue
        normalized.append(backend)
        seen.add(backend)
    return tuple(normalized or _DEFAULT_INSPIRATION_SEARCH_BACKENDS)


# Single source of truth: every env var ``_build_api_auth`` honors for
# ``[api.auth]``. The gate's "env-managed" guard (api/auth.py) imports this so a
# config-file edit (CLI / local admin endpoint) is refused for EVERY field that
# an env override would silently win back on restart — not just the password.
# Adding an override below MUST add its name here; ``test_config`` enforces it.
API_AUTH_ENV_VARS: tuple[str, ...] = (
    "OPENBILICLAW_API_AUTH_PASSWORD",
    "OPENBILICLAW_API_AUTH_PASSWORD_HASH",
    "OPENBILICLAW_API_AUTH_ENABLED",
    "OPENBILICLAW_API_AUTH_SESSION_SECRET",
    "OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS",
    "OPENBILICLAW_API_AUTH_TRUST_LOOPBACK",
)


def _build_api_auth(api_raw: dict[str, Any]) -> ApiAuthConfig:
    """Assemble ``ApiAuthConfig`` from raw config + dedicated env vars.

    Multi-word fields cannot use the generic ``OPENBILICLAW_A_B_C`` override
    (it splits on ``_``), so the security-sensitive ones are read explicitly
    here. See ``docs/plans/2026-05-30-web-password-auth-design.md`` §5.2. The set
    of variables read here is mirrored by ``API_AUTH_ENV_VARS`` above.
    """
    from openbiliclaw.auth_core import hash_password

    raw = api_raw.get("auth", {})
    auth_raw: dict[str, Any] = raw if isinstance(raw, dict) else {}

    def _env(name: str) -> str | None:
        value = os.environ.get(name)
        return value if value and value.strip() else None

    # Explicit credential precedence (review r7#1):
    #   env PASSWORD > env PASSWORD_HASH > on-disk plaintext password > on-disk hash.
    # A higher-priority source completely shadows the lower ones, so an env hash
    # rotation is never overridden by a stale on-disk plaintext password.
    env_plain = _env("OPENBILICLAW_API_AUTH_PASSWORD")
    env_hash = _env("OPENBILICLAW_API_AUTH_PASSWORD_HASH")
    disk_plain = auth_raw.get("password")
    if env_plain:
        password_hash = hash_password(env_plain)
    elif env_hash:
        password_hash = env_hash
    elif disk_plain and str(disk_plain).strip():
        password_hash = hash_password(str(disk_plain))
    else:
        password_hash = str(auth_raw.get("password_hash", ""))

    ttl_raw = _env("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS")
    if ttl_raw is None:
        ttl_raw = auth_raw.get("session_ttl_hours", 0)
    session_ttl_hours = _coerce_ttl_hours(ttl_raw)

    return ApiAuthConfig(
        enabled=_coerce_bool(
            _env("OPENBILICLAW_API_AUTH_ENABLED") or auth_raw.get("enabled", False)
        ),
        password_hash=password_hash,
        session_secret=(
            _env("OPENBILICLAW_API_AUTH_SESSION_SECRET") or str(auth_raw.get("session_secret", ""))
        ),
        session_ttl_hours=session_ttl_hours,
        trust_loopback=_coerce_bool(
            _env("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK") or auth_raw.get("trust_loopback", True),
            default=True,
        ),
        trusted_proxies=_coerce_str_list(auth_raw.get("trusted_proxies", [])),
        allowed_bearer_origins=_coerce_str_list(auth_raw.get("allowed_bearer_origins", [])),
        extension_access_enabled=_coerce_bool(auth_raw.get("extension_access_enabled", False)),
        extension_access_keys=_coerce_str_list(auth_raw.get("extension_access_keys", [])),
        extension_token_ttl_hours=_coerce_extension_token_ttl_hours(
            auth_raw.get("extension_token_ttl_hours", _DEFAULT_EXTENSION_TOKEN_TTL_HOURS)
        ),
    )


def _build_tls_proxy(raw: dict[str, Any]) -> TlsProxyConfig:
    tls_raw_val = raw.get("tls_proxy")
    tls_raw: dict[str, Any] = tls_raw_val if isinstance(tls_raw_val, dict) else {}
    san_raw = tls_raw.get("san_names", [])
    if isinstance(san_raw, str):
        san_raw = [s.strip() for s in san_raw.split(",") if s.strip()]
    return TlsProxyConfig(
        enabled=_coerce_bool(
            os.environ.get("OPENBILICLAW_TLS_PROXY_ENABLED") or tls_raw.get("enabled", False),
        ),
        port=_normalize_api_port(tls_raw.get("port", 2119)),
        cert_dir=str(tls_raw.get("cert_dir", "") or ""),
        san_names=list(san_raw) if isinstance(san_raw, list) else [],
    )


def get_auth_plain_password() -> str | None:
    """Return the plaintext auth password (env first, then config file).

    Used by the startup fingerprint reconcile (§4.7): the fingerprint must be
    derived from *stable* credential material, not the freshly-salted scrypt
    hash, or an unchanged password would falsely revoke sessions on every
    restart. The plaintext is stable across restarts whether it comes from
    ``OPENBILICLAW_API_AUTH_PASSWORD`` (Docker/env) or a ``[api.auth].password``
    line in config.toml. Returns ``None`` when only a persisted hash is used
    (in which case the hash string itself is the stable fingerprint material).
    """
    env_value = os.environ.get("OPENBILICLAW_API_AUTH_PASSWORD")
    if env_value and env_value.strip():
        return env_value
    # When an env PASSWORD_HASH governs the credential (and no env PASSWORD), there
    # is no stable plaintext — the effective password is the env hash, which wins
    # over any on-disk plaintext (see _build_api_auth precedence). Return None so
    # the reconcile fingerprint is derived from "ph:"+hash, not a stale on-disk
    # plaintext that no longer governs (review r7#1).
    env_hash = os.environ.get("OPENBILICLAW_API_AUTH_PASSWORD_HASH")
    if env_hash and env_hash.strip():
        return None
    # Fall back to a plaintext password persisted in config.toml so that path is
    # also fingerprint-stable (review r1#3).
    try:
        raw: dict[str, Any] = {}
        for filename in _CONFIG_FILENAMES:
            path = _project_root() / filename
            if path.exists():
                with open(path, "rb") as f:
                    raw = _deep_merge(raw, tomllib.load(f))
        api = raw.get("api", {})
        auth = api.get("auth", {}) if isinstance(api, dict) else {}
        value = auth.get("password") if isinstance(auth, dict) else None
        return str(value) if value and str(value).strip() else None
    except Exception:
        return None


def _normalize_api_port(value: object) -> int:
    """Normalize API port values into the valid TCP port range."""
    if isinstance(value, bool):
        return 8420
    if isinstance(value, int | float):
        port = int(value)
    elif isinstance(value, str):
        try:
            port = int(value.strip())
        except ValueError:
            return 8420
    else:
        return 8420
    return port if 1 <= port <= 65535 else 8420


def _normalize_llm_concurrency(value: object) -> int:
    """Normalize the shared LLM request concurrency limit."""
    if isinstance(value, bool):
        return DEFAULT_LLM_CONCURRENCY
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return DEFAULT_LLM_CONCURRENCY
    else:
        return DEFAULT_LLM_CONCURRENCY

    if not (_MIN_LLM_CONCURRENCY <= normalized <= _MAX_LLM_CONCURRENCY):
        return DEFAULT_LLM_CONCURRENCY
    return normalized


def _normalize_llm_timeout(value: object) -> int:
    """Normalize the LLM request timeout (seconds)."""
    if isinstance(value, bool):
        return _DEFAULT_LLM_TIMEOUT
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return _DEFAULT_LLM_TIMEOUT
    else:
        return _DEFAULT_LLM_TIMEOUT

    if not (_MIN_LLM_TIMEOUT <= normalized <= _MAX_LLM_TIMEOUT):
        return _DEFAULT_LLM_TIMEOUT
    return normalized


def llm_concurrency_from_config(config: object) -> int:
    """Extract LLM concurrency from a config object, with safe fallback.

    Works with both a full ``Config`` instance and a bare
    ``types.SimpleNamespace`` (used by test stubs and hot-reload paths).
    """
    llm_section = getattr(config, "llm", None)
    raw = getattr(llm_section, "concurrency", DEFAULT_LLM_CONCURRENCY)
    return _normalize_llm_concurrency(raw)


def _normalize_pool_source_shares(value: object) -> dict[str, int]:
    """Normalize scheduler pool source shares from TOML into positive ints."""
    if not isinstance(value, dict):
        return dict(_DEFAULT_POOL_SOURCE_SHARES)

    shares: dict[str, int] = dict(_DEFAULT_POOL_SOURCE_SHARES)
    for key, raw_share in value.items():
        source = str(key).strip().lower()
        if not source:
            continue
        try:
            share = int(raw_share)
        except (TypeError, ValueError):
            continue
        if share <= 0:
            continue
        shares[source] = share
    return shares or dict(_DEFAULT_POOL_SOURCE_SHARES)


def _normalize_extension_disconnect_grace(value: object) -> int:
    """Normalize extension disconnect grace seconds into a positive int."""
    if isinstance(value, int | float):
        grace = int(value)
    elif isinstance(value, str):
        try:
            grace = int(value.strip())
        except ValueError:
            return _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    else:
        return _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS

    if grace <= 0:
        return _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    return grace


def _legacy_hours_to_minutes(raw: dict[str, Any], prefix: str) -> Any:
    """Read ``<prefix>_minutes``, falling back to the pre-2026-07-26 hour key.

    The unit changed from hours to minutes when the Bilibili main-discovery
    cadence was aligned with every source producer's ``min_interval_minutes``.
    An existing ``config.toml`` still spelling ``trending_refresh_hours = 3``
    must keep meaning three *hours*, not three minutes — reinterpreting it in
    place would silently multiply that user's Bilibili traffic by sixty.
    """
    minutes = raw.get(f"{prefix}_minutes")
    if minutes is not None:
        return minutes
    hours = raw.get(f"{prefix}_hours")
    if hours is None:
        return None
    try:
        return max(1, int(hours) * 60)
    except (TypeError, ValueError):
        return None


def _normalize_scheduler_int(
    value: object,
    *,
    default: int,
    min_value: int,
    max_value: int | None = None,
) -> int:
    """Normalize scheduler tuning values into bounded positive ints."""
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return default
    else:
        return default

    if normalized < min_value:
        return default
    if max_value is not None and normalized > max_value:
        return default
    return normalized


def _normalize_inspiration_breadth(value: object) -> str:
    """Validate the breadth tier; unset → default, invalid → ConfigError."""
    if value is None:
        return _DEFAULT_INSPIRATION_BREADTH
    tier = str(value).strip().lower()
    derive_inspiration_breadth_params(tier)  # raises ConfigError when invalid
    return tier


def _normalize_auto_update_allowed_remotes(value: object) -> list[str]:
    """Normalize auto-update remote allowlist into non-empty string URLs."""
    if not isinstance(value, list):
        return list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)
    remotes = [str(item).strip() for item in value if str(item).strip()]
    return remotes or list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)


def _validate_auto_update_check_interval(value: object) -> int:
    """Reject unsafe save-time updater intervals instead of silently coercing them."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("auto_update_check_interval_hours 必须是整数")
    if value < _MIN_AUTO_UPDATE_CHECK_INTERVAL_HOURS:
        raise ValueError("auto_update_check_interval_hours 必须至少为 1 小时")
    return value


def _collect_llm_instance_routing_issues(llm: LLMConfig) -> list[ConfigIssue]:
    """Validate v2 instance identities, endpoint configs, and route references."""
    issues: list[ConfigIssue] = []
    instances = llm.instances

    if not instances:
        issues.append(
            ConfigIssue(
                field="llm.instances",
                message="至少需要新建一个 LLM 实例。",
                severity="blocking",
            )
        )

    for instance_id, instance in instances.items():
        field_prefix = f"llm.instances.{instance_id}"
        if _LLM_INSTANCE_ID_RE.fullmatch(instance_id) is None:
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.id",
                    message=(
                        "实例 ID 只能使用小写字母、数字、`_`、`-`，"
                        "必须以字母或数字开头，最长 64 个字符。"
                    ),
                    severity="blocking",
                )
            )
        provider_type = str(instance.provider_type or "").strip().lower()
        if provider_type not in _SUPPORTED_CHAT_PROVIDERS:
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.provider_type",
                    message=(
                        f"不支持的 provider 类型: `{instance.provider_type}`。仅支持: "
                        f"{', '.join(sorted(_SUPPORTED_CHAT_PROVIDERS))}。"
                    ),
                    severity="blocking",
                )
            )
            continue
        if not str(instance.name or "").strip():
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.name",
                    message="实例名称不能为空。",
                    severity="blocking",
                )
            )
        if instance.num_ctx < 0:
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.num_ctx",
                    message="`num_ctx` 不能小于 0。",
                    severity="blocking",
                )
            )

        auth_mode = str(instance.auth_mode or "").strip().lower()
        if provider_type == "openai" and auth_mode not in _SUPPORTED_OPENAI_AUTH_MODES:
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.auth_mode",
                    message='OpenAI `auth_mode` 仅支持: "", "api_key", "codex_oauth"。',
                    severity="blocking",
                )
            )
        flavor = str(instance.api_flavor or "").strip().lower()
        if (
            provider_type in {"openai", "openai_compatible"}
            and flavor not in _SUPPORTED_OPENAI_API_FLAVORS
        ):
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.api_flavor",
                    message='`api_flavor` 仅支持: "", "chat_completions", "responses"。',
                    severity="blocking",
                )
            )
        if provider_type == "openai" and auth_mode == "codex_oauth":
            if not _is_openai_official_base_url(instance.base_url):
                issues.append(
                    ConfigIssue(
                        field=f"{field_prefix}.base_url",
                        message=(
                            "Codex OAuth 只允许留空 base_url 或使用 OpenAI 官方 API 域名，"
                            "避免把 ChatGPT token 发送给第三方。"
                        ),
                        severity="blocking",
                    )
                )
            try:
                from openbiliclaw.llm.codex_auth import codex_credentials_exist

                has_codex_credentials = codex_credentials_exist()
            except Exception:
                has_codex_credentials = False
            if not has_codex_credentials:
                issues.append(
                    ConfigIssue(
                        field=f"{field_prefix}.auth_mode",
                        message="未找到 Codex OAuth 凭据，请先运行 `openbiliclaw login codex`。",
                    )
                )

        if not instance.enabled:
            continue
        if not str(instance.model or "").strip():
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.model",
                    message="启用的 LLM 实例必须明确填写模型。",
                    severity="blocking",
                )
            )
        uses_codex_oauth = provider_type == "openai" and auth_mode == "codex_oauth"
        has_env_key = provider_type == "gemini" and bool(_gemini_api_key_from_env())
        if (
            provider_type in _REMOTE_PROVIDER_FIELDS
            and not str(instance.api_key or "").strip()
            and not uses_codex_oauth
            and not has_env_key
        ):
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.api_key",
                    message=f"启用的 `{provider_type}` 实例缺少 API Key。",
                    severity="blocking",
                )
            )
        if provider_type == "openai_compatible" and not str(instance.base_url or "").strip():
            issues.append(
                ConfigIssue(
                    field=f"{field_prefix}.base_url",
                    message="OpenAI-compatible 实例必须填写 Base URL。",
                    severity="blocking",
                )
            )

    def _validate_chain(field_name: str, chain: list[str], *, allow_empty: bool) -> None:
        normalized = [str(item).strip().lower() for item in chain if str(item).strip()]
        if not normalized and not allow_empty:
            issues.append(
                ConfigIssue(
                    field=field_name,
                    message="调用链至少需要一个实例。",
                    severity="blocking",
                )
            )
            return
        if len(normalized) != len(set(normalized)):
            issues.append(
                ConfigIssue(
                    field=field_name,
                    message="同一调用链不能重复引用同一个实例。",
                    severity="blocking",
                )
            )
        for instance_id in normalized:
            instance = instances.get(instance_id)
            if instance is None:
                issues.append(
                    ConfigIssue(
                        field=field_name,
                        message=f"调用链引用了不存在的实例 `{instance_id}`。",
                        severity="blocking",
                    )
                )
            elif not instance.enabled:
                issues.append(
                    ConfigIssue(
                        field=field_name,
                        message=f"调用链引用了已停用的实例 `{instance_id}`。",
                        severity="blocking",
                    )
                )

    _validate_chain("llm.default_chain", llm.default_chain, allow_empty=False)
    for bucket in _LLM_MODULE_BUCKETS:
        route = getattr(llm, bucket)
        field_name = f"llm.routes.{bucket}.chain"
        if route.inherit:
            if route.chain:
                issues.append(
                    ConfigIssue(
                        field=field_name,
                        message="模块当前继承全局调用链；保存时会忽略它自己的 chain。",
                    )
                )
            continue
        _validate_chain(field_name, route.chain, allow_empty=False)
    return issues


def _collect_config_issues(config: Config) -> list[ConfigIssue]:
    """Collect non-fatal config issues to display as guidance."""
    issues: list[ConfigIssue] = []

    try:
        from openbiliclaw.sources.bangumi_client import validate_bangumi_username

        validate_bangumi_username(config.sources.bangumi.username)
    except ValueError as exc:
        issues.append(
            ConfigIssue(
                field="sources.bangumi.username",
                message=str(exc),
                severity="blocking",
            )
        )

    try:
        from openbiliclaw.sources.bangumi_client import validate_bangumi_access_token

        validate_bangumi_access_token(config.sources.bangumi.access_token)
    except ValueError as exc:
        issues.append(
            ConfigIssue(
                field="sources.bangumi.access_token",
                message=str(exc),
                severity="blocking",
            )
        )

    if config.api.auth.enabled and not config.api.auth.password_hash.strip():
        issues.append(
            ConfigIssue(
                field="api.auth.password_hash",
                message=(
                    "已开启 `api.auth.enabled` 但未设置密码。"
                    "请用 `openbiliclaw set-password` 设置，或关闭门禁。"
                ),
                severity="blocking",
            )
        )

    if config.bilibili.auth_method not in _SUPPORTED_AUTH_METHODS:
        supported = ", ".join(sorted(_SUPPORTED_AUTH_METHODS))
        issues.append(
            ConfigIssue(
                field="bilibili.auth_method",
                message=f"`bilibili.auth_method` 仅支持: {supported}。",
            )
        )

    if str(config.soul.posture_gate_mode or "").strip().lower() not in _POSTURE_GATE_MODES:
        issues.append(
            ConfigIssue(
                field="soul.posture_gate_mode",
                message=(
                    f"不支持的 posture_gate_mode: `{config.soul.posture_gate_mode}`。"
                    "仅支持: shadow, enforce, off。"
                ),
                severity="blocking",
            )
        )

    if (
        str(config.soul.topic_lifecycle_serialization or "").strip().lower()
        not in _TOPIC_LIFECYCLE_SERIALIZATION_MODES
    ):
        issues.append(
            ConfigIssue(
                field="soul.topic_lifecycle_serialization",
                message=(
                    "不支持的 topic_lifecycle_serialization: "
                    f"`{config.soul.topic_lifecycle_serialization}`。仅支持: off, on。"
                ),
                severity="blocking",
            )
        )

    # Before the default-provider early return: embedding validation must run
    # even when default_provider itself is broken.
    for emb_field, emb_value in (
        ("provider", config.llm.embedding.provider),
        ("fallback_provider", config.llm.embedding.fallback_provider),
    ):
        normalized = str(emb_value or "").strip().lower()
        if normalized not in _SUPPORTED_EMBEDDING_PROVIDERS:
            supported = '"", "ollama", "openai", "gemini", "openai_compatible", "dashscope"'
            issues.append(
                ConfigIssue(
                    field=f"llm.embedding.{emb_field}",
                    message=(
                        f"不支持的 embedding {emb_field}: `{emb_value}`。仅支持: {supported}。"
                        "如果这个值看起来像被翻译过（例如「奥拉玛」），"
                        "请关闭浏览器的网页翻译后到设置页重新选择。"
                    ),
                    severity="blocking",
                )
            )

    if config.llm.instance_routing:
        issues.extend(_collect_llm_instance_routing_issues(config.llm))
        if not (
            _MIN_POOL_TARGET_COUNT <= config.scheduler.pool_target_count <= _MAX_POOL_TARGET_COUNT
        ):
            issues.append(
                ConfigIssue(
                    field="scheduler.pool_target_count",
                    message=(
                        "`scheduler.pool_target_count` 必须在 "
                        f"{_MIN_POOL_TARGET_COUNT}..{_MAX_POOL_TARGET_COUNT} 之间。"
                    ),
                )
            )
        return issues

    # `[llm].fallback_provider` dead-state validation. The chat fallback
    # chain (llm/base.py `_fallback_order`) deliberately drops an unusable
    # fallback WITHOUT any runtime signal — surfacing the dead state here
    # at save/load time is the only user-visible diagnosis. Runs before the
    # default-provider early return so a broken default provider does not
    # hide fallback problems.
    fallback_name = str(config.llm.fallback_provider or "").strip().lower()
    if fallback_name:
        default_name = str(config.llm.default_provider or "").strip().lower()
        if fallback_name not in _SUPPORTED_CHAT_PROVIDERS:
            supported = ", ".join(sorted(_SUPPORTED_CHAT_PROVIDERS))
            issues.append(
                ConfigIssue(
                    field="llm.fallback_provider",
                    message=(
                        f"不支持的备选 provider: `{config.llm.fallback_provider}`。"
                        f"仅支持: {supported}。"
                        "如果这个值看起来像被翻译过（例如「奥拉玛」），"
                        "请关闭浏览器的网页翻译后到设置页重新选择。"
                    ),
                    severity="blocking",
                )
            )
        elif fallback_name == default_name:
            issues.append(
                ConfigIssue(
                    field="llm.fallback_provider",
                    message=(
                        "备选与主 Provider 相同时永远不会生效；"
                        "请换一个不同类型的 Provider 或留空关闭 fallback。"
                    ),
                    severity="blocking",
                )
            )
        else:
            # Mirrors the default-provider credential logic below: gemini
            # may take its key from GOOGLE_API_KEY / GEMINI_API_KEY, and
            # openai may authenticate via Codex OAuth instead of api_key.
            fallback_cfg = getattr(config.llm, fallback_name, None)
            fallback_required_field = _REMOTE_PROVIDER_FIELDS.get(fallback_name)
            fallback_has_env_key = fallback_name == "gemini" and bool(_gemini_api_key_from_env())
            fallback_uses_codex_oauth = (
                fallback_name == "openai"
                and config.llm.openai.auth_mode.strip().lower() == "codex_oauth"
            )
            if (
                fallback_required_field
                and fallback_cfg is not None
                and not fallback_cfg.api_key.strip()
                and not fallback_has_env_key
                and not fallback_uses_codex_oauth
            ):
                issues.append(
                    ConfigIssue(
                        field="llm.fallback_provider",
                        message=(
                            f"备选 provider `{fallback_name}` 缺少 `api_key`，不会被注册，"
                            f"fallback 永远不会生效；请填写 `{fallback_required_field}` "
                            "或留空关闭 fallback。"
                        ),
                        severity="blocking",
                    )
                )
            if (
                fallback_name == "openai_compatible"
                and not config.llm.openai_compatible.base_url.strip()
            ):
                issues.append(
                    ConfigIssue(
                        field="llm.fallback_provider",
                        message=(
                            "备选 provider `openai_compatible` 必须填 `base_url` "
                            "(例如 Groq: https://api.groq.com/openai/v1)，"
                            "否则不会被注册，fallback 永远不会生效。"
                        ),
                        severity="blocking",
                    )
                )
            # Keep in sync with llm/registry.py `_maybe_ollama_provider` /
            # `_ollama_is_chat_capable` (config cannot import the registry —
            # cycle). A base URL cannot identify an installed chat model, so
            # fallback Ollama always requires an explicit model.
            if fallback_name == "ollama" and not config.llm.ollama.model.strip():
                issues.append(
                    ConfigIssue(
                        field="llm.fallback_provider",
                        message=(
                            "备选 provider `ollama` 需要在 `[llm.ollama]` 填 `model` "
                            "（例如 `qwen2.5:7b`）；仅填写 `base_url` 无法确定聊天模型，"
                            "fallback 不会生效。"
                        ),
                        severity="blocking",
                    )
                )

    provider_name = config.llm.default_provider
    provider_configs: dict[str, LLMProviderConfig] = {
        "openai": config.llm.openai,
        "claude": config.llm.claude,
        "gemini": config.llm.gemini,
        "deepseek": config.llm.deepseek,
        "ollama": config.llm.ollama,
        "openrouter": config.llm.openrouter,
        "openai_compatible": config.llm.openai_compatible,
    }

    provider_config = provider_configs.get(provider_name)
    if provider_config is None:
        issues.append(
            ConfigIssue(
                field="llm.default_provider",
                message=f"不支持的默认 provider: `{provider_name}`。",
            )
        )
        return issues

    for flavor_provider in ("openai", "openai_compatible"):
        flavor = provider_configs[flavor_provider].api_flavor.strip().lower()
        if flavor not in _SUPPORTED_OPENAI_API_FLAVORS:
            issues.append(
                ConfigIssue(
                    field=f"llm.{flavor_provider}.api_flavor",
                    message=(
                        f"`llm.{flavor_provider}.api_flavor` 仅支持: "
                        '"", "chat_completions", "responses"。'
                    ),
                    severity="blocking",
                )
            )

    openai_auth_mode = config.llm.openai.auth_mode.strip().lower()
    if openai_auth_mode not in _SUPPORTED_OPENAI_AUTH_MODES:
        issues.append(
            ConfigIssue(
                field="llm.openai.auth_mode",
                message='`llm.openai.auth_mode` 仅支持: "", "api_key", "codex_oauth"。',
                severity="blocking",
            )
        )

    if openai_auth_mode == "codex_oauth":
        if config.llm.openai.api_key.strip():
            issues.append(
                ConfigIssue(
                    field="llm.openai.api_key",
                    message='`auth_mode = "codex_oauth"` 时 `api_key` 会被忽略。',
                )
            )
        if not _is_openai_official_base_url(config.llm.openai.base_url):
            issues.append(
                ConfigIssue(
                    field="llm.openai.base_url",
                    message=(
                        '`auth_mode = "codex_oauth"` 只允许留空 base_url '
                        "或使用 OpenAI 官方 API 域名，避免泄露 ChatGPT token。"
                    ),
                    severity="blocking",
                )
            )
        try:
            from openbiliclaw.llm.codex_auth import codex_credentials_exist

            has_codex_credentials = codex_credentials_exist()
        except Exception:
            has_codex_credentials = False
        if not has_codex_credentials:
            issues.append(
                ConfigIssue(
                    field="llm.openai.codex_oauth",
                    message="未找到 Codex OAuth 凭据，请先运行 `openbiliclaw login codex`。",
                )
            )

    required_field = _REMOTE_PROVIDER_FIELDS.get(provider_name)
    has_env_fallback = provider_name == "gemini" and bool(_gemini_api_key_from_env())
    provider_uses_codex_oauth = provider_name == "openai" and openai_auth_mode == "codex_oauth"
    if (
        required_field
        and not provider_config.api_key.strip()
        and not has_env_fallback
        and not provider_uses_codex_oauth
    ):
        issues.append(
            ConfigIssue(
                field=required_field,
                message=(
                    f"默认 provider `{provider_name}` 缺少 `api_key`，请在 config.toml 中填写。"
                ),
            )
        )

    # openai_compatible without an explicit base_url is meaningless — it
    # would just be ``openai`` with extra steps. Surface this so the user
    # knows to fill ``[llm.openai_compatible].base_url`` (Groq:
    # https://api.groq.com/openai/v1, vLLM: http://your-vllm:8000/v1, ...).
    if provider_name == "openai_compatible" and not config.llm.openai_compatible.base_url.strip():
        issues.append(
            ConfigIssue(
                field="llm.openai_compatible.base_url",
                message=(
                    "默认 provider `openai_compatible` 必须填 `base_url` "
                    "(例如 Groq: https://api.groq.com/openai/v1)。"
                ),
            )
        )

    if provider_name == "ollama" and not config.llm.ollama.model.strip():
        issues.append(
            ConfigIssue(
                field="llm.ollama.model",
                message=(
                    "默认 provider `ollama` 必须明确填写聊天 `model` "
                    "（例如 `qwen2.5:7b`）；系统不会再隐式使用 `llama3`。"
                ),
                severity="blocking",
            )
        )

    if not (_MIN_POOL_TARGET_COUNT <= config.scheduler.pool_target_count <= _MAX_POOL_TARGET_COUNT):
        issues.append(
            ConfigIssue(
                field="scheduler.pool_target_count",
                message=(
                    "`scheduler.pool_target_count` 必须在 "
                    f"{_MIN_POOL_TARGET_COUNT}..{_MAX_POOL_TARGET_COUNT} 之间。"
                ),
            )
        )

    return issues


def posture_gate_enforce_readiness_issue(
    config: Config,
    *,
    earliest_valid_at: str,
    valid_count_14d: int,
    valid_count_7d: int,
) -> ConfigIssue | None:
    """Blocking save-time guard for switching ``posture_gate_mode`` to enforce.

    Requires DB-side shadow statistics (computed by
    ``Database.posture_gate_shadow_stats``) — hence it lives outside
    :func:`_collect_config_issues` (which is DB-free) and is invoked by the
    config PUT handler where a database handle is available.

    Three conditions must ALL hold (spec §Phase 3, r4/R3-3): the earliest valid
    shadow judgement is ≥14 days old, ≥10 valid judgements landed in the last 14
    days, and ≥1 in the last 7. ``posture_gate_force_enforce`` bypasses the
    guard (documented risk). Returns a blocking :class:`ConfigIssue` on failure,
    else ``None``.
    """
    if str(config.soul.posture_gate_mode or "").strip().lower() != "enforce":
        return None
    if config.soul.posture_gate_force_enforce:
        return None
    now = datetime.now()
    earliest = None
    if earliest_valid_at.strip():
        try:
            earliest = datetime.fromisoformat(earliest_valid_at)
        except ValueError:
            earliest = None
    observation_days = (now - earliest).days if earliest is not None else -1
    reasons: list[str] = []
    if observation_days < POSTURE_GATE_ENFORCE_MIN_OBSERVATION_DAYS:
        reasons.append(
            f"shadow 观察不足 {POSTURE_GATE_ENFORCE_MIN_OBSERVATION_DAYS} 天"
            f"（当前 {max(0, observation_days)} 天）"
        )
    if valid_count_14d < POSTURE_GATE_ENFORCE_MIN_VALID_JUDGEMENTS:
        reasons.append(
            f"近 14 天有效判定不足 {POSTURE_GATE_ENFORCE_MIN_VALID_JUDGEMENTS} 条"
            f"（当前 {valid_count_14d} 条）"
        )
    if valid_count_7d < POSTURE_GATE_ENFORCE_MIN_RECENT_COUNT:
        reasons.append("近 7 天没有有效判定")
    if not reasons:
        return None
    return ConfigIssue(
        field="soul.posture_gate_mode",
        message=(
            "态势门控尚未积累足够的 shadow 观察数据，无法切换到 enforce："
            + "；".join(reasons)
            + "。请继续以 shadow 运行，或（有风险地）开启 posture_gate_force_enforce。"
        ),
        severity="blocking",
    )


def _is_openai_official_base_url(base_url: str) -> bool:
    raw = base_url.strip()
    if not raw:
        return True
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "api.openai.com"


def load_config_with_diagnostics(
    config_path: str | Path | None = None,
    *,
    ensure_default_file: bool = True,
) -> tuple[Config, ConfigDiagnostics]:
    """Load configuration from TOML file(s).

    Resolution order:
    1. Explicit path (if provided)
    2. config.toml in project root
    3. config.local.toml overrides (if exists)
    4. Environment variable overrides

    Args:
        config_path: Optional explicit path to config file.

    Returns:
        Populated Config instance with diagnostics.
    """
    diagnostics = ConfigDiagnostics()
    raw: dict[str, Any] = {}

    if config_path:
        path = Path(config_path)
        diagnostics.config_path = path
        if path.exists():
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        else:
            diagnostics.messages.append(f"未找到配置文件：{path}，当前使用默认配置。")
    else:
        if ensure_default_file:
            _ensure_default_config_file(diagnostics)
        else:
            diagnostics.config_path = _default_config_path()
        for filename in _CONFIG_FILENAMES:
            path = _project_root() / filename
            if path.exists():
                with open(path, "rb") as f:
                    file_data = tomllib.load(f)
                raw = _deep_merge(raw, file_data)

    raw = _apply_env_overrides(raw)
    # Removed-key notices are collected from the RAW [discovery] table before
    # _build_discovery ever runs — the values are ignored, never fail-fast.
    diagnostics.issues.extend(_removed_discovery_key_issues(raw))
    config = _build_config(raw)
    diagnostics.issues.extend(_collect_config_issues(config))
    return config, diagnostics


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration only, without diagnostics."""
    config, _ = load_config_with_diagnostics(config_path, ensure_default_file=False)
    return config


def _auth_env_field_overrides() -> dict[str, bool]:
    """Which renderable ``[api.auth]`` fields are currently env-overridden.

    Maps each persisted field to whether an ``OPENBILICLAW_API_AUTH_*`` var
    currently governs it (``PASSWORD`` and ``PASSWORD_HASH`` both feed
    ``password_hash``). ``trusted_proxies`` / ``allowed_bearer_origins`` have no
    env override (TOML-only) and so never appear here.
    """

    def _set(name: str) -> bool:
        return bool((os.environ.get(name) or "").strip())

    return {
        "enabled": _set("OPENBILICLAW_API_AUTH_ENABLED"),
        "password_hash": _set("OPENBILICLAW_API_AUTH_PASSWORD")
        or _set("OPENBILICLAW_API_AUTH_PASSWORD_HASH"),
        "session_secret": _set("OPENBILICLAW_API_AUTH_SESSION_SECRET"),
        "session_ttl_hours": _set("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS"),
        "trust_loopback": _set("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK"),
    }


# Maps each ``config.local.toml`` ``[api.auth]`` key to the ``config.toml`` render
# field it shadows (``password`` / ``password_hash`` both feed the credential).
_LOCAL_AUTH_KEY_TO_FIELD = {
    "password": "password_hash",
    "password_hash": "password_hash",
    "enabled": "enabled",
    "session_secret": "session_secret",
    "session_ttl_hours": "session_ttl_hours",
    "trust_loopback": "trust_loopback",
    "trusted_proxies": "trusted_proxies",
    "allowed_bearer_origins": "allowed_bearer_origins",
    "extension_access_enabled": "extension_access_enabled",
    "extension_access_keys": "extension_access_keys",
    "extension_token_ttl_hours": "extension_token_ttl_hours",
}


def _auth_overridden_fields(*, consult_local: bool) -> set[str]:
    """Render fields of ``[api.auth]`` governed by an override LAYER above
    ``config.toml`` — environment variables OR ``config.local.toml`` (both win over
    ``config.toml`` in ``load_config``).

    ``save_config`` must NOT bake the merged in-memory value of these fields into
    ``config.toml``: that would persist the layer's value as a stale literal that
    silently shifts the effective auth once the layer is removed (reviews r4#1 /
    r9 / r10). Such a field is instead written from ``config.toml``'s own on-disk
    value, or omitted (the layer keeps governing at runtime).

    Env vars apply to EVERY load, so env-governed fields always count. But
    ``config.local.toml`` is merged ONLY when ``load_config`` runs with no explicit
    path (the production / default-path case); ``load_config(explicit_path)`` reads
    that file alone. So ``consult_local`` must be False for an explicit-path save to
    an unrelated file, or we would preserve/omit fields based on a project-root
    local layer that was never merged into the config being saved (review r11).
    """
    fields = {field for field, on in _auth_env_field_overrides().items() if on}
    if consult_local:
        for key in config_local_auth_keys():
            mapped = _LOCAL_AUTH_KEY_TO_FIELD.get(key)
            if mapped is not None:
                fields.add(mapped)
    return fields


def _read_on_disk_auth(path: Path) -> dict[str, Any]:
    """Return the raw ``[api.auth]`` table currently persisted at ``path`` ({} if none)."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    api = data.get("api")
    auth = api.get("auth") if isinstance(api, dict) else None
    return auth if isinstance(auth, dict) else {}


def _read_on_disk_autostart(path: Path) -> dict[str, Any]:
    """Return the raw ``[autostart]`` table currently persisted at ``path`` ({} if none)."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    autostart = data.get("autostart")
    return autostart if isinstance(autostart, dict) else {}


def _uses_native_llm_routing(raw: object) -> bool:
    """Return whether parsed TOML contains the v2 LLM routing schema."""
    if not isinstance(raw, dict):
        return False
    llm = raw.get("llm")
    if not isinstance(llm, dict):
        return False
    try:
        routing_version = int(llm.get("routing_version", 0) or 0)
    except (TypeError, ValueError):
        routing_version = 0
    return bool(
        routing_version >= 2 or "instances" in llm or "default_chain" in llm or "routes" in llm
    )


def llm_migration_backup_path(config_path: str | Path) -> Path:
    """Return the permanent one-time backup path for a v1-to-v2 migration."""
    path = Path(config_path)
    return path.with_name(f"{path.name}.pre-llm-routing.bak")


def _create_llm_migration_backup(path: Path) -> Path | None:
    """Save the exact pre-v2 file once; never overwrite an earlier backup."""
    try:
        source = path.read_bytes()
    except FileNotFoundError:
        return None

    try:
        parsed = tomllib.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        # Preserve a malformed pre-migration file too. save_config historically
        # overwrote it; the new safety layer must not make recovery worse.
        parsed = {}
    if _uses_native_llm_routing(parsed):
        return None

    backup = llm_migration_backup_path(path)
    source_mode = path.stat().st_mode & 0o777
    try:
        descriptor = os.open(
            backup,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            source_mode,
        )
    except FileExistsError:
        return None

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
        # os.open applies umask, which can only make creation stricter. Restore
        # the exact source mode after all bytes are durable; the backup is never
        # more permissive than the source, even for the instant it is created.
        backup.chmod(source_mode)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _api_auth_lines(
    config: Config, on_disk_auth: dict[str, Any] | None, *, consult_local: bool
) -> list[str]:
    """Render the ``[api.auth]`` block, preserving on-disk credential provenance.

    ``on_disk_auth`` is the raw ``[api.auth]`` table currently on disk (``None``
    only when no file exists). Two preservation rules keep an unrelated write from
    silently changing the effective auth:

    1. **Override-layer fields (reviews r4#1 / r9 / r10).** Any field governed by an
       override LAYER above ``config.toml`` — an ``OPENBILICLAW_API_AUTH_*`` env var
       OR a ``config.local.toml`` ``[api.auth]`` key (both win in ``load_config``) —
       must NOT be re-rendered from the merged in-memory Config: that would bake the
       layer's value into ``config.toml`` as a stale literal that shifts the trust
       boundary / session lifetime once the layer is removed. Such a field is
       written from ``config.toml``'s own on-disk value (coerced exactly as the
       loader would, review r5#1) or omitted (falls back to default; the layer
       keeps governing at runtime).
    2. **Plaintext password convenience (review r8).** When the credential is NOT
       layer-governed and the operator uses an on-disk plaintext ``password`` key
       that the in-memory hash still verifies against, the credential is unchanged →
       keep the plaintext line so the reconcile fingerprint basis stays ``pw:`` and
       an unrelated save doesn't flip it to ``ph:`` and spuriously revoke remembered
       sessions on restart.

    All writers (`save_config` from startup secret-gen, `PUT /api/config`, cookie
    sync, admin, CLI) go through here, so the protection is central. (Layer-shadowed
    writes that *intend* to change auth, e.g. the admin endpoint, additionally do an
    effective-reload verify and refuse — see review r9.)
    """
    auth = config.api.auth
    overridden = _auth_overridden_fields(consult_local=consult_local)
    disk = on_disk_auth or {}
    lines = ["[api.auth]"]

    def emit(field: str, mem_line: str, disk_repr: Callable[[Any], str]) -> None:
        if field in overridden:
            if field in disk:
                # Re-render the base file's own value through the loader's coercion
                # (review r5#1) — never persist the override-layer value.
                lines.append(f"{field} = {disk_repr(disk[field])}")
            # else: omit — base file has no value; falls back to default at load
        else:
            lines.append(mem_line)

    emit("enabled", f"enabled = {_toml_bool(auth.enabled)}", lambda v: _toml_bool(_coerce_bool(v)))
    # The password credential maps from env PASSWORD / _PASSWORD_HASH and the
    # config.local `password` / `password_hash` keys onto the rendered field
    # `password_hash`; _build_api_auth honors EITHER an on-disk plaintext `password`
    # (hashed, preferred) OR `password_hash`.
    if "password_hash" in overridden:
        # a layer governs the credential → preserve whichever on-disk key(s) the
        # operator wrote in config.toml so removing the layer restores their own
        # password instead of leaving `enabled = true` with no credential (r6#1).
        disk_pw = disk.get("password")
        if disk_pw is not None and str(disk_pw).strip():
            lines.append(f"password = {_toml_string(str(disk_pw))}")
        disk_hash = disk.get("password_hash")
        if disk_hash is not None and str(disk_hash).strip():
            lines.append(f"password_hash = {_toml_string(str(disk_hash))}")
        # neither present → omit (no on-disk credential to preserve)
    elif _hash_matches_plaintext(disk.get("password"), auth.password_hash):
        # unchanged plaintext-backed credential → keep the plaintext line so the
        # reconcile fingerprint basis stays "pw:"+plain across restarts (r8).
        lines.append(f"password = {_toml_string(str(disk['password']))}")
    else:
        # no on-disk plaintext, or it no longer matches (password was changed in
        # memory, e.g. set-password) → persist the in-memory hash.
        lines.append(f"password_hash = {_toml_string(auth.password_hash)}")
    emit(
        "session_secret",
        f"session_secret = {_toml_string(auth.session_secret)}",
        lambda v: _toml_string(str(v)),
    )
    emit(
        "session_ttl_hours",
        f"session_ttl_hours = {auth.session_ttl_hours}",
        lambda v: str(_coerce_ttl_hours(v)),
    )
    emit(
        "trust_loopback",
        f"trust_loopback = {_toml_bool(auth.trust_loopback)}",
        lambda v: _toml_bool(_coerce_bool(v, default=True)),
    )
    # These two have no env override but config.local.toml CAN shadow them, so they
    # go through emit too (preserve the base file's list, or omit).
    emit(
        "trusted_proxies",
        f"trusted_proxies = {_toml_str_list(auth.trusted_proxies)}",
        lambda v: _toml_str_list(_coerce_str_list(v)),
    )
    emit(
        "allowed_bearer_origins",
        f"allowed_bearer_origins = {_toml_str_list(auth.allowed_bearer_origins)}",
        lambda v: _toml_str_list(_coerce_str_list(v)),
    )
    emit(
        "extension_access_enabled",
        f"extension_access_enabled = {_toml_bool(auth.extension_access_enabled)}",
        lambda v: _toml_bool(_coerce_bool(v)),
    )
    emit(
        "extension_access_keys",
        f"extension_access_keys = {_toml_str_list(auth.extension_access_keys)}",
        lambda v: _toml_str_list(_coerce_str_list(v)),
    )
    emit(
        "extension_token_ttl_hours",
        f"extension_token_ttl_hours = {auth.extension_token_ttl_hours}",
        lambda v: str(_coerce_extension_token_ttl_hours(v)),
    )
    return lines


def _autostart_lines(
    config: Config,
    on_disk_autostart: dict[str, Any] | None,
    *,
    autostart_authoritative: bool,
) -> list[str]:
    """Render ``[autostart]`` without clobbering the OS-registration intent.

    Ordinary whole-file writes can hold a stale ``Config`` snapshot, so they preserve
    the on-disk ``enabled`` value. Apply/CLI writers pass ``autostart_authoritative``
    and become the only code paths allowed to change it. ``manage_ollama`` has no OS
    side effect and is always rendered from memory.
    """
    lines = ["[autostart]"]
    if autostart_authoritative:
        lines.append(f"enabled = {_toml_bool(config.autostart.enabled)}")
    else:
        disk = on_disk_autostart or {}
        if "enabled" in disk:
            lines.append(f"enabled = {_toml_bool(_coerce_bool(disk['enabled'], default=False))}")
    lines.append(f"manage_ollama = {_toml_bool(config.autostart.manage_ollama)}")
    return lines


def save_config(
    config: Config,
    config_path: str | Path | None = None,
    *,
    autostart_authoritative: bool = False,
) -> Path:
    """Persist a Config dataclass to TOML.

    The first write that replaces an existing legacy LLM schema with native
    instance routing keeps an exact adjacent ``.pre-llm-routing.bak`` copy.
    """
    from openbiliclaw.sources.bangumi_client import (
        validate_bangumi_access_token,
        validate_bangumi_username,
    )

    _validate_auto_update_check_interval(config.scheduler.auto_update_check_interval_hours)
    config.sources.bangumi.username = validate_bangumi_username(config.sources.bangumi.username)
    config.sources.bangumi.access_token = validate_bangumi_access_token(
        config.sources.bangumi.access_token
    )
    path = Path(config_path) if config_path is not None else _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Capture the on-disk [api.auth] table so the renderer can preserve credential
    # provenance: env-overridden fields (review r4#1) and an unchanged plaintext
    # `password` convenience key (review r8). Read on every save (not just when
    # env-managed) so a normal settings/cookie write can't drop a plaintext
    # password and flip the reconcile fingerprint basis.
    on_disk_auth = _read_on_disk_auth(path) if path.exists() else None
    on_disk_autostart = _read_on_disk_autostart(path) if path.exists() else None
    # config.local.toml is merged ONLY when load_config runs with no explicit path
    # (production / default path). For a save to any other explicit file it was
    # never merged, so its overrides must not gate this render (review r11).
    consult_local = config_path is None or path.resolve() == _default_config_path().resolve()
    rendered = _render_config_toml(
        config,
        on_disk_auth=on_disk_auth,
        on_disk_autostart=on_disk_autostart,
        autostart_authoritative=autostart_authoritative,
        consult_local=consult_local,
    )
    if config.llm.instance_routing and path.exists():
        _create_llm_migration_backup(path)
    path.write_text(rendered, encoding="utf-8")
    return path


def _render_config_toml(
    config: Config,
    *,
    on_disk_auth: dict[str, Any] | None = None,
    on_disk_autostart: dict[str, Any] | None = None,
    autostart_authoritative: bool = False,
    consult_local: bool = False,
) -> str:
    """Render a Config dataclass into TOML."""
    lines = [
        "[general]",
        f"language = {_toml_string(config.language)}",
        f"data_dir = {_toml_string(config.data_dir)}",
        "",
        "[api]",
        f"host = {_toml_string(config.api.host)}",
        f"port = {config.api.port}",
        "",
        *_api_auth_lines(config, on_disk_auth, consult_local=consult_local),
        "",
    ]
    if config.llm.instance_routing:
        lines.extend(
            [
                "[llm]",
                "routing_version = 2",
                f"default_chain = {_toml_str_list(config.llm.default_chain)}",
                f"concurrency = {_normalize_llm_concurrency(config.llm.concurrency)}",
                f"timeout = {_normalize_llm_timeout(config.llm.timeout)}",
                "",
            ]
        )
        for instance_id, instance in config.llm.instances.items():
            lines.extend(_render_llm_instance_section(instance_id, instance))
    else:
        lines.extend(
            [
                "[llm]",
                f"default_provider = {_toml_string(config.llm.default_provider)}",
                f"concurrency = {_normalize_llm_concurrency(config.llm.concurrency)}",
                f"timeout = {_normalize_llm_timeout(config.llm.timeout)}",
                f"fallback_provider = {_toml_string(config.llm.fallback_provider)}",
                "",
            ]
        )
        lines.extend(_render_provider_section("openai", config.llm.openai))
        lines.extend(_render_provider_section("claude", config.llm.claude))
        lines.extend(_render_provider_section("gemini", config.llm.gemini))
        lines.extend(_render_provider_section("deepseek", config.llm.deepseek))
        lines.extend(_render_provider_section("ollama", config.llm.ollama))
        lines.extend(_render_provider_section("openrouter", config.llm.openrouter))
        lines.extend(_render_provider_section("openai_compatible", config.llm.openai_compatible))
    lines.extend(
        [
            "[llm.embedding]",
            f"provider = {_toml_string(config.llm.embedding.provider)}",
            f"model = {_toml_string(config.llm.embedding.model)}",
            f"api_key = {_toml_string(config.llm.embedding.api_key)}",
            f"base_url = {_toml_string(config.llm.embedding.base_url)}",
            f"output_dimensionality = {max(0, int(config.llm.embedding.output_dimensionality))}",
            f"similarity_threshold = {config.llm.embedding.similarity_threshold}",
            f"fallback_enabled = {_toml_bool(config.llm.embedding.fallback_enabled)}",
            f"fallback_provider = {_toml_string(config.llm.embedding.fallback_provider)}",
            f"multimodal_enabled = {_toml_bool(config.llm.embedding.multimodal_enabled)}",
            "",
        ]
    )
    if config.llm.instance_routing:
        lines.append("# Per-module routes (inherit=true uses llm.default_chain)")
        for bucket in _LLM_MODULE_BUCKETS:
            route = getattr(config.llm, bucket)
            lines.extend(
                [
                    f"[llm.routes.{bucket}]",
                    f"inherit = {_toml_bool(route.inherit)}",
                    f"chain = {_toml_str_list([] if route.inherit else route.chain)}",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "# Per-module LLM overrides (empty = use global default)",
                "[llm.soul]",
                f"provider = {_toml_string(config.llm.soul.provider)}",
                f"model = {_toml_string(config.llm.soul.model)}",
                "",
                "[llm.discovery]",
                f"provider = {_toml_string(config.llm.discovery.provider)}",
                f"model = {_toml_string(config.llm.discovery.model)}",
                "",
                "[llm.recommendation]",
                f"provider = {_toml_string(config.llm.recommendation.provider)}",
                f"model = {_toml_string(config.llm.recommendation.model)}",
                "",
                "[llm.evaluation]",
                f"provider = {_toml_string(config.llm.evaluation.provider)}",
                f"model = {_toml_string(config.llm.evaluation.model)}",
                "",
            ]
        )
    lines.extend(
        [
            "[bilibili]",
            f"auth_method = {_toml_string(config.bilibili.auth_method)}",
            f"cookie = {_toml_string(config.bilibili.cookie)}",
            f"proxy = {_toml_string(config.bilibili.proxy)}",
            "",
            "[bilibili.browser]",
            f"executable = {_toml_string(config.bilibili.browser_executable)}",
            f"headed = {_toml_bool(config.bilibili.browser_headed)}",
            "",
            "[network]",
            "# Overseas routing mode: system (default; inherit HTTP(S)_PROXY /",
            "# OS proxy), direct (ignore env proxy), custom (use proxy below).",
            "# Applies to LLM SDKs, YouTube, Bangumi,",
            "# the GitHub updater, Codex OAuth. B站/抖音/Ollama 等国内直连请求",
            "# 始终直连,不受此项影响。",
            "# 支持 http:// | https:// | socks5:// | socks5h://",
            f"mode = {_toml_string(config.network.mode)}",
            f"proxy = {_toml_string(config.network.proxy)}",
            "",
            "[sources.browser]",
            f"cdp_url = {_toml_string(config.sources.browser_cdp_url)}",
            f"headed = {_toml_bool(config.sources.browser_headed)}",
            "",
            "[sources.bilibili]",
            f"enabled = {_toml_bool(config.sources.bilibili.enabled)}",
            f"min_interval_minutes = {config.sources.bilibili.min_interval_minutes}",
            "",
            "[sources.xiaohongshu]",
            f"enabled = {_toml_bool(config.sources.xiaohongshu.enabled)}",
            f"daily_search_budget = {config.sources.xiaohongshu.daily_search_budget}",
            f"daily_creator_budget = {config.sources.xiaohongshu.daily_creator_budget}",
            f"task_interval_seconds = {config.sources.xiaohongshu.task_interval_seconds}",
            f"min_interval_minutes = {config.sources.xiaohongshu.min_interval_minutes}",
            "",
            "[sources.douyin]",
            f"enabled = {_toml_bool(config.sources.douyin.enabled)}",
            f"mode = {_toml_string(config.sources.douyin.mode)}",
            f"cookie_env = {_toml_string(config.sources.douyin.cookie_env)}",
            f"daily_search_budget = {config.sources.douyin.daily_search_budget}",
            f"daily_hot_budget = {config.sources.douyin.daily_hot_budget}",
            f"daily_feed_budget = {config.sources.douyin.daily_feed_budget}",
            f"request_interval_seconds = {config.sources.douyin.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.douyin.min_interval_minutes}",
            "",
            "[sources.youtube]",
            f"enabled = {_toml_bool(config.sources.youtube.enabled)}",
            f"daily_search_budget = {config.sources.youtube.daily_search_budget}",
            f"daily_trending_budget = {config.sources.youtube.daily_trending_budget}",
            f"daily_channel_budget = {config.sources.youtube.daily_channel_budget}",
            f"request_interval_seconds = {config.sources.youtube.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.youtube.min_interval_minutes}",
            "",
            "[sources.twitter]",
            f"enabled = {_toml_bool(config.sources.twitter.enabled)}",
            f"mode = {_toml_string(config.sources.twitter.mode)}",
            f"cookie_env = {_toml_string(config.sources.twitter.cookie_env)}",
            f"daily_search_budget = {config.sources.twitter.daily_search_budget}",
            f"daily_feed_budget = {config.sources.twitter.daily_feed_budget}",
            f"daily_creator_budget = {config.sources.twitter.daily_creator_budget}",
            f"request_interval_seconds = {config.sources.twitter.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.twitter.min_interval_minutes}",
            "",
            "[sources.zhihu]",
            f"enabled = {_toml_bool(config.sources.zhihu.enabled)}",
            f"source_modes = {_toml_str_list(list(config.sources.zhihu.source_modes))}",
            f"daily_search_budget = {config.sources.zhihu.daily_search_budget}",
            f"daily_hot_budget = {config.sources.zhihu.daily_hot_budget}",
            f"daily_feed_budget = {config.sources.zhihu.daily_feed_budget}",
            f"daily_creator_budget = {config.sources.zhihu.daily_creator_budget}",
            f"daily_related_budget = {config.sources.zhihu.daily_related_budget}",
            f"request_interval_seconds = {config.sources.zhihu.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.zhihu.min_interval_minutes}",
            "",
            "[sources.reddit]",
            f"enabled = {_toml_bool(config.sources.reddit.enabled)}",
            f"backend = {_toml_string(config.sources.reddit.backend)}",
            f"source_modes = {_toml_str_list(list(config.sources.reddit.source_modes))}",
            f"daily_search_budget = {config.sources.reddit.daily_search_budget}",
            f"daily_hot_budget = {config.sources.reddit.daily_hot_budget}",
            f"daily_subreddit_budget = {config.sources.reddit.daily_subreddit_budget}",
            f"daily_related_budget = {config.sources.reddit.daily_related_budget}",
            f"request_interval_seconds = {config.sources.reddit.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.reddit.min_interval_minutes}",
            "",
            "[sources.bangumi]",
            f"enabled = {_toml_bool(config.sources.bangumi.enabled)}",
            f"username = {_toml_string(config.sources.bangumi.username)}",
            f"access_token = {_toml_string(config.sources.bangumi.access_token)}",
            f"subject_types = {_toml_str_list(list(config.sources.bangumi.subject_types))}",
            f"source_modes = {_toml_str_list(list(config.sources.bangumi.source_modes))}",
            f"daily_search_budget = {config.sources.bangumi.daily_search_budget}",
            f"daily_ranked_budget = {config.sources.bangumi.daily_ranked_budget}",
            f"daily_latest_budget = {config.sources.bangumi.daily_latest_budget}",
            f"request_interval_seconds = {config.sources.bangumi.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.bangumi.min_interval_minutes}",
            f"bootstrap_limit = {config.sources.bangumi.bootstrap_limit}",
            "",
            "[scheduler]",
            f"enabled = {_toml_bool(config.scheduler.enabled)}",
            "pause_on_extension_disconnect = "
            f"{_toml_bool(config.scheduler.pause_on_extension_disconnect)}",
            "extension_disconnect_grace_seconds = "
            f"{config.scheduler.extension_disconnect_grace_seconds}",
            f"discovery_cron = {_toml_string(config.scheduler.discovery_cron)}",
            f"pool_target_count = {config.scheduler.pool_target_count}",
            f"account_sync_interval_hours = {config.scheduler.account_sync_interval_hours}",
            f"refresh_check_interval_seconds = {config.scheduler.refresh_check_interval_seconds}",
            f"signal_event_threshold = {config.scheduler.signal_event_threshold}",
            f"trending_refresh_minutes = {config.scheduler.trending_refresh_minutes}",
            f"explore_refresh_minutes = {config.scheduler.explore_refresh_minutes}",
            f"discovery_limit = {config.scheduler.discovery_limit}",
            f"delight_queue_limit = {config.scheduler.delight_queue_limit}",
            f"proactive_push_interval_seconds = {config.scheduler.proactive_push_interval_seconds}",
            "speculator_idle_interval_minutes = "
            f"{config.scheduler.speculator_idle_interval_minutes}",
            f"speculation_interval_minutes = {config.scheduler.speculation_interval_minutes}",
            f"speculation_ttl_days = {config.scheduler.speculation_ttl_days}",
            f"speculation_cooldown_days = {config.scheduler.speculation_cooldown_days}",
            "speculation_confirmation_threshold = "
            f"{config.scheduler.speculation_confirmation_threshold}",
            f"speculation_max_active = {config.scheduler.speculation_max_active}",
            "speculation_max_primary_interests = "
            f"{config.scheduler.speculation_max_primary_interests}",
            "speculation_max_secondary_interests = "
            f"{config.scheduler.speculation_max_secondary_interests}",
            "avoidance_speculation_interval_minutes = "
            f"{config.scheduler.avoidance_speculation_interval_minutes}",
            f"avoidance_speculation_ttl_days = {config.scheduler.avoidance_speculation_ttl_days}",
            "avoidance_speculation_cooldown_days = "
            f"{config.scheduler.avoidance_speculation_cooldown_days}",
            "avoidance_speculation_confirmation_threshold = "
            f"{config.scheduler.avoidance_speculation_confirmation_threshold}",
            "avoidance_speculation_max_active = "
            f"{config.scheduler.avoidance_speculation_max_active}",
            # User-tunable from the extension popup and the desktop settings
            # page; omitting it here made those inputs write-only (every save
            # silently reverted the value to the default 3). It doubles as the
            # unified interest line's priority-flush threshold.
            f"feedback_batch_threshold = {config.scheduler.feedback_batch_threshold}",
            f"unified_interest_line = {_toml_bool(config.scheduler.unified_interest_line)}",
            "profile_consolidation_enabled = "
            f"{_toml_bool(config.scheduler.profile_consolidation_enabled)}",
            "profile_consolidation_interval_hours = "
            f"{config.scheduler.profile_consolidation_interval_hours}",
            "profile_consolidation_like_target_upper = "
            f"{config.scheduler.profile_consolidation_like_target_upper}",
            "profile_consolidation_like_target_soft = "
            f"{config.scheduler.profile_consolidation_like_target_soft}",
            "profile_consolidation_archive_enabled = "
            f"{_toml_bool(config.scheduler.profile_consolidation_archive_enabled)}",
            f"auto_update_enabled = {_toml_bool(config.scheduler.auto_update_enabled)}",
            "auto_update_check_interval_hours = "
            f"{config.scheduler.auto_update_check_interval_hours}",
            "auto_update_allow_prerelease = "
            f"{_toml_bool(config.scheduler.auto_update_allow_prerelease)}",
            "auto_update_allowed_remotes = "
            f"{_toml_str_list(config.scheduler.auto_update_allowed_remotes)}",
            "",
            "[scheduler.pool_source_shares]",
            f"bilibili = {int(config.scheduler.pool_source_shares.get('bilibili', 5))}",
            f"xiaohongshu = {int(config.scheduler.pool_source_shares.get('xiaohongshu', 1))}",
            f"douyin = {int(config.scheduler.pool_source_shares.get('douyin', 1))}",
            f"youtube = {int(config.scheduler.pool_source_shares.get('youtube', 1))}",
            f"twitter = {int(config.scheduler.pool_source_shares.get('twitter', 1))}",
            f"zhihu = {int(config.scheduler.pool_source_shares.get('zhihu', 1))}",
            f"reddit = {int(config.scheduler.pool_source_shares.get('reddit', 1))}",
            f"bangumi = {int(config.scheduler.pool_source_shares.get('bangumi', 1))}",
            "",
            "[discovery]",
            "unified_keyword_planner_enabled = "
            f"{_toml_bool(config.discovery.unified_keyword_planner_enabled)}",
            f"kw_cache_high = {config.discovery.kw_cache_high}",
            f"kw_cache_low = {config.discovery.kw_cache_low}",
            f"gen_batch = {config.discovery.gen_batch}",
            f"fetch_batch = {config.discovery.fetch_batch}",
            f"history_window_size = {config.discovery.history_window_size}",
            f"history_window_hours = {config.discovery.history_window_hours}",
            f"claim_lease_minutes = {config.discovery.claim_lease_minutes}",
            f"planner_poll_seconds = {config.discovery.planner_poll_seconds}",
            f"plan_ttl_hours = {config.discovery.plan_ttl_hours}",
            f"admission_min_score = {config.discovery.admission_min_score:g}",
            f"candidate_eval_concurrency = {config.discovery.candidate_eval_concurrency}",
            "inspiration_search_enabled = "
            f"{_toml_bool(config.discovery.inspiration_search_enabled)}",
            "inspiration_search_backends = "
            f"{_toml_str_list(list(config.discovery.inspiration_search_backends))}",
            "inspiration_replace_merged_keywords = "
            f"{_toml_bool(config.discovery.inspiration_replace_merged_keywords)}",
            f"inspiration_breadth = {_toml_string(config.discovery.inspiration_breadth)}",
            "multimodal_evaluation_enabled = "
            f"{_toml_bool(config.discovery.multimodal_evaluation_enabled)}",
            f"multimodal_batch_size = {config.discovery.multimodal_batch_size}",
            f"multimodal_image_max_px = {config.discovery.multimodal_image_max_px}",
            f"multimodal_image_quality = {config.discovery.multimodal_image_quality}",
            "multimodal_image_timeout_seconds = "
            f"{config.discovery.multimodal_image_timeout_seconds}",
            "",
            *_autostart_lines(
                config,
                on_disk_autostart,
                autostart_authoritative=autostart_authoritative,
            ),
            "",
            "[saved_sync]",
            f"auto_sync_enabled = {_toml_bool(config.saved_sync.auto_sync_enabled)}",
            "",
            "[storage]",
            f"db_path = {_toml_string(config.storage.db_path)}",
            "",
            "[logging]",
            f"level = {_toml_string(config.logging.level)}",
            f"file_level = {_toml_string(config.logging.file_level)}",
            f"directory = {_toml_string(config.logging.directory)}",
            f"filename = {_toml_string(config.logging.filename)}",
            f"max_file_size_mb = {config.logging.max_file_size_mb}",
            f"backup_count = {config.logging.backup_count}",
            f"aggregate_budget_mb = {config.logging.aggregate_budget_mb}",
            f"unmanaged_truncate_mb = {config.logging.unmanaged_truncate_mb}",
            f"unmanaged_max_age_days = {config.logging.unmanaged_max_age_days}",
            "",
            "[soul]",
            "# Deep-write consistency gate (spec Phase 3). shadow = async",
            "# side-channel judging without blocking writes (default);",
            "# enforce = synchronous gate (savable only after >=14 days of",
            "# shadow data, unless force flag set); off = full bypass.",
            f"posture_gate_mode = {_toml_string(config.soul.posture_gate_mode)}",
            "# Escape hatch: allow saving enforce without the 14-day shadow",
            "# observation gate. Risky — enables gating before it is calibrated.",
            f"posture_gate_force_enforce = {_toml_bool(config.soul.posture_gate_force_enforce)}",
            "# Topic-lifecycle serialization (spec Phase 4). off (default) keeps",
            "# the LLM-facing profile byte-identical; on excludes archived topics.",
            f"topic_lifecycle_serialization = "
            f"{_toml_string(config.soul.topic_lifecycle_serialization)}",
            "",
            "[soul.preference]",
            "# v0.3.x event-satisfaction signal. When true, preference",
            "# analysis ignores passive negative events such as quick_exit.",
            "# Explicit dislike feedback is retained as disliked_topics",
            "# evidence instead of being learned as a positive interest.",
            "satisfaction_filter_enabled = "
            f"{_toml_bool(config.soul.preference.satisfaction_filter_enabled)}",
            "",
        ]
    )
    return "\n".join(lines)


def _render_llm_instance_section(
    instance_id: str,
    instance: LLMInstanceConfig,
) -> list[str]:
    """Render one v2 provider instance with a quoted dynamic table key."""
    return [
        f"[llm.instances.{_toml_string(instance_id)}]",
        f"name = {_toml_string(instance.name)}",
        f"provider_type = {_toml_string(instance.provider_type)}",
        f"enabled = {_toml_bool(instance.enabled)}",
        f"api_key = {_toml_string(instance.api_key)}",
        f"model = {_toml_string(instance.model)}",
        f"base_url = {_toml_string(instance.base_url)}",
        f"auth_mode = {_toml_string(instance.auth_mode)}",
        f"api_flavor = {_toml_string(instance.api_flavor)}",
        f"http_referer = {_toml_string(instance.http_referer)}",
        f"x_title = {_toml_string(instance.x_title)}",
        f"reasoning_effort = {_toml_string(instance.reasoning_effort)}",
        f"num_ctx = {max(0, int(instance.num_ctx))}",
        "",
    ]


def _render_provider_section(name: str, provider: LLMProviderConfig) -> list[str]:
    """Render one provider subsection."""
    lines = [f"[llm.{name}]"]
    lines.append(f"api_key = {_toml_string(provider.api_key)}")
    lines.append(f"model = {_toml_string(provider.model)}")
    if name in {"openai", "claude", "deepseek", "ollama", "openrouter", "openai_compatible"}:
        lines.append(f"base_url = {_toml_string(provider.base_url)}")
    if name == "openai":
        lines.append(f"auth_mode = {_toml_string(provider.auth_mode)}")
    if name in {"openai", "openai_compatible"}:
        lines.append(f"api_flavor = {_toml_string(provider.api_flavor)}")
    if name in {"openai", "claude", "gemini", "deepseek", "openrouter"}:
        lines.append(f"reasoning_effort = {_toml_string(provider.reasoning_effort)}")
    if name == "openrouter":
        lines.append(f"http_referer = {_toml_string(provider.http_referer)}")
        lines.append(f"x_title = {_toml_string(provider.x_title)}")
    lines.append("")
    return lines


def _toml_string(value: str) -> str:
    """Render a TOML string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    """Render a TOML boolean literal."""
    return "true" if value else "false"


def _toml_str_list(values: list[str]) -> str:
    """Render a TOML array of strings."""
    return "[" + ", ".join(_toml_string(item) for item in values) + "]"


def validate_runtime_config(config: Config) -> None:
    """Raise ConfigError when runtime-critical config is invalid."""
    issues = _collect_config_issues(config)
    if issues:
        issue = issues[0]
        raise ConfigError(f"{issue.field}: {issue.message}")
