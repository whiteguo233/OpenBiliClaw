"""CLI tests for configuration guidance behavior."""

import asyncio
import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw import config as config_module
from openbiliclaw.bilibili.auth import AuthStatus
from openbiliclaw.bilibili.browser import BrowserCommandError
from openbiliclaw.cli import app
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.engine import Recommendation
from openbiliclaw.soul.profile import (
    CoreLayer,
    InterestTag,
    OnionProfile,
    PreferenceLayer,
    RoleLayer,
    SoulProfile,
    ValuesLayer,
)


@pytest.fixture(autouse=True)
def _restore_cli_process_state() -> Iterator[None]:
    """Keep CLI-owned process globals from leaking into later tests."""
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    noisy_loggers = ("httpx", "httpcore", "openai", "openai._base_client")
    original_noisy_levels = {name: logging.getLogger(name).level for name in noisy_loggers}
    runtime_components = cli_module._RUNTIME_COMPONENTS
    original_runtime_components = dict(runtime_components)
    runtime_components.clear()
    try:
        yield
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            if handler not in original_handlers:
                handler.close()
        for handler in original_handlers:
            root_logger.addHandler(handler)
        root_logger.setLevel(original_level)
        for name, level in original_noisy_levels.items():
            logging.getLogger(name).setLevel(level)
        cli_module._RUNTIME_COMPONENTS.clear()
        cli_module._RUNTIME_COMPONENTS.update(original_runtime_components)


def test_build_discovery_candidate_pipeline_drains_one_shot_runs_immediately() -> None:
    class FakeDatabase:
        def __init__(self) -> None:
            self.admission_min_score: float | None = None

        def set_admission_min_score(self, value: float) -> None:
            self.admission_min_score = value

    config = SimpleNamespace(
        discovery=SimpleNamespace(admission_min_score=0.72),
        scheduler=SimpleNamespace(
            pool_target_count=321,
            eval_min_batch_size=23,
            eval_max_wait_seconds=45.5,
        ),
    )

    database = FakeDatabase()
    pipeline = cli_module._build_discovery_candidate_pipeline(
        config=config,
        database=database,
        discovery_engine=object(),
    )

    assert pipeline.pool_target_count == 321
    assert pipeline.admission_min_score == 0.72
    assert pipeline.min_eval_batch_size == 1
    assert pipeline.max_eval_wait_seconds == 0.0
    assert database.admission_min_score == 0.72


class _FakeMemoryLayer:
    def __init__(self, data: dict[str, object] | None = None) -> None:
        self.data = data or {}


def test_build_soul_engine_forwards_scheduler_speculation_config(monkeypatch) -> None:
    from openbiliclaw.config import Config

    cfg = Config()
    cfg.scheduler.speculation_interval_minutes = 22
    cfg.scheduler.speculation_ttl_days = 8
    cfg.scheduler.speculation_cooldown_days = 9
    cfg.scheduler.speculation_confirmation_threshold = 4
    cfg.scheduler.speculation_max_active = 6
    cfg.scheduler.speculation_max_primary_interests = 17
    cfg.scheduler.speculation_max_secondary_interests = 66
    cfg.scheduler.speculator_idle_interval_minutes = 11

    captured: dict[str, object] = {}

    class FakeSoulEngine:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_registry", lambda: object())
    monkeypatch.setattr("openbiliclaw.soul.engine.SoulEngine", FakeSoulEngine)

    cli_module._build_soul_engine()

    assert captured["speculation_interval_minutes"] == 22
    assert captured["speculation_ttl_days"] == 8
    assert captured["speculation_cooldown_days"] == 9
    assert captured["speculation_confirmation_threshold"] == 4
    assert captured["speculation_max_active"] == 6
    assert captured["speculation_max_primary_interests"] == 17
    assert captured["speculation_max_secondary_interests"] == 66
    assert captured["speculator_idle_interval_minutes"] == 11


def test_build_soul_engine_forwards_feedback_batch_config(monkeypatch) -> None:
    """The CLI surface reads the same feedback knobs the API surface does.

    Regression (found in unified-interest-line Wave A): ``_build_soul_engine``
    never passed ``feedback_batch_threshold`` — the CLI feedback command always
    ran the batch at the hardcoded default of 3 no matter what the user
    configured — and Wave A only plumbed ``unified_interest_line`` through
    ``api/runtime_context.py``, so ``openbiliclaw feedback`` would have stayed
    on the legacy line after the flag flip.
    """
    from openbiliclaw.config import Config

    cfg = Config()
    cfg.scheduler.feedback_batch_threshold = 6
    cfg.scheduler.unified_interest_line = True

    captured: dict[str, object] = {}

    class FakeSoulEngine:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_registry", lambda: object())
    monkeypatch.setattr("openbiliclaw.soul.engine.SoulEngine", FakeSoulEngine)

    cli_module._build_soul_engine()

    assert captured["feedback_batch_threshold"] == 6
    assert captured["unified_interest_line"] is True


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

[storage]
db_path = "data/openbiliclaw.db"
""".strip(),
        encoding="utf-8",
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _ignore_runtime_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "_load_runtime_config_error",
        lambda *, render=True: None,
        raising=False,
    )


def _registered_option_names(command_name: str) -> set[str]:
    click_app = typer.main.get_command(app)
    command = click_app.commands[command_name]
    return {option for param in command.params for option in getattr(param, "opts", [])}


def _ledger_db(tmp_path: Path) -> Any:
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "ledger.db")
    db.initialize()
    return db


def _patch_ledger_runtime(monkeypatch: pytest.MonkeyPatch, db: Any) -> None:
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: db, raising=False)


def test_ledger_command_is_registered(runner: CliRunner) -> None:
    result = runner.invoke(app, ["ledger", "--help"])
    assert result.exit_code == 0
    options = _registered_option_names("ledger")
    assert "--line" in options
    assert "--days" in options
    assert "--write-point" in options


def test_init_command_exposes_force_option(runner: CliRunner) -> None:
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    options = _registered_option_names("init")
    assert "--force" in options
    assert "--reset-cognition" in options
    assert "--no-backup" in options
    assert "重新初始化" in result.output


def test_create_reinit_backup_snapshots_db_and_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Force re-init backup must capture the DB + memory layers, skip .lock."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "openbiliclaw.db"
    db_path.write_bytes(b"fake-sqlite-bytes")
    mem = data_dir / "memory"
    mem.mkdir()
    (mem / "soul.json").write_text('{"personality_portrait":"旧画像"}', encoding="utf-8")
    (mem / "awareness.json").write_text('{"notes":[]}', encoding="utf-8")
    (mem / "soul_profile.json.lock").write_text("", encoding="utf-8")

    monkeypatch.setattr(cli_module, "_runtime_database_path", lambda: db_path)
    monkeypatch.setattr(cli_module, "_runtime_backup_dir", lambda: data_dir / "backups")

    backup_dir = cli_module._create_reinit_backup()
    assert backup_dir is not None
    assert backup_dir.name.startswith("reinit-")
    db_copies = list(backup_dir.glob("*.db"))
    assert len(db_copies) == 1
    assert db_copies[0].read_bytes() == b"fake-sqlite-bytes"
    mem_backup = backup_dir / "memory"
    assert (mem_backup / "soul.json").exists()
    assert (mem_backup / "awareness.json").exists()
    assert not (mem_backup / "soul_profile.json.lock").exists()


def test_create_reinit_backup_returns_none_without_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No DB yet → no backup (fresh install), returns None quietly."""
    db_path = tmp_path / "nope.db"
    monkeypatch.setattr(cli_module, "_runtime_database_path", lambda: db_path)
    monkeypatch.setattr(cli_module, "_runtime_backup_dir", lambda: tmp_path / "backups")
    assert cli_module._create_reinit_backup() is None


