from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from types import SimpleNamespace

import pytest

from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.runtime.events import RuntimeEventHub
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.storage.database import Database

_MULTI_SOURCE_SHARES = {"bilibili": 8, "xiaohongshu": 1, "douyin": 1}


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _seed_visible_pool_row(
    db: Database,
    bvid: str,
    *,
    source: str = "search",
    topic_group: str = "测试分组",
    relevance_score: float = 0.5,
) -> None:
    db.cache_content(
        bvid,
        title=bvid,
        up_name="UP",
        source=source,
        relevance_score=relevance_score,
        pool_expression="测试推荐文案",
        pool_topic_label="测试主题",
        style_key="tutorial",
        topic_group=topic_group,
    )


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
        source_available_counts: dict[str, int] | None = None,
        source_raw_counts: dict[str, int] | None = None,
        pool_raw_count: int | None = None,
        pool_pending_count: int = 0,
        discovery_status_counts: dict[str, int] | None = None,
        reactivate_pool_count: int = 0,
        delight_candidate: dict[str, object] | None = None,
        delight_count: int = 0,
    ) -> None:
        self.events = events
        self.pool_count = pool_count
        self.pool_raw_count = pool_raw_count
        self.pool_pending_count = pool_pending_count
        self.discovery_status_counts = dict(discovery_status_counts or {})
        self.source_counts = source_counts or {}
        self.source_available_counts = (
            dict(source_available_counts)
            if source_available_counts is not None
            else dict(self.source_counts)
        )
        self.source_raw_counts = (
            dict(source_raw_counts) if source_raw_counts is not None else dict(self.source_counts)
        )
        self.reactivate_pool_count = reactivate_pool_count
        self.delight_candidate = delight_candidate
        self.delight_count = delight_count
        self.count_delight_thresholds: list[float] = []
        self.get_delight_thresholds: list[float] = []
        self.trim_target: int | None = None
        self.trim_source_share_quotas: dict[str, int] | None = None
        self.trim_overflow_source_share_quotas: dict[str, int] | None = None
        self.reactivate_source_share_quotas: dict[str, int] | None = None
        self.reactivate_raw_source_share_quotas: dict[str, int] | None = None
        self.distribution_counts: dict[str, dict[str, int]] = {
            "topic_group": {"科技": 3},
            "style_key": {"deep_dive": 2},
            "franchise_key": {},
        }
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

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
        return self.pool_count

    def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]:
        pending_eval = int(self.discovery_status_counts.get("pending_eval", 0))
        pending_eval += int(self.discovery_status_counts.get("evaluating", 0))
        evaluated_pending = int(self.discovery_status_counts.get("evaluated", 0))
        return {
            "available": self.pool_count,
            "raw": self.pool_count if self.pool_raw_count is None else self.pool_raw_count,
            "pending": self.pool_pending_count,
            "pending_eval": pending_eval,
            "evaluated_pending": evaluated_pending,
        }

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        return dict(self.source_counts)

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        return dict(self.source_available_counts)

    def count_pool_raw_material_candidates(self) -> int:
        return self.pool_count if self.pool_raw_count is None else self.pool_raw_count

    def count_pool_raw_material_by_source(self) -> dict[str, int]:
        return dict(self.source_raw_counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        return {axis: dict(counts) for axis, counts in self.distribution_counts.items()}

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        return 0

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        return 0

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        self.reactivate_source_share_quotas = dict(source_share_quotas)
        self.reactivate_raw_source_share_quotas = (
            dict(raw_source_share_quotas) if raw_source_share_quotas is not None else None
        )
        reactivated = max(0, self.reactivate_pool_count)
        self.pool_count += reactivated
        if self.pool_raw_count is not None:
            self.pool_raw_count += reactivated
        self.reactivate_pool_count = 0
        return reactivated

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        self.trim_target = target
        self.trim_source_share_quotas = (
            dict(source_share_quotas) if source_share_quotas is not None else None
        )
        raw_count = self.pool_count if self.pool_raw_count is None else self.pool_raw_count
        if raw_count <= target:
            return 0
        trimmed = raw_count - target
        if self.pool_raw_count is None:
            self.pool_count = target
        else:
            self.pool_raw_count = target
        return trimmed

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        self.trim_overflow_source_share_quotas = dict(source_share_quotas)
        return 0

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
    def __init__(self, disliked: list[str] | None = None) -> None:
        self._disliked = list(disliked or [])

    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}

    def get_effective_disliked_topics(self) -> list[str]:
        return list(self._disliked)


class _NoProfileSoulEngine:
    async def get_profile(self) -> None:
        return None

    def get_effective_disliked_topics(self) -> list[str]:
        return []


class _RaisingNoProfileSoulEngine:
    async def get_profile(self) -> None:
        raise RuntimeError("profile not initialized")

    def get_effective_disliked_topics(self) -> list[str]:
        return []


class _DrainSpyCandidatePipeline:
    def __init__(self) -> None:
        self.calls = 0

    async def drain_pending(self, *, profile: object, batch_size: int = 30) -> dict[str, int]:
        self.calls += 1
        raise AssertionError("drain_pending should not run without a profile")


class _FakeDiscoveryEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], list[str] | None, int]] = []
        self.strategy_limit_calls: list[dict[str, int] | None] = []
        self.pool_snapshot_calls: list[object | None] = []

    async def discover(
        self,
        profile: dict[str, object],
        strategies: list[str] | None = None,
        limit: int = 30,
        *,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: object | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append((profile, strategies, limit))
        self.strategy_limit_calls.append(dict(strategy_limits) if strategy_limits else None)
        self.pool_snapshot_calls.append(pool_snapshot)
        return [{"bvid": "BV1X", "relevance_score": 0.9, "view_count": 100}]


class _FakeCandidatePipeline:
    def __init__(self) -> None:
        self.enqueued: list[tuple[list[str], int]] = []
        self.strategy_limit_calls: list[dict[str, int] | None] = []
        self.pool_snapshot_calls: list[object | None] = []
        self.drains: list[int] = []
        self.last_admitted_items: list[object] = []

    async def produce_and_enqueue(
        self,
        *,
        profile: object,
        strategies: list[str],
        limit: int,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: object | None = None,
    ) -> int:
        self.enqueued.append((list(strategies), limit))
        self.strategy_limit_calls.append(dict(strategy_limits) if strategy_limits else None)
        self.pool_snapshot_calls.append(pool_snapshot)
        return limit

    async def drain_pending(
        self,
        *,
        profile: object,
        batch_size: int = 30,
    ) -> dict[str, int]:
        self.drains.append(batch_size)
        self.last_admitted_items = [
            SimpleNamespace(
                tags=["pipeline-topic"],
                source_strategy="search",
            )
        ]
        return {"evaluated": batch_size, "cached": 3, "rejected": 0}


class _FakeXhsProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"enqueued": 1, "attempted": 1, "reason": "ok"}


