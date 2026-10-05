from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import (
    CandidateEvalClaim,
    CandidateEvalOutcome,
    DiscoveryCandidatePipeline,
)
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.llm.base import LLMRateLimitError
from openbiliclaw.llm.concurrency import InventoryPriorityState, LLMConcurrencyGate
from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.runtime.candidate_eval import CandidateEvalCoordinator, CandidateEvalSnapshot
from openbiliclaw.runtime.events import RuntimeEventHub
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.storage.database import Database, PoolMaintenanceResult

from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from pathlib import Path

_MULTI_SOURCE_SHARES = {"bilibili": 8, "xiaohongshu": 1, "douyin": 1}


def assert_publication(payload: dict[str, object]) -> None:
    assert payload["published_at"] == "2026-07-08T06:30:00Z"
    assert payload["published_label"] == "3 days ago"


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
        self.dynamic_default_thresholds: list[float] = []
        self.trim_target: int | None = None
        self.trim_source_share_quotas: dict[str, int] | None = None
        self.trim_overflow_source_share_quotas: dict[str, int] | None = None
        self.reactivate_source_share_quotas: dict[str, int] | None = None
        self.reactivate_raw_source_share_quotas: dict[str, int] | None = None
        self.maintenance_calls: list[dict[str, object]] = []
        self.legacy_maintenance_calls = 0
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
        self.legacy_maintenance_calls += 1
        return 0

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        self.legacy_maintenance_calls += 1
        return 0

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        self.legacy_maintenance_calls += 1
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
        self.legacy_maintenance_calls += 1
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
        self.legacy_maintenance_calls += 1
        self.trim_overflow_source_share_quotas = dict(source_share_quotas)
        return 0

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        self.legacy_maintenance_calls += 1
        return 0

    def maintain_pool_inventory(
        self,
        *,
        target: int,
        raw_ceiling: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
        max_per_topic_group: int = 3,
        max_per_explore_cluster: int = 3,
        stale_max_age_days: int = 14,
        xhs_self_nickname: str = "",
    ) -> PoolMaintenanceResult:
        self.maintenance_calls.append(
            {
                "target": target,
                "raw_ceiling": raw_ceiling,
                "source_share_quotas": dict(source_share_quotas),
                "raw_source_share_quotas": dict(raw_source_share_quotas or {}),
                "max_per_topic_group": max_per_topic_group,
                "max_per_explore_cluster": max_per_explore_cluster,
                "stale_max_age_days": stale_max_age_days,
                "xhs_self_nickname": xhs_self_nickname,
            }
        )
        raw_before = self.pool_count if self.pool_raw_count is None else self.pool_raw_count
        raw_after = min(raw_before, raw_ceiling)
        if self.pool_raw_count is not None:
            self.pool_raw_count = raw_after
        return PoolMaintenanceResult(
            available_before=self.pool_count,
            available_after=self.pool_count,
            target=target,
            protected_available=min(self.pool_count, target),
            recovered_suppressed=0,
            trimmed_stale=0,
            trimmed_explore_cluster=0,
            trimmed_ready_reserve=0,
            trimmed_evaluated=0,
            trimmed_raw=max(0, raw_before - raw_after),
            trimmed_by_source={},
            deferred_topic_trim=0,
            deferred_source_trim=0,
            deferred_stale_trim=0,
            deferred_explore_cluster_trim=0,
            raw_before=raw_before,
            raw_after=raw_after,
            raw_ceiling=raw_ceiling,
            untrimmed_raw_excess=0,
            rolled_back=False,
        )

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> dict[str, object] | None:
        self.get_delight_thresholds.append(min_delight_score)
        return self.delight_candidate

    def dynamic_delight_threshold(self, *, default_threshold: float) -> float:
        self.dynamic_default_thresholds.append(default_threshold)
        return 0.88

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
        **kwargs: object,
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


class _ProfileSoulEngine(_FakeSoulEngine):
    async def get_profile(self) -> object:
        return _build_profile()


class _StructuredResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _StructuredScoringLLM:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload
        self.calls = 0

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.calls += 1
        return _StructuredResponse(json.dumps(self.payload, ensure_ascii=False))


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


class _FakeBiliProducer:
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


class _FakeRedditProducer:
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


class _RealDatabasePrecomputeEngine(_FakeRecommendationEngine):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def precompute_pool_copy(
        self,
        *,
        profile: object,
        limit: int,
    ) -> int:
        self.pool_copy_calls.append((profile, limit))
        rows = self.database.get_pool_candidates_needing_copy(limit=limit)
        for row in rows:
            self.database.update_pool_copy(
                str(row["bvid"]),
                expression="端到端推荐文案",
                topic_label="端到端主题",
            )
        return len(rows)


class _FakeEventHub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


class _ExpressionCopyNotifySpy:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def notify(self, reason: str) -> None:
        self.reasons.append(reason)


_LOOP_BODY_ATTRS = [
    ("_loop_refresh", ("_on_profile_ready_if_first_time", "refresh_if_needed")),
    ("_loop_pool_precompute", ("_drain_pool_precompute_backlog",)),
    ("_loop_candidate_eval", ("_drain_discovery_candidates_and_precompute",)),
    ("_loop_soul_pipeline", ("_tick_soul_pipeline",)),
    ("_loop_bilibili_producer", ("_tick_bilibili_producer",)),
    ("_loop_xhs_producer", ("_tick_xhs_producer",)),
    ("_loop_douyin_producer", ("_tick_douyin_producer",)),
    ("_loop_youtube_producer", ("_tick_youtube_producer",)),
    ("_loop_x_producer", ("_tick_x_producer",)),
    ("_loop_github_producer", ("_tick_github_producer",)),
    ("_loop_reddit_producer", ("_tick_reddit_producer",)),
    ("_loop_linuxdo_producer", ("_tick_linuxdo_producer",)),
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


async def test_refresh_controller_pauses_when_background_llm_budget_exceeded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = LLMConcurrencyGate(4)
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=False,
            llm_budget_max_calls=1,
            llm_budget_window_seconds=3600,
        ),
    )
    controller.llm_concurrency_gate = gate

    assert controller._llm_work_allowed() is True

    async with gate.slot(caller="soul.preference"):
        pass

    with caplog.at_level(logging.WARNING):
        assert controller._llm_work_allowed() is False
    assert "Background LLM calls reached 1" in caplog.text
    assert "paused" in caplog.text


def test_refresh_controller_budget_disabled_when_max_calls_zero() -> None:
    gate = LLMConcurrencyGate(4)
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(
            enabled=True,
            pause_on_extension_disconnect=False,
            llm_budget_max_calls=0,
            llm_budget_window_seconds=3600,
        ),
    )
    controller.llm_concurrency_gate = gate

    assert controller._llm_work_allowed() is True


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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    # Trending/explore clocks are current (999-minute interval), so automatic
    # replenishment only runs search + related_chain.
    assert set(result["strategies"]) == {"search", "related_chain"}


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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
            "published_at": "2026-07-08T06:30:00Z",
            "published_label": "3 days ago",
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
    assert_publication(pending)
    assert database.dynamic_default_thresholds == [
        DEFAULT_DELIGHT_THRESHOLD,
        DEFAULT_DELIGHT_THRESHOLD,
    ]
    assert database.count_delight_thresholds == [0.88]
    assert database.get_delight_thresholds == [0.88]


async def test_publish_delight_if_available_includes_publication_fields() -> None:
    event_hub = _FakeEventHub()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            delight_candidate={
                "bvid": "BV1DELIGHT",
                "title": "惊喜候选",
                "delight_score": 0.95,
                "published_at": "2026-07-08T06:30:00Z",
                "published_label": "3 days ago",
                "share_count": 321,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        event_hub=event_hub,
    )

    await controller._publish_delight_if_available()

    assert len(event_hub.events) == 1
    assert_publication(event_hub.events[0])
    assert event_hub.events[0]["share_count"] == 321


def test_load_disliked_topic_phrases_reads_effective_dislikes() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(disliked=["营销号", "标题党"]),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )
    assert controller._load_disliked_topic_phrases() == ["营销号", "标题党"]


def test_get_pending_notification_blocks_structured_disliked_topic() -> None:
    class _NotificationDatabase(_FakeDatabase):
        def get_notification_candidate(
            self,
            *,
            min_confidence: float = 0.82,
        ) -> dict[str, object] | None:
            assert min_confidence == 0.82
            return {
                "id": 7,
                "bvid": "BV1REHAB",
                "title": "办公室久坐舒展指南",
                "expression": "一套具体动作。",
                "topic_group": "运动康复",
            }

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_NotificationDatabase([]),
        soul_engine=_FakeSoulEngine(disliked=["运动康复"]),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    assert controller.get_pending_notification() is None


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


def test_delight_consumption_notifies_expression_copy_refill() -> None:
    coordinator = _ExpressionCopyNotifySpy()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        expression_copy_coordinator=coordinator,
    )

    controller.mark_delight_sent("BVDELIGHT_SENT")
    controller.mark_delight_seen("BVDELIGHT_SEEN")

    assert coordinator.reasons == ["delight_consumed", "delight_seen"]