def test_ledger_empty_shows_no_data(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_ledger_runtime(monkeypatch, _ledger_db(tmp_path))
    result = runner.invoke(app, ["ledger"])
    assert result.exit_code == 0
    assert "暂无数据" in result.output


def test_ledger_aggregate_mode(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _ledger_db(tmp_path)
    db.insert_profile_ledger(write_point="dialogue_preference_overwrite", source="chat")
    db.insert_profile_ledger(
        write_point="dialogue_soul_rebuild", source="chat", outcome="failed", error="x"
    )
    _patch_ledger_runtime(monkeypatch, db)
    result = runner.invoke(app, ["ledger"])
    assert result.exit_code == 0
    assert "dialogue_preference_overwrite" in result.output
    assert "dialogue_soul_rebuild" in result.output


def test_ledger_line_mode(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _ledger_db(tmp_path)
    db.insert_profile_ledger(
        write_point="init_preference_build",
        source="init",
        source_refs=["events:5"],
        turn_id="",
    )
    _patch_ledger_runtime(monkeypatch, db)
    result = runner.invoke(app, ["ledger", "--line"])
    assert result.exit_code == 0
    assert "init_preference_build" in result.output


def test_ledger_write_point_filter(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = _ledger_db(tmp_path)
    db.insert_profile_ledger(write_point="dialogue_preference_overwrite", source="chat")
    db.insert_profile_ledger(write_point="feedback_preference_overwrite", source="feedback")
    _patch_ledger_runtime(monkeypatch, db)
    result = runner.invoke(app, ["ledger", "--write-point", "feedback_preference_overwrite"])
    assert result.exit_code == 0
    assert "feedback_preference_overwrite" in result.output
    assert "dialogue_preference_overwrite" not in result.output


def test_questions_command_lists_pending_confirmations_read_only(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "_fetch_pending_confirmation_snapshot",
        lambda: {
            "count": 2,
            "items": [
                {
                    "kind": "hypothesis",
                    "ref": "hyp-ref",
                    "title": "你可能更看重一手证据",
                    "confidence": 0.83,
                    "evidence_refs": ["event-7", "event-9"],
                },
                {
                    "kind": "confusion",
                    "ref": "42",
                    "title": "为什么最近跳过熟悉主题",
                    "confidence": 0.61,
                    "evidence_refs": [],
                },
            ],
        },
        raising=False,
    )

    result = runner.invoke(app, ["questions"])

    assert result.exit_code == 0
    assert "待聊确认" in result.output
    assert "猜测" in result.output
    assert "疑惑" in result.output
    assert "你可能更看重一手证据" in result.output
    assert "为什么最近跳过熟悉主题" in result.output
    assert "83%" in result.output
    assert "event-7、event-9" in result.output
    assert "确认" in result.output
    assert "不准" not in result.output


def test_questions_command_reports_empty_pending_list(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "_fetch_pending_confirmation_snapshot",
        lambda: {"count": 0, "items": []},
        raising=False,
    )

    result = runner.invoke(app, ["questions"])

    assert result.exit_code == 0
    assert "暂无待聊确认" in result.output


def test_fetch_pending_confirmation_snapshot_uses_configured_local_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            calls.append(("raise_for_status", None))

        def json(self) -> dict[str, object]:
            return {"count": 1, "items": [{"kind": "hypothesis", "ref": "h1"}]}

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            calls.append(("get", url))
            return FakeResponse()

    def fake_client(**kwargs: object) -> FakeClient:
        calls.append(("client", kwargs))
        return FakeClient()

    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda: SimpleNamespace(api=SimpleNamespace(port=8433)),
    )
    monkeypatch.setattr(httpx, "Client", fake_client)

    snapshot = cli_module._fetch_pending_confirmation_snapshot()

    assert snapshot == {"count": 1, "items": [{"kind": "hypothesis", "ref": "h1"}]}
    assert calls == [
        ("client", {"timeout": 5.0, "trust_env": False}),
        ("get", "http://127.0.0.1:8433/api/chat/pending-confirmations"),
        ("raise_for_status", None),
    ]


def test_keyword_inspiration_dry_run_command_is_registered(runner: CliRunner) -> None:
    result = runner.invoke(app, ["keyword-inspiration-dry-run", "--help"])

    assert result.exit_code == 0
    assert "keyword-inspiration-dry-run" in result.output
    options = _registered_option_names("keyword-inspiration-dry-run")
    assert "--platform" in options
    assert "--interest-limit" in options


def test_keyword_inspiration_preview_command_exposes_persist_axes(
    runner: CliRunner,
) -> None:
    result = runner.invoke(app, ["keyword-inspiration-preview", "--help"])

    assert result.exit_code == 0
    assert "keyword-inspiration-preview" in result.output
    assert "--persist-axes" in _registered_option_names("keyword-inspiration-preview")


def test_keyword_inspiration_preview_threads_persist_axes(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    cfg = config_module.Config()
    captured: dict[str, object] = {}

    class FakeLLMService:
        def __init__(self, **_: object) -> None:
            pass

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                preferences=PreferenceLayer(
                    interests=[InterestTag(name="游戏评价", category="游戏", weight=0.9)]
                )
            )

    class FakePlanner:
        def __init__(self, **_: object) -> None:
            pass

        async def preview_inspiration_keywords(
            self,
            platforms: list[str],
            *,
            profile: SoulProfile | None = None,
            query_kind: str = "regular",
            persist_axes: bool = False,
        ) -> dict[str, object]:
            captured["platforms"] = platforms
            captured["query_kind"] = query_kind
            captured["persist_axes"] = persist_axes
            captured["profile"] = profile
            return {"ok": True}

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_usage_recorder", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: object(), raising=False)
    monkeypatch.setattr(
        "openbiliclaw.llm.service.LLMService",
        FakeLLMService,
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.llm.service.module_overrides_from_config",
        lambda loaded_cfg: {},
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_planner.KeywordPlanner",
        FakePlanner,
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_inspiration_search_provider",
        lambda *args, **kwargs: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_platform_source_backends",
        lambda *args, **kwargs: {},
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "keyword-inspiration-preview",
            "--platform",
            "bilibili",
            "--kind",
            "explore",
            "--persist-axes",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True}
    assert captured["platforms"] == ["bilibili"]
    assert captured["query_kind"] == "explore"
    assert captured["persist_axes"] is True


def test_keyword_inspiration_preview_supports_v2ex_platform_grounding(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    cfg = config_module.Config()
    cfg.sources.v2ex.enabled = True
    captured: dict[str, object] = {}

    class FakeLLMService:
        def __init__(self, **_: object) -> None:
            pass

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                preferences=PreferenceLayer(
                    interests=[InterestTag(name="Agent", category="技术", weight=0.9)]
                )
            )

    class FakeV2EXClient:
        def __init__(self, **_: object) -> None:
            captured["client"] = self

        async def aclose(self) -> None:
            captured["closed"] = True

    class FakePlanner:
        def __init__(self, **_: object) -> None:
            pass

        async def preview_inspiration_keywords(
            self,
            platforms: list[str],
            *,
            profile: SoulProfile | None = None,
            query_kind: str = "regular",
            persist_axes: bool = False,
        ) -> dict[str, object]:
            captured["platforms"] = platforms
            return {"ok": True}

    def build_platform_backends(*_: object, **kwargs: object) -> list[object]:
        captured["v2ex_client"] = kwargs.get("v2ex_client")
        return []

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_usage_recorder", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: object(), raising=False)
    monkeypatch.setattr("openbiliclaw.llm.service.LLMService", FakeLLMService, raising=False)
    monkeypatch.setattr(
        "openbiliclaw.llm.service.module_overrides_from_config",
        lambda loaded_cfg: {},
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_planner.KeywordPlanner",
        FakePlanner,
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.v2ex_client.V2EXClient",
        FakeV2EXClient,
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_inspiration_search_provider",
        lambda *args, **kwargs: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_platform_source_backends",
        build_platform_backends,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["keyword-inspiration-preview", "--platform", "v2ex", "--limit", "1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True}
    assert captured["platforms"] == ["v2ex"]
    assert captured["v2ex_client"] is captured["client"]
    assert captured["closed"] is True


def test_keyword_inspiration_report_command_is_registered(runner: CliRunner) -> None:
    result = runner.invoke(app, ["keyword-inspiration-report", "--help"])

    assert result.exit_code == 0
    assert "keyword-inspiration-report" in result.output
    assert "--window-days" in _registered_option_names("keyword-inspiration-report")


def test_keyword_inspiration_preview_one_shot_overrides_apply_on_derived_params(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    """Post config-collapse, --limit / --interest-limit apply as one-shot
    overrides on the derived breadth params injected at planner construction —
    the user-visible flag behavior is unchanged (Spec AC6b)."""
    cfg = config_module.Config()
    captured: dict[str, object] = {}

    class FakeLLMService:
        def __init__(self, **_: object) -> None:
            pass

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                preferences=PreferenceLayer(
                    interests=[InterestTag(name="游戏评价", category="游戏", weight=0.9)]
                )
            )

    class FakePlanner:
        def __init__(self, **kwargs: object) -> None:
            captured["inspiration_params"] = kwargs.get("inspiration_params")

        async def preview_inspiration_keywords(
            self,
            platforms: list[str],
            *,
            profile: SoulProfile | None = None,
            query_kind: str = "regular",
            persist_axes: bool = False,
        ) -> dict[str, object]:
            return {"ok": True}

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_build_usage_recorder", lambda: None, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: object(), raising=False)
    monkeypatch.setattr("openbiliclaw.llm.service.LLMService", FakeLLMService, raising=False)
    monkeypatch.setattr(
        "openbiliclaw.llm.service.module_overrides_from_config",
        lambda loaded_cfg: {},
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_planner.KeywordPlanner",
        FakePlanner,
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_inspiration_search_provider",
        lambda *args, **kwargs: object(),
        raising=False,
    )
    monkeypatch.setattr(
        "openbiliclaw.discovery.inspiration_provider.build_platform_source_backends",
        lambda *args, **kwargs: {},
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "keyword-inspiration-preview",
            "--platform",
            "bilibili",
            "--limit",
            "3",
            "--interest-limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    params = captured["inspiration_params"]
    assert params is not None
    assert params.max_keywords_per_platform == 3  # type: ignore[union-attr]
    assert params.interest_sample_size == 2  # type: ignore[union-attr]
    # Non-overridden knobs stay at the derived default-tier (high) values.
    base = config_module.derive_inspiration_breadth_params("high")
    assert params.max_probe_searches_per_stage == base.max_probe_searches_per_stage  # type: ignore[union-attr]
    assert params.search_results_per_query == base.search_results_per_query  # type: ignore[union-attr]


def test_main_bootstraps_container_runtime_when_project_root_is_configured(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=tmp_path / "bilibili_cookie.json",
                message="未配置 B 站 Cookie。",
            )

    called: list[bool] = []
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        cli_module,
        "_bootstrap_container_runtime",
        lambda: called.append(True),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert called == [True]


def test_config_show_generates_template_and_prints_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    _write_example_config(tmp_path)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert (tmp_path / "config.toml").exists()
    assert "当前配置" in result.stdout
    assert "已自动生成" in result.stdout
    assert "llm.openai.api_key" in result.stdout


def test_config_export_legacy_writes_verified_private_copy_with_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    cfg = config_module.Config(
        llm=config_module.LLMConfig(
            instance_routing=True,
            instances={
                "primary": config_module.LLMInstanceConfig(
                    name="主渠道",
                    provider_type="openai_compatible",
                    api_key="sk-primary",
                    model="model-a",
                    base_url="https://primary.example/v1",
                ),
                "same-type-backup": config_module.LLMInstanceConfig(
                    name="同类型备选",
                    provider_type="openai_compatible",
                    api_key="sk-backup",
                    model="model-b",
                    base_url="https://backup.example/v1",
                ),
                "deepseek-backup": config_module.LLMInstanceConfig(
                    name="跨类型备选",
                    provider_type="deepseek",
                    api_key="sk-deepseek",
                    model="deepseek-chat",
                ),
            },
            default_chain=["primary", "same-type-backup", "deepseek-backup"],
        )
    )
    config_path = tmp_path / "config.toml"
    config_module.save_config(cfg, config_path)
    original = config_path.read_bytes()
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-export-legacy"])

    export_path = tmp_path / "config.legacy.toml"
    assert result.exit_code == 0, result.output
    assert config_path.read_bytes() == original
    assert export_path.exists()
    assert export_path.stat().st_mode & 0o777 == 0o600
    assert config_module.load_config(export_path).llm.instance_routing is False
    text = export_path.read_text(encoding="utf-8")
    assert "routing_version" not in text
    assert "[llm.openai_compatible]" in text
    assert "sk-primary" in text
    assert "旧版配置已导出" in result.output
    assert "旧格式每种 Provider" in result.output
    assert "只能保留一个端点" in result.output
    assert "全局链最多只有" in result.output
    assert "一个不同类型备选" in result.output
    assert "明文凭据" in result.output


def test_config_export_legacy_refuses_overwrite_without_force_and_active_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config_path = tmp_path / "config.toml"
    config_module.save_config(
        config_module.Config(
            llm=config_module.LLMConfig(
                default_provider="ollama",
                ollama=config_module.LLMProviderConfig(model="qwen2.5:7b"),
            )
        ),
        config_path,
    )
    export_path = tmp_path / "config.legacy.toml"
    export_path.write_text("do not overwrite", encoding="utf-8")
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    existing = runner.invoke(app, ["config-export-legacy"])
    same_path = runner.invoke(
        app,
        ["config-export-legacy", "--output", str(config_path), "--force"],
    )

    assert existing.exit_code == 1
    assert "--force" in existing.output
    assert export_path.read_text(encoding="utf-8") == "do not overwrite"
    assert same_path.exit_code == 1
    assert "不能直接覆盖当前配置" in same_path.output

    forced = runner.invoke(app, ["config-export-legacy", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "do not overwrite" not in export_path.read_text(encoding="utf-8")


def test_recommend_reports_clear_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    monkeypatch.setattr(config_module, "_PROJECT_ROOT", tmp_path)
    _write_example_config(tmp_path)

    result = runner.invoke(app, ["recommend"])

    assert result.exit_code == 1
    assert "配置错误" in result.stdout
    assert "llm.openai.api_key" in result.stdout


def test_config_show_displays_registered_providers(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeRegistry:
        default_provider = "claude"
        available_providers = ["claude", "ollama"]

    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert "已注册 Provider" in result.stdout
    assert "claude, ollama" in result.stdout
    assert "最终默认 Provider" in result.stdout
    assert "claude" in result.stdout


def test_config_show_displays_runtime_pause_fields(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    cfg = config_module.Config()
    cfg.scheduler.enabled = False
    cfg.scheduler.pause_on_extension_disconnect = True
    cfg.scheduler.extension_disconnect_grace_seconds = 45

    class FakeRegistry:
        default_provider = "openai"
        available_providers = ["openai"]

    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert "停止后台 LLM 请求" in result.stdout
    assert "是" in result.stdout
    assert "浏览器断开后暂停" in result.stdout
    assert "开启（宽限 45s）" in result.stdout


def test_config_show_displays_bilibili_publication_preference(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    cfg = config_module.Config()
    cfg.sources.bilibili.recommendation_date_preset = "custom"
    cfg.sources.bilibili.recommendation_date_start = "2023-01-01"
    cfg.sources.bilibili.recommendation_date_end = "2023-12-31"
    cfg.sources.bilibili.recommendation_date_weight = 0.5

    class FakeRegistry:
        default_provider = "openai"
        available_providers = ["openai"]

    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert "B站发布日期范围" in result.stdout
    assert "自定义：2023-01-01 至 2023-12-31" in result.stdout
    assert "B站发布日期权重" in result.stdout
    assert "0.5" in result.stdout


def test_config_show_displays_saved_auto_sync_status(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    cfg = config_module.Config()

    class FakeRegistry:
        default_provider = "openai"
        available_providers = ["openai"]

    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert "收藏自动同步" in result.stdout
    assert "关闭" in result.stdout


def test_health_check_reports_provider_statuses(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeResult:
        def __init__(self, available: bool, is_default: bool, error: str | None = None) -> None:
            self.available = available
            self.is_default = is_default
            self.error = error

    class FakeRegistry:
        async def health_check_all(self) -> dict[str, FakeResult]:
            return {
                "openai": FakeResult(True, True),
                "ollama": FakeResult(False, False, "connection refused"),
            }

    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["health-check"])

    assert result.exit_code == 0
    assert "Provider 健康检查" in result.stdout
    assert "openai" in result.stdout
    assert "可用" in result.stdout
    assert "connection refused" in result.stdout


def test_auth_login_accepts_interactive_cookie_and_saves_on_success(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        def __init__(self) -> None:
            self.saved_cookie: str | None = None

        async def validate_cookie(self, cookie: str) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

        def set_cookie(self, cookie: str) -> None:
            self.saved_cookie = cookie

    fake_manager = FakeAuthManager()
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: fake_manager, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["auth", "login"], input="SESSDATA=abc123\n")

    assert result.exit_code == 0
    assert fake_manager.saved_cookie == "SESSDATA=abc123"
    assert "登录成功" in result.stdout
    assert "alice" in result.stdout


def test_login_codex_status_reports_credentials(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    from openbiliclaw.llm.codex_auth import CodexCredentials

    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.load_codex_credentials",
        lambda: CodexCredentials(
            access_token="secret-access",
            refresh_token="secret-refresh",
            expires_at=4_102_444_800.0,
            account_id="acct_test",
        ),
    )

    result = runner.invoke(app, ["login", "codex", "--status"])

    assert result.exit_code == 0
    assert "已登录" in result.stdout
    assert "acct_test" in result.stdout
    assert "secret-access" not in result.stdout
    assert "secret-refresh" not in result.stdout


def test_login_codex_import_uses_source_path(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm.codex_auth import CodexCredentials

    source = tmp_path / "auth.json"
    calls: list[Path | None] = []

    def fake_import(*, source=None, destination=None) -> CodexCredentials:
        calls.append(source)
        return CodexCredentials("access", "refresh", 4_102_444_800.0, "acct_imported")

    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr("openbiliclaw.llm.codex_auth.import_codex_credentials", fake_import)

    result = runner.invoke(app, ["login", "codex", "--import", "--source", str(source)])

    assert result.exit_code == 0
    assert calls == [source]
    assert "acct_imported" in result.stdout


def test_login_codex_logout_deletes_local_credentials(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    calls: list[bool] = []

    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(
        "openbiliclaw.llm.codex_auth.delete_codex_credentials",
        lambda: calls.append(True) or True,
    )

    result = runner.invoke(app, ["login", "codex", "--logout"])

    assert result.exit_code == 0
    assert calls == [True]
    assert "已登出" in result.stdout


def test_auth_login_does_not_save_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        def __init__(self) -> None:
            self.saved_cookie = False

        async def validate_cookie(self, cookie: str) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=False,
                cookie_path=tmp_path / "bilibili_cookie.json",
                message="cookie 已过期",
            )

        def set_cookie(self, cookie: str) -> None:
            self.saved_cookie = True

    fake_manager = FakeAuthManager()
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: fake_manager, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["auth", "login", "--cookie", "SESSDATA=expired"])

    assert result.exit_code == 1
    assert fake_manager.saved_cookie is False
    assert "认证失败" in result.stdout
    assert "已过期" in result.stdout


def test_auth_status_reports_missing_cookie(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=tmp_path / "bilibili_cookie.json",
                message="未配置 B 站 Cookie。",
            )

    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "未配置" in result.stdout


def test_auth_status_reports_authenticated_user(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    assert "认证概览" in result.stdout
    assert "已认证" in result.stdout
    assert "alice" in result.stdout
    assert "10086" in result.stdout


def test_browser_status_reports_install_guidance_when_missing(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeBrowser:
        executable = "agent-browser"
        is_available = False

        @staticmethod
        def get_install_hint() -> str:
            return "npm install -g agent-browser"

    monkeypatch.setattr(cli_module, "_build_browser", lambda: FakeBrowser(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["browser", "status"])

    assert result.exit_code == 1
    assert "浏览器集成状态" in result.stdout
    assert "未安装" in result.stdout
    assert "npm install -g agent-browser" in result.stdout


def test_browser_open_reports_navigation_success(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeBrowser:
        executable = "/tmp/agent-browser"
        is_available = True

        @staticmethod
        def get_install_hint() -> str:
            return ""

        async def navigate(self, url: str) -> dict[str, object]:
            return {"success": True, "url": url}

    monkeypatch.setattr(cli_module, "_build_browser", lambda: FakeBrowser(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["browser", "open", "https://example.com"])

    assert result.exit_code == 0
    assert "浏览器已打开" in result.stdout
    assert "https://example.com" in result.stdout


def test_browser_content_reports_command_failure(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeBrowser:
        executable = "/tmp/agent-browser"
        is_available = True

        @staticmethod
        def get_install_hint() -> str:
            return ""

        async def get_page_content(self, url: str) -> str:
            raise BrowserCommandError("snapshot failed")

    monkeypatch.setattr(cli_module, "_build_browser", lambda: FakeBrowser(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["browser", "content", "https://example.com"])

    assert result.exit_code == 1
    assert "浏览器操作失败" in result.stdout
    assert "snapshot failed" in result.stdout


def test_server_migration_guard_locks_journal_target_data_before_loading_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash recovery is bound to the journal path even when config is absent."""
    from openbiliclaw.storage import migration

    project_root = tmp_path / "profile"
    migration_root = project_root / ".openbiliclaw-migration"
    migration_root.mkdir(parents=True)
    target_data = tmp_path / "relocated" / "canonical-data"
    migration_id = "d" * 32
    token = migration_id[:12]
    target_config = project_root / "config.toml"
    target_local = project_root / "config.local.toml"
    config_backup = project_root / f"config.toml.pre-import-{token}.bak"
    local_backup = project_root / f"config.local.toml.pre-import-{token}.bak"
    data_backup = target_data.with_name(f"{target_data.name}.pre-import-{token}.bak")
    prepared_config = project_root / f".config.toml.import-{token}"
    prepared_data = target_data.with_name(f".{target_data.name}.import-{token}")
    config_backup.write_text("[general]\ndata_dir = 'data'\n", encoding="utf-8")
    data_backup.mkdir(parents=True)
    (data_backup / "restored.txt").write_text("old target data", encoding="utf-8")
    (migration_root / "apply-journal.json").write_text(
        json.dumps(
            {
                "state": "applying",
                "migration_id": migration_id,
                "target_config": str(target_config.resolve()),
                "target_local": str(target_local.resolve()),
                "target_data": str(target_data.resolve()),
                "prepared_config": str(prepared_config.resolve()),
                "prepared_data": str(prepared_data.resolve()),
                "config_backup": str(config_backup.resolve()),
                "local_backup": str(local_backup.resolve()),
                "data_backup": str(data_backup.resolve()),
                "target_config_existed": True,
                "target_local_existed": False,
                "target_data_existed": True,
                "new_config_active": False,
                "new_data_active": False,
            }
        ),
        encoding="utf-8",
    )
    assert not target_config.exists()
    assert not target_data.exists()

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))

    events: list[tuple[str, Path]] = []
    applied: list[tuple[Path, Path]] = []
    real_acquire = migration.acquire_migration_runtime_guard

    def _load_recovered_config() -> SimpleNamespace:
        events.append(("load-config", target_data.resolve()))
        return SimpleNamespace(data_path=target_data.resolve())

    def _acquire_guard(project_root: Path, data_dir: Path):
        events.append(("guard", data_dir))
        return real_acquire(project_root, data_dir)

    def _apply_pending(*, project_root: Path, locked_data_dir: Path) -> None:
        events.append(("apply", locked_data_dir))
        applied.append((project_root, locked_data_dir))

    monkeypatch.setattr(config_module, "load_config", _load_recovered_config)
    monkeypatch.setattr(migration, "acquire_migration_runtime_guard", _acquire_guard)
    monkeypatch.setattr(migration, "apply_pending_migration", _apply_pending)

    guard = cli_module._acquire_server_migration_guard()
    try:
        assert guard.paths == (
            migration_root / "runtime.lock",
            target_data.parent / f".{target_data.name}.openbiliclaw-runtime.lock",
        )
        assert applied == [(project_root.resolve(), target_data.resolve())]
        assert events == [
            ("guard", target_data.resolve()),
            ("apply", target_data.resolve()),
            ("load-config", target_data.resolve()),
        ]
    finally:
        guard.release()


def test_start_uses_lan_accessible_api_defaults(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}
    backup_calls: list[str] = []

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_maybe_create_runtime_database_backup",
        lambda: backup_calls.append("called"),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert "启动 OpenBiliClaw" in result.stdout
    assert "API 服务" in result.stdout
    assert backup_calls == ["called"]
    assert called == {"host": "0.0.0.0", "port": 8420}


def test_start_creates_backup_before_guided_init_opens_runtime_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config_module.Config()
    events: list[str] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli_module,
        "_ensure_runtime_database_healthy",
        lambda: events.append("health"),
    )
    monkeypatch.setattr(
        cli_module,
        "_maybe_create_runtime_database_backup",
        lambda: events.append("backup"),
    )
    monkeypatch.setattr(
        cli_module,
        "_guided_init_completed_best_effort",
        lambda: events.append("guided-init") or True,
    )
    monkeypatch.setattr(cli_module, "_warn_if_pause_on_disconnect_requires_presence", lambda: None)
    monkeypatch.setattr(cli_module, "_preflight_loopback_ollama", lambda _cfg: None)
    monkeypatch.setattr(cli_module, "_self_heal_autostart_registration", lambda _cfg: None)
    monkeypatch.setattr(
        cli_module,
        "_run_api_server",
        lambda *, host, port: events.append(f"server:{host}:{port}"),
    )

    cli_module._start_agent(host="", port=0)

    assert events == ["health", "backup", "guided-init", "server:0.0.0.0:8420"]


def test_start_uses_configured_api_host_and_port(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}
    cfg = config_module.Config()
    cfg.api.host = "127.0.0.1"
    cfg.api.port = 19090

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert called == {"host": "127.0.0.1", "port": 19090}
    assert "127.0.0.1:19090" in result.stdout


def test_start_warns_when_pause_on_disconnect_requires_extension_presence(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}
    cfg = config_module.Config()
    cfg.scheduler.pause_on_extension_disconnect = True

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert "WARN extension presence required" in result.stdout
    assert "background LLM work after grace period" in result.stdout
    assert called == {"host": "0.0.0.0", "port": 8420}


def test_start_preflight_starts_default_loopback_ollama(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    cfg = config_module.Config()
    probes: list[str] = []
    starts: list[str] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "ollama_required", lambda loaded_cfg: True, raising=False)
    monkeypatch.setattr(
        cli_module,
        "effective_ollama_endpoint",
        lambda loaded_cfg: "http://localhost:11434",
        raising=False,
    )
    monkeypatch.setattr(cli_module, "is_loopback", lambda endpoint: True, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_ollama_is_running",
        lambda host="http://localhost:11434": probes.append(host) or False,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_ollama_start_serve_background",
        lambda: starts.append("serve") or True,
        raising=False,
    )

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert probes == ["http://localhost:11434"]
    assert starts == ["serve"]


def test_start_preflight_custom_loopback_port_does_not_serve(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    cfg = config_module.Config()
    starts: list[str] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "ollama_required", lambda loaded_cfg: True, raising=False)
    monkeypatch.setattr(
        cli_module,
        "effective_ollama_endpoint",
        lambda loaded_cfg: "http://127.0.0.1:9999",
        raising=False,
    )
    monkeypatch.setattr(cli_module, "is_loopback", lambda endpoint: True, raising=False)
    monkeypatch.setattr(cli_module, "_ollama_is_running", lambda host: False, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_ollama_start_serve_background",
        lambda: starts.append("serve") or True,
        raising=False,
    )

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert starts == []


def test_start_self_heal_skips_autostart_register_when_env_managed(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart import guards
    from openbiliclaw.runtime.autostart.base import AutostartStatus

    cfg = config_module.Config()
    cfg.autostart.enabled = True
    register_calls: list[config_module.Config] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "ollama_required", lambda loaded_cfg: False, raising=False)
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: AutostartStatus(True, False, "darwin", "launchd"),
    )
    monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: ["GOOGLE_API_KEY"])
    monkeypatch.setattr(autostart, "register", lambda loaded_cfg: register_calls.append(loaded_cfg))

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert register_calls == []


def test_start_self_heals_missing_autostart_registration(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart import guards
    from openbiliclaw.runtime.autostart.base import AutostartStatus

    cfg = config_module.Config()
    cfg.autostart.enabled = True
    register_calls: list[config_module.Config] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "ollama_required", lambda loaded_cfg: False, raising=False)
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: AutostartStatus(True, False, "darwin", "launchd"),
    )
    monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
    monkeypatch.setattr(autostart, "register", lambda loaded_cfg: register_calls.append(loaded_cfg))

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert register_calls == [cfg]


def test_start_self_heals_orphan_autostart_registration(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart.base import AutostartStatus

    cfg = config_module.Config()
    cfg.autostart.enabled = False
    unregister_calls: list[str] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_ensure_runtime_database_healthy", lambda: None)
    monkeypatch.setattr(cli_module, "_maybe_create_runtime_database_backup", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "ollama_required", lambda loaded_cfg: False, raising=False)
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: AutostartStatus(True, True, "darwin", "launchd"),
    )
    monkeypatch.setattr(autostart, "unregister", lambda: unregister_calls.append("unregister"))

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert unregister_calls == ["unregister"]


def test_autostart_cli_enable_registers_after_authoritative_config_write(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart import guards

    cfg = config_module.Config()
    calls: list[tuple[str, bool | object, bool | None]] = []

    class FakeManager:
        mechanism = "launchd"
        registered = False

        def register(self, loaded_cfg: config_module.Config) -> None:
            calls.append(("register", loaded_cfg.autostart.enabled, None))
            self.registered = True

        def unregister(self) -> None:
            calls.append(("unregister", False, None))
            self.registered = False

        def is_registered(self) -> bool:
            return self.registered

    manager = FakeManager()

    def fake_save(
        loaded_cfg: config_module.Config,
        config_path: Path | None = None,
        *,
        autostart_authoritative: bool = False,
    ) -> None:
        calls.append(("save", loaded_cfg.autostart.enabled, autostart_authoritative))

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(config_module, "save_config", fake_save, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(autostart, "get_manager", lambda: manager)
    monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
    monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)

    result = runner.invoke(app, ["autostart", "enable"])

    assert result.exit_code == 0
    assert calls == [
        ("save", True, True),
        ("register", True, None),
    ]
    assert manager.registered is True
    assert "已开启" in result.stdout


def test_autostart_cli_disable_unregisters_before_authoritative_config_write(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart import guards

    cfg = config_module.Config()
    cfg.autostart.enabled = True
    calls: list[tuple[str, bool | object, bool | None]] = []

    class FakeManager:
        mechanism = "launchd"
        registered = True

        def register(self, loaded_cfg: config_module.Config) -> None:
            calls.append(("register", loaded_cfg.autostart.enabled, None))
            self.registered = True

        def unregister(self) -> None:
            calls.append(("unregister", True, None))
            self.registered = False

        def is_registered(self) -> bool:
            return self.registered

    manager = FakeManager()

    def fake_save(
        loaded_cfg: config_module.Config,
        config_path: Path | None = None,
        *,
        autostart_authoritative: bool = False,
    ) -> None:
        calls.append(("save", loaded_cfg.autostart.enabled, autostart_authoritative))

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(config_module, "save_config", fake_save, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(autostart, "get_manager", lambda: manager)
    monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)

    result = runner.invoke(app, ["autostart", "disable"])

    assert result.exit_code == 0
    assert calls == [
        ("unregister", True, None),
        ("save", False, True),
    ]
    assert manager.registered is False
    assert "已关闭" in result.stdout


def test_autostart_cli_enable_rejects_env_managed_inputs(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart import guards

    cfg = config_module.Config()
    calls: list[str] = []

    class FakeManager:
        mechanism = "launchd"

        def register(self, loaded_cfg: config_module.Config) -> None:
            calls.append("register")

        def unregister(self) -> None:
            calls.append("unregister")

        def is_registered(self) -> bool:
            return False

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda *args, **kwargs: calls.append("save"),
        raising=False,
    )
    monkeypatch.setattr(autostart, "get_manager", lambda: FakeManager())
    monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: ["GOOGLE_API_KEY"])
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["autostart", "enable"])

    assert result.exit_code == 1
    assert calls == []
    assert "GOOGLE_API_KEY" in result.stdout


def test_autostart_cli_enable_rejects_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart

    cfg = config_module.Config()
    calls: list[str] = []

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(
        config_module,
        "save_config",
        lambda *args, **kwargs: calls.append("save"),
        raising=False,
    )
    monkeypatch.setattr(autostart, "get_manager", lambda: None)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["autostart", "enable"])

    assert result.exit_code == 1
    assert calls == []
    assert "不支持" in result.stdout


def test_autostart_cli_status_reports_current_state(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart.base import AutostartStatus

    cfg = config_module.Config()
    cfg.autostart.enabled = True

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: AutostartStatus(True, True, "darwin", "launchd"),
    )

    result = runner.invoke(app, ["autostart", "status"])

    assert result.exit_code == 0
    assert "开机自启动" in result.stdout
    assert "已注册" in result.stdout
    assert "launchd" in result.stdout


def test_config_show_displays_autostart_status(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.runtime import autostart
    from openbiliclaw.runtime.autostart.base import AutostartStatus

    cfg = config_module.Config()
    cfg.autostart.enabled = True

    class FakeRegistry:
        default_provider = "openai"
        available_providers = ["openai"]

    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        autostart,
        "status",
        lambda: AutostartStatus(True, True, "darwin", "launchd"),
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["config-show"])

    assert result.exit_code == 0
    assert "开机自启动" in result.stdout
    assert "已注册" in result.stdout


def test_embedding_cache_stats_reports_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.llm.embedding import EmbeddingCache

    cfg = config_module.Config()
    cfg.data_dir = str(tmp_path)
    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    cache = EmbeddingCache(tmp_path / "embedding_cache.db")
    cache.initialize()
    cache.put("k1", [0.1, 0.2, 0.3], model="bge-m3#namespace=abc123")
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('k2', 'bge-m3', ?, 0)",
        ("[0.4, 0.5, 0.6]",),
    )
    cache.conn.commit()
    cache.close()

    result = runner.invoke(app, ["embedding-cache-stats"])

    assert result.exit_code == 0, result.output
    assert "Embedding L2 缓存诊断" in result.stdout
    assert "bge-m3#namespace=abc123" in result.stdout
    assert "legacy" in result.stdout
    assert "总行数" in result.stdout


def test_embedding_cache_clean_dry_run_then_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.llm.embedding import EmbeddingCache

    cfg = config_module.Config()
    cfg.data_dir = str(tmp_path)
    monkeypatch.setattr(
        config_module,
        "load_config_with_diagnostics",
        lambda: (cfg, config_module.ConfigDiagnostics()),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    cache = EmbeddingCache(tmp_path / "embedding_cache.db")
    cache.initialize()
    cache.put("k1", [0.1, 0.2, 0.3], model="m#namespace=live")
    cache.conn.execute(
        "INSERT INTO embedding_cache (text_key, model, vector, encoding) "
        "VALUES ('k2', 'bge-m3', ?, 0)",
        ("[0.4, 0.5, 0.6]",),
    )
    cache.conn.commit()
    cache.close()

    # Default dry-run: reports what would change, touches nothing.
    result = runner.invoke(app, ["embedding-cache-clean"])
    assert result.exit_code == 0, result.output
    assert "dry-run" in result.stdout
    reopen = EmbeddingCache(tmp_path / "embedding_cache.db")
    reopen.initialize()
    assert reopen.count() == 2
    reopen.close()

    # --apply: migrates the JSON row, deletes the unprotected legacy row and
    # physically compacts the file.
    result = runner.invoke(
        app, ["embedding-cache-clean", "--apply", "--keep-model", "m#namespace=live"]
    )
    assert result.exit_code == 0, result.output
    assert "迁移 1 行" in result.stdout
    assert "删除 1 行" in result.stdout
    reopen = EmbeddingCache(tmp_path / "embedding_cache.db")
    reopen.initialize()
    assert reopen.count() == 1
    assert reopen.get("k1", model="m#namespace=live") is not None
    row = reopen.conn.execute(
        "SELECT encoding FROM embedding_cache WHERE text_key = 'k1'"
    ).fetchone()
    assert row[0] == 1
    reopen.close()


def test_run_api_server_prints_degraded_mode_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.api import app as api_app

    output = io.StringIO()
    fake_app = SimpleNamespace(
        state=SimpleNamespace(
            degraded=True,
            degraded_reason="llm_registry_unavailable",
            degraded_issues=[
                SimpleNamespace(
                    field="llm",
                    message="LLM registry unavailable: missing api key",
                    severity="blocking",
                )
            ],
        )
    )
    run_calls: list[dict[str, object]] = []
    # This unit test exercises the API server body only; the default
    # four-process worker mode is covered by startup integration tests.
    monkeypatch.setenv("OPENBILICLAW_WORKER", "0")

    monkeypatch.setattr(
        cli_module,
        "console",
        Console(file=output, force_terminal=False, width=120),
        raising=False,
    )
    monkeypatch.setattr(api_app, "create_app", lambda: fake_app)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **kwargs: run_calls.append({"app": app, **kwargs}),
    )

    cli_module._run_api_server(host="127.0.0.1", port=8420)

    rendered = output.getvalue()
    assert "降级模式" in rendered or "Degraded mode" in rendered
    assert "llm_registry_unavailable" in rendered
    assert "missing api key" in rendered
    assert "extension popup settings" in rendered
    assert run_calls and run_calls[0]["app"] is fake_app


def test_start_refuses_unhealthy_database(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    server_calls: list[str] = []
    backup_calls: list[str] = []

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        server_calls.append(f"{host}:{port}")

    def fake_ensure_runtime_database_healthy() -> None:
        raise typer.Exit(code=1)

    monkeypatch.setattr(
        cli_module,
        "_ensure_runtime_database_healthy",
        fake_ensure_runtime_database_healthy,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_maybe_create_runtime_database_backup",
        lambda: backup_calls.append("called"),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 1
    assert backup_calls == []
    assert server_calls == []


def test_db_repair_reports_healthy_database(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class _Result:
        status = "healthy"
        message = "数据库完整，无需修复。"
        repaired_db = None
        db_backup = None
        wal_backup = None

    monkeypatch.setattr(cli_module, "_run_db_repair", lambda: _Result(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["db-repair"])

    assert result.exit_code == 0
    assert "数据库完整，无需修复。" in result.stdout


def test_db_repair_rejects_database_in_use(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class _Result:
        status = "in_use"
        message = "数据库仍在被这些进程占用：python:86577"
        repaired_db = None
        db_backup = None
        wal_backup = None

    monkeypatch.setattr(cli_module, "_run_db_repair", lambda: _Result(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["db-repair"])

    assert result.exit_code == 1
    assert "python:86577" in result.stdout


def test_db_repair_reports_successful_rebuild(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class _Result:
        status = "repaired"
        message = "数据库已恢复并完成切换。"
        repaired_db = tmp_path / "openbiliclaw.repaired.db"
        db_backup = tmp_path / "backups" / "openbiliclaw-20260315-020000.db"
        wal_backup = None

    monkeypatch.setattr(cli_module, "_run_db_repair", lambda: _Result(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["db-repair"])

    assert result.exit_code == 0
    assert "数据库已恢复并完成切换。" in result.stdout
    assert "openbiliclaw.repaired.db" in result.stdout.replace("\n", "")


@pytest.mark.parametrize(
    ("configured_copy_target", "expected_copy_target"),
    [(47, 30), (0, 0)],
)
def test_runtime_builders_share_database_instance(
    monkeypatch: pytest.MonkeyPatch,
    configured_copy_target: int,
    expected_copy_target: int,
) -> None:
    from types import SimpleNamespace

    import openbiliclaw.discovery.engine as discovery_module
    import openbiliclaw.discovery.strategies.strategies as strategy_module
    import openbiliclaw.llm.service as llm_service_module
    import openbiliclaw.memory.manager as memory_module
    import openbiliclaw.recommendation.engine as recommendation_module
    import openbiliclaw.soul.engine as soul_module
    import openbiliclaw.storage.database as database_module

    created_databases: list[object] = []
    created_memories: list[object] = []

    class FakeDatabase:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.initialized = 0
            created_databases.append(self)

        def initialize(self) -> None:
            self.initialized += 1

    class FakeMemoryManager:
        def __init__(self, data_path: Path, database: object | None = None) -> None:
            self.data_path = data_path
            self.database = database
            self.initialized = 0
            created_memories.append(self)

        def initialize(self) -> None:
            self.initialized += 1

    class FakeLLMService:
        def __init__(
            self,
            *,
            registry: object,
            memory: object,
            usage_recorder: object | None = None,
            module_overrides: object | None = None,
            concurrency: int = 1,
            concurrency_gate: object | None = None,
        ) -> None:
            self.registry = registry
            self.memory = memory
            self.usage_recorder = usage_recorder
            self.module_overrides = module_overrides
            self.concurrency = concurrency
            self.concurrency_gate = concurrency_gate

    class FakeRecommendationEngine:
        def __init__(
            self,
            *,
            llm: object,
            database: object,
            embedding_service: object = None,
            xhs_self_info_provider: object = None,
            **_extras: object,
        ) -> None:
            self.llm = llm
            self.database = database
            self.kwargs = _extras

    class FakeDiscoveryEngine:
        def __init__(
            self,
            *,
            llm_service: object,
            database: object,
            concurrency: object | None = None,
            embedding_service: object | None = None,
            **_extras: object,
        ) -> None:
            self.llm_service = llm_service
            self.database = database
            self.concurrency = concurrency
            self.strategies: list[object] = []

        def register_strategy(self, strategy: object) -> None:
            self.strategies.append(strategy)

    class FakeStrategy:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeSoulEngine:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openbiliclaw-test-data"),
        bilibili=SimpleNamespace(cookie=""),
        llm=SimpleNamespace(concurrency=3),
        soul=SimpleNamespace(
            preference=SimpleNamespace(satisfaction_filter_enabled=True),
            preference_prompt_view="compact-v1",
            awareness_prompt_view="legacy",
            insight_prompt_view="compact-v1",
            posture_gate_mode="shadow",
            posture_gate_force_enforce=False,
            topic_lifecycle_serialization="off",
        ),
        scheduler=SimpleNamespace(
            pool_target_count=30,
            copy_ready_target_count=configured_copy_target,
            speculation_interval_minutes=10,
            speculation_ttl_days=3,
            speculation_cooldown_days=7,
            speculation_confirmation_threshold=3,
            speculation_max_active=5,
            speculation_max_primary_interests=15,
            speculation_max_secondary_interests=60,
            avoidance_speculation_interval_minutes=10,
            avoidance_speculation_ttl_days=3,
            avoidance_speculation_cooldown_days=7,
            avoidance_speculation_confirmation_threshold=3,
            avoidance_speculation_max_active=5,
            speculator_idle_interval_minutes=30,
            profile_consolidation_enabled=True,
            profile_consolidation_interval_hours=12,
            profile_consolidation_like_target_upper=512,
            profile_consolidation_like_target_soft=450,
            profile_consolidation_archive_enabled=True,
            feedback_batch_threshold=3,
            unified_interest_line=False,
        ),
    )

    monkeypatch.setattr(cli_module, "_RUNTIME_COMPONENTS", {}, raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: "registry", raising=False)
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: "client", raising=False)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr(database_module, "Database", FakeDatabase)
    monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
    monkeypatch.setattr(recommendation_module, "RecommendationEngine", FakeRecommendationEngine)
    monkeypatch.setattr(soul_module, "SoulEngine", FakeSoulEngine)
    monkeypatch.setattr(discovery_module, "ContentDiscoveryEngine", FakeDiscoveryEngine)
    monkeypatch.setattr(strategy_module, "SearchStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "TrendingStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "RelatedChainStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "ExploreStrategy", FakeStrategy)

    recommendation_engine = cli_module._build_recommendation_engine()
    discovery_engine = cli_module._build_discovery_engine()
    soul_engine = cli_module._build_soul_engine()

    assert len(created_databases) == 1
    assert created_databases[0].initialized == 1
    assert len(created_memories) == 1
    assert created_memories[0].initialized == 1
    assert created_memories[0].database is created_databases[0]
    assert recommendation_engine.database is created_databases[0]
    assert recommendation_engine.kwargs["copy_ready_target_count"] == expected_copy_target
    assert recommendation_engine.kwargs["pool_available_target_count"] == 30
    assert discovery_engine.database is created_databases[0]
    recorder = recommendation_engine.llm.usage_recorder
    assert recorder is not None
    assert recorder is discovery_engine.llm_service.usage_recorder
    assert recorder._sink is created_databases[0]
    assert (
        recommendation_engine.llm.concurrency_gate is discovery_engine.llm_service.concurrency_gate
    )
    assert soul_engine.kwargs["llm_concurrency_gate"] is recommendation_engine.llm.concurrency_gate
    assert soul_engine.kwargs["preference_prompt_view"] == "compact-v1"
    assert soul_engine.kwargs["awareness_prompt_view"] == "legacy"
    assert soul_engine.kwargs["insight_prompt_view"] == "compact-v1"
    assert discovery_engine.concurrency.llm_evaluation_concurrency == 2


def test_start_accepts_explicit_host_and_port(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["start", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 0
    assert called == {"host": "0.0.0.0", "port": 9000}
    assert "0.0.0.0:9000" in result.stdout


def test_serve_api_uses_container_defaults(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["serve-api"])

    assert result.exit_code == 0
    assert "容器 API 服务" in result.stdout
    assert "0.0.0.0:8420" in result.stdout
    assert called == {"host": "0.0.0.0", "port": 8420}


def test_serve_api_warns_when_pause_on_disconnect_requires_extension_presence(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    called: dict[str, object] = {}
    cfg = config_module.Config()
    cfg.scheduler.pause_on_extension_disconnect = True

    def fake_run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(config_module, "load_config", lambda: cfg, raising=False)
    monkeypatch.setattr(cli_module, "_run_api_server", fake_run_api_server, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["serve-api"])

    assert result.exit_code == 0
    assert "WARN extension presence required" in result.stdout
    assert "background LLM work after grace period" in result.stdout
    assert called == {"host": "0.0.0.0", "port": 8420}


def test_discover_prints_init_guidance_when_profile_missing(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            raise SoulProfileNotInitializedError("missing")

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["discover"])

    assert result.exit_code == 1
    assert "尚未初始化" in result.stdout
    assert "openbiliclaw init" in result.stdout


def test_discover_reports_empty_results(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDiscoveryEngine:
        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[DiscoveredContent]:
            return []

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: FakeDiscoveryEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["discover"])

    assert result.exit_code == 0
    assert "本次内容发现" in result.stdout
    assert "没有发现到新内容" in result.stdout


def test_discover_displays_preview_rows(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDiscoveryEngine:
        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[DiscoveredContent]:
            return [
                DiscoveredContent(
                    bvid="BV1DISC",
                    title="讲透城市空间与叙事结构",
                    up_name="城市观察局",
                    source_strategy="search",
                    relevance_score=0.83,
                )
            ]

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: FakeDiscoveryEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["discover"])

    assert result.exit_code == 0
    assert "本次内容发现" in result.stdout
    assert "发现条数" in result.stdout
    assert "讲透城市空间与叙事结构" in result.stdout
    assert "UP 主" in result.stdout
    assert "城市观察局" in result.stdout
    assert "来源策略" in result.stdout
    assert "search" in result.stdout
    assert "相关性分数" in result.stdout


def test_discover_douyin_requires_enabled_config(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = False

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["discover", "--source", "douyin"])

    assert result.exit_code == 1
    assert "抖音 discovery 未启用" in result.stdout


def test_discover_douyin_uses_formal_producer_and_ignores_scheduler_master_switch(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"
    config.scheduler.enabled = False
    captured: dict[str, object] = {}

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDatabase:
        conn = object()

    class FakePipeline:
        last_admitted_items: list[DiscoveredContent] = []

    class FakeProducer:
        async def produce_if_due(self, *, limit: int) -> dict[str, object]:
            captured["limit"] = limit
            return {
                "reason": "empty",
                "discovered": 0,
                "enqueued": 0,
                "source_counts": {},
                "source_outcomes": {"search": "empty", "hot": "empty"},
            }

    def fake_build_producer(**kwargs: object) -> FakeProducer:
        captured.update(kwargs)
        return FakeProducer()

    import openbiliclaw.runtime.douyin_producer as douyin_producer_module

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine())
    monkeypatch.setattr(cli_module, "_build_discovery_engine", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_candidate_pipeline",
        lambda **kwargs: FakePipeline(),
    )
    monkeypatch.setattr(
        douyin_producer_module,
        "build_douyin_discovery_producer",
        fake_build_producer,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "sessionid=test;")

    result = runner.invoke(app, ["discover", "--source", "douyin", "--limit", "7"])

    assert result.exit_code == 0
    assert captured["enabled_override"] is True
    assert captured["limit"] == 7
    assert "正式 producer" in result.stdout
    assert "本轮分支正常完成" in result.stdout


def test_discover_douyin_debug_runs_direct_strategy(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDiscoveryEngine:
        def __init__(self) -> None:
            self.registered: list[object] = []

        def register_strategy(self, strategy: object) -> None:
            self.registered.append(strategy)

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[DiscoveredContent]:
            assert strategies == ["douyin_direct"]
            assert limit == 5
            assert self.registered
            return [
                DiscoveredContent(
                    bvid="dy:1",
                    content_id="1",
                    content_url="https://www.douyin.com/video/1",
                    title="抖音发现内容",
                    up_name="抖音作者",
                    source_platform="douyin",
                    source_strategy="dy-direct-search",
                    relevance_score=0.8,
                )
            ]

    fake_engine = FakeDiscoveryEngine()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: fake_engine,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "msToken=t; ttwid=tw;")

    result = runner.invoke(app, ["discover-douyin", "--limit", "5"])

    assert result.exit_code == 0
    assert "抖音内容发现" in result.stdout
    assert "抖音发现内容" in result.stdout
    assert "douyin" in result.stdout


def test_discover_douyin_debug_reads_cookie_from_synced_file(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    config = config_module.Config(data_dir=str(tmp_path / "data"))
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"

    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "douyin_cookie.json").write_text(
        '{"cookie": "msToken=file; ttwid=tw;"}',
        encoding="utf-8",
    )

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDiscoveryEngine:
        def register_strategy(self, strategy: object) -> None:
            assert cast("Any", strategy).client.cookie == "msToken=file; ttwid=tw;"

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[DiscoveredContent]:
            return []

    monkeypatch.delenv("TEST_DY_COOKIE", raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: FakeDiscoveryEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["discover-douyin", "--limit", "5"])

    assert result.exit_code == 0
    assert "没有发现到新抖音内容" in result.stdout


def test_discover_douyin_debug_does_not_use_recent_bootstrap_creator_seeds_by_default(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDiscoveryEngine:
        def register_strategy(self, strategy: object) -> None:
            assert cast("Any", strategy).creator_sec_uids == ()
            assert cast("Any", strategy).sources == ("search", "hot", "feed")

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[DiscoveredContent]:
            return []

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: FakeDiscoveryEngine(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_recent_douyin_creator_sec_uids",
        lambda limit=20: pytest.fail("creator seeds should not be read by default"),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "msToken=t; ttwid=tw;")
    monkeypatch.delenv("OPENBILICLAW_DOUYIN_CREATOR_SEC_UIDS", raising=False)

    result = runner.invoke(app, ["discover-douyin", "--limit", "5"])

    assert result.exit_code == 0


def test_discover_douyin_standalone_command_passes_debug_options(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_douyin_discovery(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_run_douyin_discovery", fake_run_douyin_discovery)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(
        app,
        [
            "discover-douyin",
            "--keyword",
            "猫咪,机械键盘",
            "--source",
            "search,feed",
            "--limit",
            "12",
            "--no-cache",
            "--no-evaluate",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "limit": 12,
        "keywords": ("猫咪", "机械键盘"),
        "creator_sec_uids": (),
        "sources": ("search", "feed"),
        "cache": False,
        "evaluate": False,
    }


def test_discover_douyin_source_normalization_accepts_feed_and_rejects_creator() -> None:
    assert cli_module._normalize_douyin_discovery_sources(("search,feed",)) == (
        "search",
        "feed",
    )
    with pytest.raises(typer.BadParameter, match="search、hot、feed"):
        cli_module._normalize_douyin_discovery_sources(("creator",))


def test_discover_douyin_search_uses_plugin_client(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                preferences=PreferenceLayer(
                    interests=[],
                ),
            )

    class FakeDirectClient:
        cookie = "msToken=t; ttwid=tw;"

        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

        async def __aenter__(self) -> "FakeDirectClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

    class FakePluginSearchClient:
        cookie = "msToken=t; ttwid=tw;"

        def __init__(
            self,
            *,
            database: object,
            direct_client: FakeDirectClient,
            **kwargs: object,
        ) -> None:
            del database, kwargs
            self.cookie = direct_client.cookie

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            assert keyword == "猫"
            assert limit == 5
            return [
                {
                    "aweme_id": "plugin-1",
                    "desc": "插件搜索结果",
                    "author": {"nickname": "插件作者"},
                }
            ]

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

    class FakeDatabase:
        conn = object()

    import openbiliclaw.sources.douyin_direct as douyin_direct_module
    import openbiliclaw.sources.douyin_plugin_search as plugin_search_module

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase(), raising=False)
    monkeypatch.setattr(douyin_direct_module, "DouyinDirectClient", FakeDirectClient)
    monkeypatch.setattr(plugin_search_module, "DouyinPluginSearchClient", FakePluginSearchClient)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "msToken=t; ttwid=tw;")

    result = runner.invoke(
        app,
        [
            "discover-douyin",
            "--source",
            "search",
            "--keyword",
            "猫",
            "--limit",
            "5",
            "--no-cache",
            "--no-evaluate",
        ],
    )

    assert result.exit_code == 0
    assert "插件搜索结果" in result.stdout


def test_discover_douyin_hot_uses_plugin_client(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"
    config.sources.douyin.daily_search_budget = 11
    config.sources.douyin.daily_hot_budget = 13

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDirectClient:
        cookie = "msToken=t; ttwid=tw;"

        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

        async def __aenter__(self) -> "FakeDirectClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_hot_terms(self, *, limit: int = 30) -> list[dict[str, object]]:
            return [{"word": "热点词", "sentence_id": "2495363"}]

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

    captured: dict[str, object] = {}

    class FakePluginSearchClient:
        hot_source_strategy = "dy-plugin-hot-related"

        def __init__(
            self,
            *,
            database: object,
            direct_client: FakeDirectClient,
            **kwargs: object,
        ) -> None:
            del database
            self.cookie = direct_client.cookie
            captured.update(kwargs)

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            assert limit == 5
            return [
                {
                    "aweme_id": "plugin-hot-1",
                    "desc": "插件热点相关结果",
                    "author": {"nickname": "热点作者"},
                }
            ]

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

    class FakeDatabase:
        conn = object()

    import openbiliclaw.sources.douyin_direct as douyin_direct_module
    import openbiliclaw.sources.douyin_plugin_search as plugin_search_module

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase(), raising=False)
    monkeypatch.setattr(douyin_direct_module, "DouyinDirectClient", FakeDirectClient)
    monkeypatch.setattr(plugin_search_module, "DouyinPluginSearchClient", FakePluginSearchClient)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "msToken=t; ttwid=tw;")

    result = runner.invoke(
        app,
        [
            "discover-douyin",
            "--source",
            "hot",
            "--limit",
            "5",
            "--no-cache",
            "--no-evaluate",
        ],
    )

    assert result.exit_code == 0
    assert "插件热点相关结果" in result.stdout
    assert captured["daily_search_budget"] == 11
    assert captured["daily_hot_budget"] == 13


def test_discover_douyin_feed_uses_plugin_client(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    config = config_module.Config()
    config.sources.douyin.enabled = True
    config.sources.douyin.cookie_env = "TEST_DY_COOKIE"
    config.sources.douyin.daily_feed_budget = 17

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDirectClient:
        cookie = "msToken=t; ttwid=tw;"

        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

        async def __aenter__(self) -> "FakeDirectClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

    captured: dict[str, object] = {}

    class FakePluginSearchClient:
        feed_source_strategy = "dy-plugin-feed"

        def __init__(
            self,
            *,
            database: object,
            direct_client: FakeDirectClient,
            **kwargs: object,
        ) -> None:
            del database
            self.cookie = direct_client.cookie
            captured.update(kwargs)

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return []

        async def get_creator_posts(
            self,
            sec_uid: str,
            *,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            return []

        async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
            assert limit == 5
            return [
                {
                    "aweme_id": "plugin-feed-1",
                    "desc": "插件首页推荐结果",
                    "author": {"nickname": "推荐作者"},
                }
            ]

    class FakeDatabase:
        conn = object()

    import openbiliclaw.sources.douyin_direct as douyin_direct_module
    import openbiliclaw.sources.douyin_plugin_search as plugin_search_module

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase(), raising=False)
    monkeypatch.setattr(douyin_direct_module, "DouyinDirectClient", FakeDirectClient)
    monkeypatch.setattr(plugin_search_module, "DouyinPluginSearchClient", FakePluginSearchClient)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setenv("TEST_DY_COOKIE", "msToken=t; ttwid=tw;")

    result = runner.invoke(
        app,
        [
            "discover-douyin",
            "--source",
            "feed",
            "--limit",
            "5",
            "--no-cache",
            "--no-evaluate",
        ],
    )

    assert result.exit_code == 0
    assert "插件首页推荐结果" in result.stdout
    assert captured["daily_feed_budget"] == 17


def test_chat_prints_init_guidance_when_profile_missing(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            raise SoulProfileNotInitializedError("missing")

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["chat"])

    assert result.exit_code == 1
    assert "尚未初始化" in result.stdout
    assert "openbiliclaw init" in result.stdout


@pytest.mark.asyncio
async def test_cli_dialogue_legacy_direct_mode_still_learns_without_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.dialogue import DialogueLearningMode

    learned = asyncio.Event()

    class FakeService:
        async def complete_socratic_dialogue(
            self,
            *,
            user_message: str,
            history: list[dict[str, str]],
            caller: str = "",
        ) -> LLMResponse:
            return LLMResponse(content="CLI reply", provider="test")

    class FakeSoulEngine:
        def __init__(self) -> None:
            self._llm_service = FakeService()
            self.learn_calls: list[dict[str, object]] = []

        async def learn_from_dialogue(self, **payload: object) -> None:
            self.learn_calls.append(dict(payload))
            learned.set()

    monkeypatch.setattr(cli_module, "_build_registry", object)
    soul_engine = FakeSoulEngine()
    dialogue = cli_module._build_dialogue(soul_engine)

    assert dialogue.learning_mode is DialogueLearningMode.LEGACY_DIRECT
    assert dialogue._settlement_queue is None
    assert await dialogue.respond("CLI direct learning") == "CLI reply"
    await asyncio.wait_for(learned.wait(), timeout=1)
    assert len(soul_engine.learn_calls) == 1
    assert soul_engine.learn_calls[0]["user_message"] == "CLI direct learning"


def test_chat_runs_single_turn_and_prints_reply(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDialogue:
        async def respond(self, user_message: str) -> str:
            return f"我听见你在说：{user_message}"

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_dialogue",
        lambda soul_engine: FakeDialogue(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["chat"], input="我最近总在刷讲结构的视频。\nexit\n")

    assert result.exit_code == 0
    assert "苏格拉底式对话" in result.stdout
    assert "阿花：" in result.stdout
    assert "我听见你在说：我最近总在刷讲结构的视频。" in result.stdout


def test_chat_reports_safe_turn_failure_and_keeps_loop_usable(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.llm.service import LLMResponseContentError

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDialogue:
        def __init__(self) -> None:
            self.calls = 0

        async def respond(self, user_message: str) -> str:
            self.calls += 1
            if self.calls == 1:
                raise LLMResponseContentError("sk-live-secret")
            return f"第二轮成功：{user_message}"

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module, "_build_dialogue", lambda soul_engine: FakeDialogue(), raising=False
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["chat"], input="第一轮\n第二轮\nexit\n")

    assert result.exit_code == 0
    assert "空响应" in result.stdout
    assert "sk-live-secret" not in result.stdout
    assert "第二轮成功：第二轮" in result.stdout


def test_chat_exits_cleanly_on_exit_command(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeDialogue:
        async def respond(self, user_message: str) -> str:
            return "不应被调用"

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_dialogue",
        lambda soul_engine: FakeDialogue(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["chat"], input="exit\n")

    assert result.exit_code == 0
    assert "对话结束" in result.stdout


def test_profile_command_shows_saved_profile(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> OnionProfile:
            return OnionProfile(
                personality_portrait=(
                    "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。" * 6
                ),
                core=CoreLayer(
                    core_traits=["理性", "谨慎", "自驱"],
                    deep_needs=["被理解", "持续成长"],
                ),
                values_layer=ValuesLayer(
                    values=["成长", "真实"],
                    motivational_drivers=["自我完善"],
                ),
                role=RoleLayer(
                    life_stage="稳定积累阶段",
                    current_phase="专注深耕",
                ),
            )

    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "用户画像概览" in result.stdout
    assert "人格描述" in result.stdout
    assert "核心层" in result.stdout
    assert "理性" in result.stdout
    assert "稳定积累阶段" in result.stdout
    assert "专注深耕" in result.stdout
    assert "自我完善" in result.stdout


def test_profile_command_prints_init_guidance_when_missing_profile(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    from openbiliclaw.soul.engine import SoulProfileNotInitializedError

    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            raise SoulProfileNotInitializedError("missing")

    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 1
    assert "尚未初始化" in result.stdout
    assert "openbiliclaw init" in result.stdout


def test_recommend_prints_discover_guidance_when_no_results(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeRecommendationEngine:
        async def generate_recommendations(
            self,
            discovered: list[DiscoveredContent] | None,
            profile: SoulProfile,
            limit: int = 10,
        ) -> list[Recommendation]:
            return []

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: FakeRecommendationEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["recommend"])

    assert result.exit_code == 0
    assert "本轮推荐" in result.stdout
    assert "暂无可推荐内容" in result.stdout
    assert "openbiliclaw discover" in result.stdout


def test_recommend_displays_results_and_marks_them_presented(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeSoulEngine:
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(personality_portrait="稳定用户画像" * 30)

    class FakeRecommendationEngine:
        def __init__(self) -> None:
            self.marked_ids: list[int] = []

        async def generate_recommendations(
            self,
            discovered: list[DiscoveredContent] | None,
            profile: SoulProfile,
            limit: int = 10,
        ) -> list[Recommendation]:
            return [
                Recommendation(
                    recommendation_id=7,
                    content=DiscoveredContent(
                        bvid="BV1REC",
                        title="讲透城市与建筑的空间叙事",
                        up_name="城市观察局",
                        published_at="2020-07-08T06:30:00Z",
                        published_label="3 days ago",
                    ),
                    expression="这条会对上你最近那种想把结构想透的劲头。",
                    topic_label="你最近那股想把结构想透的劲头",
                    confidence=0.88,
                ),
                Recommendation(
                    recommendation_id=8,
                    content=DiscoveredContent(
                        bvid="BV1LABEL",
                        title="只有来源相对时间的推荐",
                        up_name="时间观察局",
                        published_label="3 days ago",
                    ),
                ),
                Recommendation(
                    recommendation_id=9,
                    content=DiscoveredContent(
                        bvid="BV1EMPTY",
                        title="没有可靠时间的推荐",
                        up_name="空值观察局",
                    ),
                ),
            ]

        def mark_presented(self, recommendation_ids: list[int]) -> None:
            self.marked_ids = recommendation_ids

    fake_engine = FakeRecommendationEngine()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: fake_engine,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["recommend"])

    assert result.exit_code == 0
    assert "本轮推荐" in result.stdout
    assert "讲透城市与建筑的空间叙事" in result.stdout
    assert "UP 主" in result.stdout
    assert "城市观察局" in result.stdout
    assert "这条会对上你最近那种想把结构想透的劲头。" in result.stdout
    assert "话题标签" in result.stdout
    assert "BV1REC" in result.stdout
    assert "2020-07-08" in result.stdout
    assert "3 days ago" in result.stdout
    assert result.stdout.count("发布时间") == 2
    assert fake_engine.marked_ids == [7, 8, 9]


def test_feedback_command_updates_recommendation_and_records_event(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeRecommendationEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, str]] = []

        async def record_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            note: str = "",
        ) -> None:
            self.calls.append((recommendation_id, feedback_type, note))

        def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
            return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def persist_events_with_receipts(
            self, events: list[dict[str, object]]
        ) -> list[SimpleNamespace]:
            self.events.extend(events)
            return [
                SimpleNamespace(
                    event_id=index,
                    event_type=event["event_type"],
                    inserted=True,
                    duplicate=False,
                )
                for index, event in enumerate(events, start=1)
            ]

        def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
            return [self.events[event_id - 1] for event_id in event_ids]

    fake_engine = FakeRecommendationEngine()
    fake_memory = FakeMemoryManager()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: fake_engine,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: fake_memory,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "dislike", "--note", "太浅了"])

    assert result.exit_code == 0
    assert "反馈已记录" in result.stdout
    assert fake_engine.calls == [(7, "dislike", "太浅了")]
    assert fake_memory.events[0]["event_type"] == "feedback"
    assert fake_memory.events[0]["metadata"]["recommendation_id"] == 7
    assert fake_memory.events[0]["metadata"]["feedback_type"] == "dislike"
    persisted_ingest_key = str(fake_memory.events[0]["ingest_key"])
    assert persisted_ingest_key.startswith("cli:")
    generated_request_id = persisted_ingest_key.removeprefix("cli:")
    assert len(generated_request_id) == 32
    assert int(generated_request_id, 16) >= 0
    assert generated_request_id in result.stdout


def test_feedback_command_trims_and_reuses_explicit_request_id(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    class FakeRecommendationEngine:
        def get_recommendation(self, recommendation_id: int) -> dict[str, object]:
            return {"id": recommendation_id, "bvid": "BV1REC", "title": "稳定重试"}

        async def record_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            note: str = "",
        ) -> None:
            return None

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def persist_events_with_receipts(
            self,
            events: list[dict[str, object]],
        ) -> list[SimpleNamespace]:
            self.events.extend(events)
            return [
                SimpleNamespace(
                    event_id=1,
                    event_type=events[0]["event_type"],
                    inserted=True,
                    duplicate=False,
                )
            ]

        def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
            assert event_ids == [1]
            return [self.events[0]]

    memory = FakeMemoryManager()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: FakeRecommendationEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(
        app,
        [
            "feedback",
            "7",
            "like",
            "--request-id",
            "  cli-stable-retry  ",
        ],
    )

    assert result.exit_code == 0
    assert memory.events[0]["ingest_key"] == "cli:cli-stable-retry"
    assert "cli-stable-retry" in result.stdout


def test_feedback_command_rejects_request_id_over_400_characters(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(
        app,
        ["feedback", "7", "like", "--request-id", "x" * 401],
    )

    assert result.exit_code == 1
    assert "最长 400" in result.stdout


def test_feedback_command_reports_missing_recommendation(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeRecommendationEngine:
        def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
            return None

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: FakeRecommendationEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "like"])

    assert result.exit_code == 1
    assert "推荐不存在" in result.stdout


def test_feedback_command_supports_comment_with_note(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    class FakeRecommendationEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str, str]] = []

        async def record_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            note: str = "",
        ) -> None:
            self.calls.append((recommendation_id, feedback_type, note))

        def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
            return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def persist_events_with_receipts(
            self, events: list[dict[str, object]]
        ) -> list[SimpleNamespace]:
            self.events.extend(events)
            return [
                SimpleNamespace(
                    event_id=index,
                    event_type=event["event_type"],
                    inserted=True,
                    duplicate=False,
                )
                for index, event in enumerate(events, start=1)
            ]

        def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
            return [self.events[event_id - 1] for event_id in event_ids]

    fake_engine = FakeRecommendationEngine()
    fake_memory = FakeMemoryManager()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: fake_engine,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: fake_memory,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(
        app,
        ["feedback", "7", "comment", "--note", "方向对，但我想看更深一点的。"],
    )

    assert result.exit_code == 0
    assert "反馈已记录" in result.stdout
    assert fake_engine.calls == [(7, "comment", "方向对，但我想看更深一点的。")]
    assert fake_memory.events[0]["metadata"]["feedback_type"] == "comment"


def test_feedback_command_requires_note_for_comment(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "comment"])

    assert result.exit_code == 1
    assert "comment 需要" in result.stdout


def test_feedback_command_triggers_profile_refresh_check(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    order: list[str] = []

    class FakeRecommendationEngine:
        async def record_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            note: str = "",
        ) -> None:
            return None

        def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
            return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def persist_events_with_receipts(
            self, events: list[dict[str, object]]
        ) -> list[SimpleNamespace]:
            order.append("event")
            self.events.extend(events)
            return [
                SimpleNamespace(
                    event_id=index,
                    event_type=event["event_type"],
                    inserted=True,
                    duplicate=False,
                )
                for index, event in enumerate(events, start=1)
            ]

        def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
            return [self.events[event_id - 1] for event_id in event_ids]

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.called = False

        async def prepare_feedback_owner_cutover(self) -> dict[str, object]:
            order.append("prepare")
            return {"prepared": True, "feedback_owner_version": 2}

        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            order.append("process")
            self.called = True
            return {"triggered": False}

    fake_soul_engine = FakeSoulEngine()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: FakeRecommendationEngine(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: FakeMemoryManager(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_soul_engine",
        lambda: fake_soul_engine,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "like"])

    assert result.exit_code == 0
    assert fake_soul_engine.called is True
    assert order == ["prepare", "event", "process"]


@pytest.mark.parametrize("owner_outcome", ["skipped", "error"])
def test_feedback_command_keeps_durable_success_when_owner_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
    owner_outcome: str,
) -> None:
    projected: list[tuple[int, str, str]] = []

    class FakeRecommendationEngine:
        def get_recommendation(self, recommendation_id: int) -> dict[str, object]:
            return {"id": recommendation_id, "bvid": "BV1REC", "title": "已落账反馈"}

        async def record_feedback(
            self,
            recommendation_id: int,
            *,
            feedback_type: str,
            note: str = "",
        ) -> None:
            projected.append((recommendation_id, feedback_type, note))

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def persist_events_with_receipts(
            self,
            events: list[dict[str, object]],
        ) -> list[SimpleNamespace]:
            self.events.extend(events)
            return [
                SimpleNamespace(
                    event_id=1,
                    event_type=events[0]["event_type"],
                    inserted=True,
                    duplicate=False,
                )
            ]

        def query_event_rows_by_ids(self, event_ids: list[int]) -> list[dict[str, object]]:
            return [self.events[event_id - 1] for event_id in event_ids]

    class FakeSoulEngine:
        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
            if owner_outcome == "error":
                raise RuntimeError("owner unavailable")
            return {"skipped": True, "reason": "feedback_batch_in_progress"}

    memory = FakeMemoryManager()
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: FakeRecommendationEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "dislike", "--note", "太浅了"])

    assert result.exit_code == 0
    assert "反馈已记录" in result.stdout
    assert "画像处理稍后重试" in result.stdout
    assert "反馈事件写入失败" not in result.stdout
    assert projected == [(7, "dislike", "太浅了")]
    assert len(memory.events) == 1


def test_init_reports_authentication_failure(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=tmp_path / "bilibili_cookie.json",
                message="未配置 B 站 Cookie。",
            )

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "认证失败" in result.stdout
    assert "auth login" in result.stdout


def test_init_guides_missing_runtime_config_interactively(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return []

    captured: dict[str, object] = {}
    config_errors = iter(["llm.openai.api_key", None])

    def fake_save_runtime_config(
        provider: str,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ) -> None:
        captured["provider"] = provider
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        captured["model"] = model

    def fake_load_runtime_config_error(*, render: bool = True) -> str | None:
        return next(config_errors)

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    monkeypatch.setattr(
        cli_module, "_maybe_setup_password_in_init", lambda **_: None, raising=False
    )
    monkeypatch.setattr(
        cli_module,
        "_save_runtime_provider_config",
        fake_save_runtime_config,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_save_embedding_config",
        lambda **_: None,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_save_module_overrides",
        lambda *_: None,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_auth_manager",
        lambda: FakeAuthManager(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    # Hermetic: the ambient data dir may hold a real profile (e.g. after a
    # local E2E run), which would otherwise flip init into the re-init
    # confirm branch and consume an extra prompt.
    monkeypatch.setattr(
        cli_module,
        "_build_soul_engine",
        lambda: SimpleNamespace(is_profile_ready=lambda: False),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(
        cli_module,
        "_load_runtime_config_error",
        fake_load_runtime_config_error,
        raising=False,
    )

    # Wizard + source-prompt inputs:
    #   1. menu choice: "gemini"
    #   2. API key
    #   3. model (accept default)
    #   4. embedding choice "3" (disable embedding; avoids host-dependent Ollama prompts)
    #   5. "n" — skip module overrides
    #   6. "y" — allow LAN access
    #   7-9. "" — accept Bili history/favorite/follow init limits
    #   10+. "n" — skip optional source prompts
    #               (xhs / douyin / youtube / X / zhihu / reddit /
    #                Linux.do / v2ex / weibo / bangumi / GitHub)
    wizard_input = (
        "\n".join(
            [
                "gemini",
                "gemini-key",
                "",
                "3",
                "n",
                "y",
                "",
                "",
                "",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
                "n",
            ]
        )
        + "\n"
    )
    result = runner.invoke(app, ["init"], input=wizard_input)

    assert result.exit_code == 1
    assert captured["provider"] == "gemini"
    assert captured["api_key"] == "gemini-key"
    assert "初始化前配置引导" in result.stdout
    assert "DeepSeek" in result.stdout  # menu now leads with DeepSeek (v0.3.20+)
    assert "历史为空" in result.stdout


def test_init_guides_missing_auth_interactively(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        def __init__(self) -> None:
            self.saved_cookie = ""

        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=False,
                authenticated=False,
                cookie_path=tmp_path / "bilibili_cookie.json",
                message="未配置 B 站 Cookie。",
            )

        async def validate_cookie(self, cookie: str) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

        def set_cookie(self, cookie: str) -> None:
            self.saved_cookie = cookie

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return []

    fake_auth = FakeAuthManager()
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    monkeypatch.setattr(
        cli_module, "_maybe_setup_password_in_init", lambda **_: None, raising=False
    )
    monkeypatch.setattr(
        cli_module,
        "_load_runtime_config_error",
        lambda *, render=True: None,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: fake_auth, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    # Hermetic: the ambient data dir may hold a real profile (e.g. after a
    # local E2E run), which would otherwise flip init into the re-init
    # confirm branch and consume an extra prompt.
    monkeypatch.setattr(
        cli_module,
        "_build_soul_engine",
        lambda: SimpleNamespace(is_profile_ready=lambda: False),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    # v0.3.13: auth wizard now opens with a 2-choice prompt
    # (1=install extension and skip / 2=paste cookie now). To keep this
    # test exercising the manual-paste path, send "2" first.
    # v0.3.89+: init asks whether to allow LAN access before the source
    # prompts. Answer yes, accept Bili signal-limit defaults, then send "n"
    # to XHS / Douyin / YouTube / X / Zhihu / Reddit / Linux.do / V2EX /
    # Weibo / Bangumi / GitHub so this test stays focused on the cookie-prompt
    # path.
    result = runner.invoke(
        app,
        ["init"],
        input="2\nSESSDATA=valid\ny\n\n\n\nn\nn\nn\nn\nn\nn\nn\nn\nn\nn\nn\n",
    )

    assert result.exit_code == 1
    assert fake_auth.saved_cookie == "SESSDATA=valid"
    assert "初始化前认证引导" in result.stdout
    assert "历史为空" in result.stdout


def test_init_reports_config_error_when_non_interactive(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_load_runtime_config_error",
        lambda *, render=True: "llm.openai.api_key",
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "配置错误" in result.stdout
    assert "llm.openai.api_key" in result.stdout


def test_init_reports_when_history_is_empty(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return []

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "历史为空" in result.stdout


def test_init_runs_history_preference_profile_and_discovery(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(
            self,
            max_folders: int = 20,
            max_items_per_folder: int = 200,
            max_total_items: int | None = None,
        ) -> list[object]:
            return []

        async def get_following(
            self,
            page: int = 1,
            page_size: int = 50,
        ) -> list[object]:
            return []

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []
            self.built_history: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            self.built_history.append(history)
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    class FakeDiscoveryEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[SoulProfile, list[str] | None, int]] = []

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
            **_: object,
        ) -> list[DiscoveredContent]:
            self.calls.append((profile, strategies, limit))
            return [
                DiscoveredContent(
                    bvid="BV1DISC",
                    title="发现内容",
                    up_name="发现实验室",
                    relevance_score=0.8,
                )
            ]

    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_discovery = FakeDiscoveryEngine()
    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: fake_discovery,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "初始化 OpenBiliClaw" in result.stdout
    assert "初始化摘要" in result.stdout
    assert "1/4" in result.stdout
    assert "2/4" in result.stdout
    assert "3/4" in result.stdout
    assert "4/4" in result.stdout
    assert "浏览历史" in result.stdout
    assert "首轮发现内容" in result.stdout
    assert fake_memory.events[0]["event_type"] == "view"
    assert fake_soul.analyzed_events
    assert fake_soul.built_history
    assert fake_discovery.calls


def test_init_caps_bilibili_history_and_favorites_at_500_and_following_at_100(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            assert max_items == 500
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(
            self,
            max_folders: int = 20,
            max_items_per_folder: int = 200,
            max_total_items: int | None = None,
        ) -> list[object]:
            assert max_total_items == 500
            items = [
                SimpleNamespace(title=f"收藏视频 {idx}", upper=f"UP {idx}") for idx in range(550)
            ]
            return [SimpleNamespace(folder=SimpleNamespace(title="默认收藏夹"), items=items)]

        async def get_following(
            self,
            page: int = 1,
            page_size: int = 50,
        ) -> list[object]:
            start = (page - 1) * page_size
            users = [
                SimpleNamespace(uname=f"关注用户 {idx}", sign=f"签名 {idx}")
                for idx in range(start, min(start + page_size, 350))
            ]
            return users

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []
            self.built_history: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            self.built_history.append(history)
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_database = type("FakeDatabase", (), {"count_pool_candidates": lambda self: 0})()

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: FakeBilibiliClient())
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_init_discovery_backfill_async", fake_discovery_backfill)

    result = runner.invoke(app, ["init", "--no-xhs", "--no-douyin", "--no-youtube"])

    assert result.exit_code == 0
    assert fake_soul.analyzed_events
    analyzed = fake_soul.analyzed_events[0]
    assert len([event for event in analyzed if event["event_type"] == "favorite"]) == 500
    assert len([event for event in analyzed if event["event_type"] == "follow"]) == 100
    assert len(fake_memory.events) == 601
    built_history = fake_soul.built_history[0]
    # 1 条历史 + 500 条收藏各自成行 + 收藏夹/关注两条汇总。收藏曾经整体塌成
    # 那一条汇总，于是 500 个用户主动存下的信号在画像里一个都看不见。
    favorite_rows = [row for row in built_history if row.get("event_type") == "favorite"]
    assert len(favorite_rows) == 500, "一个收藏就是一个信号，不能塌成汇总里的一个数字"
    assert len(built_history) == 503
    assert str(built_history[-2]["_favorites_summary"]).startswith("共 500 个收藏")
    assert str(built_history[-1]["_following_summary"]).startswith("共关注 100 人")


def test_init_accepts_custom_bilibili_history_favorites_and_following_limits(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            assert max_items == 7
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(
            self,
            max_folders: int = 20,
            max_items_per_folder: int = 200,
            max_total_items: int | None = None,
        ) -> list[object]:
            assert max_items_per_folder == 2
            assert max_total_items == 2
            items = [
                SimpleNamespace(title=f"收藏视频 {idx}", upper=f"UP {idx}") for idx in range(5)
            ]
            return [SimpleNamespace(folder=SimpleNamespace(title="默认收藏夹"), items=items)]

        async def get_following(
            self,
            page: int = 1,
            page_size: int = 50,
        ) -> list[object]:
            start = (page - 1) * page_size
            return [
                SimpleNamespace(uname=f"关注用户 {idx}", sign=f"签名 {idx}")
                for idx in range(start, min(start + page_size, 5))
            ]

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_database = type("FakeDatabase", (), {"count_pool_candidates": lambda self: 0})()

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False, raising=False)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager())
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: FakeBilibiliClient())
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_init_discovery_backfill_async", fake_discovery_backfill)

    result = runner.invoke(
        app,
        [
            "init",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
            "--bilibili-history-limit",
            "7",
            "--bilibili-favorite-limit",
            "2",
            "--bilibili-follow-limit",
            "3",
        ],
    )

    assert result.exit_code == 0
    analyzed = fake_soul.analyzed_events[0]
    assert len([event for event in analyzed if event["event_type"] == "favorite"]) == 2
    assert len([event for event in analyzed if event["event_type"] == "follow"]) == 3
    assert len(fake_memory.events) == 6


def test_init_includes_xhs_bootstrap_events(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(
            self,
            max_folders: int = 20,
            max_items_per_folder: int = 200,
            max_total_items: int | None = None,
        ) -> list[object]:
            return []

        async def get_following(
            self,
            page: int = 1,
            page_size: int = 50,
        ) -> list[object]:
            return []

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []
            self.built_history: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            self.built_history.append(history)
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    xhs_event = {
        "event_type": "favorite",
        "title": "小红书收藏咖啡",
        "url": "https://www.xiaohongshu.com/explore/xhs-note-1",
        "context": "小红书收藏：小红书收藏咖啡 作者：豆子老师",
        "metadata": {
            "source_platform": "xiaohongshu",
            "note_id": "xhs-note-1",
            "author": "豆子老师",
            "import_source": "xhs_bootstrap_saved",
            "signal_strength": 1.0,
        },
    }
    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=True: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul, raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(
        cli_module,
        "_run_init_discovery_backfill_async",
        fake_discovery_backfill,
    )
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    # v0.3.21+: init now uses split enqueue/collect APIs so the
    # XHS task can run in parallel with B站 fetches. The test fakes
    # both halves: enqueue returns a fake task id (so the "skipped"
    # branch isn't taken) and collect returns the synthetic event
    # plus the "ok" status.
    monkeypatch.setattr(
        cli_module,
        "_enqueue_xhs_bootstrap_task",
        lambda: "fake-xhs-task-id",
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, **_: ([xhs_event], {"saved": 1, "liked": 0, "xhs_history": 0}, "ok"),
        raising=False,
    )
    # Keep the legacy single-shot wrapper monkeypatched too in case
    # any old test fixture still calls it indirectly.
    monkeypatch.setattr(
        cli_module,
        "_import_xhs_bootstrap_events",
        lambda: ([xhs_event], {"saved": 1, "liked": 0, "xhs_history": 0}),
        raising=False,
    )

    # Selection is opt-in: unselected collectors must not run or consume their
    # wait budget. Explicitly select XHS so this test exercises its pipeline.
    result = runner.invoke(app, ["init", "--yes-xhs"])

    assert result.exit_code == 0
    assert "小红书" in result.stdout
    assert fake_soul.analyzed_events
    analyzed_events = fake_soul.analyzed_events[0]
    assert any(
        event.get("metadata", {}).get("source_platform") == "xiaohongshu"
        for event in analyzed_events
    )
    assert fake_soul.built_history
    built_history = fake_soul.built_history[0]
    assert any(item.get("title") == "小红书收藏咖啡" for item in built_history)


def test_init_includes_douyin_bootstrap_events_in_analysis_and_profile(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.sources.dy_tasks import dy_bootstrap_videos_to_events

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(self, **_: object) -> list[object]:
            return []

        async def get_following(self, **_: object) -> list[object]:
            return []

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []
            self.built_history: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            self.built_history.append(history)
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    dy_events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_collect",
                "title": "抖音收藏咖啡",
                "url": "https://www.douyin.com/video/dy-fav-1",
                "aweme_id": "dy-fav-1",
                "author": "抖音作者",
            },
            {
                "scope": "dy_like",
                "title": "抖音点赞历史",
                "url": "https://www.douyin.com/video/dy-like-1",
                "aweme_id": "dy-like-1",
                "author": "点赞作者",
            },
        ]
    )
    fake_memory = FakeMemoryManager()
    fake_soul = FakeSoulEngine()
    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()

    def fail_xhs_enqueue() -> str | None:
        raise AssertionError("--no-xhs should skip xhs enqueue")

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=True: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: fake_soul, raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(
        cli_module,
        "_run_init_discovery_backfill_async",
        fake_discovery_backfill,
    )
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", fail_xhs_enqueue, raising=False)
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "fake-dy-task-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, **_: (
            dy_events,
            {"dy_post": 0, "dy_collect": 1, "dy_like": 1, "dy_follow": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["init", "--no-xhs", "--yes-douyin"])

    assert result.exit_code == 0, result.output
    assert "抖音" in result.stdout
    assert "抖音信号" in result.stdout
    assert "收藏" in result.stdout
    assert "点赞" in result.stdout
    assert fake_soul.analyzed_events
    analyzed_events = fake_soul.analyzed_events[0]
    assert any(
        event.get("metadata", {}).get("source_platform") == "douyin" for event in analyzed_events
    )
    assert fake_soul.built_history
    built_history = fake_soul.built_history[0]
    assert any(
        item.get("title") == "抖音收藏咖啡"
        and item.get("source_platform") == "douyin"
        and "抖音收藏" in str(item.get("context", ""))
        for item in built_history
    )
    assert any(
        item.get("title") == "抖音点赞历史"
        and item.get("source_platform") == "douyin"
        and "抖音点赞" in str(item.get("context", ""))
        for item in built_history
    )


def test_collect_xhs_bootstrap_events_status_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """v0.3.21+: _collect_xhs_bootstrap_events must return one of
    five status labels (ok / empty / timeout / failed / skipped) so
    init can print the right user-facing message. Before this split,
    ``timeout`` and ``empty`` and ``failed`` all degraded to "未导入"
    silently — users had no way to tell whether the extension was
    offline, the page was empty, or the backend errored.
    """
    import json

    from openbiliclaw.cli import _collect_xhs_bootstrap_events

    class FakeQueue:
        def __init__(self, status_seq, result_payload=None):
            self._status_seq = list(status_seq)
            self._payload = result_payload or {}

        def get(self, _task_id):
            if not self._status_seq:
                return {"status": "completed", "result_json": json.dumps(self._payload)}
            status = self._status_seq.pop(0)
            return {"status": status, "result_json": json.dumps(self._payload)}

    class FakeDatabase:
        conn = object()

    # Status: ok — task completes with notes
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(
        "openbiliclaw.sources.xhs_tasks.XhsTaskQueue",
        lambda _db: FakeQueue(
            ["pending", "completed"],
            {
                "notes": [
                    {
                        "scope": "saved",
                        "title": "示例笔记",
                        "url": "https://www.xiaohongshu.com/explore/x1",
                    }
                ],
                "scope_counts": {"saved": 1, "liked": 0, "xhs_history": 0},
            },
        ),
    )
    events, counts, status = _collect_xhs_bootstrap_events("task-1", max_wait_seconds=2)
    assert status == "ok"
    assert events
    assert counts["saved"] == 1

    # Status: empty — task completes but 0 notes
    monkeypatch.setattr(
        "openbiliclaw.sources.xhs_tasks.XhsTaskQueue",
        lambda _db: FakeQueue(
            ["completed"],
            {"notes": [], "scope_counts": {"saved": 0, "liked": 0, "xhs_history": 0}},
        ),
    )
    events, counts, status = _collect_xhs_bootstrap_events("task-2", max_wait_seconds=2)
    assert status == "empty"
    assert events == []

    # Status: failed — backend marks task as failed
    monkeypatch.setattr(
        "openbiliclaw.sources.xhs_tasks.XhsTaskQueue",
        lambda _db: FakeQueue(["failed"]),
    )
    events, counts, status = _collect_xhs_bootstrap_events("task-3", max_wait_seconds=2)
    assert status == "failed"

    # Status: timeout — wait deadline expires, task still pending
    monkeypatch.setattr(
        "openbiliclaw.sources.xhs_tasks.XhsTaskQueue",
        lambda _db: FakeQueue(["pending", "pending", "pending", "pending", "pending", "pending"]),
    )
    events, counts, status = _collect_xhs_bootstrap_events("task-4", max_wait_seconds=0.1)
    assert status == "timeout"

    # Status: skipped — no task_id (DB unavailable / budget exhausted)
    events, counts, status = _collect_xhs_bootstrap_events(None, max_wait_seconds=2)
    assert status == "skipped"
    assert events == []
    assert counts == {}


def test_collect_source_bootstrap_events_default_wait_is_180_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from openbiliclaw.cli import (
        _collect_dy_bootstrap_events,
        _collect_xhs_bootstrap_events,
    )

    class FakeDatabase:
        conn = object()

    class AlwaysPendingQueue:
        def __init__(self, _db: object) -> None:
            self.get_calls = 0

        def get(self, _task_id: str) -> dict[str, str]:
            self.get_calls += 1
            return {"status": "pending", "result_json": "{}"}

    xhs_queue = AlwaysPendingQueue(FakeDatabase())
    dy_queue = AlwaysPendingQueue(FakeDatabase())
    monkeypatch.delenv("OPENBILICLAW_XHS_BOOTSTRAP_WAIT_SECONDS", raising=False)
    monkeypatch.delenv("OPENBILICLAW_DY_BOOTSTRAP_WAIT_SECONDS", raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    monkeypatch.setattr("openbiliclaw.sources.xhs_tasks.XhsTaskQueue", lambda _db: xhs_queue)
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", lambda _db: dy_queue)

    xhs_ticks = iter([0.0, 179.9, 180.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(xhs_ticks))
    _events, _counts, xhs_status = _collect_xhs_bootstrap_events("xhs-task")
    dy_ticks = iter([0.0, 179.9, 180.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(dy_ticks))
    _events, _counts, dy_status = _collect_dy_bootstrap_events("dy-task")

    assert xhs_status == "timeout"
    assert dy_status == "timeout"
    assert xhs_queue.get_calls == 2
    assert dy_queue.get_calls == 2


def test_enqueue_xhs_bootstrap_task_uses_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v0.3.21+: scroll rounds and item caps are env-tunable.
    Verifies the env vars actually flow through to the queue payload."""
    from openbiliclaw.cli import _enqueue_xhs_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db):
            pass

        def enqueue_with_id(self, task_type, payload, *, daily_budget):
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "task-xyz"

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.xhs_tasks.XhsTaskQueue", FakeQueue)
    monkeypatch.setenv("OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS", "5")
    monkeypatch.setenv("OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS", "100")

    task_id = _enqueue_xhs_bootstrap_task()
    assert task_id == "task-xyz"
    assert captured["task_type"] == "bootstrap_profile"
    assert captured["payload"]["max_scroll_rounds"] == 5
    assert captured["payload"]["max_items_per_scope"] == 100
    assert captured["payload"]["scopes"] == ["saved", "liked", "xhs_history"]


def test_enqueue_xhs_bootstrap_task_defaults_to_300_items_per_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_xhs_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db):
            pass

        def enqueue_with_id(self, task_type, payload, *, daily_budget):
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "task-default"

    class FakeDatabase:
        conn = object()

    monkeypatch.delenv("OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS", raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.xhs_tasks.XhsTaskQueue", FakeQueue)

    assert _enqueue_xhs_bootstrap_task(force=True) == "task-default"
    assert captured["payload"]["max_items_per_scope"] == 300


def test_enqueue_xhs_bootstrap_task_reuses_recent_task_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_xhs_bootstrap_task

    class FakeQueue:
        def __init__(self, _db):
            pass

        def find_recent_task(self, task_type, *, recent_hours, statuses=None):
            assert task_type == "bootstrap_profile"
            assert recent_hours > 0
            return {"id": "recent-task-id", "status": "completed"}

        def enqueue_with_id(self, task_type, payload, *, daily_budget):
            raise AssertionError("recent bootstrap task should be reused")

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.xhs_tasks.XhsTaskQueue", FakeQueue)

    assert _enqueue_xhs_bootstrap_task() == "recent-task-id"


def test_enqueue_xhs_bootstrap_task_force_bypasses_recent_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_xhs_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db):
            pass

        def find_recent_task(self, task_type, *, recent_hours, statuses=None):
            raise AssertionError("force should not consult recent bootstrap tasks")

        def enqueue_with_id(self, task_type, payload, *, daily_budget):
            captured["task_type"] = task_type
            return "fresh-task-id"

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.xhs_tasks.XhsTaskQueue", FakeQueue)

    assert _enqueue_xhs_bootstrap_task(force=True) == "fresh-task-id"
    assert captured["task_type"] == "bootstrap_profile"


def test_ask_xhs_inclusion_non_interactive_terminal_defaults_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-interactive init should not enable XHS unless a flag opts in."""
    from openbiliclaw.cli import _ask_xhs_inclusion

    monkeypatch.delenv("OPENBILICLAW_NO_XHS", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: False)
    assert _ask_xhs_inclusion() is False


def test_ask_xhs_inclusion_prompt_defaults_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _ask_xhs_inclusion

    defaults: list[bool | None] = []

    def fake_confirm(prompt: str, *args: object, **kwargs: object) -> bool:
        assert prompt == "加入小红书数据?"
        defaults.append(cast("bool | None", kwargs.get("default")))
        return False

    monkeypatch.delenv("OPENBILICLAW_NO_XHS", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module.typer, "confirm", fake_confirm)

    assert _ask_xhs_inclusion() is False
    assert defaults == [False]


def test_ask_dy_inclusion_prompt_defaults_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _ask_dy_inclusion

    defaults: list[bool | None] = []

    def fake_confirm(prompt: str, *args: object, **kwargs: object) -> bool:
        assert prompt == "加入抖音数据?"
        defaults.append(cast("bool | None", kwargs.get("default")))
        return False

    monkeypatch.delenv("OPENBILICLAW_NO_DOUYIN", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module.typer, "confirm", fake_confirm)

    assert _ask_dy_inclusion() is False
    assert defaults == [False]


def test_ask_yt_inclusion_prompt_defaults_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _ask_yt_inclusion

    defaults: list[bool | None] = []

    def fake_confirm(prompt: str, *args: object, **kwargs: object) -> bool:
        assert prompt == "加入 YouTube 数据?"
        defaults.append(cast("bool | None", kwargs.get("default")))
        return False

    monkeypatch.delenv("OPENBILICLAW_NO_YOUTUBE", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module.typer, "confirm", fake_confirm)

    assert _ask_yt_inclusion() is False
    assert defaults == [False]


def test_ask_xhs_inclusion_env_var_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``OPENBILICLAW_NO_XHS=1`` keeps the explicit env opt-out behavior."""
    from openbiliclaw.cli import _ask_xhs_inclusion

    monkeypatch.setenv("OPENBILICLAW_NO_XHS", "1")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    assert _ask_xhs_inclusion() is False


def test_init_youtube_env_skip_overrides_yes_flag(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    """OPENBILICLAW_NO_YOUTUBE=1 must win even when scripts pass --yes-youtube."""

    class FakeDatabase:
        def max_llm_usage_id(self) -> None:
            return None

        def count_pool_candidates(self) -> int:
            return 0

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [{"title": "B 站历史", "author_name": "UP 主"}]

        async def get_all_favorites(self, **_: object) -> list[object]:
            return []

        async def get_following(self, **_: object) -> list[object]:
            return []

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            return None

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    enqueue_calls: list[bool] = []

    def fake_enqueue_youtube() -> str | None:
        enqueue_calls.append(True)
        return "fake-yt-task-id"

    monkeypatch.setattr(cli_module, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: FakeBilibiliClient())
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: FakeMemoryManager())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine())
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(cli_module, "_run_init_discovery_backfill_async", fake_discovery_backfill)
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli_module, "_enqueue_yt_bootstrap_task", fake_enqueue_youtube)

    result = runner.invoke(
        app,
        ["init", "--no-xhs", "--no-douyin", "--yes-youtube"],
        env={"OPENBILICLAW_NO_YOUTUBE": "1"},
    )

    assert result.exit_code == 0, result.stdout
    assert enqueue_calls == []
    assert "OPENBILICLAW_NO_YOUTUBE=1" in result.stdout


def test_persist_init_source_enabled_flags_updates_optional_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _persist_init_source_enabled_flags
    from openbiliclaw.config import Config

    config = Config()
    saved: list[Config] = []
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: config)
    monkeypatch.setattr("openbiliclaw.config.save_config", lambda cfg: saved.append(cfg))

    _persist_init_source_enabled_flags(
        include_xhs=False,
        include_dy=True,
        include_yt=True,
        include_zhihu=True,
        include_reddit=True,
    )

    assert config.sources.xiaohongshu.enabled is False
    assert config.sources.douyin.enabled is True
    assert config.sources.youtube.enabled is True
    assert config.sources.zhihu.enabled is True
    assert config.sources.reddit.enabled is True
    assert saved == [config]


def test_select_init_source_shares_accepts_suggested_ratios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _select_init_source_shares

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module.typer, "confirm", lambda *args, **kwargs: True)

    selected = _select_init_source_shares(
        {"bilibili": 900, "xiaohongshu": 100, "douyin": 9, "youtube": 400},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": True,
            "douyin": True,
            "youtube": True,
        },
        configured_shares={
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        },
    )

    assert selected == {
        "bilibili": 8,
        "xiaohongshu": 3,
        "douyin": 1,
        "youtube": 5,
        # twitter carries its default share (1) even when not in the import's
        # enabled_sources; effective_pool_source_shares drops it while disabled.
        "twitter": 1,
        "zhihu": 1,
        "reddit": 1,
        "bangumi": 1,
        "github": 1,
        "linuxdo": 1,
        "weibo": 1,
        "v2ex": 1,
    }


def test_select_init_source_shares_accepts_manual_ratios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _select_init_source_shares

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module.typer, "confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        cli_module.typer,
        "prompt",
        lambda *args, **kwargs: "bilibili=6,xiaohongshu=2,youtube=3",
    )

    selected = _select_init_source_shares(
        {"bilibili": 10, "xiaohongshu": 10, "youtube": 10},
        enabled_sources={
            "bilibili": True,
            "xiaohongshu": True,
            "douyin": False,
            "youtube": True,
        },
        configured_shares={
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        },
    )

    assert selected == {
        "bilibili": 6,
        "xiaohongshu": 2,
        "douyin": 1,
        "youtube": 3,
        # Optional disabled sources carry their default share (1); dropped
        # later while disabled.
        "twitter": 1,
        "zhihu": 1,
        "reddit": 1,
        "bangumi": 1,
        "github": 1,
        "linuxdo": 1,
        "weibo": 1,
        "v2ex": 1,
    }


def test_init_no_xhs_flag_skips_enqueue(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    """v0.3.27+: ``openbiliclaw init --no-xhs`` should completely skip
    the bootstrap enqueue path so users who don't want xhs touched
    can be sure no task hits the queue."""

    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

        async def get_all_favorites(self, **_: object) -> list[object]:
            return []

        async def get_following(self, **_: object) -> list[object]:
            return []

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.analyzed_events: list[list[dict[str, object]]] = []

        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            self.analyzed_events.append(events)

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    enqueue_calls: list[bool] = []

    def fake_enqueue() -> str | None:
        enqueue_calls.append(True)
        return "fake-task-id"

    async def passthrough_progress(coro: object, **_: object) -> object:
        return await coro  # type: ignore[misc]

    async def fake_discovery_backfill(*_: object, **__: object) -> int:
        return 0

    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=True: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module, "_build_memory_manager", lambda: FakeMemoryManager(), raising=False
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_run_with_progress", passthrough_progress)
    monkeypatch.setattr(
        cli_module,
        "_run_init_discovery_backfill_async",
        fake_discovery_backfill,
    )
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "_enqueue_xhs_bootstrap_task",
        fake_enqueue,
        raising=False,
    )

    result = runner.invoke(app, ["init", "--no-xhs"])

    assert result.exit_code == 0, f"unexpected failure:\n{result.stdout}"
    # Critical: --no-xhs should fully skip the enqueue path.
    assert enqueue_calls == [], (
        f"expected --no-xhs to skip xhs enqueue, but got {len(enqueue_calls)} call(s)"
    )
    assert "跳过小红书数据接入" in result.stdout


def test_init_backfills_pool_in_stages_until_target_is_reached(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            return None

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(
                    interests=[
                        InterestTag(name="人工智能", category="科技", weight=0.96),
                        InterestTag(name="篮球战术", category="体育", weight=0.72),
                    ]
                ),
            )

    class FakeDatabase:
        def __init__(self) -> None:
            self.pool_count = 0

        def count_pool_candidates(self) -> int:
            return self.pool_count

    class FakeDiscoveryEngine:
        def __init__(self, database: FakeDatabase) -> None:
            self.database = database
            self.calls: list[dict[str, object]] = []

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
            **kwargs: object,
        ) -> list[DiscoveredContent]:
            self.calls.append(
                {
                    "strategies": strategies,
                    "limit": limit,
                    "pool_snapshot": kwargs.get("pool_snapshot"),
                }
            )
            if strategies == ["search", "trending", "related_chain", "explore"]:
                self.database.pool_count = 15
            else:
                raise AssertionError(f"unexpected strategies: {strategies}")
            return [
                DiscoveredContent(
                    bvid=f"BV1-{len(self.calls)}",
                    title="发现内容",
                    up_name="发现实验室",
                    relevance_score=0.8,
                )
            ]

    fake_database = FakeDatabase()
    fake_discovery = FakeDiscoveryEngine(fake_database)
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: FakeMemoryManager(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: fake_discovery,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert len(fake_discovery.calls) == 1
    assert fake_discovery.calls[0]["strategies"] == [
        "search",
        "trending",
        "related_chain",
        "explore",
    ]
    assert fake_discovery.calls[0]["limit"] == 20
    pool_snapshot = cast("Any", fake_discovery.calls[0]["pool_snapshot"])
    assert pool_snapshot is not None
    assert pool_snapshot.cold_start is True
    assert "人工智能" in pool_snapshot.saturated_topics
    assert "篮球战术" in pool_snapshot.undercovered_axes
    assert "补货阶段 1/1" in result.stdout
    assert "search + trending + related_chain + explore" in result.stdout
    assert "当前池子 0/15" in result.stdout
    assert "阶段完成" in result.stdout
    assert "当前池子 15/15" in result.stdout
    assert "首轮发现内容" in result.stdout


def test_init_skips_backfill_when_pool_target_is_already_reached(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A", "view_at": 1710000000},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            return None

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    class FakeDatabase:
        def __init__(self) -> None:
            self.pool_count = 45

        def count_pool_candidates(self) -> int:
            return self.pool_count

    class FakeDiscoveryEngine:
        def __init__(self, database: FakeDatabase) -> None:
            self.database = database
            self.calls: list[tuple[list[str] | None, int]] = []

        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
            **_: object,
        ) -> list[DiscoveredContent]:
            self.calls.append((strategies, limit))
            self.database.pool_count = 100
            return [
                DiscoveredContent(
                    bvid="BV1DONE",
                    title="发现内容",
                    up_name="发现实验室",
                    relevance_score=0.8,
                )
            ]

    fake_database = FakeDatabase()
    fake_discovery = FakeDiscoveryEngine(fake_database)
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: FakeMemoryManager(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: fake_discovery,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert fake_discovery.calls == []


def test_init_reports_partial_success_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeAuthManager:
        async def get_status(self) -> AuthStatus:
            return AuthStatus(
                has_cookie=True,
                authenticated=True,
                cookie_path=tmp_path / "bilibili_cookie.json",
                username="alice",
                user_id=10086,
                message="Cookie 验证成功。",
            )

    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            return [
                {
                    "history": {"bvid": "BV1A"},
                    "title": "讲透历史叙事",
                    "author_name": "历史实验室",
                }
            ]

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

        def get_layer(self, name: str) -> _FakeMemoryLayer:
            assert name == "preference"
            return _FakeMemoryLayer()

    class FakeSoulEngine:
        async def analyze_events(
            self,
            events: list[dict[str, object]],
            event_chunk_size: int = 0,
            **_: object,
        ) -> None:
            return None

        async def build_initial_profile(self, history: list[dict[str, object]]) -> SoulProfile:
            return SoulProfile(
                personality_portrait="稳定用户画像" * 30,
                core_traits=["理性"],
                preferences=PreferenceLayer(),
            )

    class FakeDiscoveryEngine:
        async def discover(
            self,
            profile: SoulProfile,
            strategies: list[str] | None = None,
            limit: int = 30,
            **_: object,
        ) -> list[DiscoveredContent]:
            raise RuntimeError("discovery unavailable")

    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()
    _ignore_runtime_config_error(monkeypatch)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_auth_manager", lambda: FakeAuthManager(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: FakeBilibiliClient(),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: FakeMemoryManager(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_engine",
        lambda: FakeDiscoveryEngine(),
        raising=False,
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: fake_database, raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "部分完成" in result.stdout
    assert "画像已生成" in result.stdout
    assert "discover" in result.stdout


# ---------------------------------------------------------------------------
# Local Ollama embedding wizard helpers
# ---------------------------------------------------------------------------


def test_ollama_is_running_returns_true_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class _FakeResp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            assert url.endswith("/api/version")
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    assert cli_module._ollama_is_running() is True


def test_ollama_is_running_returns_false_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    class _FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FailingClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> object:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "Client", _FailingClient)
    assert cli_module._ollama_is_running() is False


def test_ollama_has_model_matches_tagged_and_untagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {
                "models": [
                    {"name": "llama3:latest"},
                    {"name": "bge-m3:latest"},
                ]
            }

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _FakeResp:
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    assert cli_module._ollama_has_model("bge-m3") is True
    assert cli_module._ollama_has_model("nomic-embed-text") is False


def test_save_embedding_config_writes_to_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify the wizard's persistence helper writes both provider and model
    to [llm.embedding] in config.toml. Round-trips: start from a default
    config, run the helper, reload, assert the embedding section persisted."""
    from openbiliclaw.config import (
        Config,
        LLMConfig,
        load_config_with_diagnostics,
        save_config,
    )

    config_path = tmp_path / "config.toml"
    initial = Config(llm=LLMConfig(default_provider="gemini"))
    save_config(initial, config_path)

    # Redirect _project_root() to tmp_path. monkeypatch.chdir alone is
    # NOT enough — _project_root() checks the package install dir first
    # and would happily clobber the developer's real config.toml.
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    cli_module._save_embedding_config(provider="ollama", model="bge-m3")

    reloaded, _ = load_config_with_diagnostics()
    assert reloaded.llm.embedding.provider == "ollama"
    assert reloaded.llm.embedding.model == "bge-m3"
    assert reloaded.llm.embedding.base_url == "http://127.0.0.1:11434/v1"


def test_save_embedding_config_custom_openai_compat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 3 option 3: a vLLM-style OpenAI-compatible embedding gateway.

    The wizard collects provider="openai" plus a custom base_url / api_key /
    model, and the helper has to write all four into config.toml: the
    embedding section gets provider+model, and the [llm.openai] block gets
    base_url+api_key so the LLM registry can resolve the endpoint.
    """
    from openbiliclaw.config import (
        Config,
        LLMConfig,
        load_config_with_diagnostics,
        save_config,
    )

    config_path = tmp_path / "config.toml"
    save_config(Config(llm=LLMConfig(default_provider="claude")), config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    cli_module._save_embedding_config(
        provider="openai",
        model="bge-m3",
        base_url="http://localhost:8000/v1",
        api_key="sk-local",
    )

    reloaded, _ = load_config_with_diagnostics()
    assert reloaded.llm.embedding.provider == "openai"
    assert reloaded.llm.embedding.model == "bge-m3"
    assert reloaded.llm.embedding.base_url == "http://localhost:8000/v1"
    assert reloaded.llm.embedding.api_key == "sk-local"


def test_interactive_embedding_setup_option4_allows_empty_no_auth_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Option 4 tells users a no-auth gateway may leave the API key blank.

    The wizard must pass that empty key through (the registry injects an
    internal placeholder for the OpenAI SDK) instead of inventing a fake key
    or writing a config that the backend silently refuses to build."""
    answers = iter(["4", "http://127.0.0.1:8000/v1", "", "bge-m3"])
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: next(answers))
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli_module,
        "_save_embedding_config",
        lambda **kwargs: captured.update(kwargs),
    )

    cli_module._interactive_embedding_setup("openai")

    assert captured == {
        "provider": "openai",
        "model": "bge-m3",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "",
    }


def test_interactive_embedding_setup_option4_rejects_empty_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Option 4 is the custom-endpoint branch, so an empty base_url cannot
    produce a usable provider. Refuse to save instead of writing a config
    that silently disables embedding."""
    answers = iter(["4", "", "sk-ignored", "bge-m3"])
    monkeypatch.setattr(typer, "prompt", lambda *args, **kwargs: next(answers))
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "_save_embedding_config",
        lambda **kwargs: saved.append(kwargs),
    )

    cli_module._interactive_embedding_setup("openai")

    assert saved == []


def test_save_module_overrides_writes_per_module_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 4: per-module overrides round-trip through config.toml."""
    from openbiliclaw.config import (
        Config,
        LLMConfig,
        LLMInstanceConfig,
        load_config_with_diagnostics,
        save_config,
    )

    config_path = tmp_path / "config.toml"
    save_config(
        Config(
            llm=LLMConfig(
                instance_routing=True,
                instances={
                    "openai-main": LLMInstanceConfig(
                        name="OpenAI",
                        provider_type="openai",
                        api_key="sk-openai",
                        model="gpt-main",
                    ),
                    "deepseek-fast": LLMInstanceConfig(
                        name="DeepSeek",
                        provider_type="deepseek",
                        api_key="sk-deepseek",
                        model="deepseek-chat",
                        base_url="https://api.deepseek.com",
                    ),
                    "claude-quality": LLMInstanceConfig(
                        name="Claude",
                        provider_type="claude",
                        api_key="sk-claude",
                        model="claude-sonnet-4-5-20250929",
                    ),
                },
                default_chain=["openai-main"],
            )
        ),
        config_path,
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    cli_module._save_module_overrides(
        {
            "discovery": {"provider": "deepseek", "model": "deepseek-chat"},
            "soul": {"provider": "claude", "model": "claude-sonnet-4-5-20250929"},
            "evaluation": {"provider": "", "model": ""},  # no-op leaves defaults
        }
    )

    reloaded, _ = load_config_with_diagnostics()
    assert reloaded.llm.instance_routing is True
    assert reloaded.llm.discovery.inherit is False
    assert reloaded.llm.discovery.chain == ["deepseek-fast"]
    assert reloaded.llm.soul.inherit is False
    assert reloaded.llm.soul.chain == ["claude-quality"]
    assert reloaded.llm.evaluation.inherit is True
    assert reloaded.llm.evaluation.chain == []


def test_build_recommendation_engine_wires_module_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from openbiliclaw.config import Config, LLMConfig, save_config

    config_path = tmp_path / "config.toml"
    config = Config(llm=LLMConfig(default_provider="openai"))
    config.llm.recommendation.provider = "deepseek"
    config.llm.recommendation.model = "deepseek-chat"
    save_config(config, config_path)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_registry",
        lambda: SimpleNamespace(default_provider="openai"),
    )
    monkeypatch.setattr("openbiliclaw.llm.registry.build_embedding_service", lambda *_: None)

    engine = cli_module._build_recommendation_engine()

    assert engine._llm.module_overrides["recommendation"].provider == "deepseek"
    assert engine._llm.module_overrides["recommendation"].model == "deepseek-chat"


def test_save_runtime_provider_config_persists_triplet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 2: full triplet (api_key, base_url, model) round-trips,
    and empty values do not clobber existing config (so a user pressing
    Enter at the prompt accepts the saved value rather than blanking it).
    """
    from openbiliclaw.config import (
        Config,
        LLMConfig,
        LLMProviderConfig,
        load_config_with_diagnostics,
        save_config,
    )

    config_path = tmp_path / "config.toml"
    save_config(
        Config(
            llm=LLMConfig(
                default_provider="claude",
                openai=LLMProviderConfig(
                    api_key="sk-old",
                    base_url="https://old.example.com/v1",
                    model="gpt-old",
                ),
            )
        ),
        config_path,
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Write triplet — should switch default provider and overwrite all three.
    cli_module._save_runtime_provider_config(
        "openai",
        api_key="sk-new",
        base_url="https://new.example.com/v1",
        model="gpt-4o-mini",
    )
    reloaded, _ = load_config_with_diagnostics()
    assert reloaded.llm.default_provider == "openai"
    assert reloaded.llm.openai.api_key == "sk-new"
    assert reloaded.llm.openai.base_url == "https://new.example.com/v1"
    assert reloaded.llm.openai.model == "gpt-4o-mini"

    # Now an "accept defaults" follow-up: empty params must NOT blank the
    # values written above.
    cli_module._save_runtime_provider_config("openai", api_key="", base_url="", model="")
    reloaded2, _ = load_config_with_diagnostics()
    assert reloaded2.llm.openai.api_key == "sk-new"
    assert reloaded2.llm.openai.base_url == "https://new.example.com/v1"
    assert reloaded2.llm.openai.model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Douyin bootstrap CLI helpers (Task 6 of douyin import plan)
# ---------------------------------------------------------------------------


def test_enqueue_dy_bootstrap_task_uses_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that OPENBILICLAW_DY_BOOTSTRAP_* env vars actually flow
    through into the DyTaskQueue payload, mirroring the XHS variant."""
    from openbiliclaw.cli import _enqueue_dy_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "dy-task-xyz"

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)
    monkeypatch.setenv("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "8")
    monkeypatch.setenv("OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS", "120")

    task_id = _enqueue_dy_bootstrap_task()
    assert task_id == "dy-task-xyz"
    assert captured["task_type"] == "bootstrap_profile"
    assert captured["payload"]["max_scroll_rounds"] == 8
    assert captured["payload"]["max_items_per_scope"] == 120
    assert sorted(captured["payload"]["scopes"]) == [
        "dy_collect",
        "dy_follow",
        "dy_like",
        "dy_post",
    ]


def test_enqueue_dy_bootstrap_task_defaults_to_300_items_per_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_dy_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "dy-task-default"

    class FakeDatabase:
        conn = object()

    monkeypatch.delenv("OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS", raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    assert _enqueue_dy_bootstrap_task() == "dy-task-default"
    assert captured["payload"]["max_items_per_scope"] == 300


def test_enqueue_dy_bootstrap_task_reuses_recent_task_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_dy_bootstrap_task

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def find_recent_task(self, task_type: str, *, recent_hours: float, statuses=None):
            assert task_type == "bootstrap_profile"
            assert recent_hours > 0
            return {"id": "recent-dy-task-id", "status": "completed"}

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            raise AssertionError("recent dy bootstrap task should be reused")

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    assert _enqueue_dy_bootstrap_task() == "recent-dy-task-id"


def test_enqueue_dy_bootstrap_task_retries_recent_degraded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_dy_bootstrap_task

    captured: dict[str, object] = {}

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def find_recent_task(self, task_type: str, *, recent_hours: float, statuses=None):
            assert task_type == "bootstrap_profile"
            assert recent_hours > 0
            return {
                "id": "recent-degraded-dy-task",
                "status": "completed",
                "result_json": json.dumps({"status": "degraded"}),
            }

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "fresh-dy-task"

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)
    monkeypatch.setattr(cli_module, "_kick_task_dispatcher", lambda _source: None)

    assert _enqueue_dy_bootstrap_task() == "fresh-dy-task"
    assert captured["task_type"] == "bootstrap_profile"


def test_enqueue_dy_bootstrap_task_returns_none_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_dy_bootstrap_task

    def _raises() -> object:
        raise RuntimeError("db not initialised")

    monkeypatch.setattr(cli_module, "_get_runtime_database", _raises)
    assert _enqueue_dy_bootstrap_task() is None


def test_enqueue_yt_bootstrap_task_reuses_recent_task_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_yt_bootstrap_task

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def find_recent_task(self, task_type: str, *, recent_hours: float, statuses=None):
            assert task_type == "bootstrap_profile"
            assert recent_hours > 0
            return {"id": "recent-yt-task-id", "status": "completed"}

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            raise AssertionError("recent yt bootstrap task should be reused")

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.yt_tasks.YtTaskQueue", FakeQueue)

    assert _enqueue_yt_bootstrap_task() == "recent-yt-task-id"


def test_enqueue_yt_bootstrap_task_defaults_to_300_items_per_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_yt_bootstrap_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "yt-task-default"

    class FakeDatabase:
        conn = object()

    monkeypatch.delenv("OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS", raising=False)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.yt_tasks.YtTaskQueue", FakeQueue)

    assert _enqueue_yt_bootstrap_task() == "yt-task-default"
    assert captured["payload"]["max_items_per_scope"] == 300


def test_collect_dy_bootstrap_events_returns_skipped_for_no_task_id() -> None:
    """No task_id (DB unavailable / budget exhausted) → silent skip."""
    from openbiliclaw.cli import _collect_dy_bootstrap_events

    events, counts, status = _collect_dy_bootstrap_events(None, max_wait_seconds=2)
    assert events == []
    assert counts == {}
    assert status == "skipped"


def test_collect_dy_bootstrap_events_extracts_videos_from_completed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed task with a videos[] payload converts into events
    using dy_bootstrap_videos_to_events and surfaces scope_counts."""
    import json

    from openbiliclaw.cli import _collect_dy_bootstrap_events

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def get(self, task_id: str) -> dict:
            assert task_id == "task-1"
            return {
                "id": "task-1",
                "status": "completed",
                "result_json": json.dumps(
                    {
                        "videos": [
                            {
                                "scope": "dy_collect",
                                "title": "demo",
                                "url": "https://www.douyin.com/video/a",
                                "aweme_id": "a",
                                "author": "u",
                            },
                            {
                                "scope": "dy_like",
                                "title": "liked one",
                                "url": "https://www.douyin.com/video/b",
                                "aweme_id": "b",
                            },
                        ],
                        "scope_counts": {
                            "dy_post": 0,
                            "dy_collect": 1,
                            "dy_like": 1,
                            "dy_follow": 0,
                        },
                    }
                ),
            }

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    events, counts, status = _collect_dy_bootstrap_events("task-1", max_wait_seconds=0)
    assert status == "ok"
    assert [e["event_type"] for e in events] == ["favorite", "like"]
    assert all(e["metadata"]["source_platform"] == "douyin" for e in events)
    assert counts["dy_collect"] == 1
    assert counts["dy_like"] == 1
    assert counts["dy_post"] == 0
    assert counts["dy_follow"] == 0


def test_collect_dy_bootstrap_events_surfaces_terminal_degraded_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from openbiliclaw.cli import _collect_dy_bootstrap_events

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def get(self, task_id: str) -> dict:
            return {
                "id": task_id,
                "status": "completed",
                "result_json": json.dumps(
                    {
                        "status": "degraded",
                        "videos": [
                            {
                                "scope": "dy_collect",
                                "title": "首屏收藏",
                                "url": "https://www.douyin.com/video/a",
                                "aweme_id": "a",
                            }
                        ],
                        "scope_counts": {"dy_collect": 1},
                    }
                ),
            }

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    events, counts, status = _collect_dy_bootstrap_events("task-degraded", max_wait_seconds=0)
    assert status == "degraded"
    assert len(events) == 1
    assert counts["dy_collect"] == 1


def test_collect_dy_bootstrap_events_returns_timeout_when_task_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _collect_dy_bootstrap_events

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def get(self, task_id: str) -> dict:
            return {"id": task_id, "status": "pending", "result_json": "{}"}

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    _events, _counts, status = _collect_dy_bootstrap_events("task-1", max_wait_seconds=0.05)
    assert status == "timeout"


def test_collect_dy_bootstrap_events_surfaces_failed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _collect_dy_bootstrap_events

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def get(self, task_id: str) -> dict:
            return {
                "id": task_id,
                "status": "failed",
                "result_json": '{"error": "captcha"}',
            }

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    _events, _counts, status = _collect_dy_bootstrap_events("task-1", max_wait_seconds=0)
    assert status == "failed"


def test_enqueue_dy_search_task_records_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.cli import _enqueue_dy_search_task

    captured: dict = {}

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def enqueue_with_id(self, task_type: str, payload: dict, *, daily_budget: int) -> str:
            captured["task_type"] = task_type
            captured["payload"] = payload
            captured["daily_budget"] = daily_budget
            return "dy-search-task"

    class FakeDatabase:
        conn = object()

    config = config_module.Config()
    config.sources.douyin.daily_search_budget = 37
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr(config_module, "load_config", lambda: config)
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    task_id = _enqueue_dy_search_task(("猫", "美食"), max_items_per_keyword=7)
    assert task_id == "dy-search-task"
    assert captured["task_type"] == "search"
    assert captured["payload"] == {
        "keywords": ["猫", "美食"],
        "max_items_per_keyword": 7,
    }
    assert captured["daily_budget"] == 37


def test_search_douyin_timeout_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    runner: CliRunner,
) -> None:
    monkeypatch.setattr(cli_module, "_enqueue_dy_search_task", lambda *args, **kwargs: "task-1")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_search_results",
        lambda *args, **kwargs: ([], {}, "timeout"),
    )
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["search-douyin", "--keyword", "猫", "--wait-seconds", "1"])

    assert result.exit_code == 1
    assert "任务超时" in result.stdout


def test_collect_dy_search_results_reads_completed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from openbiliclaw.cli import _collect_dy_search_results

    class FakeQueue:
        def __init__(self, _db: object) -> None:
            pass

        def get(self, task_id: str) -> dict:
            assert task_id == "search-1"
            return {
                "id": "search-1",
                "status": "completed",
                "result_json": json.dumps(
                    {
                        "videos": [
                            {
                                "scope": "dy_search",
                                "title": "搜索结果",
                                "url": "https://www.douyin.com/video/7788",
                                "aweme_id": "7788",
                            }
                        ],
                        "scope_counts": {"dy_search": 1},
                    }
                ),
            }

    class FakeDatabase:
        conn = object()

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    videos, counts, status = _collect_dy_search_results("search-1", max_wait_seconds=0)
    assert status == "ok"
    assert counts == {"dy_search": 1}
    assert videos[0]["aweme_id"] == "7788"


def test_dy_events_to_history_items_preserves_context_and_source_platform() -> None:
    """The history-item adapter must keep the natural-language context
    field and tag rows with source_platform=douyin so cross-source
    analysis stays uniform with the XHS / B站 paths."""
    from openbiliclaw.cli import _dy_events_to_history_items
    from openbiliclaw.sources.dy_tasks import dy_bootstrap_videos_to_events

    events = dy_bootstrap_videos_to_events(
        [
            {
                "scope": "dy_collect",
                "title": "demo title",
                "url": "https://www.douyin.com/video/zzz",
                "aweme_id": "zzz",
                "author": "作者",
            }
        ]
    )
    rows = _dy_events_to_history_items(events)
    assert len(rows) == 1
    assert rows[0]["title"] == "demo title"
    assert rows[0]["source_platform"] == "douyin"
    # The natural-language context was assembled by build_event when
    # the source helper produced the event — must survive the trip
    # through the history-row adapter.
    assert "抖音收藏" in rows[0]["context"]


def test_dy_events_to_history_items_drops_rows_with_no_title_or_url() -> None:
    from openbiliclaw.cli import _dy_events_to_history_items

    rows = _dy_events_to_history_items(
        [
            {
                "event_type": "view",
                "title": "",
                "url": "",
                "metadata": {"source_platform": "douyin"},
            },
            {"event_type": "favorite", "title": "ok", "url": "https://w/a", "metadata": {}},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "ok"


# ---------------------------------------------------------------------------
# Standalone single-source fetch commands (testing convenience, no init)
# ---------------------------------------------------------------------------


def test_fetch_douyin_command_renders_scope_counts_after_extension_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch-douyin`` is the **pure pull** command — it enqueues a
    bootstrap_profile task, waits for the extension's POST results
    (which the daemon-side endpoint already propagates to memory),
    and prints the scope_counts. CLI itself does NOT propagate events
    (the daemon's /api/sources/dy/task-result handler does it once,
    on receive), so we don't need a memory-manager fake here."""
    runner = CliRunner()

    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "task-fake-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [
                {"event_type": "favorite", "title": "demo", "metadata": {}},
                {"event_type": "like", "title": "liked", "metadata": {}},
            ],
            {"dy_post": 0, "dy_collect": 1, "dy_like": 1, "dy_follow": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-douyin", "-w", "5"])
    assert result.exit_code == 0, result.output
    assert "抖音" in result.output
    # The summary should mention the count breakdown line and the
    # daemon-side propagation hint.
    assert "收藏" in result.output and "点赞" in result.output


def test_fetch_source_commands_default_wait_is_180_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    observed: dict[str, float] = {}

    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "dy-task")
    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", lambda: "xhs-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            observed.setdefault("dy", max_wait_seconds)
            and ([], {"dy_post": 0, "dy_collect": 0, "dy_like": 0, "dy_follow": 0}, "empty")
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            observed.setdefault("xhs", max_wait_seconds)
            and ([], {"saved": 0, "liked": 0, "xhs_history": 0}, "empty")
        ),
    )

    dy_result = runner.invoke(app, ["fetch-douyin"])
    xhs_result = runner.invoke(app, ["fetch-xhs"])

    assert dy_result.exit_code == 0, dy_result.output
    assert xhs_result.exit_code == 0, xhs_result.output
    assert observed == {"dy": 180.0, "xhs": 180.0}


def test_kick_task_dispatcher_uses_configured_api_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}

    class FakeResponse:
        def close(self) -> None:
            requested["closed"] = True

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        requested["url"] = getattr(request, "full_url", "")
        requested["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "openbiliclaw.config.load_config",
        lambda: SimpleNamespace(api=SimpleNamespace(port=8422)),
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    cli_module._kick_task_dispatcher("v2ex")

    assert requested == {
        "url": "http://127.0.0.1:8422/api/sources/v2ex/kick",
        "timeout": 1.0,
        "closed": True,
    }


def test_fetch_v2ex_is_read_only_four_scope_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def fake_enqueue(**kwargs: object) -> str:
        captured["enqueue"] = kwargs
        return "v2ex-task"

    def fake_collect(
        task_id: str,
        *,
        max_wait_seconds: float,
    ) -> tuple[list[dict[str, object]], dict[str, int], str]:
        captured["collect"] = (task_id, max_wait_seconds)
        return (
            [
                {
                    "event_type": "publish",
                    "title": "Topic",
                    "metadata": {"source_platform": "v2ex"},
                },
                {
                    "event_type": "favorite",
                    "title": "Saved topic",
                    "metadata": {"source_platform": "v2ex"},
                },
            ],
            {
                "public_topics": 1,
                "public_replies": 2,
                "favorite_topics": 1,
                "favorite_nodes": 3,
            },
            "partial",
        )

    monkeypatch.setattr(cli_module, "_enqueue_v2ex_bootstrap_task", fake_enqueue)
    monkeypatch.setattr(cli_module, "_collect_v2ex_bootstrap_events", fake_collect)
    monkeypatch.setattr(
        cli_module,
        "_prepare_init_runtime",
        lambda: pytest.fail("fetch-v2ex must not prepare the init runtime"),
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_manager",
        lambda: pytest.fail("fetch-v2ex must not open or write memory"),
    )

    result = runner.invoke(
        app,
        ["fetch-v2ex", "--username", "demo", "--force", "--wait-seconds", "5"],
    )

    assert result.exit_code == 0, result.output
    assert captured["enqueue"] == {
        "username": "demo",
        "profile_update": False,
        "smoke_only": True,
        "force": True,
    }
    assert captured["collect"] == ("v2ex-task", 5.0)
    assert "发布" in result.output
    assert "讨论" in result.output
    assert "收藏主题" in result.output
    assert "收藏 Node" in result.output
    assert "未写入 memory" in result.output
    assert "不作为完整收藏快照" in result.output


def test_fetch_v2ex_identity_mismatch_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        cli_module,
        "_enqueue_v2ex_bootstrap_task",
        lambda **_kwargs: "v2ex-task",
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_v2ex_bootstrap_events",
        lambda _task_id, *, max_wait_seconds: (
            [],
            {
                "public_topics": 0,
                "public_replies": 0,
                "favorite_topics": 0,
                "favorite_nodes": 0,
            },
            "identity_mismatch",
        ),
    )

    result = runner.invoke(app, ["fetch-v2ex"])

    assert result.exit_code == 1
    assert "身份" in result.output
    assert "暂停" in result.output


def test_fetch_douyin_does_not_call_prepare_init_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch-* are pure pull — they must NOT trigger init's runtime
    prep (which would force B站 cookie / auth checks the user doesn't
    care about for a single-source pull). The fixture records any
    inadvertent call so a regression here trips loudly."""
    runner = CliRunner()
    prepared = {"called": False}

    def _trip() -> None:
        prepared["called"] = True

    monkeypatch.setattr(cli_module, "_prepare_init_runtime", _trip)
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "task-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [],
            {"dy_post": 0, "dy_collect": 0, "dy_like": 0, "dy_follow": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-douyin"])
    assert result.exit_code == 0, result.output
    assert prepared["called"] is False


def test_fetch_douyin_does_not_propagate_events_cli_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon's task-result endpoint propagates events the moment
    each partial POST lands. CLI MUST NOT propagate again — that would
    double-write every event. Fail loudly if anyone wires the CLI
    propagation path back in."""
    runner = CliRunner()
    propagated: list = []

    class FakeMemoryManager:
        async def propagate_event(self, event: dict) -> None:
            propagated.append(event)

    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: FakeMemoryManager())
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "task-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [{"event_type": "favorite", "title": "x", "metadata": {}}],
            {"dy_post": 0, "dy_collect": 1, "dy_like": 0, "dy_follow": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-douyin"])
    assert result.exit_code == 0, result.output
    assert propagated == [], (
        "fetch-douyin should not propagate events CLI-side; daemon already does it"
    )


def test_fetch_zhihu_command_renders_event_counts_without_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    prepared = {"called": False}
    propagated: list[dict[str, object]] = []

    def _trip() -> None:
        prepared["called"] = True

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            propagated.append(event)

    monkeypatch.setattr(cli_module, "_prepare_init_runtime", _trip)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: FakeMemoryManager())
    monkeypatch.setattr(cli_module, "_enqueue_zhihu_bootstrap_task", lambda **_: "zhihu-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [
                {"event_type": "view", "title": "浏览", "metadata": {}},
                {"event_type": "favorite", "title": "收藏", "metadata": {}},
                {"event_type": "like", "title": "点赞", "metadata": {}},
            ],
            {
                "zhihu_read_history": 1,
                "zhihu_collection": 1,
                "zhihu_activity_like": 1,
                "zhihu_activity_favorite": 0,
            },
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-zhihu", "--profile-slug", "demo", "-w", "5"])

    assert result.exit_code == 0, result.output
    assert prepared["called"] is False
    assert propagated == []
    assert "知乎" in result.output
    assert "浏览" in result.output
    assert "收藏" in result.output
    assert "点赞" in result.output


def test_fetch_zhihu_write_memory_propagates_collected_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    propagated: list[dict[str, object]] = []

    class FakeMemoryManager:
        async def propagate_event(self, event: dict[str, object]) -> None:
            propagated.append(event)

    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: FakeMemoryManager())
    monkeypatch.setattr(cli_module, "_enqueue_zhihu_bootstrap_task", lambda **_: "zhihu-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "event_type": "view",
                    "title": "浏览",
                    "url": "https://www.zhihu.com/question/1",
                    "metadata": {"source_platform": "zhihu", "content_id": "1"},
                },
                {
                    "event_type": "favorite",
                    "title": "收藏",
                    "url": "https://www.zhihu.com/question/2",
                    "metadata": {"source_platform": "zhihu", "content_id": "2"},
                },
            ],
            {
                "zhihu_read_history": 1,
                "zhihu_collection": 1,
                "zhihu_activity_like": 0,
                "zhihu_activity_favorite": 0,
            },
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-zhihu", "--write-memory", "-w", "5"])

    assert result.exit_code == 0, result.output
    assert [event["title"] for event in propagated] == ["浏览", "收藏"]
    assert "已写入 memory" in result.output


def test_enqueue_zhihu_bootstrap_requests_activity_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "test.db")
    database.initialize()
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)
    monkeypatch.setattr(cli_module, "_kick_task_dispatcher", lambda source: None)

    task_id = cli_module._enqueue_zhihu_bootstrap_task()

    assert task_id is not None
    task = ZhihuTaskQueue(database).get(task_id)
    assert task is not None
    payload = json.loads(str(task["payload_json"]))
    assert payload["profile_slug"] == ""
    assert payload["scopes"] == [
        "zhihu_read_history",
        "zhihu_collection",
        "zhihu_activity",
    ]


def test_collect_zhihu_bootstrap_events_surfaces_login_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "test.db")
    database.initialize()
    queue = ZhihuTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
    assert task_id is not None
    assert queue.next_pending() is not None
    queue.fail(
        task_id,
        error="zhihu_login_required",
        debug={
            "login_required": True,
            "current_url": "https://www.zhihu.com/signin?next=%2F",
            "http_status": 400,
        },
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)

    events, counts, status = cli_module._collect_zhihu_bootstrap_events(task_id, max_wait_seconds=0)

    assert events == []
    assert counts["zhihu_read_history"] == 0
    assert status == "login_required"


def test_collect_zhihu_bootstrap_events_marks_in_progress_timeout_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "test.db")
    database.initialize()
    queue = ZhihuTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
    assert task_id is not None
    assert queue.next_pending() is not None
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)

    events, _counts, status = cli_module._collect_zhihu_bootstrap_events(
        task_id, max_wait_seconds=0
    )

    task = queue.get(task_id)
    assert events == []
    assert status == "timeout"
    assert task is not None
    assert task["status"] == "failed"
    assert "extension_result_timeout" in str(task["result_json"])


