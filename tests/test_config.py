"""Tests for configuration management."""

from pathlib import Path

import pytest

from openbiliclaw import config as config_module
from openbiliclaw.config import (
    ApiConfig,
    BilibiliConfig,
    Config,
    ConfigError,
    ConfigIssue,
    LLMConfig,
    LLMProviderConfig,
    SchedulerConfig,
    SoulConfig,
    SoulPreferenceConfig,
    _build_config,
    load_config,
    load_config_with_diagnostics,
    save_config,
    validate_runtime_config,
)


def _write_example_config(project_root: Path) -> None:
    (project_root / "config.example.toml").write_text(
        """
[general]
language = "zh"
data_dir = "data"

[llm]
default_provider = "openai"

[llm.openai]
api_key = ""
model = "gpt-4o"
base_url = ""

[llm.claude]
api_key = ""
model = "claude-sonnet-4-20250514"

[llm.deepseek]
api_key = ""
model = "deepseek-chat"
base_url = "https://api.deepseek.com"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434"

[bilibili]
auth_method = "cookie"
cookie = ""

[bilibili.browser]
executable = ""
headed = false

[scheduler]
enabled = true
discovery_cron = "0 */4 * * *"
account_sync_interval_hours = 6

[storage]
db_path = "data/openbiliclaw.db"
""".strip(),
        encoding="utf-8",
    )


