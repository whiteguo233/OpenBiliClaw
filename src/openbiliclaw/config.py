"""Configuration management for OpenBiliClaw.

Loads configuration from TOML files with environment variable overrides.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default config search paths
_CONFIG_FILENAMES = ["config.toml", "config.local.toml"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT_ENV = "OPENBILICLAW_PROJECT_ROOT"
_SUPPORTED_AUTH_METHODS = {"cookie", "qrcode", "none"}
_MIN_POOL_TARGET_COUNT = 1
_MAX_POOL_TARGET_COUNT = 300
_REMOTE_PROVIDER_FIELDS = {
    "openai": "llm.openai.api_key",
    "claude": "llm.claude.api_key",
    "gemini": "llm.gemini.api_key",
    "deepseek": "llm.deepseek.api_key",
    "openrouter": "llm.openrouter.api_key",
}


class ConfigError(ValueError):
    """Raised when required runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class ConfigIssue:
    """A user-facing configuration problem."""

    field: str
    message: str


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
    http_referer: str = ""
    x_title: str = ""


@dataclass
class EmbeddingConfig:
    """Embedding model configuration."""

    provider: str = ""  # Empty = use LLM default_provider
    model: str = "gemini-embedding-001"
    similarity_threshold: float = 0.82


@dataclass
class ModuleLLMConfig:
    """Per-module LLM override. Empty strings = use global defaults."""

    provider: str = ""
    model: str = ""