def test_fetch_zhihu_command_explains_login_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_enqueue_zhihu_bootstrap_task", lambda **_: "zhihu-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [],
            {
                "zhihu_read_history": 0,
                "zhihu_collection": 0,
                "zhihu_activity_like": 0,
                "zhihu_activity_favorite": 0,
            },
            "login_required",
        ),
    )

    result = runner.invoke(app, ["fetch-zhihu", "--force", "-w", "5"])

    assert result.exit_code == 0, result.output
    assert "先在当前浏览器登录知乎" in result.output


def test_enqueue_reddit_bootstrap_requests_three_signal_scopes_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "reddit.db")
    database.initialize()
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)
    monkeypatch.setattr(cli_module, "_kick_task_dispatcher", lambda source: None)

    task_id = cli_module._enqueue_reddit_bootstrap_task()

    assert task_id is not None
    task = RedditTaskQueue(database).get(task_id)
    assert task is not None
    payload = json.loads(str(task["payload_json"]))
    assert payload["scopes"] == ["reddit_saved", "reddit_upvoted", "reddit_subscribed"]
    assert payload["max_items_per_scope"] == 300


def test_collect_reddit_bootstrap_events_converts_completed_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "reddit.db")
    database.initialize()
    queue = RedditTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"]},
    )
    assert task_id is not None
    assert queue.next_pending() is not None
    queue.merge_result(
        task_id,
        items=[
            {
                "scope": "reddit_saved",
                "id": "abc123",
                "title": "Saved agent essay",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/",
                "subreddit": "LocalLLaMA",
            }
        ],
        scope_counts={"reddit_saved": 1, "reddit_upvoted": 0, "reddit_subscribed": 0},
        complete=True,
    )
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: database)

    events, counts, status = cli_module._collect_reddit_bootstrap_events(
        task_id,
        max_wait_seconds=0,
    )

    assert status == "ok"
    assert counts["reddit_saved"] == 1
    assert events[0]["event_type"] == "favorite"
    assert events[0]["metadata"]["source_platform"] == "reddit"


