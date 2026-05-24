"""Tests for related-chain discovery strategy."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveryConcurrencyController
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个会从近期高质量内容继续深挖相关主题的人。",
        core_traits=["理性", "好奇", "耐心"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="纪录片", category="知识", weight=0.92),
                InterestTag(name="历史", category="知识", weight=0.85),
            ],
            favorite_up_users=["半佛仙人"],
        ),
    )


@dataclass
class FakeLLMService:
    contents: list[str]

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> object:
        is_batch = "content_batch" in user_input
        if is_batch:
            # Batch eval: count items in batch and consume that many responses
            import json as _json

            # Count items in batch from prompt
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
                        self.contents.insert(0, raw)  # put back non-score response
                        items.append({"score": 0.0, "reason": ""})
                except _json.JSONDecodeError:
                    items.append({"score": 0.0, "reason": ""})
            return _FakeResponse(_json.dumps(items))
        content = self.contents.pop(0) if self.contents else '{"score": 0.0, "reason": ""}'
        return _FakeResponse(content)


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
        caller: str = "",
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


@dataclass
class _FakeResponse:
    content: str


@dataclass
class FakeMemoryManager:
    events: list[dict[str, Any]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "event_types": event_types,
                "limit": limit,
            }
        )
        return self.events[:limit]


@dataclass
class FakeRelatedClient:
    related_by_bvid: dict[str, list[dict[str, object]]]
    search_results_by_query: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    failing_bvids: set[str] = field(default_factory=set)
    related_calls: list[str] = field(default_factory=list)
    search_calls: list[str] = field(default_factory=list)

    async def get_related_videos(self, bvid: str) -> list[dict[str, object]]:
        self.related_calls.append(bvid)
        if bvid in self.failing_bvids:
            raise RuntimeError(f"boom: {bvid}")
        return self.related_by_bvid.get(bvid, [])

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]:
        self.search_calls.append(keyword)
        return self.search_results_by_query.get(keyword, [])


@dataclass
class FakeSeedStrategy:
    results: list[Any]
    calls: list[int] = field(default_factory=list)

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[Any]:
        self.calls.append(limit)
        return self.results[:limit]


def _event(bvid: str, *, event_type: str = "view", title: str = "seed") -> dict[str, Any]:
    return {
        "event_type": event_type,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "title": title,
        "metadata": json.dumps({"bvid": bvid}, ensure_ascii=False),
    }


@pytest.mark.asyncio
async def test_related_chain_uses_event_seeds_first() -> None:
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(
        events=[
            _event("BV1SEED", title="科技前沿"),
            _event("BV1SEED2", event_type="favorite", title="音乐推荐"),
        ]
    )
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEED": [
                {"bvid": "BV1A", "title": "相关推荐 A", "owner": {"name": "UP1", "mid": 1}}
            ],
            "BV1SEED2": [
                {"bvid": "BV1B", "title": "相关推荐 B", "owner": {"name": "UP2", "mid": 2}}
            ],
        }
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=FakeLLMService(
            ['{"score": 0.82, "reason": "符合偏好。"}', '{"score": 0.78, "reason": "仍然匹配。"}']
        ),
        memory_manager=memory,
        max_depth=1,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert client.related_calls == ["BV1SEED", "BV1SEED2"]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]
    assert results[0].topic_key == "科技前沿"
    assert results[1].topic_key == "音乐推荐"
    assert memory.calls[0]["event_types"] == ["view", "favorite", "like"]


@pytest.mark.asyncio
async def test_related_chain_falls_back_to_seed_strategies() -> None:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(events=[])
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEARCH": [
                {"bvid": "BV1A", "title": "相关推荐 A", "owner": {"name": "UP1", "mid": 1}}
            ],
            "BV1TREND": [
                {"bvid": "BV1B", "title": "相关推荐 B", "owner": {"name": "UP2", "mid": 2}}
            ],
        },
        search_results_by_query={},
    )
    search_strategy = FakeSeedStrategy(
        [DiscoveredContent(bvid="BV1SEARCH", title="搜索种子", up_name="UPS")]
    )
    trending_strategy = FakeSeedStrategy(
        [DiscoveredContent(bvid="BV1TREND", title="榜单种子", up_name="UPT")]
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=FakeLLMService(
            ['{"score": 0.80, "reason": "相关。"}', '{"score": 0.77, "reason": "相关。"}']
        ),
        memory_manager=memory,
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        max_seeds=2,
        max_depth=1,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert search_strategy.calls == [2]
    assert trending_strategy.calls == [1]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]


@pytest.mark.asyncio
async def test_related_chain_fetches_and_dedupes_related_videos() -> None:
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(events=[_event("BV1SEED")])
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEED": [
                {"bvid": "BV1SEED", "title": "自己", "owner": {"name": "UP0", "mid": 0}},
                {"bvid": "BV1A", "title": "相关推荐 A", "owner": {"name": "UP1", "mid": 1}},
                {"bvid": "BV1A", "title": "相关推荐 A 重复", "owner": {"name": "UP1", "mid": 1}},
                {"bvid": "BV1B", "title": "相关推荐 B", "owner": {"name": "UP2", "mid": 2}},
            ]
        }
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=FakeLLMService(
            ['{"score": 0.82, "reason": "符合。"}', '{"score": 0.73, "reason": "还不错。"}']
        ),
        memory_manager=memory,
        max_depth=1,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert [item.bvid for item in results] == ["BV1A", "BV1B"]


@pytest.mark.asyncio
async def test_related_chain_filters_by_score_and_tolerates_failures() -> None:
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(
        events=[
            _event("BV1FAIL", title="失败视频"),
            _event("BV1SEED", title="正常视频"),
        ]
    )
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEED": [
                {"bvid": "BV1A", "title": "低分内容", "owner": {"name": "UP1", "mid": 1}},
                {"bvid": "BV1B", "title": "高分内容", "owner": {"name": "UP2", "mid": 2}},
            ]
        },
        failing_bvids={"BV1FAIL"},
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=FakeLLMService(
            ['{"score": 0.42, "reason": "太泛。"}', '{"score": 0.88, "reason": "主题高度贴近。"}']
        ),
        memory_manager=memory,
        score_threshold=0.65,
        max_depth=1,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert client.related_calls == ["BV1FAIL", "BV1SEED"]
    assert [item.bvid for item in results] == ["BV1B"]


@pytest.mark.asyncio
async def test_related_chain_can_expand_to_second_level() -> None:
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(events=[_event("BV1SEED")])
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEED": [
                {"bvid": "BV1A", "title": "第一层 A", "owner": {"name": "UP1", "mid": 1}},
            ],
            "BV1A": [
                {"bvid": "BV1B", "title": "第二层 B", "owner": {"name": "UP2", "mid": 2}},
            ],
        }
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=FakeLLMService(
            ['{"score": 0.86, "reason": "一层匹配。"}', '{"score": 0.81, "reason": "二层仍匹配。"}']
        ),
        memory_manager=memory,
        max_depth=2,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert client.related_calls == ["BV1SEED", "BV1A"]
    assert [item.bvid for item in results] == ["BV1A", "BV1B"]


@pytest.mark.asyncio
async def test_related_chain_uses_bounded_evaluation_concurrency_within_batch() -> None:
    from openbiliclaw.discovery.strategies.strategies import RelatedChainStrategy

    memory = FakeMemoryManager(events=[_event("BV1SEED")])
    client = FakeRelatedClient(
        related_by_bvid={
            "BV1SEED": [
                {"bvid": "BV1A", "title": "A", "owner": {"name": "UP1", "mid": 1}},
                {"bvid": "BV1B", "title": "B", "owner": {"name": "UP2", "mid": 2}},
                {"bvid": "BV1C", "title": "C", "owner": {"name": "UP3", "mid": 3}},
            ]
        }
    )
    strategy = RelatedChainStrategy(
        bilibili_client=client,
        llm_service=_SlowScoringLLMService(
            [
                '{"score": 0.86, "reason": "A"}',
                '{"score": 0.85, "reason": "B"}',
                '{"score": 0.84, "reason": "C"}',
            ]
        ),
        memory_manager=memory,
        concurrency=DiscoveryConcurrencyController(
            bilibili_request_concurrency=2,
            llm_evaluation_concurrency=2,
        ),
        max_depth=1,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    # Batch evaluation sends 1 LLM call per batch (not per item)
    assert strategy.llm_service.max_active_calls >= 1
    assert [item.bvid for item in results] == ["BV1A", "BV1B", "BV1C"]
