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
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from collections.abc import Callable

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
_DEFAULT_FEEDBACK_BATCH_THRESHOLD = 3
DEFAULT_LLM_CONCURRENCY = 3
_MIN_LLM_CONCURRENCY = 1
_MAX_LLM_CONCURRENCY = 16
_DEFAULT_LLM_TIMEOUT = 300
_MIN_LLM_TIMEOUT = 10
_DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 5,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
    "twitter": 1,
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
    # Ollama-only: context window (tokens). 0 = use Ollama's server default
    # (usually 4096) via the OpenAI-compat ``/v1`` shim. When >0, chat routes
    # through Ollama's native ``/api/chat`` so ``options.num_ctx`` actually
    # applies — the ``/v1`` shim silently ignores it, truncating large batch
    # prompts and breaking structured-JSON output. Ignored by all other
    # providers. See OllamaProvider._complete_native.
    num_ctx: int = 0


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


@dataclass
class ModuleLLMConfig:
    """Per-module LLM override. Empty strings = use global defaults."""

    provider: str = ""
    model: str = ""


@dataclass
class LLMConfig:
    """LLM configuration with global defaults and per-module overrides."""

    default_provider: str = "deepseek"
    concurrency: int = DEFAULT_LLM_CONCURRENCY
    timeout: int = _DEFAULT_LLM_TIMEOUT
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
    pool_target_count: int = 300
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
    avoidance_speculation_interval_minutes: int = 10
    avoidance_speculation_ttl_days: int = 3
    avoidance_speculation_cooldown_days: int = 7
    avoidance_speculation_confirmation_threshold: int = 3
    avoidance_speculation_max_active: int = 5
    feedback_batch_threshold: int = _DEFAULT_FEEDBACK_BATCH_THRESHOLD
    # Default off. The auto-updater pulls from GitHub releases and
    # restarts the backend when a newer version is detected, but it has
    # historically caused restart loops when the local
    # ``openbiliclaw.__version__`` drifts from the published release
    # tag. Opt-in only — set ``true`` in config.toml after the release
    # pipeline is reliable.
    auto_update_enabled: bool = False
    auto_update_check_interval_hours: int = 6
    auto_update_allow_prerelease: bool = False
    auto_update_allowed_remotes: list[str] = field(
        default_factory=lambda: list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)
    )


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
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    request_interval_seconds: int = 2


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
    min_interval_minutes: int = 60


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
    min_interval_minutes: int = 60


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
    twitter: TwitterSourceConfig = field(default_factory=TwitterSourceConfig)


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
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    autostart: AutostartConfig = field(default_factory=AutostartConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Top-level `[soul]` is distinct from `[llm.soul]` (per-module
    # provider override): this carries soul-engine behavior toggles.
    soul: SoulConfig = field(default_factory=SoulConfig)

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


def _build_config(raw: dict[str, Any]) -> Config:
    """Build a Config dataclass from raw dict."""
    general = raw.get("general", {})
    api_raw = raw.get("api", {}) if isinstance(raw.get("api"), dict) else {}
    llm_raw = raw.get("llm", {})
    bili_raw = raw.get("bilibili", {})
    sources_raw = raw.get("sources", {})
    sched_raw = dict(raw.get("scheduler", {}))
    autostart_raw = raw.get("autostart", {})
    if not isinstance(autostart_raw, dict):
        autostart_raw = {}
    store_raw = raw.get("storage", {})
    logging_raw = raw.get("logging", {})

    embedding_raw = llm_raw.get("embedding", {})
    llm = LLMConfig(
        default_provider=llm_raw.get("default_provider", "deepseek"),
        concurrency=_normalize_llm_concurrency(llm_raw.get("concurrency")),
        timeout=_normalize_llm_timeout(llm_raw.get("timeout")),
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
                    "output_dimensionality",
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
    twitter_raw = sources_raw.get("twitter", {})
    sources = SourcesConfig(
        browser_cdp_url=sources_browser_raw.get("cdp_url", ""),
        browser_headed=sources_browser_raw.get("headed", False),
        bilibili=BilibiliSourceConfig(
            enabled=bool(bilibili_source_raw.get("enabled", True)),
        ),
        xiaohongshu=XiaohongshuSourceConfig(
            enabled=bool(xhs_raw.get("enabled", False)),
            daily_search_budget=int(xhs_raw.get("daily_search_budget", 0)),
            daily_creator_budget=int(xhs_raw.get("daily_creator_budget", 0)),
            task_interval_seconds=int(xhs_raw.get("task_interval_seconds", 45)),
        ),
        douyin=DouyinSourceConfig(
            enabled=bool(douyin_raw.get("enabled", False)),
            mode=str(douyin_raw.get("mode", "direct")),
            cookie_env=str(douyin_raw.get("cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")),
            daily_search_budget=int(douyin_raw.get("daily_search_budget", 0)),
            daily_hot_budget=int(douyin_raw.get("daily_hot_budget", 0)),
            daily_feed_budget=int(douyin_raw.get("daily_feed_budget", 0)),
            request_interval_seconds=int(douyin_raw.get("request_interval_seconds", 2)),
        ),
        youtube=YoutubeSourceConfig(
            enabled=bool(youtube_raw.get("enabled", False)),
            daily_search_budget=int(youtube_raw.get("daily_search_budget", 0)),
            daily_trending_budget=int(youtube_raw.get("daily_trending_budget", 0)),
            daily_channel_budget=int(youtube_raw.get("daily_channel_budget", 0)),
            request_interval_seconds=int(youtube_raw.get("request_interval_seconds", 2)),
            min_interval_minutes=max(0, int(youtube_raw.get("min_interval_minutes", 60))),
        ),
        twitter=TwitterSourceConfig(
            enabled=bool(twitter_raw.get("enabled", False)),
            mode=str(twitter_raw.get("mode", "cookie")),
            cookie_env=str(twitter_raw.get("cookie_env", "OPENBILICLAW_X_COOKIE")),
            daily_search_budget=int(twitter_raw.get("daily_search_budget", 0)),
            daily_feed_budget=int(twitter_raw.get("daily_feed_budget", 0)),
            daily_creator_budget=int(twitter_raw.get("daily_creator_budget", 0)),
            request_interval_seconds=int(twitter_raw.get("request_interval_seconds", 3)),
            min_interval_minutes=max(0, int(twitter_raw.get("min_interval_minutes", 60))),
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
                "auto_update_allowed_remotes": _normalize_auto_update_allowed_remotes(
                    sched_raw.get("auto_update_allowed_remotes")
                ),
            }
        ),
        autostart=AutostartConfig(
            enabled=_coerce_bool(autostart_raw.get("enabled"), default=False),
            manage_ollama=_coerce_bool(autostart_raw.get("manage_ollama"), default=True),
        ),
        storage=StorageConfig(**store_raw),
        logging=LoggingConfig(**logging_raw),
        soul=soul,
    )


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

    if normalized < _MIN_LLM_TIMEOUT:
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


def _normalize_auto_update_allowed_remotes(value: object) -> list[str]:
    """Normalize auto-update remote allowlist into non-empty string URLs."""
    if not isinstance(value, list):
        return list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)
    remotes = [str(item).strip() for item in value if str(item).strip()]
    return remotes or list(_DEFAULT_AUTO_UPDATE_ALLOWED_REMOTES)


def _collect_config_issues(config: Config) -> list[ConfigIssue]:
    """Collect non-fatal config issues to display as guidance."""
    issues: list[ConfigIssue] = []

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
    """Persist a Config dataclass to TOML."""
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
    path.write_text(
        _render_config_toml(
            config,
            on_disk_auth=on_disk_auth,
            on_disk_autostart=on_disk_autostart,
            autostart_authoritative=autostart_authoritative,
            consult_local=consult_local,
        ),
        encoding="utf-8",
    )
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
        "[llm]",
        f"default_provider = {_toml_string(config.llm.default_provider)}",
        f"concurrency = {_normalize_llm_concurrency(config.llm.concurrency)}",
        f"timeout = {_normalize_llm_timeout(config.llm.timeout)}",
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
            f"output_dimensionality = {max(0, int(config.llm.embedding.output_dimensionality))}",
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
            "avoidance_speculation_interval_minutes = "
            f"{config.scheduler.avoidance_speculation_interval_minutes}",
            f"avoidance_speculation_ttl_days = {config.scheduler.avoidance_speculation_ttl_days}",
            "avoidance_speculation_cooldown_days = "
            f"{config.scheduler.avoidance_speculation_cooldown_days}",
            "avoidance_speculation_confirmation_threshold = "
            f"{config.scheduler.avoidance_speculation_confirmation_threshold}",
            "avoidance_speculation_max_active = "
            f"{config.scheduler.avoidance_speculation_max_active}",
            f"auto_update_enabled = {_toml_bool(config.scheduler.auto_update_enabled)}",
            "auto_update_check_interval_hours = "
            f"{config.scheduler.auto_update_check_interval_hours}",
            "auto_update_allow_prerelease = "
            f"{_toml_bool(config.scheduler.auto_update_allow_prerelease)}",
            "auto_update_allowed_remotes = "
            f"{_toml_str_list(config.scheduler.auto_update_allowed_remotes)}",
            "",
            "[scheduler.pool_source_shares]",
            f"bilibili = {int(config.scheduler.pool_source_shares.get('bilibili', 8))}",
            f"xiaohongshu = {int(config.scheduler.pool_source_shares.get('xiaohongshu', 1))}",
            f"douyin = {int(config.scheduler.pool_source_shares.get('douyin', 1))}",
            f"youtube = {int(config.scheduler.pool_source_shares.get('youtube', 1))}",
            f"twitter = {int(config.scheduler.pool_source_shares.get('twitter', 1))}",
            "",
            *_autostart_lines(
                config,
                on_disk_autostart,
                autostart_authoritative=autostart_authoritative,
            ),
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


def _toml_str_list(values: list[str]) -> str:
    """Render a TOML array of strings."""
    return "[" + ", ".join(_toml_string(item) for item in values) + "]"


def validate_runtime_config(config: Config) -> None:
    """Raise ConfigError when runtime-critical config is invalid."""
    issues = _collect_config_issues(config)
    if issues:
        issue = issues[0]
        raise ConfigError(f"{issue.field}: {issue.message}")