def test_discover_zhihu_command_enqueues_search_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    runner = CliRunner()
    calls: dict[str, object] = {}

    def enqueue(keywords: tuple[str, ...], *, max_items_per_keyword: int) -> str:
        calls["keywords"] = keywords
        calls["max_items_per_keyword"] = max_items_per_keyword
        return "zhihu-search-task"

    monkeypatch.setattr(cli_module, "_enqueue_zhihu_search_task", enqueue)
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_search_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "scope": "zhihu_search",
                    "title": "知乎回答",
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "content_type": "answer",
                    "content_id": "2",
                }
            ],
            {"zhihu_search": 1},
            "ok",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_enqueue_zhihu_discovery_candidates",
        lambda items: (
            1,
            [
                SimpleNamespace(
                    title="知乎回答",
                    up_name="作者",
                    source_strategy="zhihu-search",
                    relevance_score=0.0,
                )
            ],
        ),
    )

    result = runner.invoke(app, ["discover-zhihu", "AI 工程化", "--limit", "7", "-w", "5"])

    assert result.exit_code == 0, result.output
    assert calls == {
        "keywords": ("AI 工程化",),
        "max_items_per_keyword": 7,
    }
    assert "知乎内容发现" in result.output
    assert "入池候选" in result.output


def test_discover_zhihu_hot_command_enqueues_hot_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    runner = CliRunner()
    calls: dict[str, object] = {}

    def enqueue(task_type: str, payload: dict[str, object], *, daily_budget_key: str) -> str:
        calls["task_type"] = task_type
        calls["payload"] = payload
        calls["daily_budget_key"] = daily_budget_key
        return "zhihu-hot-task"

    monkeypatch.setattr(cli_module, "_enqueue_zhihu_discovery_task", enqueue)
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_discovery_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "scope": "zhihu_hot",
                    "title": "热榜问题",
                    "url": "https://www.zhihu.com/question/1",
                    "content_type": "question",
                    "content_id": "1",
                    "source_strategy": "zhihu-hot",
                }
            ],
            {"zhihu_hot": 1},
            "ok",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_enqueue_zhihu_discovery_candidates",
        lambda items: (
            1,
            [
                SimpleNamespace(
                    title="热榜问题",
                    up_name="",
                    source_strategy="zhihu-hot",
                    relevance_score=0.0,
                )
            ],
        ),
    )

    result = runner.invoke(app, ["discover-zhihu-hot", "--limit", "7", "-w", "5"])

    assert result.exit_code == 0, result.output
    assert calls == {
        "task_type": "hot",
        "payload": {"max_items": 7},
        "daily_budget_key": "daily_hot_budget",
    }
    assert "zhihu-hot" in result.output


