"""Tests for the backend API app."""

from __future__ import annotations

from openbiliclaw.api.app import create_app


class TestBackendAPI:
    """Route-level tests for the plugin backend API."""

    def test_health_endpoint_returns_ok(self) -> None:
        from fastapi.testclient import TestClient

        app = create_app()
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

        app = create_app()
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
