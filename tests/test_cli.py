"""CLI tests for configuration guidance behavior."""

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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
    OnionProfile,
    PreferenceLayer,
    RoleLayer,
    SoulProfile,
    ValuesLayer,
)


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


def test_runtime_builders_share_database_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import openbiliclaw.discovery.engine as discovery_module
    import openbiliclaw.discovery.strategies.strategies as strategy_module
    import openbiliclaw.llm.service as llm_service_module
    import openbiliclaw.memory.manager as memory_module
    import openbiliclaw.recommendation.engine as recommendation_module
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
            module_overrides: object | None = None,
        ) -> None:
            self.registry = registry
            self.memory = memory
            self.module_overrides = module_overrides

    class FakeRecommendationEngine:
        def __init__(
            self,
            *,
            llm: object,
            database: object,
            embedding_service: object = None,
        ) -> None:
            self.llm = llm
            self.database = database

    class FakeDiscoveryEngine:
        def __init__(
            self,
            *,
            llm_service: object,
            database: object,
            concurrency: object | None = None,
            embedding_service: object | None = None,
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

    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openbiliclaw-test-data"),
        bilibili=SimpleNamespace(cookie=""),
    )

    monkeypatch.setattr(cli_module, "_RUNTIME_COMPONENTS", {}, raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: "registry", raising=False)
    monkeypatch.setattr(cli_module, "_build_bilibili_client", lambda: "client", raising=False)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
    monkeypatch.setattr(database_module, "Database", FakeDatabase)
    monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
    monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
    monkeypatch.setattr(recommendation_module, "RecommendationEngine", FakeRecommendationEngine)
    monkeypatch.setattr(discovery_module, "ContentDiscoveryEngine", FakeDiscoveryEngine)
    monkeypatch.setattr(strategy_module, "SearchStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "TrendingStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "RelatedChainStrategy", FakeStrategy)
    monkeypatch.setattr(strategy_module, "ExploreStrategy", FakeStrategy)

    recommendation_engine = cli_module._build_recommendation_engine()
    discovery_engine = cli_module._build_discovery_engine()

    assert len(created_databases) == 1
    assert created_databases[0].initialized == 1
    assert len(created_memories) == 1
    assert created_memories[0].initialized == 1
    assert created_memories[0].database is created_databases[0]
    assert recommendation_engine.database is created_databases[0]
    assert discovery_engine.database is created_databases[0]


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
    assert "抖音 direct discovery 未启用" in result.stdout


def test_discover_douyin_runs_direct_strategy(
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

    result = runner.invoke(app, ["discover", "--source", "douyin", "--limit", "5"])

    assert result.exit_code == 0
    assert "抖音内容发现" in result.stdout
    assert "抖音发现内容" in result.stdout
    assert "douyin" in result.stdout


def test_discover_douyin_reads_cookie_from_synced_file(
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

    result = runner.invoke(app, ["discover", "--source", "douyin", "--limit", "5"])

    assert result.exit_code == 0
    assert "没有发现到新抖音内容" in result.stdout


def test_discover_douyin_does_not_use_recent_bootstrap_creator_seeds_by_default(
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

    result = runner.invoke(app, ["discover", "--source", "douyin", "--limit", "5"])

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
                    ),
                    expression="这条会对上你最近那种想把结构想透的劲头。",
                    topic_label="你最近那股想把结构想透的劲头",
                    confidence=0.88,
                )
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
    assert fake_engine.marked_ids == [7]


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

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

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
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["feedback", "7", "dislike", "--note", "太浅了"])

    assert result.exit_code == 0
    assert "反馈已记录" in result.stdout
    assert fake_engine.calls == [(7, "dislike", "太浅了")]
    assert fake_memory.events[0]["event_type"] == "feedback"
    assert fake_memory.events[0]["metadata"]["recommendation_id"] == 7
    assert fake_memory.events[0]["metadata"]["feedback_type"] == "dislike"


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

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

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
        async def propagate_event(self, event: dict[str, object]) -> None:
            return None

    class FakeSoulEngine:
        def __init__(self) -> None:
            self.called = False

        async def process_feedback_batch_if_needed(self) -> dict[str, object]:
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
    #   7-8. "" — accept Bili favorite/follow init limits
    #   9+. "n" — skip optional source prompts
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
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    # v0.3.13: auth wizard now opens with a 2-choice prompt
    # (1=install extension and skip / 2=paste cookie now). To keep this
    # test exercising the manual-paste path, send "2" first.
    # v0.3.89+: init asks whether to allow LAN access before the source
    # prompts. Answer yes, accept Bili signal-limit defaults, then send "n"
    # to XHS / Douyin / YouTube so this test stays focused on the
    # cookie-prompt path.
    result = runner.invoke(app, ["init"], input="2\nSESSDATA=valid\ny\n\n\nn\nn\nn\n")

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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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