def test_discover_source_zhihu_runs_formal_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    calls: dict[str, object] = {}

    def run_zhihu_discovery(*, limit: int) -> None:
        calls["limit"] = limit

    monkeypatch.setattr(cli_module, "_run_zhihu_discovery", run_zhihu_discovery)

    result = runner.invoke(app, ["discover", "--source", "zhihu", "--limit", "11"])

    assert result.exit_code == 0, result.output
    assert calls == {"limit": 11}
    assert "discover-zhihu" not in result.output


def test_discover_source_reddit_runs_formal_producer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    calls: dict[str, int] = {}

    def run_reddit_discovery(*, limit: int) -> None:
        calls["limit"] = limit

    monkeypatch.setattr(cli_module, "_run_reddit_discovery", run_reddit_discovery, raising=False)

    result = runner.invoke(app, ["discover", "--source", "reddit", "--limit", "11"])

    assert result.exit_code == 0, result.output
    assert calls == {"limit": 11}


def test_discover_source_linuxdo_runs_formal_producer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    calls: dict[str, int] = {}

    def run_linuxdo_discovery(*, limit: int) -> None:
        calls["limit"] = limit

    monkeypatch.setattr(cli_module, "_run_linuxdo_discovery", run_linuxdo_discovery)

    result = runner.invoke(app, ["discover", "--source", "linuxdo", "--limit", "11"])

    assert result.exit_code == 0, result.output
    assert calls == {"limit": 11}
    assert "discover-linuxdo" not in result.output