def test_pool_maintenance_mutation_notifies_expression_copy_refill() -> None:
    coordinator = _ExpressionCopyNotifySpy()
    database = _FakeDatabase([], pool_count=10)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        expression_copy_coordinator=coordinator,
        pool_target_count=10,
    )
    result = database.maintain_pool_inventory(
        target=10,
        raw_ceiling=20,
        source_share_quotas={"bilibili": 10},
    )

    assert controller._record_pool_maintenance_result(replace(result, mutation_count=1)) is True
    assert coordinator.reasons == ["pool_maintenance"]


def _rolled_back_maintenance_result() -> PoolMaintenanceResult:
    return PoolMaintenanceResult(
        available_before=5,
        available_after=5,
        target=10,
        protected_available=5,
        recovered_suppressed=0,
        trimmed_stale=2,
        trimmed_explore_cluster=0,
        trimmed_ready_reserve=0,
        trimmed_evaluated=0,
        trimmed_raw=2,
        trimmed_by_source={"bilibili": 2},
        deferred_topic_trim=0,
        deferred_source_trim=0,
        deferred_stale_trim=0,
        deferred_explore_cluster_trim=0,
        raw_before=20,
        raw_after=20,
        raw_ceiling=100,
        untrimmed_raw_excess=0,
        rolled_back=True,
        reason="available inventory fell below protected floor: before=5 after=4 target=10",
        mutation_count=2,
    )


def test_pool_maintenance_rollback_logs_warning_not_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rollback is the availability guard working, not an operational fault."""
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=5),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=10,
    )

    with caplog.at_level(logging.INFO):
        assert (
            controller._record_pool_maintenance_result(_rolled_back_maintenance_result()) is False
        )

    records = [
        record for record in caplog.records if "pool_maintenance available=" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    # The attempted (reverted) batch is labelled so the count cannot be read
    # as committed work.
    assert "mutations=2(attempted)" in records[0].getMessage()


def test_pool_maintenance_commit_logs_info_without_attempted_label(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = _FakeDatabase([], pool_count=10)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=10,
    )
    result = database.maintain_pool_inventory(
        target=10,
        raw_ceiling=20,
        source_share_quotas={"bilibili": 10},
    )

    with caplog.at_level(logging.INFO):
        controller._record_pool_maintenance_result(result)

    records = [
        record for record in caplog.records if "pool_maintenance available=" in record.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert "(attempted)" not in records[0].getMessage()


def test_runtime_status_reports_pool_readiness_counts() -> None:
    gate = LLMConcurrencyGate(4)
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
        llm_concurrency_gate=gate,
        pool_target_count=30,
    )

    status = controller.get_runtime_status()

    assert status["pool_available_count"] == 0
    assert status["pool_raw_count"] == 142
    assert status["pool_pending_count"] == 142
    assert status["pool_pending_eval_count"] == 4
    assert status["pool_evaluated_pending_count"] == 2
    assert gate.inventory_priority_state is InventoryPriorityState.EMPTY
    assert status["inventory_priority_state"] == "empty"


def test_pool_readiness_snapshot_updates_refill_state_from_canonical_available() -> None:
    database = _FakeDatabase([], pool_count=7)
    gate = LLMConcurrencyGate(4)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        llm_concurrency_gate=gate,
        pool_target_count=30,
    )

    assert controller._pool_readiness_counts()["available"] == 7
    assert gate.inventory_priority_state is InventoryPriorityState.REFILL
    database.pool_count = 30
    assert controller._pool_readiness_counts()["available"] == 30
    assert gate.inventory_priority_state is InventoryPriorityState.HEALTHY


def test_pool_readiness_preserves_database_admitted_pending_copy(tmp_path: Path) -> None:
    database = Database(tmp_path / "readiness.db")
    database.initialize()
    database.cache_content(
        "BVPENDINGCOPY",
        title="classified admitted row",
        source="search",
        relevance_score=0.9,
        style_key="tutorial",
        topic_group="testing",
    )
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=10,
    )

    assert database.count_pool_readiness()["admitted_pending_copy"] == 1
    assert controller._pool_readiness_counts()["admitted_pending_copy"] == 1
    database.close()


def test_pool_readiness_fallback_sets_admitted_pending_copy_to_zero() -> None:
    database = _FakeDatabase([], pool_count=3)
    database.count_pool_readiness = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[method-assign]
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    assert controller._pool_readiness_counts()["admitted_pending_copy"] == 0


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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is False
    assert result["reason"] == "below_threshold"
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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


async def test_one_shot_inline_eval_cap_bounds_default_pool_supply_and_drain() -> None:
    """A one-shot bridge serves one bounded batch, not the daemon's 30-row wave."""

    pipeline = _FakeCandidatePipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        one_shot_inline_eval_limit=4,
        pool_target_count=300,
        discovery_limit=30,
    )

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert pipeline.enqueued
    assert pipeline.drains == [4]
    assert all(limit <= 4 for _strategies, limit in pipeline.enqueued)


async def test_managed_candidate_coordinator_keeps_daemon_sized_supply_wave() -> None:
    """The one-shot cap must not shrink the API runtime's coordinator-owned wave."""

    class Coordinator:
        def __init__(self) -> None:
            self.notifications: list[str] = []

        def notify(self, reason: str) -> None:
            self.notifications.append(reason)

    pipeline = _FakeCandidatePipeline()
    coordinator = Coordinator()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        candidate_eval_coordinator=coordinator,
        one_shot_inline_eval_limit=4,
        pool_target_count=300,
        discovery_limit=30,
    )

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert pipeline.enqueued
    assert all(limit == 30 for _strategies, limit in pipeline.enqueued)
    assert pipeline.drains == []
    assert coordinator.notifications


async def test_refresh_pipeline_drain_uses_shared_candidate_lock() -> None:
    pipeline = _FakeCandidatePipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    async with controller._discovery_drain_lock:
        await controller._run_refresh_plan(
            state=_FakeMemoryManager().load_discovery_runtime_state(),
            profile={"profile": "ok"},
            plan=[(["search"], 10)],
            reason="test",
        )

    assert pipeline.enqueued == [(["search"], 7)]
    assert pipeline.drains == []


async def test_managed_refresh_leaves_all_durable_eval_claims_to_coordinator(
    tmp_path: Path,
) -> None:
    """A live coordinator is the sole owner of refresh-created raw claims.

    The producer intentionally overfills the raw queue.  The coordinator can
    claim only three 30-item batches; the old inline refresh drain would claim
    a second 90-item batch, making the durable ``evaluating`` count reach 180.
    """

    class _BlockingManagedPipeline:
        def __init__(self, database: Database) -> None:
            self.database = database
            self.on_candidates_enqueued: Any | None = None
            self.inline_drain_calls = 0
            self.claim_sequence = 0
            self.release = asyncio.Event()

        async def ensure_pending_supply(self, **_kwargs: object) -> dict[str, int]:
            writes = [
                DiscoveryCandidateWrite(
                    candidate_key=f"bilibili:managed-{index}",
                    source_platform="bilibili",
                    source_strategy="search",
                    content_id=f"managed-{index}",
                    title=f"Managed candidate {index}",
                )
                for index in range(180)
            ]
            inserted = self.database.enqueue_discovery_candidates(writes)
            if inserted > 0 and callable(self.on_candidates_enqueued):
                self.on_candidates_enqueued(inserted)
            return {
                "inserted": inserted,
                "pending_eval": inserted,
                "evaluating": 0,
                "attempts": 1,
            }

        async def drain_pending(self, **_kwargs: object) -> dict[str, int]:
            self.inline_drain_calls += 1
            # This is the old competing owner: it creates a second durable
            # claim while the coordinator's three workers are still blocked.
            self.database.claim_discovery_candidates_for_eval(
                limit=90,
                claim_token="legacy-inline-drain",
            )
            await self.release.wait()
            return {"evaluated": 0, "cached": 0, "rejected": 0}

        def claim_batch(self, *, limit: int) -> CandidateEvalClaim | None:
            self.claim_sequence += 1
            token = f"coordinator-{self.claim_sequence}-{id(self)}-{limit}"
            rows = self.database.claim_discovery_candidates_for_eval(
                limit=limit,
                claim_token=token,
            )
            if not rows:
                return None
            return CandidateEvalClaim(token=token, rows=tuple(rows), items=())

        async def evaluate_claim(
            self,
            claim: CandidateEvalClaim,
            _profile: object,
        ) -> CandidateEvalOutcome:
            await self.release.wait()
            return CandidateEvalOutcome(claim=claim, scores=(), elapsed_seconds=0.0)

        async def complete_claim(
            self,
            _outcome: CandidateEvalOutcome,
            *,
            admission_limit: int | None = None,
        ) -> dict[str, int]:
            del admission_limit
            return {"evaluated": 0, "cached": 0, "rejected": 0}

        def release_claim(
            self,
            claim: CandidateEvalClaim,
            *,
            reason: str,
            increment_attempts: bool = False,
        ) -> int:
            del reason, increment_attempts
            return self.database.reset_claimed_discovery_candidates_to_pending(
                [int(row["id"]) for row in claim.rows],
                claim_token=claim.token,
                reason="test cleanup",
                max_attempts=5,
                max_batch_attempts=50,
            )

        def admit_evaluated(self, *, limit: int) -> dict[str, int]:
            del limit
            return {"cached": 0, "rejected": 0}

    database = Database(tmp_path / "managed-refresh.db")
    database.initialize()
    pipeline = _BlockingManagedPipeline(database)

    def snapshot() -> CandidateEvalSnapshot:
        statuses = database.count_discovery_candidates_by_status()
        return CandidateEvalSnapshot(
            available=0,
            target=500,
            pending_eval=int(statuses.get("pending_eval", 0)),
            evaluating=int(statuses.get("evaluating", 0)),
            evaluated_pending_admission=int(statuses.get("evaluated", 0)),
            admitted_pending_copy=0,
        )

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=snapshot,
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        safety_wake_seconds=60,
    )
    enqueue_notifications: list[int] = []

    def notify_enqueued(count: int) -> None:
        enqueue_notifications.append(count)
        coordinator.notify("candidate_enqueued:pipeline")

    pipeline.on_candidates_enqueued = notify_enqueued
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_ProfileSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=500,
        discovery_limit=90,
        pool_source_shares={"bilibili": 1},
        candidate_eval_coordinator=coordinator,
    )

    coordinator_task = asyncio.create_task(coordinator.run_forever())
    refresh_task = asyncio.create_task(
        controller._run_refresh_plan(
            state=_FakeMemoryManager().load_discovery_runtime_state(),
            profile=await _ProfileSoulEngine().get_profile(),
            plan=[(["search"], 180)],
            reason="managed-refresh",
        )
    )
    try:
        async with asyncio.timeout(2):
            while int(database.count_discovery_candidates_by_status().get("evaluating", 0)) < 90:
                await asyncio.sleep(0)
        # Give a competing inline drain one scheduling turn.  On the old path
        # it grows durable evaluating rows to 180 and keeps refresh blocked.
        await asyncio.sleep(0.02)
        assert enqueue_notifications == [180]
        assert pipeline.inline_drain_calls == 0
        assert int(database.count_discovery_candidates_by_status().get("evaluating", 0)) <= 90
        assert refresh_task.done()
    finally:
        pipeline.release.set()
        if not refresh_task.done():
            await asyncio.wait_for(refresh_task, timeout=2)
        await coordinator.stop()
        await asyncio.wait_for(coordinator_task, timeout=2)


