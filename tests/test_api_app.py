"""Tests for the backend API app."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

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
    async def test_restart_tasks_detaches_avoidance_speculator_tick(self) -> None:
        from types import SimpleNamespace

        from openbiliclaw.api.runtime_context import RuntimeContext
        from openbiliclaw.config import Config

        class HangingAvoidanceSpeculator:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.calls: list[tuple[object, object | None]] = []

            async def force_tick(
                self,
                profile: object,
                *,
                feedback_history: object | None = None,
            ) -> None:
                self.calls.append((profile, feedback_history))
                self.started.set()
                await asyncio.sleep(60)

        class FakeSoulEngine:
            def __init__(self, avoidance_speculator: HangingAvoidanceSpeculator) -> None:
                self._avoidance_speculator = avoidance_speculator

            async def get_profile(self) -> dict[str, object]:
                return {"profile": "ok"}

        feedback_history = [{"domain": "浅层热点复读", "response": "reject"}]
        avoidance_speculator = HangingAvoidanceSpeculator()
        cfg = Config()
        ctx = RuntimeContext(
            config=cfg,
            memory_manager=SimpleNamespace(
                load_discovery_runtime_state=lambda: {
                    "avoidance_probe_feedback_history": feedback_history,
                }
            ),
            runtime_controller=object(),
            account_sync_service=object(),
            auto_update_service=object(),
            soul_engine=FakeSoulEngine(avoidance_speculator),
            recommendation_engine=object(),
        )
        app = SimpleNamespace(state=SimpleNamespace())

        try:
            await asyncio.wait_for(ctx.restart_background_tasks(app), timeout=0.5)
            assert ctx.task_registry.stats().get("post_reload_avoidance_speculate") == 1
            await asyncio.wait_for(avoidance_speculator.started.wait(), timeout=0.5)
            assert avoidance_speculator.calls == [
                ({"profile": "ok"}, feedback_history),
            ]
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
                xhs_self_info_provider: object = None,
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

    def test_health_endpoint_reports_embedding_not_ready_without_service(self) -> None:
        from fastapi.testclient import TestClient

        # A bare object() soul engine has no _embedding_service attribute,
        # so embedding is reported as not ready — the popup turns this into
        # the "enable local Ollama" banner.
        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

    def test_health_endpoint_reports_embedding_ready_when_service_present(self) -> None:
        from fastapi.testclient import TestClient

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = object()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is True

    def test_health_endpoint_embedding_ready_true_when_probe_succeeds(self) -> None:
        from fastapi.testclient import TestClient

        class _ProbeService:
            async def probe(self) -> bool:
                return True

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _ProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is True

    def test_health_endpoint_embedding_not_ready_when_probe_fails(self) -> None:
        from fastapi.testclient import TestClient

        # The service object exists but the provider can't produce a vector
        # (e.g. bge-m3 never pulled, so every embed 404s). The live probe must
        # report not-ready instead of the old build-only ``True`` that left the
        # popup banner green while semantic dedup was 100% broken.
        class _FailingProbeService:
            async def probe(self) -> bool:
                return False

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _FailingProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

    def test_health_endpoint_caches_embedding_probe_result(self) -> None:
        from fastapi.testclient import TestClient

        # Frequent /health polls (Docker healthcheck + popup re-poll on focus)
        # must share one provider round-trip within the TTL window.
        class _CountingProbeService:
            def __init__(self) -> None:
                self.calls = 0

            async def probe(self) -> bool:
                self.calls += 1
                return True

        service = _CountingProbeService()

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = service

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        client.get("/api/health")
        client.get("/api/health")

        assert service.calls == 1

    def test_health_endpoint_strict_when_probe_times_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as appmod

        # gui-init: readiness is now STRICT — a probe that doesn't answer within
        # the (generous, cold-load-tolerant) window can't be confirmed working,
        # so it reports not-ready instead of optimistically green. The short
        # fail-TTL re-probes soon so it greens once the load actually finishes.
        monkeypatch.setattr(appmod, "_EMBEDDING_PROBE_TIMEOUT_SECONDS", 0.05)

        class _SlowProbeService:
            async def probe(self) -> bool:
                await asyncio.sleep(0.5)  # exceeds the cap → times out
                return True

        class EmbeddingSoulEngine:
            def __init__(self) -> None:
                self._embedding_service = _SlowProbeService()

        app = create_app(
            memory_manager=object(), database=object(), soul_engine=EmbeddingSoulEngine()
        )
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["embedding_ready"] is False

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
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
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
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
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
            "pool_raw_count": 0,
            "pool_pending_count": 0,
            "pool_pending_eval_count": 0,
            "pool_evaluated_pending_count": 0,
            "pool_target_count": 30,
            "last_discovered_count": 14,
            "last_replenished_count": 6,
            "recent_pool_topics": ["国际时事", "宏观经济", "纪录片"],
            "manual_refresh_state": "idle",
            "manual_refresh_message": "",
            "last_account_sync_at": "2026-03-14T18:00:00+00:00",
            "last_account_sync_error": "",
            "auto_update_enabled": False,
            "current_version": "0.3.102",
            "latest_remote_version": "",
            "last_update_check_at": "",
            "last_update_error": "",
            "backend_update_state": "disabled",
            "backend_update_reason": "none",
        }

    def test_runtime_status_endpoint_includes_backend_update_summary(self) -> None:
        from fastapi.testclient import TestClient

        class FakeRuntimeController:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "initialized": True,
                    "recommendation_count": 5,
                    "pending_signal_events": 3,
                    "unread_count": 2,
                }

        class FakeAutoUpdateService:
            def get_runtime_status(self) -> dict[str, object]:
                return {
                    "auto_update_enabled": False,
                    "current_version": "0.3.91",
                    "latest_remote_version": "0.3.92",
                    "last_update_check_at": "2026-05-31T12:00:00+00:00",
                    "last_update_error": "",
                    "backend_update_state": "update_available",
                    "backend_update_reason": "none",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
            auto_update_service=FakeAutoUpdateService(),
        )
        client = TestClient(app)

        response = client.get("/api/runtime-status")

        assert response.status_code == 200
        body = response.json()
        assert body["auto_update_enabled"] is False
        assert body["current_version"] == "0.3.91"
        assert body["latest_remote_version"] == "0.3.92"
        assert body["last_update_check_at"] == "2026-05-31T12:00:00+00:00"
        assert body["last_update_error"] == ""
        assert body["backend_update_state"] == "update_available"
        assert body["backend_update_reason"] == "none"

    def test_update_status_returns_backend_only_and_ignores_extension_metadata(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def get_update_status(self) -> dict[str, object]:
                return {
                    "state": "update_available",
                    "auto_update_enabled": False,
                    "current_version": "0.3.91",
                    "latest_version": "0.3.92",
                    "latest_tag": "backend-v0.3.92",
                    "last_check_at": "2026-05-31T12:00:00+00:00",
                    "last_error": "",
                    "reason": "none",
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=FakeAutoUpdateService(),
        )
        client = TestClient(app)

        response = client.get(
            "/api/update-status?extension_version=0.3.92&extension_family=chrome",
            headers={
                "X-OpenBiliClaw-Extension-Version": "0.3.92",
                "X-OpenBiliClaw-Extension-Family": "chrome",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "backend": {
                "state": "update_available",
                "auto_update_enabled": False,
                "current_version": "0.3.91",
                "latest_version": "0.3.92",
                "latest_tag": "backend-v0.3.92",
                "last_check_at": "2026-05-31T12:00:00+00:00",
                "last_error": "",
                "reason": "none",
            }
        }

    def test_update_check_ignores_legacy_extension_metadata(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def __init__(self) -> None:
                self.calls = 0

            async def check_now(self) -> dict[str, object]:
                self.calls += 1
                return {
                    "state": "up_to_date",
                    "auto_update_enabled": False,
                    "current_version": "0.3.92",
                    "latest_version": "0.3.92",
                    "latest_tag": "backend-v0.3.92",
                    "last_check_at": "2026-05-31T12:00:00+00:00",
                    "last_error": "",
                    "reason": "none",
                }

        service = FakeAutoUpdateService()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=service,
        )
        client = TestClient(app)

        response = client.post(
            "/api/update/check?extension_version=0.3.92&extension_family=firefox",
            headers={"X-OpenBiliClaw-Extension-Version": "0.3.92"},
            json={"include_backend": True, "include_extension": True},
        )

        assert response.status_code == 200
        assert service.calls == 1
        assert response.json()["backend"]["state"] == "up_to_date"
        assert "extension" not in response.json()

    def test_update_apply_validates_backend_only_target_before_lock(self) -> None:
        from fastapi.testclient import TestClient

        class FakeAutoUpdateService:
            def __init__(self) -> None:
                self.apply_calls: list[dict[str, object]] = []

            async def request_apply(self, *, tag: str = "") -> tuple[int, dict[str, object]]:
                self.apply_calls.append({"tag": tag})
                return (
                    202,
                    {
                        "target": "backend",
                        "state": "applying",
                        "reason": "none",
                        "accepted": True,
                        "observe_via": "runtime-stream",
                    },
                )

        service = FakeAutoUpdateService()
        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            auto_update_service=service,
        )
        client = TestClient(app)

        accepted = client.post(
            "/api/update/apply?extension_version=0.3.92",
            json={"target": "backend", "tag": "backend-v0.3.92"},
        )
        rejected = client.post("/api/update/apply", json={"target": "extension"})

        assert accepted.status_code == 202
        assert accepted.json()["state"] == "applying"
        assert service.apply_calls == [{"tag": "backend-v0.3.92"}]
        assert rejected.status_code == 422
        assert service.apply_calls == [{"tag": "backend-v0.3.92"}]

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

    def test_runtime_stream_accepts_and_discards_extension_metadata(self) -> None:
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

        with client.websocket_connect(
            "/api/runtime-stream?extension_version=0.3.92&extension_family=chrome"
        ) as websocket:
            websocket.send_json(
                {
                    "type": "extension_metadata",
                    "extension_version": "0.3.92",
                    "extension_family": "chrome",
                }
            )
            asyncio.run(
                hub.publish(
                    {
                        "type": "backend_update_available",
                        "latest_tag": "backend-v0.3.92",
                    }
                )
            )
            assert websocket.receive_json() == {
                "type": "backend_update_available",
                "latest_tag": "backend-v0.3.92",
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
            def get_recommendations(
                self, limit: int = 20, *, exclude_processed: bool = False
            ) -> list[dict[str, object]]:
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

    def test_empty_pool_append_and_reshuffle_skip_recommendation_path_and_debounce_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        import openbiliclaw.api.app as app_module

        monkeypatch.setattr(app_module.time, "monotonic", lambda: 1000.0)

        class FakeDatabase:
            def count_pool_candidates(self) -> int:
                return 0

        class FakeSoulEngine:
            def __init__(self) -> None:
                self.profile_calls = 0

            async def get_profile(self) -> dict[str, object]:
                self.profile_calls += 1
                raise AssertionError("empty pool should not load the profile")

        class FakeRecommendationEngine:
            def __init__(self) -> None:
                self.calls = 0

            async def reshuffle_recommendations(
                self, *, profile: object, limit: int = 10
            ) -> list[object]:
                self.calls += 1
                raise AssertionError("empty pool should not call reshuffle")

            async def append_recommendations(
                self, *, profile: object, excluded_bvids: list[str], limit: int = 10
            ) -> list[object]:
                self.calls += 1
                raise AssertionError("empty pool should not call append")

        class FakeRuntimeController:
            def __init__(self) -> None:
                self.trigger_calls = 0

            def get_runtime_status(self) -> dict[str, object]:
                return {"pool_available_count": 0}

            async def trigger_manual_refresh(self) -> dict[str, object]:
                self.trigger_calls += 1
                return {"accepted": True, "state": "running", "reason": "started"}

        soul = FakeSoulEngine()
        rec = FakeRecommendationEngine()
        runtime = FakeRuntimeController()
        app = create_app(
            memory_manager=object(),
            database=FakeDatabase(),
            soul_engine=soul,
            recommendation_engine=rec,
            runtime_controller=runtime,
        )
        client = TestClient(app)

        reshuffle = client.post("/api/recommendations/reshuffle")
        append = client.post(
            "/api/recommendations/append",
            json={"excluded_bvids": ["BV1OLD"]},
        )

        assert reshuffle.status_code == 200
        assert reshuffle.json() == {"items": []}
        assert append.status_code == 200
        assert append.json() == {"items": []}
        assert soul.profile_calls == 0
        assert rec.calls == 0
        assert runtime.trigger_calls == 1

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

    def test_feedback_endpoint_accepts_dismiss(self) -> None:
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
                "feedback_type": "dismiss",
                "note": "",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "recommendation_id": 7,
            "feedback_type": "dismiss",
        }
        assert database.updated == [(7, "dismiss", "")]
        assert memory.events[0]["event_type"] == "feedback"
        assert memory.events[0]["metadata"]["feedback_type"] == "dismiss"
        assert "忽略了" in str(memory.events[0].get("context", ""))

    def test_feedback_endpoint_rejects_unknown_feedback_type(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, object] | None:
                return {"id": recommendation_id, "bvid": "BV1REC", "title": "x"}

        app = create_app(memory_manager=object(), database=FakeDatabase())
        client = TestClient(app)

        response = client.post(
            "/api/feedback",
            json={
                "recommendation_id": 7,
                "feedback_type": "spam",
                "note": "",
            },
        )

        assert response.status_code == 422

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

    def test_autostart_status_reports_intent_and_registration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.get("/api/autostart-status")

        assert response.status_code == 200
        assert response.json() == {
            "supported": True,
            "enabled": True,
            "registered": False,
            "can_manage": True,
            "platform": "darwin",
            "mechanism": "launchd",
            "manage_ollama": True,
            "ollama_required": False,
            "reason": "none",
            "detail": "开机自启动配置已开启，但系统自启动项缺失。",
        }

    def test_autostart_status_remote_is_readable_but_not_manageable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.get("/api/autostart-status", headers={"sec-fetch-site": "cross-site"})

        assert response.status_code == 200
        assert response.json()["can_manage"] is False

    def test_autostart_status_remote_hides_env_managed_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            guards,
            "active_env_managed_inputs",
            lambda loaded_cfg: ["GOOGLE_API_KEY", "OPENBILICLAW_LLM_OPENAI_API_KEY"],
        )
        monkeypatch.setattr(guards, "autostart_shadowed", lambda intended: False)
        monkeypatch.setattr(
            "openbiliclaw.runtime.ollama_supervisor.ollama_required",
            lambda loaded_cfg: False,
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.get("/api/autostart-status", headers={"sec-fetch-site": "cross-site"})

        assert response.status_code == 200
        body = response.json()
        assert body["can_manage"] is False
        assert body["reason"] == "local_only"
        assert "GOOGLE_API_KEY" not in body["detail"]
        assert "OPENBILICLAW_LLM_OPENAI_API_KEY" not in body["detail"]

    def test_config_response_includes_autostart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config

        cfg = Config()
        cfg.autostart.enabled = True
        cfg.autostart.manage_ollama = False
        monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
        app = create_app()
        client = TestClient(app)

        response = client.get("/api/config")

        assert response.status_code == 200
        assert response.json()["autostart"] == {"enabled": True, "manage_ollama": False}

    def test_autostart_apply_rejects_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: False
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 403
        assert response.json()["reason"] == "local_only"

    def test_autostart_apply_rejects_unsupported_runtime(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(
                False, False, "darwin", "none", reason="unsupported_docker_runtime"
            ),
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "unsupported_docker_runtime"
        assert response.json()["enabled"] is False
        assert response.json()["registered"] is False

    def test_autostart_apply_rejects_env_managed_enable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            guards, "active_env_managed_inputs", lambda loaded_cfg: ["GOOGLE_API_KEY"]
        )
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "env_managed"
        assert "GOOGLE_API_KEY" in response.json()["detail"]

    def test_autostart_apply_enable_writes_config_then_registers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        registered = False
        calls: list[str] = []

        def fake_register(loaded_cfg: object) -> None:
            nonlocal registered
            calls.append("register")
            registered = True

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "register", fake_register)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 200
        assert calls == ["register"]
        assert response.json()["enabled"] is True
        assert response.json()["registered"] is True
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

    def test_autostart_apply_disable_unregisters_before_writing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        registered = True
        calls: list[str] = []

        def fake_unregister() -> None:
            nonlocal registered
            calls.append(f"unregister_before_config={load_config().autostart.enabled}")
            registered = False

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "unregister", fake_unregister)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 200
        assert calls == ["unregister_before_config=True"]
        assert response.json()["enabled"] is False
        assert response.json()["registered"] is False
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_register_failure_rolls_back_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "register",
            lambda loaded_cfg: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "registration_failed"
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_unregister_failure_keeps_config_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, True, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "unregister",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 409
        assert response.json()["reason"] == "unregister_failed"
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

    def test_autostart_apply_shadowed_enable_does_not_register(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.config import load_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        (tmp_path / "runtime" / "config.local.toml").write_text(
            "[autostart]\nenabled = false\n",
            encoding="utf-8",
        )
        register_calls: list[object] = []
        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, False, "darwin", "launchd"),
        )
        monkeypatch.setattr(
            autostart,
            "register",
            lambda loaded_cfg: register_calls.append(loaded_cfg),
        )
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": True})

        assert response.status_code == 409
        assert response.json()["reason"] == "shadowed"
        assert register_calls == []
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is False

    def test_autostart_apply_disable_save_failure_reregisters_and_rolls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw import config as config_module
        from openbiliclaw.config import Config, load_config, save_config
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart import guards
        from openbiliclaw.runtime.autostart.base import AutostartStatus

        cfg = Config()
        cfg.autostart.enabled = True
        save_config(cfg, tmp_path / "runtime" / "config.toml", autostart_authoritative=True)
        registered = True
        calls: list[str] = []

        def fake_unregister() -> None:
            nonlocal registered
            calls.append("unregister")
            registered = False

        def fake_register(loaded_cfg: object) -> None:
            nonlocal registered
            calls.append("register")
            registered = True

        def fake_save(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(
            autostart,
            "status",
            lambda: AutostartStatus(True, registered, "darwin", "launchd"),
        )
        monkeypatch.setattr(autostart, "unregister", fake_unregister)
        monkeypatch.setattr(autostart, "register", fake_register)
        monkeypatch.setattr(guards, "active_env_managed_inputs", lambda loaded_cfg: [])
        monkeypatch.setattr(config_module, "save_config", fake_save)
        app = create_app()
        app.state.auth_gate.is_trusted_local = lambda request: True
        client = TestClient(app)

        response = client.post("/api/autostart/apply", json={"enabled": False})

        assert response.status_code == 503
        assert response.json()["reason"] == "unavailable"
        assert calls == ["unregister", "register"]
        assert registered is True
        assert load_config(tmp_path / "runtime" / "config.toml").autostart.enabled is True

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
        assert data["speculative_avoidances"] == []
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

    def test_profile_summary_endpoint_includes_speculative_avoidances(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )
        from openbiliclaw.soul.profile import OnionProfile

        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        experience_mode="knowledge",
                        entry_load="light",
                        confidence=0.66,
                        specifics=[
                            SpeculativeAvoidanceSpecific(name="标题党热点解读"),
                            SpeculativeAvoidanceSpecific(name="无信息增量复读"),
                        ],
                    ),
                    SpeculativeAvoidance(domain="已确认", status="confirmed"),
                ]
            ),
        )

        class FakeSoulEngine:
            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        data = response.json()
        assert data["speculative_avoidances"][0]["domain"] == "浅层热点复读"
        assert data["speculative_avoidances"][0]["source_mode"] == "negative_signal"
        assert [item["name"] for item in data["speculative_avoidances"][0]["specifics"]] == [
            "标题党热点解读",
            "无信息增量复读",
        ]

    def test_profile_summary_endpoint_includes_probe_mode_challenge_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.profile import OnionProfile
        from openbiliclaw.soul.speculator import (
            SpeculativeInterest,
            SpeculativeSpecific,
            SpeculativeState,
            save_speculative_state,
        )

        save_speculative_state(
            tmp_path,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="城市基础设施观察",
                        reason="从城市漫游兴趣桥接到更结构化的空间理解。",
                        confidence=0.67,
                        probe_mode="bridge",
                        specifics=[SpeculativeSpecific(name="地铁换乘设计")],
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            async def get_profile(self) -> OnionProfile:
                return OnionProfile()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        item = response.json()["speculative_interests"][0]
        assert item["domain"] == "城市基础设施观察"
        assert item["probe_mode"] == "bridge"
        assert item["challenge"] is True
        assert item["specifics"][0]["name"] == "地铁换乘设计"

    def test_pending_interest_probes_include_probe_mode_challenge_metadata(
        self,
        tmp_path: Path,
    ) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.soul.speculator import (
            SpeculativeInterest,
            SpeculativeState,
            save_speculative_state,
        )

        save_speculative_state(
            tmp_path,
            SpeculativeState(
                active=[
                    SpeculativeInterest(
                        domain="公共空间设计",
                        reason="这是从建筑美学延伸出去的横向试探。",
                        confidence=0.61,
                        probe_mode="lateral",
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            _speculator = object()

        app = create_app(
            soul_engine=FakeSoulEngine(),
            memory_manager=SimpleNamespace(load_cognition_updates=lambda: []),
            database=object(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/interest-probes/pending")

        assert response.status_code == 200
        assert response.json()["items"] == [
            {
                "domain": "公共空间设计",
                "reason": "这是从建筑美学延伸出去的横向试探。",
                "confidence": 0.61,
                "status": "active",
                "probe_mode": "lateral",
                "challenge": True,
            }
        ]

    def test_interest_probe_trigger_runtime_event_includes_probe_mode_challenge_metadata(
        self,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.runtime.events import RuntimeEventHub
        from openbiliclaw.runtime.refresh import ContinuousRefreshController

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "probed_domains": {},
                    "probed_axes": {},
                    "probed_distance_bands": {},
                }

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

        class FakeSpeculator:
            def get_active_speculations(self) -> list[object]:
                return [
                    SimpleNamespace(
                        domain="城市基础设施观察",
                        category="城市",
                        reason="从城市漫游兴趣桥接到空间系统理解。",
                        confidence=0.67,
                        weight=0.5,
                        experience_mode="wander_observe",
                        entry_load="light",
                        probe_mode="bridge",
                        specifics=[SimpleNamespace(name="地铁换乘设计")],
                    )
                ]

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._speculator = FakeSpeculator()

        memory = FakeMemoryManager()
        hub = RuntimeEventHub()
        runtime = ContinuousRefreshController(
            memory_manager=memory,
            database=object(),
            soul_engine=FakeSoulEngine(),
            discovery_engine=object(),
            recommendation_engine=object(),
            event_hub=hub,
        )
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=runtime.soul_engine,
            runtime_controller=runtime,
            runtime_event_hub=hub,
        )
        client = TestClient(app)

        with client.websocket_connect("/api/runtime-stream") as websocket:
            response = client.post("/api/interest-probes/trigger")

            assert response.status_code == 200
            event = websocket.receive_json()

        assert event["type"] == "interest.probe"
        assert event["domain"] == "城市基础设施观察"
        assert event["probe_mode"] == "bridge"
        assert event["challenge"] is True

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

    def test_interest_probe_confirm_from_profile_uses_profile_confirmed_source(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
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
                self.confirmed: list[tuple[str, str]] = []
                self._active = [SimpleNamespace(domain="建筑美学")]

            def get_active_speculations(self) -> list[object]:
                return list(self._active)

            def user_confirm_speculation(
                self,
                domain: str,
                *,
                confirmation_source: str = "probe_confirmed",
            ) -> bool:
                self.confirmed.append((domain, confirmation_source))
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
            json={"domain": "建筑美学", "response": "confirm", "surface": "profile"},
        )

        assert response.status_code == 200
        assert soul_engine._speculator.confirmed == [("建筑美学", "profile_confirmed")]

    def test_interest_probe_chat_strong_positive_direct_confirms(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(self, _message: str) -> str:
                return "懂，这就是你想看的那类。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
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
                self.confirmed: list[tuple[str, str]] = []
                self.rejected: list[tuple[str, int]] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="建筑美学")]

            def user_confirm_speculation(
                self,
                domain: str,
                *,
                confirmation_source: str = "probe_confirmed",
            ) -> bool:
                self.confirmed.append((domain, confirmation_source))
                return True

            def user_reject_speculation(
                self,
                domain: str,
                cooldown_days: int = 30,
            ) -> bool:
                self.rejected.append((domain, cooldown_days))
                return True

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "建筑美学",
                "response": "chat",
                "message": "这就是我想看的，以后多推这种",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == [("建筑美学", "chat_confirmed")]
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["response"] == "chat_confirmed"
        assert history[0]["classification"] == "strong_positive"
        assert history[0]["classifier"] == "keyword"
        assert history[0]["resulting_action"] == "confirmed"
        assert history[0]["raw_text_excerpt"] == "这就是我想看的，以后多推这种"

    def test_interest_probe_chat_weak_positive_records_without_confirming(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(self, _message: str) -> str:
                return "可以，先把它当作一个轻量方向。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
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
                self.confirmed: list[object] = []
                self.observed: list[object] = []
                self.rejected: list[object] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="城市基础设施观察")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.confirmed.append((_args, _kwargs))
                return True

            def observe(self, events: object) -> None:
                self.observed.append(events)

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.rejected.append((_args, _kwargs))
                return True

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "城市基础设施观察",
                "response": "chat",
                "message": "有点意思，可以看看",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == []
        assert speculator.observed == []
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["response"] == "weak_positive"
        assert history[0]["classification"] == "weak_positive"
        assert history[0]["resulting_action"] == "weak_positive_deferred"

    def test_interest_probe_chat_weak_positive_records_buffer_event(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class FakeDialogue:
            async def respond(self, _message: str) -> str:
                return "可以，先轻量试试。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
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
            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="城市基础设施观察")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                return True

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                return True

        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=FakeSpeculator()),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "城市基础设施观察",
                "response": "chat",
                "message": "有点意思，可以看看",
            },
        )

        assert response.status_code == 200
        buffer_state = memory.runtime_state["short_term_exploration_buffer"]
        assert buffer_state["entries"][0]["domain"] == "城市基础设施观察"
        assert buffer_state["entries"][0]["recent_evidence"][0]["source_event"] == (
            "weak_positive_chat"
        )

    def test_interest_probe_chat_classifier_failure_defaults_to_neutral(self) -> None:
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        class BrokenLLM:
            async def complete_with_core_memory(self, **_kwargs: object) -> object:
                raise RuntimeError("classifier unavailable")

        class FakeDialogue:
            async def respond(self, _message: str) -> str:
                return "我先理解成你还在犹豫。"

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {"probe_feedback_history": []}
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
                self.confirmed: list[object] = []
                self.rejected: list[object] = []

            def get_active_speculations(self) -> list[object]:
                return [SimpleNamespace(domain="抽象雕塑")]

            def user_confirm_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.confirmed.append((_args, _kwargs))
                return True

            def user_reject_speculation(self, *_args: object, **_kwargs: object) -> bool:
                self.rejected.append((_args, _kwargs))
                return True

        speculator = FakeSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=SimpleNamespace(_speculator=speculator),
            dialogue=FakeDialogue(),
            recommendation_engine=SimpleNamespace(_llm=BrokenLLM()),
        )
        client = TestClient(app)

        response = client.post(
            "/api/interest-probes/respond",
            json={
                "domain": "抽象雕塑",
                "response": "chat",
                "message": "先放着吧",
            },
        )

        assert response.status_code == 200
        assert speculator.confirmed == []
        assert speculator.rejected == []
        history = memory.runtime_state["probe_feedback_history"]
        assert isinstance(history, list)
        assert history[0]["classification"] == "neutral"
        assert history[0]["resulting_action"] == "none"

    def test_avoidance_probe_pending_returns_active_items(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )

        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        confidence=0.66,
                        specifics=[SpeculativeAvoidanceSpecific(name="标题党热点解读")],
                    ),
                    SpeculativeAvoidance(domain="已确认避雷", status="confirmed"),
                ]
            ),
        )

        class FakeSoulEngine:
            pass

        app = create_app(soul_engine=FakeSoulEngine(), memory_manager=object(), database=object())
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.get("/api/avoidance-probes/pending")

        assert response.status_code == 200
        assert response.json()["items"][0]["domain"] == "浅层热点复读"
        assert len(response.json()["items"]) == 1

    def test_avoidance_probe_confirm_adds_disliked_specific_topics(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )
        from openbiliclaw.soul.profile import OnionProfile

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        reason="用户可能想避开无信息增量的热点复读内容。",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        specifics=[
                            SpeculativeAvoidanceSpecific(name="标题党热点解读"),
                            SpeculativeAvoidanceSpecific(name="无信息增量复读"),
                        ],
                    )
                ]
            ),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = None

            async def get_profile(self) -> OnionProfile:
                soul_data = memory.get_layer("soul").data
                return OnionProfile.from_dict(soul_data) if soul_data else OnionProfile()

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "浅层热点复读", "response": "confirm"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "confirmed"
        disliked_topics = memory.get_layer("preference").data["disliked_topics"]
        assert "标题党热点解读" in disliked_topics
        assert "无信息增量复读" in disliked_topics
        assert "浅层热点复读" not in disliked_topics

        profile_response = client.get("/api/profile-summary")
        assert {item["domain"] for item in profile_response.json()["dislikes"]} >= {
            "标题党热点解读",
            "无信息增量复读",
        }

    def test_avoidance_probe_reject_does_not_add_disliked_topic(
        self,
        tmp_path: Path,
    ) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            save_avoidance_state,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(active=[SpeculativeAvoidance(domain="浅层热点复读")]),
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = None

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "浅层热点复读", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "rejected"
        assert "浅层热点复读" not in memory.get_layer("preference").data.get(
            "disliked_topics",
            [],
        )
        history = memory.load_discovery_runtime_state()["avoidance_probe_feedback_history"]
        assert history[0]["response"] == "reject"

    def test_stale_avoidance_probe_reject_does_not_record_feedback_history(self) -> None:
        from fastapi.testclient import TestClient

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.runtime_state: dict[str, object] = {
                    "avoidance_probe_feedback_history": [],
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

        class FakeAvoidanceSpeculator:
            def __init__(self) -> None:
                self.rejected: list[tuple[str, int]] = []

            def get_active_avoidances(self) -> list[object]:
                return []

            def user_reject_avoidance(self, domain: str, cooldown_days: int = 30) -> bool:
                self.rejected.append((domain, cooldown_days))
                return False

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = FakeAvoidanceSpeculator()

        memory = FakeMemoryManager()
        soul_engine = FakeSoulEngine()
        app = create_app(
            memory_manager=memory,
            database=object(),
            soul_engine=soul_engine,
        )
        client = TestClient(app)

        response = client.post(
            "/api/avoidance-probes/respond",
            json={"domain": "比赛话题里的情绪型切片复读", "response": "reject"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert memory.runtime_state["avoidance_probe_feedback_history"] == []
        assert soul_engine._avoidance_speculator.rejected == [("比赛话题里的情绪型切片复读", 30)]

    def test_avoidance_probe_confirm_schedules_dislike_writeback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import time

        from fastapi.testclient import TestClient

        from openbiliclaw.api import app as app_module
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.avoidance_speculator import (
            AvoidanceSpeculator,
            AvoidanceState,
            SpeculativeAvoidance,
            SpeculativeAvoidanceSpecific,
            save_avoidance_state,
        )

        memory = MemoryManager(tmp_path)
        memory.initialize()
        save_avoidance_state(
            tmp_path,
            AvoidanceState(
                active=[
                    SpeculativeAvoidance(
                        domain="浅层热点复读",
                        source_mode="negative_signal",
                        source_signal="thumbs_down",
                        specifics=[SpeculativeAvoidanceSpecific(name="标题党热点解读")],
                    )
                ]
            ),
        )
        embedding_service = object()
        calls: list[dict[str, object]] = []

        async def fake_apply_new_dislikes(**kwargs: object) -> list[str]:
            await asyncio.sleep(0.02)
            calls.append(dict(kwargs))
            return ["新增不喜欢方向: 标题党热点解读"]

        monkeypatch.setattr(
            app_module,
            "apply_new_dislikes",
            fake_apply_new_dislikes,
            raising=False,
        )

        class FakeSoulEngine:
            def __init__(self) -> None:
                self._avoidance_speculator = AvoidanceSpeculator(
                    llm_service=None,
                    data_dir=tmp_path,
                )
                self._embedding_service = embedding_service

        app = create_app(
            memory_manager=memory,
            database=memory._database,
            soul_engine=FakeSoulEngine(),
        )
        app.state.runtime_context.config = SimpleNamespace(data_path=tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/api/avoidance-probes/respond",
                json={"domain": "浅层热点复读", "response": "confirm"},
            )

            assert response.status_code == 200
            assert calls == []
            for _ in range(20):
                time.sleep(0.02)
                if calls:
                    break

        assert calls
        assert calls[0]["topics"] == ["标题党热点解读"]
        assert calls[0]["embedding_service"] is embedding_service
        history = memory.load_discovery_runtime_state()["avoidance_probe_feedback_history"]
        assert history[0]["response"] == "confirm"
        assert history[0]["source_mode"] == "negative_signal"

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

    def test_chat_turn_endpoint_records_avoidance_probe_scope_context(self, tmp_path: Path) -> None:
        import asyncio
        import time
        from types import SimpleNamespace

        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        class FakeDialogue:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def respond(self, user_message: str) -> str:
                self.messages.append(user_message)
                await asyncio.sleep(0.01)
                return "懂，这类你更像是在避开低信息密度。"

        class FakeAvoidanceSpeculator:
            def __init__(self) -> None:
                self.observed: list[object] = []

            def get_active_avoidances(self) -> list[object]:
                return [
                    SimpleNamespace(
                        domain="浅层热点复读",
                        reason="信息密度低",
                        source_mode="negative_signal",
                        source_signal="dislike",
                        specifics=[SimpleNamespace(name="标题党热点解读")],
                        experience_mode="knowledge",
                        entry_load="light",
                    )
                ]

            def observe(self, events: object) -> None:
                self.observed.append(events)

            def user_reject_avoidance(self, *_args: object, **_kwargs: object) -> bool:
                return True

        class FakeMemoryManager:
            def __init__(self) -> None:
                self.cognition_updates: list[object] = []
                self.runtime_state: dict[str, object] = {"avoidance_probe_feedback_history": []}

            def load_cognition_updates(self) -> list[object]:
                return list(self.cognition_updates)

            def save_cognition_updates(self, updates: list[object]) -> None:
                self.cognition_updates = list(updates)

            def load_discovery_runtime_state(self) -> dict[str, object]:
                return dict(self.runtime_state)

            def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
                self.runtime_state = dict(state)

        db = Database(tmp_path / "openbiliclaw.db")
        db.initialize()
        dialogue = FakeDialogue()
        speculator = FakeAvoidanceSpeculator()
        memory = FakeMemoryManager()
        app = create_app(
            memory_manager=memory,
            database=db,
            soul_engine=SimpleNamespace(_avoidance_speculator=speculator),
            dialogue=dialogue,
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/turns",
                json={
                    "turn_id": "turn-avoidance-1",
                    "session": "popup",
                    "scope": "avoidance_probe",
                    "subject_id": "浅层热点复读",
                    "subject_title": "浅层热点复读",
                    "message": "对，这类我不喜欢",
                },
            )
            assert response.status_code == 200

            turn = response.json()
            for _ in range(20):
                time.sleep(0.02)
                turn = client.get("/api/chat/turns/turn-avoidance-1").json()
                if turn["status"] == "completed":
                    break

            assert turn["status"] == "completed"
            assert turn["scope"] == "avoidance_probe"
            assert "关于避雷方向「浅层热点复读」的反馈" in dialogue.messages[0]
            assert speculator.observed

            history = client.get(
                "/api/chat/turns",
                params={"session": "popup", "scope": "avoidance_probe"},
            ).json()
            assert [item["turn_id"] for item in history["items"]] == ["turn-avoidance-1"]
            feedback_history = memory.runtime_state["avoidance_probe_feedback_history"]
            assert feedback_history[0]["response"] == "avoidance_chat_confirmed"

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

    def test_recommendation_click_endpoint_keeps_youtube_click_source_aware(self) -> None:
        """YouTube recommendation clicks must not be persisted as Bilibili URLs."""
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
                if recommendation_id != 42:
                    return None
                return {
                    "id": 42,
                    "bvid": "KPoJ7p9iy4Q",
                    "content_id": "KPoJ7p9iy4Q",
                    "content_url": "https://www.youtube.com/watch?v=KPoJ7p9iy4Q",
                    "source_platform": "youtube",
                    "title": "A YouTube deep dive",
                    "topic_label": "技术长视频",
                    "up_name": "YT Creator",
                }

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
            json={"recommendation_id": 42},
        )

        assert response.status_code == 200
        assert response.json()["bvid"] == "KPoJ7p9iy4Q"
        assert memory.events, "YouTube click should be persisted"
        event = memory.events[0]
        assert event["url"] == "https://www.youtube.com/watch?v=KPoJ7p9iy4Q"
        assert "YouTube" in event["context"]
        assert event["metadata"]["source_platform"] == "youtube"
        assert event["metadata"]["content_id"] == "KPoJ7p9iy4Q"
        assert event["metadata"]["content_url"] == "https://www.youtube.com/watch?v=KPoJ7p9iy4Q"

        signal = soul_engine.pipeline.ingested[0]
        assert signal.payload["source_platform"] == "youtube"
        assert signal.payload["content_id"] == "KPoJ7p9iy4Q"
        assert signal.payload["content_url"] == "https://www.youtube.com/watch?v=KPoJ7p9iy4Q"

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

    def test_delight_like_records_feedback_without_consuming_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "like"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "liked"
        assert database.notified == []
        assert any("feedback_type='like'" in query for query, _params in database.writes)

    def test_delight_chat_records_context_without_consuming_candidate(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.notified: list[str] = []

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        class FakeMemory:
            def __init__(self) -> None:
                self.updates: list[dict[str, object]] = []

            def load_cognition_updates(self) -> list[dict[str, object]]:
                return self.updates

            def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates

        class FakeDialogue:
            async def respond(self, message: str) -> str:
                assert "惊喜推荐" in message
                return "继续聊"

        database = FakeDatabase()
        app = create_app(
            memory_manager=FakeMemory(),
            database=database,
            soul_engine=object(),
            dialogue=FakeDialogue(),
        )
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={
                "bvid": "BV1DL",
                "title": "惊喜",
                "response": "chat",
                "message": "这个方向挺有意思",
            },
        )

        assert response.status_code == 200
        assert response.json()["action"] == "chat"
        assert database.notified == []

    def test_delight_dislike_marks_candidate_consumed(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "dislike"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "disliked"
        assert database.notified == ["BV1DL"]
        assert any("feedback_type='dislike'" in query for query, _params in database.writes)

    def test_delight_dismiss_marks_candidate_consumed(self) -> None:
        from fastapi.testclient import TestClient

        class FakeDatabase:
            def __init__(self) -> None:
                self.writes: list[tuple[str, tuple[object, ...]]] = []
                self.notified: list[str] = []

            def _execute_write(self, query: str, params: tuple[object, ...]) -> None:
                self.writes.append((query, params))

            def mark_delight_notified(self, bvid: str) -> None:
                self.notified.append(bvid)

        database = FakeDatabase()
        app = create_app(memory_manager=object(), database=database, soul_engine=object())
        client = TestClient(app)

        response = client.post(
            "/api/delight/respond",
            json={"bvid": "BV1DL", "title": "惊喜", "response": "dismiss"},
        )

        assert response.status_code == 200
        assert response.json()["action"] == "dismissed"
        assert database.notified == ["BV1DL"]
        assert database.writes == []

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
        cfg.scheduler.auto_update_allow_prerelease = True
        cfg.scheduler.auto_update_allowed_remotes = [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
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
        assert data["scheduler"]["auto_update_allow_prerelease"] is True
        assert data["scheduler"]["auto_update_allowed_remotes"] == [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
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
                    "auto_update_allow_prerelease": True,
                    "auto_update_allowed_remotes": [
                        "https://github.com/example/OpenBiliClaw.git",
                        "git@github.com:example/OpenBiliClaw.git",
                    ],
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
        assert cfg.scheduler.auto_update_allow_prerelease is True
        assert cfg.scheduler.auto_update_allowed_remotes == [
            "https://github.com/example/OpenBiliClaw.git",
            "git@github.com:example/OpenBiliClaw.git",
        ]
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


class TestProfileEditEndpoints:
    """End-to-end tests for the editable-profile API (real SoulEngine)."""

    def _client(self, tmp_path: Path) -> object:
        from fastapi.testclient import TestClient

        from openbiliclaw.llm.base import LLMResponse
        from openbiliclaw.memory.manager import MemoryManager
        from openbiliclaw.soul.engine import SoulEngine
        from openbiliclaw.soul.profile import (
            CoreLayer,
            InterestDomain,
            InterestLayer,
            OnionProfile,
        )

        class _Reg:
            async def complete(
                self,
                messages: list[dict[str, str]],
                *,
                temperature: float = 0.7,
                max_tokens: int = 4096,
                json_mode: bool = False,
                reasoning_effort: str | None = None,
                model: str | None = None,
            ) -> LLMResponse:
                return LLMResponse(content="{}", provider="openai")

        memory = MemoryManager(tmp_path / "data")
        memory.initialize()
        engine = SoulEngine(llm=_Reg(), memory=memory)
        profile = OnionProfile(
            core=CoreLayer(core_traits=["完美主义"]),
            interest=InterestLayer(
                likes=[InterestDomain(domain=f"领域{i}", weight=0.5) for i in range(14)],
                favorite_up_users=[f"UP{i}" for i in range(10)],
            ),
        )
        layer = memory.get_layer("soul")
        layer.data.clear()
        layer.data.update(profile.to_dict())
        layer.save()
        app = create_app(memory_manager=memory, database=memory._database, soul_engine=engine)
        return TestClient(app)

    def test_edit_state_returns_untruncated_fields(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.get("/api/profile/edit-state")  # type: ignore[attr-defined]
        assert resp.status_code == 200
        body = resp.json()
        assert body["initialized"] is True
        # un-truncated: summary caps likes at 12 / favorite_up at 8; edit-state must not
        assert len(body["fields"]["likes"]["domains"]) == 14
        assert len(body["fields"]["interest.favorite_up_users"]["items"]) == 10

    def test_edit_adds_dislike_and_reflects_in_returned_state(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "dislikes", "op": "add", "value": "营销号"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        domains = body["edit_state"]["fields"]["dislikes"]["domains"]
        assert any(d["domain"] == "营销号" and d["user_added"] for d in domains)

    def test_edit_invalid_target_returns_422(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "core.bogus", "op": "add", "value": "x"},
        )
        assert resp.status_code == 422

    def test_edit_set_then_reset_text_field(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "personality_portrait", "op": "set", "value": "我的画像"},
        )
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state["fields"]["personality_portrait"]["value"] == "我的画像"
        assert state["fields"]["personality_portrait"]["pinned"] is True

        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "personality_portrait", "op": "reset"},
        )
        state2 = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state2["fields"]["personality_portrait"]["pinned"] is False

    def test_edit_set_then_reset_scalar_field(self, tmp_path: Path) -> None:
        # Mirrors the slider UI path: scalar fields surface in edit-state and
        # round-trip through op=set (numeric, clamped 0..1) + op=reset.
        client = self._client(tmp_path)
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state["fields"]["surface.exploration_openness"]["type"] == "scalar"

        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "surface.exploration_openness", "op": "set", "value": 0.8},
        )
        assert resp.status_code == 200
        field = resp.json()["edit_state"]["fields"]["surface.exploration_openness"]
        assert field["pinned"] is True
        assert abs(field["value"] - 0.8) < 1e-9

        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "surface.exploration_openness", "op": "reset"},
        )
        state2 = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert state2["fields"]["surface.exploration_openness"]["pinned"] is False

    def test_profile_summary_carries_overrides_annotation(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "core.core_traits", "op": "add", "value": "务实"},
        )
        resp = client.get("/api/profile-summary")  # type: ignore[attr-defined]
        assert resp.status_code == 200
        overrides = resp.json()["overrides"]
        assert overrides["list_edits"]["core.core_traits"]["add"] == ["务实"]

    def test_summary_surfaces_user_added_item_past_display_cap(self, tmp_path: Path) -> None:
        # Seed has 10 favorite_up_users; the summary caps the list at 8. A
        # user-added entry lands past the cap and must still surface — it used
        # to show in edit mode (un-truncated) but get truncated out of the
        # read-only summary, reading as if the edit was lost.
        client = self._client(tmp_path)
        marker = "我亲手加的UP"
        resp = client.post(  # type: ignore[attr-defined]
            "/api/profile/edit",
            json={"target": "interest.favorite_up_users", "op": "add", "value": marker},
        )
        assert resp.status_code == 200
        state = client.get("/api/profile/edit-state").json()  # type: ignore[attr-defined]
        assert marker in state["fields"]["interest.favorite_up_users"]["items"]
        summary = client.get("/api/profile-summary").json()  # type: ignore[attr-defined]
        assert marker in summary["favorite_up_users"]


def test_cap_keeping_user_added_keeps_manual_entries_past_limit() -> None:
    """The summary display cap must never hide a user-added entry."""
    from openbiliclaw.api.app import _cap_keeping_user_added

    # 6 AI items + 1 user-added past the cap of 6 → user entry rides along.
    merged = ["a", "b", "c", "d", "e", "f", "用户加的"]
    assert _cap_keeping_user_added(merged, ["用户加的"], 6) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "用户加的",
    ]
    # No overrides → plain truncation (AI noise still capped).
    assert _cap_keeping_user_added(["a", "b", "c", "d", "e", "f", "g"], [], 6) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
    ]
    # Under the cap → unchanged.
    assert _cap_keeping_user_added(["a", "b"], ["a"], 6) == ["a", "b"]

    # Interest-domain objects via key=.
    class _D:
        def __init__(self, domain: str) -> None:
            self.domain = domain

    doms = [_D(x) for x in ["a", "b", "c", "d", "e", "f", "g", "h", "mine"]]
    out = _cap_keeping_user_added(doms, ["mine"], 8, key=lambda d: d.domain)
    assert any(d.domain == "mine" for d in out)
    assert len(out) == 9  # 8 head + 1 user-added


class _FakeInitPrereqs:
    """Controllable stand-in for InitPrereqs (E2 endpoint tests)."""

    def __init__(self, *, bili: str = "ok", chat: bool = True, platforms=None) -> None:
        self._bili = bili
        self._chat = chat
        self._platforms = list(platforms or [])

    async def bilibili_check(self) -> str:
        return self._bili

    async def chat_ready(self) -> bool:
        return self._chat

    def enabled_platforms(self) -> list[str]:
        return list(self._platforms)


def test_select_init_platforms_none_selection_uses_all_enabled() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    enabled = {"bilibili", "xiaohongshu", "douyin"}
    # None = no selection sent (CLI / legacy) → use everything enabled.
    assert _select_init_platforms(enabled, None) == enabled


def test_select_init_platforms_narrows_to_intersection() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    enabled = {"bilibili", "xiaohongshu", "douyin", "youtube"}
    assert _select_init_platforms(enabled, {"bilibili", "xiaohongshu"}) == {
        "bilibili",
        "xiaohongshu",
    }


def test_select_init_platforms_drops_unconfigured_selection() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    # A selected source that isn't enabled in config can't be init'd → dropped.
    assert _select_init_platforms({"bilibili", "xiaohongshu"}, {"bilibili", "douyin"}) == {
        "bilibili"
    }


def test_select_init_platforms_empty_selection_yields_empty() -> None:
    from openbiliclaw.api.app import _select_init_platforms

    # Explicit empty selection narrows to nothing optional (bilibili is the base,
    # pulled separately, so an empty effective set is fine).
    assert _select_init_platforms({"bilibili", "xiaohongshu"}, set()) == set()


class TestGuidedInitEndpoints:
    """E2: POST /api/init + POST /api/init/cancel (local-only, gui-init §2/§5b)."""

    def _make_app(self, tmp_path, *, profile_ready=False, prereqs=None):
        from openbiliclaw.storage.database import Database

        db = Database(tmp_path / "e2.db")
        db.initialize()
        soul = SimpleNamespace(is_profile_ready=lambda: profile_ready)
        app = create_app(memory_manager=object(), database=db, soul_engine=soul)
        app.state.auth_gate.is_trusted_local = lambda request: True
        if prereqs is not None:
            app.state.runtime_context._init_prereqs = prereqs
        return app, db

    def test_init_rejects_non_local(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 403
        assert resp.json()["error"] == "local_only"

    def test_init_rejects_docker_runtime(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        monkeypatch.setattr(
            "openbiliclaw.docker_runtime.is_running_in_container", lambda *a, **k: True
        )
        app, db = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "unsupported_runtime"
        # Rejected before reserving — no run row created at all.
        assert db.get_latest_init_run() is None

    def test_init_rejects_already_initialized_without_force(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, db = self._make_app(tmp_path, profile_ready=True)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_initialized"
        assert db.get_latest_init_run() is None

    def test_init_already_running_returns_409(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            # Seed AFTER startup reconcile (which would otherwise fail a stale
            # "starting" row) so the run is genuinely active at POST time.
            app.state.runtime_context.init_coordinator.try_start("existing")
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_running"

    def test_init_missing_bilibili_resets_to_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="failed", chat=True)
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "bilibili_not_logged_in"
        # Critical: the reserved run was rolled back, never left "starting".
        run = db.get_latest_init_run()
        assert run["status"] == "idle"
        assert run["error_reason"] == "bilibili_not_logged_in"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def test_init_missing_llm_resets_to_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        prereqs = _FakeInitPrereqs(bili="ok", chat=False)
        app, db = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "llm_not_ready"
        assert db.get_latest_init_run()["status"] == "idle"
        assert app.state.runtime_context.init_coordinator.init_active() is False

    def _capture_run_guided_init(self, monkeypatch):
        """Replace the shared pipeline with an async capture of its kwargs.

        The wrapper imports ``run_guided_init`` lazily from ``openbiliclaw.cli``,
        so patching it there intercepts the API path without running real work.
        """
        captured: dict[str, object] = {}

        async def _fake(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(discovery_error=False)

        monkeypatch.setattr("openbiliclaw.cli.run_guided_init", _fake)
        return captured

    def _drive_until(self, client, captured, key="include_xhs"):
        # Pump the portal loop so the background wrapper task reaches the
        # (mocked) run_guided_init call, then return its captured kwargs.
        import time

        for _ in range(100):
            if key in captured:
                break
            client.get("/api/init-status")
            time.sleep(0.02)
        return captured

    def test_init_honors_source_selection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(
            bili="ok", chat=True, platforms=["bilibili", "xiaohongshu", "douyin", "youtube"]
        )
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bilibili", "xiaohongshu"]})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        # Only the selected optional source is included, even though all 4 are
        # enabled in config.
        assert captured["include_xhs"] is True
        assert captured["include_dy"] is False
        assert captured["include_yt"] is False

    def test_init_without_sources_uses_all_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        prereqs = _FakeInitPrereqs(
            bili="ok", chat=True, platforms=["bilibili", "xiaohongshu", "douyin"]
        )
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            # No "sources" key → legacy behaviour: everything enabled.
            resp = client.post("/api/init", json={})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["include_xhs"] is True
        assert captured["include_dy"] is True
        assert captured["include_yt"] is False  # youtube not enabled in config

    def test_init_drops_selected_source_not_enabled_in_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        captured = self._capture_run_guided_init(monkeypatch)
        # douyin is selected but NOT enabled in config → backend intersects it
        # away (the extension separately warns the user; the API stays robust).
        prereqs = _FakeInitPrereqs(bili="ok", chat=True, platforms=["bilibili", "xiaohongshu"])
        app, _ = self._make_app(tmp_path, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.post("/api/init", json={"sources": ["bilibili", "douyin"]})
            assert resp.status_code == 202
            self._drive_until(client, captured)
        assert captured["include_dy"] is False
        assert captured["include_xhs"] is False

    def test_cancel_without_active_run_returns_409(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/init/cancel", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_running"

    def test_cancel_rejects_non_local(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        app.state.auth_gate.is_trusted_local = lambda request: False
        with TestClient(app) as client:
            resp = client.post("/api/init/cancel", json={})
        assert resp.status_code == 403
        assert resp.json()["error"] == "local_only"

    def test_write_endpoint_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/events", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_write_endpoint_allowed_when_idle(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            resp = client.post("/api/events", json={})
        # No active init → the init gate must NOT fire (a bad payload is 422,
        # never the 409 init_running short-circuit).
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_recommendation_click_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/recommendation-click", json={})
        # recommendation-click propagates events to the profile → gated.
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_soul_and_pool_writers_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Chat (dialogue learning), probe responses, and init-completed all
        # mutate soul/pool state and must be blocked while a run is active.
        gated_paths = [
            "/api/chat",
            "/api/chat/turns",
            "/api/delight/respond",
            "/api/interest-probes/respond",
            "/api/avoidance-probes/respond",
            "/api/init-completed",
        ]
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            for path in gated_paths:
                resp = client.post(path, json={})
                assert resp.status_code == 409, f"{path} not gated ({resp.status_code})"
                assert resp.json().get("error") == "init_running", path

    def test_cookie_same_value_is_noop_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import load_config

        app, _ = self._make_app(tmp_path)
        # Make the submitted cookie match the effective stored cookie so the
        # init-active branch treats it as a silent no-op (not a rebuild).
        AuthManager(data_dir=load_config().data_path).set_cookie("SESSDATA=same")
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post(
                "/api/bilibili/cookie", json={"cookie": "SESSDATA=same", "source": "test"}
            )
        # Same effective cookie during init → 200 no-op, no rebuild, no error.
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_cookie_changed_during_init_is_rejected(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post(
                "/api/bilibili/cookie", json={"cookie": "SESSDATA=different", "source": "test"}
            )
        # A genuinely different cookie during init is rejected (not silently
        # dropped, not a mid-init rebuild) so the user knows it didn't apply.
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_deny_by_default_gates_arbitrary_writer_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Deny-by-default: a mutating endpoint that isn't on the init allowlist
        # is 409'd during init even though it's not individually enumerated.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/watch-later", json={"bvid": "BV1xx"})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_dispatcher_kick_allowed_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # Init's own enqueue kicks /api/sources/<src>/kick — it must pass the
        # gate (the bootstrap protocol), so it's never the init 409.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/sources/xhs/kick", json={})
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_source_recipe_crud_not_bypassing_gate_via_kick_id(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # The bootstrap allowlist is exact-segment: a recipe whose id happens to
        # be "kick"/"task-result" (PUT /api/sources/kick) must NOT slip past the
        # init gate — only /api/sources/<source>/kick (4 segments) is allowed.
        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.put("/api/sources/kick", json={})
        assert resp.status_code == 409
        assert resp.json()["error"] == "init_running"

    def test_init_status_can_start_false_when_already_initialized(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        # E1 must mirror E2's already-initialized guard so they don't disagree.
        prereqs = _FakeInitPrereqs(bili="ok", chat=True)
        app, _ = self._make_app(tmp_path, profile_ready=True, prereqs=prereqs)
        with TestClient(app) as client:
            resp = client.get("/api/init-status")
        body = resp.json()
        assert body["initialized"] is True
        assert body["can_start"] is False
        assert body["reason"] == "already_initialized"

    def test_task_result_not_gated_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app, _ = self._make_app(tmp_path)
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.post("/api/sources/xhs/task-result", json={})
        # Init's own bootstrap collectors depend on task-results landing —
        # the writer gate must let them through (never 409 init_running).
        assert not (resp.status_code == 409 and resp.json().get("error") == "init_running")

    def test_recommendations_get_skips_serve_bootstrap_during_init(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from openbiliclaw.storage.database import Database

        served: list[int] = []

        class _FakeRec:
            async def serve(self, profile: object, limit: int = 10) -> list[object]:
                served.append(limit)
                return []

        class _FakeSoul:
            def is_profile_ready(self) -> bool:
                return True

            async def get_profile(self) -> object:
                return object()

        db = Database(tmp_path / "rec.db")
        db.initialize()
        # Pretend the pool has scored candidates so the empty-history bootstrap
        # would normally call serve() (a side-effecting write).
        db.count_pool_candidates = lambda **_kw: 5  # type: ignore[method-assign]
        app = create_app(memory_manager=object(), database=db, soul_engine=_FakeSoul())
        app.state.auth_gate.is_trusted_local = lambda request: True
        app.state.runtime_context.recommendation_engine = _FakeRec()
        with TestClient(app) as client:
            app.state.runtime_context.init_coordinator.try_start("active")
            resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        # serve() (writes recommendation rows + marks pool shown) must NOT run on
        # a half-built pool during init — the side-effecting GET is gated too.
        assert served == []
