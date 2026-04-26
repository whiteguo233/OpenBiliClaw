from __future__ import annotations

import asyncio
from contextlib import suppress
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
        delight_candidate: dict[str, object] | None = None,
        delight_count: int = 0,
    ) -> None:
        self.events = events
        self.pool_count = pool_count
        self.source_counts = source_counts or {}
        self.delight_candidate = delight_candidate
        self.delight_count = delight_count
        self.count_delight_thresholds: list[float] = []
        self.get_delight_thresholds: list[float] = []
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

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        return 0

    def trim_pool_to_target_count(self, *, target: int) -> int:
        if self.pool_count <= target:
            return 0
        trimmed = self.pool_count - target
        self.pool_count = target
        return trimmed

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        return 0

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> dict[str, object] | None:
        self.get_delight_thresholds.append(min_delight_score)
        return self.delight_candidate

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        self.get_delight_thresholds.append(min_delight_score)
        if self.delight_candidate is None:
            return []
        return [self.delight_candidate]

    def mark_delight_notified(self, bvid: str) -> None:
        pass

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        self.count_delight_thresholds.append(min_delight_score)
        return self.delight_count


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


class _FakeSpeculation:
    def __init__(
        self,
        *,
        domain: str,
        category: str = "",
        reason: str = "",
        confidence: float = 0.4,
        weight: float = 0.4,
        confirmation_count: int = 0,
        experience_mode: str = "",
        entry_load: str = "",
        specifics: list[object] | None = None,
    ) -> None:
        self.domain = domain
        self.category = category
        self.reason = reason
        self.confidence = confidence
        self.weight = weight
        self.confirmation_count = confirmation_count
        self.experience_mode = experience_mode
        self.entry_load = entry_load
        self.specifics = specifics or []


class _FakeSpeculator:
    def __init__(self, specs: list[_FakeSpeculation]) -> None:
        self._specs = specs

    def get_active_speculations(self) -> list[_FakeSpeculation]:
        return list(self._specs)


async def test_refresh_controller_falls_back_to_full_plan_when_below_target() -> None:
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
            pool_count=20,
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
    assert set(result["strategies"]) == {"search", "trending", "related_chain", "explore"}


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


async def test_refresh_controller_uses_shared_delight_threshold_for_runtime_queries() -> None:
    database = _FakeDatabase(
        [],
        delight_candidate={
            "bvid": "BV1DELIGHT",
            "title": "惊喜候选",
            "delight_reason": "这条会戳到你最近那股想把问题想透的劲头。",
            "delight_score": 0.72,
            "delight_hook": "意外击中",
            "cover_url": "https://example.com/cover.jpg",
        },
        delight_count=2,
    )
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    status = controller.get_runtime_status()
    pending = controller.get_pending_delight()

    assert status["pending_delight_count"] == 2
    assert pending is not None
    assert database.count_delight_thresholds == [0.70]
    assert database.get_delight_thresholds == [0.70]


async def test_refresh_controller_prepares_delight_candidates_without_refresh() -> None:
    recommendations = _FakeRecommendationEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=recommendations,
    )

    prepared = await controller.prepare_delight_candidates()

    assert prepared == 0
    assert recommendations.pool_copy_calls == [({"profile": "ok"}, 0)]


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

    await controller.force_refresh()

    pool_updated = next(
        event for event in event_hub.events if event["type"] == "refresh.pool_updated"
    )
    # force_refresh runs two phases: each returns 1 item from fake engine
    assert pool_updated["last_discovered_count"] == 2
    assert pool_updated["last_replenished_count"] == 0
    assert pool_updated["message"] == (
        "\u8fd9\u8f6e\u627e\u5230\u4e86\u5185\u5bb9\uff0c"
        "\u4f46\u53ef\u7acb\u5373\u6362\u7684\u5e93\u5b58\u6ca1\u53d8"
    )


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
            pool_count=20,
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