class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_config(self) -> None:
        config = Config()
        assert config.language == "zh"
        assert isinstance(config.api, ApiConfig)
        assert config.api.host == "0.0.0.0"
        assert config.api.port == 8420
        assert config.llm.default_provider == "openai"
        assert config.llm.concurrency == 3
        assert config.bilibili.auth_method == "cookie"
        assert config.scheduler.enabled is True
        assert config.scheduler.discovery_cron == "0 */8 * * *"
        assert config.scheduler.pool_target_count == 300

    def test_config_defaults_pool_target_count_to_300(self) -> None:
        config = Config()

        assert config.scheduler.pool_target_count == 300

    def test_scheduler_pool_source_shares_defaults(self) -> None:
        config = Config()

        assert config.scheduler.pool_source_shares == {
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        }

    def test_bilibili_source_enabled_defaults_true(self) -> None:
        config = Config()

        assert config.sources.bilibili.enabled is True

    def test_scheduler_pause_on_extension_disconnect_defaults(self) -> None:
        config = Config()

        assert config.scheduler.pause_on_extension_disconnect is False
        assert config.scheduler.extension_disconnect_grace_seconds == 90

    def test_scheduler_runtime_field_defaults(self) -> None:
        config = Config()

        assert config.scheduler.refresh_check_interval_seconds == 60
        assert config.scheduler.signal_event_threshold == 6
        assert config.scheduler.trending_refresh_hours == 3
        assert config.scheduler.explore_refresh_hours == 12
        assert config.scheduler.discovery_limit == 30
        assert config.scheduler.proactive_push_interval_seconds == 120
        assert config.scheduler.speculator_idle_interval_minutes == 30

    def test_build_from_empty_dict(self) -> None:
        config = _build_config({})
        assert config.language == "zh"
        assert config.llm.default_provider == "openai"

    def test_build_from_partial_dict(self) -> None:
        raw = {
            "general": {"language": "en"},
            "api": {"host": "127.0.0.1", "port": 19090},
            "llm": {"default_provider": "claude"},
        }
        config = _build_config(raw)
        assert config.language == "en"
        assert config.api.host == "127.0.0.1"
        assert config.api.port == 19090
        assert config.llm.default_provider == "claude"
        # Other defaults should remain
        assert config.bilibili.auth_method == "cookie"

    def test_api_config_round_trips_through_toml(self, tmp_path: Path) -> None:
        config = Config()
        config.api.host = "127.0.0.1"
        config.api.port = 19090

        target = tmp_path / "config.toml"
        save_config(config, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert "[api]" in rendered
        assert 'host = "127.0.0.1"' in rendered
        assert "port = 19090" in rendered
        assert loaded.api.host == "127.0.0.1"
        assert loaded.api.port == 19090

    def test_data_path_relative(self) -> None:
        config = Config(data_dir="data")
        # Should resolve to an absolute path
        assert config.data_path.is_absolute()

    def test_data_path_absolute(self) -> None:
        config = Config(data_dir="/tmp/openbiliclaw_test")
        assert config.data_path == Path("/tmp/openbiliclaw_test")

    def test_soul_preference_satisfaction_filter_defaults_on(self) -> None:
        """v0.3.x event-satisfaction: default drops quick-exit rows while
        keeping explicit dislike evidence for disliked_topics."""
        config = Config()
        assert isinstance(config.soul, SoulConfig)
        assert isinstance(config.soul.preference, SoulPreferenceConfig)
        assert config.soul.preference.satisfaction_filter_enabled is True

    def test_soul_preference_satisfaction_filter_round_trips_false(self, tmp_path: Path) -> None:
        """save_config → load_config preserves an explicit opt-out."""
        cfg = Config()
        cfg.soul.preference.satisfaction_filter_enabled = False
        target = tmp_path / "config.toml"
        save_config(cfg, target)
        loaded = load_config(target)
        assert loaded.soul.preference.satisfaction_filter_enabled is False

    def test_soul_preference_satisfaction_filter_built_from_toml(self) -> None:
        raw = {"soul": {"preference": {"satisfaction_filter_enabled": True}}}
        config = _build_config(raw)
        assert config.soul.preference.satisfaction_filter_enabled is True

    def test_soul_preference_section_appears_in_rendered_toml(self) -> None:
        """The default config should round-trip through render with a
        documented `[soul.preference]` section so existing installs see
        the new toggle on the next save."""
        from openbiliclaw.config import _render_config_toml

        rendered = _render_config_toml(Config())
        assert "[soul.preference]" in rendered
        assert "satisfaction_filter_enabled = true" in rendered

    def test_load_config_missing_file(self) -> None:
        """Should return defaults when no config file exists."""
        config = load_config("/nonexistent/path/config.toml")
        assert config.language == "zh"

    def test_build_logging_config(self) -> None:
        raw = {
            "logging": {
                "level": "WARNING",
                "file_level": "DEBUG",
                "directory": "runtime_logs",
                "filename": "app.log",
            }
        }

        config = _build_config(raw)

        assert config.logging.level == "WARNING"
        assert config.logging.file_level == "DEBUG"
        assert config.logging.directory == "runtime_logs"
        assert config.logging.filename == "app.log"

    def test_logging_rotation_defaults(self) -> None:
        config = Config()

        # v0.3.30+: lowered max_file_size_mb default 1024 → 100. Long-running
        # daemon previously accumulated 1 GB before rotating, which is too
        # large per-active-log; 100 MB × 2 backups = 200 MB cap is plenty
        # for 1-2 weeks of INFO traffic.
        assert config.logging.max_file_size_mb == 100
        assert config.logging.backup_count == 1
        # v0.3.30+: aggregate-budget + unmanaged-file cleanup defaults
        assert config.logging.aggregate_budget_mb == 500
        assert config.logging.unmanaged_truncate_mb == 200
        assert config.logging.unmanaged_max_age_days == 30

    def test_build_logging_config_parses_rotation_fields(self) -> None:
        raw = {
            "logging": {
                "max_file_size_mb": 256,
                "backup_count": 3,
            }
        }

        config = _build_config(raw)

        assert config.logging.max_file_size_mb == 256
        assert config.logging.backup_count == 3


def test_load_config_with_diagnostics_creates_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    _write_example_config(tmp_path)

    config, diagnostics = load_config_with_diagnostics()

    assert config.language == "zh"
    assert (tmp_path / "config.toml").exists()
    assert diagnostics.created_default_config is True
    assert diagnostics.config_path == tmp_path / "config.toml"
    assert (
        ConfigIssue(
            field="llm.openai.api_key",
            message="默认 provider `openai` 缺少 `api_key`，请在 config.toml 中填写。",
        )
        in diagnostics.issues
    )


def test_load_config_prefers_current_working_directory_for_default_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", Path("/usr/local/lib/python3.11"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(
        """
[general]
language = "en"
data_dir = "runtime-data"

[llm]
default_provider = "ollama"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434"
""".strip(),
        encoding="utf-8",
    )

    config, diagnostics = load_config_with_diagnostics(ensure_default_file=False)

    assert config.language == "en"
    assert config.data_path == tmp_path / "runtime-data"
    assert diagnostics.config_path == tmp_path / "config.toml"