class _FakeDouyinProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"discovered": 3, "reason": "ok"}


class _FakeYoutubeProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"discovered": 3, "reason": "ok"}


class _FakeXProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"enqueued": 3, "discovered": 3, "reason": "ok"}


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

    async def prewarm_supergroup_embeddings(self) -> int:
        return 0

    async def prewarm_pool_mmr_embeddings(self, *, limit: int = 200) -> int:
        return 0


class _FakeEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


_LOOP_BODY_ATTRS = [
    ("_loop_refresh", ("_on_profile_ready_if_first_time", "refresh_if_needed")),
    ("_loop_pool_precompute", ("_drain_pool_precompute_backlog",)),
    ("_loop_soul_pipeline", ("_tick_soul_pipeline",)),
    ("_loop_xhs_producer", ("_tick_xhs_producer",)),
    ("_loop_douyin_producer", ("_tick_douyin_producer",)),
    ("_loop_youtube_producer", ("_tick_youtube_producer",)),
    ("_loop_x_producer", ("_tick_x_producer",)),
    (
        "_loop_proactive_push",
        (
            "prepare_delight_candidates",
            "_publish_delight_if_available",
            "_publish_probe_if_available",
        ),
    ),
]


def _controller_with_gate(
    *,
    scheduler_config: object,
    presence: PresenceTracker | None = None,
) -> ContinuousRefreshController:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        scheduler_config=scheduler_config,
        presence=presence or PresenceTracker(now=_FakeClock()),
        check_interval_seconds=3600,
        proactive_push_interval_seconds=3600,
    )
    controller._init_grace_consumed = True
    return controller


async def _run_one_loop_with_cancelled_sleep(
    monkeypatch: pytest.MonkeyPatch,
    controller: ContinuousRefreshController,
    loop_name: str,
    body_attrs: tuple[str, ...],
) -> int:
    calls = 0

    async def _body(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return None

    async def _cancel_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    for body_attr in body_attrs:
        monkeypatch.setattr(controller, body_attr, _body)
    monkeypatch.setattr(asyncio, "sleep", _cancel_sleep)

    with pytest.raises(asyncio.CancelledError):
        await getattr(controller, loop_name)()
    return calls


@pytest.mark.parametrize(("loop_name", "body_attrs"), _LOOP_BODY_ATTRS)
async def test_refresh_loops_skip_body_when_scheduler_disabled(
    monkeypatch: pytest.MonkeyPatch,
    loop_name: str,
    body_attrs: tuple[str, ...],
) -> None:
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )

    calls = await _run_one_loop_with_cancelled_sleep(
        monkeypatch,
        controller,
        loop_name,
        body_attrs,
    )

    assert calls == 0


@pytest.mark.parametrize(("loop_name", "body_attrs"), _LOOP_BODY_ATTRS)
async def test_refresh_loops_skip_body_when_extension_presence_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    loop_name: str,
    body_attrs: tuple[str, ...],
) -> None:
    clock = _FakeClock()
    presence = PresenceTracker(now=clock)
    clock.advance(11)
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=True,
            extension_disconnect_grace_seconds=10,
        ),
        presence=presence,
    )

    calls = await _run_one_loop_with_cancelled_sleep(
        monkeypatch,
        controller,
        loop_name,
        body_attrs,
    )

    assert calls == 0


@pytest.mark.parametrize(("loop_name", "body_attrs"), _LOOP_BODY_ATTRS)
async def test_refresh_loops_run_body_when_extension_presence_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
    loop_name: str,
    body_attrs: tuple[str, ...],
) -> None:
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=True,
            extension_disconnect_grace_seconds=10,
        ),
    )

    calls = await _run_one_loop_with_cancelled_sleep(
        monkeypatch,
        controller,
        loop_name,
        body_attrs,
    )

    assert calls >= 1


async def test_profile_ready_classify_is_skipped_while_llm_work_blocked() -> None:
    class _ClassifyingRecommendationEngine(_FakeRecommendationEngine):
        def __init__(self) -> None:
            super().__init__()
            self.classify_calls = 0

        async def classify_pool_backlog(self, *, profile: object, limit: int) -> int:
            self.classify_calls += 1
            return limit

    rec_engine = _ClassifyingRecommendationEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=rec_engine,
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )

    await controller._on_profile_ready_if_first_time()

    assert rec_engine.classify_calls == 0
    assert controller._profile_ready_observed is False


def test_refresh_controller_llm_work_allowed_delegates_to_shared_gate() -> None:
    clock = _FakeClock()
    presence = PresenceTracker(now=clock)
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=True,
            extension_disconnect_grace_seconds=5,
        ),
        presence=presence,
    )

    assert controller._llm_work_allowed() is True

    clock.advance(6)

    assert controller._llm_work_allowed() is False


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
        probe_mode: str = "near",
        specifics: list[object] | None = None,
        status: str = "active",
    ) -> None:
        self.domain = domain
        self.category = category
        self.reason = reason
        self.confidence = confidence
        self.weight = weight
        self.confirmation_count = confirmation_count
        self.experience_mode = experience_mode
        self.entry_load = entry_load
        self.probe_mode = probe_mode
        self.specifics = specifics or []
        self.status = status


class _FakeSpeculator:
    def __init__(self, specs: list[_FakeSpeculation]) -> None:
        self._specs = specs

    def get_active_speculations(self) -> list[_FakeSpeculation]:
        return list(self._specs)