async def test_refresh_controller_skips_when_pool_at_cap() -> None:
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
    assert result["reason"] == "pool_at_cap"
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
            pool_count=20,
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
            pool_count=20,
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
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=20),
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


async def test_publish_interest_probe_skips_recent_axis_repeat() -> None:
    event_hub = _FakeEventHub()
    memory = _FakeMemoryManager(
        {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": "",
            "last_explore_refresh_at": "",
            "last_processed_event_id": 0,
            "last_notification_at": "",
            "last_discovered_count": 0,
            "last_replenished_count": 0,
            "recent_pool_topics": [],
            "probed_domains": {},
            "probed_axes": {"knowledge|heavy": datetime.now().isoformat()},
        }
    )

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [
                    _FakeSpeculation(
                        domain="量子物理",
                        reason="偏结构化理解。",
                        weight=0.9,
                        experience_mode="knowledge",
                        entry_load="heavy",
                    ),
                    _FakeSpeculation(
                        domain="城市漫游",
                        reason="能从场景里看结构。",
                        weight=0.5,
                        experience_mode="wander_observe",
                        entry_load="light",
                    ),
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    await controller._publish_interest_probe_if_available()

    probe_events = [event for event in event_hub.events if event["type"] == "interest.probe"]
    assert len(probe_events) == 1
    assert probe_events[0]["domain"] == "城市漫游"


# ===========================================================================
# Pool cap — hard upper bound on replenishment
# ===========================================================================


async def test_refresh_if_needed_skips_when_pool_at_cap() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=30),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    result = await controller.refresh_if_needed()

    assert result == {"refreshed": False, "strategies": [], "reason": "pool_at_cap"}
    assert discovery.calls == []


async def test_force_refresh_skips_when_pool_at_cap() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=30),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    result = await controller.force_refresh()

    assert result == {"refreshed": False, "strategies": [], "reason": "pool_at_cap"}
    assert discovery.calls == []


async def test_refresh_trims_pool_overflow_before_skipping() -> None:
    database = _FakeDatabase([], pool_count=50)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    result = await controller.refresh_if_needed()

    assert result["reason"] == "pool_at_cap"
    assert database.pool_count == 30  # trimmed back down to target


async def test_run_refresh_plan_stops_midway_when_cap_hit() -> None:
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
            self.database.pool_count += 15
            return [{"bvid": "BV-x", "relevance_score": 0.5}]

    database = _FakeDatabase([], pool_count=20)
    discovery = GrowingDiscovery(database)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    await controller.force_refresh()

    # First phase pushes pool to 35 (>= 30), second phase skipped.
    assert len(discovery.calls) == 1
    assert database.pool_count >= 30


# ===========================================================================
# Pipeline tick wiring — verifies the refresh loop drives ProfileUpdatePipeline.tick()
# ===========================================================================


class _SpyPipeline:
    """Records every call to tick() so the runtime test can assert wiring."""

    def __init__(self) -> None:
        self.tick_calls: int = 0

    async def tick(self) -> None:
        self.tick_calls += 1


class _BrokenPipeline:
    async def tick(self) -> None:
        raise RuntimeError("pipeline tick simulated failure")


class _FakeSoulEngineWithPipeline:
    def __init__(self, pipeline: object | None) -> None:
        self.pipeline = pipeline

    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


def _build_minimal_controller(
    soul_engine: object,
) -> ContinuousRefreshController:
    """Build a controller with the minimum scaffolding needed to call _tick_soul_pipeline."""
    return ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=soul_engine,  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )


async def test_runtime_tick_helper_invokes_pipeline_tick() -> None:
    """_tick_soul_pipeline should call soul_engine.pipeline.tick() once."""
    spy = _SpyPipeline()
    engine = _FakeSoulEngineWithPipeline(spy)
    controller = _build_minimal_controller(engine)

    await controller._tick_soul_pipeline()
    assert spy.tick_calls == 1

    await controller._tick_soul_pipeline()
    assert spy.tick_calls == 2


