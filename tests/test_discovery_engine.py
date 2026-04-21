"""Tests for discovery engine orchestration."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
)
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
    ) -> object:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(self.delay)
        self.active_calls -= 1
        return _SlowResponse('{"score": 0.88, "reason": "still relevant"}')


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
            {
                "纪录片 原理": [
                    {"bvid": "BV1A", "title": "纪录片", "author": "UP1", "mid": 1}
                ]
            }
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
        llm_service=FakeRelatedLLMService(
            ['{"score": 0.84, "reason": "延续了近期观看兴趣。"}']
        )
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

    @property
    def name(self) -> str:
        return self._name

    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
        self._started.append(self._name)
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
        async def discover(
            self, profile: SoulProfile, limit: int = 20
        ) -> list[DiscoveredContent]:
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
        async def discover(
            self, profile: SoulProfile, limit: int = 20
        ) -> list[DiscoveredContent]:
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
        async def discover(
            self, profile: SoulProfile, limit: int = 20
        ) -> list[DiscoveredContent]:
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
        async def discover(
            self, profile: SoulProfile, limit: int = 20
        ) -> list[DiscoveredContent]:
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