async def test_candidate_eval_drain_runs_when_refresh_plan_empty() -> None:
    pipeline = _FakeCandidatePipeline()
    recommendations = _FakeRecommendationEngine()
    memory = _FakeMemoryManager()
    database = _FakeDatabase(
        [],
        pool_count=0,
        source_available_counts={"bilibili": 30},
        source_raw_counts={"bilibili": 60},
        discovery_status_counts={"pending_eval": 5},
    )
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=recommendations,
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares={"bilibili": 1},
    )

    assert controller._build_refresh_plan(memory.load_discovery_runtime_state()) == []

    result = await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
        batch_size=30,
    )

    assert result["cached"] == 3
    assert pipeline.drains == [30]
    assert recommendations.pool_copy_calls == [({"profile": "ok"}, 60)]


async def test_candidate_eval_drain_defaults_to_larger_eval_batch() -> None:
    pipeline = _FakeCandidatePipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    result = await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
    )

    assert result["cached"] == 3
    assert pipeline.drains == [45]


async def test_candidate_eval_releases_drain_lock_before_precompute() -> None:
    pipeline = _FakeCandidatePipeline()

    class _LockInspectingRecommendationEngine(_FakeRecommendationEngine):
        def __init__(self) -> None:
            super().__init__()
            self.controller: ContinuousRefreshController | None = None
            self.locked_during_precompute: bool | None = None

        async def precompute_pool_copy(
            self,
            *,
            profile: object,
            limit: int,
        ) -> int:
            if self.controller is None:
                raise AssertionError("controller not attached")
            self.locked_during_precompute = self.controller._discovery_drain_lock.locked()
            return await super().precompute_pool_copy(profile=profile, limit=limit)

    recommendations = _LockInspectingRecommendationEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=recommendations,
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )
    recommendations.controller = controller

    await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
        batch_size=30,
    )

    assert recommendations.locked_during_precompute is False


async def test_candidate_eval_drain_with_real_database_makes_raw_candidate_available(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "candidate-eval-e2e.db")
    database.initialize()
    database.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVperiodic001",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVperiodic001",
                content_url="https://www.bilibili.com/video/BVperiodic001",
                title="周期评估端到端候选",
            )
        ]
    )
    llm = _StructuredScoringLLM(
        [
            {
                "content_id": "BVperiodic001",
                "score": 0.91,
                "reason": "fit",
                "topic_group": "tech",
                "style_key": "deep_dive",
            }
        ]
    )
    discovery_engine = ContentDiscoveryEngine(llm_service=llm, database=database)
    pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery_engine,
        pool_target_count=30,
    )
    recommendations = _RealDatabasePrecomputeEngine(database)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_ProfileSoulEngine(),
        discovery_engine=discovery_engine,
        recommendation_engine=recommendations,
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares={"bilibili": 1},
    )

    assert database.count_pool_candidates() == 0

    result = await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
        batch_size=30,
    )

    assert result == {"evaluated": 1, "cached": 1, "rejected": 0}
    assert llm.calls == 1
    assert database.count_discovery_candidates_by_status()["cached"] == 1
    assert database.count_pool_candidates() == 1
    assert [call[1] for call in recommendations.pool_copy_calls] == [60]


async def test_candidate_eval_rate_limit_releases_claims_for_recovery_with_real_database(
    tmp_path: Path,
) -> None:
    class RateLimitThenScoringLLM:
        def __init__(self, payload: list[dict[str, object]]) -> None:
            self.payload = payload
            self.calls = 0

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> object:
            self.calls += 1
            if self.calls == 1:
                raise LLMRateLimitError("provider 429 rate limit")
            candidate_block = user_input.split("<content_batch>", 1)[1].split(
                "</content_batch>", 1
            )[0]
            decoded = json.loads(candidate_block.strip())
            if isinstance(decoded, dict):
                request_items = decoded.get("items", [])
                assert isinstance(request_items, list)
                payload = [
                    {
                        **{
                            key: value
                            for key, value in self.payload[index].items()
                            if key not in {"bvid", "content_id"}
                        },
                        "id": str(index),
                    }
                    for index in range(len(request_items))
                ]
            else:
                payload = self.payload
            return _StructuredResponse(json.dumps(payload, ensure_ascii=False))

    database = Database(tmp_path / "candidate-eval-rate-limit-e2e.db")
    database.initialize()
    payload: list[dict[str, object]] = []
    writes: list[DiscoveryCandidateWrite] = []
    style_keys = [
        "deep_focus",
        "quick_scan",
        "hands_on",
        "decision_support",
        "story_immersion",
        "opinion_sparring",
        "social_chat",
        "daily_wander",
        "mood_release",
        "aesthetic_browse",
        "ambient_companion",
        "live_pulse",
        "curiosity_spark",
    ]
    for index in range(32):
        content_id = f"BVratelimit{index:02d}"
        writes.append(
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:{content_id}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=content_id,
                content_url=f"https://www.bilibili.com/video/{content_id}",
                title=f"限流恢复候选 {index}",
            )
        )
        payload.append(
            {
                "content_id": content_id,
                "score": 0.91,
                "reason": "fit after recovery",
                "topic_group": "tech",
                "style_key": style_keys[index % len(style_keys)],
            }
        )
    database.enqueue_discovery_candidates(writes)
    llm = RateLimitThenScoringLLM(payload)
    discovery_engine = ContentDiscoveryEngine(llm_service=llm, database=database)
    pipeline = DiscoveryCandidatePipeline(
        database=database,
        discovery_engine=discovery_engine,
        pool_target_count=64,
    )
    recommendations = _RealDatabasePrecomputeEngine(database)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_ProfileSoulEngine(),
        discovery_engine=discovery_engine,
        recommendation_engine=recommendations,
        discovery_candidate_pipeline=pipeline,
        pool_target_count=64,
        pool_source_shares={"bilibili": 1},
    )

    first = await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
        batch_size=32,
    )

    first_counts = database.count_discovery_candidates_by_status()
    assert first == {"evaluated": 0, "cached": 0, "rejected": 0, "failed": 32}
    assert first_counts["pending_eval"] == 32
    assert first_counts.get("evaluating", 0) == 0
    assert first_counts.get("rejected_low_score", 0) == 0

    second = await controller._drain_discovery_candidates_and_precompute(
        reason="periodic",
        batch_size=32,
    )

    assert second == {"evaluated": 32, "cached": 32, "rejected": 0}
    assert llm.calls == 2
    final_counts = database.count_discovery_candidates_by_status()
    assert final_counts["cached"] == 32
    assert final_counts.get("evaluating", 0) == 0
    cached_rows = database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0]
    assert cached_rows == 32


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


