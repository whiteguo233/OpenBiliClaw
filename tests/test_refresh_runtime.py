from __future__ import annotations

import asyncio
from datetime import datetime

from openbiliclaw.runtime.refresh import ContinuousRefreshController


class _FakeMemoryManager:
    def __init__(self, state: dict[str, object] | None = None) -> None:
        self.state = state or {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": "",
            "last_explore_refresh_at": "",
            "last_processed_event_id": 0,
            "last_notification_at": "",
            "last_discovered_count": 0,
            "last_replenished_count": 0,
            "recent_pool_topics": [],
        }
        self.layers = {"soul": type("Layer", (), {"data": {"personality_portrait": "ready"}})()}

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self.state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self.state = dict(state)

    def get_layer(self, name: str) -> object:
        return self.layers[name]


class _FakeDatabase:
    def __init__(
        self,
        events: list[dict[str, object]],
        *,
        pool_count: int = 30,
        source_counts: dict[str, int] | None = None,
    ) -> None:
        self.events = events
        self.pool_count = pool_count
        self.source_counts = source_counts or {}
        self.recommendations = [
            {"id": 1, "presented": 0},
            {"id": 2, "presented": 1},
        ]

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, object]]:
        return [
            event
            for event in self.events
            if int(event["id"]) > after_event_id and str(event["event_type"]) in event_types
        ]

    def get_latest_event_id(self) -> int:
        if not self.events:
            return 0
        return max(int(event["id"]) for event in self.events)

    def count_recommendations(self) -> int:
        return len(self.recommendations)

    def count_unread_recommendations(self) -> int:
        return sum(1 for row in self.recommendations if not int(row["presented"]))

    def count_pool_candidates(self) -> int:
        return self.pool_count

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        return dict(self.source_counts)

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        return 0

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        return 0

class _FakeSoulEngine:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _FakeDiscoveryEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], list[str] | None, int]] = []

    async def discover(
        self,
        profile: dict[str, object],
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        self.calls.append((profile, strategies, limit))
        return [{"bvid": "BV1X", "relevance_score": 0.9, "view_count": 100}]


class _FakeRecommendationEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, object]], dict[str, object], int]] = []
        self.pool_copy_calls: list[tuple[dict[str, object], int]] = []

    async def generate_recommendations(
        self,
        discovered: list[dict[str, object]] | None,
        profile: dict[str, object],
        limit: int = 10,
    ) -> list[dict[str, object]]:
        self.calls.append((discovered or [], profile, limit))
        return [{"recommendation_id": 1}]

    async def precompute_pool_copy(
        self,
        *,
        profile: dict[str, object],
        limit: int,
    ) -> int:
        self.pool_copy_calls.append((profile, limit))
        return limit


class _FakeEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


async def test_refresh_controller_triggers_event_refresh_when_signal_threshold_reached() -> None:
    now = datetime.now().isoformat()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": now,
                "last_explore_refresh_at": now,
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "view"},
                {"id": 4, "event_type": "favorite"},
                {"id": 5, "event_type": "comment"},
                {"id": 6, "event_type": "feedback"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    assert result["strategies"] == ["search", "related_chain"]


async def test_refresh_controller_publishes_refresh_lifecycle_events() -> None:
    event_hub = _FakeEventHub()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "favorite"},
                {"id": 4, "event_type": "comment"},
                {"id": 5, "event_type": "feedback"},
                {"id": 6, "event_type": "view"},
            ],
            pool_count=20,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.refresh_if_needed()

    event_types = [event["type"] for event in event_hub.events]
    assert "refresh.started" in event_types
    assert "refresh.strategy" in event_types
    assert "refresh.pool_updated" in event_types


async def test_refresh_controller_backfills_pool_copy_after_replenishment() -> None:
    database = _FakeDatabase(
        [
            {"id": 1, "event_type": "view"},
            {"id": 2, "event_type": "search"},
            {"id": 3, "event_type": "favorite"},
            {"id": 4, "event_type": "comment"},
            {"id": 5, "event_type": "feedback"},
            {"id": 6, "event_type": "view"},
        ],
        pool_count=20,
    )
    recommendations = _FakeRecommendationEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=recommendations,
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.refresh_if_needed()

    assert recommendations.pool_copy_calls == [({"profile": "ok"}, 60)]