def test_fetch_linuxdo_write_memory_is_owned_by_staged_task_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    captured: dict[str, object] = {}

    def enqueue(**kwargs: object) -> str:
        captured["enqueue"] = kwargs
        return "linuxdo-task"

    monkeypatch.setattr(cli_module, "_enqueue_linuxdo_bootstrap_task", enqueue)
    monkeypatch.setattr(
        cli_module,
        "_collect_linuxdo_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "event_type": "favorite",
                    "title": "Linux.do 收藏",
                    "metadata": {"source_platform": "linuxdo"},
                }
            ],
            {
                "linuxdo_bookmarks": 1,
                "linuxdo_likes": 0,
                "linuxdo_read_history": 0,
            },
            "ok",
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_write_events_to_memory",
        lambda *_args, **_kwargs: pytest.fail(
            "Linux.do writes must stay inside staged task-result ingestion"
        ),
    )

    result = runner.invoke(app, ["fetch-linuxdo", "--write-memory", "--wait-seconds", "5"])

    assert result.exit_code == 0, result.output
    assert captured["enqueue"] == {"profile_update": True}
    assert "按账号原子写入" in result.output


def test_explicit_linuxdo_discovery_ignores_scheduler_master_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config_module.Config()
    cfg.sources.linuxdo.enabled = True
    cfg.scheduler.enabled = False
    captured: dict[str, object] = {}

    class FakeDatabase:
        conn = object()

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"preferences": {"interests": [{"name": "自托管"}]}}

    class FakeProducer:
        async def produce_if_due(self, *, limit: int) -> dict[str, object]:
            captured["limit"] = limit
            return {"reason": "empty", "discovered": 0, "enqueued": 0}

    def fake_build(**kwargs: object) -> FakeProducer:
        captured.update(kwargs)
        return FakeProducer()

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_get_runtime_database", FakeDatabase)
    monkeypatch.setattr(cli_module, "_build_soul_engine", FakeSoulEngine)
    monkeypatch.setattr(cli_module, "_build_discovery_engine", object)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_candidate_pipeline",
        lambda **_kwargs: SimpleNamespace(last_admitted_items=[]),
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_fetch.KeywordFetchCoordinator",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.linuxdo_producer.build_linuxdo_discovery_producer",
        fake_build,
    )
    monkeypatch.setattr(cli_module, "_print_page_title", lambda *_args: None)
    monkeypatch.setattr(cli_module, "_print_status_panel", lambda *_args: None)

    cli_module._run_linuxdo_discovery(limit=5)

    assert captured["require_scheduler"] is False
    assert captured["limit"] == 5


def test_explicit_bangumi_discovery_ignores_scheduler_master_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = config_module.Config()
    cfg.scheduler.enabled = False
    cfg.sources.bangumi.enabled = True
    captured: dict[str, object] = {}
    result_reasons = iter(("disabled", "no_profile"))
    panels: list[tuple[str, str, str]] = []

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"preferences": {"interests": [{"name": "科幻"}]}}

    class FakeBangumiClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "FakeBangumiClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeProducer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def produce_if_due(self, *, limit: int, force: bool) -> dict[str, object]:
            captured["limit"] = limit
            captured["force"] = force
            return {"reason": next(result_reasons), "discovered": 0, "enqueued": 0}

    class FakeKeywordFetch:
        def __init__(self, **kwargs: object) -> None:
            captured["keyword_fetch_kwargs"] = kwargs

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_get_runtime_database", object)
    monkeypatch.setattr(cli_module, "_build_soul_engine", FakeSoulEngine)
    monkeypatch.setattr(cli_module, "_build_discovery_engine", object)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_candidate_pipeline",
        lambda **kwargs: SimpleNamespace(last_admitted_items=[]),
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_fetch.KeywordFetchCoordinator",
        FakeKeywordFetch,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.bangumi_client.BangumiClient",
        FakeBangumiClient,
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.bangumi_producer.BangumiDiscoveryProducer",
        FakeProducer,
    )
    monkeypatch.setattr(cli_module, "_print_page_title", lambda *args: None)
    monkeypatch.setattr(
        cli_module,
        "_print_status_panel",
        lambda kind, title, body: panels.append((kind, title, body)),
    )

    cli_module._run_bangumi_discovery(limit=7, force=True)
    cli_module._run_bangumi_discovery(limit=7, force=True)

    assert captured["enabled"] is True
    assert captured["limit"] == 7
    assert captured["force"] is True
    assert [title for _, title, _ in panels] == [
        "Bangumi discovery 已禁用",
        "尚未初始化用户画像",
    ]


def test_bangumi_discovery_reports_budget_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config_module.Config()
    cfg.sources.bangumi.enabled = True
    panels: list[tuple[str, str, str]] = []

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"preferences": {"interests": [{"name": "科幻"}]}}

    class FakeBangumiClient:
        def __init__(self, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeBangumiClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeProducer:
        def __init__(self, **kwargs: object) -> None:
            return None

        async def produce_if_due(self, *, limit: int, force: bool) -> dict[str, object]:
            return {
                "reason": "budget_exhausted",
                "discovered": 0,
                "enqueued": 0,
                "mode_results": {
                    "search": "budget_exhausted",
                    "ranked": "budget_exhausted",
                    "latest": "budget_exhausted",
                },
            }

    class FakeKeywordFetch:
        def __init__(self, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_get_runtime_database", object)
    monkeypatch.setattr(cli_module, "_build_soul_engine", FakeSoulEngine)
    monkeypatch.setattr(cli_module, "_build_discovery_engine", object)
    monkeypatch.setattr(
        cli_module,
        "_build_discovery_candidate_pipeline",
        lambda **kwargs: SimpleNamespace(last_admitted_items=[]),
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.keyword_fetch.KeywordFetchCoordinator",
        FakeKeywordFetch,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.bangumi_client.BangumiClient",
        FakeBangumiClient,
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.bangumi_producer.BangumiDiscoveryProducer",
        FakeProducer,
    )
    monkeypatch.setattr(cli_module, "_print_page_title", lambda *args: None)
    monkeypatch.setattr(
        cli_module,
        "_print_status_panel",
        lambda kind, title, body: panels.append((kind, title, body)),
    )

    cli_module._run_bangumi_discovery(limit=5, force=True)

    assert panels == [
        (
            "info",
            "Bangumi discovery 今日预算已用完",
            "所有启用分支的每日预算均已耗尽，可在配置页调整对应分支预算或明日重试。",
        )
    ]


def test_discover_reddit_default_uses_rdt_command_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="ready", message="ok"),
    )

    def run_command(args: list[str], *, timeout: float) -> list[dict[str, Any]]:
        calls["args"] = args
        calls["timeout"] = timeout
        return [
            {
                "id": "abc123",
                "title": "Local-first agents",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "author": "agent_builder",
                "search_keyword": "local agents",
            }
        ]

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        run_command,
    )

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        raise AssertionError("default discover-reddit must use rdt-cli, not extension tasks")

    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_task",
        enqueue_task,
        raising=False,
    )
    enqueued: dict[str, object] = {}

    def enqueue(items: list[dict[str, Any]], *, strategy: str) -> tuple[int, list[Any]]:
        enqueued["items"] = items
        enqueued["strategy"] = strategy
        return 1, [
            DiscoveredContent(
                title="Local-first agents",
                source_platform="reddit",
                source_strategy=strategy,
                content_url="https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
            )
        ]

    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_candidates",
        enqueue,
        raising=False,
    )

    result = runner.invoke(app, ["discover-reddit", "local agents", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert calls["args"] == ["rdt", "search", "local agents", "-n", "5", "--json"]
    assert enqueued["strategy"] == "reddit-search"
    assert "Reddit" in result.output
    assert "Local-first agents" in result.output


def test_discover_reddit_default_falls_back_to_plugin_when_rdt_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    task_payload: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="missing", message="未安装 rdt。"),
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        lambda *_a, **_kw: pytest.fail("missing rdt should fall back to plugin"),
    )

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        task_payload.update(
            {
                "task_type": task_type,
                "payload": payload,
                "daily_budget_key": daily_budget_key,
            }
        )
        return "reddit-task"

    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_task",
        enqueue_task,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_reddit_discovery_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "id": "abc123",
                    "title": "Local-first agents",
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                    "subreddit": "LocalLLaMA",
                    "author": "agent_builder",
                    "search_keyword": "local agents",
                }
            ],
            {"reddit_search": 1},
            "ok",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_candidates",
        lambda items, *, strategy: (
            1,
            [
                DiscoveredContent(
                    title="Local-first agents",
                    source_platform="reddit",
                    source_strategy=strategy,
                    content_url="https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                )
            ],
        ),
        raising=False,
    )

    result = runner.invoke(app, ["discover-reddit", "local agents", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert task_payload == {
        "task_type": "search",
        "payload": {"keywords": ["local agents"], "max_items_per_keyword": 5},
        "daily_budget_key": "daily_search_budget",
    }
    assert "fallback" in result.output
    assert "Local-first agents" in result.output


def test_fetch_reddit_default_does_not_persist_or_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="ready", message="ok"),
    )

    def run_command(args: list[str], *, timeout: float) -> list[dict[str, Any]]:
        calls["args"] = args
        calls["timeout"] = timeout
        return [
            {
                "id": "abc123",
                "title": "Local-first agents",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "author": "agent_builder",
            }
        ]

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        run_command,
    )

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        raise AssertionError("default fetch-reddit must use rdt-cli, not extension tasks")

    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_task",
        enqueue_task,
        raising=False,
    )

    def trip_write_memory(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise AssertionError("fetch-reddit must not write memory without --write-memory")

    def trip_soul_engine() -> object:
        raise AssertionError("fetch-reddit must not rebuild profile by default")

    monkeypatch.setattr(cli_module, "_write_events_to_memory", trip_write_memory)
    monkeypatch.setattr(cli_module, "_build_soul_engine", trip_soul_engine)

    result = runner.invoke(app, ["fetch-reddit", "local agents", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert calls["args"] == ["rdt", "search", "local agents", "-n", "5", "--json"]
    assert "Reddit" in result.output
    assert "未写入 memory" in result.output
    assert "Local-first agents" in result.output


def test_fetch_reddit_default_falls_back_to_plugin_when_rdt_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    task_payload: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="missing", message="未安装 rdt。"),
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        lambda *_a, **_kw: pytest.fail("missing rdt should fall back to plugin"),
    )

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        task_payload.update(
            {
                "task_type": task_type,
                "payload": payload,
                "daily_budget_key": daily_budget_key,
            }
        )
        return "reddit-task"

    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_task",
        enqueue_task,
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_reddit_discovery_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "id": "abc123",
                    "title": "Local-first agents",
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
                    "subreddit": "LocalLLaMA",
                    "author": "agent_builder",
                }
            ],
            {"reddit_search": 1},
            "ok",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_write_events_to_memory",
        lambda *_a, **_kw: pytest.fail("fetch-reddit must not write memory by default"),
    )
    monkeypatch.setattr(
        cli_module,
        "_build_soul_engine",
        lambda: pytest.fail("fetch-reddit must not rebuild profile by default"),
    )

    result = runner.invoke(app, ["fetch-reddit", "local agents", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert task_payload == {
        "task_type": "search",
        "payload": {"keywords": ["local agents"], "max_items_per_keyword": 5},
        "daily_budget_key": "daily_search_budget",
    }
    assert "fallback" in result.output
    assert "未写入 memory" in result.output


def test_fetch_reddit_write_memory_persists_converted_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    written: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="ready", message="ok"),
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        lambda *_a, **_kw: [
            {
                "id": "abc123",
                "title": "Local-first agents",
                "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
                "subreddit": "LocalLLaMA",
                "author": "agent_builder",
            }
        ],
    )

    def write_events(events: list[dict[str, Any]], *, source: str) -> tuple[int, int]:
        written["events"] = events
        written["source"] = source
        return len(events), 0

    monkeypatch.setattr(cli_module, "_write_events_to_memory", write_events)

    result = runner.invoke(
        app,
        ["fetch-reddit", "local agents", "--backend", "rdt", "--write-memory"],
    )

    assert result.exit_code == 0, result.output
    assert written["source"] == "reddit"
    events = cast("list[dict[str, Any]]", written["events"])
    assert events[0]["event_type"] == "view"
    assert events[0]["metadata"]["source_platform"] == "reddit"
    assert events[0]["metadata"]["import_source"] == "reddit_fetch_search"


def test_discover_reddit_hot_default_uses_rdt_command_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.probe_reddit_command_backend",
        lambda backend: SimpleNamespace(backend="rdt", state="ready", message="ok"),
    )

    def run_command(args: list[str], *, timeout: float) -> list[dict[str, Any]]:
        calls["args"] = args
        calls["timeout"] = timeout
        return [
            {
                "id": "hot123",
                "title": "Hot Reddit topic",
                "permalink": "/r/all/comments/hot123/hot_reddit_topic/",
                "subreddit": "all",
            }
        ]

    monkeypatch.setattr(
        "openbiliclaw.sources.reddit_tasks.run_reddit_command",
        run_command,
    )
    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_discovery_task",
        lambda *_a, **_kw: pytest.fail("default hot discovery must use rdt-cli"),
        raising=False,
    )

    result = runner.invoke(app, ["discover-reddit-hot", "--limit", "5", "--no-enqueue"])

    assert result.exit_code == 0, result.output
    assert calls["args"] == ["rdt", "all", "-n", "5", "--json"]
    assert "reddit-hot" in result.output


def test_discover_reddit_hot_uses_plugin_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    task_payload: dict[str, object] = {}

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        task_payload.update(
            {
                "task_type": task_type,
                "payload": payload,
                "daily_budget_key": daily_budget_key,
            }
        )
        return "reddit-hot-task"

    monkeypatch.setattr(cli_module, "_enqueue_reddit_discovery_task", enqueue_task, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_collect_reddit_discovery_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "id": "hot123",
                    "title": "Hot Reddit topic",
                    "permalink": "/r/all/comments/hot123/hot_reddit_topic/",
                    "subreddit": "all",
                }
            ],
            {"reddit_hot": 1},
            "ok",
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        ["discover-reddit-hot", "--backend", "extension", "--limit", "5", "--no-enqueue"],
    )

    assert result.exit_code == 0, result.output
    assert task_payload == {
        "task_type": "hot",
        "payload": {"subreddit": "all", "max_items": 5},
        "daily_budget_key": "daily_hot_budget",
    }
    assert "reddit-hot" in result.output


def test_discover_reddit_related_uses_plugin_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    task_payload: dict[str, object] = {}
    url = "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/"

    def enqueue_task(
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget_key: str,
    ) -> str:
        task_payload.update(
            {
                "task_type": task_type,
                "payload": payload,
                "daily_budget_key": daily_budget_key,
            }
        )
        return "reddit-related-task"

    monkeypatch.setattr(cli_module, "_enqueue_reddit_discovery_task", enqueue_task, raising=False)
    monkeypatch.setattr(
        cli_module,
        "_collect_reddit_discovery_results",
        lambda task_id, *, max_wait_seconds: (
            [
                {
                    "id": "rel123",
                    "title": "Related Reddit topic",
                    "permalink": "/r/LocalLLaMA/comments/rel123/related/",
                    "subreddit": "LocalLLaMA",
                }
            ],
            {"reddit_related": 1},
            "ok",
        ),
        raising=False,
    )

    result = runner.invoke(
        app,
        [
            "discover-reddit-related",
            url,
            "--backend",
            "extension",
            "--limit",
            "3",
            "--no-enqueue",
        ],
    )

    assert result.exit_code == 0, result.output
    assert task_payload == {
        "task_type": "related",
        "payload": {"related_urls": [url], "max_items_per_seed": 3},
        "daily_budget_key": "daily_related_budget",
    }
    assert "reddit-related" in result.output


def test_fetch_douyin_does_not_rebuild_profile_cli_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fetch-douyin`` only verifies/imports the source data. Profile
    rebuild stays on the init / learning paths instead of being hidden
    behind the smoke command."""
    runner = CliRunner()
    rebuilt = {"called": False}

    def trip_soul_engine() -> object:
        rebuilt["called"] = True
        raise AssertionError("fetch-douyin should not build or rebuild the soul profile")

    monkeypatch.setattr(cli_module, "_build_soul_engine", trip_soul_engine)
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: "task-id")
    monkeypatch.setattr(
        cli_module,
        "_collect_dy_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [{"event_type": "favorite", "title": "x", "metadata": {}}],
            {"dy_post": 0, "dy_collect": 1, "dy_like": 0, "dy_follow": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-douyin"])
    assert result.exit_code == 0, result.output
    assert rebuilt["called"] is False


def test_fetch_douyin_exits_with_code_1_when_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_enqueue_dy_bootstrap_task", lambda: None)
    result = runner.invoke(app, ["fetch-douyin"])
    assert result.exit_code == 1
    assert "无法入队" in result.output


def test_fetch_xhs_renders_xhs_specific_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch-xhs has its own scope vocabulary (saved/liked/xhs_history)
    and summary line format — verify they surface correctly."""
    runner = CliRunner()

    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", lambda: "xhs-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, *, max_wait_seconds: (
            [],
            {"saved": 12, "liked": 7, "xhs_history": 0},
            "ok",
        ),
    )

    result = runner.invoke(app, ["fetch-xhs"])
    assert result.exit_code == 0, result.output
    assert "小红书" in result.output
    assert "收藏" in result.output and "点赞" in result.output and "浏览记录" in result.output