async def test_run_refresh_plan_uses_supply_loop_when_pipeline_supports_it() -> None:
    class SupplyPipeline:
        last_admitted_items: list[object] = []

        def __init__(self) -> None:
            self.supply_calls: list[dict[str, object]] = []
            self.drain_calls: list[dict[str, object]] = []

        async def ensure_pending_supply(self, **kwargs: object) -> dict[str, int]:
            self.supply_calls.append(dict(kwargs))
            return {"inserted": 6, "pending_eval": 6, "evaluating": 0, "attempts": 2}

        async def drain_pending(self, **kwargs: object) -> dict[str, int]:
            self.drain_calls.append(dict(kwargs))
            return {"evaluated": 6, "cached": 0, "rejected": 0}

    pipeline = SupplyPipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=20, source_counts={"bilibili": 12}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    result = await controller._run_refresh_plan(
        state=_FakeMemoryManager().load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["search", "explore"], 10)],
        reason="test",
    )

    assert pipeline.supply_calls
    assert pipeline.supply_calls[0]["target_pending"] == pipeline.drain_calls[0]["batch_size"]
    assert pipeline.supply_calls[0]["strategies"] == ["search", "explore"]
    assert result["supply_inserted_count"] == 6
    assert result["supply_productive"] is True


async def test_run_refresh_plan_stamps_explore_only_when_supply_attempted() -> None:
    class NoAttemptExploreSupplyPipeline:
        last_admitted_items: list[object] = []

        async def ensure_pending_supply(self, **_kwargs: object) -> dict[str, int | str]:
            return {
                "inserted": 0,
                "pending_eval": 0,
                "evaluating": 0,
                "attempts": 0,
                "reason": "target_reached",
            }

        async def drain_pending(self, **_kwargs: object) -> dict[str, int]:
            return {"evaluated": 0, "cached": 0, "rejected": 0}

    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([], pool_count=20, source_counts={"bilibili": 12}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=NoAttemptExploreSupplyPipeline(),
        pool_target_count=30,
    )

    await controller._run_refresh_plan(
        state=memory.load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["explore"], 10)],
        reason="test",
    )

    assert not memory.state["last_explore_refresh_at"]


async def test_run_refresh_plan_stamps_explore_when_supply_attempted() -> None:
    class AttemptedExploreSupplyPipeline:
        last_admitted_items: list[object] = []

        async def ensure_pending_supply(self, **_kwargs: object) -> dict[str, int]:
            return {"inserted": 0, "pending_eval": 0, "evaluating": 0, "attempts": 1}

        async def drain_pending(self, **_kwargs: object) -> dict[str, int]:
            return {"evaluated": 0, "cached": 0, "rejected": 0}

    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([], pool_count=20, source_counts={"bilibili": 12}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=AttemptedExploreSupplyPipeline(),
        pool_target_count=30,
    )

    await controller._run_refresh_plan(
        state=memory.load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["explore"], 10)],
        reason="test",
    )

    assert memory.state["last_explore_refresh_at"]


async def test_refresh_attempt_with_only_duplicate_supply_is_not_productive() -> None:
    class DuplicateOnlySupplyPipeline:
        last_admitted_items: list[object] = []

        async def ensure_pending_supply(self, **_kwargs: object) -> dict[str, int]:
            return {"inserted": 0, "pending_eval": 0, "evaluating": 0, "attempts": 3}

        async def drain_pending(self, **_kwargs: object) -> dict[str, int]:
            return {"evaluated": 0, "cached": 0, "rejected": 0}

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=20, source_counts={"bilibili": 12}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=DuplicateOnlySupplyPipeline(),
        pool_target_count=30,
    )

    result = await controller._run_refresh_plan(
        state=_FakeMemoryManager().load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["search", "related_chain"], 10)],
        reason="test",
    )

    assert result["refreshed"] is True
    assert result["supply_inserted_count"] == 0
    assert result["supply_productive"] is False


async def test_run_refresh_plan_respects_candidate_eval_batch_floor() -> None:
    class SupplyPipeline:
        min_eval_batch_size = 8
        last_admitted_items: list[object] = []

        def __init__(self) -> None:
            self.supply_calls: list[dict[str, object]] = []
            self.drain_calls: list[dict[str, object]] = []

        async def ensure_pending_supply(self, **kwargs: object) -> dict[str, int]:
            self.supply_calls.append(dict(kwargs))
            return {"inserted": 8, "pending_eval": 8, "evaluating": 0, "attempts": 1}

        async def drain_pending(self, **kwargs: object) -> dict[str, int]:
            self.drain_calls.append(dict(kwargs))
            return {"evaluated": 8, "cached": 0, "rejected": 0}

    pipeline = SupplyPipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=24, source_counts={"bilibili": 20}),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
    )

    await controller._run_refresh_plan(
        state=_FakeMemoryManager().load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["search", "explore"], 6)],
        reason="test",
    )

    assert pipeline.supply_calls[0]["target_pending"] == 8
    assert pipeline.drain_calls[0]["batch_size"] == 8
    assert pipeline.supply_calls[0]["strategy_limits"] == {"search": 4, "explore": 4}


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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
    1. Tiny gap (5): stay above replenish low-watermark and skip discovery
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
            trending_refresh_minutes=999,
            explore_refresh_minutes=999,
        )

    # Tiny gap: 95/100, gap=5 → above low-watermark; don't spend discovery LLM.
    discovery.calls.clear()
    result = await make_controller(pool_count=95, pool_target=100).refresh_if_needed()
    assert result["reason"] == "below_threshold"
    assert discovery.calls == []

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


async def test_refresh_controller_small_gap_skips_expensive_bilibili_generators() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=85),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=100,
        discovery_limit=30,
    )

    await controller.refresh_if_needed()

    assert discovery.calls[0][1] == ["search", "related_chain"]
    assert discovery.calls[0][2] == 11
    assert discovery.strategy_limit_calls[0] == {"search": 6, "related_chain": 5}


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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": datetime.now().isoformat(),
                "last_explore_refresh_at": datetime.now().isoformat(),
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    # Bilibili is under its platform quota (24/30 target pool maps to
    # bilibili=24), so the four established B strategies are merged into
    # one discover() call and get mixed in one round.
    assert len(discovery.calls) == 1
    call_profile, call_strategies, _call_limit = discovery.calls[0]
    assert call_profile == {"profile": "ok"}
    assert call_strategies == ["search", "related_chain"]


async def test_refresh_controller_backfills_bilibili_when_only_small_sources_underfilled() -> None:
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    assert result["reason"] == "triggered"
    assert set(result["strategies"]) == {"search", "related_chain", "trending", "explore"}
    assert [call[1] for call in discovery.calls] == [
        ["search", "related_chain"],
        ["trending"],
        ["explore"],
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
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
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
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": datetime.now().isoformat(),
                "last_explore_refresh_at": datetime.now().isoformat(),
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase([], pool_count=30),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    result = await controller.refresh_if_needed()

    assert result == {"refreshed": False, "strategies": [], "reason": "below_threshold"}
    assert discovery.calls == []


async def test_refresh_if_needed_runs_pool_maintenance_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(
            {
                "last_event_refresh_at": "",
                "last_trending_refresh_at": datetime.now().isoformat(),
                "last_explore_refresh_at": datetime.now().isoformat(),
                "last_processed_event_id": 0,
                "last_notification_at": "",
            }
        ),
        database=_FakeDatabase([], pool_count=30),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )
    event_loop_thread_id = threading.get_ident()
    maintenance_thread_ids: list[int] = []

    def maintenance() -> bool:
        maintenance_thread_ids.append(threading.get_ident())
        return True

    monkeypatch.setattr(controller, "_enforce_pool_cap", maintenance)

    result = await controller.refresh_if_needed()

    assert result == {"refreshed": False, "strategies": [], "reason": "below_threshold"}
    assert maintenance_thread_ids
    assert maintenance_thread_ids[0] != event_loop_thread_id


