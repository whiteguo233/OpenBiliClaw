"""Tests for configuration management."""

import tomllib
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
    DiscoveryConfig,
    LLMConfig,
    LLMProviderConfig,
    NetworkConfig,
    SchedulerConfig,
    SoulConfig,
    SoulPreferenceConfig,
    _build_config,
    load_config,
    load_config_with_diagnostics,
    normalize_outbound_proxy,
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
        assert config.llm.timeout == 1200
        assert config.bilibili.auth_method == "cookie"
        assert config.bilibili.proxy == ""  # direct connection by default
        assert config.scheduler.enabled is True
        assert config.scheduler.llm_budget_max_calls == 120
        assert config.scheduler.llm_budget_window_seconds == 3600
        assert config.scheduler.discovery_cron == "0 */8 * * *"
        assert config.scheduler.pool_target_count == 300
        assert isinstance(config.autostart, AutostartConfig)
        assert config.autostart.enabled is False
        assert config.autostart.manage_ollama is True
        assert config.api.auth.extension_access_enabled is False
        assert config.api.auth.extension_access_keys == []
        assert config.api.auth.extension_token_ttl_hours == 24

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("", False), ("0", False), ("false", False), ("1", True), ("true", True)],
    )
    def test_scheduler_enabled_env_override_is_always_a_bool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        expected: bool,
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("[scheduler]\nenabled = true\n", encoding="utf-8")
        monkeypatch.setenv("OPENBILICLAW_SCHEDULER_ENABLED", raw)

        config = load_config(config_path)

        assert config.scheduler.enabled is expected
        assert type(config.scheduler.enabled) is bool

    def test_explicit_old_concurrency_is_preserved_and_derives_background(self) -> None:
        from openbiliclaw.llm.concurrency import background_llm_concurrency

        config = Config(llm=LLMConfig(concurrency=3))

        assert config.llm.concurrency == 3
        assert background_llm_concurrency(config.llm.concurrency) == 2

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(10, 10), (600, 600), (1200, 1200), (9, 1200), (1201, 1200), ("bad", 1200)],
    )
    def test_llm_timeout_normalization_uses_twenty_minute_default(
        self, raw: object, expected: int
    ) -> None:
        assert config_module._normalize_llm_timeout(raw) == expected

    def test_saved_sync_defaults_off_and_round_trips(self, tmp_path: Path) -> None:
        config = Config()
        assert config.saved_sync.auto_sync_enabled is False

        config.saved_sync.auto_sync_enabled = True
        config_path = tmp_path / "config.toml"
        save_config(config, config_path)

        assert load_config(config_path).saved_sync.auto_sync_enabled is True

    def test_embedding_cache_capacity_defaults_unlimited(self) -> None:
        config = Config()

        # 0 = unlimited: the L2 cache keeps current behavior unless the user
        # opts into a byte budget (issue #153).
        assert config.llm.embedding.cache_max_bytes == 0
        assert config.llm.embedding.cache_high_watermark == 0.9
        assert config.llm.embedding.cache_low_watermark == 0.7

    def test_embedding_cache_capacity_fields_round_trip(self, tmp_path: Path) -> None:
        config = Config()
        config.llm.embedding.cache_max_bytes = 536870912
        config.llm.embedding.cache_high_watermark = 0.85
        config.llm.embedding.cache_low_watermark = 0.6
        config_path = tmp_path / "config.toml"
        save_config(config, config_path)

        loaded = load_config(config_path)
        assert loaded.llm.embedding.cache_max_bytes == 536870912
        assert loaded.llm.embedding.cache_high_watermark == 0.85
        assert loaded.llm.embedding.cache_low_watermark == 0.6

    def test_example_config_disables_saved_auto_sync(self) -> None:
        example_path = Path(__file__).parents[1] / "config.example.toml"

        with example_path.open("rb") as handle:
            example = tomllib.load(handle)

        assert example["saved_sync"] == {"auto_sync_enabled": False}

    def test_config_defaults_pool_target_count_to_300(self) -> None:
        config = Config()

        assert config.scheduler.pool_target_count == 300
        assert config.scheduler.copy_ready_target_count == 90

    def test_config_defaults_eval_batch_coalescing_fields(self) -> None:
        config = Config()

        assert config.scheduler.eval_min_batch_size == 15
        assert config.scheduler.eval_max_wait_seconds == 90.0

    def test_scheduler_pool_source_shares_defaults(self) -> None:
        config = Config()

        assert config.scheduler.pool_source_shares == {
            "bilibili": 5,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
            "twitter": 1,
            "zhihu": 1,
            "reddit": 1,
            "bangumi": 1,
            "linuxdo": 1,
            "weibo": 1,
            "v2ex": 1,
        }

    def test_bilibili_source_enabled_defaults_true(self) -> None:
        config = Config()

        assert config.sources.bilibili.enabled is True

    def test_bilibili_publication_preference_defaults_to_legacy_behavior(self) -> None:
        config = Config()

        assert config.sources.bilibili.recommendation_date_preset == "all"
        assert config.sources.bilibili.recommendation_date_start == ""
        assert config.sources.bilibili.recommendation_date_end == ""
        assert config.sources.bilibili.recommendation_date_weight == 0.5

    def test_bilibili_publication_preference_round_trips(self, tmp_path: Path) -> None:
        config = Config()
        config.sources.bilibili.recommendation_date_preset = "custom"
        config.sources.bilibili.recommendation_date_start = "2023-01-01"
        config.sources.bilibili.recommendation_date_end = "2023-12-31"
        config.sources.bilibili.recommendation_date_weight = 0.5

        config_path = tmp_path / "config.toml"
        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.sources.bilibili.recommendation_date_preset == "custom"
        assert loaded.sources.bilibili.recommendation_date_start == "2023-01-01"
        assert loaded.sources.bilibili.recommendation_date_end == "2023-12-31"
        assert loaded.sources.bilibili.recommendation_date_weight == 0.5

    @pytest.mark.parametrize(
        "raw",
        [
            {"sources": {"bilibili": {"recommendation_date_preset": "invalid"}}},
            {"sources": {"bilibili": {"recommendation_date_start": "2023-02-30"}}},
            {
                "sources": {
                    "bilibili": {
                        "recommendation_date_preset": "custom",
                        "recommendation_date_start": "2023-01-01",
                        "recommendation_date_end": "2022-01-01",
                    }
                }
            },
            {"sources": {"bilibili": {"recommendation_date_weight": 1.1}}},
        ],
    )
    def test_invalid_bilibili_publication_preference_is_blocking(
        self,
        raw: dict[str, object],
    ) -> None:
        config = _build_config(raw)
        issues = config_module._collect_config_issues(config)

        assert any(
            issue.field == "sources.bilibili.recommendation_date"
            and issue.severity == "blocking"
            for issue in issues
        )

    def test_save_rejects_invalid_bilibili_publication_preference(self, tmp_path: Path) -> None:
        config = Config()
        config.sources.bilibili.recommendation_date_weight = 1.1

        with pytest.raises(ConfigError, match="sources.bilibili.recommendation_date"):
            save_config(config, tmp_path / "config.toml")

    def test_scheduler_pause_on_extension_disconnect_defaults(self) -> None:
        config = Config()

        assert config.scheduler.pause_on_extension_disconnect is False
        assert config.scheduler.extension_disconnect_grace_seconds == 90

    def test_scheduler_runtime_field_defaults(self) -> None:
        config = Config()

        assert config.scheduler.refresh_check_interval_seconds == 60
        assert config.scheduler.signal_event_threshold == 6
        assert config.scheduler.trending_refresh_minutes == 3
        assert config.scheduler.explore_refresh_minutes == 3
        assert config.scheduler.discovery_limit == 30
        assert config.scheduler.proactive_push_interval_seconds == 120
        assert config.scheduler.speculator_idle_interval_minutes == 30

    def test_scheduler_source_incremental_defaults(self) -> None:
        config = Config()
        loaded_without_override = _build_config({"scheduler": {}})

        assert config.scheduler.source_incremental_enabled is False
        assert loaded_without_override.scheduler.source_incremental_enabled is False
        assert config.scheduler.source_incremental_hours == 24
        assert config.scheduler.xhs_incremental_hours is None
        assert config.scheduler.douyin_incremental_hours == 0
        assert loaded_without_override.scheduler.douyin_incremental_hours == 0
        assert config.scheduler.youtube_incremental_hours is None
        assert config.scheduler.zhihu_incremental_hours is None
        assert config.scheduler.reddit_incremental_hours is None
        for source in (
            config.sources.xiaohongshu,
            config.sources.douyin,
            config.sources.youtube,
            config.sources.zhihu,
            config.sources.reddit,
            config.sources.linuxdo,
            config.sources.v2ex,
        ):
            assert source.incremental_enabled is False

    def test_example_config_disables_all_periodic_source_sync_by_default(self) -> None:
        example_path = Path(__file__).parents[1] / "config.example.toml"

        with example_path.open("rb") as handle:
            example = tomllib.load(handle)

        scheduler = example["scheduler"]
        assert scheduler["source_incremental_enabled"] is False
        assert scheduler["source_incremental_hours"] == 24
        assert "xhs_incremental_hours" not in scheduler
        assert scheduler["douyin_incremental_hours"] == 0
        assert "youtube_incremental_hours" not in scheduler
        assert "zhihu_incremental_hours" not in scheduler
        assert "reddit_incremental_hours" not in scheduler
        sources = example["sources"]
        for slug in ("xiaohongshu", "douyin", "youtube", "zhihu", "reddit", "linuxdo", "v2ex"):
            assert sources[slug]["incremental_enabled"] is False

    def test_default_config_persists_periodic_source_sync_disabled(self, tmp_path: Path) -> None:
        target = tmp_path / "config.toml"

        save_config(Config(), target)

        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert "source_incremental_enabled = false" in rendered
        assert "douyin_incremental_hours = 0" in rendered
        assert loaded.scheduler.source_incremental_enabled is False
        assert loaded.scheduler.douyin_incremental_hours == 0

    def test_scheduler_source_incremental_config_round_trip(self, tmp_path: Path) -> None:
        config = Config()
        config.scheduler.source_incremental_enabled = True
        config.scheduler.source_incremental_hours = 37
        config.scheduler.xhs_incremental_hours = 0
        config.scheduler.douyin_incremental_hours = 168
        config.scheduler.youtube_incremental_hours = None
        config.scheduler.zhihu_incremental_hours = 7
        config.scheduler.reddit_incremental_hours = 42

        target = tmp_path / "config.toml"
        save_config(config, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert loaded.scheduler.source_incremental_enabled is True
        assert loaded.scheduler.source_incremental_hours == 37
        assert loaded.scheduler.xhs_incremental_hours == 0
        assert loaded.scheduler.douyin_incremental_hours == 168
        assert loaded.scheduler.youtube_incremental_hours is None
        assert loaded.scheduler.zhihu_incremental_hours == 7
        assert loaded.scheduler.reddit_incremental_hours == 42
        assert "xhs_incremental_hours = 0" in rendered
        assert "youtube_incremental_hours" not in rendered

    def test_source_incremental_enabled_per_source_round_trip(self, tmp_path: Path) -> None:
        config = Config()
        config.sources.xiaohongshu.incremental_enabled = True
        config.sources.douyin.incremental_enabled = True
        config.sources.youtube.incremental_enabled = False
        config.sources.zhihu.incremental_enabled = True
        config.sources.reddit.incremental_enabled = False
        config.sources.linuxdo.incremental_enabled = True
        config.sources.v2ex.incremental_enabled = False

        target = tmp_path / "config.toml"
        save_config(config, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert loaded.sources.xiaohongshu.incremental_enabled is True
        assert loaded.sources.douyin.incremental_enabled is True
        assert loaded.sources.youtube.incremental_enabled is False
        assert loaded.sources.zhihu.incremental_enabled is True
        assert loaded.sources.reddit.incremental_enabled is False
        assert loaded.sources.linuxdo.incremental_enabled is True
        assert loaded.sources.v2ex.incremental_enabled is False
        assert "incremental_enabled = true" in rendered
        assert "incremental_enabled = false" in rendered

    def test_scheduler_source_incremental_env_fields_are_filtered_to_flat_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "config.toml"
        target.write_text("[scheduler]\n", encoding="utf-8")
        values = {
            "OPENBILICLAW_SCHEDULER_SOURCE_INCREMENTAL_ENABLED": "true",
            "OPENBILICLAW_SCHEDULER_SOURCE_INCREMENTAL_HOURS": "1",
            "OPENBILICLAW_SCHEDULER_XHS_INCREMENTAL_HOURS": "2",
            "OPENBILICLAW_SCHEDULER_DOUYIN_INCREMENTAL_HOURS": "3",
            "OPENBILICLAW_SCHEDULER_YOUTUBE_INCREMENTAL_HOURS": "4",
            "OPENBILICLAW_SCHEDULER_ZHIHU_INCREMENTAL_HOURS": "5",
            "OPENBILICLAW_SCHEDULER_REDDIT_INCREMENTAL_HOURS": "6",
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)

        loaded = load_config(target)

        assert loaded.scheduler.source_incremental_enabled is True
        assert loaded.scheduler.source_incremental_hours == 1
        assert loaded.scheduler.xhs_incremental_hours == 2
        assert loaded.scheduler.douyin_incremental_hours == 3
        assert loaded.scheduler.youtube_incremental_hours == 4
        assert loaded.scheduler.zhihu_incremental_hours == 5
        assert loaded.scheduler.reddit_incremental_hours == 6

    @pytest.mark.parametrize("value", [-1, 169, True, 1.5, "not-an-int"])
    def test_scheduler_source_incremental_loader_falls_back_for_invalid_values(
        self, value: object
    ) -> None:
        config = _build_config(
            {
                "scheduler": {
                    "source_incremental_hours": value,
                    "xhs_incremental_hours": value,
                    "douyin_incremental_hours": value,
                }
            }
        )

        assert config.scheduler.source_incremental_hours == 24
        assert config.scheduler.xhs_incremental_hours is None
        assert config.scheduler.douyin_incremental_hours == 0

    def test_scheduler_source_incremental_save_rejects_invalid_direct_dataclass_values(
        self, tmp_path: Path
    ) -> None:
        config = Config()
        config.scheduler.source_incremental_hours = 169

        with pytest.raises(ValueError, match="0..168"):
            save_config(config, tmp_path / "config.toml")

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

    def test_bilibili_proxy_round_trips_through_toml(self, tmp_path: Path) -> None:
        config = Config()
        config.bilibili.proxy = "http://127.0.0.1:7890"

        target = tmp_path / "config.toml"
        save_config(config, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert 'proxy = "http://127.0.0.1:7890"' in rendered
        assert loaded.bilibili.proxy == "http://127.0.0.1:7890"

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

    def test_token_diet_runtime_controls_round_trip(self, tmp_path: Path) -> None:
        cfg = Config()
        cfg.scheduler.copy_ready_target_count = 47
        cfg.soul.preference_prompt_view = "compact-v1"
        cfg.soul.awareness_prompt_view = "legacy"
        cfg.soul.insight_prompt_view = "compact-v1"
        target = tmp_path / "config.toml"

        save_config(cfg, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert "copy_ready_target_count = 47" in rendered
        assert 'preference_prompt_view = "compact-v1"' in rendered
        assert 'awareness_prompt_view = "legacy"' in rendered
        assert 'insight_prompt_view = "compact-v1"' in rendered
        assert "cognition_prompt_view" not in rendered
        assert loaded.scheduler.copy_ready_target_count == 47
        assert loaded.soul.preference_prompt_view == "compact-v1"
        assert loaded.soul.awareness_prompt_view == "legacy"
        assert loaded.soul.insight_prompt_view == "compact-v1"

    def test_token_diet_runtime_control_defaults_only_enable_awareness(self) -> None:
        cfg = Config()

        assert cfg.soul.preference_prompt_view == "legacy"
        assert cfg.soul.awareness_prompt_view == "compact-v1"
        assert cfg.soul.insight_prompt_view == "legacy"

    def test_cognition_budget_knobs_round_trip_through_toml(self, tmp_path: Path) -> None:
        cfg = Config()
        cfg.soul.awareness_event_batch_size = 80
        cfg.soul.insight_note_batch_size = 40
        cfg.soul.cognition_max_tokens = 8192
        target = tmp_path / "config.toml"

        save_config(cfg, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert "awareness_event_batch_size = 80" in rendered
        assert "insight_note_batch_size = 40" in rendered
        assert "cognition_max_tokens = 8192" in rendered
        assert loaded.soul.awareness_event_batch_size == 80
        assert loaded.soul.insight_note_batch_size == 40
        assert loaded.soul.cognition_max_tokens == 8192

    def test_cognition_budget_knobs_default_to_module_constants(self) -> None:
        cfg = Config()

        assert cfg.soul.awareness_event_batch_size == 300
        assert cfg.soul.insight_note_batch_size == 150
        assert cfg.soul.cognition_max_tokens == 32768

    def test_cognition_budget_knobs_reject_invalid_values(self) -> None:
        from openbiliclaw.config import _collect_config_issues

        cfg = Config()
        cfg.soul.awareness_event_batch_size = 1
        cfg.soul.insight_note_batch_size = 9999
        cfg.soul.cognition_max_tokens = 99

        fields = {issue.field for issue in _collect_config_issues(cfg)}

        assert "soul.awareness_event_batch_size" in fields
        assert "soul.insight_note_batch_size" in fields
        assert "soul.cognition_max_tokens" in fields

    def test_token_diet_runtime_controls_reject_invalid_values(self) -> None:
        from openbiliclaw.config import _collect_config_issues

        cfg = Config()
        cfg.scheduler.copy_ready_target_count = 601
        cfg.soul.preference_prompt_view = "semantic-preference"
        cfg.soul.awareness_prompt_view = "semantic-awareness"
        cfg.soul.insight_prompt_view = "semantic-insight"

        fields = {issue.field for issue in _collect_config_issues(cfg)}

        assert "scheduler.copy_ready_target_count" in fields
        assert "soul.preference_prompt_view" in fields
        assert "soul.awareness_prompt_view" in fields
        assert "soul.insight_prompt_view" in fields

    def test_unpublished_global_cognition_prompt_view_is_not_a_compatibility_alias(self) -> None:
        config = _build_config({"soul": {"cognition_prompt_view": "compact-v1"}})

        assert config.soul.preference_prompt_view == "legacy"
        assert config.soul.awareness_prompt_view == "compact-v1"
        assert config.soul.insight_prompt_view == "legacy"
        assert not hasattr(config.soul, "cognition_prompt_view")

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


def test_build_config_supports_orcarouter_provider() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "orcarouter",
                "orcarouter": {
                    "api_key": "sk-orca-test",
                    "model": "openai/gpt-4o",
                    "base_url": "https://api.orcarouter.ai/v1",
                    "reasoning_effort": "high",
                },
            }
        }
    )

    assert config.llm.default_provider == "orcarouter"
    assert config.llm.orcarouter.api_key == "sk-orca-test"
    assert config.llm.orcarouter.model == "openai/gpt-4o"
    assert config.llm.orcarouter.base_url == "https://api.orcarouter.ai/v1"
    assert config.llm.orcarouter.reasoning_effort == "high"


def test_validate_runtime_config_requires_orcarouter_api_key() -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="orcarouter",
            orcarouter=LLMProviderConfig(api_key="", model="openai/gpt-4o"),
        )
    )

    with pytest.raises(ConfigError, match="llm.orcarouter.api_key"):
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


