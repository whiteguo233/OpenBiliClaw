"""Tests for the backend API app."""

from __future__ import annotations

from openbiliclaw.api.app import create_app


class TestBackendAPI:
    """Route-level tests for the plugin backend API."""

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
                }

        app = create_app(
            memory_manager=object(),
            database=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
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
            "manual_refresh_state": "idle",
            "manual_refresh_message": "",
        }

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
                        "notified": False,
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

        response = client.get("/api/profile-summary")

        assert response.status_code == 200
        assert response.json() == {
            "initialized": True,
            "personality_portrait": "这是一个喜欢把问题想透、信息密度偏高的用户。",
            "core_traits": ["理性", "好奇"],
            "deep_needs": ["理解世界", "持续成长"],
            "top_interests": ["国际新闻", "深度分析"],
            "recent_cognition_updates": [
                "阿B 现在更确定你会吃国际时事深拆这一口。",
                "我对你又对上了一点：你不是只看热闹的人。",
            ],
        }

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
