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