async def test_blocked_maintenance_worker_does_not_block_heartbeat_or_reshuffle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "maintenance-worker.db")
    database.initialize()
    _seed_visible_pool_row(
        database,
        "BV_WORKER_A",
        topic_group="worker-a",
        relevance_score=0.9,
    )
    _seed_visible_pool_row(
        database,
        "BV_WORKER_B",
        topic_group="worker-b",
        relevance_score=0.8,
    )
    engine = RecommendationEngine(llm=object(), database=database)  # type: ignore[arg-type]
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=engine,
        pool_target_count=1,
    )
    maintenance_started = threading.Event()
    release_maintenance = threading.Event()
    original_maintenance = database.maintain_pool_inventory

    def blocked_maintenance(**kwargs: object) -> PoolMaintenanceResult:
        maintenance_started.set()
        if not release_maintenance.wait(timeout=2):
            raise AssertionError("test did not release maintenance worker")
        return original_maintenance(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(database, "maintain_pool_inventory", blocked_maintenance)
    heartbeat_ticks = 0
    heartbeat_done = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal heartbeat_ticks
        while not heartbeat_done.is_set():
            heartbeat_ticks += 1
            await asyncio.sleep(0.005)

    heartbeat_task = asyncio.create_task(heartbeat())
    maintenance_task = asyncio.create_task(controller._enforce_pool_cap_async())
    try:
        started = await asyncio.wait_for(
            asyncio.to_thread(maintenance_started.wait, 1),
            timeout=1.2,
        )
        assert started is True

        recommendations = await asyncio.wait_for(
            engine.reshuffle_recommendations(profile=_build_profile(), limit=1),
            timeout=1.0,
        )
        assert len(recommendations) == 1
        assert maintenance_task.done() is False
        await asyncio.sleep(0.04)
        assert heartbeat_ticks >= 5
    finally:
        release_maintenance.set()
        await asyncio.wait_for(maintenance_task, timeout=2)
        heartbeat_done.set()
        await heartbeat_task

    assert database.count_pool_candidates() == 1
    database.close()


async def test_unchanged_pool_skips_heavy_maintenance_until_forced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "maintenance-fingerprint.db")
    database.initialize()
    _seed_visible_pool_row(
        database,
        "BV_STABLE_POOL",
        topic_group="stable-pool",
        relevance_score=0.9,
    )
    engine = RecommendationEngine(llm=object(), database=database)  # type: ignore[arg-type]
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=engine,
        pool_target_count=1,
    )
    maintenance_calls = 0
    original_runner = database.maintain_pool_inventory_async

    async def counted_runner(**kwargs: object) -> PoolMaintenanceResult:
        nonlocal maintenance_calls
        maintenance_calls += 1
        return await original_runner(**kwargs)

    monkeypatch.setattr(database, "maintain_pool_inventory_async", counted_runner)

    assert await controller._enforce_pool_cap_async(force_scan=True) is True
    assert await controller._enforce_pool_cap_async() is True
    assert maintenance_calls == 1

    assert await controller._enforce_pool_cap_async(force_scan=True) is True
    assert maintenance_calls == 2
    database.close()


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
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    result = await controller.refresh_if_needed()

    assert result["reason"] == "below_threshold"
    assert database.pool_count == 50
    assert database.maintenance_calls[0]["raw_ceiling"] == 150


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


def test_source_requested_count_uses_own_share_not_global_headroom() -> None:
    # Pool-share fairness spec (2026-07-20, invariant 2): per-source deficit is
    # NO LONGER clamped by the small global headroom (100-98=2). bilibili owns
    # 100% of the target (share 1), sits at 10/100, and has ample raw headroom,
    # so it requests its full own-share deficit of 90. The previous口径
    # (``min(available_deficit, global_available_deficit)`` → 2) is exactly the
    # starvation bug this fix removes.
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

    assert controller._source_requested_count("bilibili") == 90


async def test_refresh_replenishes_when_raw_ceiling_is_full_but_available_pool_is_low() -> None:
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
            [],
            pool_count=104,
            source_available_counts={"bilibili": 104},
            source_raw_counts={"bilibili": 600},
            pool_raw_count=600,
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        pool_source_shares={"bilibili": 1},
        discovery_limit=30,
    )

    result = await controller.refresh_if_needed()

    assert result["refreshed"] is True
    assert discovery.calls, "raw-ceiling pressure must not strand a low available pool"
    assert discovery.calls[0][1] == ["search", "related_chain"]


async def test_candidate_supply_wakes_all_under_quota_platform_producers() -> None:
    xhs = _FakeXhsProducer()
    douyin = _FakeDouyinProducer()
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=30,
            source_available_counts={
                "bilibili": 30,
                "xiaohongshu": 0,
                "douyin": 0,
            },
            source_raw_counts={
                "bilibili": 30,
                "xiaohongshu": 0,
                "douyin": 0,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        xhs_producer=xhs,
        douyin_producer=douyin,
        pool_target_count=90,
        pool_source_shares={"bilibili": 1, "xiaohongshu": 1, "douyin": 1},
        discovery_limit=30,
    )

    result = await controller.supply_candidates_once(reason="candidate_supply")

    assert xhs.calls == [30]
    assert douyin.calls == [30]
    # Bilibili is at its own share, but the global pool is still below target
    # and no discovery-candidate work is pending, so the periodic Bilibili
    # backfill is allowed to run in addition to the under-quota producers.
    assert [call[1] for call in discovery.calls] == [["trending"], ["explore"]]
    assert result["refreshed"] is True
    assert result["supply_progress_count"] == 6
    assert result["supply_productive"] is True


async def test_periodic_and_demand_ticks_do_not_duplicate_same_source_fetch() -> None:
    class BlockingProducer:
        def __init__(self) -> None:
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return {"enqueued": 1, "reason": "ok"}

    producer = BlockingProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=0,
            source_available_counts={"xiaohongshu": 0},
            source_raw_counts={"xiaohongshu": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        xhs_producer=producer,
        pool_target_count=30,
        pool_source_shares={"xiaohongshu": 1},
    )

    first = asyncio.create_task(controller._tick_xhs_producer())
    await producer.started.wait()
    overlapping = await controller._tick_xhs_producer()
    producer.release.set()
    await first

    assert producer.calls == 1
    assert overlapping["reason"] == "in_flight"


async def test_under_share_non_bili_producer_runs_even_when_global_pool_is_full() -> None:
    # Pool-share fairness spec (2026-07-20, invariant 2 / Goal 1): xhs owns 1/9
    # of a 100-slot target (≈11) but sits at 0. Even though the global pool is
    # full, the producer must run so its supply can wait in the raw/evaluated
    # backlog and win a freed slot once share-aware admission (Phase 2/3) makes
    # room. The old behavior (never called while global full) starved it.
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
        discovery_limit=30,
    )

    await controller._tick_xhs_producer()

    assert xhs.calls == [11]


async def test_under_share_douyin_producer_runs_even_when_global_pool_is_full() -> None:
    # Pool-share fairness spec (2026-07-20, invariant 2 / Goal 1): douyin owns
    # 1/9 (≈11) but sits at 0 while the global pool is full. It must still run
    # to feed the backlog for share-aware admission. Old behavior starved it.
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
        discovery_limit=30,
    )

    await controller._tick_douyin_producer()

    assert douyin.calls == [11]


async def test_under_share_youtube_producer_runs_even_when_global_pool_is_full() -> None:
    # Pool-share fairness spec (2026-07-20, invariant 2 / Goal 1): youtube owns
    # 1/9 (≈11) but sits at 0 while the global pool is full. It must still run
    # to feed the backlog for share-aware admission. Old behavior starved it.
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
        discovery_limit=30,
    )

    await controller._tick_youtube_producer()

    assert youtube.calls == [11]


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

    # Pool-share fairness spec (2026-07-20, invariant 2): bilibili owns 100% of
    # the 600 target and sits at 300 available → its own-share deficit is 300,
    # bounded only by raw headroom (900 here), not by the smaller global
    # headroom (600-420=180) the old口径 clamped to.
    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 300)
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


def test_source_replenishment_plan_escapes_raw_headroom_deadlock() -> None:
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

    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 30)
    ]
    assert controller._source_deficit("bilibili") == 30


def test_keyword_planner_explore_due_soon_requires_bili_deficit() -> None:
    now = datetime.now()
    last_planned = (now - timedelta(hours=12) + timedelta(seconds=30)).isoformat()
    last_refreshed = now.isoformat()
    state = _FakeMemoryManager(
        {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": "",
            "last_explore_planned_at": last_planned,
            "last_explore_refresh_at": last_refreshed,
            "last_processed_event_id": 0,
            "last_notification_at": "",
            "last_discovered_count": 0,
            "last_replenished_count": 0,
            "recent_pool_topics": [],
        }
    )
    controller = ContinuousRefreshController(
        memory_manager=state,
        database=_FakeDatabase(
            [],
            pool_count=250,
            source_available_counts={"bilibili": 250},
            source_raw_counts={"bilibili": 250},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        explore_refresh_minutes=12,
        check_interval_seconds=60,
    )

    assert controller.keyword_planner_explore_due_soon() is True

    no_bili_room = ContinuousRefreshController(
        memory_manager=state,
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 300},
            source_raw_counts={"bilibili": 300},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        explore_refresh_minutes=12,
        check_interval_seconds=60,
    )

    assert no_bili_room.keyword_planner_explore_due_soon() is False


def test_keyword_planner_mark_explore_planned_updates_refresh_state() -> None:
    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    controller.keyword_planner_mark_explore_planned()

    assert memory.state["last_explore_planned_at"]
    assert not memory.state["last_explore_refresh_at"]


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
    assert db.count_pool_candidates_by_source() == {"bilibili": 246}
    assert db.count_pool_raw_material_by_source() == {"bilibili": 246}
    assert controller._build_source_replenishment_plan() == [
        (["search", "related_chain", "trending", "explore"], 54)
    ]

    assert controller._enforce_pool_cap() is False
    assert db.count_pool_raw_material_by_source() == {"bilibili": 246}

    for index in range(54):
        _seed_visible_pool_row(
            db,
            f"BVNEW{index:02d}",
            topic_group=f"new-topic-{index}",
            relevance_score=0.80,
        )

    assert controller._enforce_pool_cap() is True
    assert db.count_pool_candidates() == 300
    assert db.count_pool_raw_material_by_source() == {"bilibili": 300}
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


