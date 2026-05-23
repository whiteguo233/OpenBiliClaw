"""Tests for discovery engine orchestration."""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    llm_eval_candidate_limit,
)
from openbiliclaw.discovery.pool_snapshot import PoolDistributionSnapshot
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.soul.profile import SoulProfile
from openbiliclaw.storage.database import Database

from .test_explore_strategy import (
    FakeBilibiliClient as FakeExploreBilibiliClient,
)
from .test_explore_strategy import (
    FakeLLMService as FakeExploreLLMService,
)
from .test_related_chain_strategy import (
    FakeLLMService as FakeRelatedLLMService,
)
from .test_related_chain_strategy import (
    FakeMemoryManager,
    FakeRelatedClient,
    _event,
)
from .test_search_strategy import FakeBilibiliClient, FakeLLMService, _build_profile
from .test_trending_strategy import FakeLLMService as FakeTrendingLLMService
from .test_trending_strategy import FakeRankingClient


@dataclass
class _SlowResponse:
    content: str


class _SlowLLMService:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0

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
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(self.delay)
        self.active_calls -= 1
        return _SlowResponse('{"score": 0.88, "reason": "still relevant"}')


class _RecentViewedDatabase:
    def __init__(
        self,
        viewed_bvids: set[str],
        *,
        viewed_content_keys: set[str] | None = None,
    ) -> None:
        self.viewed_bvids = set(viewed_bvids)
        self.viewed_content_keys = set(viewed_content_keys or viewed_bvids)

    def get_recent_viewed_bvids(self) -> set[str]:
        return set(self.viewed_bvids)

    def get_recent_viewed_content_keys(self) -> set[str]:
        return set(self.viewed_content_keys)

    def get_latest_event_id(self) -> int:
        return 0

    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        return []


class _RecordingCacheDatabase(_RecentViewedDatabase):
    def __init__(self, viewed_bvids: set[str]) -> None:
        super().__init__(viewed_bvids)
        self.cached_bvids: list[str] = []

    def count_pool_by_franchise(self) -> dict[str, int]:
        return {}

    def cache_content(self, bvid: str, **kwargs: object) -> None:
        self.cached_bvids.append(bvid)


async def _contend_llm_semaphore(
    controller: DiscoveryConcurrencyController,
    *,
    delay: float = 0.01,
) -> None:
    async def _job() -> str:
        await asyncio.sleep(delay)
        return "ok"

    await asyncio.gather(
        controller.run_llm(_job()),
        controller.run_llm(_job()),
    )


async def _contend_bilibili_semaphore(
    controller: DiscoveryConcurrencyController,
    *,
    delay: float = 0.01,
) -> None:
    async def _job() -> str:
        await asyncio.sleep(delay)
        return "ok"

    await asyncio.gather(
        controller.run_bilibili(_job()),
        controller.run_bilibili(_job()),
    )


def test_discovery_concurrency_controller_survives_multiple_event_loops() -> None:
    controller = DiscoveryConcurrencyController(
        bilibili_request_concurrency=1,
        llm_evaluation_concurrency=1,
    )

    asyncio.run(_contend_llm_semaphore(controller))
    asyncio.run(_contend_bilibili_semaphore(controller))
    asyncio.run(_contend_llm_semaphore(controller))
    asyncio.run(_contend_bilibili_semaphore(controller))


@pytest.mark.asyncio
async def test_discovery_engine_runs_registered_search_strategy() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    engine = ContentDiscoveryEngine()
    strategy = SearchStrategy(
        llm_service=FakeLLMService('{"queries": ["纪录片 原理"]}'),
        bilibili_client=FakeBilibiliClient(
            {"纪录片 原理": [{"bvid": "BV1A", "title": "纪录片", "author": "UP1", "mid": 1}]}
        ),
        llm_evaluation=False,
    )
    engine.register_strategy(strategy)

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1A"
    assert results[0].source_strategy == "search"


