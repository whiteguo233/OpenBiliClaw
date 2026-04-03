"""Tests for the backend API app."""

from __future__ import annotations

import asyncio
from pathlib import Path

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
            def __init__(self, *, registry: object, memory: object) -> None:
                self.registry = registry
                self.memory = memory

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
            def __init__(self, *, llm_service: object, database: object, concurrency=None, embedding_service=None) -> None:
                captured["engine_concurrency"] = concurrency

            def register_strategy(self, strategy: object) -> None:
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
            def __init__(self, *, registry: object, memory: object) -> None:
                self.registry = registry
                self.memory = memory

        class FakeBilibiliClient:
            def __init__(self, *, cookie: str) -> None:
                self.cookie = cookie

        class FakeSoulEngine:
            def __init__(self, *, llm: object, memory: object) -> None:
                self.llm = llm
                self.memory = memory

        class FakeRecommendationEngine:
            def __init__(self, *, llm: object, database: object, curator: object = None, embedding_service: object = None) -> None:
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
            ) -> None:
                self.llm = llm
                self.soul_engine = soul_engine
                self.llm_service = llm_service
                self.session = session

        fake_config = SimpleNamespace(
            data_path=Path("/tmp/openbiliclaw-test-data"),
            bilibili=SimpleNamespace(cookie=""),
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

    def test_health_endpoint_returns_ok(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app(memory_manager=object(), database=object(), soul_engine=object())
        client = TestClient(app)

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "openbiliclaw-api"}

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
                assert limit == 20
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
        assert data["cognitive_style"] == ["会先看结构", "对证据比较敏感", "偏好把问题讲透", "不太吃空话"]
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
