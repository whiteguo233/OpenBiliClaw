"""Tests for search-based discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from openbiliclaw.discovery.engine import DiscoveryConcurrencyController
from openbiliclaw.soul.profile import (
    InterestTag,
    PreferenceLayer,
    SoulProfile,
    StylePreference,
)


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个偏好深度内容、耐心较强、会主动寻找高信息密度表达的人。",
        core_traits=["理性", "好奇", "克制"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="纪录片", category="知识", weight=0.9),
                InterestTag(name="摄影", category="创作", weight=0.8),
            ],
            favorite_up_users=["影视飓风"],
        ),
    )


@dataclass
class FakeLLMService:
    content: str
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
                "history": history,
            }
        )
        return _FakeResponse(self.content)


@dataclass
class _FakeResponse:
    content: str


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


@dataclass
class _SlowSearchClient:
    results_by_query: dict[str, list[dict[str, object]]]
    delay: float = 0.02
    active_calls: int = 0
    max_active_calls: int = 0
    calls: list[str] = field(default_factory=list)

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]:
        self.calls.append(f"{keyword}:{page}")
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(self.delay)
        self.active_calls -= 1
        return self.results_by_query.get(keyword, [])


@pytest.mark.asyncio
async def test_search_strategy_uses_llm_queries_and_searches_each_query() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片 原理", "摄影 构图"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片 原理": [
                {
                    "bvid": "BV1A",
                    "title": "把纪录片讲透",
                    "author": "知识区UP",
                    "mid": 11,
                    "pic": "cover-a.jpg",
                    "duration": "12:30",
                    "play": 1234,
                    "description": "高信息密度讲解",
                }
            ],
            "摄影 构图": [
                {
                    "bvid": "BV1B",
                    "title": "摄影构图入门",
                    "author": "影像UP",
                    "mid": 22,
                    "pic": "cover-b.jpg",
                    "duration": "08:05",
                    "play": 5678,
                    "description": "构图与镜头语言",
                }
            ],
        }
    )

    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )
    await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == ["纪录片 原理", "摄影 构图"]


@pytest.mark.asyncio
async def test_search_strategy_passes_style_preferences_to_query_prompt() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["摄影 vlog"]}')
    profile = _build_profile()
    profile.preferences.style = StylePreference(
        preferred_duration="short",
        preferred_pace="fast",
        humor_preference=0.85,
        depth_preference=0.25,
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=FakeBilibiliClient({}),
        llm_evaluation=False,
    )

    queries = await strategy._generate_queries(profile)

    assert queries == ["摄影 vlog"]
    user_input = str(llm_service.calls[0]["user_input"])
    assert '"preferred_duration": "short"' in user_input
    assert '"humor_preference": 0.85' in user_input
    assert '"depth_preference": 0.25' in user_input
    assert llm_service.calls


@pytest.mark.asyncio
async def test_search_strategy_deduplicates_results_by_bvid() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片", "深度讲解"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片": [
                {"bvid": "BV1A", "title": "纪录片 1", "author": "UP1", "mid": 1},
                {"bvid": "BV1B", "title": "纪录片 2", "author": "UP2", "mid": 2},
            ],
            "深度讲解": [
                {"bvid": "BV1A", "title": "纪录片 1", "author": "UP1", "mid": 1},
                {"bvid": "BV1C", "title": "纪录片 3", "author": "UP3", "mid": 3},
            ],
        }
    )

    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile())

    assert [item.bvid for item in results] == ["BV1A", "BV1B", "BV1C"]


@pytest.mark.asyncio
async def test_search_strategy_boosts_high_weight_interest_matches() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片 原理", "陌生 主题"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片 原理": [
                {
                    "bvid": "BV1A",
                    "title": "纪录片原理讲透",
                    "author": "UP1",
                    "mid": 1,
                    "description": "把纪录片结构一次讲清楚",
                }
            ],
            "陌生 主题": [
                {
                    "bvid": "BV1B",
                    "title": "陌生主题速看",
                    "author": "UP2",
                    "mid": 2,
                    "description": "泛兴趣快餐内容",
                }
            ],
        }
    )

    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile(), limit=20)

    assert [item.bvid for item in results] == ["BV1A", "BV1B"]
    assert results[0].relevance_score >= 0.5
    assert results[0].relevance_score > results[1].relevance_score


@pytest.mark.asyncio
async def test_search_strategy_falls_back_when_llm_returns_invalid_json() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService("not-json")
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片": [{"bvid": "BV1A", "title": "纪录片", "author": "UP1", "mid": 1}],
            "摄影": [{"bvid": "BV1B", "title": "摄影", "author": "UP2", "mid": 2}],
        }
    )

    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile())

    assert bilibili_client.calls[:2] == ["纪录片", "摄影"]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]


@pytest.mark.asyncio
async def test_search_strategy_continues_when_single_query_fails() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片", "摄影"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "摄影": [{"bvid": "BV1B", "title": "摄影", "author": "UP2", "mid": 2}],
        },
        failing_queries={"纪录片"},
    )

    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )
    results = await strategy.discover(_build_profile())

    assert bilibili_client.calls == ["纪录片", "摄影"]
    assert [item.bvid for item in results] == ["BV1B"]


@pytest.mark.asyncio
async def test_search_strategy_uses_bounded_request_concurrency_and_keeps_limit() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片", "摄影", "构图"]}')
    bilibili_client = _SlowSearchClient(
        {
            "纪录片": [{"bvid": "BV1A", "title": "纪录片", "author": "UP1", "mid": 1}],
            "摄影": [{"bvid": "BV1B", "title": "摄影", "author": "UP2", "mid": 2}],
            "构图": [{"bvid": "BV1C", "title": "构图", "author": "UP3", "mid": 3}],
        }
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
        llm_evaluation=False,
    )

    results = await strategy.discover(_build_profile(), limit=2)

    # Search runs sequentially to avoid B站 rate-limiting, so max_active == 1
    assert bilibili_client.max_active_calls == 1
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]
