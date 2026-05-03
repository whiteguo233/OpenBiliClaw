"""Tests for the backend API app."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from openbiliclaw.api.app import create_app


class TestBackendAPI:
    """Route-level tests for the plugin backend API."""

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
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder

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
            ) -> None:
                self.registry = registry
                self.memory = memory
                self.usage_recorder = usage_recorder

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
            ) -> None:
                self.llm = llm
                self.memory = memory
                self.usage_recorder = usage_recorder

        class FakeRecommendationEngine:
            def __init__(
                self,
                *,
                llm: object,
                database: object,
                curator: object = None,
                embedding_service: object = None,
            ) -> None:
                self.llm = llm
                self.database = database

        class FakeRuntimeController:
            def __init__(self, **kwargs) -> None:
                captured["runtime_controller_kwargs"] = kwargs

        class FakeAccountSyncService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

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
            sources=SimpleNamespace(
                browser_cdp_url="",
                browser_headed=False,
                xiaohongshu=SimpleNamespace(
                    daily_search_budget=20,
                    daily_creator_budget=10,
                    task_interval_seconds=45,
                ),
            ),
            scheduler=SimpleNamespace(pool_target_count=300, account_sync_interval_hours=24),
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

        app_module.create_app()

        assert captured["bilibili_request_concurrency"] == 2
        assert captured["llm_evaluation_concurrency"] == 2
        assert captured["engine_concurrency"] is captured["controller"]
        assert all(item is captured["controller"] for item in captured["strategy_concurrency"])

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
        assert response.json() == {"status": "ok", "service": "openbiliclaw-api"}

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
                    base.append({
                        "id": i,
                        "bvid": f"BV原神{i}",
                        "title": f"原神 番外 {i}",
                        "up_name": "某 UP",
                        "cover_url": "",
                        "expression": "",
                        "topic": "游戏",
                        "presented": 0,
                        "franchise_key": "原神",
                    })
                base.append({
                    "id": 99,
                    "bvid": "BV番茄",
                    "title": "番茄炒蛋 5 分钟",
                    "up_name": "美食 UP",
                    "cover_url": "",
                    "expression": "",
                    "topic": "美食",
                    "presented": 0,
                    "franchise_key": "",
                })
                return base

        app = create_app(database=FakeDatabase())
        client = TestClient(app)

        response = client.get("/api/recommendations")
        assert response.status_code == 200
        items = response.json()["items"]
        franchise_count = sum(
            1 for it in items if str(it["title"]).startswith("原神")
        )
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
                        "impact": '画像里\u201c国际新闻 / 深度分析\u201d这条偏好会更靠前。',
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
            "建立判断确定性", "持续扩展理解边界", "在复杂信息里找到秩序感",
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
        assert response.json() == {
            "reply": "你更在意的是它背后的逻辑，还是事件本身的冲突感？"
        }

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
                self, recommendation_id: int,
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
                self, recommendation_id: int,
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
                self, recommendation_id: int,
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
                gemini=LLMProviderConfig(api_key="test-gemini-key", model="gemini-2.5-flash"),
                embedding=EmbeddingConfig(
                    provider="gemini",
                    model="gemini-embedding-001",
                    similarity_threshold=0.85,
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
        assert data["llm"]["gemini"]["api_key"] == "test-gemini-key"
        assert data["llm"]["gemini"]["model"] == "gemini-2.5-flash"

        # Embedding fields
        assert data["llm"]["embedding"]["provider"] == "gemini"
        assert data["llm"]["embedding"]["model"] == "gemini-embedding-001"
        assert data["llm"]["embedding"]["similarity_threshold"] == 0.85

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
                    "embedding": {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "similarity_threshold": 0.78,
                    },
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

        # Verify the embedding was updated on the config object
        assert cfg.llm.embedding.provider == "openai"
        assert cfg.llm.embedding.model == "text-embedding-3-small"
        assert cfg.llm.embedding.similarity_threshold == 0.78

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