async def test_runtime_tick_helper_no_pipeline_attribute_is_noop() -> None:
    """If the soul engine has no .pipeline, the helper should silently no-op."""
    engine = _FakeSoulEngine()  # original fake — no .pipeline
    controller = _build_minimal_controller(engine)

    # Should not raise
    await controller._tick_soul_pipeline()


async def test_runtime_tick_helper_pipeline_without_tick_is_noop() -> None:
    """If pipeline exists but lacks a tick() method, helper should no-op."""

    class _NoTickPipeline:
        pass

    engine = _FakeSoulEngineWithPipeline(_NoTickPipeline())
    controller = _build_minimal_controller(engine)

    # Should not raise
    await controller._tick_soul_pipeline()


async def test_run_forever_drives_pipeline_tick_and_refresh() -> None:
    """Single iteration of run_forever should call BOTH refresh_if_needed AND tick."""
    spy = _SpyPipeline()
    engine = _FakeSoulEngineWithPipeline(spy)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=engine,  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        check_interval_seconds=3600,  # long sleep so we can cancel cleanly
    )

    # Run one full iteration of the loop and cancel the second sleep
    task = asyncio.create_task(controller.run_forever())
    # Yield enough times for the first iteration to complete and reach asyncio.sleep
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert spy.tick_calls >= 1, (
        f"Expected pipeline.tick() to be called at least once. Got: {spy.tick_calls}"
    )


async def test_run_forever_continues_when_pipeline_tick_raises() -> None:
    """A failing pipeline.tick() must not break the refresh loop."""
    broken = _BrokenPipeline()
    engine = _FakeSoulEngineWithPipeline(broken)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=engine,  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        check_interval_seconds=3600,
    )

    task = asyncio.create_task(controller.run_forever())
    for _ in range(20):
        await asyncio.sleep(0)
    # Loop should still be alive — neither cancelled nor exception-killed
    assert not task.done(), "run_forever must absorb pipeline.tick() exceptions and keep looping"
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_run_forever_continues_when_refresh_raises() -> None:
    """A failing refresh_if_needed() must not break the loop or block tick()."""
    spy = _SpyPipeline()
    engine = _FakeSoulEngineWithPipeline(spy)

    class _BrokenMemory(_FakeMemoryManager):
        def load_discovery_runtime_state(self) -> dict[str, object]:
            raise RuntimeError("memory broken")

    controller = ContinuousRefreshController(
        memory_manager=_BrokenMemory(),
        database=_FakeDatabase(events=[]),
        soul_engine=engine,  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        check_interval_seconds=3600,
    )

    task = asyncio.create_task(controller.run_forever())
    for _ in range(20):
        await asyncio.sleep(0)
    # tick() should still have been called even though refresh raised
    assert spy.tick_calls >= 1
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_run_forever_cancels_child_loops_on_shutdown() -> None:
    """Cancelling the parent refresh task must cancel spawned child loops too."""
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        check_interval_seconds=3600,
    )

    started = {name: asyncio.Event() for name in ("refresh", "soul", "xhs", "push")}
    cancelled = {name: asyncio.Event() for name in started}
    spawned_tasks: list[asyncio.Task[None]] = []

    def make_loop(name: str):
        async def loop() -> None:
            task = asyncio.current_task()
            if task is not None:
                spawned_tasks.append(task)
            started[name].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled[name].set()

        return loop

    controller._loop_refresh = make_loop("refresh")  # type: ignore[method-assign]
    controller._loop_soul_pipeline = make_loop("soul")  # type: ignore[method-assign]
    controller._loop_xhs_producer = make_loop("xhs")  # type: ignore[method-assign]
    controller._loop_proactive_push = make_loop("push")  # type: ignore[method-assign]

    task = asyncio.create_task(controller.run_forever())
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=0.5,
        )
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in cancelled.values())),
            timeout=0.5,
        )
    finally:
        for child in spawned_tasks:
            child.cancel()
        for child in spawned_tasks:
            with suppress(asyncio.CancelledError):
                await child
