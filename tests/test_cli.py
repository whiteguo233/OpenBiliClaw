"""CLI tests for configuration guidance behavior."""

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw import config as config_module
from openbiliclaw.bilibili.auth import AuthStatus
from openbiliclaw.bilibili.browser import BrowserCommandError
from openbiliclaw.cli import app
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.engine import Recommendation
from openbiliclaw.soul.profile import PreferenceLayer, SoulProfile


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


def test_start_uses_local_api_defaults(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
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
    assert called == {"host": "127.0.0.1", "port": 8420}


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
    assert "openbiliclaw.repaired.db" in result.stdout


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
        def __init__(self, *, registry: object, memory: object) -> None:
            self.registry = registry
            self.memory = memory

    class FakeRecommendationEngine:
        def __init__(self, *, llm: object, database: object, embedding_service: object = None) -> None:
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


def test_discover_reports_empty_results(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
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


def test_discover_displays_preview_rows(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
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
        async def get_profile(self) -> SoulProfile:
            return SoulProfile(
                personality_portrait=(
                    "这是一个偏爱深度内容、会主动寻找原理解释、决策比较克制的人。"
                    * 6
                ),
                core_traits=["理性", "谨慎", "自驱"],
                values=["成长", "真实"],
                life_stage="稳定积累阶段",
                deep_needs=["被理解", "持续成长"],
                preferences=PreferenceLayer(),
            )

    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: FakeSoulEngine(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(app, ["profile"])

    assert result.exit_code == 0
    assert "用户画像概览" in result.stdout
    assert "人格描述" in result.stdout
    assert "核心特质" in result.stdout
    assert "理性" in result.stdout
    assert "稳定积累阶段" in result.stdout


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

    def fake_save_runtime_config(provider: str, api_key: str) -> None:
        captured["provider"] = provider
        captured["api_key"] = api_key

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

    result = runner.invoke(app, ["init"], input="gemini\ngemini-key\n")

    assert result.exit_code == 1
    assert captured == {"provider": "gemini", "api_key": "gemini-key"}
    assert "初始化前配置引导" in result.stdout
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

    result = runner.invoke(app, ["init"], input="SESSDATA=valid\n")

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
            self, max_folders: int = 20, max_items_per_folder: int = 200,
        ) -> list[object]:
            return []

        async def get_following(
            self, page: int = 1, page_size: int = 50,
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

        async def analyze_events(self, events: list[dict[str, object]]) -> None:
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
    assert "发现内容数" in result.stdout
    assert fake_memory.events[0]["event_type"] == "view"
    assert fake_soul.analyzed_events
    assert fake_soul.built_history
    assert fake_discovery.calls


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

    class FakeSoulEngine:
        async def analyze_events(self, events: list[dict[str, object]]) -> None:
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
        ) -> list[DiscoveredContent]:
            self.calls.append((strategies, limit))
            if strategies == ["search", "related_chain"]:
                self.database.pool_count = 20
            elif strategies == ["trending"]:
                self.database.pool_count = 100
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
        (["search", "related_chain"], 100),
        (["trending"], 80),
    ]
    assert "补货阶段 1/3" in result.stdout
    assert "search + related_chain" in result.stdout
    assert "当前池子 0/100" in result.stdout
    assert "阶段完成" in result.stdout
    assert "当前池子 20/100" in result.stdout
    assert "发现内容数" in result.stdout
    assert "2" in result.stdout


def test_init_stops_backfill_early_when_first_stage_reaches_pool_target(
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

    class FakeSoulEngine:
        async def analyze_events(self, events: list[dict[str, object]]) -> None:
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
    assert fake_discovery.calls == [(["search", "related_chain"], 55)]


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

    class FakeSoulEngine:
        async def analyze_events(self, events: list[dict[str, object]]) -> None:
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
        ) -> list[DiscoveredContent]:
            raise RuntimeError("discovery unavailable")

    fake_database = type(
        "FakeDatabase",
        (),
        {"count_pool_candidates": lambda self: 0},
    )()
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