def test_openai_compatible_legacy_config_does_not_opt_into_reasoning_field() -> None:
    config = _build_config(
        {
            "llm": {
                "default_provider": "openai_compatible",
                "openai_compatible": {
                    "api_key": "sk-relay",
                    "model": "relay-model",
                    "base_url": "https://relay.example/v1",
                    # Older save_config versions materialized this inherited
                    # default even though the adapter ignored it.
                    "reasoning_effort": "medium",
                },
            }
        }
    )

    assert config.llm.openai_compatible.reasoning_effort == ""


def test_openai_compatible_native_instance_explicit_reasoning_effort_is_preserved() -> None:
    config = _build_config(
        {
            "llm": {
                "routing_version": 2,
                "default_chain": ["relay-main"],
                "instances": {
                    "relay-main": {
                        "name": "Relay",
                        "provider_type": "openai_compatible",
                        "api_key": "sk-relay",
                        "model": "relay-model",
                        "base_url": "https://relay.example/v1",
                        "reasoning_effort": "medium",
                    }
                },
            }
        }
    )

    assert config.llm.instances["relay-main"].reasoning_effort == "medium"


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


def test_save_config_round_trips_claude_base_url(tmp_path: Path) -> None:
    """issue #72 — [llm.claude].base_url must be written back by
    save_config; it used to be dropped by the provider-section whitelist."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.claude.api_key = "sk-ant-test"
    config.llm.claude.base_url = "https://relay.example.com/api"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.claude.base_url == "https://relay.example.com/api"


def test_save_config_round_trips_api_flavor(tmp_path: Path) -> None:
    """issue #72 — api_flavor survives a save/load cycle for the
    OpenAI-protocol family."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.openai.api_flavor = "responses"
    config.llm.openai_compatible.api_key = "sk-relay"
    config.llm.openai_compatible.base_url = "https://relay.example.com/v1"
    config.llm.openai_compatible.api_flavor = "responses"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.openai.api_flavor == "responses"
    assert loaded.llm.openai_compatible.api_flavor == "responses"