@pytest.mark.asyncio
async def test_evaluate_content_passes_style_preferences_to_prompt() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.82, "reason": "匹配", "topic_group": "摄影", "style_key": "light_chat"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    profile = _build_profile()
    profile.preferences.style.preferred_duration = "short"
    profile.preferences.style.humor_preference = 0.8
    profile.preferences.style.depth_preference = 0.25

    await engine.evaluate_content(
        DiscoveredContent(
            bvid="BV1STYLE",
            title="摄影散步 vlog",
            description="轻松聊拍照",
            source_strategy="search",
        ),
        profile,
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"preferred_duration": "short"' in user_input
    assert '"humor_preference": 0.8' in user_input
    assert '"depth_preference": 0.25' in user_input


@pytest.mark.asyncio
async def test_evaluate_content_passes_disliked_topics_to_prompt() -> None:
    llm_service = FakeLLMService(
        '{"score": 0.52, "reason": "命中避雷", "topic_group": "混剪", "style_key": "light_chat"}'
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    profile = _build_profile()
    profile.preferences.disliked_topics = ["标题党", "低质混剪"]

    await engine.evaluate_content(
        DiscoveredContent(
            bvid="BV1DISLIKE",
            title="震惊体低质混剪",
            description="标题党式盘点",
            source_strategy="search",
        ),
        profile,
    )

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"disliked_topics": [' in user_input
    assert "标题党" in user_input
    assert "低质混剪" in user_input


@pytest.mark.asyncio
async def test_evaluate_content_batch_skips_recently_viewed_before_llm() -> None:
    llm_service = FakeLLMService(
        json.dumps(
            [
                {
                    "bvid": "BV1FRESH",
                    "score": 0.88,
                    "reason": "fresh match",
                    "topic_group": "AI工具",
                    "style_key": "practical_guide",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=_RecentViewedDatabase({"BV1VIEWED"}),  # type: ignore[arg-type]
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(bvid="BV1VIEWED", title="已经看过", source_strategy="trending"),
            DiscoveredContent(bvid="BV1FRESH", title="新内容", source_strategy="trending"),
        ],
        _build_profile(),
    )

    assert scores == [0.0, 0.88]
    assert len(llm_service.calls) == 1
    user_input = str(llm_service.calls[0]["user_input"])
    assert "BV1FRESH" in user_input
    assert "BV1VIEWED" not in user_input
    assert "已经看过" not in user_input


@pytest.mark.asyncio
async def test_evaluate_content_batch_skips_recently_viewed_non_bilibili_before_llm() -> None:
    llm_service = FakeLLMService(
        json.dumps(
            [
                {
                    "content_id": "fresh-yt",
                    "score": 0.82,
                    "reason": "fresh youtube match",
                    "topic_group": "AI工具",
                    "style_key": "practical_guide",
                }
            ],
            ensure_ascii=False,
        )
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=_RecentViewedDatabase(
            set(),
            viewed_content_keys={"youtube:seen-yt"},
        ),  # type: ignore[arg-type]
    )

    scores = await engine.evaluate_content_batch(
        [
            DiscoveredContent(
                content_id="seen-yt",
                source_platform="youtube",
                title="已经看过的 YouTube",
                source_strategy="youtube_search",
            ),
            DiscoveredContent(
                content_id="fresh-yt",
                source_platform="youtube",
                title="新的 YouTube",
                source_strategy="youtube_search",
            ),
        ],
        _build_profile(),
    )

    assert scores == [0.0, 0.82]
    assert len(llm_service.calls) == 1
    user_input = str(llm_service.calls[0]["user_input"])
    assert "fresh-yt" in user_input
    assert "seen-yt" not in user_input
    assert "已经看过的 YouTube" not in user_input


def test_cache_results_skips_recently_viewed_items() -> None:
    database = _RecordingCacheDatabase({"BV1VIEWED"})
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]

    engine._cache_results(
        [
            DiscoveredContent(bvid="BV1VIEWED", title="已经看过"),
            DiscoveredContent(bvid="BV1FRESH", title="新内容"),
        ]
    )

    assert database.cached_bvids == ["BV1FRESH"]


def test_cache_results_skips_recently_viewed_non_bilibili_items() -> None:
    database = _RecordingCacheDatabase(set())
    database.viewed_content_keys = {"xiaohongshu:note-seen"}
    engine = ContentDiscoveryEngine(database=database)  # type: ignore[arg-type]

    engine._cache_results(
        [
            DiscoveredContent(
                content_id="note-seen",
                source_platform="xiaohongshu",
                title="已经看过的小红书",
            ),
            DiscoveredContent(
                content_id="note-fresh",
                source_platform="xiaohongshu",
                title="新小红书",
            ),
        ]
    )

    assert database.cached_bvids == ["note-fresh"]


@pytest.mark.asyncio
async def test_discovery_engine_handles_empty_strategy_results() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        SearchStrategy(
            llm_service=FakeLLMService('{"queries": []}'),
            bilibili_client=FakeBilibiliClient({}),
            llm_evaluation=False,
        )
    )

    results = await engine.discover(SoulProfile())

    assert results == []


@pytest.mark.asyncio
async def test_discovery_engine_runs_registered_trending_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeTrendingLLMService(
            [
                '{"rids": [36]}',
                '{"score": 0.83, "reason": "符合你的深度内容偏好。"}',
            ]
        )
    )
    engine.register_strategy(
        TrendingStrategy(
            bilibili_client=FakeRankingClient(
                {
                    0: [{"bvid": "BV1A", "title": "全站榜", "author": "UP1", "mid": 1}],
                    36: [],
                }
            ),
            llm_service=engine._llm_service,
            score_threshold=0.65,
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1A"
    assert results[0].source_strategy == "trending"


@pytest.mark.asyncio
async def test_discovery_engine_runs_related_chain_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeRelatedLLMService(['{"score": 0.84, "reason": "延续了近期观看兴趣。"}'])
    )
    engine.register_strategy(
        RelatedChainStrategy(
            bilibili_client=FakeRelatedClient(
                {
                    "BV1SEED": [
                        {
                            "bvid": "BV1REL",
                            "title": "相关推荐",
                            "owner": {"name": "UPR", "mid": 10},
                        }
                    ]
                }
            ),
            llm_service=engine._llm_service,
            memory_manager=FakeMemoryManager(events=[_event("BV1SEED")]),
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1REL"
    assert results[0].source_strategy == "related_chain"


@pytest.mark.asyncio
async def test_discovery_engine_runs_explore_strategy() -> None:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    engine = ContentDiscoveryEngine(
        llm_service=FakeExploreLLMService(
            [
                """
                {
                  "domains": [
                    {
                      "domain": "城市空间与建筑叙事",
                      "why_it_might_resonate": "你偏好理解复杂系统。",
                      "novelty_level": 0.7,
                      "queries": ["城市 建筑 纪录片"]
                    }
                  ]
                }
                """,
                '{"score": 0.84, "reason": "这个陌生主题仍然符合你的理解欲。"}',
            ]
        )
    )
    engine.register_strategy(
        ExploreStrategy(
            llm_service=engine._llm_service,
            bilibili_client=FakeExploreBilibiliClient(
                {
                    "城市 建筑 纪录片": [
                        {"bvid": "BV1EXP", "title": "城市建筑", "author": "UPX", "mid": 9}
                    ]
                }
            ),
            score_threshold=0.65,
        )
    )

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1EXP"
    assert results[0].source_strategy == "explore"


class _RecordingStrategy:
    def __init__(
        self,
        name: str,
        result: list[DiscoveredContent],
        *,
        delay: float = 0.0,
        should_fail: bool = False,
        started: list[str] | None = None,
    ) -> None:
        self._name = name
        self._result = result
        self._delay = delay
        self._should_fail = should_fail
        self._started = started if started is not None else []
        self.limits: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        self._started.append(self._name)
        self.limits.append(limit)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._should_fail:
            raise RuntimeError(f"boom: {self._name}")
        return self._result[:limit]


class _BackfillAwareStrategy(_RecordingStrategy):
    def __init__(
        self,
        name: str,
        result: list[DiscoveredContent],
        *,
        backfill_result: list[DiscoveredContent],
        started: list[str] | None = None,
        backfill_started: list[str] | None = None,
    ) -> None:
        super().__init__(name, result, started=started)
        self._backfill_result = backfill_result
        self._backfill_started = backfill_started if backfill_started is not None else []

    def create_backfill_strategy(self) -> _RecordingStrategy:
        return _RecordingStrategy(
            f"{self.name}-backfill",
            self._backfill_result,
            started=self._backfill_started,
        )


class _PoolSnapshotStrategy(_RecordingStrategy):
    def __init__(self) -> None:
        super().__init__(
            "snapshot-aware",
            [
                DiscoveredContent(
                    bvid="BV1SNAP",
                    relevance_score=0.9,
                    source_strategy="snapshot-aware",
                )
            ],
        )
        self.pool_snapshots: list[object | None] = []

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        pool_snapshot: object | None = None,
    ) -> list[DiscoveredContent]:
        self.pool_snapshots.append(pool_snapshot)
        return await super().discover(profile, limit=limit)


class _PoolSnapshotBackfillStrategy(_RecordingStrategy):
    def __init__(self, backfill_strategy: _PoolSnapshotStrategy) -> None:
        super().__init__(
            "snapshot-primary",
            [
                DiscoveredContent(
                    bvid="BV1PRIMARY",
                    relevance_score=0.9,
                    source_strategy="snapshot-primary",
                )
            ],
        )
        self._backfill_strategy = backfill_strategy

    def create_backfill_strategy(self) -> _PoolSnapshotStrategy:
        return self._backfill_strategy


@pytest.mark.asyncio
async def test_register_strategy_replaces_existing_strategy_with_same_name() -> None:
    started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "douyin_direct",
            [DiscoveredContent(bvid="dy:old", source_strategy="old")],
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "douyin_direct",
            [DiscoveredContent(bvid="dy:new", source_strategy="new")],
            started=started,
        )
    )

    results = await engine.discover(
        _build_profile(),
        strategies=["douyin_direct"],
        limit=20,
    )

    assert started == ["douyin_direct"]
    assert [item.bvid for item in results] == ["dy:new"]


@pytest.mark.asyncio
async def test_discovery_engine_passes_pool_snapshot_to_supported_strategy() -> None:
    pool_snapshot = object()
    strategy = _PoolSnapshotStrategy()
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["snapshot-aware"],
        limit=1,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BV1SNAP"]
    assert strategy.pool_snapshots == [pool_snapshot]


@pytest.mark.asyncio
async def test_discovery_engine_keeps_legacy_strategy_signature() -> None:
    strategy = _RecordingStrategy(
        "legacy",
        [
            DiscoveredContent(
                bvid="BV1LEGACY",
                relevance_score=0.9,
                source_strategy="legacy",
            )
        ],
    )
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["legacy"],
        limit=1,
        pool_snapshot=object(),
    )

    assert [item.bvid for item in results] == ["BV1LEGACY"]
    assert strategy.limits == [1]


@pytest.mark.asyncio
async def test_discovery_engine_passes_pool_snapshot_to_backfill_strategy() -> None:
    pool_snapshot = object()
    backfill_strategy = _PoolSnapshotStrategy()
    engine = ContentDiscoveryEngine(target_primary_count=2)
    engine.register_strategy(_PoolSnapshotBackfillStrategy(backfill_strategy))

    results = await engine.discover(
        _build_profile(),
        strategies=["snapshot-primary"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BV1PRIMARY", "BV1SNAP"]
    assert backfill_strategy.pool_snapshots == [pool_snapshot]


@pytest.mark.asyncio
async def test_pool_snapshot_soft_rerank_prefers_undercovered_topics_without_dropping_strong_matches(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sat = DiscoveredContent(
        bvid="BVsat",
        title="AI",
        topic_group="AI 编程",
        style_key="deep_dive",
        relevance_score=0.82,
    )
    gap = DiscoveredContent(
        bvid="BVgap",
        title="纪录",
        topic_group="人物纪录",
        style_key="story_doc",
        relevance_score=0.79,
    )
    strong = DiscoveredContent(
        bvid="BVstrong",
        title="AI high",
        topic_group="AI 编程",
        relevance_score=0.96,
    )
    pool_snapshot = PoolDistributionSnapshot(
        pool_target_count=10,
        pool_available_count=10,
        source_targets={},
        source_counts={},
        source_deficits={},
        saturated_topics=("AI 编程",),
        undercovered_axes=("人物纪录",),
    )

    class _ThreeCandidateStrategy(_RecordingStrategy):
        async def discover(
            self,
            profile: SoulProfile,
            limit: int = 20,
        ) -> list[DiscoveredContent]:
            self.limits.append(limit)
            return [sat, gap, strong]

    strategy = _ThreeCandidateStrategy("search", [sat, gap, strong])
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)
    monkeypatch.setattr(
        ContentDiscoveryEngine,
        "_compress_topic_repeats",
        staticmethod(lambda results, *, limit: results[:limit]),
    )
    monkeypatch.setattr(engine, "_cache_results", lambda results: None)

    results = await engine.discover(
        _build_profile(),
        strategies=["search"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BVstrong", "BVgap"]
    assert gap.relevance_score == 0.79
    assert sat.relevance_score == 0.82


@pytest.mark.asyncio
async def test_pool_snapshot_soft_rerank_runs_before_real_compression() -> None:
    strong = DiscoveredContent(
        bvid="BVstrong",
        title="AI high",
        topic_group="AI 编程",
        style_key="tech_analysis",
        source_strategy="search",
        relevance_score=0.96,
    )
    weak_saturated = DiscoveredContent(
        bvid="BVsatweak",
        title="AI tool",
        topic_group="AI 工具",
        style_key="deep_dive",
        source_strategy="explore",
        relevance_score=0.82,
    )
    gap = DiscoveredContent(
        bvid="BVgap",
        title="人物纪录",
        topic_group="人物纪录",
        style_key="story_doc",
        source_strategy="related_chain",
        relevance_score=0.79,
    )
    pool_snapshot = PoolDistributionSnapshot(
        pool_target_count=10,
        pool_available_count=10,
        source_targets={},
        source_counts={},
        source_deficits={},
        saturated_topics=("AI 编程", "AI 工具"),
        undercovered_axes=("人物纪录",),
    )

    class _ThreeSourceStrategy(_RecordingStrategy):
        async def discover(
            self,
            profile: SoulProfile,
            limit: int = 20,
        ) -> list[DiscoveredContent]:
            self.limits.append(limit)
            return [strong, weak_saturated, gap]

    strategy = _ThreeSourceStrategy("search", [strong, weak_saturated, gap])
    engine = ContentDiscoveryEngine()
    engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["search"],
        limit=2,
        pool_snapshot=pool_snapshot,
    )

    assert [item.bvid for item in results] == ["BVstrong", "BVgap"]
    assert "BVsatweak" not in {item.bvid for item in results}
    assert strong.relevance_score == 0.96
    assert gap.relevance_score == 0.79


def test_llm_eval_candidate_limit_uses_tighter_small_gap_window() -> None:
    assert llm_eval_candidate_limit(1) == 6
    assert llm_eval_candidate_limit(3) == 6
    assert llm_eval_candidate_limit(30) == 60


@pytest.mark.asyncio
async def test_discovery_engine_applies_strategy_specific_limits() -> None:
    started: list[str] = []
    search = _RecordingStrategy(
        "search",
        [
            DiscoveredContent(
                bvid=f"BVSEARCH{index}",
                relevance_score=0.9 - index * 0.01,
                source_strategy="search",
            )
            for index in range(5)
        ],
        started=started,
    )
    related = _RecordingStrategy(
        "related_chain",
        [
            DiscoveredContent(
                bvid=f"BVRELATED{index}",
                relevance_score=0.8 - index * 0.01,
                source_strategy="related_chain",
            )
            for index in range(5)
        ],
        started=started,
    )
    trending = _RecordingStrategy(
        "trending",
        [
            DiscoveredContent(
                bvid=f"BVTREND{index}",
                relevance_score=0.7 - index * 0.01,
                source_strategy="trending",
            )
            for index in range(5)
        ],
        started=started,
    )
    explore = _RecordingStrategy(
        "explore",
        [
            DiscoveredContent(
                bvid=f"BVEXPLORE{index}",
                relevance_score=0.6 - index * 0.01,
                source_strategy="explore",
            )
            for index in range(5)
        ],
        started=started,
    )
    engine = ContentDiscoveryEngine()
    for strategy in (search, related, trending, explore):
        engine.register_strategy(strategy)

    results = await engine.discover(
        _build_profile(),
        strategies=["search", "related_chain", "trending", "explore"],
        limit=5,
        strategy_limits={
            "search": 2,
            "related_chain": 1,
            "trending": 1,
            "explore": 1,
        },
    )

    assert started == ["search", "related_chain", "trending", "explore"]
    assert search.limits == [2]
    assert related.limits == [1]
    assert trending.limits == [1]
    assert explore.limits == [1]
    assert len(results) == 5


@pytest.mark.asyncio
async def test_discovery_engine_runs_strategies_concurrently_and_tolerates_failures() -> None:
    started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "slow-search",
            [DiscoveredContent(bvid="BV1A", relevance_score=0.72, source_strategy="search")],
            delay=0.02,
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "fast-failing",
            [],
            delay=0.0,
            should_fail=True,
            started=started,
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "fast-trending",
            [DiscoveredContent(bvid="BV1B", relevance_score=0.81, source_strategy="trending")],
            delay=0.0,
            started=started,
        )
    )

    results = await engine.discover(_build_profile(), limit=20)

    assert started == ["slow-search", "fast-failing", "fast-trending"]
    assert [item.bvid for item in results] == ["BV1B", "BV1A"]


@pytest.mark.asyncio
async def test_discovery_engine_keeps_highest_scored_duplicate() -> None:
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _RecordingStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1DUP",
                    title="低分版本",
                    relevance_score=0.52,
                    source_strategy="search",
                )
            ],
        )
    )
    engine.register_strategy(
        _RecordingStrategy(
            "trending",
            [
                DiscoveredContent(
                    bvid="BV1DUP",
                    title="高分版本",
                    relevance_score=0.91,
                    source_strategy="trending",
                )
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=20)

    assert len(results) == 1
    assert results[0].title == "高分版本"
    assert results[0].source_strategy == "trending"


@pytest.mark.asyncio
async def test_discovery_engine_compresses_repeated_topic_keys_in_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1INTA",
                    title="中东局势 A",
                    relevance_score=0.96,
                    source_strategy="search",
                    topic_key="国际时事:地缘政治",
                ),
                DiscoveredContent(
                    bvid="BV1INTB",
                    title="中东局势 B",
                    relevance_score=0.95,
                    source_strategy="related_chain",
                    topic_key="国际时事:地缘政治",
                ),
                DiscoveredContent(
                    bvid="BV1AI",
                    title="模型能力边界",
                    relevance_score=0.9,
                    source_strategy="search",
                    topic_key="AI:大模型",
                ),
                DiscoveredContent(
                    bvid="BV1DOC",
                    title="城市纪录片",
                    relevance_score=0.89,
                    source_strategy="explore",
                    topic_key="纪录片:城市",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=3)

    assert results[0].bvid == "BV1INTA"
    assert {item.bvid for item in results} == {"BV1INTA", "BV1AI", "BV1DOC"}


@pytest.mark.asyncio
async def test_discovery_engine_limits_explore_dominance_in_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1SEARCH",
                    title="搜索补进",
                    relevance_score=0.9,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BV1TREND",
                    title="热榜补进",
                    relevance_score=0.89,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="news_brief",
                ),
                DiscoveredContent(
                    bvid="BV1EXP1",
                    title="探索一",
                    relevance_score=0.96,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BV1EXP2",
                    title="探索二",
                    relevance_score=0.95,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BV1EXP3",
                    title="探索三",
                    relevance_score=0.94,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="light_chat",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=4)

    picked_sources = [item.source_strategy for item in results]

    assert picked_sources.count("explore") <= 2
    assert "search" in picked_sources
    assert "trending" in picked_sources


