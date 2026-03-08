"""Tests for cross-domain explore discovery strategy."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

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
    assert results[0].relevance_score > 0.80
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