def test_collect_issues_blocks_invalid_api_flavor() -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai_compatible=LLMProviderConfig(
                api_key="sk-relay",
                base_url="https://relay.example.com/v1",
                api_flavor="banana",
            ),
        )
    )

    issues = _collect_config_issues(config)
    flavor_issues = [i for i in issues if i.field == "llm.openai_compatible.api_flavor"]
    assert flavor_issues and flavor_issues[0].severity == "blocking"


def test_collect_issues_allows_responses_api_flavor() -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config(
        llm=LLMConfig(
            default_provider="openai_compatible",
            openai_compatible=LLMProviderConfig(
                api_key="sk-relay",
                base_url="https://relay.example.com/v1",
                api_flavor="responses",
            ),
        )
    )

    fields = [i.field for i in _collect_config_issues(config)]
    assert "llm.openai_compatible.api_flavor" not in fields


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("eval_min_batch_size", 0),
        ("eval_min_batch_size", 91),
        ("eval_max_wait_seconds", -0.1),
        ("eval_max_wait_seconds", 600.1),
    ],
)
def test_validate_runtime_config_rejects_eval_batch_coalescing_out_of_range(
    field: str,
    value: int | float,
) -> None:
    config = Config(
        llm=LLMConfig(
            default_provider="ollama",
            ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
        )
    )
    setattr(config.scheduler, field, value)

    with pytest.raises(ConfigError, match=f"scheduler.{field}"):
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
eval_min_batch_size = 23
eval_max_wait_seconds = 45.5
signal_event_threshold = 9
trending_refresh_minutes = 5
explore_refresh_minutes = 18
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
    assert config.scheduler.eval_min_batch_size == 23
    assert config.scheduler.eval_max_wait_seconds == 45.5
    assert config.scheduler.signal_event_threshold == 9
    assert config.scheduler.trending_refresh_minutes == 5
    assert config.scheduler.explore_refresh_minutes == 18
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
        ("eval_min_batch_size", "0", 15),
        ("eval_min_batch_size", "91", 15),
        ("eval_max_wait_seconds", "-1", 90.0),
        ("eval_max_wait_seconds", "601", 90.0),
        ("eval_max_wait_seconds", '"abc"', 90.0),
        ("signal_event_threshold", "-1", 6),
        ("trending_refresh_minutes", "0", 3),
        ("explore_refresh_minutes", "0", 3),
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


@pytest.mark.parametrize("literal", ["0", "-1", '"invalid"'])
def test_load_config_clamps_invalid_auto_update_interval_to_at_least_one_hour(
    tmp_path: Path,
    literal: str,
) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        f"[scheduler]\nauto_update_check_interval_hours = {literal}\n",
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.auto_update_check_interval_hours >= 1


def test_save_config_round_trips_scheduler_runtime_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.refresh_check_interval_seconds = 75
    config.scheduler.eval_min_batch_size = 23
    config.scheduler.eval_max_wait_seconds = 45.5
    config.scheduler.signal_event_threshold = 9
    config.scheduler.trending_refresh_minutes = 5
    config.scheduler.explore_refresh_minutes = 18
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
    assert loaded.scheduler.eval_min_batch_size == 23
    assert loaded.scheduler.eval_max_wait_seconds == 45.5
    assert loaded.scheduler.signal_event_threshold == 9
    assert loaded.scheduler.trending_refresh_minutes == 5
    assert loaded.scheduler.explore_refresh_minutes == 18
    assert loaded.scheduler.discovery_limit == 17
    assert loaded.scheduler.proactive_push_interval_seconds == 155
    assert loaded.scheduler.speculator_idle_interval_minutes == 11
    assert loaded.scheduler.avoidance_speculation_interval_minutes == 12
    assert loaded.scheduler.avoidance_speculation_ttl_days == 4
    assert loaded.scheduler.avoidance_speculation_cooldown_days == 8
    assert loaded.scheduler.avoidance_speculation_confirmation_threshold == 2
    assert loaded.scheduler.avoidance_speculation_max_active == 5


def test_save_config_round_trips_feedback_batch_threshold(tmp_path: Path) -> None:
    """A user-set feedback batch threshold survives a settings save.

    Regression (found in unified-interest-line Wave A): ``_render_config_toml``
    never emitted ``scheduler.feedback_batch_threshold``, so the extension popup
    and desktop settings inputs were write-only — every save silently reverted a
    user-tuned value back to the default 3 on the next load. The unified line
    reuses this key as its priority-flush threshold, so a silent revert would
    also silently change the interest-layer cadence.
    """
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.feedback_batch_threshold = 5

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.feedback_batch_threshold == 5


def test_save_config_round_trips_profile_consolidation_scheduler_fields(tmp_path: Path) -> None:
    """All ProfileConsolidator scheduler knobs survive a full config rewrite."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.profile_consolidation_enabled = False
    config.scheduler.profile_consolidation_interval_hours = 23
    config.scheduler.profile_consolidation_like_target_upper = 321
    config.scheduler.profile_consolidation_like_target_soft = 234
    config.scheduler.profile_consolidation_archive_enabled = False

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.profile_consolidation_enabled is False
    assert loaded.scheduler.profile_consolidation_interval_hours == 23
    assert loaded.scheduler.profile_consolidation_like_target_upper == 321
    assert loaded.scheduler.profile_consolidation_like_target_soft == 234
    assert loaded.scheduler.profile_consolidation_archive_enabled is False


def test_settings_save_path_preserves_feedback_batch_threshold(tmp_path: Path) -> None:
    """The exact sequence the settings API runs: load → mutate → save → reload.

    ``/api/settings`` loads the on-disk config, applies the submitted scheduler
    fields onto the dataclass, and calls ``save_config``. Before the renderer
    fix, the reload dropped the threshold even though the save request carried
    it.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text("[scheduler]\nfeedback_batch_threshold = 7\n", encoding="utf-8")

    cfg = load_config(config_path)
    assert cfg.scheduler.feedback_batch_threshold == 7
    # The settings endpoint mutates in place and re-saves the whole config.
    cfg.scheduler.discovery_limit = 21
    save_config(cfg, config_path)

    reloaded = load_config(config_path)
    assert reloaded.scheduler.discovery_limit == 21
    assert reloaded.scheduler.feedback_batch_threshold == 7


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
        "twitter": 1,
        "zhihu": 1,
        "reddit": 1,
        "bangumi": 1,
        "linuxdo": 1,
        "weibo": 1,
        "v2ex": 1,
    }