def test_fetch_xhs_handles_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the extension never reports back, the command surfaces a
    'timeout' hint rather than crashing or claiming success."""
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", lambda: "xhs-task")
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, *, max_wait_seconds: ([], {}, "timeout"),
    )
    result = runner.invoke(app, ["fetch-xhs"])
    assert result.exit_code == 1
    assert "超时" in result.output


# ── Password gate CLI (set-password / init prompt) ──────────────────────────


def _extension_key_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, "config_module.Config"]:
    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    cfg = config_module.Config()
    config_module.save_config(cfg, root / "config.toml")
    return root, cfg


def test_ext_key_generate_persists_digest_once_and_keeps_switch_off(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.auth_core import parse_extension_access_key

    _root, _cfg = _extension_key_runtime(monkeypatch, tmp_path)
    result = runner.invoke(app, ["ext-key", "generate"])
    assert result.exit_code == 0, result.stdout

    loaded = config_module.load_config()
    assert loaded.api.auth.extension_access_enabled is False
    assert len(loaded.api.auth.extension_access_keys) == 1
    record = loaded.api.auth.extension_access_keys[0]
    key_id, digest = record.split(":")
    assert len(key_id) == 12
    assert len(digest) == 64
    assert record not in result.stdout
    full_keys = [part for part in result.stdout.split() if part.startswith("obc_ext_")]
    assert len(full_keys) == 1
    assert parse_extension_access_key(full_keys[0]) is not None


def test_ext_key_list_never_prints_digest_or_secret(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.auth_core import generate_extension_access_key

    _root, cfg = _extension_key_runtime(monkeypatch, tmp_path)
    key_id, full_key, record = generate_extension_access_key()
    cfg.api.auth.extension_access_keys = [record]
    config_module.save_config(cfg)

    result = runner.invoke(app, ["ext-key", "list"])
    assert result.exit_code == 0
    assert key_id in result.stdout
    assert record.split(":", 1)[1] not in result.stdout
    assert full_key not in result.stdout


def test_ext_key_enable_disable_requires_key_and_preserves_records(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.auth_core import generate_extension_access_key

    _root, cfg = _extension_key_runtime(monkeypatch, tmp_path)
    no_key = runner.invoke(app, ["ext-key", "enable"])
    assert no_key.exit_code == 1

    _key_id, _full_key, record = generate_extension_access_key()
    cfg.api.auth.extension_access_keys = [record]
    config_module.save_config(cfg)
    assert runner.invoke(app, ["ext-key", "enable"]).exit_code == 0
    assert config_module.load_config().api.auth.extension_access_enabled is True

    assert runner.invoke(app, ["ext-key", "disable"]).exit_code == 0
    loaded = config_module.load_config()
    assert loaded.api.auth.extension_access_enabled is False
    assert loaded.api.auth.extension_access_keys == [record]


def test_ext_key_revoke_removes_one_key_and_bumps_epoch(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.auth_core import generate_extension_access_key
    from openbiliclaw.storage.database import Database

    _root, cfg = _extension_key_runtime(monkeypatch, tmp_path)
    first_id, _first_key, first_record = generate_extension_access_key()
    _second_id, _second_key, second_record = generate_extension_access_key()
    cfg.api.auth.extension_access_keys = [first_record, second_record]
    config_module.save_config(cfg)

    result = runner.invoke(app, ["ext-key", "revoke", first_id])
    assert result.exit_code == 0, result.stdout
    loaded = config_module.load_config()
    assert loaded.api.auth.extension_access_keys == [second_record]
    db = Database(loaded.data_path / "openbiliclaw.db")
    db.initialize()
    try:
        assert db.get_auth_epoch() == 1
    finally:
        db.close()


def test_ext_key_revoke_rolls_back_config_when_epoch_bump_fails(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.auth_core import generate_extension_access_key

    root, cfg = _extension_key_runtime(monkeypatch, tmp_path)
    key_id, _full_key, record = generate_extension_access_key()
    cfg.api.auth.extension_access_keys = [record]
    config_module.save_config(cfg)
    before = (root / "config.toml").read_bytes()
    monkeypatch.setattr(cli_module, "_bump_auth_epoch", lambda _cfg: False)

    result = runner.invoke(app, ["ext-key", "revoke", key_id])
    assert result.exit_code == 1
    assert (root / "config.toml").read_bytes() == before
    assert config_module.load_config().api.auth.extension_access_keys == [record]


@pytest.mark.parametrize("mode", ["env", "local"])
def test_ext_key_writes_refuse_shadowed_auth_config(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path, mode: str
) -> None:
    root, _cfg = _extension_key_runtime(monkeypatch, tmp_path)
    if mode == "env":
        monkeypatch.setenv("OPENBILICLAW_API_AUTH_ENABLED", "true")
    else:
        (root / "config.local.toml").write_text(
            "[api.auth]\nextension_access_enabled = false\n", encoding="utf-8"
        )

    result = runner.invoke(app, ["ext-key", "generate"])
    assert result.exit_code == 1
    assert config_module.load_config().api.auth.extension_access_keys == []


def test_set_password_command_sets_enables_and_disables(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw import auth_core as ac
    from openbiliclaw.config import Config, load_config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)

    # set a password (hidden prompt + confirmation)
    result = runner.invoke(app, ["set-password"], input="hunter2\nhunter2\n")
    assert result.exit_code == 0, result.stdout
    cfg = load_config()
    assert cfg.api.auth.enabled is True
    assert ac.verify_password("hunter2", cfg.api.auth.password_hash)
    assert cfg.api.auth.session_secret  # auto-generated

    # set-password must revoke existing sessions immediately (bump the epoch),
    # not only at the next restart's reconcile (review r2#1).
    from openbiliclaw.storage.database import Database

    db = Database(cfg.data_path / "openbiliclaw.db")
    db.initialize()
    try:
        assert db.get_auth_epoch() >= 1
    finally:
        db.close()

    # disable turns the gate off
    result = runner.invoke(app, ["set-password", "--disable"])
    assert result.exit_code == 0
    assert load_config().api.auth.enabled is False


def test_set_password_fails_loudly_when_revocation_unavailable(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    # epoch bump fails (e.g. data dir unwritable) → must NOT claim immediate revoke
    monkeypatch.setattr(cli_module, "_bump_auth_epoch", lambda _cfg: False, raising=False)

    result = runner.invoke(app, ["set-password"], input="hunter2\nhunter2\n")
    assert result.exit_code == 1
    assert "未能立即撤销" in result.stdout


def test_set_password_logout_all_bumps_epoch(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw import auth_core as ac
    from openbiliclaw.config import Config, load_config, save_config
    from openbiliclaw.storage.database import Database

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    cfg = Config()
    cfg.api.auth.enabled = True
    cfg.api.auth.password_hash = ac.hash_password("pw")
    cfg.api.auth.session_secret = "secret"
    save_config(cfg, root / "config.toml")

    result = runner.invoke(app, ["set-password", "--logout-all"])
    assert result.exit_code == 0
    db = Database(load_config().data_path / "openbiliclaw.db")
    db.initialize()
    try:
        assert db.get_auth_epoch() == 1
    finally:
        db.close()


def test_set_password_refuses_when_env_override_present(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    # env supplies the password → config edits would be silently overridden
    monkeypatch.setenv("OPENBILICLAW_API_AUTH_PASSWORD", "env-pw")

    for args in (
        ["set-password"],
        ["set-password", "--disable"],
        ["set-password", "--rotate-secret"],
    ):
        result = runner.invoke(app, args, input="newpw\nnewpw\n")
        assert result.exit_code == 1, args
        assert "环境变量覆盖" in result.stdout, args

    # but --logout-all (DB-only revocation) still works despite env overrides
    monkeypatch.setattr(cli_module, "_bump_auth_epoch", lambda _cfg: True, raising=False)
    result = runner.invoke(app, ["set-password", "--logout-all"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "env_var",
    ["OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS", "OPENBILICLAW_API_AUTH_TRUST_LOOPBACK"],
)
def test_set_password_refuses_on_any_auth_env_override(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path, env_var: str
) -> None:
    # save_config writes the WHOLE [api.auth] block, so even a ttl / trust_loopback
    # env override would be baked into config.toml as a stale literal. The guard
    # must refuse the full override surface, not just the password (review r3#2).
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    monkeypatch.setenv(env_var, "1")

    result = runner.invoke(app, ["set-password"], input="newpw\nnewpw\n")
    assert result.exit_code == 1
    assert "环境变量覆盖" in result.stdout
    assert env_var in result.stdout


def test_set_password_refuses_when_shadowed_by_config_local(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    # config.local.toml is merged OVER config.toml; a credential field pinned there
    # shadows a set-password write to config.toml (silently reverts on restart), so
    # the command must refuse loudly instead of reporting success (review r9).
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    (root / "config.local.toml").write_text('[api.auth]\npassword = "localpw"\n', encoding="utf-8")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)

    result = runner.invoke(app, ["set-password"], input="newpw\nnewpw\n")
    assert result.exit_code == 1
    assert "config.local.toml" in result.stdout
    assert "password" in result.stdout


def test_set_password_logout_all_fails_loudly_when_unavailable(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_bump_auth_epoch", lambda _cfg: False, raising=False)
    result = runner.invoke(app, ["set-password", "--logout-all"])
    assert result.exit_code == 1  # must not report success when revocation failed


def test_init_password_prompt_sets_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openbiliclaw import auth_core as ac
    from openbiliclaw.config import Config, load_config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)
    monkeypatch.setattr(cli_module.typer, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(cli_module.typer, "prompt", lambda *a, **k: "lanpass")

    cli_module._maybe_setup_password_in_init(allow_lan=True)

    cfg = load_config()
    assert cfg.api.auth.enabled is True
    assert ac.verify_password("lanpass", cfg.api.auth.password_hash)


def test_init_password_prompt_skipped_when_lan_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openbiliclaw.config import Config, load_config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True, raising=False)

    def _boom(*_a: object, **_k: object) -> bool:
        raise AssertionError("should not prompt when LAN access is disabled")

    monkeypatch.setattr(cli_module.typer, "confirm", _boom)
    cli_module._maybe_setup_password_in_init(allow_lan=False)
    assert load_config().api.auth.enabled is False


def test_set_password_rotate_secret_rebases_fingerprint(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    # --rotate-secret must re-base the stored fingerprint under the NEW secret so
    # the next startup reconcile does not perform a redundant epoch bump (the
    # set_password_fingerprint method exists for exactly this).
    from openbiliclaw.auth_core import hash_password, password_fingerprint
    from openbiliclaw.config import Config, get_auth_plain_password, load_config, save_config
    from openbiliclaw.storage.database import Database

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    cfg = Config()
    cfg.api.auth.enabled = True
    cfg.api.auth.password_hash = hash_password("pw")
    cfg.api.auth.session_secret = "old-secret-aaaaaaaaaaaaaaaaaaaaaaaa"
    save_config(cfg, root / "config.toml")

    # simulate a running backend that already reconciled under the OLD secret
    db_path = cfg.data_path / "openbiliclaw.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    db.initialize()
    db.set_password_fingerprint(
        password_fingerprint(
            "old-secret-aaaaaaaaaaaaaaaaaaaaaaaa",
            plain=None,
            password_hash=cfg.api.auth.password_hash,
        )
    )
    db.close()

    result = runner.invoke(app, ["set-password", "--rotate-secret"])
    assert result.exit_code == 0, result.stdout

    # reconcile under the NEW secret must NOT bump — the fingerprint was re-based
    reloaded = load_config()
    assert reloaded.api.auth.session_secret != "old-secret-aaaaaaaaaaaaaaaaaaaaaaaa"
    db2 = Database(reloaded.data_path / "openbiliclaw.db")
    db2.initialize()
    expected_fp = password_fingerprint(
        reloaded.api.auth.session_secret,
        plain=get_auth_plain_password(),
        password_hash=reloaded.api.auth.password_hash,
    )
    assert db2.reconcile_password_fingerprint(expected_fp) is False  # no redundant bump
    db2.close()


# ── X (twitter) source enable toggle in init (Task 12) ──────────────────────


def test_persist_init_source_enabled_flags_toggles_twitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--yes-x`` flows into _persist_init_source_enabled_flags(include_x=True)
    and must flip ``[sources.twitter].enabled`` on (mirror xhs/douyin/youtube)."""
    from openbiliclaw.config import Config

    cfg = Config()
    cfg.sources.twitter.enabled = False

    saved: list[Config] = []
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    monkeypatch.setattr(config_module, "save_config", lambda c: saved.append(c))

    cli_module._persist_init_source_enabled_flags(
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=True,
    )

    assert cfg.sources.twitter.enabled is True
    assert saved and saved[-1] is cfg

    # And the inverse: include_x=False turns it back off.
    cfg.sources.twitter.enabled = True
    saved.clear()
    cli_module._persist_init_source_enabled_flags(
        include_xhs=False,
        include_dy=False,
        include_yt=False,
        include_x=False,
    )
    assert cfg.sources.twitter.enabled is False
    assert saved and saved[-1] is cfg


def test_init_yes_x_flag_is_registered() -> None:
    """The ``init`` command must expose ``--yes-x`` (scripted opt-in),
    mirroring ``--yes-xhs`` / ``--yes-douyin`` / ``--yes-youtube``."""
    import inspect

    sig = inspect.signature(cli_module.init)
    assert "skip_x_prompt" in sig.parameters
    default = sig.parameters["skip_x_prompt"].default
    # typer.Option(...) carries the flag declarations in param_decls.
    decls = getattr(default, "param_decls", ())
    assert "--yes-x" in decls


def test_init_yes_reddit_flag_is_registered() -> None:
    import inspect

    sig = inspect.signature(cli_module.init)
    assert "skip_reddit_prompt" in sig.parameters
    default = sig.parameters["skip_reddit_prompt"].default
    decls = getattr(default, "param_decls", ())
    assert "--yes-reddit" in decls


# ── X (Twitter) likes/bookmarks init backfill ────────────────────────


def test_x_tweet_to_event_builds_like_event() -> None:
    ev = cli_module._x_tweet_to_event(
        {
            "id": "123",
            "text": "rust async wins\nsecond line",
            "author": {"screenName": "alice", "name": "Alice"},
        },
        event_type="like",
    )
    assert ev is not None
    assert ev["event_type"] == "like"
    assert ev["url"] == "https://x.com/alice/status/123"
    assert ev["title"] == "rust async wins"  # first line only, truncated to 140
    assert ev["metadata"]["source_platform"] == "twitter"
    assert ev["metadata"]["tweet_id"] == "123"
    assert "点赞" in ev["context"]


def test_x_tweet_to_event_bookmark_prefers_article_text() -> None:
    ev = cli_module._x_tweet_to_event(
        {
            "id": "9",
            "text": "short",
            "articleText": "long form note body",
            "author": {"screenName": "bob"},
        },
        event_type="favorite",
    )
    assert ev is not None
    assert ev["event_type"] == "favorite"
    assert ev["metadata"]["body_text"] == "long form note body"
    assert "收藏" in ev["context"]


def test_x_tweet_to_event_tombstone_returns_none() -> None:
    assert cli_module._x_tweet_to_event({"id": "", "text": "x"}, event_type="like") is None


async def test_fetch_x_init_data_skips_without_cookie(monkeypatch, tmp_path) -> None:
    cfg = SimpleNamespace(
        sources=SimpleNamespace(twitter=SimpleNamespace(cookie_env="OPENBILICLAW_X_COOKIE")),
        data_path=tmp_path,
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr("openbiliclaw.sources.x_auth.resolve_x_cookie", lambda **kwargs: "")
    likes, bookmarks = await cli_module._fetch_x_init_data(likes_limit=10, bookmarks_limit=10)
    assert likes == []
    assert bookmarks == []


async def test_fetch_x_init_data_fetches_likes_and_bookmarks(monkeypatch, tmp_path) -> None:
    cfg = SimpleNamespace(
        sources=SimpleNamespace(twitter=SimpleNamespace(cookie_env="OPENBILICLAW_X_COOKIE")),
        data_path=tmp_path,
    )

    class _FakeXClient:
        def __init__(self, cookie: str) -> None:
            self.cookie = cookie

        async def likes(self, *, limit: int):
            return [{"id": "1", "text": "liked", "author": {"screenName": "a"}}]

        async def bookmarks(self, *, limit: int):
            return [{"id": "2", "text": "saved", "author": {"screenName": "b"}}]

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr(
        "openbiliclaw.sources.x_auth.resolve_x_cookie", lambda **kwargs: "auth_token=a; ct0=b"
    )
    monkeypatch.setattr("openbiliclaw.sources.x_client.XClient", _FakeXClient)

    likes, bookmarks = await cli_module._fetch_x_init_data(likes_limit=5, bookmarks_limit=5)
    assert [t["id"] for t in likes] == ["1"]
    assert [t["id"] for t in bookmarks] == ["2"]

    from openbiliclaw.api.source_auth.write import credential_fingerprint
    from openbiliclaw.storage.database import Database
    from openbiliclaw.storage.x_health import XSourceHealthStore

    db = Database(tmp_path / "openbiliclaw.db")
    db.initialize()
    health = XSourceHealthStore(db).get()
    assert health["state"] == "ok"
    assert health["last_success_at"]
    assert health["last_success_credential"] == credential_fingerprint(
        "twitter", "auth_token=a; ct0=b"
    )
    db.close()


def test_fetch_x_ingests_likes_and_bookmarks(runner, monkeypatch) -> None:
    async def _fake_fetch(*, likes_limit, bookmarks_limit):
        assert likes_limit == 5
        assert bookmarks_limit == 5
        return (
            [{"id": "1", "text": "liked tweet", "author": {"screenName": "a"}}],
            [{"id": "2", "text": "saved tweet", "author": {"screenName": "b"}}],
        )

    class _FakeMemory:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def propagate_event(self, ev: dict[str, Any]) -> None:
            self.events.append(ev)

    mem = _FakeMemory()
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_bootstrap_container_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_fetch_x_init_data", _fake_fetch)
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: mem)

    result = runner.invoke(app, ["fetch-x", "-n", "5"])

    assert result.exit_code == 0, result.output
    assert [e["event_type"] for e in mem.events] == ["like", "favorite"]
    assert mem.events[0]["metadata"]["tweet_id"] == "1"
    assert mem.events[0]["url"] == "https://x.com/a/status/1"


def test_fetch_x_dry_run_does_not_persist(runner, monkeypatch) -> None:
    async def _fake_fetch(*, likes_limit, bookmarks_limit):
        return ([{"id": "1", "text": "liked", "author": {"screenName": "a"}}], [])

    built = {"memory": False}

    def _build_mem():
        built["memory"] = True
        return object()

    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_bootstrap_container_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_fetch_x_init_data", _fake_fetch)
    monkeypatch.setattr(cli_module, "_build_memory_manager", _build_mem)

    result = runner.invoke(app, ["fetch-x", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert built["memory"] is False  # dry-run must not build memory / persist


def test_fetch_x_no_events_exits_cleanly(runner, monkeypatch) -> None:
    async def _fake_fetch(*, likes_limit, bookmarks_limit):
        return ([], [])

    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    monkeypatch.setattr(cli_module, "_bootstrap_container_runtime", lambda: None)
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_fetch_x_init_data", _fake_fetch)

    result = runner.invoke(app, ["fetch-x"])

    assert result.exit_code == 0, result.output


# ── run_guided_init: bilibili-optional pipeline (v0.3.118+) ──────────────


def _guided_init_pipeline_doubles(monkeypatch) -> dict[str, Any]:
    """Stub the heavy collaborators of run_guided_init for pipeline tests."""
    state: dict[str, Any] = {"propagated": [], "analyzed": None, "profile_history": None}

    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", lambda **kwargs: "xhs-task-1")
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, **kwargs: (
            (list(state.get("xhs_events", [])), {"saved": 1, "liked": 0, "xhs_history": 0}, "ok")
            if task_id
            else ([], {}, "skipped")
        ),
    )
    monkeypatch.setattr(
        cli_module, "_collect_dy_bootstrap_events", lambda task_id, **kwargs: ([], {}, "skipped")
    )
    monkeypatch.setattr(
        cli_module, "_collect_yt_bootstrap_events", lambda task_id, **kwargs: ([], {}, "skipped")
    )
    monkeypatch.setattr(
        cli_module, "_enqueue_zhihu_bootstrap_task", lambda **kwargs: "zhihu-task-1"
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_zhihu_bootstrap_events",
        lambda task_id, **kwargs: (
            (
                list(state.get("zhihu_events", [])),
                {
                    "zhihu_read_history": 1,
                    "zhihu_collection": 0,
                    "zhihu_activity_like": 0,
                    "zhihu_activity_favorite": 0,
                },
                "ok",
            )
            if task_id
            else (
                [],
                {
                    "zhihu_read_history": 0,
                    "zhihu_collection": 0,
                    "zhihu_activity_like": 0,
                    "zhihu_activity_favorite": 0,
                },
                "skipped",
            )
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_enqueue_reddit_bootstrap_task",
        lambda **kwargs: "reddit-task-1",
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_reddit_bootstrap_events",
        lambda task_id, **kwargs: (
            (
                list(state.get("reddit_events", [])),
                {
                    "reddit_saved": 1,
                    "reddit_upvoted": 0,
                    "reddit_subscribed": 0,
                },
                "ok",
            )
            if task_id
            else (
                [],
                {
                    "reddit_saved": 0,
                    "reddit_upvoted": 0,
                    "reddit_subscribed": 0,
                },
                "skipped",
            )
        ),
        raising=False,
    )

    def _enqueue_linuxdo(**kwargs: object) -> str:
        state["linuxdo_enqueue_kwargs"] = dict(kwargs)
        return "linuxdo-task-1"

    monkeypatch.setattr(
        cli_module,
        "_enqueue_linuxdo_bootstrap_task",
        _enqueue_linuxdo,
    )
    monkeypatch.setattr(
        cli_module,
        "_collect_linuxdo_bootstrap_events",
        lambda task_id, **kwargs: (
            (
                list(state.get("linuxdo_events", [])),
                {
                    "linuxdo_bookmarks": 1,
                    "linuxdo_likes": 0,
                    "linuxdo_read_history": 0,
                },
                "ok",
            )
            if task_id
            else (
                [],
                {
                    "linuxdo_bookmarks": 0,
                    "linuxdo_likes": 0,
                    "linuxdo_read_history": 0,
                },
                "skipped",
            )
        ),
    )

    async def _fetch_bangumi(*, username: str, token: str = ""):
        state["bangumi_fetch_token"] = token
        events = list(state.get("bangumi_events", []))
        counts: dict[str, int] = {}
        for event in events:
            status = str((event.get("metadata") or {}).get("collection_status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        if events:
            return events, counts, "ok"
        if token or username:
            return events, counts, "empty"
        return events, counts, "missing_username"

    monkeypatch.setattr(cli_module, "_fetch_bangumi_init_data", _fetch_bangumi)
    monkeypatch.setattr(cli_module, "_maybe_update_init_source_shares", lambda counts: None)

    class _Memory:
        async def propagate_event(self, event: dict[str, Any]) -> None:
            state["propagated"].append(event)

    class _Soul:
        async def analyze_events(self, events: list[dict[str, Any]], **kwargs: Any) -> None:
            state["analyzed"] = list(events)

        async def build_initial_profile(self, history: list[dict[str, Any]]) -> dict[str, Any]:
            state["profile_history"] = list(history)
            return {"ok": True}

    async def _backfill(profile: Any, *, target_pool_count: int, label_suffix: str = "") -> int:
        return 7

    state["memory"] = _Memory()
    state["soul"] = _Soul()
    state["backfill"] = _backfill
    return state


def test_run_guided_init_without_bilibili_builds_profile_from_xhs(monkeypatch) -> None:
    """include_bili=False skips the B站 fetch entirely; XHS signals alone feed
    analyze + profile build (client may be None)."""
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)
    state["xhs_events"] = [
        {
            "event_type": "favorite",
            "title": "咖啡手冲入门",
            "url": "https://www.xiaohongshu.com/explore/abc",
            "context": "在小红书收藏了《咖啡手冲入门》",
            "metadata": {"source_platform": "xiaohongshu", "author": "某博主"},
        }
    ]

    result = asyncio.run(
        cli_module.run_guided_init(
            client=None,
            memory=state["memory"],
            soul_engine=state["soul"],
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=True,
            include_dy=False,
            include_yt=False,
            target_pool_count=10,
            discover_backfill=state["backfill"],
        )
    )

    assert result.history == []
    assert result.bilibili_event_count == 0
    assert result.xhs_status == "ok"
    assert result.discovered_count == 7
    assert result.profile_data == {"ok": True}
    assert state["analyzed"] == state["xhs_events"]
    # XHS events feed the profile builder even with no B站 history at all.
    assert state["profile_history"]
    assert state["profile_history"][0]["source_platform"] == "xiaohongshu"
    # Cross-platform events are persisted by the task-result handler, not here.
    assert state["propagated"] == []


def test_run_guided_init_without_bilibili_builds_profile_from_zhihu(monkeypatch) -> None:
    """Zhihu bootstrap signals should participate in first-profile init just
    like the other plugin-backed sources."""
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)
    state["zhihu_events"] = [
        {
            "event_type": "favorite",
            "title": "LLM Agent 工程经验",
            "url": "https://www.zhihu.com/question/1/answer/2",
            "context": "知乎收藏夹：LLM Agent 工程经验 作者：知乎作者",
            "metadata": {
                "source_platform": "zhihu",
                "author": "知乎作者",
                "import_source": "zhihu_bootstrap_collection",
            },
        }
    ]

    result = asyncio.run(
        cli_module.run_guided_init(
            client=None,
            memory=state["memory"],
            soul_engine=state["soul"],
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_zhihu=True,
            target_pool_count=10,
            discover_backfill=state["backfill"],
        )
    )

    assert result.history == []
    assert result.bilibili_event_count == 0
    assert result.zhihu_status == "ok"
    assert result.zhihu_scope_counts["zhihu_read_history"] == 1
    assert state["analyzed"] == state["zhihu_events"]
    assert state["profile_history"]
    assert state["profile_history"][0]["source_platform"] == "zhihu"
    assert state["propagated"] == state["zhihu_events"]


def test_run_guided_init_linuxdo_uses_account_owned_staged_ingress(monkeypatch) -> None:
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)
    state["linuxdo_events"] = [
        {
            "event_type": "favorite",
            "title": "Linux.do 收藏",
            "url": "https://linux.do/t/example/42",
            "context": "在 Linux.do 收藏了《Linux.do 收藏》",
            "metadata": {
                "source_platform": "linuxdo",
                "content_id": "topic:42",
                "account_key": "linuxdo-user:7",
            },
        }
    ]

    result = asyncio.run(
        cli_module.run_guided_init(
            client=None,
            memory=state["memory"],
            soul_engine=state["soul"],
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_linuxdo=True,
            target_pool_count=10,
            discover_backfill=state["backfill"],
        )
    )

    assert result.linuxdo_status == "ok"
    assert state["linuxdo_enqueue_kwargs"]["profile_update"] is True
    assert state["analyzed"] == state["linuxdo_events"]
    assert state["profile_history"][0]["source_platform"] == "linuxdo"


def test_run_guided_init_all_sources_empty_raises_empty_signals(monkeypatch) -> None:
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)

    with pytest.raises(cli_module.GuidedInitError) as excinfo:
        asyncio.run(
            cli_module.run_guided_init(
                client=None,
                memory=state["memory"],
                soul_engine=state["soul"],
                favorite_limit=0,
                follow_limit=0,
                include_bili=False,
                include_xhs=False,
                include_dy=False,
                include_yt=False,
                include_zhihu=False,
                target_pool_count=10,
                discover_backfill=state["backfill"],
            )
        )

    assert excinfo.value.reason == "empty_signals"


def test_run_guided_init_without_bilibili_builds_profile_from_reddit(monkeypatch) -> None:
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)
    state["reddit_events"] = [
        {
            "event_type": "favorite",
            "title": "Practical local agent notes",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/title/",
            "context": "在Reddit收藏了《Practical local agent notes》,作者:u/agent_builder",
            "metadata": {
                "source_platform": "reddit",
                "author": "u/agent_builder",
                "scope": "reddit_saved",
                "import_source": "reddit_bootstrap_events",
            },
        }
    ]

    result = asyncio.run(
        cli_module.run_guided_init(
            client=None,
            memory=state["memory"],
            soul_engine=state["soul"],
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_zhihu=False,
            include_reddit=True,
            target_pool_count=10,
            discover_backfill=state["backfill"],
        )
    )

    assert result.history == []
    assert result.bilibili_event_count == 0
    assert result.reddit_status == "ok"
    assert result.reddit_scope_counts["reddit_saved"] == 1
    assert state["analyzed"] == state["reddit_events"]
    assert state["profile_history"]
    assert state["profile_history"][0]["source_platform"] == "reddit"
    assert state["propagated"] == state["reddit_events"]


def test_run_guided_init_builds_profile_from_bangumi_public_collection(monkeypatch) -> None:
    import asyncio

    state = _guided_init_pipeline_doubles(monkeypatch)
    state["bangumi_events"] = [
        {
            "event_type": "like",
            "title": "Cowboy Bebop",
            "url": "https://bgm.tv/subject/253",
            "context": "在Bangumi点赞了《Cowboy Bebop》",
            "metadata": {
                "source_platform": "bangumi",
                "collection_status": "collect",
                "user_rate": 9,
            },
        }
    ]

    result = asyncio.run(
        cli_module.run_guided_init(
            client=None,
            memory=state["memory"],
            soul_engine=state["soul"],
            favorite_limit=0,
            follow_limit=0,
            include_bili=False,
            include_xhs=False,
            include_dy=False,
            include_yt=False,
            include_zhihu=False,
            include_reddit=False,
            include_bangumi=True,
            bangumi_username="sai",
            target_pool_count=10,
            discover_backfill=state["backfill"],
        )
    )

    assert result.bangumi_status == "ok"
    assert result.bangumi_scope_counts == {"collect": 1}
    assert state["analyzed"] == state["bangumi_events"]
    assert state["profile_history"][0]["source_platform"] == "bangumi"
    assert state["propagated"] == state["bangumi_events"]


def test_init_command_rejects_no_sources_at_all(monkeypatch) -> None:
    """--no-bilibili plus every other source disabled must exit with guidance."""
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(
        cli_module, "_get_runtime_database", lambda: SimpleNamespace(max_llm_usage_id=lambda: None)
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    monkeypatch.setattr(cli_module, "_ask_network_binding", lambda: False)
    monkeypatch.setattr(cli_module, "_persist_api_host_choice", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_maybe_setup_password_in_init", lambda **kwargs: None)

    result = runner.invoke(
        app,
        ["init", "--no-bilibili", "--no-xhs", "--no-douyin", "--no-youtube", "--no-x"],
    )

    assert result.exit_code == 1
    assert "至少需要一个数据来源" in result.output


def test_fetch_bangumi_init_data_token_resolves_me_and_reads_private(monkeypatch) -> None:
    """With a token, /v0/me decides the account and private rows are included."""
    import asyncio

    from openbiliclaw.config import Config

    cfg = Config()
    cfg.sources.bangumi.access_token = "tok-123"
    cfg.sources.bangumi.username = "typed-name"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)

    fetch_calls: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            fetch_calls["access_token"] = kwargs.get("access_token")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_me(self):
            return {"username": "token-owner"}

    async def _fake_fetch(client, *, username, subject_types, limit, include_private=False):
        fetch_calls["username"] = username
        fetch_calls["include_private"] = include_private
        return [
            {
                "event_type": "like",
                "title": "t",
                "url": "u",
                "metadata": {"collection_status": "done"},
            }
        ]

    monkeypatch.setattr("openbiliclaw.sources.bangumi_client.BangumiClient", _FakeClient)
    monkeypatch.setattr(
        "openbiliclaw.sources.bangumi.fetch_bangumi_public_collection_events", _fake_fetch
    )

    events, counts, status = asyncio.run(cli_module._fetch_bangumi_init_data(username="typed-name"))

    assert status == "ok"
    assert len(events) == 1
    assert counts == {"done": 1}
    assert fetch_calls["access_token"] == "tok-123"
    # /v0/me wins over the differing configured username; private rows included.
    assert fetch_calls["username"] == "token-owner"
    assert fetch_calls["include_private"] is True


def test_fetch_bangumi_init_data_reports_invalid_token(monkeypatch) -> None:
    """A 401 from /v0/me surfaces as invalid_token instead of a silent skip."""
    import asyncio

    from openbiliclaw.config import Config
    from openbiliclaw.sources.bangumi_client import BangumiAPIError

    cfg = Config()
    cfg.sources.bangumi.access_token = "expired"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)

    class _FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_me(self):
            raise BangumiAPIError("unauthorized", "denied", status_code=401)

    monkeypatch.setattr("openbiliclaw.sources.bangumi_client.BangumiClient", _FakeClient)

    events, counts, status = asyncio.run(cli_module._fetch_bangumi_init_data(username=""))

    assert status == "invalid_token"
    assert events == []
    assert counts == {}