def test_init_caps_bilibili_favorites_and_following_at_300(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    class FakeBilibiliClient:
        async def get_user_history(self, max_items: int = 100) -> list[dict[str, object]]:
            assert max_items == 300
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
        ) -> list[object]:
            items = [
                SimpleNamespace(title=f"收藏视频 {idx}", upper=f"UP {idx}") for idx in range(350)
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
    assert len([event for event in analyzed if event["event_type"] == "favorite"]) == 300
    assert len([event for event in analyzed if event["event_type"] == "follow"]) == 300
    assert len(fake_memory.events) == 601
    built_history = fake_soul.built_history[0]
    assert len(built_history) == 3
    assert str(built_history[1]["_favorites_summary"]).startswith("共 300 个收藏")
    assert str(built_history[2]["_following_summary"]).startswith("共关注 300 人")


def test_init_accepts_custom_bilibili_favorites_and_following_limits(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
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
        ) -> list[object]:
            assert max_items_per_folder == 2
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
    monkeypatch.setattr(
        cli_module,
        "_build_draft_profile_for_discover",
        lambda memory: SoulProfile(preferences=PreferenceLayer()),
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

    result = runner.invoke(app, ["init"])

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
    monkeypatch.setattr(
        cli_module,
        "_build_draft_profile_for_discover",
        lambda memory: SoulProfile(preferences=PreferenceLayer()),
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
    monkeypatch.setattr(
        cli_module,
        "_build_draft_profile_for_discover",
        lambda memory: SoulProfile(preferences=PreferenceLayer()),
    )
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

    _persist_init_source_enabled_flags(include_xhs=False, include_dy=True, include_yt=True)

    assert config.sources.xiaohongshu.enabled is False
    assert config.sources.douyin.enabled is True
    assert config.sources.youtube.enabled is True
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
    monkeypatch.setattr(
        cli_module,
        "_build_draft_profile_for_discover",
        lambda memory: SoulProfile(preferences=PreferenceLayer()),
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
            self.pool_count = 0

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
    assert fake_discovery.calls == [
        (["search", "trending", "related_chain", "explore"], 20),
    ]
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
            self, events: list[dict[str, object]], event_chunk_size: int = 0
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
    assert reloaded.llm.embedding.base_url == "http://localhost:11434/v1"


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


def test_save_module_overrides_writes_per_module_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 4: per-module overrides round-trip through config.toml."""
    from openbiliclaw.config import (
        Config,
        LLMConfig,
        load_config_with_diagnostics,
        save_config,
    )

    config_path = tmp_path / "config.toml"
    save_config(Config(llm=LLMConfig(default_provider="openai")), config_path)
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
    assert reloaded.llm.discovery.provider == "deepseek"
    assert reloaded.llm.discovery.model == "deepseek-chat"
    assert reloaded.llm.soul.provider == "claude"
    assert reloaded.llm.soul.model == "claude-sonnet-4-5-20250929"
    assert reloaded.llm.evaluation.provider == ""
    assert reloaded.llm.evaluation.model == ""


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

    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: FakeDatabase())
    monkeypatch.setattr("openbiliclaw.sources.dy_tasks.DyTaskQueue", FakeQueue)

    task_id = _enqueue_dy_search_task(("猫", "美食"), max_items_per_keyword=7)
    assert task_id == "dy-search-task"
    assert captured["task_type"] == "search"
    assert captured["payload"] == {
        "keywords": ["猫", "美食"],
        "max_items_per_keyword": 7,
    }


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
    assert result.exit_code == 0  # timeout is not a hard failure
    assert "超时" in result.output