@dataclass
class LLMConfig:
    """LLM configuration with global defaults and per-module overrides."""

    default_provider: str = "openai"
    openai: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    claude: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    gemini: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    deepseek: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    ollama: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    openrouter: LLMProviderConfig = field(default_factory=LLMProviderConfig)
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
    discovery_cron: str = "0 */4 * * *"
    pool_target_count: int = 300
    account_sync_interval_hours: int = 6
    speculation_interval_minutes: int = 10
    speculation_ttl_days: int = 3
    speculation_cooldown_days: int = 7
    speculation_confirmation_threshold: int = 3
    speculation_max_active: int = 5
    speculation_max_primary_interests: int = 15
    speculation_max_secondary_interests: int = 60
    auto_update_enabled: bool = True
    auto_update_check_interval_hours: int = 6


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
class Config:
    """Root configuration for OpenBiliClaw."""

    language: str = "zh"
    data_dir: str = "data"
    llm: LLMConfig = field(default_factory=LLMConfig)
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

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
    diagnostics.messages.append(
        f"未检测到 config.toml，已自动生成模板文件：{config_path}。"
    )


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
    llm_raw = raw.get("llm", {})
    bili_raw = raw.get("bilibili", {})
    sched_raw = raw.get("scheduler", {})
    store_raw = raw.get("storage", {})
    logging_raw = raw.get("logging", {})

    embedding_raw = llm_raw.get("embedding", {})
    llm = LLMConfig(
        default_provider=llm_raw.get("default_provider", "openai"),
        openai=LLMProviderConfig(**llm_raw.get("openai", {})),
        claude=LLMProviderConfig(**llm_raw.get("claude", {})),
        gemini=LLMProviderConfig(**llm_raw.get("gemini", {})),
        deepseek=LLMProviderConfig(**llm_raw.get("deepseek", {})),
        ollama=LLMProviderConfig(**llm_raw.get("ollama", {})),
        openrouter=LLMProviderConfig(**llm_raw.get("openrouter", {})),
        embedding=EmbeddingConfig(**{
            k: v for k, v in embedding_raw.items()
            if k in ("provider", "model", "similarity_threshold")
        }),
        soul=ModuleLLMConfig(**{
            k: v for k, v in llm_raw.get("soul", {}).items()
            if k in ("provider", "model")
        }),
        discovery=ModuleLLMConfig(**{
            k: v for k, v in llm_raw.get("discovery", {}).items()
            if k in ("provider", "model")
        }),
        recommendation=ModuleLLMConfig(**{
            k: v for k, v in llm_raw.get("recommendation", {}).items()
            if k in ("provider", "model")
        }),
        evaluation=ModuleLLMConfig(**{
            k: v for k, v in llm_raw.get("evaluation", {}).items()
            if k in ("provider", "model")
        }),
    )

    browser_raw = bili_raw.pop("browser", {})
    bilibili = BilibiliConfig(
        auth_method=bili_raw.get("auth_method", "cookie"),
        cookie=bili_raw.get("cookie", ""),
        browser_executable=browser_raw.get("executable", ""),
        browser_headed=browser_raw.get("headed", False),
    )

    return Config(
        language=general.get("language", "zh"),
        data_dir=general.get("data_dir", "data"),
        llm=llm,
        bilibili=bilibili,
        scheduler=SchedulerConfig(**sched_raw),
        storage=StorageConfig(**store_raw),
        logging=LoggingConfig(**logging_raw),
    )


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

    required_field = _REMOTE_PROVIDER_FIELDS.get(provider_name)
    has_env_fallback = provider_name == "gemini" and bool(_gemini_api_key_from_env())
    if required_field and not provider_config.api_key.strip() and not has_env_fallback:
        issues.append(
            ConfigIssue(
                field=required_field,
                message=(
                    f"默认 provider `{provider_name}` 缺少 `api_key`，"
                    "请在 config.toml 中填写。"
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
        f'language = {_toml_string(config.language)}',
        f'data_dir = {_toml_string(config.data_dir)}',
        "",
        "[llm]",
        f'default_provider = {_toml_string(config.llm.default_provider)}',
        "",
    ]
    lines.extend(_render_provider_section("openai", config.llm.openai))
    lines.extend(_render_provider_section("claude", config.llm.claude))
    lines.extend(_render_provider_section("gemini", config.llm.gemini))
    lines.extend(_render_provider_section("deepseek", config.llm.deepseek))
    lines.extend(_render_provider_section("ollama", config.llm.ollama))
    lines.extend(_render_provider_section("openrouter", config.llm.openrouter))
    lines.extend([
        "[llm.embedding]",
        f'provider = {_toml_string(config.llm.embedding.provider)}',
        f'model = {_toml_string(config.llm.embedding.model)}',
        f"similarity_threshold = {config.llm.embedding.similarity_threshold}",
        "",
        "# Per-module LLM overrides (empty = use global default)",
        "[llm.soul]",
        f'provider = {_toml_string(config.llm.soul.provider)}',
        f'model = {_toml_string(config.llm.soul.model)}',
        "",
        "[llm.discovery]",
        f'provider = {_toml_string(config.llm.discovery.provider)}',
        f'model = {_toml_string(config.llm.discovery.model)}',
        "",
        "[llm.recommendation]",
        f'provider = {_toml_string(config.llm.recommendation.provider)}',
        f'model = {_toml_string(config.llm.recommendation.model)}',
        "",
        "[llm.evaluation]",
        f'provider = {_toml_string(config.llm.evaluation.provider)}',
        f'model = {_toml_string(config.llm.evaluation.model)}',
        "",
    ])
    lines.extend(
        [
            "[bilibili]",
            f'auth_method = {_toml_string(config.bilibili.auth_method)}',
            f'cookie = {_toml_string(config.bilibili.cookie)}',
            "",
            "[bilibili.browser]",
            f'executable = {_toml_string(config.bilibili.browser_executable)}',
            f"headed = {_toml_bool(config.bilibili.browser_headed)}",
            "",
            "[scheduler]",
            f"enabled = {_toml_bool(config.scheduler.enabled)}",
            f'discovery_cron = {_toml_string(config.scheduler.discovery_cron)}',
            f"pool_target_count = {config.scheduler.pool_target_count}",
            f"account_sync_interval_hours = {config.scheduler.account_sync_interval_hours}",
            "",
            "[storage]",
            f'db_path = {_toml_string(config.storage.db_path)}',
            "",
            "[logging]",
            f'level = {_toml_string(config.logging.level)}',
            f'file_level = {_toml_string(config.logging.file_level)}',
            f'directory = {_toml_string(config.logging.directory)}',
            f'filename = {_toml_string(config.logging.filename)}',
            "",
        ]
    )
    return "\n".join(lines)


def _render_provider_section(name: str, provider: LLMProviderConfig) -> list[str]:
    """Render one provider subsection."""
    lines = [f"[llm.{name}]"]
    lines.append(f'api_key = {_toml_string(provider.api_key)}')
    lines.append(f'model = {_toml_string(provider.model)}')
    if name in {"openai", "deepseek", "ollama", "openrouter"}:
        lines.append(f'base_url = {_toml_string(provider.base_url)}')
    if name == "openrouter":
        lines.append(f'http_referer = {_toml_string(provider.http_referer)}')
        lines.append(f'x_title = {_toml_string(provider.x_title)}')
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