@pytest.mark.asyncio
async def test_discovery_engine_limits_source_and_style_dominance_for_larger_pool() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "mixed",
            [
                DiscoveredContent(
                    bvid="BVEXP1",
                    title="探索纪录片 1",
                    relevance_score=0.99,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVEXP2",
                    title="探索深挖 2",
                    relevance_score=0.98,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BVEXP3",
                    title="探索轻聊 3",
                    relevance_score=0.97,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVEXP4",
                    title="探索攻略 4",
                    relevance_score=0.96,
                    source_strategy="explore",
                    topic_key="探索:4",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BVREL1",
                    title="相关推荐机制拆解 1",
                    relevance_score=0.95,
                    source_strategy="related_chain",
                    topic_key="相关:1",
                    style_key="game_strategy",
                ),
                DiscoveredContent(
                    bvid="BVREL2",
                    title="相关推荐机制拆解 2",
                    relevance_score=0.94,
                    source_strategy="related_chain",
                    topic_key="相关:2",
                    style_key="game_strategy",
                ),
                DiscoveredContent(
                    bvid="BVREL3",
                    title="相关推荐故事向 3",
                    relevance_score=0.935,
                    source_strategy="related_chain",
                    topic_key="相关:3",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVSEA1",
                    title="搜索教程 1",
                    relevance_score=0.93,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="practical_guide",
                ),
                DiscoveredContent(
                    bvid="BVSEA2",
                    title="搜索快讯 2",
                    relevance_score=0.92,
                    source_strategy="search",
                    topic_key="搜索:2",
                    style_key="news_brief",
                ),
                DiscoveredContent(
                    bvid="BVTR1",
                    title="热榜纪录片 1",
                    relevance_score=0.91,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVTR2",
                    title="热榜视觉 2",
                    relevance_score=0.9,
                    source_strategy="trending",
                    topic_key="热榜:2",
                    style_key="visual_showcase",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=10)

    picked_sources = [item.source_strategy for item in results]
    picked_styles = [item.style_key for item in results]

    assert picked_sources.count("explore") <= 3
    assert picked_sources.count("related_chain") <= 3
    assert len(results) == 10
    assert picked_styles.count("game_strategy") <= 3


def test_infer_style_key_classifies_hard_courses_and_documentaries() -> None:
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="【强化学习的数学原理】课程：从零开始到透彻理解",
            source_strategy="explore",
        )
        == "practical_guide"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="精密加工的磨床纪录片",
            source_strategy="explore",
        )
        == "story_doc"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="CPU芯片经显微镜放大到纳米级别",
            source_strategy="explore",
        )
        == "tech_analysis"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="钛制造全过程，一般人没见过，工艺难度超乎你的想象",
            source_strategy="explore",
        )
        == "story_doc"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="【从零看懂fsf】世界观/伪从者设定解析",
            source_strategy="explore",
        )
        == "deep_dive"
    )
    assert (
        ContentDiscoveryEngine.infer_style_key(
            title="囚犯盒子问题，史上最烧脑的逻辑谜题，超乎你的想象！",
            source_strategy="explore",
        )
        == "deep_dive"
    )