def test_validate_runtime_config_requires_api_key_for_default_provider() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key=""),
        )
    )

    with pytest.raises(ConfigError, match="llm.openai.api_key"):
        validate_runtime_config(config)


def test_validate_runtime_config_allows_ollama_without_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        )
    )

    validate_runtime_config(config)


def test_build_config_supports_openrouter_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "openrouter",
                "openrouter": {
                    "api_key": "test-key",
                    "model": "openai/gpt-4o-mini",
                    "base_url": "https://openrouter.ai/api/v1",
                    "http_referer": "https://example.com",
                    "x_title": "OpenBiliClaw",
                },
            }
        }
    )

    assert config.llm.default_provider == "openrouter"
    assert config.llm.openrouter.api_key == "test-key"
    assert config.llm.openrouter.model == "openai/gpt-4o-mini"
    assert config.llm.openrouter.base_url == "https://openrouter.ai/api/v1"
    assert config.llm.openrouter.http_referer == "https://example.com"
    assert config.llm.openrouter.x_title == "OpenBiliClaw"


def test_validate_runtime_config_requires_openrouter_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="openrouter",
            openrouter=LLMProviderConfig(api_key="", model="openai/gpt-4o-mini"),
        )
    )

    with pytest.raises(ConfigError, match="llm.openrouter.api_key"):
        validate_runtime_config(config)


def test_build_config_supports_openai_compatible_provider() -> None:
    """v0.3.32+ — generic OpenAI-protocol-compatible provider with its
    own [llm.openai_compatible] block. Distinct from [llm.openai]."""
    config = _build_config(
        {
            "llm": {
                "default_provider": "openai_compatible",
                "openai": {"api_key": "real-openai-key"},
                "openai_compatible": {
                    "api_key": "gsk-groq-test",
                    "model": "llama-3.1-70b-versatile",
                    "base_url": "https://api.groq.com/openai/v1",
                },
            }
        }
    )

    assert config.llm.default_provider == "openai_compatible"
    assert config.llm.openai_compatible.api_key == "gsk-groq-test"
    assert config.llm.openai_compatible.model == "llama-3.1-70b-versatile"
    assert config.llm.openai_compatible.base_url == "https://api.groq.com/openai/v1"
    # The two blocks stay independent — adding openai_compatible does
    # not stomp on [llm.openai].
    assert config.llm.openai.api_key == "real-openai-key"