async def test_refresh_controller_reports_zero_replenishment_without_false_positive_copy() -> None:
    event_hub = _FakeEventHub()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "favorite"},
                {"id": 4, "event_type": "comment"},
                {"id": 5, "event_type": "feedback"},
                {"id": 6, "event_type": "view"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.force_refresh()

    pool_updated = next(
        event for event in event_hub.events if event["type"] == "refresh.pool_updated"
    )
    # force_refresh runs two phases: each returns 1 item from fake engine
    assert pool_updated["last_discovered_count"] == 2
    assert pool_updated["last_replenished_count"] == 0
    assert pool_updated["message"] == "\u8fd9\u8f6e\u627e\u5230\u4e86\u5185\u5bb9\uff0c\u4f46\u53ef\u7acb\u5373\u6362\u7684\u5e93\u5b58\u6ca1\u53d8"


async def test_refresh_controller_tracks_discovered_count_when_net_pool_does_not_grow() -> None:
    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "favorite"},
                {"id": 4, "event_type": "comment"},
                {"id": 5, "event_type": "feedback"},
                {"id": 6, "event_type": "view"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.force_refresh()

    assert memory.state["last_discovered_count"] == 2
    assert memory.state["last_replenished_count"] == 0


async def test_refresh_controller_skips_when_threshold_not_met() -> None:
    discovery = _FakeDiscoveryEngine()
    recommendations = _FakeRecommendationEngine()
    now = datetime.now().isoformat()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": now,
                "last_explore_refresh_at": now,
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=recommendations,
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is False
    assert discovery.calls == []
    assert recommendations.calls == []


async def test_force_refresh_runs_even_when_threshold_not_met() -> None:
    discovery = _FakeDiscoveryEngine()
    recommendations = _FakeRecommendationEngine()
    now = datetime.now().isoformat()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": now,
                "last_explore_refresh_at": now,
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=recommendations,
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert set(result["strategies"]) == {"search", "trending", "related_chain", "explore"}
    assert len(discovery.calls) == 2  # Two phases: search+trending, related_chain+explore
    assert recommendations.calls == []
    assert result["recommendation_count"] == 0


async def test_refresh_controller_requests_discovery_with_backfill_limit() -> None:
    discovery = _FakeDiscoveryEngine()
    now = datetime.now().isoformat()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": now,
                "last_explore_refresh_at": now,
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "view"},
                {"id": 4, "event_type": "favorite"},
                {"id": 5, "event_type": "comment"},
                {"id": 6, "event_type": "feedback"},
            ],
            pool_count=30,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.refresh_if_needed()

    assert discovery.calls[0][2] == 30  # discovery_limit default


async def test_refresh_controller_caps_single_discovery_backfill_request() -> None:
    discovery = _FakeDiscoveryEngine()
    now = datetime.now().isoformat()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": now,
                "last_explore_refresh_at": now,
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "view"},
                {"id": 4, "event_type": "favorite"},
                {"id": 5, "event_type": "comment"},
                {"id": 6, "event_type": "feedback"},
            ],
            pool_count=0,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    await controller.refresh_if_needed()

    assert discovery.calls[0][2] == 60


async def test_refresh_controller_replenishes_until_pool_reaches_target() -> None:
    class GrowingDiscovery(_FakeDiscoveryEngine):
        def __init__(self, database: _FakeDatabase) -> None:
            super().__init__()
            self.database = database

        async def discover(
            self,
            profile: dict[str, object],
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            self.calls.append((profile, strategies, limit))
            # All strategies run in one call now
            self.database.pool_count += 12
            return [
                {
                    "bvid": "BV-all",
                    "relevance_score": 0.8,
                    "source_strategy": "explore",
                }
            ]

    database = _FakeDatabase(
        [
            {"id": 1, "event_type": "view"},
            {"id": 2, "event_type": "search"},
            {"id": 3, "event_type": "favorite"},
            {"id": 4, "event_type": "comment"},
            {"id": 5, "event_type": "feedback"},
            {"id": 6, "event_type": "view"},
        ],
        pool_count=20,
    )
    discovery = GrowingDiscovery(database)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    # First phase (search+trending) already fills pool to target, second phase skipped
    assert "search" in result["strategies"]
    assert "trending" in result["strategies"]
    assert database.pool_count >= 30
    assert result["recommendation_count"] == 0


async def test_refresh_controller_prioritizes_underfilled_sources() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [
                {"id": 1, "event_type": "view"},
                {"id": 2, "event_type": "search"},
                {"id": 3, "event_type": "favorite"},
                {"id": 4, "event_type": "comment"},
                {"id": 5, "event_type": "feedback"},
                {"id": 6, "event_type": "view"},
            ],
            pool_count=24,
            source_counts={
                "search": 2,
                "related_chain": 4,
                "trending": 0,
                "explore": 18,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        discovery_limit=4,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    assert discovery.calls == [
        ({"profile": "ok"}, ["search", "related_chain"], 10),
        ({"profile": "ok"}, ["trending"], 6),
    ]


async def test_trigger_manual_refresh_sets_running_state() -> None:
    class SlowDiscovery(_FakeDiscoveryEngine):
        async def discover(
            self,
            profile: dict[str, object],
            strategies: list[str] | None = None,
            limit: int = 30,
        ) -> list[dict[str, object]]:
            await asyncio.sleep(0.01)
            return await super().discover(profile, strategies, limit)

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=30),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=SlowDiscovery(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.trigger_manual_refresh()

    assert result["accepted"] is True
    assert result["state"] == "running"
    status = controller.get_runtime_status()
    assert status["manual_refresh_state"] == "running"

    await asyncio.sleep(0.05)
    status = controller.get_runtime_status()
    assert status["manual_refresh_state"] == "success"