def test_refresh_plan_logs_diagnostics_when_pool_below_target_but_no_plan(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=33,
            source_available_counts={"youtube": 33},
            source_raw_counts={"youtube": 600},
            pool_raw_count=600,
            pool_pending_count=9,
            discovery_status_counts={"pending_eval": 4, "evaluated": 2},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        pool_source_shares={"youtube": 1},
    )
    caplog.set_level(logging.INFO, logger="openbiliclaw.runtime.refresh")

    plan = controller._build_refresh_plan(_FakeMemoryManager().load_discovery_runtime_state())

    assert plan == []
    assert "refresh plan empty" in caplog.text
    for key in (
        "pool_available",
        "raw",
        "pending",
        "source_available",
        "source_raw",
        "source_targets",
        "raw_targets",
        "requested_by_source",
    ):
        assert key in caplog.text


def test_build_refresh_plan_falls_back_to_periodic_bilibili_plan_when_no_source_deficit() -> None:
    # Global pool is below target and below the replenishment watermark, but
    # Bilibili is already at its own share. Other sources are under-share;
    # their producers are ticked separately. The source-replenishment plan is
    # empty because it only knows Bilibili strategy fan-out, so the refresh
    # plan must fall back to the periodic Bilibili plan instead of returning
    # empty and stalling the pool below target forever.
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=237,
            source_available_counts={
                "bilibili": 100,
                "youtube": 100,
                "weibo": 37,
            },
            source_raw_counts={
                "bilibili": 100,
                "youtube": 100,
                "weibo": 37,
            },
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        pool_source_shares={"bilibili": 1, "youtube": 1, "weibo": 1},
        trending_refresh_minutes=3,
        explore_refresh_minutes=3,
    )

    plan = controller._build_refresh_plan(_FakeMemoryManager().load_discovery_runtime_state())

    assert plan == [
        (["trending"], controller.discovery_limit),
        (["explore"], controller.discovery_limit),
    ]


def test_build_refresh_plan_above_watermark_keeps_due_explore_only() -> None:
    """270-299 band must not replenish, but due explore must still run."""
    now = datetime.now()
    old_explore = (now - timedelta(minutes=5)).isoformat()
    memory = _FakeMemoryManager(
        {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": now.isoformat(),
            "last_explore_refresh_at": old_explore,
            "last_processed_event_id": 0,
            "last_notification_at": "",
        }
    )
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase([], pool_count=285),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        trending_refresh_minutes=999,
        explore_refresh_minutes=3,
    )

    plan = controller._build_refresh_plan(memory.load_discovery_runtime_state())

    assert plan == [(["explore"], controller.discovery_limit)]


def test_build_refresh_plan_splits_replenishment_and_appends_due_periodic() -> None:
    """Below-watermark replenishment only fills with search + related_chain;
    due trending / explore join as their own plan entries.
    """
    now = datetime.now()
    old_trending = (now - timedelta(minutes=5)).isoformat()
    old_explore = (now - timedelta(minutes=5)).isoformat()
    memory = _FakeMemoryManager(
        {
            "last_event_refresh_at": "",
            "last_trending_refresh_at": old_trending,
            "last_explore_refresh_at": old_explore,
            "last_processed_event_id": 0,
            "last_notification_at": "",
        }
    )
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_FakeDatabase(
            [],
            pool_count=237,
            source_available_counts={"bilibili": 237},
            source_raw_counts={"bilibili": 237},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=300,
        pool_source_shares={"bilibili": 1},
        trending_refresh_minutes=3,
        explore_refresh_minutes=3,
    )

    plan = controller._build_refresh_plan(memory.load_discovery_runtime_state())

    assert len(plan) == 3
    assert plan[0][0] == ["search", "related_chain"]
    assert plan[1] == (["trending"], controller.discovery_limit)
    assert plan[2] == (["explore"], controller.discovery_limit)


async def test_refresh_controller_uses_bilibili_deficit_for_discovery_limit() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=530,
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

    assert discovery.calls[0][1] == ["search", "related_chain"]
    assert discovery.calls[0][2] == 5
    assert discovery.strategy_limit_calls[0] == {"search": 3, "related_chain": 2}


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


def test_refresh_source_order_reuses_canonical_policy_order() -> None:
    from openbiliclaw.runtime import refresh
    from openbiliclaw.runtime.source_policy import SOURCE_ORDER

    assert refresh._PLATFORM_SOURCE_ORDER is SOURCE_ORDER


def test_warn_on_stranded_source_shares_checks_github_producer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")

    def _controller(github_producer: object | None) -> ContinuousRefreshController:
        return ContinuousRefreshController(
            memory_manager=_FakeMemoryManager(),
            database=_FakeDatabase(
                [],
                pool_count=80,
                source_counts={"bilibili": 80, "github": 0},
            ),
            soul_engine=_FakeSoulEngine(),
            discovery_engine=_FakeDiscoveryEngine(),
            recommendation_engine=_FakeRecommendationEngine(),
            pool_target_count=100,
            pool_source_shares={"bilibili": 8, "github": 2},
            github_producer=github_producer,
        )

    _controller(None)._warn_on_stranded_source_shares()
    assert "github" in caplog.text

    caplog.clear()
    _controller(object())._warn_on_stranded_source_shares()
    assert "without an active producer" not in caplog.text


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


async def test_bilibili_producer_runs_when_bilibili_under_quota() -> None:
    producer = _FakeBiliProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=595,
            source_counts={"bilibili": 475, "xiaohongshu": 60, "douyin": 60},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        discovery_limit=30,
        bilibili_producer=producer,
    )

    await controller._tick_bilibili_producer()

    assert producer.calls == [5]


async def test_bilibili_producer_skips_when_bilibili_at_quota() -> None:
    producer = _FakeBiliProducer()
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
        pool_source_shares=_MULTI_SOURCE_SHARES,
        bilibili_producer=producer,
    )

    await controller._tick_bilibili_producer()

    assert producer.calls == []


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


async def test_reddit_producer_runs_when_reddit_under_quota() -> None:
    producer = _FakeRedditProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "reddit": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "reddit": 2},
        discovery_limit=30,
        reddit_producer=producer,
    )

    await controller._tick_reddit_producer()

    assert producer.calls == [30]


async def test_reddit_producer_skips_when_reddit_at_quota() -> None:
    producer = _FakeRedditProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=600,
            source_counts={"bilibili": 480, "reddit": 120},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "reddit": 2},
        reddit_producer=producer,
    )

    await controller._tick_reddit_producer()

    assert producer.calls == []


async def test_linuxdo_producer_runs_when_linuxdo_under_quota() -> None:
    producer = _FakeRedditProducer()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(
            [],
            pool_count=540,
            source_counts={"bilibili": 480, "linuxdo": 0},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=600,
        pool_source_shares={"bilibili": 8, "linuxdo": 2},
        discovery_limit=30,
        linuxdo_producer=producer,
    )

    await controller._tick_linuxdo_producer()

    assert producer.calls == [30]


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
    call = database.maintenance_calls[0]
    assert call["raw_ceiling"] == 1200
    assert call["raw_source_share_quotas"] == {
        "bilibili": 960,
        "xiaohongshu": 120,
        "douyin": 120,
    }
    assert database.pool_raw_count == 1200


def test_pool_cap_uses_one_atomic_maintenance_entry_point() -> None:
    database = _FakeDatabase([], pool_count=20, pool_raw_count=70)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
    )

    assert controller._enforce_pool_cap() is False
    assert database.legacy_maintenance_calls == 0
    assert database.maintenance_calls == [
        {
            "target": 30,
            "raw_ceiling": 150,
            "source_share_quotas": {"bilibili": 30},
            "raw_source_share_quotas": {"bilibili": 150},
            "max_per_topic_group": 3,
            "max_per_explore_cluster": 3,
            "stale_max_age_days": 14,
            "xhs_self_nickname": "",
        }
    ]


def test_pool_cap_uses_canonical_fallback_when_begin_immediate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BeginFailure:
        def execute(self, sql: str) -> None:
            assert sql == "BEGIN IMMEDIATE"
            raise sqlite3.OperationalError("database is locked")

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    database = Database(tmp_path / "begin-failure.db")
    database.initialize()
    _seed_visible_pool_row(
        database,
        "BV_LOCKED_READY",
        topic_group="ready",
        relevance_score=0.9,
    )
    assert database.count_pool_candidates() == 1
    monkeypatch.setattr(database, "open_connection", BeginFailure)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=1,
    )

    assert controller._enforce_pool_cap() is True
    assert database.count_pool_candidates() == 1


def test_pool_cap_skips_platform_overflow_when_ready_pool_below_target() -> None:
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
    assert database.trim_overflow_source_share_quotas is None


def test_pool_cap_does_not_compose_legacy_reactivation_before_maintenance() -> None:
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
    assert database.legacy_maintenance_calls == 0
    assert database.reactivate_source_share_quotas is None
    assert database.maintenance_calls[0]["source_share_quotas"] == {
        "bilibili": 480,
        "xiaohongshu": 60,
        "douyin": 60,
    }
    assert database.pool_count == 600


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