def test_save_config_round_trips_openai_compatible(tmp_path: Path) -> None:
    """[llm.openai_compatible] must survive a save/load cycle so popup
    edits don't get silently dropped on backend restart."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.openai_compatible.api_key = "gsk-test-key"
    config.llm.openai_compatible.model = "qwen2.5-72b-instruct"
    config.llm.openai_compatible.base_url = "https://api.together.xyz/v1"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.openai_compatible.api_key == "gsk-test-key"
    assert loaded.llm.openai_compatible.model == "qwen2.5-72b-instruct"
    assert loaded.llm.openai_compatible.base_url == "https://api.together.xyz/v1"


def test_build_config_supports_openai_codex_auth_mode() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "openai",
                "openai": {
                    "api_key": "",
                    "model": "gpt-5-nano",
                    "auth_mode": "codex_oauth",
                },
            }
        }
    )

    assert config.llm.openai.auth_mode == "codex_oauth"


def test_save_config_round_trips_openai_auth_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.openai.auth_mode = "codex_oauth"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.openai.auth_mode == "codex_oauth"


def test_collect_issues_allows_codex_oauth_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.config import _collect_config_issues

    token_path = tmp_path / "codex_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_CODEX_AUTH_PATH", str(token_path))
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="", auth_mode="codex_oauth"),
        )
    )

    fields = [issue.field for issue in _collect_config_issues(config)]

    assert "llm.openai.api_key" not in fields
    assert "llm.openai.codex_oauth" not in fields


def test_collect_issues_blocks_codex_oauth_with_custom_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.config import _collect_config_issues

    token_path = tmp_path / "codex_auth.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_CODEX_AUTH_PATH", str(token_path))
    config = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="",
                auth_mode="codex_oauth",
                base_url="https://proxy.example.com/v1",
            ),
        )
    )

    issues = _collect_config_issues(config)

    assert any(issue.field == "llm.openai.base_url" for issue in issues)
    assert any(issue.severity == "blocking" for issue in issues)


def test_collect_issues_flags_missing_base_url_for_openai_compatible() -> None:
    """openai_compatible without a base_url is meaningless — it would
    just hit api.openai.com with the wrong key. Surface a config issue
    so the user fixes it before the daemon starts."""
    from openbiliclaw.config import _collect_config_issues

    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai_compatible=LLMProviderConfig(
                api_key="gsk-test-key",
                model="llama-3.1-70b-versatile",
                base_url="",  # ← missing
            ),
        )
    )

    issues = _collect_config_issues(config)
    fields = [i.field for i in issues]
    assert "llm.openai_compatible.base_url" in fields


def test_build_config_supports_gemini_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "gemini",
                "gemini": {
                    "api_key": "test-key",
                    "model": "gemini-2.5-flash",
                },
            }
        }
    )

    assert config.llm.default_provider == "gemini"
    assert config.llm.gemini.api_key == "test-key"
    assert config.llm.gemini.model == "gemini-2.5-flash"


def test_validate_runtime_config_allows_gemini_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    validate_runtime_config(config)


def test_validate_runtime_config_requires_gemini_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="gemini",
            gemini=LLMProviderConfig(api_key="", model="gemini-2.5-flash"),
        )
    )

    with pytest.raises(ConfigError, match="llm.gemini.api_key"):
        validate_runtime_config(config)


def test_validate_runtime_config_rejects_invalid_auth_method() -> None:
    config = Config(bilibili=BilibiliConfig(auth_method="invalid"))

    with pytest.raises(ConfigError, match="bilibili.auth_method"):
        validate_runtime_config(config)


def test_validate_runtime_config_rejects_pool_target_count_above_cap() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        ),
        scheduler=SchedulerConfig(
            enabled=True,
            discovery_cron="0 */4 * * *",
            pool_target_count=601,
            account_sync_interval_hours=6,
        ),
    )

    with pytest.raises(ConfigError, match="scheduler.pool_target_count"):
        validate_runtime_config(config)


def test_build_config_supports_account_sync_interval() -> None:
    config = _build_config(
        {
            "scheduler": {
                "enabled": True,
                "discovery_cron": "0 */4 * * *",
                "pool_target_count": 30,
                "account_sync_interval_hours": 12,
            }
        }
    )

    assert config.scheduler.account_sync_interval_hours == 12


def test_load_config_reads_scheduler_pause_on_extension_disconnect(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[scheduler]
pause_on_extension_disconnect = true
extension_disconnect_grace_seconds = 123
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.pause_on_extension_disconnect is True
    assert config.scheduler.extension_disconnect_grace_seconds == 123


def test_load_config_defaults_scheduler_pause_on_extension_disconnect_when_absent(
    tmp_path: Path,
) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[scheduler]
enabled = true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.pause_on_extension_disconnect is False
    assert config.scheduler.extension_disconnect_grace_seconds == 90


@pytest.mark.parametrize("raw_grace", [-1, 0, "abc"])
def test_load_config_defaults_invalid_scheduler_disconnect_grace(
    tmp_path: Path,
    raw_grace: object,
) -> None:
    toml_path = tmp_path / "c.toml"
    grace_literal = f'"{raw_grace}"' if isinstance(raw_grace, str) else str(raw_grace)
    toml_path.write_text(
        f"""
[scheduler]
extension_disconnect_grace_seconds = {grace_literal}
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.extension_disconnect_grace_seconds == 90


