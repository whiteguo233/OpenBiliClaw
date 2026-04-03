"""Tests for cross-domain explore discovery strategy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from openbiliclaw.discovery.engine import DiscoveryConcurrencyController
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个愿意投入时间理解复杂事物，但偶尔也希望被带去陌生领域的人。",
        core_traits=["理性", "好奇", "克制"],
        deep_needs=["扩展认知边界", "理解复杂系统"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="纪录片", category="知识", weight=0.94),
                InterestTag(name="历史", category="知识", weight=0.88),
            ],
            exploration_openness=0.8,
        ),
    )


@dataclass
class _FakeResponse:
    content: str


@dataclass
class FakeLLMService:
    contents: list[str]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> object:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
            }
        )
        content = self.contents.pop(0) if self.contents else '{"score": 0.0, "reason": ""}'
        return _FakeResponse(content)


@dataclass
class FakeBilibiliClient:
    results_by_query: dict[str, list[dict[str, object]]]
    failing_queries: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]:
        self.calls.append(keyword)
        if keyword in self.failing_queries:
            raise RuntimeError(f"boom: {keyword}")
        return self.results_by_query.get(keyword, [])


class _SlowScoringLLMService(FakeLLMService):
    def __init__(self, contents: list[str], delay: float = 0.02) -> None:
        super().__init__(contents)
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
        response = await super().complete_structured_task(
            system_instruction=system_instruction,
            user_input=user_input,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.active_calls -= 1
        return response


@pytest.mark.asyncio
async def test_explore_strategy_generates_and_filters_domains() -> None:
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    llm_service = FakeLLMService(
        [
            """
            {
              "domains": [
                {
                  "domain": "纪录片",
                  "why_it_might_resonate": "和现有兴趣完全相同。",
                  "novelty_level": 0.55,
                  "queries": ["纪录片 深度讲解"]
                },
                {
                  "domain": "城市空间与建筑叙事",
                  "why_it_might_resonate": "你偏好结构清晰、能从具体对象看见更大系统的内容。",
                  "novelty_level": 0.68,
                  "queries": ["城市 建筑 纪录片", "空间 设计 深度讲解"]
                }
              ]
            }
            """
        ]
    )
    bilibili_client = FakeBilibiliClient(
        {
            "城市 建筑 纪录片": [
                {"bvid": "BV1A", "title": "城市与建筑", "author": "UP1", "mid": 1}
            ],
            "空间 设计 深度讲解": [
                {"bvid": "BV1B", "title": "空间设计", "author": "UP2", "mid": 2}
            ],
        }
    )

    strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        score_threshold=0.0,
    )
    results = await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == ["城市 建筑 纪录片", "空间 设计 深度讲解"]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]


@pytest.mark.asyncio
async def test_explore_strategy_prioritizes_interest_anchored_domains() -> None:
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    llm_service = FakeLLMService(
        [
            """
            {
              "domains": [
                {
                  "domain": "纪录片幕后工艺",
                  "why_it_might_resonate": "你会想把纪录片质感背后的工艺看明白。",
                  "novelty_level": 0.62,
                  "queries": ["纪录片 幕后 工艺"]
                },
                {
                  "domain": "历史事件深度复盘",
                  "why_it_might_resonate": "你会把历史事件背后的因果链一路看透。",
                  "novelty_level": 0.64,
                  "queries": ["历史 事件 复盘"]
                },
                {
                  "domain": "排水系统工程科普",
                  "why_it_might_resonate": "你喜欢把系统原理讲清楚的内容。",
                  "novelty_level": 0.66,
                  "queries": ["排水 系统 科普"]
                },
                {
                  "domain": "电影拟音幕后",
                  "why_it_might_resonate": "你会对幕后工艺感兴趣。",
                  "novelty_level": 0.65,
                  "queries": ["电影 拟音 幕后"]
                }
              ]
            }
            """
        ]
    )
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片 幕后 工艺": [
                {"bvid": "BV1A", "title": "纪录片幕后", "author": "UP1", "mid": 1}
            ],
            "历史 事件 复盘": [
                {"bvid": "BV1B", "title": "历史复盘", "author": "UP2", "mid": 2}
            ],
            "排水 系统 科普": [
                {"bvid": "BV1C", "title": "排水系统", "author": "UP3", "mid": 3}
            ],
            "电影 拟音 幕后": [
                {"bvid": "BV1D", "title": "电影拟音", "author": "UP4", "mid": 4}
            ],
        }
    )

    strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        score_threshold=0.0,
        max_domains=3,
    )
    results = await strategy.discover(_build_profile(), limit=20)

    # Loose (novel) domains prioritized first to fight echo chamber,
    # then anchored domains fill remaining slots
    assert bilibili_client.calls == ["排水 系统 科普", "纪录片 幕后 工艺", "历史 事件 复盘"]
    assert {item.bvid for item in results} == {"BV1A", "BV1B", "BV1C"}
    assert "BV1D" not in {item.bvid for item in results}


@pytest.mark.asyncio
async def test_explore_strategy_applies_exploration_bonus() -> None:
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    llm_service = FakeLLMService(
        [
            """
            {
              "domains": [
                {
                  "domain": "城市空间与建筑叙事",
                  "why_it_might_resonate": "你偏好系统性理解。",
                  "novelty_level": 0.8,
                  "queries": ["城市 建筑 纪录片"]
                }
              ]
            }
            """,
            '{"score": 0.86, "reason": "主题与你的理解欲相符。"}',
        ]
    )
    bilibili_client = FakeBilibiliClient(
        {
            "城市 建筑 纪录片": [
                {"bvid": "BV1A", "title": "城市与建筑", "author": "UP1", "mid": 1}
            ]
        }
    )

    strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        score_threshold=0.65,
    )
    results = await strategy.discover(_build_profile(), limit=20)

    assert len(results) == 1
    # New blending: score * 0.60 + bonus * 0.40, gentler than old 0.75/0.25
    assert results[0].relevance_score > 0.70
    assert results[0].source_strategy == "explore"


@pytest.mark.asyncio
async def test_explore_strategy_tolerates_partial_failures() -> None:
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    llm_service = FakeLLMService(
        [
            """
            {
              "domains": [
                {
                  "domain": "声音景观与录音文化",
                  "why_it_might_resonate": "你可能会喜欢通过媒介理解世界。",
                  "novelty_level": 0.72,
                  "queries": ["声音 文化 纪录片", "  "]
                },
                {
                  "domain": "城市空间与建筑叙事",
                  "why_it_might_resonate": "你偏好结构清晰的系统视角。",
                  "novelty_level": 0.67,
                  "queries": ["城市 建筑 纪录片"]
                }
              ]
            }
            """,
            '{"score": 0.82, "reason": "解释世界的方式和你相符。"}',
        ]
    )
    bilibili_client = FakeBilibiliClient(
        {
            "城市 建筑 纪录片": [
                {"bvid": "BV1A", "title": "城市与建筑", "author": "UP1", "mid": 1}
            ]
        },
        failing_queries={"声音 文化 纪录片"},
    )

    strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        score_threshold=0.0,
    )
    results = await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == ["声音 文化 纪录片", "城市 建筑 纪录片"]
    assert [item.bvid for item in results] == ["BV1A"]


@pytest.mark.asyncio
async def test_explore_strategy_uses_bounded_evaluation_concurrency() -> None:
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    llm_service = _SlowScoringLLMService(
        [
            """
            {
              "domains": [
                {
                  "domain": "城市空间与建筑叙事",
                  "why_it_might_resonate": "你偏好结构化理解。",
                  "novelty_level": 0.68,
                  "queries": ["城市 建筑 纪录片", "空间 设计 深度讲解"]
                }
              ]
            }
            """,
            '{"score": 0.82, "reason": "A"}',
            '{"score": 0.81, "reason": "B"}',
            '{"score": 0.80, "reason": "C"}',
        ]
    )
    bilibili_client = FakeBilibiliClient(
        {
            "城市 建筑 纪录片": [
                {"bvid": "BV1A", "title": "A", "author": "UP1", "mid": 1},
                {"bvid": "BV1B", "title": "B", "author": "UP2", "mid": 2},
            ],
            "空间 设计 深度讲解": [
                {"bvid": "BV1C", "title": "C", "author": "UP3", "mid": 3}
            ],
        }
    )
    strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
        score_threshold=0.65,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert llm_service.max_active_calls == 2
    assert [item.bvid for item in results] == ["BV1A", "BV1B", "BV1C"]