def test_init_command_rejects_bangumi_only_without_username(monkeypatch) -> None:
    from openbiliclaw.config import Config

    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(
        cli_module, "_get_runtime_database", lambda: SimpleNamespace(max_llm_usage_id=lambda: None)
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    monkeypatch.setattr(cli_module, "_ask_network_binding", lambda: False)
    monkeypatch.setattr(cli_module, "_persist_api_host_choice", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_maybe_setup_password_in_init", lambda **kwargs: None)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: Config())

    result = runner.invoke(
        app,
        [
            "init",
            "--no-bilibili",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
            "--no-x",
            "--no-zhihu",
            "--no-reddit",
            "--yes-bangumi",
        ],
    )

    assert result.exit_code == 1
    assert "Bangumi 缺少令牌或用户名" in result.output


def _patch_bangumi_init_fetch(monkeypatch, *, me_username_value="token-owner", token_error=None):
    """Wire fake Bangumi client + collection fetch for _fetch_bangumi_init_data."""
    import openbiliclaw.sources.bangumi as bangumi_mod
    import openbiliclaw.sources.bangumi_client as bc_mod

    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, *, access_token=None, **kwargs):
            captured["access_token"] = access_token

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_me(self):
            if token_error is not None:
                raise token_error
            return {"username": me_username_value}

    async def _fake_fetch(client, *, username, subject_types, limit, include_private=False):
        captured["username"] = username
        captured["include_private"] = include_private
        return [
            {
                "event_type": "like",
                "title": "X",
                "url": "https://bgm.tv/subject/1",
                "metadata": {"collection_status": "collect"},
            }
        ]

    monkeypatch.setattr(bc_mod, "BangumiClient", _FakeClient)
    monkeypatch.setattr(bangumi_mod, "fetch_bangumi_public_collection_events", _fake_fetch)
    return captured


def test_fetch_bangumi_init_data_token_resolves_username_and_reads_private(monkeypatch) -> None:
    import asyncio

    cfg = config_module.Config()
    cfg.sources.bangumi.access_token = "tok-1"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    captured = _patch_bangumi_init_fetch(monkeypatch, me_username_value="token-owner")

    events, counts, status = asyncio.run(
        cli_module._fetch_bangumi_init_data(username="typed-name", token="")
    )

    assert status == "ok"
    assert len(events) == 1
    # Token wins: username auto-resolved from /v0/me and private rows included.
    assert captured["access_token"] == "tok-1"
    assert captured["username"] == "token-owner"
    assert captured["include_private"] is True


def test_fetch_bangumi_init_data_without_token_uses_public_username(monkeypatch) -> None:
    import asyncio

    cfg = config_module.Config()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    captured = _patch_bangumi_init_fetch(monkeypatch)

    events, _counts, status = asyncio.run(
        cli_module._fetch_bangumi_init_data(username="sai", token="")
    )

    assert status == "ok"
    assert captured["access_token"] is None
    assert captured["username"] == "sai"
    assert captured["include_private"] is False


def test_fetch_bangumi_init_data_missing_username_without_token(monkeypatch) -> None:
    import asyncio

    cfg = config_module.Config()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    _patch_bangumi_init_fetch(monkeypatch)

    events, counts, status = asyncio.run(cli_module._fetch_bangumi_init_data(username="", token=""))

    assert (events, counts, status) == ([], {}, "missing_username")


def test_load_extension_bangumi_identity_reads_runtime_state(monkeypatch, tmp_path) -> None:
    import json

    cfg = config_module.Config()
    cfg.data_dir = str(tmp_path)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)

    # No state file yet → ("", False).
    assert cli_module._load_extension_bangumi_identity() == ("", False)

    state_dir = tmp_path / "memory"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "discovery_runtime.json"

    state_path.write_text(
        json.dumps({"bangumi_self_info": {"uid": "123", "username": "ext-user", "verified": True}}),
        encoding="utf-8",
    )
    assert cli_module._load_extension_bangumi_identity() == ("ext-user", True)

    # Fail-open (bgm.tv unreachable) record: usable, but flagged unverified.
    state_path.write_text(
        json.dumps(
            {"bangumi_self_info": {"uid": "123", "username": "ext-user", "verified": False}}
        ),
        encoding="utf-8",
    )
    assert cli_module._load_extension_bangumi_identity() == ("ext-user", False)

    # A verified record with no username is one the superseded 404 path wrote;
    # no current rule produces it, so it normalises to unverified on read.
    state_path.write_text(
        json.dumps({"bangumi_self_info": {"uid": "123", "username": "", "verified": True}}),
        encoding="utf-8",
    )
    assert cli_module._load_extension_bangumi_identity() == ("", False)

    # Backward compatibility: a record written before the flag existed cannot
    # prove it was ever cross-checked, so it reads back as unverified.
    state_path.write_text(
        json.dumps({"bangumi_self_info": {"uid": "123", "username": "ext-user"}}),
        encoding="utf-8",
    )
    assert cli_module._load_extension_bangumi_identity() == ("ext-user", False)

    # Malformed values are treated as missing, never propagated.
    state_path.write_text(
        json.dumps({"bangumi_self_info": {"uid": "123", "username": "bad/name"}}),
        encoding="utf-8",
    )
    assert cli_module._load_extension_bangumi_identity() == ("", False)
    state_path.write_text("not-json", encoding="utf-8")
    assert cli_module._load_extension_bangumi_identity() == ("", False)


def test_fetch_bangumi_init_data_expired_token_returns_invalid_token(monkeypatch) -> None:
    import asyncio

    from openbiliclaw.sources.bangumi_client import BangumiAPIError

    cfg = config_module.Config()
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    _patch_bangumi_init_fetch(
        monkeypatch,
        token_error=BangumiAPIError("unauthorized", "denied", status_code=401),
    )

    events, counts, status = asyncio.run(
        cli_module._fetch_bangumi_init_data(username="", token="expired-tok")
    )

    assert status == "invalid_token"
    assert events == []


def test_fetch_bangumi_rebuild_profile_skips_bilibili_auth_gate(monkeypatch) -> None:
    """``fetch-bangumi --rebuild-profile`` rebuilds from Bangumi events alone, so
    it must NOT go through the Bilibili auth gate. On a non-interactive terminal
    the old ``_prepare_init_runtime()`` would abort with '请先执行 auth login';
    the rebuild now passes ``require_bili_auth=False`` and proceeds without ever
    building a Bilibili auth manager."""
    runner = CliRunner()

    cfg = config_module.Config()
    cfg.sources.bangumi.username = "sai"
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    _patch_bangumi_init_fetch(monkeypatch)

    # Runtime config is fine; the auth manager must never be built for a
    # Bangumi-only rebuild — trip it to prove the B站 auth gate is bypassed.
    monkeypatch.setattr(cli_module, "_load_runtime_config_error", lambda render=False: None)
    monkeypatch.setattr(
        cli_module,
        "_build_auth_manager",
        lambda: (_ for _ in ()).throw(
            AssertionError("Bangumi rebuild must not touch the Bilibili auth gate")
        ),
    )
    monkeypatch.setattr(cli_module, "_write_events_to_memory", lambda events, source="": (1, 0))

    analyzed: dict[str, object] = {}

    class _Soul:
        async def analyze_events(self, events, **kwargs):
            analyzed["events"] = list(events)

        async def build_initial_profile(self, history):
            analyzed["history"] = list(history)
            return {"ok": True}

    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: _Soul())

    result = runner.invoke(app, ["fetch-bangumi", "--rebuild-profile"])

    assert result.exit_code == 0, result.output
    assert "完成" in result.output and "画像重建" in result.output
    assert analyzed.get("events")  # analyze ran on the fetched Bangumi events
    assert analyzed.get("history")  # profile builder consumed the history items


def test_init_command_allows_reddit_as_only_profile_signal_source(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(
        cli_module, "_get_runtime_database", lambda: SimpleNamespace(max_llm_usage_id=lambda: None)
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    monkeypatch.setattr(cli_module, "_ask_network_binding", lambda: False)
    monkeypatch.setattr(cli_module, "_persist_api_host_choice", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_maybe_setup_password_in_init", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli_module, "_print_init_cost_summary", lambda *args, **kwargs: None)

    captured: dict[str, object] = {}

    async def _fake_init(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(
            history=[],
            favorites_data=[],
            following_data=[],
            events=[
                {
                    "event_type": "favorite",
                    "title": "Reddit saved",
                    "metadata": {"source_platform": "reddit"},
                }
            ],
            bilibili_event_count=0,
            xhs_events=[],
            xhs_scope_counts={},
            xhs_status="skipped",
            dy_events=[],
            dy_scope_counts={},
            dy_status="skipped",
            yt_events=[],
            yt_scope_counts={},
            yt_status="skipped",
            zhihu_events=[],
            zhihu_scope_counts={},
            zhihu_status="skipped",
            reddit_events=[
                {
                    "event_type": "favorite",
                    "title": "Reddit saved",
                    "metadata": {"source_platform": "reddit"},
                }
            ],
            reddit_scope_counts={"reddit_saved": 1},
            reddit_status="ok",
            profile_data={"ok": True},
            discovered_count=0,
            discovery_error=False,
            discover_exc=None,
        )

    monkeypatch.setattr(cli_module, "run_guided_init", _fake_init)

    result = runner.invoke(
        app,
        [
            "init",
            "--no-bilibili",
            "--no-xhs",
            "--no-douyin",
            "--no-youtube",
            "--no-x",
            "--no-zhihu",
            "--yes-reddit",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["include_bili"] is False
    assert captured["include_reddit"] is True
    assert "Reddit" in result.output


def test_init_summary_marks_douyin_degraded_without_dropping_collected_events(
    monkeypatch,
) -> None:
    """Usable partial Douyin events still feed the profile, while the final
    summary remains explicitly degraded instead of claiming full success."""
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "_prepare_init_runtime", lambda: None)
    monkeypatch.setattr(
        cli_module, "_get_runtime_database", lambda: SimpleNamespace(max_llm_usage_id=lambda: None)
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: object())
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_build_bilibili_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be built")),
    )
    monkeypatch.setattr(cli_module, "_ask_network_binding", lambda: False)
    monkeypatch.setattr(cli_module, "_persist_api_host_choice", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_maybe_setup_password_in_init", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "_notify_running_server_init_completed", lambda: None)
    monkeypatch.setattr(cli_module, "_print_init_cost_summary", lambda *args, **kwargs: None)

    captured: dict[str, object] = {}
    dy_event = {
        "event_type": "favorite",
        "title": "已采到的收藏",
        "metadata": {"source_platform": "douyin"},
    }

    async def _fake_init(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(
            history=[],
            favorites_data=[],
            following_data=[],
            events=[dy_event],
            bilibili_event_count=0,
            xhs_events=[],
            xhs_scope_counts={},
            xhs_status="skipped",
            dy_events=[dy_event],
            dy_scope_counts={"dy_collect": 1},
            dy_status="degraded",
            yt_events=[],
            yt_scope_counts={},
            yt_status="skipped",
            zhihu_events=[],
            zhihu_scope_counts={},
            zhihu_status="skipped",
            reddit_events=[],
            reddit_scope_counts={},
            reddit_status="skipped",
            profile_data={"ok": True},
            discovered_count=3,
            discovery_error=False,
            discover_exc=None,
            discovery_detail="",
        )

    monkeypatch.setattr(cli_module, "run_guided_init", _fake_init)

    result = runner.invoke(
        app,
        [
            "init",
            "--no-bilibili",
            "--no-xhs",
            "--yes-douyin",
            "--no-youtube",
            "--no-x",
            "--no-zhihu",
            "--no-reddit",
            "--no-bangumi",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["include_dy"] is True
    assert "抖音采集部分完成" in result.output
    assert "已采到的抖音事件仍已用于画像建模" in result.output
    assert "初始化部分完成" in result.output
    assert "dy_status" in result.output
    assert "degraded" in result.output
    assert "初始化完成" not in result.output


@pytest.mark.asyncio
async def test_run_with_progress_heartbeat_reports_status_and_honest_eta(monkeypatch) -> None:
    """Heartbeat appends the live sub-progress and never pins the ETA at ~0s.

    Guards the desktop.log stall-visibility fix: past ``eta_seconds`` the tick
    reads "已超预估" (not a lying "预计还需 ~0s") and carries the
    ``status_provider`` suffix so a frozen "已完成 X/N 批" localises the hang.
    """
    import asyncio as _asyncio

    buf = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=buf, force_terminal=False, width=200))

    async def _work() -> str:
        await _asyncio.sleep(0.16)
        return "ok"

    result = await cli_module._run_with_progress(
        _work(),
        label="分析偏好（分片批处理）",
        eta_seconds=0,
        tick_seconds=0.05,
        status_provider=lambda: "已完成 2/5 批",
    )

    out = buf.getvalue()
    assert result == "ok"
    assert "已超预估" in out
    assert "已完成 2/5 批" in out
    assert "预计还需 ~0s" not in out


def _render_cli_console(monkeypatch, render) -> str:
    """Run ``render()`` against a captured CLI console and return the text."""
    buf = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=buf, force_terminal=False, width=200))
    render()
    return buf.getvalue()


def test_discovered_content_preview_shows_non_bilibili_author_as_neutral_label(
    monkeypatch,
) -> None:
    """Bangumi rows carry only ``author_name`` — show it, and call it 作者.

    Regression for the CLI half of the four-surface contract: the three GUI
    surfaces already read the universal ``author_name``, but the CLI read the
    Bilibili-only ``up_name``, so every non-Bilibili source printed "（未知）"
    (real smoke: ``discover-bangumi 攻壳机动队`` hid 押井守). "UP 主" is also
    wrong for a film director / Zhihu answerer / YouTube channel.
    """
    content = DiscoveredContent(
        content_id="8",
        title="攻壳机动队",
        source_platform="bangumi",
        author_name="押井守",
        source_strategy="bangumi-search",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_discovered_content_preview(content, 1),
    )

    assert "押井守" in out
    assert "作者" in out
    assert "UP 主" not in out
    assert "（未知）" not in out


def test_discovered_content_preview_keeps_up_label_for_bilibili(monkeypatch) -> None:
    """Bilibili keeps its native "UP 主" label and still resolves the name."""
    content = DiscoveredContent(
        bvid="BV1AUTHOR",
        title="城市与建筑的空间叙事",
        up_name="城市观察局",
        source_platform="bilibili",
        source_strategy="search",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_discovered_content_preview(content, 1),
    )

    assert "UP 主" in out
    assert "城市观察局" in out
    assert "作者" not in out


def test_discovered_content_preview_falls_back_to_bilibili_label_for_legacy_rows(
    monkeypatch,
) -> None:
    """A row with no ``source_platform`` keeps the old "UP 主" label.

    Matches ``formatRecommendationAuthorLine``'s ``|| "bilibili"`` default so
    pre-multi-source rows do not silently re-label themselves.
    """
    content = DiscoveredContent(title="老数据行为", up_name="旧的观察局")

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_discovered_content_preview(content, 1),
    )

    assert "UP 主" in out
    assert "旧的观察局" in out
    assert "作者" not in out


def test_recommendation_card_shows_non_bilibili_author_as_neutral_label(monkeypatch) -> None:
    """The recommendation card reads the same universal author field."""
    item = Recommendation(
        recommendation_id=11,
        content=DiscoveredContent(
            content_id="8",
            title="攻壳机动队",
            source_platform="bangumi",
            author_name="押井守",
        ),
        expression="这条会对上你最近那种想把结构想透的劲头。",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_recommendation_card(item, 1),
    )

    assert "押井守" in out
    assert "作者" in out
    assert "UP 主" not in out


def test_recommendation_card_keeps_up_label_for_bilibili(monkeypatch) -> None:
    """Bilibili recommendation cards are untouched by the author-row fix."""
    item = Recommendation(
        recommendation_id=12,
        content=DiscoveredContent(
            bvid="BV1REC",
            title="讲透城市与建筑的空间叙事",
            up_name="城市观察局",
        ),
        expression="这条会对上你最近那种想把结构想透的劲头。",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_recommendation_card(item, 1),
    )

    assert "UP 主" in out
    assert "城市观察局" in out
    assert "作者" not in out


def test_recommendation_card_labels_non_bilibili_id_neutrally(monkeypatch) -> None:
    """A Bangumi subject id must not be announced as a "BV号".

    ``bangumi_subject_to_content`` stores the subject id in the universal
    ``bvid`` column, so the old unconditional label rendered ``BV号  8``.
    """
    item = Recommendation(
        recommendation_id=13,
        content=DiscoveredContent(
            content_id="8",
            bvid="8",
            title="攻壳机动队",
            source_platform="bangumi",
            author_name="押井守",
        ),
        expression="这条会对上你最近那种想把结构想透的劲头。",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_recommendation_card(item, 1),
    )

    assert "内容 ID" in out
    assert "BV号" not in out
    assert "8" in out


def test_recommendation_card_keeps_bv_label_for_bilibili(monkeypatch) -> None:
    """Bilibili rows keep the native "BV号" wording."""
    item = Recommendation(
        recommendation_id=14,
        content=DiscoveredContent(
            bvid="BV1REC",
            title="讲透城市与建筑的空间叙事",
            up_name="城市观察局",
            source_platform="bilibili",
        ),
        expression="这条会对上你最近那种想把结构想透的劲头。",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_recommendation_card(item, 1),
    )

    assert "BV号" in out
    assert "BV1REC" in out
    assert "内容 ID" not in out


def test_recommendation_card_falls_back_to_bv_label_for_legacy_rows(monkeypatch) -> None:
    """No ``source_platform`` keeps the old "BV号" label, like the author row."""
    item = Recommendation(
        recommendation_id=15,
        content=DiscoveredContent(bvid="BV1LEGACY", title="老数据行为", up_name="旧的观察局"),
        expression="这条会对上你最近那种想把结构想透的劲头。",
    )

    out = _render_cli_console(
        monkeypatch,
        lambda: cli_module._print_recommendation_card(item, 1),
    )

    assert "BV号" in out
    assert "BV1LEGACY" in out
    assert "内容 ID" not in out


class TestInitSignalsAreDeduplicable:
    """init 拉到的内容必须能被去重链路认出来，否则会被当新内容推回去。

    历史事件一直是入库的（stage 1 结尾 propagate_events），但收藏事件既不带 bvid
    也不带 url——没有身份就进不了 seen_items，用户明确收藏过的视频照样能被推荐。
    历史事件也缺完播元数据，于是每条观看在账本里都是"满意度未知"。
    """

    @staticmethod
    def _history_item(bvid: str = "BV1", *, progress: int = 100) -> dict:
        return {
            "title": "标题",
            "history": {"bvid": bvid},
            "author_name": "UP",
            "view_at": 1_785_000_000,
            "progress": progress,
            "duration": 200,
            "tag_name": "科技",
        }

    def test_history_events_carry_watch_metrics(self) -> None:
        """完播信息决定 classify_event_satisfaction 怎么读这条事件。"""
        from openbiliclaw.cli import _history_item_to_event

        event = _history_item_to_event(self._history_item(progress=180))
        metadata = event["metadata"]

        assert metadata["content_id"] == "BV1"
        assert metadata["watch_seconds"] == 180
        assert metadata["video_duration_seconds"] == 200
        assert metadata["category"] == "科技"

    def test_history_event_without_metrics_still_builds(self) -> None:
        from openbiliclaw.cli import _history_item_to_event

        event = _history_item_to_event({"title": "无进度", "history": {"bvid": "BV2"}})

        assert event["metadata"]["content_id"] == "BV2"
        assert "watch_seconds" not in event["metadata"]

    def test_favorite_events_carry_identity(self) -> None:
        """收藏没有身份就进不了 seen_items——收藏过的视频会被再推一遍。"""
        from openbiliclaw.cli import _build_bilibili_init_events

        events = _build_bilibili_init_events(
            history=[],
            favorites_data=[
                {
                    "title": "收藏的视频",
                    "upper": "UP",
                    "folder": "默认收藏夹",
                    "bvid": "BV3",
                    "fav_time": 1_785_000_001,
                    "duration": 300,
                }
            ],
            following_data=[],
        )

        assert len(events) == 1
        favorite = events[0]
        assert favorite["url"] == "https://www.bilibili.com/video/BV3"
        assert favorite["metadata"]["content_id"] == "BV3"
        assert favorite["metadata"]["fav_time"] == 1_785_000_001
        assert favorite["metadata"]["folder"] == "默认收藏夹"

    def test_favorite_without_bvid_degrades_quietly(self) -> None:
        from openbiliclaw.cli import _build_bilibili_init_events

        events = _build_bilibili_init_events(
            history=[], favorites_data=[{"title": "老数据", "upper": "UP"}], following_data=[]
        )

        assert events[0].get("url", "") == ""
        assert "content_id" not in events[0]["metadata"]

    def test_dead_favorites_never_become_signals(self) -> None:
        """失效视频的标题字面就是「已失效视频」，进画像就是噪声。"""
        from openbiliclaw.bilibili.api import favorite_item_is_dead
        from openbiliclaw.cli import _build_bilibili_init_events

        assert favorite_item_is_dead({"attr": 1, "title": "已失效视频"})
        assert favorite_item_is_dead({"title": "已失效视频"}), "没有 attr 时按标题兜底"
        assert not favorite_item_is_dead({"attr": 0, "title": "正常视频"})

        events = _build_bilibili_init_events(
            history=[],
            favorites_data=[
                {"title": "正常视频", "upper": "UP", "bvid": "BV5"},
            ],
            following_data=[],
        )
        assert [event["title"] for event in events] == ["正常视频"]

    def test_favorite_reach_reaches_the_ledger(self) -> None:
        """播放量 / 发布时间是「爱挖冷门还是追热门」的判据，别在拉取时丢掉。"""
        from openbiliclaw.cli import _build_bilibili_init_events

        events = _build_bilibili_init_events(
            history=[],
            favorites_data=[
                {
                    "title": "冷门视频",
                    "upper": "UP",
                    "bvid": "BV6",
                    "play_count": 431,
                    "pubtime": 1_780_000_000,
                }
            ],
            following_data=[],
        )

        assert events[0]["metadata"]["play_count"] == 431
        assert events[0]["metadata"]["pubtime"] == 1_780_000_000

    def test_intro_is_stored_but_never_reaches_the_prompt(self) -> None:
        """简介信噪比差（大量恰饭文案），入库备用但不花画像的 token。"""
        from openbiliclaw.cli import _build_bilibili_init_events

        favorites = [
            {"title": "视频", "upper": "UP", "bvid": "BV7", "intro": "简" * 400},
            {"title": "无简介", "upper": "UP", "bvid": "BV8", "intro": "-"},
        ]
        events = _build_bilibili_init_events(
            history=[], favorites_data=favorites, following_data=[]
        )

        assert len(events[0]["metadata"]["intro"]) == 200, "长简介要截断，别把账本撑爆"
        assert "intro" not in events[1]["metadata"], "占位符「-」不算简介"
        prompt_keys = {"title", "upper", "folder"}
        assert not (set(favorites[0]) & {"intro"}) <= prompt_keys, (
            "intro 不在画像 prompt 的白名单里"
        )

    def test_reimporting_the_same_snapshot_does_not_double_count(self, tmp_path: Path) -> None:
        """重跑 init 会重新拉同一份账号快照。

        实测：重跑一次后账本 56% 是重复行，699 个键连观看时间戳都一模一样——
        那不是二次观看，是同一次行为被记了两遍，凭事件条数算出来的权重全部虚高。
        """
        from openbiliclaw.cli import _drop_events_already_imported
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "t.db")
        db.initialize()

        def _event(event_type: str, bvid: str, stamp: int) -> dict:
            return {
                "event_type": event_type,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "title": "标题",
                "metadata": {"bvid": bvid, "content_id": bvid, "view_at": stamp},
            }

        snapshot = [_event("view", "BV1", 100), _event("favorite", "BV1", 300)]
        kept, skipped = _drop_events_already_imported(db, snapshot)
        assert (len(kept), skipped) == (2, 0), "第一次导入不该跳过任何东西"
        for event in kept:
            payload = dict(event)
            db.insert_event(payload.pop("event_type"), **payload)

        kept, skipped = _drop_events_already_imported(db, snapshot)
        assert (len(kept), skipped) == (0, 2), "同一份快照第二次导入应全部跳过"

    def test_a_genuine_rewatch_still_lands(self, tmp_path: Path) -> None:
        """键里带时间戳正是为了这个：重看是新行为，不能被去重吃掉。"""
        from openbiliclaw.cli import _drop_events_already_imported
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "t.db")
        db.initialize()
        first = {
            "event_type": "view",
            "url": "https://www.bilibili.com/video/BV1",
            "title": "标题",
            "metadata": {"bvid": "BV1", "content_id": "BV1", "view_at": 100},
        }
        db.insert_event("view", url=first["url"], title="标题", metadata=first["metadata"])

        rewatch = {**first, "metadata": {**first["metadata"], "view_at": 999}}
        kept, skipped = _drop_events_already_imported(db, [first, rewatch])

        assert skipped == 1
        assert [e["metadata"]["view_at"] for e in kept] == [999]

    def test_rows_without_identity_are_kept(self, tmp_path: Path) -> None:
        """认不出来 ≠ 重复。没有身份的行宁可留着，也不能默默丢信号。"""
        from openbiliclaw.cli import _drop_events_already_imported
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "t.db")
        db.initialize()
        faceless = {"event_type": "search", "url": "", "title": "搜索了什么", "metadata": {}}

        kept, skipped = _drop_events_already_imported(db, [faceless, faceless])

        assert (len(kept), skipped) == (2, 0)

    def test_missing_database_imports_everything(self) -> None:
        from openbiliclaw.cli import _drop_events_already_imported

        events = [{"event_type": "view", "metadata": {"bvid": "BV1"}}]
        assert _drop_events_already_imported(None, events) == (events, 0)

    def test_favorites_become_individual_history_rows(self) -> None:
        """一个收藏就是一个信号，不是汇总行里的一个数字。"""
        from openbiliclaw.cli import _favorites_to_history_rows

        rows = _favorites_to_history_rows(
            [
                {"title": "收藏的视频", "upper": "UP", "folder": "AI", "fav_time": 1_785_000_000},
                {"title": "   ", "upper": "UP"},
            ]
        )

        assert len(rows) == 1, "没有标题的行进画像只会变成噪声"
        assert rows[0]["event_type"] == "favorite", "event_type 决定强信号权重和「收藏了」语境"
        assert rows[0]["author_name"] == "UP"
        assert rows[0]["fav_time"] == 1_785_000_000

    def test_a_favorited_video_lands_in_the_seen_ledger(self, tmp_path: Path) -> None:
        """端到端：收藏事件写库后必须出现在 seen_items 里。"""
        from openbiliclaw.cli import _build_bilibili_init_events
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "t.db")
        db.initialize()
        for event in _build_bilibili_init_events(
            history=[],
            favorites_data=[{"title": "收藏的视频", "upper": "UP", "bvid": "BV4"}],
            following_data=[],
        ):
            db.insert_event(**event)

        assert "BV4" in db.get_seen_bvids(), "收藏过的内容不该再被推荐"