def test_save_config_round_trips_scheduler_pause_on_extension_disconnect(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.pause_on_extension_disconnect = True
    config.scheduler.extension_disconnect_grace_seconds = 45

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.pause_on_extension_disconnect is True
    assert loaded.scheduler.extension_disconnect_grace_seconds == 45


def test_load_config_reads_scheduler_runtime_fields(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[scheduler]
refresh_check_interval_seconds = 75
signal_event_threshold = 9
trending_refresh_hours = 5
explore_refresh_hours = 18
discovery_limit = 17
proactive_push_interval_seconds = 155
speculator_idle_interval_minutes = 11
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.refresh_check_interval_seconds == 75
    assert config.scheduler.signal_event_threshold == 9
    assert config.scheduler.trending_refresh_hours == 5
    assert config.scheduler.explore_refresh_hours == 18
    assert config.scheduler.discovery_limit == 17
    assert config.scheduler.proactive_push_interval_seconds == 155
    assert config.scheduler.speculator_idle_interval_minutes == 11


@pytest.mark.parametrize(
    ("field", "literal", "expected"),
    [
        ("refresh_check_interval_seconds", "0", 60),
        ("refresh_check_interval_seconds", '"abc"', 60),
        ("signal_event_threshold", "-1", 6),
        ("trending_refresh_hours", "0", 3),
        ("explore_refresh_hours", "0", 12),
        ("discovery_limit", "0", 30),
        ("discovery_limit", "61", 30),
        ("proactive_push_interval_seconds", "29", 120),
        ("speculator_idle_interval_minutes", "4", 30),
    ],
)
def test_load_config_defaults_invalid_scheduler_runtime_fields(
    tmp_path: Path,
    field: str,
    literal: str,
    expected: int,
) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        f"""
[scheduler]
{field} = {literal}
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert getattr(config.scheduler, field) == expected


def test_save_config_round_trips_scheduler_runtime_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.refresh_check_interval_seconds = 75
    config.scheduler.signal_event_threshold = 9
    config.scheduler.trending_refresh_hours = 5
    config.scheduler.explore_refresh_hours = 18
    config.scheduler.discovery_limit = 17
    config.scheduler.proactive_push_interval_seconds = 155
    config.scheduler.speculator_idle_interval_minutes = 11

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.refresh_check_interval_seconds == 75
    assert loaded.scheduler.signal_event_threshold == 9
    assert loaded.scheduler.trending_refresh_hours == 5
    assert loaded.scheduler.explore_refresh_hours == 18
    assert loaded.scheduler.discovery_limit == 17
    assert loaded.scheduler.proactive_push_interval_seconds == 155
    assert loaded.scheduler.speculator_idle_interval_minutes == 11


def test_scheduler_pool_source_shares_override(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[scheduler.pool_source_shares]
bilibili = 7
xiaohongshu = 2
douyin = 1
youtube = 3
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.pool_source_shares == {
        "bilibili": 7,
        "xiaohongshu": 2,
        "douyin": 1,
        "youtube": 3,
    }


def test_build_config_supports_sources_browser_cdp_url() -> None:
    config = _build_config(
        {
            "sources": {
                "browser": {
                    "cdp_url": "http://127.0.0.1:9222",
                    "headed": True,
                }
            }
        }
    )

    assert config.sources.browser_cdp_url == "http://127.0.0.1:9222"
    assert config.sources.browser_headed is True


def test_sources_browser_defaults_are_empty() -> None:
    config = _build_config({})

    assert config.sources.browser_cdp_url == ""
    assert config.sources.browser_headed is False


def test_sources_xiaohongshu_defaults() -> None:
    config = _build_config({})

    assert config.sources.xiaohongshu.enabled is False
    assert config.sources.xiaohongshu.daily_search_budget == 30
    assert config.sources.xiaohongshu.daily_creator_budget == 10
    assert config.sources.xiaohongshu.task_interval_seconds == 45


def test_sources_douyin_defaults() -> None:
    config = _build_config({})

    assert config.sources.douyin.enabled is False
    assert config.sources.douyin.mode == "direct"
    assert config.sources.douyin.cookie_env == "OPENBILICLAW_DOUYIN_COOKIE"
    assert config.sources.douyin.daily_search_budget == 30
    assert config.sources.douyin.daily_hot_budget == 5
    assert config.sources.douyin.daily_feed_budget == 30
    assert config.sources.douyin.request_interval_seconds == 2


def test_sources_youtube_defaults() -> None:
    config = _build_config({})

    assert config.sources.youtube.enabled is False
    assert config.sources.youtube.daily_search_budget == 6
    assert config.sources.youtube.daily_trending_budget == 50
    assert config.sources.youtube.daily_channel_budget == 10
    assert config.sources.youtube.request_interval_seconds == 2
    assert config.sources.youtube.min_interval_minutes == 60


def test_build_config_supports_sources_xiaohongshu(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.xiaohongshu]
enabled = false
daily_search_budget = 30
daily_creator_budget = 5
task_interval_seconds = 60
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.xiaohongshu.enabled is False
    assert config.sources.xiaohongshu.daily_search_budget == 30
    assert config.sources.xiaohongshu.daily_creator_budget == 5
    assert config.sources.xiaohongshu.task_interval_seconds == 60


def test_build_config_supports_sources_douyin(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.douyin]
enabled = true
mode = "direct"
cookie_env = "CUSTOM_DY_COOKIE"
daily_search_budget = 12
daily_hot_budget = 3
daily_feed_budget = 7
request_interval_seconds = 4
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.douyin.enabled is True
    assert config.sources.douyin.mode == "direct"
    assert config.sources.douyin.cookie_env == "CUSTOM_DY_COOKIE"
    assert config.sources.douyin.daily_search_budget == 12
    assert config.sources.douyin.daily_hot_budget == 3
    assert config.sources.douyin.daily_feed_budget == 7
    assert config.sources.douyin.request_interval_seconds == 4


def test_build_config_supports_sources_youtube(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.youtube]
enabled = true
daily_search_budget = 4
daily_trending_budget = 40
daily_channel_budget = 7
request_interval_seconds = 3
min_interval_minutes = 45
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.youtube.enabled is True
    assert config.sources.youtube.daily_search_budget == 4
    assert config.sources.youtube.daily_trending_budget == 40
    assert config.sources.youtube.daily_channel_budget == 7
    assert config.sources.youtube.request_interval_seconds == 3
    assert config.sources.youtube.min_interval_minutes == 45


def test_save_config_round_trips_sources_youtube(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.youtube.enabled = True
    config.sources.youtube.daily_search_budget = 5
    config.sources.youtube.daily_trending_budget = 42
    config.sources.youtube.daily_channel_budget = 8
    config.sources.youtube.request_interval_seconds = 4
    config.sources.youtube.min_interval_minutes = 30

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.sources.youtube.enabled is True
    assert loaded.sources.youtube.daily_search_budget == 5
    assert loaded.sources.youtube.daily_trending_budget == 42
    assert loaded.sources.youtube.daily_channel_budget == 8
    assert loaded.sources.youtube.request_interval_seconds == 4
    assert loaded.sources.youtube.min_interval_minutes == 30


def test_save_config_round_trips_sources_browser_cdp_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.browser_cdp_url = "http://127.0.0.1:9222"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.sources.browser_cdp_url == "http://127.0.0.1:9222"


def test_save_config_round_trips_bilibili_source_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.bilibili.enabled = False

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.sources.bilibili.enabled is False


def test_save_config_round_trips_pool_source_shares(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 2,
        "youtube": 1,
    }

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.pool_source_shares == {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 2,
        "youtube": 1,
    }


def test_save_config_round_trips_advanced_scheduler_and_logging_fields(
    tmp_path: Path,
) -> None:
    """Popup/API saves must not drop advanced fields that the UI may not edit."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.speculation_interval_minutes = 22
    config.scheduler.speculation_ttl_days = 8
    config.scheduler.speculation_cooldown_days = 9
    config.scheduler.speculation_confirmation_threshold = 4
    config.scheduler.speculation_max_active = 6
    config.scheduler.speculation_max_primary_interests = 17
    config.scheduler.speculation_max_secondary_interests = 66
    config.scheduler.auto_update_enabled = True
    config.scheduler.auto_update_check_interval_hours = 12
    config.logging.aggregate_budget_mb = 444
    config.logging.unmanaged_truncate_mb = 55
    config.logging.unmanaged_max_age_days = 6

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.speculation_interval_minutes == 22
    assert loaded.scheduler.speculation_ttl_days == 8
    assert loaded.scheduler.speculation_cooldown_days == 9
    assert loaded.scheduler.speculation_confirmation_threshold == 4
    assert loaded.scheduler.speculation_max_active == 6
    assert loaded.scheduler.speculation_max_primary_interests == 17
    assert loaded.scheduler.speculation_max_secondary_interests == 66
    assert loaded.scheduler.auto_update_enabled is True
    assert loaded.scheduler.auto_update_check_interval_hours == 12
    assert loaded.logging.aggregate_budget_mb == 444
    assert loaded.logging.unmanaged_truncate_mb == 55
    assert loaded.logging.unmanaged_max_age_days == 6


def test_save_config_round_trips_runtime_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.language = "en"
    config.data_dir = "runtime-data"
    config.llm.default_provider = "gemini"
    config.llm.concurrency = 6
    config.llm.fallback_enabled = True
    config.llm.fallback_provider = "openai"
    config.llm.gemini.api_key = "gemini-test-key"
    config.llm.gemini.model = "gemini-2.5-flash"
    config.llm.embedding.fallback_enabled = True
    config.llm.embedding.fallback_provider = "ollama"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.language == "en"
    assert loaded.data_dir == "runtime-data"
    assert loaded.llm.default_provider == "gemini"
    assert loaded.llm.concurrency == 6
    assert loaded.llm.fallback_enabled is True
    assert loaded.llm.fallback_provider == "openai"
    assert loaded.llm.gemini.api_key == "gemini-test-key"
    assert loaded.llm.gemini.model == "gemini-2.5-flash"
    assert loaded.llm.embedding.fallback_enabled is True
    assert loaded.llm.embedding.fallback_provider == "ollama"


def test_llm_and_embedding_fallback_defaults_are_disabled() -> None:
    config = Config()

    assert config.llm.fallback_enabled is False
    assert config.llm.fallback_provider == ""
    assert config.llm.embedding.fallback_enabled is False
    assert config.llm.embedding.fallback_provider == ""


def test_save_config_round_trips_embedding_credentials(tmp_path: Path) -> None:
    """v0.3.32+ EmbeddingConfig owns api_key/base_url. They must survive
    a save/load round-trip — otherwise the popup's PUT /api/config would
    silently lose the user's dedicated embedding credentials on restart."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.embedding.provider = "openai"
    config.llm.embedding.model = "text-embedding-3-small"
    config.llm.embedding.api_key = "sk-dedicated-embedding-xyz"
    config.llm.embedding.base_url = "https://embed.example.com/v1"
    config.llm.embedding.similarity_threshold = 0.91
    config.llm.embedding.fallback_enabled = True

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.embedding.provider == "openai"
    assert loaded.llm.embedding.model == "text-embedding-3-small"
    assert loaded.llm.embedding.api_key == "sk-dedicated-embedding-xyz"
    assert loaded.llm.embedding.base_url == "https://embed.example.com/v1"
    assert loaded.llm.embedding.similarity_threshold == 0.91
    assert loaded.llm.embedding.fallback_enabled is True


def test_load_config_accepts_legacy_embedding_section_without_api_key(
    tmp_path: Path,
) -> None:
    """Pre-v0.3.32 configs only have provider/model/similarity_threshold
    in [llm.embedding]. Loading must still succeed and the new fields
    default to empty strings (which triggers the back-compat fallback in
    build_embedding_service)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[llm]
default_provider = "ollama"

[llm.ollama]
model = "llama3"
base_url = "http://localhost:11434/v1"

[llm.embedding]
provider = "ollama"
model = ""
similarity_threshold = 0.88
""".strip()
    )

    loaded = load_config(config_path)

    assert loaded.llm.embedding.provider == "ollama"
    assert loaded.llm.embedding.api_key == ""
    assert loaded.llm.embedding.base_url == ""
    assert loaded.llm.embedding.similarity_threshold == 0.88
