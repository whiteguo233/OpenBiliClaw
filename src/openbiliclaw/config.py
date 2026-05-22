"""Configuration management for OpenBiliClaw.

Loads configuration from TOML files with environment variable overrides.
SchedulerConfig.enabled is the authoritative gate for background LLM loops.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Default config search paths
_CONFIG_FILENAMES = ["config.toml", "config.local.toml"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT_ENV = "OPENBILICLAW_PROJECT_ROOT"
_SUPPORTED_AUTH_METHODS = {"cookie", "qrcode", "none"}
_SUPPORTED_OPENAI_AUTH_MODES = {"", "api_key", "codex_oauth"}
_MIN_POOL_TARGET_COUNT = 1
_MAX_POOL_TARGET_COUNT = 600
_DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS = 90
_DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS = 60
_DEFAULT_SIGNAL_EVENT_THRESHOLD = 6
_DEFAULT_TRENDING_REFRESH_HOURS = 3
_DEFAULT_EXPLORE_REFRESH_HOURS = 12
_DEFAULT_DISCOVERY_LIMIT = 30
_DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS = 120
_DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES = 30
_DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 8,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
}
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


@dataclass
class ConfigDiagnostics:
    """Supplementary information collected during config loading."""

    config_path: Path | None = None
    created_default_config: bool = False
    messages: list[str] = field(default_factory=list)
    issues: list[ConfigIssue] = field(default_factory=list)


@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    auth_mode: str = ""
    http_referer: str = ""
    x_title: str = ""
    # DeepSeek v4 thinking-mode control. "" disables; "high" / "max" enable
    # reasoning. v0.3.31 default = "max" — combined with v0.3.29's prompt-cache
    # refactor (system 100% static, DeepSeek auto-cache 90% off) the
    # reasoning-token cost becomes affordable, and the LLM produces noticeably
    # better tags (franchise_key consistent across batch, score_threshold=0.70
    # still gives healthy pool throughput). Set to "" if the per-day spend
    # creeps too high and you want to trade off label quality for budget.
    # Ignored by providers that don't accept ``thinking`` / ``reasoning_effort``.
    reasoning_effort: str = "max"


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
    similarity_threshold: float = 0.82
    fallback_enabled: bool = False
    fallback_provider: str = ""


@dataclass
class ModuleLLMConfig:
    """Per-module LLM override. Empty strings = use global defaults."""

    provider: str = ""
    model: str = ""


@dataclass
class LLMConfig:
    """LLM configuration with global defaults and per-module overrides."""

    default_provider: str = "openai"
    fallback_enabled: bool = False
    fallback_provider: str = ""
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
    browser_executable: str = ""
    browser_headed: bool = False


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""

    enabled: bool = True
    pause_on_extension_disconnect: bool = False
    extension_disconnect_grace_seconds: int = _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    discovery_cron: str = "0 */8 * * *"
    pool_target_count: int = 600
    pool_source_shares: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_POOL_SOURCE_SHARES)
    )
    account_sync_interval_hours: int = 6
    refresh_check_interval_seconds: int = _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS
    signal_event_threshold: int = _DEFAULT_SIGNAL_EVENT_THRESHOLD
    trending_refresh_hours: int = _DEFAULT_TRENDING_REFRESH_HOURS
    explore_refresh_hours: int = _DEFAULT_EXPLORE_REFRESH_HOURS
    discovery_limit: int = _DEFAULT_DISCOVERY_LIMIT
    proactive_push_interval_seconds: int = _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS
    speculator_idle_interval_minutes: int = _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES
    speculation_interval_minutes: int = 10
    speculation_ttl_days: int = 3
    speculation_cooldown_days: int = 7
    speculation_confirmation_threshold: int = 3
    speculation_max_active: int = 5
    speculation_max_primary_interests: int = 15
    speculation_max_secondary_interests: int = 60
    # Default off. The auto-updater pulls from GitHub releases and
    # restarts the backend when a newer version is detected, but it has
    # historically caused restart loops when the local
    # ``openbiliclaw.__version__`` drifts from the published release
    # tag. Opt-in only — set ``true`` in config.toml after the release
    # pipeline is reliable.
    auto_update_enabled: bool = False
    auto_update_check_interval_hours: int = 6


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
    daily_search_budget: int = 30
    # Max creator-subscription fetch tasks per day.
    daily_creator_budget: int = 10
    # Seconds the extension dispatcher waits between tasks.
    task_interval_seconds: int = 45


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
    daily_search_budget: int = 30
    daily_hot_budget: int = 5
    daily_feed_budget: int = 30
    request_interval_seconds: int = 2


@dataclass
class YoutubeSourceConfig:
    """YouTube source-specific configuration.

    YouTube steady-state discovery runs through a backend-direct runtime
    producer. The budget knobs cap per-day execution units: search
    queries, trending fetch breadth, and subscribed-channel breadth.
    """

    enabled: bool = False
    daily_search_budget: int = 6
    daily_trending_budget: int = 50
    daily_channel_budget: int = 10
    request_interval_seconds: int = 2
    min_interval_minutes: int = 60
    # When True, the recommendation API endpoint will detect Bilibili
    # reposts of English/foreign-language videos and replace the link
    # with the original YouTube URL.  Detection is title-based heuristic
    # + yt-dlp search.  Results cached in <data_dir>/yt_replacer_cache.json.
    replace_bilibili_reposts: bool = False
    # Cache TTL for yt_replacer results in hours (default 24).
    yt_replacer_cache_ttl: int = 24
    # When True, the Bilibili content script will automatically redirect
    # to YouTube when viewing a reposted video on B站.
    auto_redirect_youtube: bool = False


@dataclass
class BilibiliSourceConfig:
    """Bilibili discovery source switch."""

    enabled: bool = True


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


@dataclass
class StorageConfig:
    """Storage configuration."""

    db_path: str = "data/openbiliclaw.db"


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


@dataclass
class SoulConfig:
    """Soul engine knobs. Currently only the preference sub-section."""

    preference: SoulPreferenceConfig = field(default_factory=SoulPreferenceConfig)


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


@dataclass
class RecommendationConfig:
    """Recommendation-engine tuning knobs.

    Currently houses 营销号-filter parameters; over time other
    cross-cutting recommendation switches that don't fit cleanly into
    SchedulerConfig (which is about *when* to refresh) or
    SourcesConfig (which is about *where* candidates come from) can
    move here.
    """

    # When a candidate's marketing-filter score crosses this
    # threshold it's treated as "clickbait" for hard-filter contexts
    # like the delight push notification. The curator's soft demote
    # path still applies below the threshold, scaled by
    # ``marketing_filter_demote_weight``.
    #
    # Set to 1.01 (i.e. unreachable) to effectively disable the
    # filter without removing the configuration. Set lower (e.g. 0.4)
    # to be more aggressive — be aware that false positives compound:
    # over-filtering looks to the user like the recommendation
    # engine ignored their interests.
    marketing_filter_threshold: float = 0.6

    # Per-unit demote weight. The curator subtracts
    # ``marketing_filter_demote_weight * score`` from a candidate's
    # composite rec_score. With score in [0, 1] and base scores
    # typically in the 0–20 range, a default weight of 8 lets a
    # heavily-clickbait video (score ≈ 1.0) drop ~8 points — usually
    # enough to push it well below organic relevance-driven peers
    # without being a hard exclusion.
    marketing_filter_demote_weight: float = 8.0

    # When True, the proactive-delight push path also applies the
    # threshold as a hard filter. Pushing a notification for a
    # clickbait video is much worse UX than burying it, so this
    # is on by default. Disable only if the heuristics keep
    # mis-filtering legitimate delights.
    marketing_filter_block_delight: bool = True


@dataclass
class Config:
    """Root configuration for OpenBiliClaw."""

    language: str = "zh"
    data_dir: str = "data"
    api: ApiConfig = field(default_factory=ApiConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Top-level `[soul]` is distinct from `[llm.soul]` (per-module
    # provider override): this carries soul-engine behavior toggles.
    soul: SoulConfig = field(default_factory=SoulConfig)
    recommendation: RecommendationConfig = field(default_factory=RecommendationConfig)

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
        parts = env_key[len(prefix) :].lower().split("_")
        current = raw
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = env_value
    return raw


def _build_config(raw: dict[str, Any]) -> Config:
    """Build a Config dataclass from raw dict."""
    general = raw.get("general", {})
    api_raw = raw.get("api", {}) if isinstance(raw.get("api"), dict) else {}
    llm_raw = raw.get("llm", {})
    bili_raw = raw.get("bilibili", {})
    sources_raw = raw.get("sources", {})
    sched_raw = dict(raw.get("scheduler", {}))
    store_raw = raw.get("storage", {})
    logging_raw = raw.get("logging", {})

    embedding_raw = llm_raw.get("embedding", {})
    llm = LLMConfig(
        default_provider=llm_raw.get("default_provider", "openai"),
        fallback_enabled=bool(llm_raw.get("fallback_enabled", False)),
        fallback_provider=llm_raw.get("fallback_provider", ""),
        openai=LLMProviderConfig(**llm_raw.get("openai", {})),
        claude=LLMProviderConfig(**llm_raw.get("claude", {})),
        gemini=LLMProviderConfig(**llm_raw.get("gemini", {})),
        deepseek=LLMProviderConfig(**llm_raw.get("deepseek", {})),
        ollama=LLMProviderConfig(**llm_raw.get("ollama", {})),
        openrouter=LLMProviderConfig(**llm_raw.get("openrouter", {})),
        openai_compatible=LLMProviderConfig(**llm_raw.get("openai_compatible", {})),
        embedding=EmbeddingConfig(
            **{
                k: v
                for k, v in embedding_raw.items()
                if k
                in (
                    "provider",
                    "model",
                    "api_key",
                    "base_url",
                    "similarity_threshold",
                    "fallback_enabled",
                    "fallback_provider",
                )
            }
        ),
        soul=ModuleLLMConfig(
            **{k: v for k, v in llm_raw.get("soul", {}).items() if k in ("provider", "model")}
        ),
        discovery=ModuleLLMConfig(
            **{k: v for k, v in llm_raw.get("discovery", {}).items() if k in ("provider", "model")}
        ),
        recommendation=ModuleLLMConfig(
            **{
                k: v
                for k, v in llm_raw.get("recommendation", {}).items()
                if k in ("provider", "model")
            }
        ),
        evaluation=ModuleLLMConfig(
            **{k: v for k, v in llm_raw.get("evaluation", {}).items() if k in ("provider", "model")}
        ),
    )

    browser_raw = bili_raw.pop("browser", {})
    bilibili = BilibiliConfig(
        auth_method=bili_raw.get("auth_method", "cookie"),
        cookie=bili_raw.get("cookie", ""),
        browser_executable=browser_raw.get("executable", ""),
        browser_headed=browser_raw.get("headed", False),
    )

    sources_browser_raw = sources_raw.get("browser", {})
    bilibili_source_raw = sources_raw.get("bilibili", {})
    xhs_raw = sources_raw.get("xiaohongshu", {})
    douyin_raw = sources_raw.get("douyin", {})
    youtube_raw = sources_raw.get("youtube", {})
    sources = SourcesConfig(
        browser_cdp_url=sources_browser_raw.get("cdp_url", ""),
        browser_headed=sources_browser_raw.get("headed", False),
        bilibili=BilibiliSourceConfig(
            enabled=bool(bilibili_source_raw.get("enabled", True)),
        ),
        xiaohongshu=XiaohongshuSourceConfig(
            enabled=bool(xhs_raw.get("enabled", False)),
            daily_search_budget=int(xhs_raw.get("daily_search_budget", 30)),
            daily_creator_budget=int(xhs_raw.get("daily_creator_budget", 10)),
            task_interval_seconds=int(xhs_raw.get("task_interval_seconds", 45)),
        ),
        douyin=DouyinSourceConfig(
            enabled=bool(douyin_raw.get("enabled", False)),
            mode=str(douyin_raw.get("mode", "direct")),
            cookie_env=str(douyin_raw.get("cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")),
            daily_search_budget=int(douyin_raw.get("daily_search_budget", 30)),
            daily_hot_budget=int(douyin_raw.get("daily_hot_budget", 5)),
            daily_feed_budget=int(douyin_raw.get("daily_feed_budget", 30)),
            request_interval_seconds=int(douyin_raw.get("request_interval_seconds", 2)),
        ),
        youtube=YoutubeSourceConfig(
            enabled=bool(youtube_raw.get("enabled", False)),
            daily_search_budget=int(youtube_raw.get("daily_search_budget", 6)),
            daily_trending_budget=int(youtube_raw.get("daily_trending_budget", 50)),
            daily_channel_budget=int(youtube_raw.get("daily_channel_budget", 10)),
            request_interval_seconds=int(youtube_raw.get("request_interval_seconds", 2)),
            min_interval_minutes=max(0, int(youtube_raw.get("min_interval_minutes", 60))),
            replace_bilibili_reposts=bool(youtube_raw.get("replace_bilibili_reposts", False)),
            yt_replacer_cache_ttl=int(youtube_raw.get("yt_replacer_cache_ttl", 24)),
            auto_redirect_youtube=bool(youtube_raw.get("auto_redirect_youtube", False)),
        ),
    )

    soul_raw = raw.get("soul", {}) if isinstance(raw.get("soul"), dict) else {}
    soul_preference_raw = (
        soul_raw.get("preference", {}) if isinstance(soul_raw.get("preference"), dict) else {}
    )
    soul = SoulConfig(
        preference=SoulPreferenceConfig(
            satisfaction_filter_enabled=bool(
                soul_preference_raw.get("satisfaction_filter_enabled", True)
            ),
        ),
    )

    return Config(
        language=general.get("language", "zh"),
        data_dir=general.get("data_dir", "data"),
        api=ApiConfig(
            host=str(api_raw.get("host", "0.0.0.0") or "0.0.0.0").strip() or "0.0.0.0",
            port=_normalize_api_port(api_raw.get("port", 8420)),
        ),
        llm=llm,
        bilibili=bilibili,
        sources=sources,
        scheduler=SchedulerConfig(
            **{
                **sched_raw,
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
                "trending_refresh_hours": _normalize_scheduler_int(
                    sched_raw.get("trending_refresh_hours"),
                    default=_DEFAULT_TRENDING_REFRESH_HOURS,
                    min_value=1,
                ),
                "explore_refresh_hours": _normalize_scheduler_int(
                    sched_raw.get("explore_refresh_hours"),
                    default=_DEFAULT_EXPLORE_REFRESH_HOURS,
                    min_value=1,
                ),
                "discovery_limit": _normalize_scheduler_int(
                    sched_raw.get("discovery_limit"),
                    default=_DEFAULT_DISCOVERY_LIMIT,
                    min_value=1,
                    max_value=60,
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
            }
        ),
        storage=StorageConfig(**store_raw),
        logging=LoggingConfig(**logging_raw),
        soul=soul,
        recommendation=_build_recommendation_config(raw.get("recommendation", {})),
    )


def _build_recommendation_config(raw: object) -> RecommendationConfig:
    """Build RecommendationConfig from a TOML ``[recommendation]`` block.

    Tolerates a missing/empty block (returns defaults), and clamps
    out-of-range values rather than raising — config errors here
    would block the whole app from starting, and the safe fallback
    for a malformed knob is "off / default" rather than "crash".
    """
    if not isinstance(raw, dict):
        return RecommendationConfig()

    defaults = RecommendationConfig()

    def _clamped_float(key: str, default: float, lo: float, hi: float) -> float:
        value = raw.get(key, default)
        try:
            f = float(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, f))

    return RecommendationConfig(
        # Threshold can run from 0 (filter everything) to 1.01
        # (filter nothing — > any possible score).
        marketing_filter_threshold=_clamped_float(
            "marketing_filter_threshold",
            defaults.marketing_filter_threshold,
            0.0,
            1.01,
        ),
        # Weight is a multiplier on the candidate score; values
        # above 50 are absurd (would dominate every other signal).
        marketing_filter_demote_weight=_clamped_float(
            "marketing_filter_demote_weight",
            defaults.marketing_filter_demote_weight,
            0.0,
            50.0,
        ),
        marketing_filter_block_delight=bool(
            raw.get(
                "marketing_filter_block_delight",
                defaults.marketing_filter_block_delight,
            )
        ),
    )


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


def _normalize_pool_source_shares(value: object) -> dict[str, int]:
    """Normalize scheduler pool source shares from TOML into positive ints."""
    if not isinstance(value, dict):
        return dict(_DEFAULT_POOL_SOURCE_SHARES)

    shares: dict[str, int] = {}
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


def _collect_config_issues(config: Config) -> list[ConfigIssue]:
    """Collect non-fatal config issues to display as guidance."""
    issues: list[ConfigIssue] = []

    if config.bilibili.auth_method not in _SUPPORTED_AUTH_METHODS:
        supported = ", ".join(sorted(_SUPPORTED_AUTH_METHODS))
        issues.append(
            ConfigIssue(
                field="bilibili.auth_method",
                message=f"`bilibili.auth_method` 仅支持: {supported}。",
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
    config = _build_config(raw)
    diagnostics.issues.extend(_collect_config_issues(config))
    return config, diagnostics


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration only, without diagnostics."""
    config, _ = load_config_with_diagnostics(config_path, ensure_default_file=False)
    return config