class _FakeAvoidanceSpeculator:
    def __init__(self, avoidances: list[_FakeSpeculation]) -> None:
        self._avoidances = avoidances

    def get_active_avoidances(self) -> list[_FakeSpeculation]:
        return list(self._avoidances)


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

    # v0.3.47+: precompute_pool_copy is fired once per discovery
    # strategy (parallel with subsequent strategies' LLM calls), so the
    # default 2-strategy plan produces 2 calls. Each carries the same
    # profile + per-refresh backfill limit.
    assert len(recommendations.pool_copy_calls) >= 1
    assert all(call == ({"profile": "ok"}, 60) for call in recommendations.pool_copy_calls)


async def test_refresh_controller_detaches_embedding_prewarm_from_refresh_completion() -> None:
    class _SlowEmbeddingPrewarmRecommendationEngine(_FakeRecommendationEngine):
        def __init__(self) -> None:
            super().__init__()
            self.supergroup_started = asyncio.Event()
            self.mmr_started = asyncio.Event()
            self.release = asyncio.Event()

        async def prewarm_supergroup_embeddings(self) -> int:
            self.supergroup_started.set()
            await self.release.wait()
            return 1

        async def prewarm_pool_mmr_embeddings(self, *, limit: int = 200) -> int:
            self.mmr_started.set()
            await self.release.wait()
            return 1

    database = _FakeDatabase(
        [
            {"id": 1, "event_type": "view"},
            {"id": 2, "event_type": "search"},
        ],
        pool_count=20,
    )
    recommendations = _SlowEmbeddingPrewarmRecommendationEngine()
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

    try:
        result = await asyncio.wait_for(controller.refresh_if_needed(), timeout=0.2)
    finally:
        recommendations.release.set()
        await asyncio.sleep(0)

    assert result["refreshed"] is True
    assert recommendations.supergroup_started.is_set()
    assert recommendations.mmr_started.is_set()
    assert controller._refresh_lock.locked() is False


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
    assert database.count_delight_thresholds == [DEFAULT_DELIGHT_THRESHOLD]
    assert database.get_delight_thresholds == [DEFAULT_DELIGHT_THRESHOLD]


def test_load_disliked_topic_phrases_reads_effective_dislikes() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(disliked=["营销号", "标题党"]),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )
    assert controller._load_disliked_topic_phrases() == ["营销号", "标题党"]


def test_get_pending_delight_skips_effective_disliked_candidate() -> None:
    candidate = {
        "bvid": "BV1MKT",
        "title": "震惊！营销号的标题党",
        "delight_reason": "r",
        "delight_score": 0.72,
        "delight_hook": "h",
        "cover_url": "https://example.com/c.jpg",
    }
    blocked = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], delight_candidate=candidate, delight_count=1),
        soul_engine=_FakeSoulEngine(disliked=["营销号"]),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )
    assert blocked.get_pending_delight() is None

    allowed = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], delight_candidate=candidate, delight_count=1),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )
    assert allowed.get_pending_delight() is not None


def test_runtime_status_reports_pool_readiness_counts() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=0,
            pool_raw_count=142,
            pool_pending_count=142,
            discovery_status_counts={"pending_eval": 4, "evaluated": 2},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    status = controller.get_runtime_status()

    assert status["pool_available_count"] == 0
    assert status["pool_raw_count"] == 142
    assert status["pool_pending_count"] == 142
    assert status["pool_pending_eval_count"] == 4
    assert status["pool_evaluated_pending_count"] == 2


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


async def test_periodic_pool_precompute_reports_newly_available_inventory() -> None:
    memory = _FakeMemoryManager({"last_discovered_count": 21, "last_replenished_count": 0})
    database = _FakeDatabase([], pool_count=0)
    recommendations = _FakeRecommendationEngine()
    event_hub = _FakeEventHub()

    async def precompute_then_fill(**kwargs):
        recommendations.pool_copy_calls.append((kwargs["profile"], kwargs["limit"]))
        database.pool_count = 16
        return 16

    recommendations.precompute_pool_copy = precompute_then_fill  # type: ignore[assignment]
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=recommendations,
        event_hub=event_hub,
    )

    await controller._drain_pool_precompute_backlog()

    assert memory.state["last_replenished_count"] == 16
    assert memory.state["last_discovered_count"] == 21
    pool_updated = [event for event in event_hub.events if event["type"] == "refresh.pool_updated"]
    assert pool_updated == [
        {
            "type": "refresh.pool_updated",
            "phase": "done",
            "message": "刚补进 16 条新的",
            "pool_available_count": 16,
            "pool_raw_count": 16,
            "pool_pending_count": 0,
            "pool_pending_eval_count": 0,
            "pool_evaluated_pending_count": 0,
            "last_discovered_count": 21,
            "last_replenished_count": 16,
            "recent_pool_topics": [],
        }
    ]


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
    # force_refresh now uses the same source-aware replenishment plan as
    # periodic refresh, so all Bilibili strategies share one grouped call.
    assert pool_updated["last_discovered_count"] == 1
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

    assert memory.state["last_discovered_count"] == 1
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
    assert len(discovery.calls) == 1
    assert recommendations.calls == []
    assert result["recommendation_count"] == 0


async def test_force_refresh_skips_bilibili_when_platform_quota_full() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "xiaohongshu": 0, "douyin": 60},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
    )

    result = await controller.force_refresh()

    assert result == {"refreshed": False, "strategies": [], "reason": "below_threshold"}
    assert discovery.calls == []


async def test_manual_refresh_skip_does_not_reuse_stale_replenishment_message() -> None:
    memory = _FakeMemoryManager(
        {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": "",
            "last_explore_refresh_at": "",
            "last_processed_event_id": 0,
            "last_notification_at": "",
            "last_discovered_count": 5,
            "last_replenished_count": 1,
            "recent_pool_topics": ["旧主题"],
        }
    )
    event_hub = _FakeEventHub()
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "xiaohongshu": 60, "douyin": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
    )

    await controller._complete_manual_refresh()

    assert discovery.calls == []
    assert controller.get_runtime_status()["manual_refresh_state"] == "success"
    assert controller.get_runtime_status()["manual_refresh_message"] == "这轮没补进新的候选。"
    pool_updated = next(
        event for event in event_hub.events if event["type"] == "refresh.pool_updated"
    )
    assert pool_updated["message"] == "这轮没补进新的候选。"


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

    # v0.3.24+: pool_count=20, target=30, gap=10. Per-strategy target =
    # max(5, gap*3//4) = max(5, 7) = 7. Pre-fix this would have asked
    # for 30 (the discovery_limit floor) regardless of gap, causing
    # ~80% of LLM evaluation cost to land on candidates that were
    # immediately suppressed by trim_pool_to_target_count.
    assert discovery.calls[0][2] == 7


