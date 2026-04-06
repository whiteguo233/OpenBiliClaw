"""Tests for trending discovery strategy."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
)
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个偏好高信息密度内容、判断克制、愿意投入时间理解复杂议题的人。",
        core_traits=["理性", "好奇", "耐心"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="纪录片", category="知识", weight=0.92),
                InterestTag(name="历史", category="知识", weight=0.87),
            ]
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
        if "content_batch" in user_input:
            import json as _json

            try:
                batch_data = _json.loads(
                    user_input.split("<content_batch>")[1].split("</content_batch>")[0]
                )
                batch_size = len(batch_data) if isinstance(batch_data, list) else 1
            except Exception:
                batch_size = 1
            items: list[object] = []
            for _ in range(batch_size):
                if not self.contents:
                    items.append({"score": 0.0, "reason": ""})
                    continue
                raw = self.contents.pop(0)
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict) and "score" in parsed:
                        items.append(parsed)
                    else:
                        self.contents.insert(0, raw)
                        items.append({"score": 0.0, "reason": ""})
                except _json.JSONDecodeError:
                    items.append({"score": 0.0, "reason": ""})
            return _FakeResponse(_json.dumps(items))
        content = self.contents.pop(0) if self.contents else '{"score": 0.0, "reason": ""}'
        return _FakeResponse(content)


@dataclass
class FakeRankingClient:
    results_by_rid: dict[int, list[dict[str, object]]]
    failing_rids: set[int] = field(default_factory=set)
    calls: list[int] = field(default_factory=list)

    async def get_ranking(self, rid: int = 0) -> list[dict[str, object]]:
        self.calls.append(rid)
        if rid in self.failing_rids:
            raise RuntimeError(f"boom: {rid}")
        return self.results_by_rid.get(rid, [])


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
async def test_trending_strategy_fetches_global_and_related_rankings() -> None:
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    llm_service = FakeLLMService(
        [
            '{"rids": [36, 181]}',
            '{"score": 0.82, "reason": "讲解深度和你的偏好接近。"}',
            '{"score": 0.74, "reason": "内容主题和你常看的历史纪录片相近。"}',
        ]
    )
    bilibili_client = FakeRankingClient(
        {
            0: [{"bvid": "BV1A", "title": "全站榜内容", "author": "UP1", "mid": 1}],
            36: [{"bvid": "BV1B", "title": "知识区内容", "author": "UP2", "mid": 2}],
            181: [],
        }
    )

    strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        score_threshold=0.65,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == [0, 36, 181]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]
    assert all(item.source_strategy == "trending" for item in results)


@pytest.mark.asyncio
async def test_trending_strategy_filters_by_score_threshold() -> None:
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    llm_service = FakeLLMService(
        [
            '{"rids": [36]}',
            '{"score": 0.40, "reason": "相关度较弱。"}',
            '{"score": 0.79, "reason": "主题和表达方式都更贴近你的长期偏好。"}',
        ]
    )
    bilibili_client = FakeRankingClient(
        {
            0: [{"bvid": "BV1A", "title": "一般内容", "author": "UP1", "mid": 1}],
            36: [{"bvid": "BV1B", "title": "高匹配内容", "author": "UP2", "mid": 2}],
        }
    )

    strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        score_threshold=0.65,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert [item.bvid for item in results] == ["BV1B"]
    assert results[0].relevance_score == 0.79


@pytest.mark.asyncio
async def test_trending_strategy_continues_when_one_ranking_fails() -> None:
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    llm_service = FakeLLMService(
        [
            '{"rids": [36, 181]}',
            '{"score": 0.81, "reason": "依然匹配。"}',
        ]
    )
    bilibili_client = FakeRankingClient(
        {
            181: [{"bvid": "BV1C", "title": "影视区内容", "author": "UP3", "mid": 3}],
        },
        failing_rids={0, 36},
    )

    strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        score_threshold=0.65,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == [0, 36, 181]
    assert [item.bvid for item in results] == ["BV1C"]


@pytest.mark.asyncio
async def test_evaluate_content_sets_score_and_reason() -> None:
    llm_service = FakeLLMService(
        ['{"score": 0.76, "reason": "这个视频的主题密度和表达方式都更贴近你的偏好。"}']
    )
    engine = ContentDiscoveryEngine(llm_service=llm_service)
    content = DiscoveredContent(
        bvid="BV1A",
        title="纪录片讲透世界史",
        up_name="知识UP",
        description="高信息密度讲解",
        source_strategy="trending",
    )

    score = await engine.evaluate_content(content, _build_profile())

    assert score == 0.76
    assert content.relevance_score == 0.76
    assert "更贴近你的偏好" in content.relevance_reason


@pytest.mark.asyncio
async def test_trending_strategy_uses_bounded_evaluation_concurrency() -> None:
    from openbiliclaw.discovery.strategies.strategies import TrendingStrategy

    llm_service = _SlowScoringLLMService(
        [
            '{"rids": [36]}',
            '{"score": 0.82, "reason": "A"}',
            '{"score": 0.81, "reason": "B"}',
            '{"score": 0.80, "reason": "C"}',
        ]
    )
    bilibili_client = FakeRankingClient(
        {
            0: [
                {"bvid": "BV1A", "title": "A", "author": "UP1", "mid": 1},
                {"bvid": "BV1B", "title": "B", "author": "UP2", "mid": 2},
            ],
            36: [{"bvid": "BV1C", "title": "C", "author": "UP3", "mid": 3}],
        }
    )
    strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        score_threshold=0.65,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert llm_service.max_active_calls >= 1  # Batch eval sends fewer calls
    assert [item.bvid for item in results] == ["BV1A", "BV1B", "BV1C"]