def save_config(config: Config, config_path: str | Path | None = None) -> Path:
    """Persist a Config dataclass to TOML."""
    path = Path(config_path) if config_path is not None else _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_config_toml(config), encoding="utf-8")
    return path


def _render_config_toml(config: Config) -> str:
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
        "[llm]",
        f"default_provider = {_toml_string(config.llm.default_provider)}",
        f"fallback_enabled = {_toml_bool(config.llm.fallback_enabled)}",
        f"fallback_provider = {_toml_string(config.llm.fallback_provider)}",
        "",
    ]
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
            f"similarity_threshold = {config.llm.embedding.similarity_threshold}",
            f"fallback_enabled = {_toml_bool(config.llm.embedding.fallback_enabled)}",
            f"fallback_provider = {_toml_string(config.llm.embedding.fallback_provider)}",
            "",
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
            "",
            "[bilibili.browser]",
            f"executable = {_toml_string(config.bilibili.browser_executable)}",
            f"headed = {_toml_bool(config.bilibili.browser_headed)}",
            "",
            "[sources.browser]",
            f"cdp_url = {_toml_string(config.sources.browser_cdp_url)}",
            f"headed = {_toml_bool(config.sources.browser_headed)}",
            "",
            "[sources.bilibili]",
            f"enabled = {_toml_bool(config.sources.bilibili.enabled)}",
            "",
            "[sources.xiaohongshu]",
            f"enabled = {_toml_bool(config.sources.xiaohongshu.enabled)}",
            f"daily_search_budget = {config.sources.xiaohongshu.daily_search_budget}",
            f"daily_creator_budget = {config.sources.xiaohongshu.daily_creator_budget}",
            f"task_interval_seconds = {config.sources.xiaohongshu.task_interval_seconds}",
            "",
            "[sources.douyin]",
            f"enabled = {_toml_bool(config.sources.douyin.enabled)}",
            f"mode = {_toml_string(config.sources.douyin.mode)}",
            f"cookie_env = {_toml_string(config.sources.douyin.cookie_env)}",
            f"daily_search_budget = {config.sources.douyin.daily_search_budget}",
            f"daily_hot_budget = {config.sources.douyin.daily_hot_budget}",
            f"daily_feed_budget = {config.sources.douyin.daily_feed_budget}",
            f"request_interval_seconds = {config.sources.douyin.request_interval_seconds}",
            "",
            "[sources.youtube]",
            f"enabled = {_toml_bool(config.sources.youtube.enabled)}",
            f"daily_search_budget = {config.sources.youtube.daily_search_budget}",
            f"daily_trending_budget = {config.sources.youtube.daily_trending_budget}",
            f"daily_channel_budget = {config.sources.youtube.daily_channel_budget}",
            f"request_interval_seconds = {config.sources.youtube.request_interval_seconds}",
            f"min_interval_minutes = {config.sources.youtube.min_interval_minutes}",
            f"replace_bilibili_reposts = {_toml_bool(config.sources.youtube.replace_bilibili_reposts)}",
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
            f"trending_refresh_hours = {config.scheduler.trending_refresh_hours}",
            f"explore_refresh_hours = {config.scheduler.explore_refresh_hours}",
            f"discovery_limit = {config.scheduler.discovery_limit}",
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
            f"auto_update_enabled = {_toml_bool(config.scheduler.auto_update_enabled)}",
            "auto_update_check_interval_hours = "
            f"{config.scheduler.auto_update_check_interval_hours}",
            "",
            "[scheduler.pool_source_shares]",
            f"bilibili = {int(config.scheduler.pool_source_shares.get('bilibili', 8))}",
            f"xiaohongshu = {int(config.scheduler.pool_source_shares.get('xiaohongshu', 1))}",
            f"douyin = {int(config.scheduler.pool_source_shares.get('douyin', 1))}",
            f"youtube = {int(config.scheduler.pool_source_shares.get('youtube', 1))}",
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


def _render_provider_section(name: str, provider: LLMProviderConfig) -> list[str]:
    """Render one provider subsection."""
    lines = [f"[llm.{name}]"]
    lines.append(f"api_key = {_toml_string(provider.api_key)}")
    lines.append(f"model = {_toml_string(provider.model)}")
    if name in {"openai", "deepseek", "ollama", "openrouter", "openai_compatible"}:
        lines.append(f"base_url = {_toml_string(provider.base_url)}")
    if name == "openai":
        lines.append(f"auth_mode = {_toml_string(provider.auth_mode)}")
    if name == "deepseek" and provider.reasoning_effort:
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


def validate_runtime_config(config: Config) -> None:
    """Raise ConfigError when runtime-critical config is invalid."""
    issues = _collect_config_issues(config)
    if issues:
        issue = issues[0]
        raise ConfigError(f"{issue.field}: {issue.message}")