async def test_refresh_plan_uses_candidate_pipeline_when_available() -> None:
    pipeline = _FakeCandidatePipeline()
    discovery = _FakeDiscoveryEngine()
    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert pipeline.enqueued
    assert pipeline.drains
    assert discovery.calls == []
    assert memory.state["last_discovered_count"] == pipeline.enqueued[0][1]
    assert memory.state["recent_pool_topics"][:1] == ["pipeline-topic"]


async def test_refresh_pipeline_does_not_use_stale_topics_when_drain_skips() -> None:
    class SkipDrainPipeline:
        def __init__(self) -> None:
            self.last_admitted_items = [
                SimpleNamespace(tags=["stale-topic"], source_strategy="search")
            ]

        async def produce_and_enqueue(self, **kwargs: object) -> int:
            return 4

        async def drain_pending(self, **kwargs: object) -> dict[str, int]:
            return {"evaluated": 0, "cached": 0, "rejected": 0}

    memory = _FakeMemoryManager({"recent_pool_topics": []})
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=SkipDrainPipeline(),
        pool_target_count=30,
    )

    await controller.force_refresh()

    assert memory.state["last_discovered_count"] == 4
    assert memory.state["recent_pool_topics"] == []


async def test_refresh_pipeline_counts_only_newly_enqueued_candidates_as_discovered() -> None:
    class RetryDrainPipeline:
        last_admitted_items: list[object] = []

        async def produce_and_enqueue(self, **kwargs: object) -> int:
            return 0

        async def drain_pending(self, **kwargs: object) -> dict[str, int]:
            return {"evaluated": 5, "cached": 0, "rejected": 0}

    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=RetryDrainPipeline(),
        pool_target_count=30,
    )

    await controller.force_refresh()

    assert memory.state["last_discovered_count"] == 0


async def test_refresh_pipeline_updates_topics_for_retry_only_admissions() -> None:
    class RetryAdmissionPipeline:
        def __init__(self) -> None:
            self.last_admitted_items = [
                SimpleNamespace(tags=["retry-topic"], source_strategy="search")
            ]

        async def produce_and_enqueue(self, **kwargs: object) -> int:
            return 0

        async def drain_pending(self, **kwargs: object) -> dict[str, int]:
            return {"evaluated": 2, "cached": 2, "rejected": 0}

    memory = _FakeMemoryManager({"recent_pool_topics": ["旧主题"]})
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=RetryAdmissionPipeline(),
        pool_target_count=30,
    )

    await controller.force_refresh()

    assert memory.state["last_discovered_count"] == 0
    assert memory.state["recent_pool_topics"][:1] == ["retry-topic"]


async def test_run_refresh_plan_passes_pool_snapshot() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=20, source_counts={"bilibili": 12}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    await controller._run_refresh_plan(
        state=_FakeMemoryManager().load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["search"], 10)],
        reason="test",
    )

    assert discovery.pool_snapshot_calls
    assert discovery.pool_snapshot_calls[0] is not None


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

    # v0.3.24+: pool_count=0, target=300, gap=300. Per-strategy target =
    # max(5, gap*3//4) = max(5, 225) = 225, capped at discovery_limit=30
    # to avoid one huge wave on init. (Pre-fix this returned 60 — the
    # _MAX_DISCOVERY_BACKFILL_PER_REFRESH ceiling — because the old
    # ``effective_limit = max(discovery_limit, gap)`` formula bumped to
    # gap=300 and hit the absolute cap.)
    assert discovery.calls[0][2] == 30


async def test_refresh_controller_pool_aware_limit_scales_with_gap() -> None:
    """v0.3.24+: when pool is close to target, request fewer candidates
    per strategy. Pre-fix this enforced a 30-item floor regardless of
    gap, causing the LLM evaluation pipeline to score way more
    candidates than the pool could absorb (88% of evaluations were
    suppressed by trim_pool_to_target_count immediately after
    scoring).

    Verifies the gap → per-strategy mapping for three regimes:
    1. Tiny gap (5): floor at 5 (don't starve strategies entirely)
    2. Mid gap (40): per_strategy = 30 (gap*3//4=30, no excess)
    3. Huge gap (1000): cap at discovery_limit=30 (avoid wave)
    """
    discovery = _FakeDiscoveryEngine()
    now = datetime.now().isoformat()

    def make_controller(pool_count: int, pool_target: int) -> ContinuousRefreshController:
        return ContinuousRefreshController(
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
                pool_count=pool_count,
            ),
            soul_engine=_FakeSoulEngine(),
            discovery_engine=discovery,
            recommendation_engine=_FakeRecommendationEngine(),
            pool_target_count=pool_target,
            trending_refresh_hours=999,
            explore_refresh_hours=999,
        )

    # Tiny gap: 95/100, gap=5 → max(5, 5*3//4=3) = 5 (floor protects)
    discovery.calls.clear()
    await make_controller(pool_count=95, pool_target=100).refresh_if_needed()
    assert discovery.calls[0][2] == 5

    # Mid gap: 60/100, gap=40 → max(5, 40*3//4=30) = 30 (full discovery_limit)
    discovery.calls.clear()
    await make_controller(pool_count=60, pool_target=100).refresh_if_needed()
    assert discovery.calls[0][2] == 30

    # Huge gap: 0/1000, gap=1000 → max(5, 1000*3//4=750), capped at
    # discovery_limit=30. Pre-fix this would have hit the
    # _MAX_DISCOVERY_BACKFILL_PER_REFRESH=60 ceiling.
    discovery.calls.clear()
    await make_controller(pool_count=0, pool_target=1000).refresh_if_needed()
    assert discovery.calls[0][2] == 30


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
            pool_count=16,
            source_counts={
                "bilibili": 10,
                "xiaohongshu": 3,
                "douyin": 3,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=4,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    # Bilibili is under its platform quota (24/30 target pool maps to
    # bilibili=24), so the four established B strategies are merged into
    # one discover() call and get mixed in one round.
    assert len(discovery.calls) == 1
    call_profile, call_strategies, _call_limit = discovery.calls[0]
    assert call_profile == {"profile": "ok"}
    assert call_strategies == ["search", "related_chain", "trending", "explore"]


async def test_refresh_controller_skips_bilibili_when_only_small_sources_underfilled() -> None:
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
                "bilibili": 24,
                "xiaohongshu": 0,
                "douyin": 0,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=4,
        trending_refresh_hours=999,
        explore_refresh_hours=999,
    )

    result = await controller.refresh_if_needed()

    assert result == {"refreshed": False, "strategies": [], "reason": "below_threshold"}
    assert discovery.calls == []


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


