"""Tests for the backend API app."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from openbiliclaw.api.app import create_app


def _wait_for_presence_count(ctx: object, expected: int) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        snapshot = ctx.presence.snapshot()
        if snapshot["active_count"] == expected:
            return
        time.sleep(0.01)
    assert ctx.presence.snapshot()["active_count"] == expected


@pytest.fixture(autouse=True)
def _isolate_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep create_app() route tests independent from the developer machine.

    Several API tests intentionally exercise routes with partial fake runtime
    components. create_app() still loads runtime config up front, so without
    this fixture CI sees the repo's empty template while local runs may see a
    private config.toml with real credentials.
    """

    from openbiliclaw.config import Config, save_config

    project_root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project_root))
    cfg = Config()
    cfg.llm.default_provider = "ollama"
    cfg.llm.ollama.model = "llama3"
    save_config(cfg, project_root / "config.toml")


class TestBackendAPI:
    """Route-level tests for the plugin backend API."""

    @pytest.mark.asyncio
    async def test_runtime_context_presence_survives_rebuild(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        ctx = RuntimeContext()
        original_presence = ctx.presence

        def _fake_rebuild_components(self: RuntimeContext, new_config: Config) -> None:
            self.config = new_config

        monkeypatch.setattr(RuntimeContext, "_rebuild_components", _fake_rebuild_components)

        await ctx.rebuild_from_config(Config())

        assert ctx.presence is original_presence

    @pytest.mark.asyncio
    async def test_runtime_context_skips_startup_one_shots_when_llm_work_blocked(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class FakeSpeculator:
            def __init__(self) -> None:
                self.force_tick_calls = 0

            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                self.force_tick_calls += 1

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()
                self.profile_calls = 0

            async def get_profile(self) -> dict[str, object]:
                self.profile_calls += 1
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.prewarm_calls = 0

            async def prewarm_pool_mmr_embeddings(self) -> int:
                self.prewarm_calls += 1
                return 1

        cfg = Config()
        cfg.scheduler.enabled = False
        soul = FakeSoulEngine()
        rec = FakeRecommendationEngine()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=soul,
            recommendation_engine=rec,
        )
        app = SimpleNamespace(state=SimpleNamespace())

        await ctx.restart_background_tasks(app)

        assert soul._speculator.force_tick_calls == 0
        assert rec.prewarm_calls == 0

    @pytest.mark.asyncio
    async def test_restart_tasks_detaches_speculator_tick(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class HangingSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                await asyncio.sleep(60)

        class FakeSoulEngine:
            _speculator = HangingSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        try:
            await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=0.5)
            assert ctx.task_registry.stats().get("post_reload_speculate") == 1
        finally:
            await ctx.task_registry.cancel_all()

    @pytest.mark.asyncio
    async def test_restart_tasks_swallows_detached_speculator_failure(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class BrokenSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("boom")

        class FakeSoulEngine:
            _speculator = BrokenSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(load_discovery_runtime_state=lambda: {}),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())
        captured_tasks: list[asyncio.Task[object]] = []
        original_track = ctx.task_registry.track

        def _track(name: str, coro):
            task = original_track(name, coro)
            if name == "post_reload_speculate":
                captured_tasks.append(task)
            return task

        ctx.task_registry.track = _track  # type: ignore[method-assign]

        await ctx.restart_background_tasks(app)
        assert len(captured_tasks) == 1
        await asyncio.wait_for(captured_tasks[0], timeout=0.5)
        assert captured_tasks[0].exception() is None

    @pytest.mark.asyncio
    async def test_put_config_does_not_block_on_speculator(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        import httpx

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config, save_config

        config_path = tmp_path / "config.toml"
        cfg = Config()
        cfg.llm.default_provider = "openai"
        cfg.llm.openai.api_key = "sk-test-openai"
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        class HangingSpeculator:
            async def force_tick(self, *_args: object, **_kwargs: object) -> None:
                await asyncio.sleep(60)

        class FakeSoulEngine:
            _speculator = HangingSpeculator()

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        async def _fake_rebuild(self: RuntimeContext, new_config: Config) -> None:
            self.config = new_config
            self.memory_manager = SimpleNamespace(load_discovery_runtime_state=lambda: {})
            self.runtime_controller = object()
            self.account_sync_service = object()
            self.auto_update_service = object()
            self.soul_engine = FakeSoulEngine()
            self.recommendation_engine = object()

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await asyncio.wait_for(
                client.put("/api/config", json={"language": "zh"}),
                timeout=0.5,
            )

        assert response.status_code == 200
        assert response.json()["reloaded"] is True

    def test_create_app_bootstrap_shares_database_with_memory_manager(
        self,
        monkeypatch,
    ) -> None:
        from types import SimpleNamespace

        import openbiliclaw.api.app as app_module
        import openbiliclaw.bilibili.api as bilibili_api_module
        import openbiliclaw.llm.service as llm_service_module
        import openbiliclaw.memory.manager as memory_module
        import openbiliclaw.storage.database as database_module

        created_databases: list[object] = []
        created_memories: list[object] = []

        class FakeDatabase:
            def __init__(self, path) -> None:
                self.path = path
                self.initialized = 0
                created_databases.append(self)

            def initialize(self) -> None:
                self.initialized += 1

        class FakeMemoryManager:
            def __init__(self, data_path, database=None) -> None:
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
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder
                self.module_overrides = module_overrides
                self.concurrency = concurrency

        class FakeBilibiliClient:
            def __init__(self, *, cookie: str) -> None:
                self.cookie = cookie

        fake_config = SimpleNamespace(
            data_path=Path("/tmp/openbiliclaw-test-data"),
            bilibili=SimpleNamespace(cookie=""),
        )

        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
        monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
        monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")
        monkeypatch.setattr(database_module, "Database", FakeDatabase)
        monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
        monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
        monkeypatch.setattr(bilibili_api_module, "BilibiliAPIClient", FakeBilibiliClient)

        app_module.create_app(
            soul_engine=object(),
            recommendation_engine=object(),
            runtime_controller=object(),
            account_sync_service=object(),
            dialogue=object(),
        )

        assert len(created_databases) == 1
        assert created_databases[0].initialized == 1
        assert len(created_memories) == 1
        assert created_memories[0].initialized == 1
        assert created_memories[0].database is created_databases[0]

    def test_runtime_context_wires_llm_module_overrides(self, tmp_path: Path) -> None:
        from openbiliclaw.api.runtime_context import build_runtime_context
        from openbiliclaw.config import Config

        config = Config(data_dir=str(tmp_path / "data"))
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "llama3"
        config.llm.soul.provider = "ollama"
        config.llm.soul.model = "llama3-soul"
        config.llm.discovery.model = "llama3-discovery"

        ctx = build_runtime_context(config)

        assert ctx.llm_service.module_overrides["soul"].model == "llama3-soul"
        assert ctx.llm_service.module_overrides["discovery"].model == "llama3-discovery"
        assert ctx.soul_engine._llm_service.module_overrides["soul"].provider == "ollama"

    def test_create_app_bootstrap_wires_discovery_concurrency_controller(
        self,
        monkeypatch,
    ) -> None:
        from types import SimpleNamespace

        import openbiliclaw.api.app as app_module
        import openbiliclaw.bilibili.api as bilibili_api_module
        import openbiliclaw.discovery.engine as discovery_engine_module
        import openbiliclaw.discovery.strategies.strategies as strategies_module
        import openbiliclaw.llm.service as llm_service_module
        import openbiliclaw.memory.manager as memory_module
        import openbiliclaw.recommendation.engine as recommendation_module
        import openbiliclaw.runtime.account_sync as account_sync_module
        import openbiliclaw.runtime.events as runtime_events_module
        import openbiliclaw.runtime.refresh as refresh_module
        import openbiliclaw.soul.dialogue as dialogue_module
        import openbiliclaw.soul.engine as soul_engine_module
        import openbiliclaw.storage.database as database_module

        captured: dict[str, object] = {}

        class FakeDiscoveryConcurrencyController:
            def __init__(
                self,
                *,
                bilibili_request_concurrency: int,
                llm_evaluation_concurrency: int,
            ) -> None:
                captured["controller"] = self
                captured["bilibili_request_concurrency"] = bilibili_request_concurrency
                captured["llm_evaluation_concurrency"] = llm_evaluation_concurrency

        class FakeContentDiscoveryEngine:
            def __init__(
                self,
                *,
                llm_service: object,
                database: object,
                concurrency=None,
                embedding_service=None,
            ) -> None:
                captured["engine_concurrency"] = concurrency

            def register_strategy(self, strategy: object) -> None:
                return None

            def register_adapter(self, adapter: object) -> None:
                return None

        class _FakeStrategy:
            def __init__(self, *args, concurrency=None, **kwargs) -> None:
                captured.setdefault("strategy_concurrency", []).append(concurrency)

        class FakeDatabase:
            def __init__(self, path) -> None:
                self.path = path

            def initialize(self) -> None:
                return None

        class FakeMemoryManager:
            def __init__(self, data_path, database=None) -> None:
                self.data_path = data_path
                self.database = database

            def initialize(self) -> None:
                return None

        class FakeLLMService:
            def __init__(
                self,
                *,
                registry: object,
                memory: object,
                usage_recorder: object | None = None,
                module_overrides: object | None = None,
                concurrency: int = 1,
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder
                self.module_overrides = module_overrides
                self.concurrency = concurrency

        class FakeBilibiliClient:
            def __init__(self, *, cookie: str) -> None:
                self.cookie = cookie

        class FakeSoulEngine:
            def __init__(
                self,
                *,
                llm: object,
                memory: object,
                usage_recorder: object = None,
                **_extras: object,
            ) -> None:
                self.llm = llm
                self.memory = memory
                self.usage_recorder = usage_recorder
                captured["soul_engine_kwargs"] = _extras

        class FakeRecommendationEngine:
            def __init__(
                self,
                *,
                llm: object,
                database: object,
                curator: object = None,
                embedding_service: object = None,
                task_registry: object = None,
            ) -> None:
                self.llm = llm
                self.database = database
                self.task_registry = task_registry

        class FakeRuntimeController:
            def __init__(self, **kwargs) -> None:
                captured["runtime_controller_kwargs"] = kwargs

        class FakeAccountSyncService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                captured["account_sync_kwargs"] = kwargs

        class FakeRuntimeEventHub:
            pass

        class FakeDialogue:
            def __init__(
                self,
                *,
                llm: object | None = None,
                soul_engine: object,
                llm_service: object | None = None,
                session: str,
                tools: object | None = None,
                tool_dispatcher: object | None = None,
            ) -> None:
                self.llm = llm
                self.soul_engine = soul_engine
                self.llm_service = llm_service
                self.session = session

        fake_config = SimpleNamespace(
            data_path=Path("/tmp/openbiliclaw-test-data"),
            bilibili=SimpleNamespace(cookie="", browser_executable="", browser_headed=False),
            llm=SimpleNamespace(concurrency=3),
            sources=SimpleNamespace(
                browser_cdp_url="",
                browser_headed=False,
                xiaohongshu=SimpleNamespace(
                    daily_search_budget=20,
                    daily_creator_budget=10,
                    task_interval_seconds=45,
                ),
            ),
            scheduler=SimpleNamespace(
                enabled=True,
                pause_on_extension_disconnect=False,
                pool_target_count=300,
                account_sync_interval_hours=24,
                refresh_check_interval_seconds=77,
                signal_event_threshold=9,
                trending_refresh_hours=5,
                explore_refresh_hours=18,
                discovery_limit=17,
                proactive_push_interval_seconds=155,
                speculation_interval_minutes=22,
                speculation_ttl_days=8,
                speculation_cooldown_days=9,
                speculation_confirmation_threshold=4,
                speculation_max_active=6,
                speculation_max_primary_interests=17,
                speculation_max_secondary_interests=66,
                speculator_idle_interval_minutes=11,
            ),
        )

        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)
        monkeypatch.setattr("openbiliclaw.llm.build_llm_registry", lambda config: "registry")
        monkeypatch.setattr("openbiliclaw.bilibili.auth.resolve_runtime_cookie", lambda **_: "")
        monkeypatch.setattr(
            discovery_engine_module,
            "DiscoveryConcurrencyController",
            FakeDiscoveryConcurrencyController,
        )
        monkeypatch.setattr(
            discovery_engine_module,
            "ContentDiscoveryEngine",
            FakeContentDiscoveryEngine,
        )
        monkeypatch.setattr(strategies_module, "SearchStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "TrendingStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "RelatedChainStrategy", _FakeStrategy)
        monkeypatch.setattr(strategies_module, "ExploreStrategy", _FakeStrategy)
        monkeypatch.setattr(database_module, "Database", FakeDatabase)
        monkeypatch.setattr(memory_module, "MemoryManager", FakeMemoryManager)
        monkeypatch.setattr(llm_service_module, "LLMService", FakeLLMService)
        monkeypatch.setattr(bilibili_api_module, "BilibiliAPIClient", FakeBilibiliClient)
        monkeypatch.setattr(soul_engine_module, "SoulEngine", FakeSoulEngine)
        monkeypatch.setattr(recommendation_module, "RecommendationEngine", FakeRecommendationEngine)
        monkeypatch.setattr(refresh_module, "ContinuousRefreshController", FakeRuntimeController)
        monkeypatch.setattr(account_sync_module, "AccountSyncService", FakeAccountSyncService)
        monkeypatch.setattr(runtime_events_module, "RuntimeEventHub", FakeRuntimeEventHub)
        monkeypatch.setattr(dialogue_module, "SocraticDialogue", FakeDialogue)

        app = app_module.create_app()

        assert captured["bilibili_request_concurrency"] == 2
        assert captured["llm_evaluation_concurrency"] == 2
        assert captured["engine_concurrency"] is captured["controller"]
        assert all(item is captured["controller"] for item in captured["strategy_concurrency"])
        assert captured["runtime_controller_kwargs"]["scheduler_config"] is fake_config.scheduler
        assert (
            captured["runtime_controller_kwargs"]["presence"] is app.state.runtime_context.presence
        )
        assert captured["runtime_controller_kwargs"]["check_interval_seconds"] == 77
        assert captured["runtime_controller_kwargs"]["signal_event_threshold"] == 9
        assert captured["runtime_controller_kwargs"]["trending_refresh_hours"] == 5
        assert captured["runtime_controller_kwargs"]["explore_refresh_hours"] == 18
        assert captured["runtime_controller_kwargs"]["discovery_limit"] == 17
        assert captured["runtime_controller_kwargs"]["proactive_push_interval_seconds"] == 155
        assert captured["soul_engine_kwargs"]["speculation_interval_minutes"] == 22
        assert captured["soul_engine_kwargs"]["speculation_ttl_days"] == 8
        assert captured["soul_engine_kwargs"]["speculation_cooldown_days"] == 9
        assert captured["soul_engine_kwargs"]["speculation_confirmation_threshold"] == 4
        assert captured["soul_engine_kwargs"]["speculation_max_active"] == 6
        assert captured["soul_engine_kwargs"]["speculation_max_primary_interests"] == 17
        assert captured["soul_engine_kwargs"]["speculation_max_secondary_interests"] == 66
        assert captured["soul_engine_kwargs"]["speculator_idle_interval_minutes"] == 11
        assert callable(captured["account_sync_kwargs"]["llm_work_allowed"])

    def test_cap_by_franchise_keeps_at_most_n_per_franchise(self) -> None:
        """Regression for the 'one popup full of 原神' bug. The API
        layer caps how many same-``franchise_key`` items reach the
        client. franchise_key is the LLM-tagged IP column on
        content_cache (NOT a heuristic from titles).

        Items with empty franchise_key (general-interest content) must
        always pass through — the cap only fires for tagged IPs.
        """
        from openbiliclaw.api.app import _cap_by_franchise

        rows = [
            {"id": 1, "title": "原神 4.0 须弥探索", "franchise_key": "原神"},
            {"id": 2, "title": "提瓦特 摄影集锦", "franchise_key": "原神"},
            {"id": 3, "title": "番茄炒蛋 5 分钟教程", "franchise_key": ""},
            {"id": 4, "title": "蒙德角色真实化", "franchise_key": "原神"},
            {"id": 5, "title": "塞尔达 王国之泪", "franchise_key": "塞尔达传说"},
            {"id": 6, "title": "枫丹海域旅拍", "franchise_key": "原神"},
            {"id": 7, "title": "原神 AI 重制 2024", "franchise_key": "原神"},
        ]
        out = _cap_by_franchise(rows, max_per_franchise=2)
        # First two 原神 rows survive (id=1, 2); subsequent ones drop.
        # 番茄炒蛋 has empty franchise_key so it always passes.
        # 塞尔达 is a different franchise, also passes.
        assert [r["id"] for r in out] == [1, 2, 3, 5]

    def test_cap_by_franchise_zero_disables_cap(self) -> None:
        """max_per_franchise=0 is the escape hatch for ops who want to
        debug without re-deploying. Returns input unchanged."""
        from openbiliclaw.api.app import _cap_by_franchise

        rows = [
            {"id": 1, "franchise_key": "原神"},
            {"id": 2, "franchise_key": "原神"},
            {"id": 3, "franchise_key": "原神"},
        ]
        out = _cap_by_franchise(rows, max_per_franchise=0)
        assert len(out) == 3

    def test_health_endpoint_returns_ok(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "openbiliclaw-api"

    def test_favicon_endpoint_serves_mobile_web_icon(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/favicon.ico")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.content

    def test_health_endpoint_reports_profile_ready_when_available(self) -> None:
        from fastapi.testclient import TestClient

        class ReadySoulEngine:
            def is_profile_ready(self) -> bool:
                return True

        app = create_app(memory_manager=object(), database=object(), soul_engine=ReadySoulEngine())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "openbiliclaw-api"
        assert body["profile_ready"] is True

    def test_detect_lan_ip_prefers_rfc1918_interface_over_benchmark_tun(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.api import app as app_module

        monkeypatch.setattr(app_module, "_default_route_ip", lambda: "198.18.0.1")
        monkeypatch.setattr(
            app_module,
            "_interface_ipv4_candidates",
            lambda: ["198.18.0.1", "192.168.31.98"],
        )

        assert app_module._detect_lan_ip() == "192.168.31.98"

    def test_bilibili_cookie_endpoint_persists_and_validates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The extension's cookie-sync endpoint must:

        1. Validate the incoming cookie against B 站 nav (not blindly trust).
        2. Persist to data/bilibili_cookie.json AND config.toml [bilibili].cookie.
        3. Reject the request when validation fails (don't clobber a working cookie).

        Uses the real AuthManager but stubs the API client factory so
        we never actually hit api.bilibili.com.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        # Sandboxed config + data dir; OPENBILICLAW_PROJECT_ROOT redirects
        # config.toml + data/ to tmp_path so the test can't touch the
        # developer's real config.
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        # Fake B 站 nav: returns a logged-in response so validation passes.
        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        # Patch the auth-manager default client factory globally — the
        # endpoint constructs its own AuthManager so we can't pass
        # the factory through; we monkeypatch the staticmethod instead.
        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        cookie_value = "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999"
        response = client.post(
            "/api/bilibili/cookie",
            json={
                "cookie": cookie_value,
                "source": "extension",
                "validate_with_bilibili": True,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["authenticated"] is True
        assert body["username"] == "test_user"
        assert body["user_id"] == 12345

        # Side effect 1: data/bilibili_cookie.json got written.
        cookie_file = tmp_path / "data" / "bilibili_cookie.json"
        assert cookie_file.exists()
        import json

        assert json.loads(cookie_file.read_text())["cookie"] == cookie_value

        # Side effect 2: config.toml [bilibili].cookie mirrors the cookie.
        config_text = (tmp_path / "config.toml").read_text()
        assert cookie_value in config_text

    def test_bilibili_cookie_sync_restarts_background_tasks_after_rebuild(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Cookie hot-reload must restart refresh loops after cancelling them.

        ``RuntimeContext.rebuild_from_config`` cancels tracked background tasks
        before replacing runtime components.  The cookie endpoint therefore
        must call ``restart_background_tasks`` too, otherwise the refresh loop
        that drives XHS / Douyin producers stays stopped after cookie sync.
        """
        from fastapi.testclient import TestClient

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        calls: list[str] = []

        async def _fake_rebuild(self: RuntimeContext, config: object) -> None:
            calls.append("rebuild")

        async def _fake_restart(self: RuntimeContext, app: object) -> None:
            calls.append("restart")

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )
        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _fake_restart)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        with TestClient(app) as client:
            calls.clear()
            response = client.post(
                "/api/bilibili/cookie",
                json={
                    "cookie": "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999",
                    "source": "extension",
                    "validate_with_bilibili": True,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert calls == ["rebuild", "restart"]

    def test_bilibili_cookie_sync_skips_hot_reload_when_cookie_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Repeated extension sync for the same cookie must be idempotent."""
        from fastapi.testclient import TestClient

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        cookie_value = "SESSDATA=abc123; bili_jct=def456; DedeUserID=99999"
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        config = Config()
        config.bilibili.cookie = cookie_value
        save_config(config, tmp_path / "config.toml")
        AuthManager(tmp_path / "data").set_cookie(cookie_value)

        class _FakeNav:
            is_login = True
            uname = "test_user"
            mid = 12345

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                self.cookie = cookie

            async def get_nav_info(self) -> _FakeNav:
                return _FakeNav()

            async def close(self) -> None:
                pass

        calls: list[str] = []

        async def _fake_rebuild(self: RuntimeContext, config: object) -> None:
            calls.append("rebuild")

        async def _fake_restart(self: RuntimeContext, app: object) -> None:
            calls.append("restart")

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )
        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        monkeypatch.setattr(RuntimeContext, "restart_background_tasks", _fake_restart)

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        with TestClient(app) as client:
            calls.clear()
            response = client.post(
                "/api/bilibili/cookie",
                json={
                    "cookie": cookie_value,
                    "source": "extension",
                    "validate_with_bilibili": True,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True
        assert calls == []

    def test_bilibili_cookie_endpoint_rejects_invalid_cookie(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When B 站 nav says the cookie isn't logged in, do NOT persist."""
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        class _FakeNavLoggedOut:
            is_login = False
            uname = ""
            mid = 0

        class _FakeClient:
            def __init__(self, cookie: str) -> None:
                pass

            async def get_nav_info(self) -> _FakeNavLoggedOut:
                return _FakeNavLoggedOut()

            async def close(self) -> None:
                pass

        monkeypatch.setattr(
            AuthManager,
            "_default_api_client_factory",
            staticmethod(lambda cookie: _FakeClient(cookie)),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/bilibili/cookie",
            json={
                "cookie": "SESSDATA=expired; bili_jct=stale",
                "validate_with_bilibili": True,
            },
        )

        body = response.json()
        assert body["ok"] is False
        assert body["authenticated"] is False
        # No file written (because validation failed before persistence).
        assert not (tmp_path / "data" / "bilibili_cookie.json").exists()

    def test_douyin_cookie_endpoint_persists_cookie_without_config_mirror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import json

        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        cookie_value = "msToken=abc; ttwid=tw; sessionid=sess"
        response = client.post(
            "/api/sources/dy/cookie",
            json={"cookie": cookie_value, "source": "extension"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["has_cookie"] is True
        assert body["cookie_names"] == ["msToken", "sessionid", "ttwid"]

        cookie_file = tmp_path / "data" / "douyin_cookie.json"
        assert cookie_file.exists()
        payload = json.loads(cookie_file.read_text(encoding="utf-8"))
        assert payload["cookie"] == cookie_value
        assert payload["source"] == "extension"
        assert cookie_value not in (tmp_path / "config.toml").read_text(encoding="utf-8")

    def test_events_endpoint_persists_batch(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory)
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BV1TEST",
                        "title": "测试标题",
                        "timestamp": 1710000000000,
                        "context": {"pageType": "video"},
                        "metadata": {"href": "https://www.bilibili.com/video/BV1TEST"},
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 1
        assert memory.events[0]["event_type"] == "click"
        assert memory.events[0]["url"] == "https://www.bilibili.com/video/BV1TEST"
        assert memory.events[0]["metadata"]["timestamp"] == 1710000000000
        # Legacy payload without source_platform defaults to bilibili so the
        # existing extension build keeps working across the upgrade.
        assert memory.events[0]["metadata"]["source_platform"] == "bilibili"

    def test_events_endpoint_preserves_source_platform(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory)
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "type": "click",
                        "url": "https://www.xiaohongshu.com/explore/69dea966000000001a0280ad",
                        "title": "测试笔记",
                        "timestamp": 1710000000000,
                        "source_platform": "xiaohongshu",
                        "context": {"pageType": "note"},
                        "metadata": {"note_id": "69dea966000000001a0280ad"},
                    },
                    {
                        "type": "scroll",
                        "url": "https://www.xiaohongshu.com/explore",
                        "title": "",
                        "timestamp": 1710000000001,
                        "source_platform": "   ",
                        "context": {"pageType": "home"},
                        "metadata": {},
                    },
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["accepted"] == 2
        assert memory.events[0]["metadata"]["source_platform"] == "xiaohongshu"
        assert memory.events[0]["metadata"]["note_id"] == "69dea966000000001a0280ad"
        # Blank source_platform (whitespace only) falls back to bilibili.
        assert memory.events[1]["metadata"]["source_platform"] == "bilibili"

    def test_events_endpoint_preserves_top_level_dwell_fields(self) -> None:
        """v0.3.x event-satisfaction: top-level watch_seconds /
        video_duration_seconds get folded into metadata so the storage
        classifier sees them."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory)
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BVquick",
                        "title": "标题党",
                        "timestamp": 1710000000000,
                        "watch_seconds": 2,
                        "video_duration_seconds": 120,
                    }
                ]
            },
        )

        assert response.status_code == 200
        ev = memory.events[0]
        assert ev["metadata"]["watch_seconds"] == 2
        assert ev["metadata"]["video_duration_seconds"] == 120

    def test_events_endpoint_preserves_metadata_dwell_fields(self) -> None:
        """Same fields also accepted when the extension nests them in metadata."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory)
        client = TestClient(app)

        response = client.post(
            "/api/events",
            json={
                "events": [
                    {
                        "type": "click",
                        "url": "https://www.bilibili.com/video/BVdeep",
                        "title": "深度教程",
                        "timestamp": 1710000000000,
                        "metadata": {"watch_seconds": 600, "video_duration_seconds": 700},
                    }
                ]
            },
        )

        assert response.status_code == 200
        ev = memory.events[0]
        assert ev["metadata"]["watch_seconds"] == 600
        assert ev["metadata"]["video_duration_seconds"] == 700

    def test_events_endpoint_handles_extension_cors_preflight(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.options(
            "/api/events",
            headers={
                "Origin": "chrome-extension://alolnnalhpddolgelnhfkmmiehhcmokl",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
        assert "POST" in response.headers["access-control-allow-methods"]

    def test_recommendations_endpoint_returns_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(self, limit: int = 20) -> list[dict[str, object]]:
                # v0.3.18: the endpoint pulls 2x the visible window so
                # the per-franchise cap still has 20 survivors after
                # dropping over-represented IPs.
                assert limit == 40
                return [
                    {
                        "id": 7,
                        "bvid": "BV1REC",
                        "title": "讲透城市与建筑",
                        "up_name": "城市观察局",
                        "cover_url": "https://i0.hdslb.com/bfs/archive/cover.jpg",
                        "expression": "这条很对你最近的状态。",
                        "topic": "你最近那股想把结构想透的劲头",
                        "presented": 1,
                        "franchise_key": "",  # general-interest content
                    }
                ]

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == 7
        assert data["items"][0]["title"] == "讲透城市与建筑"
        assert data["items"][0]["cover_url"] == "https://i0.hdslb.com/bfs/archive/cover.jpg"

    def test_recommendations_endpoint_caps_same_franchise(self) -> None:
        """End-to-end: when the DB returns 5 同 IP rows in the
        franchise_key column, the API trims down to ``max_per_franchise=2``
        before serving."""
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(self, limit: int = 20) -> list[dict[str, object]]:
                # Five 原神 rows + one 番茄炒蛋. Without the franchise
                # cap, the response would carry all 5 原神; with cap=2,
                # only 2 survive.
                base: list[dict[str, object]] = []
                for i in range(5):
                    base.append(
                        {
                            "id": i,
                            "bvid": f"BV原神{i}",
                            "title": f"原神 番外 {i}",
                            "up_name": "某 UP",
                            "cover_url": "",
                            "expression": "",
                            "topic": "游戏",
                            "presented": 0,
                            "franchise_key": "原神",
                        }
                    )
                base.append(
                    {
                        "id": 99,
                        "bvid": "BV番茄",
                        "title": "番茄炒蛋 5 分钟",
                        "up_name": "美食 UP",
                        "cover_url": "",
                        "expression": "",
                        "topic": "美食",
                        "presented": 0,
                        "franchise_key": "",
                    }
                )
                return base

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")
        assert response.status_code == 200
        items = response.json()["items"]
        franchise_count = sum(1 for it in items if str(it["title"]).startswith("原神"))
        # 5 同 IP 行被砍到 2，番茄炒蛋（无 franchise）仍保留
        assert franchise_count == 2
        assert any(it["title"].startswith("番茄炒蛋") for it in items)

    def test_runtime_status_endpoint_returns_runtime_summary(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 3,
                    "last_refresh_at": "2026-03-10T12:00:00",
                    "last_notification_at": "2026-03-10T12:30:00",
                    "unread_count": 2,
                    "pool_available_count": 28,
                    "pool_target_count": 30,
                    "last_discovered_count": 14,
                    "last_replenished_count": 6,
                    "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
                }

        class FakeAccountSyncService:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "last_account_sync_at": "2026-03-14T18:00:00+00:00",
                    "last_account_sync_error": "",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
            account_sync_service=FakeAccountSyncService(),
        )
        client = TestClient(app)

        response = client.get("/api/runtime-status")

        assert response.status_code == 200
        assert response.json() == {
            "initialized": True,
            "recommendation_count": 5,
            "pending_signal_events": 3,
            "last_refresh_at": "2026-03-10T12:00:00",
            "last_notification_at": "2026-03-10T12:30:00",
            "unread_count": 2,
            "pool_available_count": 28,
            "pool_target_count": 30,
            "last_discovered_count": 14,
            "last_replenished_count": 6,
            "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
            "manual_refresh_state": "idle",
            "manual_refresh_message": "",
            "last_account_sync_at": "2026-03-14T18:00:00+00:00",
            "last_account_sync_error": "",
        }

    def test_runtime_stream_websocket_receives_published_events(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream") as websocket:
            asyncio.run(hub.publish({"type": "refresh.started", "message": "开始给你补候选了"}))
            assert websocket.receive_json() == {
                "type": "refresh.started",
                "message": "开始给你补候选了",
            }

    def test_runtime_stream_websocket_updates_shared_presence(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream"):
            _wait_for_presence_count(ctx, 1)

        _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_websocket_keeps_presence_for_second_client(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream"):
            _wait_for_presence_count(ctx, 1)
            with client.websocket_connect("/api/runtime-stream"):
                _wait_for_presence_count(ctx, 2)
            _wait_for_presence_count(ctx, 1)
            assert ctx.presence.is_present(grace_seconds=1) is True

        _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_idle_disconnect_decrements_presence_promptly(self) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)
        ctx = app.state.runtime_context

        with client.websocket_connect("/api/runtime-stream") as websocket:
            _wait_for_presence_count(ctx, 1)
            websocket.close()
            _wait_for_presence_count(ctx, 0)

    def test_runtime_stream_requests_cookie_sync_for_background_client(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config
        from openbiliclaw.runtime.events import RuntimeEventHub

        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        save_config(Config(), tmp_path / "config.toml")

        hub = RuntimeEventHub()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream?client=background") as websocket:
            assert websocket.receive_json() == {
                "type": "bilibili_cookie_sync_requested",
                "reason": "missing_cookie",
                "source": "runtime-stream",
            }

    def test_activity_feed_endpoint_returns_live_summary_headline_and_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendations(self, limit: int = 20) -> list[dict[str, object]]:
                assert limit in {10, 20}
                return [
                    {
                        "id": 7,
                        "title": "讲透贸易逆差",
                        "topic": "你最近还挺想把因果链理顺",
                        "expression": "这条会对上你最近那股想把事情想透的劲头。",
                        "created_at": "2026-03-15T10:00:00+08:00",
                        "feedback_type": "comment",
                        "feedback_note": "想看更深一点的。",
                        "feedback_at": "2026-03-15T10:05:00+08:00",
                    }
                ]

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 刚记下了：你最近更吃把因果链讲透的内容。",
                        "created_at": "2026-03-15T10:10:00+08:00",
                    }
                ]

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 2,
                    "last_refresh_at": "2026-03-15T10:06:00+08:00",
                    "last_notification_at": "",
                    "unread_count": 1,
                    "pool_available_count": 42,
                    "pool_target_count": 30,
                    "last_replenished_count": 6,
                    "recent_pool_topics": ["国际时事", "宏观经济"],
                    "manual_refresh_state": "running",
                    "manual_refresh_message": "正在给你补候选…",
                }

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.get("/api/activity-feed")

        assert response.status_code == 200
        data = response.json()
        assert data["live_summary"] == "正在给你补候选…"
        assert data["headline"] == "阿B 刚记下了：你最近更吃把因果链讲透的内容。"
        assert data["items"][0]["kind"] == "interest_added"
        assert any(item["kind"] == "feedback" for item in data["items"])
        assert any(item["kind"] == "pool_update" for item in data["items"])

    def test_refresh_recommendations_endpoint_triggers_runtime_refresh(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            async def trigger_manual_refresh(self) -> dict[str, object]:
                return {
                    "accepted": True,
                    "state": "running",
                    "reason": "started",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": True,
            "state": "running",
            "reason": "started",
        }

    def test_refresh_recommendations_endpoint_reports_uninitialized_runtime(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            async def trigger_manual_refresh(self) -> dict[str, object]:
                return {
                    "accepted": False,
                    "state": "idle",
                    "reason": "not_initialized",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": False,
            "state": "idle",
            "reason": "not_initialized",
        }

    def test_refresh_recommendations_endpoint_uses_force_refresh(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.called: list[str] = []

            async def refresh_if_needed(self) -> dict[str, object]:
                self.called.append("normal")
                return {
                    "refreshed": False,
                    "strategies": [],
                    "reason": "below_threshold",
                    "recommendation_count": 0,
                }

            async def trigger_manual_refresh(self) -> dict[str, object]:
                self.called.append("trigger")
                return {
                    "accepted": True,
                    "state": "running",
                    "reason": "started",
                }

        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/refresh")

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "accepted": True,
            "state": "running",
            "reason": "started",
        }
        assert runtime.called == ["trigger"]

    def test_reshuffle_recommendations_endpoint_returns_immediate_items(self) -> None:
        from fastapi.testclient import TestClient

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            async def reshuffle_recommendations(
                self,
                *,
                profile: object,
                limit: int = 10,
            ) -> list[object]:
                assert profile == {"profile": "ok"}
                assert limit == 10
                from openbiliclaw.discovery.engine import DiscoveredContent
                from openbiliclaw.recommendation.engine import Recommendation

                return [
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV1NEW",
                            title="新的一批",
                            up_name="UPA",
                            cover_url="https://i0.hdslb.com/bfs/archive/new-cover.jpg",
                        ),
                        recommendation_id=11,
                        expression="先给你捞一条新的。",
                        topic_label="刚补进来的新东西",
                        confidence=0.88,
                        presented=False,
                    )
                ]

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=FakeRecommendationEngine(),
        )
        client = TestClient(app)

        response = client.post("/api/recommendations/reshuffle")

        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {
                    "id": 11,
                    "bvid": "BV1NEW",
                    "title": "新的一批",
                    "up_name": "UPA",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/new-cover.jpg",
                    "expression": "先给你捞一条新的。",
                    "topic_label": "刚补进来的新东西",
                    "presented": False,
                    "feedback_type": "",
                    "content_id": "BV1NEW",
                    "content_url": "https://www.bilibili.com/video/BV1NEW",
                    "source_platform": "bilibili",
                }
            ]
        }

    def test_append_recommendations_endpoint_excludes_existing_bvids(self) -> None:
        from fastapi.testclient import TestClient

        class FakeSoulEngine:
            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.calls: list[tuple[object, list[str], int]] = []

            async def append_recommendations(
                self,
                *,
                profile: object,
                excluded_bvids: list[str],
                limit: int = 10,
            ) -> list[object]:
                self.calls.append((profile, excluded_bvids, limit))
                from openbiliclaw.discovery.engine import DiscoveredContent
                from openbiliclaw.recommendation.engine import Recommendation

                return [
                    Recommendation(
                        content=DiscoveredContent(
                            bvid="BV1NEXT",
                            title="下一批 1",
                            up_name="UPB",
                            cover_url="https://i0.hdslb.com/bfs/archive/next-cover.jpg",
                        ),
                        recommendation_id=22,
                        expression="这条接在你刚刚看的后面也顺。",
                        topic_label="下一条",
                        confidence=0.81,
                        presented=False,
                    )
                ]

        recommendation_engine = FakeRecommendationEngine()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=recommendation_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendations/append",
            json={"excluded_bvids": ["BV1A", "BV1B"]},
        )

        assert response.status_code == 200
        assert recommendation_engine.calls == [({"profile": "ok"}, ["BV1A", "BV1B"], 10)]
        assert response.json() == {
            "items": [
                {
                    "id": 22,
                    "bvid": "BV1NEXT",
                    "title": "下一批 1",
                    "up_name": "UPB",
                    "cover_url": "https://i0.hdslb.com/bfs/archive/next-cover.jpg",
                    "expression": "这条接在你刚刚看的后面也顺。",
                    "topic_label": "下一条",
                    "presented": False,
                    "feedback_type": "",
                    "content_id": "BV1NEXT",
                    "content_url": "https://www.bilibili.com/video/BV1NEXT",
                    "source_platform": "bilibili",
                }
            ]
        }

    def test_pending_notification_endpoint_returns_single_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_notification_candidate(
                self, *, min_confidence: float = 0.82
            ) -> dict[str, object] | None:
                assert min_confidence == 0.82
                return {
                    "id": 9,
                    "bvid": "BV1PENDING",
                    "title": "新的高置信推荐",
                    "expression": "这条很对你现在的口味。",
                }

        app = create_app(memory_manager=object(), database=FakeDatabase(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/notifications/pending")

        assert response.status_code == 200
        assert response.json() == {
            "item": {
                "recommendation_id": 9,
                "bvid": "BV1PENDING",
                "title": "新的高置信推荐",
                "reason": "这条很对你现在的口味。",
            }
        }

    def test_notification_sent_endpoint_marks_delivery(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.marked: list[str] = []

            def mark_notification_sent(self, bvid: str) -> None:
                self.marked.append(bvid)

        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=runtime,
        )
        client = TestClient(app)

        response = client.post("/api/notifications/sent", json={"bvid": "BV1ACK"})

        assert response.status_code == 200
        assert response.json() == {"ok": True, "bvid": "BV1ACK"}
        assert runtime.marked == ["BV1ACK"]

    def test_feedback_endpoint_updates_recommendation_and_records_event(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def __init__(self) -> None:
                self.updated: list[tuple[int, str, str]] = []

            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                if recommendation_id != 7:
                    return None
                return {"id": 7, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                self.updated.append((recommendation_id, feedback_type, feedback_note))

        memory = FakeMemoryManager()
        database = FakeDatabase()
        app = create_app(memory_manager=memory, database=database)
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "这条确实对胃口",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "recommendation_id": 7,
            "feedback_type": "like",
        }
        assert database.updated == [(7, "like", "这条确实对胃口")]
        assert memory.events[0]["event_type"] == "feedback"
        assert memory.events[0]["metadata"]["recommendation_id"] == 7
        assert memory.events[0]["metadata"]["feedback_type"] == "like"

    def test_feedback_endpoint_rejects_comment_without_note(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "comment",
                "note": "",
            },
        )

        assert response.status_code == 422

    def test_feedback_endpoint_reports_missing_recommendation(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return None

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "dislike",
                "note": "太浅了",
            },
        )

        assert response.status_code == 404

    def test_feedback_endpoint_triggers_profile_refresh_check(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                return None

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.called = False
                self.immediate_calls: list[tuple[str, str, str]] = []

            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str = "",
            ) -> None:
                self.immediate_calls.append((feedback_type, title, note))

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                self.called = True
                return {"triggered": False}

        fake_soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=fake_soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
            },
        )

        assert response.status_code == 200
        assert fake_soul_engine.called is True
        assert fake_soul_engine.immediate_calls == [("like", "讲透城市与建筑", "")]

    def test_feedback_endpoint_does_not_block_on_post_feedback_refresh(self) -> None:
        import time

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                return None

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "讲透城市与建筑"}

            def update_recommendation_feedback(
                self,
                recommendation_id: int,
                *,
                feedback_type: str,
                feedback_note: str = "",
            ) -> None:
                return None

        class SlowSoulEngine:
            def record_immediate_feedback_cognition(
                self,
                *,
                feedback_type: str,
                title: str,
                note: str = "",
            ) -> None:
                return None

            async def process_feedback_batch_if_needed(self) -> dict[str, object]:
                await asyncio.sleep(0.2)
                return {"triggered": False}

        class SlowRuntimeController:
            async def refresh_after_feedback(self) -> dict[str, object]:
                await asyncio.sleep(0.2)
                return {"refreshed": False}

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=SlowSoulEngine(),
            runtime_controller=SlowRuntimeController(),
        )
        client = TestClient(app)

        started_at = time.perf_counter()
        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "like",
                "note": "",
            },
        )
        elapsed = time.perf_counter() - started_at

        assert response.status_code == 200
        assert elapsed < 0.15

    def test_profile_summary_endpoint_returns_initialized_profile(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-2",
                        "kind": "profile_shift",
                        "summary": "我对你又对上了一点：你不是只看热闹的人。",
                        "notified": True,
                    },
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "context_line": "基于最近内容：《中东局势深拆》 / 《国际秩序观察》",
                        "impact": "画像里\u201c国际新闻 / 深度分析\u201d这条偏好会更靠前。",
                        "reasoning": "这更像是连续强化后的稳定兴趣，不只是一次随手点开。",
                        "evidence": "因为你最近连续点开相关内容，还主动提到了国际时事。",
                        "source": "chat",
                        "source_label": "聊天",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-14T22:30:00",
                        "notified": False,
                    },
                ]

        class FakeProfile:
            personality_portrait = "这是一个喜欢把问题想透、信息密度偏高的用户。"
            core_traits = ["理性", "好奇", "克制", "耐心", "敏感", "深究", "自驱"]
            cognitive_style = ["会先看结构", "对证据比较敏感", "偏好把问题讲透", "不太吃空话"]
            motivational_drivers = ["建立判断确定性", "持续扩展理解边界", "在复杂信息里找到秩序感"]
            current_phase = "最近更像在一边吸收高密度信息，一边整理自己的判断框架。"
            deep_needs = ["理解世界", "持续成长", "高质量独处", "智性共鸣", "掌控感", "审美沉浸"]
            values = ["独立思考", "真实", "深度"]
            life_stage = "职业上升期，开始关注更宏观的议题。"
            preferences = type(
                "Preferences",
                (),
                {
                    "interests": [
                        type("Interest", (), {"name": "国际新闻"})(),
                        type("Interest", (), {"name": "深度分析"})(),
                        type("Interest", (), {"name": "工业设计"})(),
                        type("Interest", (), {"name": "城市观察"})(),
                        type("Interest", (), {"name": "纪录片"})(),
                        type("Interest", (), {"name": "商业案例"})(),
                        type("Interest", (), {"name": "复杂系统"})(),
                        type("Interest", (), {"name": "技术史"})(),
                        type("Interest", (), {"name": "冷知识考据"})(),
                    ],
                    "disliked_topics": [
                        "标题党",
                        "浅层热点复读",
                        "尬笑段子",
                        "纯情绪输出",
                        "过度说教",
                        "工业糖精",
                    ],
                    "favorite_up_users": ["经济观察", "构图实验室"],
                    "exploration_openness": 0.72,
                },
            )()

        class FakeSoulEngine:
            async def get_profile(self) -> FakeProfile:
                return FakeProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=FakeMemoryManager(),
            database=object(),
        )
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        data = response.json()
        assert data["initialized"] is True
        assert data["personality_portrait"] == "这是一个喜欢把问题想透、信息密度偏高的用户。"
        assert data["core_traits"] == ["理性", "好奇", "克制", "耐心", "敏感", "深究"]
        assert data["deep_needs"] == ["理解世界", "持续成长", "高质量独处", "智性共鸣", "掌控感"]
        assert data["values"] == ["独立思考", "真实", "深度"]
        assert data["motivational_drivers"] == [
            "建立判断确定性",
            "持续扩展理解边界",
            "在复杂信息里找到秩序感",
        ]
        assert data["cognitive_style"] == [
            "会先看结构",
            "对证据比较敏感",
            "偏好把问题讲透",
            "不太吃空话",
        ]
        assert data["current_phase"] == "最近更像在一边吸收高密度信息，一边整理自己的判断框架。"
        assert data["life_stage"] == "职业上升期，开始关注更宏观的议题。"
        assert data["favorite_up_users"] == ["经济观察", "构图实验室"]
        assert data["exploration_openness"] == 0.72
        assert data["speculative_interests"] == []
        # mbti, likes, dislikes, style, context come from OnionProfile layers
        # FakeProfile has no OnionProfile.interest or .core.mbti so these are defaults
        assert data["mbti"]["type"] == ""
        assert isinstance(data["likes"], list)
        assert isinstance(data["dislikes"], list)
        assert isinstance(data["style"], dict)
        assert isinstance(data["context"], dict)
        assert len(data["recent_cognition_updates"]) == 2
        assert "summary" in data["recent_cognition_updates"][0]
        assert data["has_more_cognition_updates"] is False
        assert data["next_cognition_cursor"] == ""

    def test_profile_summary_endpoint_paginates_cognition_history(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "第一条更新",
                        "context_line": "来自：《第一条内容》",
                        "impact": "第一条影响",
                        "reasoning": "第一条原因",
                        "evidence": "第一条证据",
                        "source": "feedback",
                        "source_label": "推荐反馈",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-15T09:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-2",
                        "kind": "interest_added",
                        "summary": "第二条更新",
                        "context_line": "来自最近这轮聊天",
                        "impact": "第二条影响",
                        "reasoning": "第二条原因",
                        "evidence": "第二条证据",
                        "source": "chat",
                        "source_label": "聊天",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-15T08:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-3",
                        "kind": "profile_shift",
                        "summary": "第三条更新",
                        "created_at": "2026-03-14T22:00:00",
                        "notified": False,
                    },
                    {
                        "id": "cog-4",
                        "kind": "profile_shift",
                        "summary": "第四条更新",
                        "context_line": "基于最近几条相关内容",
                        "impact": "第四条影响",
                        "reasoning": "第四条原因",
                        "evidence": "第四条证据",
                        "source": "refresh",
                        "expand_hint": "expandable",
                        "created_at": "2026-03-13T21:00:00",
                        "notified": True,
                    },
                ]

        class FakeProfile:
            personality_portrait = "这是一个喜欢把问题想透、信息密度偏高的用户。"
            core_traits = ["理性", "好奇"]
            deep_needs = ["理解世界", "持续成长"]
            preferences = type(
                "Preferences",
                (),
                {
                    "interests": [
                        type("Interest", (), {"name": "国际新闻"})(),
                        type("Interest", (), {"name": "深度分析"})(),
                    ]
                },
            )()

        class FakeSoulEngine:
            async def get_profile(self) -> FakeProfile:
                return FakeProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=FakeMemoryManager(),
            database=object(),
        )
        client = TestClient(app)

        first_page = client.get("/api/profile-summary?limit=3")

        assert first_page.status_code == 200
        assert first_page.json()["recent_cognition_updates"] == [
            {
                "summary": "第一条更新",
                "context_line": "来自：《第一条内容》",
                "impact": "第一条影响",
                "reasoning": "第一条原因",
                "evidence": "第一条证据",
                "source": "feedback",
                "source_label": "推荐反馈",
                "expand_hint": "expandable",
                "created_at": "2026-03-15T09:00:00",
            },
            {
                "summary": "第二条更新",
                "context_line": "来自最近这轮聊天",
                "impact": "第二条影响",
                "reasoning": "第二条原因",
                "evidence": "第二条证据",
                "source": "chat",
                "source_label": "聊天",
                "expand_hint": "expandable",
                "created_at": "2026-03-15T08:00:00",
            },
            {
                "summary": "第三条更新",
                "context_line": "基于最近几条相关内容",
                "impact": "",
                "reasoning": "",
                "evidence": "",
                "source": "",
                "source_label": "",
                "expand_hint": "summary_only",
                "created_at": "2026-03-14T22:00:00",
            },
        ]
        assert first_page.json()["has_more_cognition_updates"] is True
        assert first_page.json()["next_cognition_cursor"] == "3"

        second_page = client.get("/api/profile-summary?limit=3&cursor=3")

        assert second_page.status_code == 200
        assert second_page.json()["recent_cognition_updates"] == [
            {
                "summary": "第四条更新",
                "context_line": "基于最近几条相关内容",
                "impact": "第四条影响",
                "reasoning": "第四条原因",
                "evidence": "第四条证据",
                "source": "refresh",
                "source_label": "",
                "expand_hint": "expandable",
                "created_at": "2026-03-13T21:00:00",
            }
        ]
        assert second_page.json()["has_more_cognition_updates"] is False
        assert second_page.json()["next_cognition_cursor"] == ""

    def test_profile_summary_endpoint_handles_missing_profile(self) -> None:
        from fastapi.testclient import TestClient

        class FakeSoulEngine:
            async def get_profile(self) -> object:
                raise RuntimeError("not initialized")

        app = create_app(soul_engine=FakeSoulEngine(), memory_manager=object(), database=object())
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        assert response.json()["initialized"] is False

    def test_pending_cognition_update_endpoint_returns_latest_unnotified_item(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def load_cognition_updates(self) -> list[dict[str, object]]:
                return [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "confidence": 0.86,
                        "created_at": "2026-03-10T12:00:00",
                        "source": "feedback",
                        "notified": False,
                    },
                    {
                        "id": "cog-2",
                        "kind": "profile_shift",
                        "summary": "我对你又对上了一点：你不是只看热闹的人。",
                        "confidence": 0.9,
                        "created_at": "2026-03-10T11:00:00",
                        "source": "profile_refresh",
                        "notified": True,
                    },
                ]

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=object(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.get("/api/cognition-updates/pending")

        assert response.status_code == 200
        assert response.json() == {
            "item": {
                "id": "cog-1",
                "kind": "interest_added",
                "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
            }
        }

    def test_seen_cognition_update_endpoint_marks_item_notified(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self._updates = [
                    {
                        "id": "cog-1",
                        "kind": "interest_added",
                        "summary": "阿B 现在更确定你会吃国际时事深拆这一口。",
                        "notified": False,
                    }
                ]

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self._updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self._updates = list(updates)

        memory = FakeMemoryManager()
        app = create_app(memory_manager=memory, database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.post("/api/cognition-updates/seen", json={"id": "cog-1"})

        assert response.status_code == 200
        assert response.json() == {"ok": True, "id": "cog-1"}
        assert memory._updates[0]["notified"] is True

    def test_chat_endpoint_returns_dialogue_reply(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(self, user_message: str) -> str:
                assert user_message == "我最近总在看国际新闻"
                return "你更在意的是它背后的逻辑，还是事件本身的冲突感？"

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "我最近总在看国际新闻"})

        assert response.status_code == 200
        assert response.json() == {"reply": "你更在意的是它背后的逻辑，还是事件本身的冲突感？"}

    def test_chat_endpoint_rejects_empty_message(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(self, user_message: str) -> str:
                return user_message

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post("/api/chat", json={"message": "   "})

        assert response.status_code == 422

    def test_interest_probe_reject_records_feedback_history(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "probed_domains": {},
                    "probed_axes": {},
                    "probe_feedback_history": [],
                }
                self.cognition_updates: list[dict[str, object]] = []

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.cognition_updates = list(updates)

        class FakeSpeculator:
            def __init__(self) -> None:
                self.rejected: list[tuple[str, int]] = []
                self._active = [
                    SimpleNamespace(
                        domain="城市漫游路线",
                        category="生活方式",
                        reason="用户最近对城市空间和徒步路线表现出探索意愿。",
                        experience_mode="wander_observe",
                        entry_load="light",
                        specifics=[SimpleNamespace(name="老街路线")],
                    )
                ]

            def get_active_speculations(self) -> list[object]:
                return list(self._active)

            def user_reject_speculation(
                self,
                domain: str,
                cooldown_days: int = 30,
            ) -> bool:
                self.rejected.append((domain, cooldown_days))
                self._active = []
                return True

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={"domain": "城市漫游路线", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "rejected"
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history == [
            {
                "domain": "城市漫游路线",
                "response": "reject",
                "axis": "wander_observe|light",
                "category": "生活方式",
                "reason": "用户最近对城市空间和徒步路线表现出探索意愿。",
                "specifics": ["老街路线"],
                "created_at": history[0]["created_at"],
            }
        ]
        assert soul_engine._speculator.rejected == [("城市漫游路线", 30)]

    def test_chat_turn_endpoint_persists_pending_turn_until_reply(self, tmp_path: Path) -> None:
        import asyncio
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(self, user_message: str) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.05)
                return "你更在意的是它背后的逻辑。"

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            start = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-test-1",
                    "session": "popup",
                    "scope": "chat",
                    "message": "我最近总在看国际新闻",
                },
            )

            assert start.status_code == 200
            assert start.json()["turn_id"] == "turn-test-1"
            assert start.json()["status"] == "pending"

            turn = start.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-test-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["reply"] == "你更在意的是它背后的逻辑。"
            assert dialogue.messages == ["我最近总在看国际新闻"]

            history = client.get("/api/chat/turns", params={"session": "popup"}).json()
            assert history["items"] == [turn]

        # Re-open the app on the same database to simulate a popup/backend
        # client lifecycle boundary: completed turns must be recoverable.
        app2 = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )
        client2 = TestClient(app2)
        restored = client2.get("/api/chat/turns", params={"session": "popup"}).json()

        assert restored["items"][0]["turn_id"] == "turn-test-1"
        assert restored["items"][0]["status"] == "completed"
        assert restored["items"][0]["reply"] == "你更在意的是它背后的逻辑。"

    def test_chat_turn_endpoint_records_delight_scope_context(self, tmp_path: Path) -> None:
        import asyncio
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(self, user_message: str) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.01)
                return "这条像是从另一个角度补上你的问题。"

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        app = create_app(
            memory_manager=object(),
            database=db,
            soul_engine=object(),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-delight-1",
                    "session": "popup",
                    "scope": "delight",
                    "subject_id": "BV1DL",
                    "subject_title": "复杂系统入门",
                    "message": "我想知道它为什么会推荐给我",
                },
            )
            assert response.status_code == 200

            turn = response.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-delight-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["scope"] == "delight"
            assert turn["subject_id"] == "BV1DL"
            assert "关于惊喜推荐「复杂系统入门」的反馈" in dialogue.messages[0]

            delight_history = client.get(
                "/api/chat/turns",
                params={"session": "popup", "scope": "delight"},
            ).json()
            assert [item["turn_id"] for item in delight_history["items"]] == ["turn-delight-1"]

    def test_recommendation_click_endpoint_ingests_strong_signal(self) -> None:
        """POST /api/recommendation-click should push a strong signal through the pipeline."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                if recommendation_id != 99:
                    return None
                return {
                    "id": 99,
                    "bvid": "BV1REC99",
                    "title": "深入理解Transformer",
                    "topic_label": "AI技术",
                    "up_name": "ML教程君",
                }

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)

                from openbiliclaw.soul.pipeline import (
                    IngestResult,
                    LayerUpdateResult,
                    OnionLayer,
                )

                return IngestResult(
                    signals_accepted=1,
                    layers_buffered=["interest", "surface"],
                    layers_updated=[
                        LayerUpdateResult(
                            layer=OnionLayer.INTEREST,
                            changed=True,
                            changes=["新增兴趣: AI"],
                        ),
                        LayerUpdateResult(
                            layer=OnionLayer.SURFACE,
                            changed=False,
                        ),
                    ],
                )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        database = FakeDatabase()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=database,
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"recommendation_id": 99},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["bvid"] == "BV1REC99"
        assert "interest" in body["layers_updated"]
        assert "surface" in body["layers_updated"]

        # Click should have been persisted as an event and ingested as a signal.
        assert memory.events, "Click should be persisted as an event"
        assert memory.events[0]["event_type"] == "click"
        assert memory.events[0]["metadata"]["bvid"] == "BV1REC99"
        assert memory.events[0]["metadata"]["recommendation_id"] == 99

        assert len(soul_engine.pipeline.ingested) == 1
        ingested_signal = soul_engine.pipeline.ingested[0]
        from openbiliclaw.soul.pipeline import SignalType

        assert ingested_signal.signal_type == SignalType.RECOMMENDATION_CLICK
        assert ingested_signal.payload["bvid"] == "BV1REC99"
        # Database lookup should have hydrated title/topic/up_name.
        assert ingested_signal.payload["title"] == "深入理解Transformer"
        assert ingested_signal.payload["topic_label"] == "AI技术"
        assert ingested_signal.payload["up_name"] == "ML教程君"

    def test_recommendation_click_endpoint_persists_dwell_fields(self) -> None:
        """When the extension reports dwell on the click-through, those
        fields flow into the persisted click event so storage can classify
        the recommendation outcome (meaningful_dwell vs quick_exit)."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class StubPipeline:
            async def ingest(self, signal: object) -> object:
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1, layers_buffered=[], layers_updated=[])

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = StubPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={
                "bvid": "BVdwell",
                "title": "深度教程",
                "watch_seconds": 600,
                "video_duration_seconds": 700,
            },
        )

        assert response.status_code == 200
        assert memory.events, "click should be persisted"
        ev = memory.events[0]
        assert ev["event_type"] == "click"
        assert ev["metadata"]["watch_seconds"] == 600
        assert ev["metadata"]["video_duration_seconds"] == 700
        assert ev["metadata"]["source"] == "recommendation_click"

    def test_recommendation_click_endpoint_persists_without_dwell_fields(self) -> None:
        """No dwell fields supplied → click still persists (storage will
        classify it as unknown / missing_dwell, but the endpoint must
        not require the fields)."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None

        class StubPipeline:
            async def ingest(self, signal: object) -> object:
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1, layers_buffered=[], layers_updated=[])

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = StubPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"bvid": "BVnoDwell", "title": "未知"},
        )

        assert response.status_code == 200
        assert memory.events, "click should still persist without dwell"
        ev = memory.events[0]
        assert "watch_seconds" not in ev["metadata"]
        assert "video_duration_seconds" not in ev["metadata"]

    def test_recommendation_click_endpoint_accepts_bvid_without_db_lookup(self) -> None:
        """When no recommendation_id is supplied, use the bvid from the payload directly."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None  # should not be called

        class SpyPipeline:
            def __init__(self) -> None:
                self.ingested: list[object] = []

            async def ingest(self, signal: object) -> object:
                self.ingested.append(signal)
                from openbiliclaw.soul.pipeline import IngestResult

                return IngestResult(signals_accepted=1)

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = SpyPipeline()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=FakeDatabase(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"bvid": "BV1DIRECT", "title": "直接点击"},
        )

        assert response.status_code == 200
        assert response.json()["bvid"] == "BV1DIRECT"
        assert len(soul_engine.pipeline.ingested) == 1
        assert soul_engine.pipeline.ingested[0].payload["bvid"] == "BV1DIRECT"
        assert soul_engine.pipeline.ingested[0].payload["title"] == "直接点击"

    def test_recommendation_click_endpoint_rejects_missing_bvid(self) -> None:
        """Without a bvid (either from payload or DB lookup), return 422."""
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            async def propagate_event(self, event: dict[str, object]) -> None:
                pass

        class FakeDatabase:
            def get_recommendation_by_id(
                self,
                recommendation_id: int,
            ) -> dict[str, object] | None:
                return None  # unknown recommendation

        app = create_app(
            memory_manager=FakeMemoryManager(),
            database=FakeDatabase(),
            soul_engine=None,
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"recommendation_id": 999},
        )

        assert response.status_code == 422
        assert "bvid" in response.json()["detail"].lower()

    def test_recommendation_click_endpoint_survives_pipeline_exception(self) -> None:
        """If the pipeline raises during ingest, the endpoint should still return 200.

        A click is user-visible — we must never propagate a backend failure back
        to the extension popup. The click is already persisted via propagate_event.
        """
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.events: list[dict[str, object]] = []

            async def propagate_event(self, event: dict[str, object]) -> None:
                self.events.append(event)

        class BrokenPipeline:
            async def ingest(self, signal: object) -> object:
                raise RuntimeError("pipeline is broken")

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.pipeline = BrokenPipeline()

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=FakeSoulEngine(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/recommendation-click",
            json={"bvid": "BVresilient", "title": "即便后端出错也不应阻塞"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        # Click should still have been persisted as an event.
        assert len(memory.events) == 1
        assert memory.events[0]["metadata"]["bvid"] == "BVresilient"
        # But layers_updated should be empty because ingest raised.
        assert response.json()["layers_updated"] == []

    def test_get_config_returns_llm_and_embedding_settings(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="gemini",
                fallback_enabled=True,
                gemini=LLMProviderConfig(api_key="test-gemini-key", model="gemini-2.5-flash"),
                embedding=EmbeddingConfig(
                    provider="gemini",
                    model="gemini-embedding-001",
                    similarity_threshold=0.85,
                    fallback_enabled=True,
                ),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/config", params={"reveal_keys": "true"})

        assert response.status_code == 200
        data = response.json()

        # LLM provider fields
        assert data["llm"]["default_provider"] == "gemini"
        assert data["llm"]["fallback_enabled"] is True
        assert data["llm"]["gemini"]["api_key"] == "test-gemini-key"
        assert data["llm"]["gemini"]["model"] == "gemini-2.5-flash"

        # Embedding fields
        assert data["llm"]["embedding"]["provider"] == "gemini"
        assert data["llm"]["embedding"]["model"] == "gemini-embedding-001"
        assert data["llm"]["embedding"]["similarity_threshold"] == 0.85
        assert data["llm"]["embedding"]["fallback_enabled"] is True

    def test_get_config_masks_api_keys_by_default(
        self,
        monkeypatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="sk-abcdef1234567890xyzw", model="gpt-4o"),
            ),
        )
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/config")

        assert response.status_code == 200
        data = response.json()
        # Key should be masked (not equal to the original)
        assert data["llm"]["openai"]["api_key"] != "sk-abcdef1234567890xyzw"
        assert "****" in data["llm"]["openai"]["api_key"] or "*" in data["llm"]["openai"]["api_key"]

    def test_put_config_updates_embedding_settings(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="ollama",
                ollama=LLMProviderConfig(model="llama3", base_url="http://localhost:11434"),
                embedding=EmbeddingConfig(
                    provider="",
                    model="gemini-embedding-001",
                    similarity_threshold=0.82,
                ),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        # Patch load_config to return our config
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        # Patch save_config to write to our temp path
        saved_configs: list[Config] = []

        def fake_save(c, path=None):
            saved_configs.append(c)
            save_config(c, config_path)

        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            fake_save,
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "ollama",
                    "fallback_enabled": True,
                    "embedding": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "similarity_threshold": 0.78,
                        "fallback_enabled": True,
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["config"]["llm"]["fallback_enabled"] is True
        assert data["config"]["llm"]["embedding"]["fallback_enabled"] is True

        # Verify the embedding was updated on the config object
        assert cfg.llm.embedding.provider == "openai"
        assert cfg.llm.embedding.model == "text-embedding-3-small"
        assert cfg.llm.embedding.similarity_threshold == 0.78

    def test_put_config_updates_embedding_credentials(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        """v0.3.32+ — embedding owns api_key/base_url. PUT /api/config
        must accept the new fields and round-trip them through GET (with
        the api_key masked on the way out)."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(api_key="ds-key", model="deepseek-v4-flash"),
                embedding=EmbeddingConfig(),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        # PUT — supply dedicated embedding credentials.
        put_resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "api_key": "sk-dedicated-embedding-xyz1234567890",
                        "base_url": "https://embed.example.com/v1",
                    },
                },
            },
        )
        assert put_resp.status_code == 200
        assert cfg.llm.embedding.api_key == "sk-dedicated-embedding-xyz1234567890"
        assert cfg.llm.embedding.base_url == "https://embed.example.com/v1"

        # GET (default — masked). api_key contains '*' but never the raw key.
        get_resp = client.get("/api/config")
        emb = get_resp.json()["llm"]["embedding"]
        assert emb["provider"] == "openai"
        assert emb["model"] == "text-embedding-3-small"
        assert emb["base_url"] == "https://embed.example.com/v1"
        assert "*" in emb["api_key"]
        assert "sk-dedicated-embedding-xyz1234567890" not in emb["api_key"]

        # PUT again with the masked key echoed back — must NOT overwrite
        # the real key with asterisks.
        masked_echo = emb["api_key"]
        client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "api_key": masked_echo,
                        "model": "text-embedding-3-large",
                    },
                },
            },
        )
        # Real key preserved; model still updated.
        assert cfg.llm.embedding.api_key == "sk-dedicated-embedding-xyz1234567890"
        assert cfg.llm.embedding.model == "text-embedding-3-large"

    def test_put_config_updates_provider_api_key_and_model(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import (
            Config,
            LLMConfig,
            LLMProviderConfig,
            save_config,
        )

        config_path = tmp_path / "config.toml"
        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="", model="gpt-4o"),
            ),
        )
        save_config(cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "deepseek",
                    "deepseek": {
                        "api_key": "sk-new-deepseek-key",
                        "model": "deepseek-chat",
                        "base_url": "https://api.deepseek.com",
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

        # Verify provider switch and key update
        assert cfg.llm.default_provider == "deepseek"
        assert cfg.llm.deepseek.api_key == "sk-new-deepseek-key"
        assert cfg.llm.deepseek.model == "deepseek-chat"
        assert cfg.llm.deepseek.base_url == "https://api.deepseek.com"


class TestEmbeddingAndCompatProviderE2E:
    """End-to-end coverage for the v0.3.32 changes through the HTTP boundary.

    Two related shifts ship in v0.3.32:

      1. ``[llm.embedding]`` owns its own ``api_key`` / ``base_url`` —
         embedding is fully decoupled from the chat ``[llm.<provider>]``
         blocks.
      2. ``openai_compatible`` becomes a first-class registered provider
         (separate ``[llm.openai_compatible]`` block, distinct registry
         entry from ``openai``) — Groq / Together / Azure OpenAI / vLLM
         and friends get a dedicated home instead of hijacking
         ``[llm.openai].base_url``.

    These tests exercise both end-to-end through ``/api/config`` so we
    catch any regression in serialization, masking, partial-update
    merging, hot-reload, or ConfigIssue surfacing.
    """

    @staticmethod
    def _make_client(monkeypatch, tmp_path, initial_cfg):
        """Wire up a TestClient with load_config/save_config patched to
        round-trip against a real on-disk config in tmp_path."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import save_config

        config_path = tmp_path / "config.toml"
        save_config(initial_cfg, config_path)
        monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))

        # `cfg` is a single mutable instance that both load_config and
        # save_config see — that mirrors how the FastAPI lifecycle reads
        # one config object and mutates it in place across requests.
        monkeypatch.setattr(
            "openbiliclaw.config.load_config",
            lambda *_a, **_kw: initial_cfg,
        )
        monkeypatch.setattr(
            "openbiliclaw.config.save_config",
            lambda c, path=None: save_config(c, config_path),
        )

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        return TestClient(app)

    # ── GET masking & shape ─────────────────────────────────────────

    def test_get_config_exposes_openai_compatible_block(self, monkeypatch, tmp_path) -> None:
        """The /api/config response must include the new
        [llm.openai_compatible] block so the popup can populate its
        fields. api_key is masked by default."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(api_key="sk-real-openai-1234567890"),
                openai_compatible=LLMProviderConfig(
                    api_key="gsk-groq-secret-key-1234567890",
                    model="llama-3.1-70b-versatile",
                    base_url="https://api.groq.com/openai/v1",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()

        compat = data["llm"]["openai_compatible"]
        # Shape: all expected fields are present, with the api_key masked
        # but model / base_url surfaced verbatim.
        assert compat["model"] == "llama-3.1-70b-versatile"
        assert compat["base_url"] == "https://api.groq.com/openai/v1"
        assert "*" in compat["api_key"]
        assert "gsk-groq-secret-key-1234567890" not in compat["api_key"]

    def test_get_config_exposes_embedding_credentials_masked(self, monkeypatch, tmp_path) -> None:
        """v0.3.32+ embedding owns api_key/base_url. They must surface
        in /api/config (so the popup knows what's configured) with
        api_key masked."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(api_key="ds-key"),
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="text-embedding-3-large",
                    api_key="sk-embed-secret-1234567890",
                    base_url="https://api.openai.com/v1",
                    similarity_threshold=0.91,
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")
        emb = response.json()["llm"]["embedding"]

        assert emb["provider"] == "openai"
        assert emb["model"] == "text-embedding-3-large"
        assert emb["base_url"] == "https://api.openai.com/v1"
        assert emb["similarity_threshold"] == 0.91
        assert "*" in emb["api_key"]
        assert "sk-embed-secret-1234567890" not in emb["api_key"]

    def test_get_config_with_reveal_keys_returns_raw_secrets(self, monkeypatch, tmp_path) -> None:
        """``GET /api/config?reveal_keys=true`` returns unmasked keys
        for both new fields (openai_compatible.api_key + embedding.api_key).
        Used by the popup when the user clicks "show" to edit."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                openai_compatible=LLMProviderConfig(
                    api_key="gsk-raw-1234567890",
                    base_url="https://api.groq.com/openai/v1",
                ),
                embedding=EmbeddingConfig(api_key="sk-emb-raw-1234567890"),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        revealed = client.get("/api/config", params={"reveal_keys": "true"}).json()
        assert revealed["llm"]["openai_compatible"]["api_key"] == "gsk-raw-1234567890"
        assert revealed["llm"]["embedding"]["api_key"] == "sk-emb-raw-1234567890"

    # ── PUT round-trip: openai_compatible ───────────────────────────

    def test_put_openai_compatible_round_trips_through_get(self, monkeypatch, tmp_path) -> None:
        """PUT a full [llm.openai_compatible] block, then GET — the
        non-secret fields come back identical, api_key comes back
        masked but the in-memory config object holds the real value."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        put_resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-fresh-groq-key-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )
        assert put_resp.status_code == 200
        body = put_resp.json()
        assert body["ok"] is True

        # In-memory config has the real key
        assert cfg.llm.default_provider == "openai_compatible"
        assert cfg.llm.openai_compatible.api_key == "gsk-fresh-groq-key-1234567890"
        assert cfg.llm.openai_compatible.model == "llama-3.1-70b-versatile"
        assert cfg.llm.openai_compatible.base_url == "https://api.groq.com/openai/v1"

        # Subsequent GET round-trips with masking
        get_resp = client.get("/api/config")
        compat = get_resp.json()["llm"]["openai_compatible"]
        assert compat["model"] == "llama-3.1-70b-versatile"
        assert compat["base_url"] == "https://api.groq.com/openai/v1"
        assert "*" in compat["api_key"]
        assert "gsk-fresh-groq-key-1234567890" not in compat["api_key"]

    def test_put_openai_compatible_does_not_stomp_openai_block(self, monkeypatch, tmp_path) -> None:
        """Partial PUT with only [llm.openai_compatible] must NOT clear
        the existing [llm.openai] block. Both providers can coexist
        (the whole point of the v0.3.32 split)."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            llm=LLMConfig(
                default_provider="openai",
                openai=LLMProviderConfig(
                    api_key="sk-original-openai-1234567890",
                    model="gpt-5-nano",
                    base_url="",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        client.put(
            "/api/config",
            json={
                "llm": {
                    "openai_compatible": {
                        "api_key": "gsk-groq-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )

        # openai block survived intact
        assert cfg.llm.openai.api_key == "sk-original-openai-1234567890"
        assert cfg.llm.openai.model == "gpt-5-nano"
        assert cfg.llm.default_provider == "openai"  # unchanged
        # openai_compatible block freshly populated
        assert cfg.llm.openai_compatible.api_key == "gsk-groq-1234567890"
        assert cfg.llm.openai_compatible.base_url == "https://api.groq.com/openai/v1"

    # ── ConfigIssue surfacing ───────────────────────────────────────

    def test_put_default_openai_compatible_without_base_url_surfaces_issue(
        self, monkeypatch, tmp_path
    ) -> None:
        """If the user picks openai_compatible as default but forgets
        base_url, ``_collect_config_issues`` flags it and the issue
        appears in the PUT response so the popup can highlight the
        offending field — without this, the bad config would silently
        save and the daemon would 401 against api.openai.com on first
        request."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-test",
                        "model": "llama-3.1-70b-versatile",
                        # base_url deliberately omitted
                    },
                },
            },
        )
        assert resp.status_code == 200

        issues = resp.json()["config"]["issues"]
        fields = [i["field"] for i in issues]
        assert "llm.openai_compatible.base_url" in fields, f"expected base_url issue in {fields}"

    # ── Embedding round-trip + masked-echo protection ───────────────

    def test_put_embedding_via_openai_compatible_round_trip(self, monkeypatch, tmp_path) -> None:
        """Embedding can independently target an openai_compatible
        backend (vLLM / Together / Azure OpenAI), with its own api_key
        and base_url — no need to also fill [llm.openai_compatible]."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        put = client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "provider": "openai_compatible",
                        "model": "bge-large-en-v1.5",
                        "api_key": "vllm-token-1234567890",
                        "base_url": "http://vllm.internal:8000/v1",
                        "similarity_threshold": 0.85,
                    },
                },
            },
        )
        assert put.status_code == 200
        assert cfg.llm.embedding.provider == "openai_compatible"
        assert cfg.llm.embedding.api_key == "vllm-token-1234567890"
        assert cfg.llm.embedding.base_url == "http://vllm.internal:8000/v1"
        assert cfg.llm.embedding.similarity_threshold == 0.85

        # Round-trip via GET — base_url + provider + threshold come back
        # raw, api_key masked.
        emb = client.get("/api/config").json()["llm"]["embedding"]
        assert emb["provider"] == "openai_compatible"
        assert emb["base_url"] == "http://vllm.internal:8000/v1"
        assert emb["similarity_threshold"] == 0.85
        assert "*" in emb["api_key"]
        assert "vllm-token-1234567890" not in emb["api_key"]

    def test_put_embedding_masked_echo_does_not_overwrite_real_key(
        self, monkeypatch, tmp_path
    ) -> None:
        """Workflow: open settings → backend returns masked key → user
        edits an unrelated field (model) → submits — the masked api_key
        gets echoed back. Backend must detect the mask (any '*') and
        keep the real key. Otherwise every save would silently destroy
        the user's secret."""
        from openbiliclaw.config import (
            Config,
            EmbeddingConfig,
            LLMConfig,
            LLMProviderConfig,
        )

        cfg = Config(
            llm=LLMConfig(
                openai=LLMProviderConfig(api_key="sk-openai"),
                embedding=EmbeddingConfig(
                    provider="openai",
                    model="text-embedding-3-small",
                    api_key="sk-real-secret-do-not-overwrite-1234567890",
                    base_url="",
                ),
            ),
        )
        client = self._make_client(monkeypatch, tmp_path, cfg)

        # Step 1 — popup loads the config and gets a masked key.
        masked = client.get("/api/config").json()["llm"]["embedding"]["api_key"]
        assert "*" in masked

        # Step 2 — popup re-submits with the masked key and a new model.
        client.put(
            "/api/config",
            json={
                "llm": {
                    "embedding": {
                        "api_key": masked,
                        "model": "text-embedding-3-large",
                    },
                },
            },
        )

        # Real key preserved; the model field still updated.
        assert cfg.llm.embedding.api_key == "sk-real-secret-do-not-overwrite-1234567890"
        assert cfg.llm.embedding.model == "text-embedding-3-large"

    # ── Hot-reload verification ─────────────────────────────────────

    def test_put_triggers_runtime_hot_reload(self, monkeypatch, tmp_path) -> None:
        """``rebuild_from_config`` must run successfully when the new
        config is valid (here: openai default with api_key set). The
        ``reloaded=true`` flag in the response is the externally
        observable signal that the registry was actually rebuilt — the
        popup uses this to decide whether to show "立即生效" vs "重启
        生效" feedback."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-old")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        resp = client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai_compatible",
                    "openai_compatible": {
                        "api_key": "gsk-new",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )
        body = resp.json()
        assert body["reloaded"] is True, (
            f"expected hot-reload to succeed, got message: {body['message']}"
        )

    # ── Coexistence: both providers usable in one config ────────────

    def test_get_after_dual_put_returns_both_provider_blocks(self, monkeypatch, tmp_path) -> None:
        """Set both [llm.openai] (real OpenAI for chat) and
        [llm.openai_compatible] (Groq for fast drafting) in one PUT.
        Both blocks must round-trip independently — the v0.3.32 split
        explicitly enables this dual-stack scenario."""
        from openbiliclaw.config import Config, LLMConfig

        cfg = Config(llm=LLMConfig())
        client = self._make_client(monkeypatch, tmp_path, cfg)

        client.put(
            "/api/config",
            json={
                "llm": {
                    "default_provider": "openai",
                    "openai": {
                        "api_key": "sk-real-openai-1234567890",
                        "model": "gpt-5-nano",
                    },
                    "openai_compatible": {
                        "api_key": "gsk-groq-1234567890",
                        "model": "llama-3.1-70b-versatile",
                        "base_url": "https://api.groq.com/openai/v1",
                    },
                },
            },
        )

        data = client.get("/api/config").json()["llm"]
        assert data["default_provider"] == "openai"
        # Two distinct masked secrets, each pointing at its own block.
        assert data["openai"]["model"] == "gpt-5-nano"
        assert data["openai_compatible"]["model"] == "llama-3.1-70b-versatile"
        assert data["openai_compatible"]["base_url"] == "https://api.groq.com/openai/v1"
        # api_keys are both masked but distinct (different last 4 chars
        # in the mask), proving they're stored as separate values.
        openai_mask = data["openai"]["api_key"]
        compat_mask = data["openai_compatible"]["api_key"]
        assert "*" in openai_mask and "*" in compat_mask
        assert openai_mask != compat_mask

    def test_get_config_exposes_sources_and_advanced_settings(self, monkeypatch, tmp_path) -> None:
        """The config API should expose persisted advanced fields so the
        extension settings page can stay aligned with config.toml."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(
            data_dir="runtime-data",
            llm=LLMConfig(
                default_provider="deepseek",
                deepseek=LLMProviderConfig(
                    api_key="ds-key",
                    model="deepseek-v4-flash",
                    base_url="https://api.deepseek.com",
                    reasoning_effort="high",
                ),
                openrouter=LLMProviderConfig(
                    api_key="or-key",
                    model="openai/gpt-5-nano",
                    base_url="https://openrouter.ai/api/v1",
                    http_referer="https://example.com",
                    x_title="Example App",
                ),
            ),
        )
        cfg.bilibili.browser_executable = "/Applications/Chrome.app"
        cfg.bilibili.browser_headed = True
        cfg.sources.browser_cdp_url = "http://localhost:9222"
        cfg.sources.browser_headed = True
        cfg.sources.bilibili.enabled = False
        cfg.sources.xiaohongshu.enabled = False
        cfg.sources.xiaohongshu.daily_search_budget = 11
        cfg.sources.xiaohongshu.daily_creator_budget = 3
        cfg.sources.xiaohongshu.task_interval_seconds = 66
        cfg.sources.douyin.enabled = True
        cfg.sources.douyin.cookie_env = "CUSTOM_DY_COOKIE"
        cfg.sources.douyin.daily_search_budget = 12
        cfg.sources.douyin.daily_hot_budget = 4
        cfg.sources.douyin.daily_feed_budget = 13
        cfg.sources.douyin.request_interval_seconds = 5
        cfg.sources.youtube.enabled = True
        cfg.sources.youtube.daily_search_budget = 4
        cfg.sources.youtube.daily_trending_budget = 44
        cfg.sources.youtube.daily_channel_budget = 8
        cfg.sources.youtube.request_interval_seconds = 3
        cfg.scheduler.pool_source_shares = {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
        }
        cfg.scheduler.account_sync_interval_hours = 9
        cfg.scheduler.refresh_check_interval_seconds = 75
        cfg.scheduler.signal_event_threshold = 9
        cfg.scheduler.trending_refresh_hours = 5
        cfg.scheduler.explore_refresh_hours = 18
        cfg.scheduler.discovery_limit = 17
        cfg.scheduler.proactive_push_interval_seconds = 155
        cfg.scheduler.speculator_idle_interval_minutes = 11
        cfg.scheduler.speculation_interval_minutes = 21
        cfg.scheduler.speculation_ttl_days = 8
        cfg.scheduler.auto_update_enabled = True
        cfg.scheduler.auto_update_check_interval_hours = 10
        cfg.logging.file_level = "WARNING"
        cfg.logging.directory = "runtime-logs"
        cfg.logging.filename = "backend.log"
        cfg.logging.max_file_size_mb = 123
        cfg.logging.aggregate_budget_mb = 456
        cfg.logging.unmanaged_truncate_mb = 78
        cfg.logging.unmanaged_max_age_days = 9

        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config", params={"reveal_keys": "true"})
        assert response.status_code == 200
        data = response.json()

        assert data["data_dir"] == "runtime-data"
        assert data["llm"]["concurrency"] == 3
        assert data["llm"]["deepseek"]["reasoning_effort"] == "high"
        assert data["llm"]["openrouter"]["http_referer"] == "https://example.com"
        assert data["llm"]["openrouter"]["x_title"] == "Example App"
        assert data["bilibili"]["browser_executable"] == "/Applications/Chrome.app"
        assert data["bilibili"]["browser_headed"] is True
        assert data["sources"]["browser"]["cdp_url"] == "http://localhost:9222"
        assert data["sources"]["browser"]["headed"] is True
        assert data["sources"]["bilibili"]["enabled"] is False
        assert data["sources"]["xiaohongshu"]["enabled"] is False
        assert data["sources"]["xiaohongshu"]["daily_search_budget"] == 11
        assert data["sources"]["douyin"]["enabled"] is True
        assert data["sources"]["douyin"]["daily_feed_budget"] == 13
        assert data["sources"]["youtube"]["enabled"] is True
        assert data["sources"]["youtube"]["daily_search_budget"] == 4
        assert data["sources"]["youtube"]["daily_trending_budget"] == 44
        assert data["sources"]["youtube"]["daily_channel_budget"] == 8
        assert data["sources"]["youtube"]["request_interval_seconds"] == 3
        assert data["scheduler"]["pool_source_shares"] == {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
        }
        assert data["scheduler"]["account_sync_interval_hours"] == 9
        assert data["scheduler"]["refresh_check_interval_seconds"] == 75
        assert data["scheduler"]["signal_event_threshold"] == 9
        assert data["scheduler"]["trending_refresh_hours"] == 5
        assert data["scheduler"]["explore_refresh_hours"] == 18
        assert data["scheduler"]["discovery_limit"] == 17
        assert data["scheduler"]["proactive_push_interval_seconds"] == 155
        assert data["scheduler"]["speculator_idle_interval_minutes"] == 11
        assert data["scheduler"]["speculation_interval_minutes"] == 21
        assert data["scheduler"]["speculation_ttl_days"] == 8
        assert data["scheduler"]["auto_update_enabled"] is True
        assert data["scheduler"]["auto_update_check_interval_hours"] == 10
        assert data["logging"]["file_level"] == "WARNING"
        assert data["logging"]["directory"] == "runtime-logs"
        assert data["logging"]["filename"] == "backend.log"
        assert data["logging"]["file_path"] == str(tmp_path / "runtime-logs" / "backend.log")
        assert data["logging"]["max_file_size_mb"] == 123
        assert data["logging"]["aggregate_budget_mb"] == 456
        assert data["logging"]["unmanaged_truncate_mb"] == 78
        assert data["logging"]["unmanaged_max_age_days"] == 9

    def test_get_config_exposes_scheduler_pause_on_extension_disconnect(
        self, monkeypatch, tmp_path
    ) -> None:
        from openbiliclaw.config import Config

        cfg = Config()
        cfg.scheduler.pause_on_extension_disconnect = True
        cfg.scheduler.extension_disconnect_grace_seconds = 45
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.get("/api/config")

        assert response.status_code == 200
        scheduler = response.json()["scheduler"]
        assert scheduler["pause_on_extension_disconnect"] is True
        assert scheduler["extension_disconnect_grace_seconds"] == 45

    @pytest.mark.parametrize(("raw_bool", "bad_grace"), [("true", -1), ("on", 0), ("true", "abc")])
    def test_put_config_updates_scheduler_pause_on_extension_disconnect(
        self,
        monkeypatch,
        tmp_path,
        raw_bool: str,
        bad_grace: object,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "pause_on_extension_disconnect": raw_bool,
                    "extension_disconnect_grace_seconds": bad_grace,
                },
            },
        )

        assert response.status_code == 200
        assert cfg.scheduler.pause_on_extension_disconnect is True
        assert cfg.scheduler.extension_disconnect_grace_seconds == 90
        scheduler = response.json()["config"]["scheduler"]
        assert scheduler["pause_on_extension_disconnect"] is True
        assert scheduler["extension_disconnect_grace_seconds"] == 90

    def test_put_config_rebuilds_runtime_with_pause_on_disconnect(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))

        async def _fake_rebuild(self: RuntimeContext, config: Config) -> None:
            self.config = config
            self.runtime_controller = SimpleNamespace(scheduler_config=config.scheduler)

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", _fake_rebuild)
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "pause_on_extension_disconnect": True,
                    "extension_disconnect_grace_seconds": 12,
                },
            },
        )

        assert response.status_code == 200
        runtime_scheduler = client.app.state.runtime_context.runtime_controller.scheduler_config
        assert runtime_scheduler.pause_on_extension_disconnect is True
        assert runtime_scheduler.extension_disconnect_grace_seconds == 12

    def test_put_config_updates_sources_and_advanced_settings(self, monkeypatch, tmp_path) -> None:
        """PUT /api/config should update the same advanced fields that the
        extension settings page exposes."""
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "data_dir": "runtime-data",
                "llm": {
                    "concurrency": 5,
                    "deepseek": {"reasoning_effort": "high"},
                    "openrouter": {
                        "http_referer": "https://example.com",
                        "x_title": "Example App",
                    },
                    "soul": {"provider": "claude", "model": "claude-sonnet-4-6"},
                    "discovery": {"provider": "deepseek", "model": "deepseek-v4-flash"},
                    "recommendation": {"provider": "gemini", "model": "gemini-2.5-flash"},
                    "evaluation": {"provider": "openai", "model": "gpt-5-nano"},
                },
                "bilibili": {
                    "browser_executable": "/Applications/Chrome.app",
                    "browser_headed": True,
                },
                "sources": {
                    "browser": {"cdp_url": "http://localhost:9222", "headed": True},
                    "bilibili": {"enabled": False},
                    "xiaohongshu": {
                        "enabled": False,
                        "daily_search_budget": 11,
                        "daily_creator_budget": 3,
                        "task_interval_seconds": 66,
                    },
                    "douyin": {
                        "enabled": True,
                        "mode": "direct",
                        "cookie_env": "CUSTOM_DY_COOKIE",
                        "daily_search_budget": 12,
                        "daily_hot_budget": 4,
                        "daily_feed_budget": 13,
                        "request_interval_seconds": 5,
                    },
                    "youtube": {
                        "enabled": True,
                        "daily_search_budget": 5,
                        "daily_trending_budget": 41,
                        "daily_channel_budget": 9,
                        "request_interval_seconds": 4,
                        "min_interval_minutes": 30,
                    },
                },
                "scheduler": {
                    "account_sync_interval_hours": 9,
                    "pool_source_shares": {
                        "bilibili": 6,
                        "xiaohongshu": 2,
                        "douyin": 2,
                        "youtube": 1,
                    },
                    "refresh_check_interval_seconds": 75,
                    "signal_event_threshold": 9,
                    "trending_refresh_hours": 5,
                    "explore_refresh_hours": 18,
                    "discovery_limit": 17,
                    "proactive_push_interval_seconds": 155,
                    "speculator_idle_interval_minutes": 11,
                    "speculation_interval_minutes": 21,
                    "speculation_ttl_days": 8,
                    "speculation_cooldown_days": 9,
                    "speculation_confirmation_threshold": 4,
                    "speculation_max_active": 6,
                    "speculation_max_primary_interests": 17,
                    "speculation_max_secondary_interests": 66,
                    "auto_update_enabled": True,
                    "auto_update_check_interval_hours": 10,
                },
                "storage": {"db_path": "runtime-data/openbiliclaw.db"},
                "logging": {
                    "file_level": "WARNING",
                    "directory": "runtime-logs",
                    "filename": "backend.log",
                    "max_file_size_mb": 123,
                    "backup_count": 3,
                    "aggregate_budget_mb": 456,
                    "unmanaged_truncate_mb": 78,
                    "unmanaged_max_age_days": 9,
                },
            },
        )

        assert response.status_code == 200
        assert cfg.data_dir == "runtime-data"
        assert cfg.llm.concurrency == 5
        assert response.json()["config"]["llm"]["concurrency"] == 5
        assert cfg.llm.deepseek.reasoning_effort == "high"
        assert cfg.llm.openrouter.http_referer == "https://example.com"
        assert cfg.llm.openrouter.x_title == "Example App"
        assert cfg.llm.soul.provider == "claude"
        assert cfg.llm.discovery.provider == "deepseek"
        assert cfg.llm.recommendation.provider == "gemini"
        assert cfg.llm.evaluation.provider == "openai"
        assert cfg.bilibili.browser_executable == "/Applications/Chrome.app"
        assert cfg.bilibili.browser_headed is True
        assert cfg.sources.browser_cdp_url == "http://localhost:9222"
        assert cfg.sources.browser_headed is True
        assert cfg.sources.bilibili.enabled is False
        assert cfg.sources.xiaohongshu.enabled is False
        assert cfg.sources.xiaohongshu.daily_search_budget == 11
        assert cfg.sources.douyin.enabled is True
        assert cfg.sources.douyin.cookie_env == "CUSTOM_DY_COOKIE"
        assert cfg.sources.douyin.daily_feed_budget == 13
        assert cfg.sources.youtube.enabled is True
        assert cfg.sources.youtube.daily_search_budget == 5
        assert cfg.sources.youtube.daily_trending_budget == 41
        assert cfg.sources.youtube.daily_channel_budget == 9
        assert cfg.sources.youtube.request_interval_seconds == 4
        assert cfg.sources.youtube.min_interval_minutes == 30
        assert response.json()["config"]["sources"]["youtube"]["min_interval_minutes"] == 30
        assert cfg.scheduler.pool_source_shares == {
            "bilibili": 6,
            "xiaohongshu": 2,
            "douyin": 2,
            "youtube": 1,
        }
        assert cfg.scheduler.refresh_check_interval_seconds == 75
        assert cfg.scheduler.signal_event_threshold == 9
        assert cfg.scheduler.trending_refresh_hours == 5
        assert cfg.scheduler.explore_refresh_hours == 18
        assert cfg.scheduler.discovery_limit == 17
        assert cfg.scheduler.proactive_push_interval_seconds == 155
        assert cfg.scheduler.speculator_idle_interval_minutes == 11
        assert cfg.scheduler.speculation_interval_minutes == 21
        assert cfg.scheduler.auto_update_enabled is True
        assert cfg.scheduler.auto_update_check_interval_hours == 10
        assert cfg.storage.db_path == "runtime-data/openbiliclaw.db"
        assert cfg.logging.file_level == "WARNING"
        assert cfg.logging.max_file_size_mb == 123
        assert cfg.logging.aggregate_budget_mb == 456
        assert cfg.logging.unmanaged_truncate_mb == 78
        assert cfg.logging.unmanaged_max_age_days == 9

    def test_put_config_normalizes_invalid_scheduler_runtime_fields(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig

        cfg = Config(llm=LLMConfig(openai=LLMProviderConfig(api_key="sk-openai")))
        client = self._make_client(monkeypatch, tmp_path, cfg)

        response = client.put(
            "/api/config",
            json={
                "scheduler": {
                    "refresh_check_interval_seconds": "abc",
                    "signal_event_threshold": -1,
                    "trending_refresh_hours": 0,
                    "explore_refresh_hours": 0,
                    "discovery_limit": 61,
                    "proactive_push_interval_seconds": 29,
                    "speculator_idle_interval_minutes": 4,
                },
            },
        )

        assert response.status_code == 200
        scheduler = response.json()["config"]["scheduler"]
        assert scheduler["refresh_check_interval_seconds"] == 60
        assert scheduler["signal_event_threshold"] == 6
        assert scheduler["trending_refresh_hours"] == 3
        assert scheduler["explore_refresh_hours"] == 12
        assert scheduler["discovery_limit"] == 30
        assert scheduler["proactive_push_interval_seconds"] == 120
        assert scheduler["speculator_idle_interval_minutes"] == 30

    def test_source_share_suggestion_uses_event_counts(self, monkeypatch, tmp_path) -> None:
        """GET /api/config/source-share-suggestion should suggest ratios
        from observed platform event counts and current enabled switches."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        cfg = Config()
        cfg.sources.xiaohongshu.enabled = True
        cfg.sources.douyin.enabled = True
        cfg.sources.youtube.enabled = True
        cfg.scheduler.pool_source_shares = {
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        }
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        class FakeDatabase:
            def count_events_by_source_platform(self) -> dict[str, int]:
                return {
                    "bilibili": 900,
                    "xiaohongshu": 100,
                    "douyin": 9,
                    "youtube": 400,
                }

        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.get("/api/config/source-share-suggestion")

        assert response.status_code == 200
        assert response.json() == {
            "event_counts": {
                "bilibili": 900,
                "xiaohongshu": 100,
                "douyin": 9,
                "youtube": 400,
            },
            "enabled_sources": {
                "bilibili": True,
                "xiaohongshu": True,
                "douyin": True,
                "youtube": True,
            },
            "suggested_shares": {
                "bilibili": 8,
                "xiaohongshu": 3,
                "douyin": 1,
                "youtube": 5,
            },
        }

    def test_source_share_suggestion_post_uses_form_overrides(self, monkeypatch, tmp_path) -> None:
        """POST /api/config/source-share-suggestion should support the
        extension settings page's unsaved switch/share state."""
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, save_config

        cfg = Config()
        cfg.sources.xiaohongshu.enabled = True
        cfg.sources.douyin.enabled = True
        cfg.sources.youtube.enabled = False
        cfg.scheduler.pool_source_shares = {
            "bilibili": 8,
            "xiaohongshu": 1,
            "douyin": 1,
            "youtube": 1,
        }
        config_path = tmp_path / "config.toml"
        save_config(cfg, config_path)
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: cfg)

        class FakeDatabase:
            def count_events_by_source_platform(self) -> dict[str, int]:
                return {
                    "bilibili": 900,
                    "xiaohongshu": 100,
                    "douyin": 9,
                    "youtube": 400,
                }

        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=object(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/config/source-share-suggestion",
            json={
                "enabled_sources": {
                    "bilibili": True,
                    "xiaohongshu": False,
                    "douyin": False,
                    "youtube": True,
                },
                "configured_shares": {
                    "bilibili": 6,
                    "xiaohongshu": 4,
                    "douyin": 4,
                    "youtube": 2,
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "event_counts": {
                "bilibili": 900,
                "xiaohongshu": 100,
                "douyin": 9,
                "youtube": 400,
            },
            "enabled_sources": {
                "bilibili": True,
                "xiaohongshu": False,
                "douyin": False,
                "youtube": True,
            },
            "suggested_shares": {
                "bilibili": 6,
                "youtube": 4,
            },
        }


def test_events_endpoint_emits_activity_added_runtime_event() -> None:
    """v0.3.38 — POST /api/events publishes ``activity.added`` so the
    popup can refresh its activity feed without polling.
    """
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeRuntimeController:
        def __init__(self, hub: FakeEventHub) -> None:
            self.event_hub = hub

    hub = FakeEventHub()
    memory = FakeMemoryManager()
    app = create_app(
        memory_manager=memory,
        database=object(),
        soul_engine=object(),
        runtime_controller=FakeRuntimeController(hub),
    )
    client = TestClient(app)

    response = client.post(
        "/api/events",
        json={
            "events": [
                {
                    "type": "click",
                    "url": "https://www.bilibili.com/video/BV1A",
                    "title": "A",
                    "timestamp": 1710000000000,
                },
                {
                    "type": "view",
                    "url": "https://www.bilibili.com/video/BV1B",
                    "title": "B",
                    "timestamp": 1710000001000,
                },
                {
                    "type": "click",
                    "url": "https://www.bilibili.com/video/BV1C",
                    "title": "C",
                    "timestamp": 1710000002000,
                },
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 3

    activity_events = [e for e in hub.events if e["type"] == "activity.added"]
    assert len(activity_events) == 1, "should fire exactly once per ingest call"
    assert activity_events[0]["count"] == 3


def test_events_endpoint_skips_activity_added_for_empty_batch() -> None:
    """No events accepted → no activity.added (avoids spamming popup
    when the extension flushes an empty buffer)."""
    from fastapi.testclient import TestClient

    class FakeMemoryManager:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def propagate_event(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeEventHub:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def publish(self, event: dict[str, object]) -> None:
            self.events.append(event)

    class FakeRuntimeController:
        def __init__(self, hub: FakeEventHub) -> None:
            self.event_hub = hub

    hub = FakeEventHub()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=object(),
        runtime_controller=FakeRuntimeController(hub),
    )
    client = TestClient(app)

    response = client.post("/api/events", json={"events": []})
    assert response.status_code == 200
    assert response.json()["accepted"] == 0

    activity_events = [e for e in hub.events if e["type"] == "activity.added"]
    assert activity_events == []


def test_probe_chat_sentiment_uses_plain_text_llm_call() -> None:
    """Probe chat sentiment asks for one scalar word, not a JSON object.

    DeepSeek rejects ``response_format=json_object`` for this prompt because
    it intentionally does not ask for JSON. The API should therefore call the
    plain core-memory LLM path with ``json_mode=False``.
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    class FakeLLM:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def complete_structured_task(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(("structured", kwargs))
            return SimpleNamespace(content="positive")

        async def complete_with_core_memory(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(("core", kwargs))
            return SimpleNamespace(content="positive")

    class FakeDialogue:
        async def respond(self, _message: str) -> str:
            return "懂，你更喜欢和 VOCALOID 相关的部分。"

    class FakeSpeculator:
        def __init__(self) -> None:
            self.observed: list[object] = []

        def observe(self, events: object) -> None:
            self.observed.append(events)

        def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class FakeSoulEngine:
        def __init__(self, speculator: FakeSpeculator) -> None:
            self._speculator = speculator

    class FakeMemoryManager:
        def load_cognition_updates(self) -> list[object]:
            return []

        def save_cognition_updates(self, _updates: list[object]) -> None:
            return None

    llm = FakeLLM()
    speculator = FakeSpeculator()
    app = create_app(
        memory_manager=FakeMemoryManager(),
        database=object(),
        soul_engine=FakeSoulEngine(speculator),
        dialogue=FakeDialogue(),
        recommendation_engine=SimpleNamespace(_llm=llm),
    )
    client = TestClient(app)

    response = client.post(
        "/api/interest-probes/respond",
        json={
            "domain": "电子音乐制作",
            "response": "chat",
            "message": "我更喜欢与vocaloid有关系的部分",
        },
    )

    assert response.status_code == 200
    assert llm.calls
    method, kwargs = llm.calls[0]
    assert method == "core"
    assert kwargs["caller"] == "api.sentiment"
    assert kwargs["max_tokens"] == 8
    assert kwargs["json_mode"] is False