def test_scheduler_pool_source_shares_backfills_new_source_defaults(tmp_path: Path) -> None:
    toml_path = tmp_path / "legacy.toml"
    toml_path.write_text(
        """
[scheduler.pool_source_shares]
bilibili = 5
xiaohongshu = 1
douyin = 1
youtube = 1
twitter = 1
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.scheduler.pool_source_shares["zhihu"] == 1
    assert config.scheduler.pool_source_shares["reddit"] == 1
    assert config.scheduler.pool_source_shares["linuxdo"] == 1


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
    assert config.sources.xiaohongshu.daily_search_budget == 20
    assert config.sources.xiaohongshu.daily_creator_budget == 0
    assert config.sources.xiaohongshu.task_interval_seconds == 1200
    assert config.sources.xiaohongshu.min_interval_minutes == 20


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
    assert config.sources.youtube.min_interval_minutes == 3


def test_sources_reddit_defaults() -> None:
    config = _build_config({})

    assert config.sources.reddit.enabled is False
    assert config.sources.reddit.backend == "rdt"
    assert config.sources.reddit.source_modes == ("search", "hot", "subreddit", "related")
    assert config.sources.reddit.daily_search_budget == 300
    assert config.sources.reddit.daily_hot_budget == 300
    assert config.sources.reddit.daily_subreddit_budget == 300
    assert config.sources.reddit.daily_related_budget == 300
    assert config.sources.reddit.request_interval_seconds == 3
    assert config.sources.reddit.min_interval_minutes == 3


def test_sources_bangumi_defaults() -> None:
    config = Config()

    assert config.sources.bangumi.enabled is False
    assert config.sources.bangumi.username == ""
    assert config.sources.bangumi.access_token == ""
    assert config.sources.bangumi.subject_types == ("anime", "book", "game")
    assert config.sources.bangumi.source_modes == ("search", "ranked", "latest")
    assert config.sources.bangumi.daily_search_budget == 300
    assert config.sources.bangumi.daily_ranked_budget == 100
    assert config.sources.bangumi.daily_latest_budget == 100
    assert config.sources.bangumi.request_interval_seconds == 1
    assert config.sources.bangumi.min_interval_minutes == 3
    assert config.sources.bangumi.bootstrap_limit == 300


def test_load_config_clamps_linuxdo_browser_task_limits(tmp_path: Path) -> None:
    config_path = tmp_path / "linuxdo-limits.toml"
    config_path.write_text(
        """
[sources.linuxdo]
request_interval_seconds = 31
bootstrap_limit = 500
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.sources.linuxdo.request_interval_seconds == 30
    assert config.sources.linuxdo.bootstrap_limit == 300


def test_sources_v2ex_defaults() -> None:
    config = Config()

    assert config.sources.v2ex.enabled is False
    assert config.sources.v2ex.username == ""
    assert config.sources.v2ex.access_token == ""
    assert config.sources.v2ex.token_env == "OPENBILICLAW_V2EX_TOKEN"
    assert config.sources.v2ex.source_modes == ("search", "node", "tab", "hot", "latest")
    assert config.sources.v2ex.tab_modes == ("tech", "creative", "qna")
    assert config.sources.v2ex.node_blocklist == ("sandbox",)
    assert config.sources.v2ex.daily_search_budget == 120
    assert config.sources.v2ex.daily_node_budget == 180
    assert config.sources.v2ex.request_interval_seconds == 2
    assert config.sources.v2ex.min_interval_minutes == 5


def test_save_config_round_trips_sources_v2ex(tmp_path: Path) -> None:
    config = Config()
    config.sources.v2ex.enabled = True
    config.sources.v2ex.username = "alice"
    config.sources.v2ex.access_token = "pat-123"
    config.sources.v2ex.token_env = "V2EX_TOKEN_TEST"
    config.sources.v2ex.source_modes = ("search", "node", "hot")
    config.sources.v2ex.tab_modes = ("tech",)
    config.sources.v2ex.node_allowlist = ("programmer", "python")
    config.sources.v2ex.node_blocklist = ("sandbox", "deals")
    config.sources.v2ex.daily_search_budget = 42
    config.sources.v2ex.request_interval_seconds = 4
    config.sources.v2ex.min_interval_minutes = 20
    config.scheduler.pool_source_shares["v2ex"] = 2

    target = tmp_path / "config.toml"
    save_config(config, target)
    loaded = load_config(target)

    assert loaded.sources.v2ex == config.sources.v2ex
    assert loaded.scheduler.pool_source_shares["v2ex"] == 2


def test_save_config_rejects_invalid_v2ex_credentials(tmp_path: Path) -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config()
    config.sources.v2ex.username = "bad/name"
    config.sources.v2ex.access_token = "bad token\nwith newline"
    target = tmp_path / "config.toml"

    issues = _collect_config_issues(config)
    assert any(
        issue.field == "sources.v2ex.username" and issue.severity == "blocking" for issue in issues
    )
    assert any(
        issue.field == "sources.v2ex.access_token" and issue.severity == "blocking"
        for issue in issues
    )
    with pytest.raises(ValueError, match="unsupported"):
        save_config(config, target)
    assert not target.exists()


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("node_allowlist", ("programmer", "../private")),
        ("max_topic_chars", 20_001),
        ("bootstrap_max_pages_per_scope", 101),
    ),
)
def test_save_config_rejects_unsafe_v2ex_bounds(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    config = Config()
    setattr(config.sources.v2ex, field_name, value)
    target = tmp_path / "config.toml"

    with pytest.raises(ValueError, match=field_name):
        save_config(config, target)

    assert not target.exists()


def test_legacy_v2ex_config_is_bounded_and_normalized() -> None:
    from openbiliclaw.config import V2EXSourceConfig, normalize_v2ex_source_config

    source = V2EXSourceConfig(
        source_modes=("SEARCH", "unknown", "search"),
        tab_modes=("Tech",),
        node_allowlist=("Programmer", "../private", "programmer"),
        max_topic_chars=999_999,
        bootstrap_max_pages_per_scope=0,
    )

    normalize_v2ex_source_config(source, strict=False)

    assert source.source_modes == ("search",)
    assert source.tab_modes == ("tech",)
    assert source.node_allowlist == ("programmer",)
    assert source.max_topic_chars == 20_000
    assert source.bootstrap_max_pages_per_scope == 1


def test_save_config_round_trips_sources_bangumi(tmp_path: Path) -> None:
    config = Config()
    config.sources.bangumi.enabled = True
    config.sources.bangumi.username = "sai"
    config.sources.bangumi.access_token = "tok-abc123"
    config.sources.bangumi.subject_types = ("anime", "music")
    config.sources.bangumi.source_modes = ("search", "ranked")
    config.sources.bangumi.daily_search_budget = 42
    config.sources.bangumi.daily_ranked_budget = 21
    config.sources.bangumi.daily_latest_budget = 11
    config.sources.bangumi.request_interval_seconds = 2
    config.sources.bangumi.min_interval_minutes = 45
    config.sources.bangumi.bootstrap_limit = 123
    config.scheduler.pool_source_shares["bangumi"] = 2

    target = tmp_path / "config.toml"
    save_config(config, target)
    loaded = load_config(target)

    assert loaded.sources.bangumi == config.sources.bangumi
    assert loaded.scheduler.pool_source_shares["bangumi"] == 2


def test_save_config_rejects_invalid_bangumi_username(tmp_path: Path) -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config()
    config.sources.bangumi.username = "bad/name"
    target = tmp_path / "config.toml"

    issues = _collect_config_issues(config)
    assert any(
        issue.field == "sources.bangumi.username" and issue.severity == "blocking"
        for issue in issues
    )
    with pytest.raises(ValueError, match="unsupported character"):
        save_config(config, target)
    assert not target.exists()


def test_save_config_rejects_invalid_bangumi_access_token(tmp_path: Path) -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config()
    config.sources.bangumi.access_token = "bad token\nwith newline"
    target = tmp_path / "config.toml"

    issues = _collect_config_issues(config)
    assert any(
        issue.field == "sources.bangumi.access_token" and issue.severity == "blocking"
        for issue in issues
    )
    with pytest.raises(ValueError, match="unsupported character"):
        save_config(config, target)
    assert not target.exists()


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


def test_build_config_supports_sources_reddit(tmp_path: Path) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.reddit]
enabled = true
backend = "rdt"
source_modes = ["search", "hot", "subreddit", "related", "unknown"]
daily_search_budget = 4
daily_hot_budget = 2
daily_subreddit_budget = 3
daily_related_budget = 5
request_interval_seconds = 6
min_interval_minutes = 45
""".strip(),
        encoding="utf-8",
    )

    config = load_config(toml_path)

    assert config.sources.reddit.enabled is True
    assert config.sources.reddit.backend == "rdt"
    assert config.sources.reddit.source_modes == ("search", "hot", "subreddit", "related")
    assert config.sources.reddit.daily_search_budget == 4
    assert config.sources.reddit.daily_hot_budget == 2
    assert config.sources.reddit.daily_subreddit_budget == 3
    assert config.sources.reddit.daily_related_budget == 5
    assert config.sources.reddit.request_interval_seconds == 6
    assert config.sources.reddit.min_interval_minutes == 45


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


def test_save_config_round_trips_sources_reddit(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.reddit.enabled = True
    config.sources.reddit.backend = "rdt"
    config.sources.reddit.source_modes = ("search", "hot", "subreddit", "related")
    config.sources.reddit.daily_search_budget = 7
    config.sources.reddit.daily_hot_budget = 3
    config.sources.reddit.daily_subreddit_budget = 4
    config.sources.reddit.daily_related_budget = 5
    config.sources.reddit.request_interval_seconds = 6
    config.sources.reddit.min_interval_minutes = 30
    config.scheduler.pool_source_shares["reddit"] = 2

    save_config(config, config_path)
    rendered = config_path.read_text(encoding="utf-8")

    assert "[sources.reddit]" in rendered
    assert 'source_modes = ["search", "hot", "subreddit", "related"]' in rendered
    assert "daily_subreddit_budget = 4" in rendered
    assert "reddit = 2" in rendered
    loaded = load_config(config_path)
    assert loaded.sources.reddit.enabled is True
    assert loaded.sources.reddit.backend == "rdt"
    assert loaded.sources.reddit.source_modes == ("search", "hot", "subreddit", "related")
    assert loaded.sources.reddit.daily_search_budget == 7
    assert loaded.sources.reddit.daily_hot_budget == 3
    assert loaded.sources.reddit.daily_subreddit_budget == 4
    assert loaded.sources.reddit.daily_related_budget == 5
    assert loaded.sources.reddit.request_interval_seconds == 6
    assert loaded.sources.reddit.min_interval_minutes == 30
    assert loaded.scheduler.pool_source_shares["reddit"] == 2


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


def test_load_config_repairs_weibo_creator_only_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.weibo.source_modes = ("creator",)

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.sources.weibo.source_modes == ("search", "creator")


def test_save_config_round_trips_pool_source_shares(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.pool_source_shares = {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 2,
        "youtube": 1,
        "twitter": 3,
        "zhihu": 1,
        "reddit": 2,
        "bangumi": 1,
        "linuxdo": 1,
        "weibo": 1,
        "v2ex": 1,
    }

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.scheduler.pool_source_shares == {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 2,
        "youtube": 1,
        "twitter": 3,
        "zhihu": 1,
        "reddit": 2,
        "bangumi": 1,
        "linuxdo": 1,
        "weibo": 1,
        "v2ex": 1,
    }


def test_save_config_round_trips_advanced_scheduler_and_logging_fields(
    tmp_path: Path,
) -> None:
    """Popup/API saves must not drop advanced fields that the UI may not edit."""
    config_path = tmp_path / "config.toml"
    config = Config()
    config.scheduler.llm_budget_max_calls = 30
    config.scheduler.llm_budget_window_seconds = 1800
    config.scheduler.speculation_interval_minutes = 22
    config.scheduler.speculation_ttl_days = 8
    config.scheduler.speculation_cooldown_days = 9
    config.scheduler.speculation_confirmation_threshold = 4
    config.scheduler.speculation_max_active = 6
    config.scheduler.speculation_max_primary_interests = 17
    config.scheduler.speculation_max_secondary_interests = 66
    config.scheduler.delight_queue_limit = 37
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

    assert loaded.scheduler.llm_budget_max_calls == 30
    assert loaded.scheduler.llm_budget_window_seconds == 1800
    assert loaded.scheduler.speculation_interval_minutes == 22
    assert loaded.scheduler.speculation_ttl_days == 8
    assert loaded.scheduler.speculation_cooldown_days == 9
    assert loaded.scheduler.speculation_confirmation_threshold == 4
    assert loaded.scheduler.speculation_max_active == 6
    assert loaded.scheduler.speculation_max_primary_interests == 17
    assert loaded.scheduler.speculation_max_secondary_interests == 66
    assert loaded.scheduler.delight_queue_limit == 37
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
    assert loaded.llm.fallback_provider == "openai"
    assert loaded.llm.gemini.api_key == "gemini-test-key"
    assert loaded.llm.gemini.model == "gemini-2.5-flash"
    assert loaded.llm.embedding.fallback_enabled is True
    assert loaded.llm.embedding.fallback_provider == "ollama"


def test_save_config_round_trips_empty_deepseek_reasoning_effort(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.deepseek.reasoning_effort = ""

    save_config(config, config_path)
    rendered = config_path.read_text(encoding="utf-8")
    loaded = load_config(config_path)

    assert 'reasoning_effort = ""' in rendered
    assert loaded.llm.deepseek.reasoning_effort == ""


def test_save_config_round_trips_supported_provider_reasoning_efforts(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.llm.openai.reasoning_effort = "low"
    config.llm.claude.reasoning_effort = "high"
    config.llm.gemini.reasoning_effort = "minimal"
    config.llm.openrouter.reasoning_effort = "max"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.llm.openai.reasoning_effort == "low"
    assert loaded.llm.claude.reasoning_effort == "high"
    assert loaded.llm.gemini.reasoning_effort == "minimal"
    assert loaded.llm.openrouter.reasoning_effort == "max"


def test_supported_provider_reasoning_effort_defaults_to_medium() -> None:
    config = Config()

    assert config.llm.openai.reasoning_effort == "medium"
    assert config.llm.claude.reasoning_effort == "medium"
    assert config.llm.gemini.reasoning_effort == "medium"
    assert config.llm.deepseek.reasoning_effort == "medium"
    assert config.llm.openrouter.reasoning_effort == "medium"


def test_llm_and_embedding_fallback_defaults_are_disabled() -> None:
    config = Config()

    # Chat side: a non-empty fallback_provider IS the enable switch — the
    # legacy [llm].fallback_enabled bool has been removed entirely.
    assert not hasattr(config.llm, "fallback_enabled")
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
    config.llm.embedding.multimodal_enabled = True

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
    assert loaded.llm.embedding.multimodal_enabled is True


def test_embedding_multimodal_enabled_defaults_false() -> None:
    config = Config()
    assert config.llm.embedding.multimodal_enabled is False


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


def test_extension_access_config_round_trips_and_preserves_local_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "config.toml"
    base_record = "base:" + "a" * 64
    local_records = ["local-a:" + "b" * 64, "local-b:" + "c" * 64]
    base.write_text(
        "[api.auth]\n"
        "extension_access_enabled = false\n"
        f'extension_access_keys = ["{base_record}"]\n'
        "extension_token_ttl_hours = 12\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.toml").write_text(
        "[api.auth]\n"
        "extension_access_enabled = true\n"
        f'extension_access_keys = ["{local_records[0]}", "{local_records[1]}"]\n'
        "extension_token_ttl_hours = 48\n",
        encoding="utf-8",
    )

    merged = load_config()
    assert merged.api.auth.extension_access_enabled is True
    assert merged.api.auth.extension_access_keys == local_records
    assert merged.api.auth.extension_token_ttl_hours == 48

    save_config(merged)
    rendered = base.read_text(encoding="utf-8")
    assert "extension_access_enabled = false" in rendered
    assert f'extension_access_keys = ["{base_record}"]' in rendered
    assert "extension_token_ttl_hours = 12" in rendered

    (tmp_path / "config.local.toml").unlink()
    reloaded = load_config()
    assert reloaded.api.auth.extension_access_enabled is False
    assert reloaded.api.auth.extension_access_keys == [base_record]
    assert reloaded.api.auth.extension_token_ttl_hours == 12


@pytest.mark.parametrize("value", [0, 169, "not-a-number"])
def test_extension_access_token_ttl_normalizes_invalid_values_to_default(value: object) -> None:
    config = _build_config({"api": {"auth": {"extension_token_ttl_hours": value}}})

    assert config.api.auth.extension_token_ttl_hours == 24


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
    assert config.sources.twitter.min_interval_minutes == 3


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


def test_zhihu_source_round_trips_through_save_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config = Config()
    config.sources.zhihu.enabled = True
    config.sources.zhihu.source_modes = ("search", "hot", "feed", "creator", "related")
    config.sources.zhihu.daily_search_budget = 9
    config.sources.zhihu.daily_hot_budget = 3
    config.sources.zhihu.daily_feed_budget = 4
    config.sources.zhihu.daily_creator_budget = 5
    config.sources.zhihu.daily_related_budget = 6
    config.sources.zhihu.request_interval_seconds = 4
    config.sources.zhihu.min_interval_minutes = 30
    config.scheduler.pool_source_shares["zhihu"] = 2

    save_config(config, config_path)
    rendered = config_path.read_text(encoding="utf-8")
    assert "[sources.zhihu]" in rendered
    assert 'source_modes = ["search", "hot", "feed", "creator", "related"]' in rendered
    assert "daily_search_budget = 9" in rendered
    assert "daily_hot_budget = 3" in rendered
    assert "daily_feed_budget = 4" in rendered
    assert "daily_creator_budget = 5" in rendered
    assert "daily_related_budget = 6" in rendered
    assert "zhihu = 2" in rendered

    loaded = load_config(config_path)
    assert loaded.sources.zhihu.enabled is True
    assert loaded.sources.zhihu.source_modes == ("search", "hot", "feed", "creator", "related")
    assert loaded.sources.zhihu.daily_search_budget == 9
    assert loaded.sources.zhihu.daily_hot_budget == 3
    assert loaded.sources.zhihu.daily_feed_budget == 4
    assert loaded.sources.zhihu.daily_creator_budget == 5
    assert loaded.sources.zhihu.daily_related_budget == 6
    assert loaded.sources.zhihu.request_interval_seconds == 4
    assert loaded.sources.zhihu.min_interval_minutes == 30
    assert loaded.scheduler.pool_source_shares["zhihu"] == 2


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


def test_zhihu_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "c.toml"
    toml_path.write_text(
        """