@pytest.mark.asyncio
async def test_discovery_engine_keeps_non_explore_sources_when_style_repeats() -> None:
    class _UnlimitedStrategy(_RecordingStrategy):
        async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
            self._started.append(self._name)
            return list(self._result)

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _UnlimitedStrategy(
            "mixed",
            [
                DiscoveredContent(
                    bvid="BVEXP1",
                    title="探索深挖 1",
                    relevance_score=0.99,
                    source_strategy="explore",
                    topic_key="探索:1",
                    style_key="deep_dive",
                ),
                DiscoveredContent(
                    bvid="BVEXP2",
                    title="探索深挖 2",
                    relevance_score=0.98,
                    source_strategy="explore",
                    topic_key="探索:2",
                    style_key="story_doc",
                ),
                DiscoveredContent(
                    bvid="BVEXP3",
                    title="探索深挖 3",
                    relevance_score=0.97,
                    source_strategy="explore",
                    topic_key="探索:3",
                    style_key="visual_showcase",
                ),
                DiscoveredContent(
                    bvid="BVSEA1",
                    title="搜索杂谈 1",
                    relevance_score=0.96,
                    source_strategy="search",
                    topic_key="搜索:1",
                    style_key="light_chat",
                ),
                DiscoveredContent(
                    bvid="BVTR1",
                    title="热榜杂谈 1",
                    relevance_score=0.95,
                    source_strategy="trending",
                    topic_key="热榜:1",
                    style_key="light_chat",
                ),
            ],
        )
    )

    results = await engine.discover(_build_profile(), limit=3)

    picked_sources = [item.source_strategy for item in results]

    assert "search" in picked_sources
    assert "trending" in picked_sources
    assert picked_sources.count("explore") <= 1


