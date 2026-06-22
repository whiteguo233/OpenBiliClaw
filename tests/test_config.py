"""Tests for configuration management."""

from pathlib import Path

import pytest

from openbiliclaw import config as config_module
from openbiliclaw.config import (
    ApiConfig,
    AutostartConfig,
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
        assert config.llm.default_provider == "deepseek"
        assert config.llm.concurrency == 3
        assert config.bilibili.auth_method == "cookie"
        assert config.scheduler.enabled is True
        assert config.scheduler.discovery_cron == "0 */8 * * *"
        assert config.scheduler.pool_target_count == 300
        assert isinstance(config.autostart, AutostartConfig)
        assert config.autostart.enabled is False
        assert config.autostart.manage_ollama is True

    def test_config_defaults_pool_target_count_to_300(self) -> None:
        config = Config()

        assert config.scheduler.pool_target_count == 300

    def test_scheduler_pool_source_shares_defaults(self) -> None:
        config = Config()

        assert config.scheduler.pool_source_shares == {
            "bilibili": 5,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
            "twitter": 1,
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
        assert config.llm.default_provider == "deepseek"
        assert config.autostart.enabled is False
        assert config.autostart.manage_ollama is True

    def test_load_config_coerces_autostart_env_bool_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[autostart]
enabled = true
manage_ollama = true
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENBILICLAW_AUTOSTART_ENABLED", "false")

        config = load_config(config_path)

        assert config.autostart.enabled is False
        assert config.autostart.manage_ollama is True

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

    def test_autostart_section_appears_in_rendered_toml(self) -> None:
        from openbiliclaw.config import _render_config_toml

        rendered = _render_config_toml(Config())

        assert "[autostart]" in rendered
        assert "manage_ollama = true" in rendered

    def test_save_config_round_trips_authoritative_autostart(self, tmp_path: Path) -> None:
        cfg = Config()
        cfg.autostart.enabled = True
        cfg.autostart.manage_ollama = False
        target = tmp_path / "config.toml"

        save_config(cfg, target, autostart_authoritative=True)
        loaded = load_config(target)

        assert loaded.autostart.enabled is True
        assert loaded.autostart.manage_ollama is False

    def test_save_config_preserves_on_disk_autostart_enabled_by_default(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "config.toml"
        target.write_text(
            """
[autostart]
enabled = true
manage_ollama = false
""".strip(),
            encoding="utf-8",
        )
        stale_cfg = Config()
        stale_cfg.autostart.enabled = False
        stale_cfg.autostart.manage_ollama = True

        save_config(stale_cfg, target)
        loaded = load_config(target)

        assert loaded.autostart.enabled is True
        assert loaded.autostart.manage_ollama is True

    def test_save_config_authoritative_autostart_enabled_overrides_disk(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "config.toml"
        target.write_text(
            """
[autostart]
enabled = false
manage_ollama = false
""".strip(),
            encoding="utf-8",
        )
        cfg = Config()
        cfg.autostart.enabled = True
        cfg.autostart.manage_ollama = True

        save_config(cfg, target, autostart_authoritative=True)
        loaded = load_config(target)

        assert loaded.autostart.enabled is True
        assert loaded.autostart.manage_ollama is True

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
avoidance_speculation_interval_minutes = 12
avoidance_speculation_ttl_days = 4
avoidance_speculation_cooldown_days = 8
avoidance_speculation_confirmation_threshold = 2
avoidance_speculation_max_active = 5
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
    assert config.scheduler.avoidance_speculation_interval_minutes == 12
    assert config.scheduler.avoidance_speculation_ttl_days == 4
    assert config.scheduler.avoidance_speculation_cooldown_days == 8
    assert config.scheduler.avoidance_speculation_confirmation_threshold == 2
    assert config.scheduler.avoidance_speculation_max_active == 5


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
    config.scheduler.avoidance_speculation_interval_minutes = 12
    config.scheduler.avoidance_speculation_ttl_days = 4
    config.scheduler.avoidance_speculation_cooldown_days = 8
    config.scheduler.avoidance_speculation_confirmation_threshold = 2
    config.scheduler.avoidance_speculation_max_active = 5

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.refresh_check_interval_seconds == 75
    assert loaded.scheduler.signal_event_threshold == 9
    assert loaded.scheduler.trending_refresh_hours == 5
    assert loaded.scheduler.explore_refresh_hours == 18
    assert loaded.scheduler.discovery_limit == 17
    assert loaded.scheduler.proactive_push_interval_seconds == 155
    assert loaded.scheduler.speculator_idle_interval_minutes == 11
    assert loaded.scheduler.avoidance_speculation_interval_minutes == 12
    assert loaded.scheduler.avoidance_speculation_ttl_days == 4
    assert loaded.scheduler.avoidance_speculation_cooldown_days == 8
    assert loaded.scheduler.avoidance_speculation_confirmation_threshold == 2
    assert loaded.scheduler.avoidance_speculation_max_active == 5


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
    assert config.sources.xiaohongshu.daily_search_budget == 0
    assert config.sources.xiaohongshu.daily_creator_budget == 0
    assert config.sources.xiaohongshu.task_interval_seconds == 45


def test_sources_douyin_defaults() -> None:
    config = _build_config({})

    assert config.sources.douyin.enabled is False
    assert config.sources.douyin.mode == "direct"
    assert config.sources.douyin.cookie_env == "OPENBILICLAW_DOUYIN_COOKIE"
    assert config.sources.douyin.daily_search_budget == 0
    assert config.sources.douyin.daily_hot_budget == 0
    assert config.sources.douyin.daily_feed_budget == 0
    assert config.sources.douyin.request_interval_seconds == 2


def test_sources_youtube_defaults() -> None:
    config = _build_config({})

    assert config.sources.youtube.enabled is False
    assert config.sources.youtube.daily_search_budget == 0
    assert config.sources.youtube.daily_trending_budget == 0
    assert config.sources.youtube.daily_channel_budget == 0
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
        "twitter": 3,
    }

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.pool_source_shares == {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 2,
        "youtube": 1,
        "twitter": 3,
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
    config.scheduler.auto_update_allow_prerelease = True
    config.scheduler.auto_update_allowed_remotes = [
        "https://github.com/example/OpenBiliClaw.git",
        "git@github.com:example/OpenBiliClaw.git",
    ]
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
    assert loaded.scheduler.auto_update_allow_prerelease is True
    assert loaded.scheduler.auto_update_allowed_remotes == [
        "https://github.com/example/OpenBiliClaw.git",
        "git@github.com:example/OpenBiliClaw.git",
    ]
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
    assert config.llm.embedding.output_dimensionality == 1024


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
    config.llm.embedding.output_dimensionality = 768
    config.llm.embedding.similarity_threshold = 0.91
    config.llm.embedding.fallback_enabled = True
    config.llm.embedding.fallback_provider = "openai_compatible"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.embedding.provider == "openai"
    assert loaded.llm.embedding.model == "text-embedding-3-small"
    assert loaded.llm.embedding.api_key == "sk-dedicated-embedding-xyz"
    assert loaded.llm.embedding.base_url == "https://embed.example.com/v1"
    assert loaded.llm.embedding.output_dimensionality == 768
    assert loaded.llm.embedding.similarity_threshold == 0.91
    assert loaded.llm.embedding.fallback_enabled is True
    assert loaded.llm.embedding.fallback_provider == "openai_compatible"


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
    assert loaded.llm.embedding.output_dimensionality == 1024
    assert loaded.llm.embedding.similarity_threshold == 0.88


def test_api_auth_env_vars_matches_loader_read_surface() -> None:
    """The env-managed guard list MUST equal what ``_build_api_auth`` reads.

    Drift here is a real security gap: a new ``OPENBILICLAW_API_AUTH_*`` override
    added to config loading but not to ``API_AUTH_ENV_VARS`` would let the local
    admin endpoint / CLI silently write a config the env wins back on restart
    (review r2#2). Scoped to ``_build_api_auth`` so it tracks the loader exactly.
    """
    import inspect
    import re

    from openbiliclaw.config import API_AUTH_ENV_VARS, _build_api_auth

    src = inspect.getsource(_build_api_auth)
    read = set(re.findall(r"OPENBILICLAW_API_AUTH_[A-Z_]+", src))
    assert read == set(API_AUTH_ENV_VARS), (
        f"_build_api_auth reads {read} but API_AUTH_ENV_VARS guards "
        f"{set(API_AUTH_ENV_VARS)} — keep them in lockstep"
    )


def test_save_config_does_not_bake_in_auth_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auth env override must never be persisted into config.toml by an
    unrelated save (review r4#1).

    load_config gives env precedence, so the in-memory Config carries the env
    value; writing it back would leave a stale literal once the env var is
    removed, silently shifting the trust boundary / session lifetime. save_config
    must preserve the operator's on-disk [api.auth] value for env-overridden
    fields instead. Covers the central save path used by startup secret-gen,
    PUT /api/config and cookie sync alike.
    """
    from openbiliclaw.config import Config, load_config, save_config

    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.api.auth.enabled = True
    cfg.api.auth.password_hash = "phash"
    cfg.api.auth.trust_loopback = True  # operator's on-disk choice
    cfg.api.auth.session_ttl_hours = 0
    save_config(cfg, path)

    # env now overrides trust_loopback + ttl; load reflects the env values
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK", "false")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS", "12")
    loaded = load_config(path)
    assert loaded.api.auth.trust_loopback is False  # env wins at load
    assert loaded.api.auth.session_ttl_hours == 12

    # an unrelated change is saved while env-managed
    loaded.llm.openai.api_key = "sk-unrelated"
    save_config(loaded, path)

    # with the env vars gone, the file must still hold the ORIGINAL on-disk values,
    # not the env-derived ones
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK")
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS")
    reloaded = load_config(path)
    assert reloaded.api.auth.trust_loopback is True
    assert reloaded.api.auth.session_ttl_hours == 0
    assert reloaded.llm.openai.api_key == "sk-unrelated"  # unrelated change persisted


def test_save_config_omits_env_auth_field_absent_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an env-overridden auth field has no on-disk value, save omits it
    rather than baking the env value — load then falls back to the safe default."""
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    # hand-written config WITHOUT a trust_loopback line under [api.auth]
    path.write_text('[api.auth]\nenabled = true\npassword_hash = "x"\n', encoding="utf-8")

    monkeypatch.setenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK", "false")
    loaded = load_config(path)
    assert loaded.api.auth.trust_loopback is False  # env wins at load
    save_config(loaded, path)
    assert "trust_loopback" not in path.read_text(encoding="utf-8")  # not baked in
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK")
    # default is True; the env "false" was never written through
    assert load_config(path).api.auth.trust_loopback is True


def test_save_config_preserves_string_boolean_auth_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserving an env-overridden boolean must use loader coercion, not bool().

    A quoted string boolean such as `trust_loopback = "false"` is accepted by the
    loader as False; a naive bool("false") would round-trip it to true and
    silently reopen the loopback bypass once the env var is removed (review r5#1).
    """
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    # operator wrote QUOTED string booleans on disk
    path.write_text(
        '[api.auth]\nenabled = "false"\npassword_hash = "x"\ntrust_loopback = "false"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK", "true")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_ENABLED", "true")
    loaded = load_config(path)
    assert loaded.api.auth.trust_loopback is True  # env wins at load
    assert loaded.api.auth.enabled is True
    save_config(loaded, path)
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK")
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_ENABLED")

    # the on-disk "false" values must be preserved as False, NOT flipped to true
    reloaded = load_config(path)
    assert reloaded.api.auth.trust_loopback is False
    assert reloaded.api.auth.enabled is False


def test_save_config_preserves_malformed_ttl_as_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed on-disk TTL must coerce like the loader (→ 0), not crash save."""
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text(
        '[api.auth]\nenabled = true\npassword_hash = "x"\nsession_ttl_hours = "garbage"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS", "24")
    loaded = load_config(path)
    assert loaded.api.auth.session_ttl_hours == 24  # env wins at load
    save_config(loaded, path)  # must not raise on the garbage on-disk value
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS")
    assert load_config(path).api.auth.session_ttl_hours == 0


def test_save_config_preserves_on_disk_plaintext_password_under_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An env-managed save must not drop a supported on-disk plaintext password.

    _build_api_auth honors a plaintext `password` key (hashing it) and
    get_auth_plain_password treats it as stable fingerprint material. If
    preservation only handled `password_hash`, a file with `password = "..."` and
    no `password_hash` would lose its credential under an env override, locking the
    gate out after the env var is removed (review r6#1).
    """
    from openbiliclaw.auth_core import verify_password
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    # operator wrote a PLAINTEXT password (no password_hash) on disk
    path.write_text('[api.auth]\nenabled = true\npassword = "oldpw"\n', encoding="utf-8")

    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "envpw")
    loaded = load_config(path)
    assert verify_password("envpw", loaded.api.auth.password_hash)  # env wins at load
    save_config(loaded, path)  # unrelated env-managed save
    assert 'password = "oldpw"' in path.read_text(encoding="utf-8")  # credential preserved

    monkeypatch.delenv("OPENBILICLAW_API_AUTH_PASSWORD")
    reloaded = load_config(path)
    assert reloaded.api.auth.enabled is True
    assert reloaded.api.auth.password_hash  # non-empty → no lockout
    assert verify_password("oldpw", reloaded.api.auth.password_hash)  # operator's pw restored


def test_coerce_ttl_hours_handles_toml_special_floats(tmp_path: Path) -> None:
    """Bare TOML nan / inf TTL must coerce to 0, not crash load_config (review r6#2)."""
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    for literal in ("nan", "inf", "-inf"):
        path.write_text(f"[api.auth]\nsession_ttl_hours = {literal}\n", encoding="utf-8")
        assert load_config(path).api.auth.session_ttl_hours == 0, literal


def test_save_config_preserves_nan_ttl_without_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env-managed preservation of an on-disk nan TTL must not raise (review r6#2)."""
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text(
        '[api.auth]\nenabled = true\npassword_hash = "x"\nsession_ttl_hours = nan\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS", "5")
    loaded = load_config(path)
    assert loaded.api.auth.session_ttl_hours == 5  # env wins at load
    save_config(loaded, path)  # must not raise on the nan on-disk value
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS")
    assert load_config(path).api.auth.session_ttl_hours == 0


def test_password_hash_env_governs_credential_without_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPENBILICLAW_API_AUTH_PASSWORD_HASH must be used verbatim, not mangled by
    the generic env splitter into a dict hashed as its repr (review r7#1)."""
    from openbiliclaw.auth_core import hash_password, verify_password
    from openbiliclaw.config import get_auth_plain_password, load_config

    real_hash = hash_password("secret")
    path = tmp_path / "config.toml"
    path.write_text("[api.auth]\nenabled = true\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD_HASH", real_hash)

    loaded = load_config(path)
    assert loaded.api.auth.enabled is True
    # the env hash is the credential — login with the matching password works
    assert loaded.api.auth.password_hash == real_hash
    assert verify_password("secret", loaded.api.auth.password_hash)
    # no stable plaintext under a hash env → reconcile uses the hash material
    assert get_auth_plain_password() is None


def test_password_hash_env_does_not_crash_with_on_disk_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An on-disk plaintext `password` plus PASSWORD_HASH env must not crash load
    (the splitter previously raised TypeError descending into the string), and the
    env hash must WIN precedence over the on-disk plaintext (review r7#1)."""
    from openbiliclaw.auth_core import hash_password, verify_password
    from openbiliclaw.config import load_config

    real_hash = hash_password("envsecret")
    path = tmp_path / "config.toml"
    path.write_text('[api.auth]\nenabled = true\npassword = "oldpw"\n', encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD_HASH", real_hash)

    loaded = load_config(path)  # must not raise
    # env hash wins over on-disk plaintext
    assert loaded.api.auth.password_hash == real_hash
    assert verify_password("envsecret", loaded.api.auth.password_hash)
    assert not verify_password("oldpw", loaded.api.auth.password_hash)


def test_password_env_wins_over_password_hash_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Precedence: env PASSWORD (plaintext) beats env PASSWORD_HASH (review r7#1)."""
    from openbiliclaw.auth_core import hash_password, verify_password
    from openbiliclaw.config import load_config

    path = tmp_path / "config.toml"
    path.write_text("[api.auth]\nenabled = true\n", encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "plainwins")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD_HASH", hash_password("hashloses"))

    loaded = load_config(path)
    assert verify_password("plainwins", loaded.api.auth.password_hash)
    assert not verify_password("hashloses", loaded.api.auth.password_hash)


def test_password_hash_env_preserves_on_disk_plaintext_for_after_env_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PASSWORD_HASH-env-managed save preserves the on-disk plaintext password so
    removing the env override restores the operator's own credential (review r7#1)."""
    from openbiliclaw.auth_core import hash_password, verify_password
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text('[api.auth]\nenabled = true\npassword = "diskpw"\n', encoding="utf-8")
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD_HASH", hash_password("envsecret"))

    loaded = load_config(path)
    save_config(loaded, path)  # env-managed write
    assert 'password = "diskpw"' in path.read_text(encoding="utf-8")
    monkeypatch.delenv("OPENBILICLAW_API_AUTH_PASSWORD_HASH")
    reloaded = load_config(path)
    assert verify_password("diskpw", reloaded.api.auth.password_hash)


def test_save_config_preserves_unchanged_plaintext_password_non_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-env save must NOT drop an unchanged on-disk plaintext `password`.

    Dropping it (writing hash-only) flips the reconcile fingerprint basis from
    "pw:"+plain to "ph:"+hash and spuriously revokes remembered sessions on the
    next restart after an unrelated settings/cookie save (review r8).
    """
    from openbiliclaw.config import get_auth_plain_password, load_config, save_config

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text('[api.auth]\nenabled = true\npassword = "secret"\n', encoding="utf-8")

    # an UNRELATED save (e.g. settings UI changing an LLM key) — auth untouched
    cfg = load_config(path)
    cfg.llm.openai.api_key = "sk-unrelated"
    save_config(cfg, path)

    text = path.read_text(encoding="utf-8")
    assert 'password = "secret"' in text  # plaintext preserved
    assert "password_hash" not in text  # not converted to hash-only
    # the plaintext fingerprint source is still available → stable basis
    assert get_auth_plain_password() == "secret"


def test_save_config_drops_stale_plaintext_when_password_changed(tmp_path: Path) -> None:
    """When the in-memory hash no longer matches the on-disk plaintext (password
    deliberately changed, e.g. set-password), the stale plaintext is dropped and
    the new hash persisted — the change is not silently reverted (review r8)."""
    from openbiliclaw.auth_core import hash_password, verify_password
    from openbiliclaw.config import load_config, save_config

    path = tmp_path / "config.toml"
    path.write_text('[api.auth]\nenabled = true\npassword = "oldpw"\n', encoding="utf-8")

    cfg = load_config(path)
    cfg.api.auth.password_hash = hash_password("newpw")  # deliberate change
    save_config(cfg, path)

    text = path.read_text(encoding="utf-8")
    assert "oldpw" not in text  # stale plaintext dropped
    reloaded = load_config(path)
    assert verify_password("newpw", reloaded.api.auth.password_hash)
    assert not verify_password("oldpw", reloaded.api.auth.password_hash)


def test_save_config_does_not_bake_in_config_local_auth_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated full-config save must not bake config.local.toml-derived auth
    values into config.toml (review r10). load_config merges config.local OVER
    config.toml (local wins); persisting the merged value would leave a stale
    literal that shifts the trust boundary once config.local is removed.
    """
    from openbiliclaw.config import load_config, save_config

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[api.auth]\nenabled = true\npassword_hash = "h"\n'
        "trust_loopback = true\nsession_ttl_hours = 5\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.toml").write_text(
        "[api.auth]\ntrust_loopback = false\nsession_ttl_hours = 12\n", encoding="utf-8"
    )

    merged = load_config()  # config.local wins
    assert merged.api.auth.trust_loopback is False
    assert merged.api.auth.session_ttl_hours == 12

    merged.llm.openai.api_key = "sk-unrelated"  # unrelated change
    save_config(merged)  # writes config.toml (no explicit path → default)

    # config.toml must keep its OWN base values, not config.local's overrides
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "trust_loopback = true" in text
    assert "session_ttl_hours = 5" in text
    assert "sk-unrelated" in text  # the unrelated change persisted

    # removing config.local → the base config.toml values govern (no stale local)
    (tmp_path / "config.local.toml").unlink()
    reloaded = load_config()
    assert reloaded.api.auth.trust_loopback is True
    assert reloaded.api.auth.session_ttl_hours == 5


def test_save_config_does_not_bake_in_config_local_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config.local plaintext password must not be materialized into config.toml
    by an unrelated save; config.toml keeps its own credential (review r10)."""
    from openbiliclaw.auth_core import verify_password
    from openbiliclaw.config import load_config, save_config

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[api.auth]\nenabled = true\npassword = "basepw"\n', encoding="utf-8"
    )
    (tmp_path / "config.local.toml").write_text(
        '[api.auth]\npassword = "localpw"\n', encoding="utf-8"
    )

    merged = load_config()
    assert verify_password("localpw", merged.api.auth.password_hash)  # local wins
    merged.llm.openai.api_key = "sk-unrelated"
    save_config(merged)

    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'password = "basepw"' in text  # base credential preserved
    assert "localpw" not in text  # config.local value NOT baked in

    (tmp_path / "config.local.toml").unlink()
    assert verify_password("basepw", load_config().api.auth.password_hash)


def test_save_config_explicit_path_ignores_project_root_config_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A save to an explicit path unrelated to the project root must NOT be gated
    by the project-root config.local.toml — load_config(explicit) never merges it,
    so its overrides must not preserve/omit fields in the explicit file (review
    r11). Otherwise a legitimate explicit-path auth change is silently dropped.
    """
    from openbiliclaw.config import Config, load_config, save_config

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(proj))
    (proj / "config.local.toml").write_text(
        "[api.auth]\ntrust_loopback = false\n", encoding="utf-8"
    )

    explicit = tmp_path / "elsewhere" / "config.toml"
    cfg = Config()
    cfg.api.auth.enabled = True
    cfg.api.auth.password_hash = "h"
    cfg.api.auth.trust_loopback = False  # the intended explicit-path value
    save_config(cfg, explicit)

    # the project-root config.local must not have shadowed the explicit write
    assert "trust_loopback = false" in explicit.read_text(encoding="utf-8")
    assert load_config(explicit).api.auth.trust_loopback is False


def test_twitter_source_defaults() -> None:
    config = Config()

    assert config.sources.twitter.enabled is False
    assert config.sources.twitter.mode == "cookie"
    assert config.sources.twitter.cookie_env == "OPENBILICLAW_X_COOKIE"
    assert config.sources.twitter.daily_search_budget == 0
    assert config.sources.twitter.daily_feed_budget == 0
    assert config.sources.twitter.daily_creator_budget == 0
    assert config.sources.twitter.request_interval_seconds == 3
    assert config.sources.twitter.min_interval_minutes == 60


def test_twitter_source_parsed_from_toml(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.twitter]
enabled = true
mode = "cookie"
cookie_env = "MY_X_COOKIE"
daily_search_budget = 12
daily_feed_budget = 4
daily_creator_budget = 6
request_interval_seconds = 5
min_interval_minutes = 90
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.twitter.enabled is True
    assert config.sources.twitter.cookie_env == "MY_X_COOKIE"
    assert config.sources.twitter.daily_search_budget == 12
    assert config.sources.twitter.daily_feed_budget == 4
    assert config.sources.twitter.daily_creator_budget == 6
    assert config.sources.twitter.request_interval_seconds == 5
    assert config.sources.twitter.min_interval_minutes == 90


def test_twitter_source_round_trips_through_save_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.twitter.enabled = True
    config.sources.twitter.cookie_env = "ROUND_TRIP_X_COOKIE"
    config.sources.twitter.daily_search_budget = 7
    config.sources.twitter.daily_feed_budget = 2
    config.sources.twitter.daily_creator_budget = 3
    config.sources.twitter.request_interval_seconds = 4
    config.sources.twitter.min_interval_minutes = 45

    save_config(config, config_path)
    rendered = config_path.read_text(encoding="utf-8")
    assert "[sources.twitter]" in rendered
    assert "daily_feed_budget = 2" in rendered

    loaded = load_config(config_path)
    assert loaded.sources.twitter.enabled is True
    assert loaded.sources.twitter.cookie_env == "ROUND_TRIP_X_COOKIE"
    assert loaded.sources.twitter.daily_search_budget == 7
    assert loaded.sources.twitter.daily_feed_budget == 2
    assert loaded.sources.twitter.daily_creator_budget == 3
    assert loaded.sources.twitter.request_interval_seconds == 4
    assert loaded.sources.twitter.min_interval_minutes == 45


def test_pool_source_shares_twitter_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 1,
        "youtube": 1,
        "twitter": 2,
    }

    save_config(config, config_path)
    rendered = config_path.read_text(encoding="utf-8")
    assert "twitter = 2" in rendered

    loaded = load_config(config_path)
    assert loaded.scheduler.pool_source_shares["twitter"] == 2


def test_twitter_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.twitter]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_SOURCES_TWITTER_ENABLED", "true")

    config = load_config(toml_path)

    assert config.sources.twitter.enabled is True


def test_disabling_twitter_drops_its_pool_quota() -> None:
    from openbiliclaw.runtime.source_policy import effective_pool_source_shares

    config = Config()
    config.sources.twitter.enabled = True
    config.scheduler.pool_source_shares = {
        "bilibili": 8,
        "twitter": 3,
    }
    assert effective_pool_source_shares(config).get("twitter") == 3

    config.sources.twitter.enabled = False
    assert "twitter" not in effective_pool_source_shares(config)
