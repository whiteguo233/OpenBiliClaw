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
_SUPPORTED_AUTH_METHODS = {"cookie", "qrcode", "none"}
_REMOTE_PROVIDER_FIELDS = {
    "openai": "llm.openai.api_key",
    "claude": "llm.claude.api_key",
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
class LLMConfig:
    """LLM configuration."""

    default_provider: str = "openai"
    openai: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    claude: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    deepseek: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    ollama: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    openrouter: LLMProviderConfig = field(default_factory=LLMProviderConfig)


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
            path = _PROJECT_ROOT / path
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
            p = _PROJECT_ROOT / p
        return p


def _default_config_path() -> Path:
    """Return the default config.toml path."""
    return _PROJECT_ROOT / "config.toml"


def _config_example_path() -> Path:
    """Return the repository config example path."""
    return _PROJECT_ROOT / "config.example.toml"


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

    llm = LLMConfig(
        default_provider=llm_raw.get("default_provider", "openai"),
        openai=LLMProviderConfig(**llm_raw.get("openai", {})),
        claude=LLMProviderConfig(**llm_raw.get("claude", {})),
        deepseek=LLMProviderConfig(**llm_raw.get("deepseek", {})),
        ollama=LLMProviderConfig(**llm_raw.get("ollama", {})),
        openrouter=LLMProviderConfig(**llm_raw.get("openrouter", {})),
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
    if required_field and not provider_config.api_key.strip():
        issues.append(
            ConfigIssue(
                field=required_field,
                message=(
                    f"默认 provider `{provider_name}` 缺少 `api_key`，"
                    "请在 config.toml 中填写。"
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
            path = _PROJECT_ROOT / filename
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


def validate_runtime_config(config: Config) -> None:
    """Raise ConfigError when runtime-critical config is invalid."""
    issues = _collect_config_issues(config)
    if issues:
        issue = issues[0]
        raise ConfigError(f"{issue.field}: {issue.message}")