@pytest.mark.asyncio
async def test_discovery_engine_caches_final_results() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="BV1A",
                        title="缓存内容 A",
                        up_name="UPA",
                        relevance_score=0.88,
                        source_strategy="search",
                    ),
                    DiscoveredContent(
                        bvid="BV1B",
                        title="缓存内容 B",
                        up_name="UPB",
                        relevance_score=0.74,
                        source_strategy="explore",
                    ),
                ],
            )
        )

        results = await engine.discover(_build_profile(), limit=20)
        cached = db.get_cached_content(limit=10)

        assert [item.bvid for item in results] == ["BV1A", "BV1B"]
        assert [item["bvid"] for item in cached] == ["BV1A", "BV1B"]
        assert cached[0]["source"] == "search"


@pytest.mark.asyncio
async def test_discovery_engine_cache_results_preserves_multi_source_fields() -> None:
    """Regression: rescoring xhs rows must not overwrite source_platform.

    Previously `_cache_results` dropped `source_platform` / `content_id` /
    `content_url` on the cache_content call, so the upsert reverted xhs
    rows to the `bilibili` default — producing rows labeled with
    `source_platform='bilibili'` even though their bvid was an xhs note id.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="6613e9ac000000001a015e65",
                        title="鸡煲复刻",
                        up_name="作者A",
                        relevance_score=0.7,
                        source_strategy="xhs-extension-task",
                        content_id="6613e9ac000000001a015e65",
                        content_url="https://www.xiaohongshu.com/explore/6613e9ac000000001a015e65",
                        source_platform="xiaohongshu",
                    )
                ],
            )
        )

        await engine.discover(_build_profile(), limit=20)

        row = db.conn.execute(
            "SELECT source, source_platform, content_id, content_url "
            "FROM content_cache WHERE bvid=?",
            ("6613e9ac000000001a015e65",),
        ).fetchone()
        assert row is not None
        assert row["source_platform"] == "xiaohongshu"
        assert row["source"] == "xhs-extension-task"
        assert row["content_id"] == "6613e9ac000000001a015e65"
        assert row["content_url"].endswith("/6613e9ac000000001a015e65")


def test_merge_duplicates_uses_multi_source_content_identity() -> None:
    first = DiscoveredContent(
        content_id="yt-a",
        source_platform="youtube",
        title="YouTube A",
        relevance_score=0.6,
    )
    second = DiscoveredContent(
        content_id="yt-b",
        source_platform="youtube",
        title="YouTube B",
        relevance_score=0.5,
    )
    duplicate = DiscoveredContent(
        content_id="yt-a",
        source_platform="youtube",
        title="YouTube A better",
        relevance_score=0.9,
    )

    merged = ContentDiscoveryEngine._merge_duplicates([first, second, duplicate])

    assert [item.content_id for item in merged] == ["yt-a", "yt-b"]
    assert merged[0].title == "YouTube A better"


@pytest.mark.asyncio
async def test_discovery_engine_cache_results_preserves_relevance_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        engine = ContentDiscoveryEngine(database=db)
        engine.register_strategy(
            _RecordingStrategy(
                "search",
                [
                    DiscoveredContent(
                        bvid="BV1A",
                        title="缓存内容 A",
                        up_name="UPA",
                        relevance_score=0.88,
                        relevance_reason="fits profile",
                        source_strategy="search",
                    )
                ],
            )
        )

        await engine.discover(_build_profile(), limit=20)
        cached = db.get_cached_content(limit=1)

        assert cached[0]["relevance_score"] == 0.88
        assert cached[0]["relevance_reason"] == "fits profile"
        assert cached[0]["candidate_tier"] == "primary"


@pytest.mark.asyncio
async def test_discovery_engine_backfills_when_primary_results_too_few() -> None:
    started: list[str] = []
    backfill_started: list[str] = []
    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        _BackfillAwareStrategy(
            "search",
            [
                DiscoveredContent(
                    bvid="BV1PRIMARY",
                    title="主候选",
                    relevance_score=0.91,
                    candidate_tier="primary",
                    source_strategy="search",
                )
            ],
            backfill_result=[
                DiscoveredContent(
                    bvid="BV1BACK1",
                    title="补货 1",
                    relevance_score=0.73,
                    candidate_tier="backfill",
                    source_strategy="search",
                ),
                DiscoveredContent(
                    bvid="BV1BACK2",
                    title="补货 2",
                    relevance_score=0.68,
                    candidate_tier="backfill",
                    source_strategy="search",
                ),
            ],
            started=started,
            backfill_started=backfill_started,
        )
    )

    results = await engine.discover(_build_profile(), limit=18)

    assert started == ["search"]
    assert backfill_started == ["search-backfill"]
    assert [item.bvid for item in results] == ["BV1PRIMARY", "BV1BACK1", "BV1BACK2"]
    assert [item.candidate_tier for item in results] == ["primary", "backfill", "backfill"]


@pytest.mark.asyncio
async def test_discovery_engine_skips_backfill_when_primary_results_enough() -> None:
    started: list[str] = []
    backfill_started: list[str] = []
    engine = ContentDiscoveryEngine()
    primary_results = [
        DiscoveredContent(
            bvid=f"BV1{index:02d}",
            title=f"主候选 {index}",
            relevance_score=0.95 - index * 0.01,
            candidate_tier="primary",
            source_strategy="search",
        )
        for index in range(25)
    ]
    engine.register_strategy(
        _BackfillAwareStrategy(
            "search",
            primary_results,
            backfill_result=[
                DiscoveredContent(
                    bvid="BV1BACK",
                    title="补货",
                    relevance_score=0.5,
                    candidate_tier="backfill",
                    source_strategy="search",
                )
            ],
            started=started,
            backfill_started=backfill_started,
        )
    )

    results = await engine.discover(_build_profile(), limit=40)

    assert started == ["search"]
    assert backfill_started == []
    assert len(results) == 25
    assert all(item.candidate_tier == "primary" for item in results)


@pytest.mark.asyncio
async def test_discovery_engine_limits_llm_evaluation_concurrency() -> None:
    llm_service = _SlowLLMService(delay=0.02)
    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
    )

    items = [
        DiscoveredContent(
            bvid=f"BV{i}",
            title=f"title-{i}",
            up_name=f"up-{i}",
            description="desc",
            source_strategy="test",
        )
        for i in range(4)
    ]

    await asyncio.gather(*(engine.evaluate_content(item, _build_profile()) for item in items))

    assert llm_service.max_active_calls == 2


@pytest.mark.asyncio
async def test_evaluate_batch_accepts_fenced_json_without_single_eval_fallback() -> None:
    """Batch responses wrapped in ```json fences should not explode into N calls."""

    class _FencedBatchLLMService:
        def __init__(self) -> None:
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
            return _SlowResponse(
                """```json