async def test_publish_interest_probe_skips_confirmed_or_rejected_items() -> None:
    event_hub = _FakeEventHub()
    memory = _FakeMemoryManager(
        {
            "probed_domains": {},
            "probed_axes": {},
            "probed_distance_bands": {},
        }
    )

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [
                    _FakeSpeculation(
                        domain="建筑美学",
                        reason="handled",
                        status="confirmed",
                    ),
                    _FakeSpeculation(
                        domain="城市基础设施",
                        reason="handled",
                        status="rejected",
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

    delivered = await controller._publish_interest_probe_if_available()

    assert delivered is False
    assert [event for event in event_hub.events if event["type"] == "interest.probe"] == []


async def test_publish_interest_probe_records_probed_distance_bands() -> None:
    event_hub = _FakeEventHub()
    memory = _FakeMemoryManager(
        {
            "probed_domains": {},
            "probed_axes": {},
            "probed_distance_bands": {},
        }
    )

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [
                    _FakeSpeculation(
                        domain="桥接方向",
                        reason="从已有兴趣自然跨到一个挑战方向。",
                        weight=0.5,
                        probe_mode="bridge",
                    )
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

    assert "bridge" in memory.state["probed_distance_bands"]


async def test_publish_interest_probe_does_not_record_probe_without_stream_subscriber() -> None:
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
            "probed_axes": {},
        }
    )

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [
                    _FakeSpeculation(
                        domain="城市漫游",
                        reason="能从场景里看结构。",
                        weight=0.5,
                        experience_mode="wander_observe",
                        entry_load="light",
                    )
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=RuntimeEventHub(),
    )

    await controller._publish_interest_probe_if_available()

    assert memory.state["probed_domains"] == {}
    assert memory.state["probed_axes"] == {}


async def test_publish_avoidance_probe_skips_recent_axis_repeat() -> None:
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
            "probed_avoidance_domains": {},
            "probed_avoidance_axes": {"knowledge|heavy": datetime.now().isoformat()},
        }
    )

    class _SoulEngineWithAvoidanceSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._avoidance_speculator = _FakeAvoidanceSpeculator(
                [
                    _FakeSpeculation(
                        domain="标题党热点解读",
                        reason="容易造成低信息密度重复消费。",
                        weight=0.9,
                        experience_mode="knowledge",
                        entry_load="heavy",
                    ),
                    _FakeSpeculation(
                        domain="浅层情绪争吵",
                        reason="和用户偏好的冷静分析方式冲突。",
                        weight=0.5,
                        experience_mode="wander_observe",
                        entry_load="light",
                    ),
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithAvoidanceSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    await controller._publish_avoidance_probe_if_available()

    probe_events = [event for event in event_hub.events if event["type"] == "avoidance.probe"]
    assert len(probe_events) == 1
    assert probe_events[0]["domain"] == "浅层情绪争吵"


async def test_publish_avoidance_probe_skips_confirmed_or_rejected_items() -> None:
    event_hub = _FakeEventHub()
    memory = _FakeMemoryManager(
        {
            "probed_avoidance_domains": {},
            "probed_avoidance_axes": {},
        }
    )

    class _SoulEngineWithAvoidanceSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._avoidance_speculator = _FakeAvoidanceSpeculator(
                [
                    _FakeSpeculation(
                        domain="标题党热点解读",
                        reason="handled",
                        status="confirmed",
                    ),
                    _FakeSpeculation(
                        domain="浅层情绪争吵",
                        reason="handled",
                        status="rejected",
                    ),
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithAvoidanceSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    delivered = await controller._publish_avoidance_probe_if_available()

    assert delivered is False
    assert [event for event in event_hub.events if event["type"] == "avoidance.probe"] == []


async def test_publish_avoidance_probe_does_not_record_without_stream_subscriber() -> None:
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
            "probed_avoidance_domains": {},
            "probed_avoidance_axes": {},
        }
    )

    class _SoulEngineWithAvoidanceSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._avoidance_speculator = _FakeAvoidanceSpeculator(
                [
                    _FakeSpeculation(
                        domain="标题党热点解读",
                        reason="容易造成低信息密度重复消费。",
                        weight=0.5,
                        experience_mode="knowledge",
                        entry_load="light",
                    )
                ]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithAvoidanceSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=RuntimeEventHub(),
    )

    await controller._publish_avoidance_probe_if_available()

    assert memory.state["probed_avoidance_domains"] == {}
    assert memory.state["probed_avoidance_axes"] == {}


async def test_proactive_probe_push_publishes_only_one_probe_per_tick() -> None:
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
            "probed_axes": {},
            "probed_avoidance_domains": {},
            "probed_avoidance_axes": {},
            "last_probe_kind": "",
        }
    )

    class _SoulEngineWithBothSpeculators(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [_FakeSpeculation(domain="城市漫游", reason="能从场景里看结构。")]
            )
            self._avoidance_speculator = _FakeAvoidanceSpeculator(
                [_FakeSpeculation(domain="标题党热点解读", reason="低信息密度。")]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithBothSpeculators(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    await controller._publish_probe_if_available()

    probe_events = [
        event
        for event in event_hub.events
        if event["type"] in {"interest.probe", "avoidance.probe"}
    ]
    assert len(probe_events) == 1
    assert probe_events[0]["type"] == "interest.probe"
    assert memory.state["last_probe_kind"] == "interest"

    await controller._publish_probe_if_available()

    probe_events = [
        event
        for event in event_hub.events
        if event["type"] in {"interest.probe", "avoidance.probe"}
    ]
    assert len(probe_events) == 2
    assert probe_events[1]["type"] == "avoidance.probe"
    assert memory.state["last_probe_kind"] == "avoidance"


async def test_proactive_probe_push_does_not_record_kind_when_publish_fails() -> None:
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
            "probed_axes": {},
            "last_probe_kind": "",
        }
    )

    class _SoulEngineWithSpeculator(_FakeSoulEngine):
        def __init__(self) -> None:
            self._speculator = _FakeSpeculator(
                [_FakeSpeculation(domain="城市漫游", reason="能从场景里看结构。")]
            )

    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(events=[]),
        soul_engine=_SoulEngineWithSpeculator(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=RuntimeEventHub(),
    )

    await controller._publish_probe_if_available()

    assert memory.state["last_probe_kind"] == ""
    assert memory.state["probed_domains"] == {}


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


