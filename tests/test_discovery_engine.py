"""Tests for discovery engine orchestration."""

from __future__ import annotations

import pytest

from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.soul.profile import SoulProfile

from .test_explore_strategy import (
    FakeBilibiliClient as FakeExploreBilibiliClient,
)
from .test_explore_strategy import FakeLLMService as FakeExploreLLMService
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
    )
    engine.register_strategy(strategy)

    results = await engine.discover(_build_profile())

    assert len(results) == 1
    assert results[0].bvid == "BV1A"
    assert results[0].source_strategy == "search"


@pytest.mark.asyncio
async def test_discovery_engine_handles_empty_strategy_results() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    engine = ContentDiscoveryEngine()
    engine.register_strategy(
        SearchStrategy(
            llm_service=FakeLLMService('{"queries": []}'),
            bilibili_client=FakeBilibiliClient({}),
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