[
  {"score": 0.82, "reason": "ok", "style_key": "deep_dive"},
  {"score": 0.76, "reason": "ok", "style_key": "story_doc"}
]
```"""
            )

    llm_service = _FencedBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    batch = [
        DiscoveredContent(bvid="BVF1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVF2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.82, 0.76]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_skips_single_fallback_during_provider_cooldown() -> None:
    class _CooldownLLMService:
        def __init__(self) -> None:
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
            raise LLMProviderExecutionError(
                "All providers failed (gemini). Last error: "
                "Provider gemini is cooling down after rate limit."
            )

    llm_service = _CooldownLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    batch = [
        DiscoveredContent(bvid="BV_COOL_A", title="A", source_strategy="trending"),
        DiscoveredContent(bvid="BV_COOL_B", title="B", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.0, 0.0]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_ignores_echoed_prompt_before_result_array() -> None:
    """Some JSON-mode providers echo input JSON before the actual scored array."""

    class _EchoThenResultLLMService:
        def __init__(self) -> None:
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
            return _SlowResponse(
                """{
  "source_context": "trending",
  "content_batch": [
    {"title": "echoed input without score"}
  ]
}
```json
[
  {"score": 0.81, "reason": "ok", "style_key": "deep_dive"},
  {"score": 0.74, "reason": "ok", "style_key": "story_doc"}
]
```"""
            )

    llm_service = _EchoThenResultLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    batch = [
        DiscoveredContent(bvid="BVE1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVE2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.81, 0.74]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_matches_results_by_bvid_when_response_reorders() -> None:
    class _ReorderedBatchLLMService:
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
            return _SlowResponse(
                json.dumps(
                    [
                        {
                            "bvid": "BV_EVAL_C",
                            "score": 0.33,
                            "reason": "C 自己的理由",
                            "topic_group": "C 类",
                            "style_key": "story_doc",
                        },
                        {
                            "bvid": "BV_EVAL_A",
                            "score": 0.71,
                            "reason": "A 自己的理由",
                            "topic_group": "A 类",
                            "style_key": "deep_dive",
                        },
                        {
                            "bvid": "BV_EVAL_B",
                            "score": 0.52,
                            "reason": "B 自己的理由",
                            "topic_group": "B 类",
                            "style_key": "light_chat",
                        },
                    ],
                    ensure_ascii=False,
                )
            )

    engine = ContentDiscoveryEngine(llm_service=_ReorderedBatchLLMService())
    batch = [
        DiscoveredContent(bvid="BV_EVAL_A", title="A 视频", source_strategy="trending"),
        DiscoveredContent(bvid="BV_EVAL_B", title="B 视频", source_strategy="trending"),
        DiscoveredContent(bvid="BV_EVAL_C", title="C 视频", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.71, 0.52, 0.33]
    assert batch[0].relevance_reason == "A 自己的理由"
    assert batch[0].topic_group == "A 类"
    assert batch[1].relevance_reason == "B 自己的理由"
    assert batch[1].topic_group == "B 类"
    assert batch[2].relevance_reason == "C 自己的理由"
    assert batch[2].topic_group == "C 类"


@pytest.mark.asyncio
async def test_evaluate_batch_accepts_newline_delimited_json_objects() -> None:
    """Some providers return one scored JSON object per line instead of an array."""

    class _NdjsonBatchLLMService:
        def __init__(self) -> None:
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
            return _SlowResponse(
                "\n".join(
                    [
                        '{"score": 0.71, "reason": "ok", "style_key": "practical_guide"}',
                        '{"score": 0.68, "reason": "ok", "style_key": "story_doc"}',
                    ]
                )
            )

    llm_service = _NdjsonBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    batch = [
        DiscoveredContent(bvid="BVN1", title="t1", up_name="u1", source_strategy="trending"),
        DiscoveredContent(bvid="BVN2", title="t2", up_name="u2", source_strategy="trending"),
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.71, 0.68]
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_evaluate_batch_intra_batch_franchise_cap() -> None:
    """v0.3.50: same-franchise items beyond the cap get their scores zeroed.

    Reproduces the production trigger: a single eval batch returning 6
    张雪机车 entries (or 7 风犬少年的天空, etc.) used to all stay
    kept=30, flooding the pool with one franchise. Cap is 4 — the top
    4 by score survive, the rest are zeroed (so the caller's
    ``score > 0`` filter drops them from the kept list).
    """
    import json

    class _FrancheseClumpLLMService:
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
            # 6 items, all "张雪机车" franchise, with descending scores.
            results = [
                {
                    "score": 0.95 - i * 0.05,
                    "reason": "好看",
                    "topic_group": "机车",
                    "style_key": "review_roundup",
                    "franchise_key": "张雪机车",
                }
                for i in range(6)
            ]
            return _SlowResponse(json.dumps(results, ensure_ascii=False))

    engine = ContentDiscoveryEngine(llm_service=_FrancheseClumpLLMService())
    batch = [
        DiscoveredContent(
            bvid=f"BVZX{i}",
            title=f"张雪机车第{i}集",
            up_name="张雪机车",
            description="d",
            source_strategy="related_chain",
        )
        for i in range(6)
    ]

    scores = await engine._evaluate_batch(batch, _build_profile())

    # Cap is 4 — top-4 scoring entries kept (>0), the rest zeroed.
    nonzero = [s for s in scores if s > 0]
    assert len(nonzero) == 4
    assert sum(1 for s in scores if s == 0.0) == 2
    # Zeroed entries must also have their content's relevance_score reset
    # so downstream code that reads the content directly gets the same answer.
    zero_indices = [i for i, s in enumerate(scores) if s == 0.0]
    for idx in zero_indices:
        assert batch[idx].relevance_score == 0.0


def test_count_pool_by_franchise_returns_lowercased_groups(tmp_path: Path) -> None:
    """v0.3.50: pool-quota query groups + lowercases franchise_key."""
    from openbiliclaw.storage.database import Database

    db = Database(tmp_path / "fk.db")
    db.initialize()
    # Two items sharing a franchise (case-different), one with empty,
    # one with a different franchise.
    db.cache_content(
        bvid="BV1A",
        title="A",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="张雪机车",
    )
    db.cache_content(
        bvid="BV1B",
        title="B",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="张雪机车",  # exact match
    )
    db.cache_content(
        bvid="BV1C",
        title="C",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="风犬少年的天空",
    )
    db.cache_content(
        bvid="BV1D",
        title="D",
        up_name="up",
        source_platform="bilibili",
        source="search",
        franchise_key="",
    )

    counts = db.count_pool_by_franchise()
    assert counts.get("张雪机车") == 2
    assert counts.get("风犬少年的天空") == 1
    assert "" not in counts


# ----------------------------------------------------------------------
# v0.3.x eval-batch negative-anchors wiring.


class _StubNegativeExemplarsDatabase:
    """Minimal database stub for the negative-exemplars wiring tests."""

    def __init__(
        self,
        rows: list[dict[str, object]],
        *,
        latest_event_id: int = 1,
    ) -> None:
        self._rows = rows
        self._latest_event_id = latest_event_id
        self.query_calls = 0

    def get_latest_event_id(self) -> int:
        return self._latest_event_id

    def bump_latest_event_id(self) -> None:
        self._latest_event_id += 1

    def query_events(self, **kwargs: object) -> list[dict[str, object]]:
        self.query_calls += 1
        return list(self._rows)


def _negative_row(idx: int, title: str) -> dict[str, object]:
    from datetime import datetime

    return {
        "id": idx,
        "title": title,
        "inferred_satisfaction": "negative",
        "satisfaction_reason": "quick_exit",
        "created_at": datetime(2026, 5, 16, 12, 0, 0).isoformat(sep=" "),
    }


class _RecordingBatchLLMService:
    """Captures the user_input sent to the batch evaluator for assertions."""

    def __init__(
        self,
        response: str = '[{"score": 0.7, "reason": "ok", "style_key": "deep_dive"}]',
    ) -> None:
        self.response = response
        self.user_inputs: list[str] = []

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
        self.user_inputs.append(user_input)
        return _SlowResponse(self.response)


@pytest.mark.asyncio
async def test_evaluate_batch_includes_negative_exemplars_in_user_prompt() -> None:
    """When the event store has negative rows, the eval batch user
    message must include the <negative_examples> block."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())

    assert llm.user_inputs, "LLM should have been called once"
    user = llm.user_inputs[0]
    assert "<negative_examples>" in user
    assert "震惊！我刚发现的神器" in user


@pytest.mark.asyncio
async def test_evaluate_batch_omits_block_with_no_negative_rows() -> None:
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())

    user = llm.user_inputs[0]
    assert "<negative_examples>" not in user


@pytest.mark.asyncio
async def test_evaluate_batch_runs_when_exemplar_helper_raises() -> None:
    """Storage failure inside _get_negative_exemplars must not abort the batch."""

    class _BrokenDatabase:
        def get_latest_event_id(self) -> int:
            raise RuntimeError("database is locked")

        def query_events(self, **kwargs: object) -> list[dict[str, object]]:
            raise RuntimeError("database is locked")

    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=_BrokenDatabase())
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    scores = await engine._evaluate_batch(batch, _build_profile())

    assert scores == [0.7], "batch should still produce a score"
    assert "<negative_examples>" not in llm.user_inputs[0]


@pytest.mark.asyncio
async def test_evaluate_batch_caches_exemplars_across_back_to_back_calls() -> None:
    """Two batches with the same latest_event_id should share one query."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())
    first_query_count = db.query_calls

    await engine._evaluate_batch(batch, _build_profile())
    assert db.query_calls == first_query_count, "cache hit, no second query"


@pytest.mark.asyncio
async def test_evaluate_batch_refreshes_exemplars_on_new_event_id() -> None:
    """A new negative classified row should bust the cache on the next batch."""
    db = _StubNegativeExemplarsDatabase(rows=[_negative_row(1, "震惊！我刚发现的神器")])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    batch = [DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")]

    await engine._evaluate_batch(batch, _build_profile())
    db.bump_latest_event_id()
    await engine._evaluate_batch(batch, _build_profile())

    assert db.query_calls >= 2, "new event id must invalidate the cache"


@pytest.mark.asyncio
async def test_eval_cache_rechecks_content_when_negative_exemplars_change() -> None:
    """Cached relevance scores must not bypass newly available negative anchors."""
    db = _StubNegativeExemplarsDatabase(rows=[])
    llm = _RecordingBatchLLMService()
    engine = ContentDiscoveryEngine(llm_service=llm, database=db)
    profile = _build_profile()
    content = DiscoveredContent(bvid="BVx", title="候选", up_name="u", source_strategy="search")

    await engine.evaluate_content_batch([content], profile)
    assert len(llm.user_inputs) == 1
    assert "<negative_examples>" not in llm.user_inputs[0]

    db._rows = [_negative_row(1, "震惊！我刚发现的神器")]  # noqa: SLF001
    db.bump_latest_event_id()
    await engine.evaluate_content_batch([content], profile)

    assert len(llm.user_inputs) == 2, "negative-anchor revision must invalidate eval cache"
    assert "<negative_examples>" in llm.user_inputs[1]