async def test_drain_discovery_candidates_skips_when_profile_unavailable() -> None:
    pipeline = _DrainSpyCandidatePipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_NoProfileSoulEngine(),  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    result = await controller.drain_discovery_candidates_once(batch_size=30)

    assert result == {"evaluated": 0, "cached": 0, "rejected": 0}
    assert pipeline.calls == 0


async def test_drain_discovery_candidates_skips_when_profile_lookup_raises() -> None:
    pipeline = _DrainSpyCandidatePipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_RaisingNoProfileSoulEngine(),  # type: ignore[arg-type]
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    result = await controller.drain_discovery_candidates_once(batch_size=30)

    assert result == {"evaluated": 0, "cached": 0, "rejected": 0}
    assert pipeline.calls == 0


async def test_refresh_skips_discovery_when_available_pool_is_at_target_floor() -> None:
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
    assert database.pool_count == 50
    assert database.trim_target == 150


def test_source_target_counts_use_platform_default_shares() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=600),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
    )

    assert controller._source_target_counts() == {"bilibili": 600}


def test_source_target_counts_use_configured_platform_shares() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=600),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 6, "xiaohongshu": 2, "douyin": 2},
    )

    assert controller._source_target_counts() == {
        "bilibili": 360,
        "xiaohongshu": 120,
        "douyin": 120,
    }


def test_source_requested_count_is_bounded_by_global_available_deficit() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=98,
            source_available_counts={"bilibili": 10},
            source_raw_counts={"bilibili": 10},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 1},
    )

    assert controller._source_requested_count("bilibili") == 2


async def test_non_bili_producer_not_called_when_global_pool_is_full() -> None:
    xhs = _FakeXhsProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=100,
            source_available_counts={"xiaohongshu": 0},
            source_raw_counts={"xiaohongshu": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        xhs_producer=xhs,
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "xiaohongshu": 1},
    )

    await controller._tick_xhs_producer()

    assert xhs.calls == []


async def test_douyin_producer_not_called_when_global_pool_is_full() -> None:
    douyin = _FakeDouyinProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=100,
            source_available_counts={"douyin": 0},
            source_raw_counts={"douyin": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        douyin_producer=douyin,
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "douyin": 1},
    )

    await controller._tick_douyin_producer()

    assert douyin.calls == []


async def test_youtube_producer_not_called_when_global_pool_is_full() -> None:
    youtube = _FakeYoutubeProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=100,
            source_available_counts={"youtube": 0},
            source_raw_counts={"youtube": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        youtube_producer=youtube,
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "youtube": 1},
    )

    await controller._tick_youtube_producer()

    assert youtube.calls == []


def test_source_replenishment_plan_maps_bilibili_deficit_to_bilibili_strategies() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=420,
            source_counts={
                "bilibili": 300,
                "xiaohongshu": 60,
                "douyin": 60,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
    )

    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 180)
    ]


def test_source_replenishment_plan_uses_frontend_available_source_counts() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=246,
            source_counts={"bilibili": 300},
            source_available_counts={"bilibili": 246},
            source_raw_counts={"bilibili": 300},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
    )

    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 54)
    ]


def test_source_replenishment_plan_clamps_requested_count_by_raw_headroom() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=250,
            source_available_counts={"bilibili": 250},
            source_raw_counts={"bilibili": 570},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
    )

    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 30)
    ]


def test_source_replenishment_plan_stops_when_raw_headroom_is_zero() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=270,
            source_available_counts={"bilibili": 270},
            source_raw_counts={"bilibili": 600},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
    )

    assert controller._build_source_replenishment_plan() == []
    assert controller._source_deficit("bilibili") == 0


def test_real_database_enforce_then_replenish_reaches_available_target(
    tmp_path,
) -> None:
    db = Database(tmp_path / "pool.db")
    db.initialize()

    for group_index in range(82):
        for rank in range(3):
            _seed_visible_pool_row(
                db,
                f"BVBASE{group_index:02d}{rank}",
                topic_group=f"topic-{group_index}",
                relevance_score=1.0 - rank / 100,
            )
    for index in range(54):
        _seed_visible_pool_row(
            db,
            f"BVEXTRA{index:02d}",
            topic_group=f"topic-{index % 18}",
            relevance_score=0.10,
        )

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=db,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
    )

    assert db.count_pool_candidates() == 246
    assert db.count_pool_candidates_by_source() == {"bilibili": 300}
    assert db.count_pool_raw_material_by_source() == {"bilibili": 300}
    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 54)
    ]

    assert controller._enforce_pool_cap() is False
    assert db.count_pool_raw_material_by_source() == {"bilibili": 300}

    for index in range(54):
        _seed_visible_pool_row(
            db,
            f"BVNEW{index:02d}",
            topic_group=f"new-topic-{index}",
            relevance_score=0.80,
        )

    assert controller._enforce_pool_cap() is True
    assert db.count_pool_candidates() == 300
    assert db.count_pool_raw_material_by_source() == {"bilibili": 354}
    db.close()


def test_disabled_bilibili_share_skips_bilibili_refresh_strategies() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [{"id": 1, "event_type": "click"}],
            pool_count=600,
            source_counts={"youtube": 600},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"youtube": 1},
        signal_event_threshold=1,
    )

    assert controller._build_refresh_plan(_FakeMemoryManager().load_discovery_runtime_state()) == []