async def test_source_incremental_loop_is_not_behind_the_llm_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = 0

    class Scheduler:
        async def tick(self) -> None:
            nonlocal ticks
            ticks += 1

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        source_incremental_sync=Scheduler(),
        check_interval_seconds=60,
    )

    def _must_not_be_called() -> bool:
        raise AssertionError("source incremental loop must not consult the LLM gate")

    controller._llm_work_allowed = _must_not_be_called  # type: ignore[method-assign]

    async def _cancel_sleep(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", _cancel_sleep)

    with pytest.raises(asyncio.CancelledError):
        await controller._loop_source_incremental_sync()

    assert ticks == 1


async def test_run_forever_owns_one_source_incremental_loop() -> None:
    started = 0
    ready = asyncio.Event()
    release = asyncio.Event()

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        source_incremental_sync=object(),
        check_interval_seconds=3600,
    )
    controller.run_startup_maintenance = lambda: None  # type: ignore[method-assign]
    controller._llm_work_allowed = lambda: False  # type: ignore[method-assign]

    async def _source_loop() -> None:
        nonlocal started
        started += 1
        ready.set()
        await release.wait()

    controller._loop_source_incremental_sync = _source_loop  # type: ignore[method-assign]
    task = asyncio.create_task(controller.run_forever())
    try:
        await asyncio.wait_for(ready.wait(), timeout=0.5)
        assert started == 1
    finally:
        release.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_run_forever_startup_order_repairs_before_llm_and_background_tasks() -> None:
    calls: list[str] = []
    candidate_started = asyncio.Event()
    expression_started = asyncio.Event()

    class _OrderedDatabase(_FakeDatabase):
        def maintain_pool_inventory(self, **kwargs: object) -> PoolMaintenanceResult:
            calls.append("maintenance")
            return super().maintain_pool_inventory(**kwargs)  # type: ignore[arg-type]

    class _OrderedCoordinator:
        async def run_forever(self) -> None:
            calls.append("candidate")
            candidate_started.set()
            await asyncio.Event().wait()

    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_OrderedDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        candidate_eval_coordinator=_OrderedCoordinator(),
        check_interval_seconds=3600,
    )

    async def _prepare() -> int:
        calls.append("prepare_delight")
        return 0

    async def _expression_loop() -> None:
        calls.append("expression")
        expression_started.set()
        await asyncio.Event().wait()

    controller.prepare_delight_candidates = _prepare  # type: ignore[method-assign]
    controller._loop_pool_precompute = _expression_loop  # type: ignore[method-assign]
    task = asyncio.create_task(controller.run_forever())
    try:
        await asyncio.wait_for(
            asyncio.gather(candidate_started.wait(), expression_started.wait()),
            timeout=0.5,
        )
        assert calls[0] == "maintenance"
        assert calls.index("maintenance") < calls.index("prepare_delight")
        assert calls.index("maintenance") < calls.index("candidate")
        assert calls.index("maintenance") < calls.index("expression")
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_run_forever_does_not_repeat_host_startup_maintenance() -> None:
    database = _FakeDatabase(events=[])
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        check_interval_seconds=3600,
    )
    prepare_started = asyncio.Event()

    async def _prepare() -> int:
        prepare_started.set()
        return 0

    controller.prepare_delight_candidates = _prepare  # type: ignore[method-assign]
    controller.run_startup_maintenance()

    task = asyncio.create_task(controller.run_forever())
    try:
        await asyncio.wait_for(prepare_started.wait(), timeout=0.5)
        assert len(database.maintenance_calls) == 1
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def test_startup_maintenance_snapshot_failure_remains_retryable() -> None:
    class _SnapshotFailureDatabase(_FakeDatabase):
        def maintain_pool_inventory(self, **kwargs: object) -> PoolMaintenanceResult:
            self.maintenance_calls.append(dict(kwargs))
            raise RuntimeError("snapshot unavailable")

    database = _SnapshotFailureDatabase(events=[])
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )
    controller.run_startup_maintenance()
    controller.run_startup_maintenance()

    assert len(database.maintenance_calls) == 2


def test_startup_maintenance_rolled_back_result_remains_retryable() -> None:
    class _RolledBackDatabase(_FakeDatabase):
        def maintain_pool_inventory(self, **kwargs: object) -> PoolMaintenanceResult:
            result = super().maintain_pool_inventory(**kwargs)  # type: ignore[arg-type]
            return replace(result, rolled_back=True, reason="forced rollback")

    database = _RolledBackDatabase(events=[])
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
    )

    controller.run_startup_maintenance()
    controller.run_startup_maintenance()

    assert len(database.maintenance_calls) == 2


async def test_hot_reload_new_controller_repairs_before_new_background_tasks() -> None:
    async def _run_controller_once(label: str) -> list[str]:
        calls: list[str] = []
        candidate_started = asyncio.Event()
        expression_started = asyncio.Event()

        class _ReloadDatabase(_FakeDatabase):
            def maintain_pool_inventory(self, **kwargs: object) -> PoolMaintenanceResult:
                calls.append(f"{label}:maintenance")
                return super().maintain_pool_inventory(**kwargs)  # type: ignore[arg-type]

        class _ReloadCoordinator:
            async def run_forever(self) -> None:
                calls.append(f"{label}:candidate")
                candidate_started.set()
                await asyncio.Event().wait()

        controller = ContinuousRefreshController(
            memory_manager=_FakeMemoryManager(),
            database=_ReloadDatabase(events=[]),
            soul_engine=_FakeSoulEngine(),
            discovery_engine=_FakeDiscoveryEngine(),
            recommendation_engine=_FakeRecommendationEngine(),
            candidate_eval_coordinator=_ReloadCoordinator(),
            check_interval_seconds=3600,
        )

        async def _prepare() -> int:
            calls.append(f"{label}:prepare_delight")
            return 0

        async def _expression_loop() -> None:
            calls.append(f"{label}:expression")
            expression_started.set()
            await asyncio.Event().wait()

        controller.prepare_delight_candidates = _prepare  # type: ignore[method-assign]
        controller._loop_pool_precompute = _expression_loop  # type: ignore[method-assign]
        task = asyncio.create_task(controller.run_forever())
        try:
            await asyncio.wait_for(
                asyncio.gather(candidate_started.wait(), expression_started.wait()),
                timeout=0.5,
            )
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        return calls

    old_calls = await _run_controller_once("old")
    new_calls = await _run_controller_once("new")

    assert old_calls[0] == "old:maintenance"
    assert new_calls[0] == "new:maintenance"
    assert new_calls.index("new:maintenance") < new_calls.index("new:prepare_delight")
    assert new_calls.index("new:maintenance") < new_calls.index("new:candidate")
    assert new_calls.index("new:maintenance") < new_calls.index("new:expression")


async def test_run_forever_starts_one_candidate_eval_coordinator() -> None:
    class _Coordinator:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
            self.run_calls = 0

        async def run_forever(self) -> None:
            self.run_calls += 1
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()

        def status_payload(self) -> dict[str, object]:
            return {
                "candidate_eval_state": "running",
                "candidate_eval_workers": 3,
            }

    coordinator = _Coordinator()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase(events=[]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        candidate_eval_coordinator=coordinator,
        check_interval_seconds=3600,
    )
    task = asyncio.create_task(controller.run_forever())
    await asyncio.wait_for(coordinator.started.wait(), timeout=0.5)

    assert coordinator.run_calls == 1
    assert controller.get_runtime_status()["candidate_eval_state"] == "running"
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await asyncio.wait_for(coordinator.stopped.wait(), timeout=0.5)


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
    assert result["reason"] == "queued"
    assert result["queued_reason"] == "event_ingest"


async def test_refresh_after_event_ingest_queues_without_running_discovery() -> None:
    discovery = _FakeDiscoveryEngine()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=discovery,
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        signal_event_threshold=1,
    )

    result = await controller.refresh_after_event_ingest()

    assert result == {
        "refreshed": False,
        "strategies": [],
        "reason": "queued",
        "queued_reason": "event_ingest",
    }
    assert discovery.calls == []


async def test_refresh_after_feedback_skips_when_scheduler_disabled() -> None:
    coordinator = _ExpressionCopyNotifySpy()
    controller = _controller_with_gate(
        scheduler_config=SimpleNamespace(enabled=False, pause_on_extension_disconnect=False),
    )
    controller.expression_copy_coordinator = coordinator

    result = await controller.refresh_after_feedback()

    assert result["refreshed"] is False
    assert result["reason"] == "queued"
    assert result["queued_reason"] == "feedback"
    assert coordinator.reasons == ["feedback"]


async def test_public_feedback_replenishment_notifies_expression_copy() -> None:
    coordinator = _ExpressionCopyNotifySpy()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([]),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        expression_copy_coordinator=coordinator,
    )

    result = await controller.request_replenishment(reason="feedback")

    assert result["state"] == "queued"
    assert coordinator.reasons == ["feedback"]


async def test_force_refresh_consumes_queued_replenishment_reasons() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=20),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    await controller.refresh_after_event_ingest()
    await controller.refresh_after_feedback()

    result = await controller.force_refresh()

    assert result["refreshed"] is True
    assert result["queued_reasons"] == ["event_ingest", "feedback"]
    assert controller._pending_replenishment_reasons == set()