[sources.zhihu]
enabled = false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENBILICLAW_SOURCES_ZHIHU_ENABLED", "true")

    config = load_config(toml_path)

    assert config.sources.zhihu.enabled is True


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


class TestDiscoveryConfig:
    """Unified keyword planner config group (Discover backpressure P1, spec §6)."""

    def test_discovery_defaults_match_spec_section_6(self) -> None:
        """Defaults are the owner-approved §6 baseline. Pinning every value here
        is the contract the planner relies on before any TOML is written."""
        config = Config()

        assert isinstance(config.discovery, DiscoveryConfig)
        assert config.discovery.unified_keyword_planner_enabled is True
        assert config.discovery.kw_cache_high == 30
        assert config.discovery.kw_cache_low == 10
        assert config.discovery.gen_batch == 30
        assert config.discovery.fetch_batch == 5
        assert config.discovery.history_window_size == 150
        assert config.discovery.history_window_hours == 48
        assert config.discovery.claim_lease_minutes == 10
        assert config.discovery.planner_poll_seconds == 120
        assert config.discovery.plan_ttl_hours == 12
        assert config.discovery.keyword_digest_grace_hours == 24
        assert config.discovery.admission_min_score == 0.60
        assert config.discovery.inspiration_search_enabled is True
        assert config.discovery.inspiration_replace_merged_keywords is False
        assert config.discovery.inspiration_search_backends == (
            "local_cache",
            "platform_sources",
            "bing_rss",
            "exa",
            "you",
            "serply",
        )
        assert config.discovery.inspiration_breadth == "high"
        assert config.discovery.eval_prefilter_mode == "shadow"
        assert config.discovery.eval_scorer == "llm"
        assert config.discovery.multimodal_evaluation_enabled is False
        assert config.discovery.visual_profile_enabled is False
        assert config.discovery.keyframe_enabled is False
        assert config.llm.embedding.multimodal_enabled is False
        assert config.discovery.candidate_eval_concurrency == 3
        assert config.discovery.multimodal_batch_size == 8
        assert config.discovery.multimodal_image_max_px == 384
        assert config.discovery.multimodal_image_quality == 72
        assert config.discovery.multimodal_image_timeout_seconds == 6
        assert config.discovery.keyframe_max_frames == 4
        assert config.discovery.keyframe_fetch_limit == 50
        assert config.discovery.danmaku_fetch_limit == 50
        assert config.discovery.danmaku_max_chars == 500

    def test_discovery_defaults_from_empty_dict(self) -> None:
        config = _build_config({})

        assert config.discovery.unified_keyword_planner_enabled is True
        assert config.discovery.kw_cache_high == 30
        assert config.discovery.plan_ttl_hours == 12
        assert config.discovery.keyword_digest_grace_hours == 24
        assert config.discovery.admission_min_score == 0.60
        assert config.discovery.inspiration_search_enabled is True
        assert config.discovery.inspiration_replace_merged_keywords is False
        assert config.discovery.inspiration_search_backends == (
            "local_cache",
            "platform_sources",
            "bing_rss",
            "exa",
            "you",
            "serply",
        )
        assert config.discovery.inspiration_breadth == "high"
        assert config.discovery.eval_prefilter_mode == "shadow"
        assert config.discovery.eval_scorer == "llm"
        assert config.discovery.multimodal_evaluation_enabled is False
        assert config.discovery.visual_profile_enabled is False
        assert config.discovery.keyframe_enabled is False
        assert config.llm.embedding.multimodal_enabled is False
        assert config.discovery.multimodal_batch_size == 8

    def test_example_config_defaults_to_hybrid_with_visual_features_off(self) -> None:
        example_path = Path(__file__).parents[1] / "config.example.toml"

        with example_path.open("rb") as handle:
            example = tomllib.load(handle)

        assert example["discovery"]["inspiration_search_enabled"] is True
        assert example["discovery"]["inspiration_replace_merged_keywords"] is False
        assert example["discovery"]["multimodal_evaluation_enabled"] is False
        assert example["discovery"]["visual_profile_enabled"] is False
        assert example["discovery"]["keyframe_enabled"] is False
        assert example["llm"]["embedding"]["multimodal_enabled"] is False

    def test_visual_enrichment_numeric_fields_round_trip(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config = Config()
        config.discovery.keyframe_max_frames = 9
        config.discovery.keyframe_fetch_limit = 17
        config.discovery.danmaku_fetch_limit = 23
        config.discovery.danmaku_max_chars = 1800

        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.discovery.keyframe_max_frames == 9
        assert loaded.discovery.keyframe_fetch_limit == 17
        assert loaded.discovery.danmaku_fetch_limit == 23
        assert loaded.discovery.danmaku_max_chars == 1800

    def test_top_level_discovery_is_distinct_from_llm_discovery(self) -> None:
        """`[discovery]` (planner knobs) must not collide with `[llm.discovery]`
        (per-module provider override) — they are independent tables."""
        config = _build_config(
            {
                "discovery": {"unified_keyword_planner_enabled": True, "gen_batch": 42},
                "llm": {"discovery": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
            }
        )

        assert config.discovery.unified_keyword_planner_enabled is True
        assert config.discovery.gen_batch == 42
        assert config.llm.discovery.provider == "deepseek"
        assert config.llm.discovery.model == "deepseek-v4-flash"

    def test_discovery_loads_and_normalizes_from_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            """
[discovery]
unified_keyword_planner_enabled = true
kw_cache_high = 50
kw_cache_low = 20
gen_batch = 40
fetch_batch = 8
history_window_size = 200
history_window_hours = 72
claim_lease_minutes = 15
planner_poll_seconds = 90
plan_ttl_hours = 6
keyword_digest_grace_hours = 36
admission_min_score = 0.72
inspiration_search_enabled = true
inspiration_replace_merged_keywords = true
inspiration_search_backends = ["platform_sources", "exa", "you"]
inspiration_breadth = "high"
eval_prefilter_mode = "enforce"
eval_scorer = "shadow"
multimodal_evaluation_enabled = true
candidate_eval_concurrency = 3
multimodal_batch_size = 4
multimodal_image_max_px = 512
multimodal_image_quality = 80
multimodal_image_timeout_seconds = 10
""".strip(),
            encoding="utf-8",
        )

        config = load_config(toml_path)

        assert config.discovery.unified_keyword_planner_enabled is True
        assert config.discovery.kw_cache_high == 50
        assert config.discovery.kw_cache_low == 20
        assert config.discovery.gen_batch == 40
        assert config.discovery.fetch_batch == 8
        assert config.discovery.history_window_size == 200
        assert config.discovery.history_window_hours == 72
        assert config.discovery.claim_lease_minutes == 15
        assert config.discovery.planner_poll_seconds == 90
        assert config.discovery.plan_ttl_hours == 6
        assert config.discovery.keyword_digest_grace_hours == 36
        assert config.discovery.admission_min_score == 0.72
        assert config.discovery.inspiration_search_enabled is True
        assert config.discovery.inspiration_replace_merged_keywords is True
        assert config.discovery.inspiration_search_backends == ("platform_sources", "exa", "you")
        assert config.discovery.inspiration_breadth == "high"
        assert config.discovery.eval_prefilter_mode == "enforce"
        assert config.discovery.eval_scorer == "shadow"
        assert config.discovery.multimodal_evaluation_enabled is True
        assert config.discovery.candidate_eval_concurrency == 3
        assert config.discovery.multimodal_batch_size == 4
        assert config.discovery.multimodal_image_max_px == 512
        assert config.discovery.multimodal_image_quality == 80
        assert config.discovery.multimodal_image_timeout_seconds == 10

    def test_discovery_flag_accepts_string_boolean(self) -> None:
        """The flag coerces TOML/env string booleans like other bool fields."""
        assert (
            _build_config(
                {"discovery": {"unified_keyword_planner_enabled": "true"}}
            ).discovery.unified_keyword_planner_enabled
            is True
        )
        assert (
            _build_config(
                {"discovery": {"unified_keyword_planner_enabled": "off"}}
            ).discovery.unified_keyword_planner_enabled
            is False
        )

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            (1, 1),
            (2, 2),
            (3, 3),
            # Scheduler integer settings fall back to their default when a
            # persisted value exceeds the documented ceiling; they do not
            # silently retain an unsafe value.
            (8, 3),
        ],
    )
    def test_discovery_candidate_eval_concurrency_is_limited_to_three(
        self, tmp_path: Path, configured: int, expected: int
    ) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            f"[discovery]\ncandidate_eval_concurrency = {configured}\n",
            encoding="utf-8",
        )

        config = load_config(toml_path)

        assert config.discovery.candidate_eval_concurrency == expected

    @pytest.mark.parametrize(
        ("field", "literal", "expected"),
        [
            ("kw_cache_high", "0", 30),
            ("kw_cache_high", '"abc"', 30),
            ("kw_cache_low", "-1", 10),
            ("gen_batch", "0", 30),
            ("fetch_batch", '"x"', 5),
            ("history_window_size", "0", 150),
            ("history_window_hours", "-5", 48),
            ("claim_lease_minutes", "0", 10),
            ("planner_poll_seconds", '"nope"', 120),
            ("plan_ttl_hours", "0", 12),
            ("keyword_digest_grace_hours", "-1", 24),
            ("keyword_digest_grace_hours", "169", 24),
            ("candidate_eval_concurrency", "0", 3),
            ("candidate_eval_concurrency", "4", 3),
            ("multimodal_batch_size", "0", 8),
            ("multimodal_batch_size", "13", 8),
            ("multimodal_image_max_px", "127", 384),
            ("multimodal_image_max_px", "769", 384),
            ("multimodal_image_quality", "39", 72),
            ("multimodal_image_quality", "91", 72),
            ("multimodal_image_timeout_seconds", "0", 6),
            ("multimodal_image_timeout_seconds", "21", 6),
        ],
    )
    def test_discovery_invalid_values_fall_back_to_defaults(
        self, tmp_path: Path, field: str, literal: str, expected: int
    ) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            f"""
[discovery]
{field} = {literal}
""".strip(),
            encoding="utf-8",
        )

        config = load_config(toml_path)

        assert getattr(config.discovery, field) == expected

    @pytest.mark.parametrize("literal", ["0", "0.49", "-0.1", "1.1", '"nope"'])
    def test_discovery_invalid_admission_min_score_falls_back_to_default(
        self, tmp_path: Path, literal: str
    ) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            f"""
[discovery]
admission_min_score = {literal}
""".strip(),
            encoding="utf-8",
        )

        config = load_config(toml_path)

        assert config.discovery.admission_min_score == 0.60

    def test_discovery_eval_prefilter_mode_normalizes_from_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            """
[discovery]
eval_prefilter_mode = "  Shadow  "
""".strip(),
            encoding="utf-8",
        )

        config = load_config(toml_path)

        assert config.discovery.eval_prefilter_mode == "shadow"

    def test_validate_runtime_config_rejects_invalid_eval_prefilter_mode(self) -> None:
        config = Config()
        config.llm.default_provider = "ollama"
        # Main's stricter validation requires an explicit ollama chat model;
        # satisfy it so validation reaches the prefilter-mode check.
        config.llm.ollama.model = "qwen2.5:7b"
        config.discovery.eval_prefilter_mode = "aggressive"

        with pytest.raises(ConfigError, match="discovery\\.eval_prefilter_mode"):
            validate_runtime_config(config)

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [("shadow", "shadow"), ("LEARNED", "learned"), ("unsafe", "llm")],
    )
    def test_discovery_eval_scorer_normalizes_from_toml(
        self,
        tmp_path: Path,
        configured: str,
        expected: str,
    ) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            f'[discovery]\neval_scorer = "{configured}"\n',
            encoding="utf-8",
        )

        assert load_config(toml_path).discovery.eval_scorer == expected

    def test_validate_runtime_config_rejects_invalid_eval_scorer(self) -> None:
        config = Config()
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "qwen2.5:7b"
        config.discovery.eval_scorer = "unsafe"

        with pytest.raises(ConfigError, match="discovery\\.eval_scorer"):
            validate_runtime_config(config)

    def test_discovery_missing_table_uses_defaults(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text("[scheduler]\nenabled = true\n", encoding="utf-8")

        config = load_config(toml_path)

        assert config.discovery == DiscoveryConfig()

    def test_discovery_non_table_value_falls_back_to_defaults(self) -> None:
        """A malformed `discovery = "x"` (scalar, not a table) must not crash;
        it falls back to all defaults like other dict-guarded sections."""
        config = _build_config({"discovery": "not-a-table"})

        assert config.discovery == DiscoveryConfig()

    def test_discovery_env_override_coerces_string_values(self) -> None:
        """`_apply_env_overrides` injects env values as strings into the raw
        table; the loader coerces them exactly like the scheduler fields do.
        This mirrors the real load path for a (hypothetical) single-token key."""
        config = _build_config(
            {
                "discovery": {
                    "unified_keyword_planner_enabled": "true",
                    "gen_batch": "25",
                    "plan_ttl_hours": "9",
                }
            }
        )

        assert config.discovery.unified_keyword_planner_enabled is True
        assert config.discovery.gen_batch == 25
        assert config.discovery.plan_ttl_hours == 9

    def test_discovery_multiword_env_var_mis_nests_like_other_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Documented limitation: the generic ``OPENBILICLAW_A_B_C`` splitter
        splits on every ``_``, so a multi-word ``[discovery]`` key
        (``OPENBILICLAW_DISCOVERY_GEN_BATCH`` → ``discovery.gen.batch``) does
        NOT reach the field — exactly like ``[scheduler]`` multi-word keys. The
        loader silently keeps the default rather than crashing. Pinned so the
        behavior is intentional, not an accidental regression. (A future env
        override for these would need an explicit reader, like `[api.auth]`.)"""
        toml_path = tmp_path / "c.toml"
        toml_path.write_text("[discovery]\ngen_batch = 30\n", encoding="utf-8")
        monkeypatch.setenv("OPENBILICLAW_DISCOVERY_GEN_BATCH", "7")

        config = load_config(toml_path)

        assert config.discovery.gen_batch == 30  # env var mis-nested → default kept

    def test_discovery_round_trips_through_save_load(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config = Config()
        config.discovery.unified_keyword_planner_enabled = True
        config.discovery.kw_cache_high = 44
        config.discovery.kw_cache_low = 11
        config.discovery.gen_batch = 33
        config.discovery.fetch_batch = 6
        config.discovery.history_window_size = 175
        config.discovery.history_window_hours = 60
        config.discovery.claim_lease_minutes = 12
        config.discovery.planner_poll_seconds = 100
        config.discovery.plan_ttl_hours = 8
        config.discovery.keyword_digest_grace_hours = 0
        config.discovery.admission_min_score = 0.72
        config.discovery.inspiration_search_enabled = True
        config.discovery.inspiration_replace_merged_keywords = True
        config.discovery.inspiration_search_backends = ("you",)
        config.discovery.inspiration_breadth = "low"
        config.discovery.eval_prefilter_mode = "enforce"
        config.discovery.eval_scorer = "shadow"
        config.discovery.multimodal_evaluation_enabled = True
        config.discovery.multimodal_batch_size = 4
        config.discovery.multimodal_image_max_px = 512
        config.discovery.multimodal_image_quality = 80
        config.discovery.multimodal_image_timeout_seconds = 10

        save_config(config, config_path)
        loaded = load_config(config_path)

        assert loaded.discovery.unified_keyword_planner_enabled is True
        assert loaded.discovery.kw_cache_high == 44
        assert loaded.discovery.kw_cache_low == 11
        assert loaded.discovery.gen_batch == 33
        assert loaded.discovery.fetch_batch == 6
        assert loaded.discovery.history_window_size == 175
        assert loaded.discovery.history_window_hours == 60
        assert loaded.discovery.claim_lease_minutes == 12
        assert loaded.discovery.planner_poll_seconds == 100
        assert loaded.discovery.plan_ttl_hours == 8
        assert loaded.discovery.keyword_digest_grace_hours == 0
        assert loaded.discovery.admission_min_score == 0.72
        assert loaded.discovery.inspiration_search_enabled is True
        assert loaded.discovery.inspiration_replace_merged_keywords is True
        assert loaded.discovery.inspiration_search_backends == ("you",)
        assert loaded.discovery.inspiration_breadth == "low"
        assert loaded.discovery.eval_prefilter_mode == "enforce"
        assert loaded.discovery.eval_scorer == "shadow"
        assert loaded.discovery.multimodal_evaluation_enabled is True
        assert loaded.discovery.multimodal_batch_size == 4
        assert loaded.discovery.multimodal_image_max_px == 512
        assert loaded.discovery.multimodal_image_quality == 80
        assert loaded.discovery.multimodal_image_timeout_seconds == 10

    def test_discovery_section_appears_in_rendered_toml(self) -> None:
        from openbiliclaw.config import _render_config_toml

        rendered = _render_config_toml(Config())

        assert "[discovery]" in rendered
        assert "unified_keyword_planner_enabled = true" in rendered
        assert "kw_cache_high = 30" in rendered
        assert "plan_ttl_hours = 12" in rendered
        assert "keyword_digest_grace_hours = 24" in rendered
        assert "admission_min_score = 0.6" in rendered
        assert "inspiration_search_enabled = true" in rendered
        assert "inspiration_replace_merged_keywords = false" in rendered
        assert (
            'inspiration_search_backends = ["local_cache", "platform_sources", '
            '"bing_rss", "exa", "you", "serply"]' in rendered
        )
        assert 'inspiration_breadth = "high"' in rendered
        assert 'eval_prefilter_mode = "shadow"' in rendered
        assert 'eval_scorer = "llm"' in rendered
        assert "multimodal_evaluation_enabled = false" in rendered
        assert "multimodal_batch_size = 8" in rendered
        assert "multimodal_image_max_px = 384" in rendered


def test_collect_issues_blocks_unknown_embedding_provider() -> None:
    """A browser page-translator once rewrote value-less <option> text into
    config ('奥拉玛'), silently disabling the embedding service. Unknown
    embedding provider names must block the save instead of persisting."""
    from openbiliclaw.config import _collect_config_issues

    config = Config()
    config.llm.embedding.provider = "奥拉玛"
    config.llm.embedding.fallback_provider = "双子座"

    issues = _collect_config_issues(config)

    fields = {issue.field for issue in issues if issue.severity == "blocking"}
    assert "llm.embedding.provider" in fields
    assert "llm.embedding.fallback_provider" in fields

    # Legit values (any case) and empty stay clean.
    config.llm.embedding.provider = "Ollama"
    config.llm.embedding.fallback_provider = ""
    issues = _collect_config_issues(config)
    assert not any(issue.field.startswith("llm.embedding.") for issue in issues)


def _llm_fallback_issues(config: Config) -> list[ConfigIssue]:
    from openbiliclaw.config import _collect_config_issues

    issues = _collect_config_issues(config)
    return [issue for issue in issues if issue.field == "llm.fallback_provider"]


def test_collect_issues_blocks_unknown_llm_fallback_provider() -> None:
    """`_fallback_order()` silently drops an unknown fallback name (e.g. a
    browser-translated '奥拉玛'), so the save must block with the same
    translation hint as the embedding-provider check."""
    config = Config()
    config.llm.default_provider = "deepseek"
    config.llm.deepseek.api_key = "sk-x"
    config.llm.fallback_provider = "奥拉玛"

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)
    assert "网页翻译" in issues[0].message


def test_collect_issues_blocks_llm_fallback_even_when_default_is_unknown() -> None:
    """The fallback checks must run before the default-provider early return
    and must not crash when the default provider itself is unknown."""
    config = Config()
    config.llm.default_provider = "bogus"
    config.llm.fallback_provider = "deepseek"  # no api_key -> dead fallback

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)


def test_collect_issues_blocks_same_name_llm_fallback() -> None:
    """A fallback identical to the default provider would never fire —
    comparison is normalized (strip/lower)."""
    config = Config()
    config.llm.default_provider = "deepseek"
    config.llm.deepseek.api_key = "sk-x"
    config.llm.fallback_provider = " DeepSeek "

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)
    assert "永远不会生效" in issues[0].message


def test_collect_issues_blocks_llm_fallback_missing_api_key() -> None:
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "sk-main"
    config.llm.fallback_provider = "deepseek"

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)
    assert "llm.deepseek.api_key" in issues[0].message


def test_collect_issues_blocks_openai_compatible_llm_fallback_without_base_url() -> None:
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "sk-main"
    config.llm.fallback_provider = "openai_compatible"
    config.llm.openai_compatible.api_key = "sk-gateway"
    config.llm.openai_compatible.base_url = ""

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)
    assert "base_url" in issues[0].message


def test_collect_issues_blocks_ollama_llm_fallback_without_model() -> None:
    """An Ollama server URL cannot identify which local chat model to use."""
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "sk-main"
    config.llm.fallback_provider = "ollama"

    issues = _llm_fallback_issues(config)

    assert issues
    assert all(issue.severity == "blocking" for issue in issues)

    config.llm.ollama.base_url = "http://localhost:11434/v1"
    assert _llm_fallback_issues(config)

    config.llm.ollama.model = "llama3"
    assert _llm_fallback_issues(config) == []


def test_collect_issues_blocks_default_ollama_without_model() -> None:
    from openbiliclaw.config import _collect_config_issues

    config = Config()
    config.llm.default_provider = "ollama"
    config.llm.ollama.base_url = "http://localhost:11434/v1"

    issues = _collect_config_issues(config)

    assert any(
        issue.field == "llm.ollama.model" and issue.severity == "blocking" for issue in issues
    )


def test_collect_issues_allows_gemini_llm_fallback_with_env_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "sk-main"
    config.llm.fallback_provider = "gemini"

    assert _llm_fallback_issues(config) == []


def test_collect_issues_allows_empty_and_configured_llm_fallback() -> None:
    config = Config()
    config.llm.default_provider = "openai"
    config.llm.openai.api_key = "sk-main"
    config.llm.fallback_provider = ""

    assert _llm_fallback_issues(config) == []

    config.llm.fallback_provider = "deepseek"
    config.llm.deepseek.api_key = "sk-fallback"

    assert _llm_fallback_issues(config) == []


# ── Phase 2 Task 4: inspiration config collapse (13 → 4) ────────────────


class TestInspirationBreadth:
    """Breadth tier validation, derivation tables, and removed-key notices."""

    def test_medium_breadth_derivation_matches_precollapse_defaults(self) -> None:
        """Table-driven zero-drift guard: medium == the pre-collapse
        `_DEFAULT_INSPIRATION_*` values, item by item (Spec Part C)."""
        params = config_module.derive_inspiration_breadth_params("medium")

        expected = {
            "aspect_window_size": 32,
            "interest_sample_size": 6,
            "max_probe_searches_per_stage": 12,
            "platforms_per_probe": 2,
            "riskcontrolled_probe_budget": 4,
            "search_pages_per_probe": 1,
            "search_results_per_query": 5,
            "max_seeds_per_aspect": 3,
            "max_keywords_per_platform": 12,
        }
        for field, value in expected.items():
            assert getattr(params, field) == value, field
        # And item-identical to the module constants the old fields defaulted to.
        constant_by_field = {
            "aspect_window_size": config_module._DEFAULT_INSPIRATION_ASPECT_WINDOW_SIZE,
            "interest_sample_size": config_module._DEFAULT_INSPIRATION_INTEREST_SAMPLE_SIZE,
            "max_probe_searches_per_stage": (
                config_module._DEFAULT_INSPIRATION_MAX_PROBE_SEARCHES_PER_STAGE
            ),
            "platforms_per_probe": config_module._DEFAULT_INSPIRATION_PLATFORMS_PER_PROBE,
            "riskcontrolled_probe_budget": (
                config_module._DEFAULT_INSPIRATION_RISKCONTROLLED_PROBE_BUDGET
            ),
            "search_pages_per_probe": config_module._DEFAULT_INSPIRATION_SEARCH_PAGES_PER_PROBE,
            "search_results_per_query": (
                config_module._DEFAULT_INSPIRATION_SEARCH_RESULTS_PER_QUERY
            ),
            "max_seeds_per_aspect": config_module._DEFAULT_INSPIRATION_MAX_SEEDS_PER_ASPECT,
            "max_keywords_per_platform": (
                config_module._DEFAULT_INSPIRATION_MAX_KEYWORDS_PER_PLATFORM
            ),
        }
        for field, constant in constant_by_field.items():
            assert getattr(params, field) == constant, field

    @pytest.mark.parametrize(
        ("tier", "expected"),
        [
            (
                "low",
                {
                    "aspect_window_size": 16,
                    "interest_sample_size": 3,
                    "max_probe_searches_per_stage": 6,
                    "platforms_per_probe": 1,
                    "riskcontrolled_probe_budget": 2,
                    "search_pages_per_probe": 1,
                    "search_results_per_query": 3,
                    "max_seeds_per_aspect": 2,
                    "max_keywords_per_platform": 8,
                },
            ),
            (
                "high",
                {
                    "aspect_window_size": 48,
                    "interest_sample_size": 8,
                    "max_probe_searches_per_stage": 20,
                    "platforms_per_probe": 3,
                    "riskcontrolled_probe_budget": 8,
                    "search_pages_per_probe": 2,
                    "search_results_per_query": 8,
                    "max_seeds_per_aspect": 5,
                    "max_keywords_per_platform": 16,
                },
            ),
        ],
    )
    def test_low_and_high_breadth_derivation_tables(
        self, tier: str, expected: dict[str, int]
    ) -> None:
        params = config_module.derive_inspiration_breadth_params(tier)

        for field, value in expected.items():
            assert getattr(params, field) == value, field

    def test_breadth_tier_is_case_insensitive_and_trimmed(self) -> None:
        config = _build_config({"discovery": {"inspiration_breadth": "  HIGH "}})

        assert config.discovery.inspiration_breadth == "high"

    def test_invalid_breadth_tier_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="inspiration_breadth"):
            _build_config({"discovery": {"inspiration_breadth": "ultra"}})
        with pytest.raises(ConfigError, match="inspiration_breadth"):
            config_module.derive_inspiration_breadth_params("")

    def test_removed_inspiration_keys_surface_diagnostics_and_are_ignored(
        self, tmp_path: Path
    ) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text(
            """
[discovery]
inspiration_max_keywords_per_platform = 99
inspiration_interest_sample_size = 42
inspiration_breadth = "low"
""".strip(),
            encoding="utf-8",
        )

        config, diagnostics = load_config_with_diagnostics(toml_path, ensure_default_file=False)

        removal_fields = {
            issue.field
            for issue in diagnostics.issues
            if "inspiration_breadth" in issue.message and "已移除" in issue.message
        }
        assert "discovery.inspiration_max_keywords_per_platform" in removal_fields
        assert "discovery.inspiration_interest_sample_size" in removal_fields
        # Values ignored (no fail-fast); the kept key still applies.
        assert config.discovery.inspiration_breadth == "low"
        assert not hasattr(config.discovery, "inspiration_max_keywords_per_platform")

    def test_clean_config_gets_no_removed_key_notice(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "c.toml"
        toml_path.write_text('[discovery]\ninspiration_breadth = "medium"', encoding="utf-8")

        _config, diagnostics = load_config_with_diagnostics(toml_path, ensure_default_file=False)

        assert not any("已移除" in issue.message for issue in diagnostics.issues)

    def test_rendered_toml_contains_only_four_inspiration_keys(self) -> None:
        rendered = config_module._render_config_toml(Config())

        inspiration_lines = [
            line.strip()
            for line in rendered.splitlines()
            if line.strip().startswith("inspiration_")
        ]
        assert sorted(line.split(" = ")[0] for line in inspiration_lines) == [
            "inspiration_breadth",
            "inspiration_replace_merged_keywords",
            "inspiration_search_backends",
            "inspiration_search_enabled",
        ]
        assert 'inspiration_breadth = "high"' in inspiration_lines


class TestNetworkProxyConfig:
    """`[network].proxy` — the overseas-outbound proxy for LLM/YouTube/updater.

    Spec: docs/plans/2026-07-11-network-proxy-config-spec.md. CN-direct clients
    (bilibili/douyin/ollama) never consume this — that isolation is guarded in
    tests/test_network_proxy_isolation.py.
    """

    def test_default_network_proxy_is_empty(self) -> None:
        config = Config()
        assert isinstance(config.network, NetworkConfig)
        assert config.network.proxy == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
            ("https://proxy.example.com:443", "https://proxy.example.com:443"),
            ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
            ("socks5h://127.0.0.1:1080", "socks5h://127.0.0.1:1080"),
            ("  socks5://127.0.0.1:1080  ", "socks5://127.0.0.1:1080"),
            ("SOCKS5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
            ("HTTP://Proxy.Example.com:8080", "http://Proxy.Example.com:8080"),
            ("socks5://user:pass@127.0.0.1:1080", "socks5://user:pass@127.0.0.1:1080"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalize_outbound_proxy_accepts_and_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_outbound_proxy(raw) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "ftp://127.0.0.1:1080",
            "socks4://127.0.0.1:1080",
            "http://",
            "socks5://",
            "not-a-url",
            "127.0.0.1:1080",
        ],
    )
    def test_normalize_outbound_proxy_rejects_bad_values(self, bad: str) -> None:
        with pytest.raises(ValueError):
            normalize_outbound_proxy(bad)

    def test_network_proxy_round_trips_through_toml(self, tmp_path: Path) -> None:
        config = Config()
        config.network.mode = "custom"
        config.network.proxy = "socks5://127.0.0.1:1080"

        target = tmp_path / "config.toml"
        save_config(config, target)
        rendered = target.read_text(encoding="utf-8")
        loaded = load_config(target)

        assert "[network]" in rendered
        assert 'mode = "custom"' in rendered
        assert 'proxy = "socks5://127.0.0.1:1080"' in rendered
        assert loaded.network.mode == "custom"
        assert loaded.network.proxy == "socks5://127.0.0.1:1080"

    def test_network_section_appears_in_rendered_toml(self) -> None:
        rendered = config_module._render_config_toml(Config())
        assert "[network]" in rendered

    def test_build_config_normalizes_network_proxy(self) -> None:
        config = _build_config({"network": {"proxy": "SOCKS5://127.0.0.1:1080"}})
        assert config.network.mode == "custom"
        assert config.network.proxy == "socks5://127.0.0.1:1080"

    @pytest.mark.parametrize("mode", ["direct", "system", "custom"])
    def test_build_config_accepts_network_proxy_modes(self, mode: str) -> None:
        proxy = "socks5://127.0.0.1:1080" if mode == "custom" else ""
        config = _build_config({"network": {"mode": mode, "proxy": proxy}})
        assert config.network.mode == mode

    def test_build_config_defaults_missing_network_mode_to_system(self) -> None:
        """An unconfigured [network].mode inherits env/OS proxies.

        Every consumer of this setting is an overseas-only service, so
        ``direct`` made the out-of-the-box experience in mainland China an
        opaque timeout. ``system`` without a proxy configured is identical
        to a direct connection, so nobody else is affected.
        """
        config = _build_config({"network": {}})
        assert config.network.mode == "system"

    def test_build_config_defaults_absent_network_table_to_system(self) -> None:
        """No ``[network]`` table at all is the same "never configured" case."""
        assert _build_config({}).network.mode == "system"

    def test_network_config_dataclass_default_is_system(self) -> None:
        """The field default and the missing-key branch must not drift apart."""
        assert config_module.NetworkConfig().mode == "system"

    def test_build_config_keeps_explicitly_configured_direct_mode(self) -> None:
        """An explicit ``direct`` still means direct after the default moved.

        Only "never configured" takes the new default; a user who wrote
        ``mode = "direct"`` asked to ignore env/system proxies and keeps
        doing so.
        """
        config = _build_config({"network": {"mode": "direct", "proxy": ""}})
        assert config.network.mode == "direct"

    def test_build_config_migrates_legacy_nonempty_proxy_to_custom(self) -> None:
        config = _build_config({"network": {"proxy": "http://127.0.0.1:7897"}})
        assert config.network.mode == "custom"

    def test_build_config_clamps_custom_without_proxy_to_direct(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Invalid values clamp to ``direct``, not to the ``system`` default.

        The user did write something here, so the conservative reading wins:
        a broken value must not silently start routing traffic through an
        inherited env proxy.
        """
        with caplog.at_level("WARNING"):
            config = _build_config({"network": {"mode": "custom", "proxy": ""}})
        assert config.network.mode == "direct"
        assert "custom" in caplog.text

    def test_build_config_clamps_unknown_mode_to_direct(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            config = _build_config({"network": {"mode": "auto"}})
        assert config.network.mode == "direct"
        assert "mode" in caplog.text.lower()

    def test_build_config_drops_invalid_network_proxy_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            config = _build_config({"network": {"proxy": "ftp://127.0.0.1:1"}})
        assert config.network.proxy == ""
        assert any("network" in record.message.lower() for record in caplog.records)

    def test_env_override_sets_network_proxy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
        monkeypatch.setenv("OPENBILICLAW_NETWORK_PROXY", "socks5://127.0.0.1:1080")
        config = load_config(config_path)
        assert config.network.mode == "custom"
        assert config.network.proxy == "socks5://127.0.0.1:1080"

    def test_env_override_can_explicitly_select_system_proxy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
        monkeypatch.setenv("OPENBILICLAW_NETWORK_MODE", "system")
        config = load_config(config_path)
        assert config.network.mode == "system"

    def test_on_disk_explicit_direct_survives_the_new_system_default(self, tmp_path: Path) -> None:
        """A real config.toml saying ``direct`` still loads as ``direct``.

        The whole-file path, not just ``_build_config``: this is the one
        guarantee the default change is not allowed to break.
        """
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[general]\nlanguage = "zh"\n\n[network]\nmode = "direct"\nproxy = ""\n',
            encoding="utf-8",
        )
        assert load_config(config_path).network.mode == "direct"

    def test_env_override_can_explicitly_select_direct_proxy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``OPENBILICLAW_NETWORK_MODE=direct`` is explicit too (Docker opt-out)."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
        monkeypatch.setenv("OPENBILICLAW_NETWORK_MODE", "direct")
        assert load_config(config_path).network.mode == "direct"

    def test_config_without_network_table_loads_as_system(self, tmp_path: Path) -> None:
        """A pre-[network] config file takes the new default on upgrade."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
        assert load_config(config_path).network.mode == "system"


def test_legacy_refresh_hours_keys_convert_to_minutes_without_crashing() -> None:
    """A pre-rename config.toml must keep its cadence, not be read as minutes.

    ``SchedulerConfig(**sched_raw)`` splats the raw table, so leaving the retired
    ``*_refresh_hours`` keys in place would raise TypeError at load and brick
    startup for every existing install. Reinterpreting them in place would be
    worse than a crash — silently multiplying that user's Bilibili traffic by 60.
    """
    config = _build_config(
        {"scheduler": {"trending_refresh_hours": 3, "explore_refresh_hours": 12}}
    )

    assert config.scheduler.trending_refresh_minutes == 180
    assert config.scheduler.explore_refresh_minutes == 720

    # The new key wins when both are present.
    mixed = _build_config(
        {"scheduler": {"trending_refresh_hours": 3, "trending_refresh_minutes": 7}}
    )
    assert mixed.scheduler.trending_refresh_minutes == 7

    # Absent keys land on the aligned 3-minute default.
    assert _build_config({"scheduler": {}}).scheduler.trending_refresh_minutes == 3


class TestUnknownConfigKeysAreTolerated:
    """A config.toml written by a newer build must not brick an older one.

    ``source_incremental_hours`` is now a supported scheduler field. Keep this
    regression focused on a genuinely unknown key so future additions do not
    accidentally weaken the compatibility filter.
    """

    def test_unknown_scheduler_key_is_ignored_instead_of_crashing(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[scheduler]
future_scheduler_field = 24
discovery_limit = 17
""".strip(),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            config = load_config(config_path)

        # Known siblings keep working — the unknown key is dropped, not the section.
        assert config.scheduler.discovery_limit == 17
        assert not hasattr(config.scheduler, "future_scheduler_field")
        assert "future_scheduler_field" in caplog.text
        assert "scheduler" in caplog.text

    def test_unknown_keys_are_tolerated_across_provider_and_plain_sections(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Coverage must not be scheduler-only — every ``**raw`` splat is a hazard."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[llm]
default_provider = "ollama"

[llm.ollama]
model = "llama3"
future_provider_field = "v9"

[storage]
unknown_storage_key = 7

[logging]
unknown_logging_key = "loud"
""".strip(),
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            config = load_config(config_path)

        assert config.llm.ollama.model == "llama3"
        for section, key in (
            ("llm.ollama", "future_provider_field"),
            ("storage", "unknown_storage_key"),
            ("logging", "unknown_logging_key"),
        ):
            assert key in caplog.text, f"{key} should be reported"
            assert section in caplog.text, f"{section} should be named in the warning"

    def test_known_scheduler_values_survive_alongside_unknown_keys(self, tmp_path: Path) -> None:
        """The filter must not silently reset neighbours to their defaults."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[scheduler]
enabled = false
future_scheduler_field = 24
refresh_check_interval_seconds = 75
trending_refresh_minutes = 5
""".strip(),
            encoding="utf-8",
        )

        config = load_config(config_path)

        assert config.scheduler.enabled is False
        assert config.scheduler.refresh_check_interval_seconds == 75
        assert config.scheduler.trending_refresh_minutes == 5


class TestUnifiedInterestLineFlag:
    """统一兴趣更新线回退开关（scheduler.unified_interest_line）。

    2026-07-28 经真实 A/B 三道门与 A/A 噪声控制后默认开启；显式 false
    仍逐字节回退旧反馈批线。
    """

    def test_absent_key_loads_as_true_through_load_config(self, tmp_path: Path) -> None:
        """真实安装的 config.toml 没有这个键——必须经 load_config 得 True。

        2026-07-28 真机 E2E 抓到 dataclass 默认与加载路径 _coerce_bool 默认漂移：
        直构 SchedulerConfig() 是 True，真实加载却是 False，统一线在真后端从未启用。
        """
        config_path = tmp_path / "config.toml"
        config_path.write_text("[scheduler]\ndiscovery_limit = 10\n", encoding="utf-8")

        config = load_config(config_path)

        assert config.scheduler.unified_interest_line is True

    def test_defaults_on(self) -> None:
        assert Config().scheduler.unified_interest_line is True

    def test_toml_true_survives_the_dataclass_filter(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[scheduler]\nunified_interest_line = true\n",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING", logger="openbiliclaw.config"):
            config = load_config(config_path)

        assert config.scheduler.unified_interest_line is True
        assert "unified_interest_line" not in caplog.text, (
            "开关必须是 SchedulerConfig 的已知字段，否则 _filter_dataclass_kwargs 会静默丢掉它"
        )

    def test_non_bool_value_is_coerced(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            '[scheduler]\nunified_interest_line = "false"\n',
            encoding="utf-8",
        )

        config = load_config(config_path)

        assert config.scheduler.unified_interest_line is False
        assert type(config.scheduler.unified_interest_line) is bool

    def test_explicit_false_survives_a_save_round_trip(self, tmp_path: Path) -> None:
        """回退开关必须能落盘：任何一次设置保存都会整份重写 config.toml。"""
        config = Config()
        config.scheduler.unified_interest_line = False
        config_path = tmp_path / "config.toml"
        save_config(config, config_path)

        assert load_config(config_path).scheduler.unified_interest_line is False

    def test_example_config_ships_the_switch_on(self) -> None:
        example_path = Path(__file__).parents[1] / "config.example.toml"

        with example_path.open("rb") as handle:
            example = tomllib.load(handle)

        assert example["scheduler"]["unified_interest_line"] is True