async def test_refresh_controller_uses_bilibili_deficit_for_discovery_limit() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=543,
            source_counts={
                "bilibili": 475,
                "xiaohongshu": 60,
                "douyin": 8,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
    )

    await controller.refresh_if_needed()

    assert discovery.calls[0][1] == ["search", "related_chain", "trending", "explore"]
    assert discovery.calls[0][2] == 5
    assert discovery.strategy_limit_calls[0] == {
        "search": 2,
        "related_chain": 1,
        "trending": 1,
        "explore": 1,
    }


def test_source_replenishment_plan_leaves_xhs_deficit_to_xhs_producer() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=458,
            source_counts={
                "bilibili": 480,
                "xiaohongshu": 0,
                "douyin": 60,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
    )

    assert controller._build_source_replenishment_plan() == []


def test_source_replenishment_plan_leaves_youtube_deficit_to_youtube_producer() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=80,
            source_counts={
                "bilibili": 80,
                "youtube": 0,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "youtube": 2},
    )

    assert controller._build_source_replenishment_plan() == []


def test_warn_on_stranded_source_shares_checks_youtube_producer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=80,
            source_counts={
                "bilibili": 80,
                "youtube": 0,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        youtube_producer=None,
    )

    controller._warn_on_stranded_source_shares()

    assert "youtube" in caplog.text


async def test_xhs_producer_receives_source_deficit_limit() -> None:
    producer = _FakeXhsProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=598,
            source_counts={"bilibili": 480, "xiaohongshu": 58, "douyin": 60},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
        xhs_producer=producer,
    )

    await controller._tick_xhs_producer()

    assert producer.calls == [2]


async def test_douyin_producer_runs_when_douyin_under_quota() -> None:
    producer = _FakeDouyinProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "xiaohongshu": 60, "douyin": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
        douyin_producer=producer,
    )

    await controller._tick_douyin_producer()

    assert producer.calls == [30]


async def test_douyin_producer_skips_when_douyin_at_quota() -> None:
    producer = _FakeDouyinProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=600,
            source_counts={"bilibili": 480, "xiaohongshu": 60, "douyin": 60},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        douyin_producer=producer,
    )

    await controller._tick_douyin_producer()

    assert producer.calls == []


async def test_youtube_producer_runs_when_youtube_under_quota() -> None:
    producer = _FakeYoutubeProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "youtube": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        discovery_limit=30,
        youtube_producer=producer,
    )

    await controller._tick_youtube_producer()

    assert producer.calls == [30]


async def test_youtube_producer_skips_when_youtube_at_quota() -> None:
    producer = _FakeYoutubeProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=600,
            source_counts={"bilibili": 480, "youtube": 120},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "youtube": 2},
        youtube_producer=producer,
    )

    await controller._tick_youtube_producer()

    assert producer.calls == []


async def test_x_producer_runs_when_twitter_under_quota() -> None:
    producer = _FakeXProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "twitter": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "twitter": 2},
        discovery_limit=30,
        x_producer=producer,
    )

    await controller._tick_x_producer()

    assert producer.calls == [30]


async def test_x_producer_skips_when_twitter_at_quota() -> None:
    producer = _FakeXProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=600,
            source_counts={"bilibili": 480, "twitter": 120},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "twitter": 2},
        x_producer=producer,
    )

    await controller._tick_x_producer()

    assert producer.calls == []


async def test_x_producer_skips_when_not_configured() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=540, source_counts={"bilibili": 480, "twitter": 0}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "twitter": 2},
        x_producer=None,
    )

    # No producer wired → tick is a safe no-op (does not raise).
    await controller._tick_x_producer()


def test_pool_cap_total_trim_receives_raw_ceiling_source_quotas() -> None:
    database = _FakeDatabase([], pool_count=650, pool_raw_count=1300)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
    )

    assert controller._enforce_pool_cap() is True
    assert database.trim_target == 1200
    assert database.trim_source_share_quotas is not None
    assert database.trim_source_share_quotas["bilibili"] == 960
    assert database.trim_source_share_quotas["xiaohongshu"] == 120
    assert database.trim_source_share_quotas["douyin"] == 120
    assert database.pool_raw_count == 1200


def test_pool_cap_enforces_platform_caps_even_when_ready_pool_below_target() -> None:
    database = _FakeDatabase([], pool_count=580)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
    )

    assert controller._enforce_pool_cap() is False
    assert database.trim_overflow_source_share_quotas == {
        "bilibili": 960,
        "xiaohongshu": 120,
        "douyin": 120,
    }


def test_pool_cap_reactivates_under_quota_sources_before_trim() -> None:
    database = _FakeDatabase([], pool_count=600, reactivate_pool_count=20)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
    )

    assert controller._enforce_pool_cap() is True
    assert database.reactivate_source_share_quotas is not None
    assert database.reactivate_source_share_quotas["bilibili"] == 480
    assert database.reactivate_source_share_quotas["xiaohongshu"] == 60
    assert database.reactivate_source_share_quotas["douyin"] == 60
    assert database.reactivate_raw_source_share_quotas is not None
    assert database.reactivate_raw_source_share_quotas["bilibili"] == 960
    assert database.reactivate_raw_source_share_quotas["xiaohongshu"] == 120
    assert database.reactivate_raw_source_share_quotas["douyin"] == 120
    assert database.trim_source_share_quotas is not None
    assert database.trim_source_share_quotas["bilibili"] == 960
    assert database.pool_count == 620


async def test_run_refresh_plan_enforces_raw_ceiling_when_discovery_overshoots() -> None:
    """Regression: the per-strategy ``current_pool_count >= target`` check
    only prevents *starting* a strategy when already at cap. Within a
    single ``discover()`` call the pool can overshoot freely (long-tail
    LLM eval batches add 50-100 rows per strategy in production). The
    frontend-visible count is allowed to overshoot the target floor, but
    post-refresh enforcement must still cap raw material at the raw
    ceiling.
    """

    class OvershootingDiscovery(_FakeDiscoveryEngine):
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
            # Single strategy adds 25 rows. Available jumps from 25 to 50
            # and raw jumps from 145 to 170, above raw ceiling 150.
            self.database.pool_count += 25
            if self.database.pool_raw_count is not None:
                self.database.pool_raw_count += 25
            return [{"bvid": "BV-y", "relevance_score": 0.5}]

    database = _FakeDatabase(
        [],
        pool_count=25,
        pool_raw_count=145,
        source_available_counts={"bilibili": 25},
        source_raw_counts={"bilibili": 145},
    )
    discovery = OvershootingDiscovery(database)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    await controller.force_refresh()

    assert database.pool_count == 50
    assert database.pool_raw_count == 150


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

    started = {name: asyncio.Event() for name in ("refresh", "soul", "xhs", "douyin", "push")}
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
    controller._loop_douyin_producer = make_loop("douyin")  # type: ignore[method-assign]
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