async def test_refresh_after_init_triggers_replenishment_now() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([{"id": 1, "event_type": "view"}], pool_count=20),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        pool_target_count=30,
        trending_refresh_minutes=999,
        explore_refresh_minutes=999,
    )

    result = await controller.refresh_after_init()

    assert result["accepted"] is True
    assert result["state"] == "running"
    await asyncio.sleep(0.05)
    assert controller.get_runtime_status()["manual_refresh_state"] == "success"


# ── P1.7 B站 search inline-admit keyword lifecycle ───────────────────────


from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator  # noqa: E402


class _BiliKwCfg:
    def __init__(self, enabled: bool = False, fetch_batch: int = 5) -> None:
        self.unified_keyword_planner_enabled = enabled
        self.fetch_batch = fetch_batch


class _KeywordStoreFakeDatabase(_FakeDatabase):
    """``_FakeDatabase`` + the keyword-store DAO backed by a real Database."""

    def __init__(self, *args: object, kw_db: Database, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._kw_db = kw_db

    def claim_keywords(self, platform: str, n: int) -> list[dict[str, object]]:
        return self._kw_db.claim_keywords(platform, n)

    def mark_keyword_used(self, keyword_id: int) -> None:
        self._kw_db.mark_keyword_used(keyword_id)

    def mark_keyword_failed(self, keyword_id: int) -> int:
        return self._kw_db.mark_keyword_failed(keyword_id)


class _CapturingPipeline:
    """Captures the ``keywords`` injected into produce_and_enqueue; admits."""

    def __init__(self, *, cached: int = 2) -> None:
        self.produce_kwargs: list[dict[str, object]] = []
        self.last_admitted_items = [SimpleNamespace(tags=["t"], source_strategy="search")]
        self._cached = cached

    async def produce_and_enqueue(self, **kwargs: object) -> int:
        self.produce_kwargs.append(dict(kwargs))
        return 4

    async def drain_pending(self, **kwargs: object) -> dict[str, int]:
        return {"evaluated": 4, "cached": self._cached, "rejected": 0}


def _bili_kw_statuses(db: Database) -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT keyword, status FROM discovery_keywords WHERE platform = 'bilibili' ORDER BY id"
    ).fetchall()
    return {str(r["keyword"]): str(r["status"]) for r in rows}


async def test_bili_search_flag_off_does_not_claim(tmp_path: Path) -> None:
    kw_db = Database(tmp_path / "bili_off.db")
    kw_db.initialize()
    kw_db.insert_pending_keywords("bilibili", ["stored"], "dig")
    pipeline = _CapturingPipeline()
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_KeywordStoreFakeDatabase(
            [{"id": 1, "event_type": "view"}], pool_count=0, kw_db=kw_db
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        keyword_fetch=KeywordFetchCoordinator(database=kw_db, discovery_config=_BiliKwCfg(False)),
    )

    await controller.force_refresh()

    # Flag off → no keywords injected into any produce_and_enqueue; store untouched.
    assert all("keywords" not in kw for kw in pipeline.produce_kwargs)
    assert _bili_kw_statuses(kw_db) == {"stored": "pending"}


async def test_bili_search_flag_on_injects_and_marks_used(tmp_path: Path) -> None:
    kw_db = Database(tmp_path / "bili_on.db")
    kw_db.initialize()
    kw_db.insert_pending_keywords("bilibili", ["kw1", "kw2"], "dig")
    pipeline = _CapturingPipeline(cached=2)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_KeywordStoreFakeDatabase(
            [{"id": 1, "event_type": "view"}], pool_count=0, kw_db=kw_db
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        keyword_fetch=KeywordFetchCoordinator(
            database=kw_db, discovery_config=_BiliKwCfg(True, fetch_batch=5)
        ),
    )

    await controller.force_refresh()

    # The search-bearing plan entry injected the claimed words as ``keywords``.
    search_calls = [
        kw for kw in pipeline.produce_kwargs if "search" in list(kw.get("strategies", []))
    ]
    assert search_calls, "expected a produce_and_enqueue call carrying the search strategy"
    assert search_calls[0]["keywords"] == ["kw1", "kw2"]
    # Inline-admit success (discovered > 0) → both words USED.
    assert _bili_kw_statuses(kw_db) == {"kw1": "used", "kw2": "used"}


async def test_bili_search_flag_on_store_empty_drops_search_instead_of_legacy_query_gen(
    tmp_path: Path,
) -> None:
    kw_db = Database(tmp_path / "bili_empty.db")
    kw_db.initialize()
    pipeline = _CapturingPipeline(cached=2)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_KeywordStoreFakeDatabase(
            [{"id": 1, "event_type": "view"}], pool_count=0, kw_db=kw_db
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        keyword_fetch=KeywordFetchCoordinator(
            database=kw_db, discovery_config=_BiliKwCfg(True, fetch_batch=5)
        ),
    )

    await controller.force_refresh()

    assert pipeline.produce_kwargs, "expected non-search strategies to keep replenishing"
    assert all("search" not in list(kwargs["strategies"]) for kwargs in pipeline.produce_kwargs)
    assert all("keywords" not in kwargs for kwargs in pipeline.produce_kwargs)
    assert _bili_kw_statuses(kw_db) == {}


async def test_bili_search_flag_on_store_empty_reassigns_tiny_search_budget(
    tmp_path: Path,
) -> None:
    kw_db = Database(tmp_path / "bili_empty_tiny_gap.db")
    kw_db.initialize()
    pipeline = _CapturingPipeline(cached=1)
    memory = _FakeMemoryManager()
    controller = ContinuousRefreshController(
        memory_manager=memory,
        database=_KeywordStoreFakeDatabase([], pool_count=0, kw_db=kw_db),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=1,
        discovery_limit=1,
        pool_source_shares={"bilibili": 1},
        keyword_fetch=KeywordFetchCoordinator(
            database=kw_db, discovery_config=_BiliKwCfg(True, fetch_batch=5)
        ),
    )

    await controller._run_refresh_plan(
        state=memory.load_discovery_runtime_state(),
        profile={"profile": "ok"},
        plan=[(["search", "related_chain", "trending", "explore"], 1)],
        reason="tiny_gap",
    )

    call = pipeline.produce_kwargs[0]
    assert call["strategies"] == ["related_chain", "trending", "explore"]
    assert call["strategy_limits"] == {
        "related_chain": 1,
        "trending": 0,
        "explore": 0,
    }


async def test_bili_search_does_not_claim_when_eval_supply_is_full(tmp_path: Path) -> None:
    kw_db = Database(tmp_path / "bili_supply_full.db")
    kw_db.initialize()
    kw_db.insert_pending_keywords("bilibili", ["kw1"], "dig")
    pipeline = _CapturingPipeline(cached=0)
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_KeywordStoreFakeDatabase(
            [{"id": 1, "event_type": "view"}],
            pool_count=0,
            kw_db=kw_db,
            discovery_status_counts={"pending_eval": 30},
        ),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        discovery_candidate_pipeline=pipeline,
        pool_target_count=30,
        pool_source_shares=_MULTI_SOURCE_SHARES,
        keyword_fetch=KeywordFetchCoordinator(
            database=kw_db, discovery_config=_BiliKwCfg(True, fetch_batch=5)
        ),
    )

    await controller.force_refresh()

    assert all("keywords" not in kwargs for kwargs in pipeline.produce_kwargs)
    assert _bili_kw_statuses(kw_db) == {"kw1": "pending"}


class _IdleExpressionCopyCoordinator:
    """The API process's never-started coordinator: perpetual idle payload."""

    def status_payload(self) -> dict[str, object]:
        return {
            "expression_pending_count": 0,
            "expression_batch_state": "idle",
            "expression_last_completed": 0,
        }


def test_delegated_coordinator_status_overlays_local_idle_payload() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        expression_copy_coordinator=_IdleExpressionCopyCoordinator(),
        delegated_coordinator_status_reader=lambda: {
            "expression_copy": {
                "expression_pending_count": 19,
                "expression_batch_state": "backoff",
                "expression_last_completed": 0,
            },
            "candidate_eval": {
                "candidate_eval_state": "backoff",
                "candidate_eval_pending": 10,
            },
        },
    )

    status = controller.get_runtime_status()

    assert status["expression_pending_count"] == 19
    assert status["expression_batch_state"] == "backoff"
    assert status["candidate_eval_state"] == "backoff"
    assert status["candidate_eval_pending"] == 10


def test_missing_delegated_status_keeps_local_coordinator_payload() -> None:
    controller = ContinuousRefreshController(
        memory_manager=_FakeMemoryManager(),
        database=_FakeDatabase([], pool_count=0),
        soul_engine=_FakeSoulEngine(),
        discovery_engine=_FakeDiscoveryEngine(),
        recommendation_engine=_FakeRecommendationEngine(),
        expression_copy_coordinator=_IdleExpressionCopyCoordinator(),
        delegated_coordinator_status_reader=lambda: None,
    )

    status = controller.get_runtime_status()

    assert status["expression_pending_count"] == 0
    assert status["expression_batch_state"] == "idle"