# ---------------------------------------------------------------------------
# v0.3.37+ — runtime event emission (delight.refreshed / pool_status)
# ---------------------------------------------------------------------------


async def test_refresh_publishes_delight_refreshed_when_count_increases() -> None:
    """``_run_refresh_plan`` emits ``delight.refreshed`` when precompute
    finds net new above-threshold delights. Popup uses this to trigger a
    silent re-fetch of /api/delight/pending-batch.
    """
    event_hub = _FakeEventHub()
    database = _FakeDatabase(
        [{"id": 1, "event_type": "view"}],
        pool_count=20,
        delight_count=2,  # Initial count
    )

    # Recommendation engine bumps the database's delight count when its
    # precompute runs, simulating a new above-threshold item being scored.
    rec_engine = _FakeRecommendationEngine()
    original_precompute = rec_engine.precompute_pool_copy

    async def precompute_then_bump(**kwargs):
        result = await original_precompute(**kwargs)
        database.delight_count = 5  # +3 new delights after precompute
        return result

    rec_engine.precompute_pool_copy = precompute_then_bump  # type: ignore[assignment]

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=rec_engine,
        event_hub=event_hub,
        pool_target_count=30,
    )

    await controller.force_refresh()

    delight_events = [e for e in event_hub.events if e["type"] == "delight.refreshed"]
    assert len(delight_events) == 1, f"expected 1 delight.refreshed, got {len(delight_events)}"
    assert delight_events[0]["count"] == 3
    assert delight_events[0]["total_pending"] == 5


async def test_refresh_skips_delight_refreshed_when_count_unchanged() -> None:
    """No event when precompute finishes without new above-threshold delights
    (avoids spamming popup with no-op refreshes)."""
    event_hub = _FakeEventHub()
    database = _FakeDatabase(
        [{"id": 1, "event_type": "view"}],
        pool_count=20,
        delight_count=2,  # stays at 2 — no new delights
    )

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
    )

    await controller.force_refresh()

    delight_events = [e for e in event_hub.events if e["type"] == "delight.refreshed"]
    assert len(delight_events) == 0


async def test_refresh_publishes_pool_status_when_count_changes() -> None:
    """``_publish_pool_status_if_changed`` emits ``pool_status`` only when
    the count differs from last published."""
    event_hub = _FakeEventHub()
    database = _FakeDatabase(
        [{"id": 1, "event_type": "view"}],
        pool_count=42,  # → emit pool_status with 42
    )

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
    )

    # Trigger _enforce_pool_cap via a tick that hits the gate
    await controller._publish_pool_status_if_changed()

    pool_events = [e for e in event_hub.events if e["type"] == "pool_status"]
    assert len(pool_events) == 1
    assert pool_events[0]["pool_available_count"] == 42
    assert pool_events[0]["pool_target_count"] == 30


async def test_refresh_pool_status_includes_readiness_counts() -> None:
    event_hub = _FakeEventHub()
    database = _FakeDatabase(
        [],
        pool_count=0,
        pool_raw_count=142,
        pool_pending_count=142,
        discovery_status_counts={"pending_eval": 1, "evaluating": 1, "evaluated": 3},
    )

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
    )

    await controller._publish_pool_status_if_changed()

    pool_events = [e for e in event_hub.events if e["type"] == "pool_status"]
    assert pool_events == [
        {
            "type": "pool_status",
            "pool_available_count": 0,
            "pool_raw_count": 142,
            "pool_pending_count": 142,
            "pool_pending_eval_count": 2,
            "pool_evaluated_pending_count": 3,
            "pool_target_count": 30,
        }
    ]


async def test_refresh_pool_status_dedupes_unchanged_count() -> None:
    """Calling ``_publish_pool_status_if_changed`` repeatedly with the
    same count must only emit the first one — popup-side state
    rendering would still re-paint on duplicate."""
    event_hub = _FakeEventHub()
    database = _FakeDatabase([], pool_count=42)

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
    )

    await controller._publish_pool_status_if_changed()
    await controller._publish_pool_status_if_changed()
    await controller._publish_pool_status_if_changed()

    pool_events = [e for e in event_hub.events if e["type"] == "pool_status"]
    assert len(pool_events) == 1, "second/third calls should not re-publish"


async def test_refresh_pool_status_re_emits_when_count_rotates() -> None:
    """When count changes back, we must re-emit. Otherwise popup never
    sees a pool drain → refill cycle."""
    event_hub = _FakeEventHub()
    database = _FakeDatabase([], pool_count=42)

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
        pool_target_count=30,
    )

    await controller._publish_pool_status_if_changed()  # 42
    database.pool_count = 20
    await controller._publish_pool_status_if_changed()  # 20
    database.pool_count = 42
    await controller._publish_pool_status_if_changed()  # 42 again

    pool_events = [e for e in event_hub.events if e["type"] == "pool_status"]
    counts = [e["pool_available_count"] for e in pool_events]
    assert counts == [42, 20, 42]


async def test_refresh_if_needed_skips_when_scheduler_disabled() -> None:
    """refresh_if_needed must respect the LLM gate so event-ingest and
    feedback paths don't fire discovery when 停止后台 LLM 请求 is on."""
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is False
    assert result["reason"] == "llm_paused"


async def test_refresh_after_event_ingest_skips_when_scheduler_disabled() -> None:
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )

    result = await controller.refresh_after_event_ingest()

    assert result["refreshed"] is False
    assert result["reason"] == "llm_paused"


async def test_refresh_after_feedback_skips_when_scheduler_disabled() -> None:
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )

    result = await controller.refresh_after_feedback()

    assert result["refreshed"] is False
    assert result["reason"] == "llm_paused"
