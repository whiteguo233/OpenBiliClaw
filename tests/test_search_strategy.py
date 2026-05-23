"""Tests for search-based discovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from openbiliclaw.discovery.engine import DiscoveryConcurrencyController
from openbiliclaw.discovery.pool_snapshot import PoolDistributionSnapshot
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.soul.profile import (
    MBTI,
    AwarenessNote,
    ContextMode,
    CoreLayer,
    InsightHypothesis,
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    InterestTag,
    MBTIDimension,
    OnionProfile,
    PreferenceLayer,
    RoleLayer,
    SoulProfile,
    StylePreference,
    SurfaceLayer,
    ValuesLayer,
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


def test_build_profile_summary_includes_full_profile_context() -> None:
    profile = OnionProfile(
        personality_portrait="重视结构与证据的人。",
        core=CoreLayer(
            core_traits=["理性", "谨慎", "好奇"],
            deep_needs=["确定性", "掌控感"],
            mbti=MBTI(
                type="INTJ",
                confidence=0.76,
                dimensions={"EI": MBTIDimension(pole="I", strength=0.8)},
                inferred_from=["长期观看模式"],
            ),
        ),
        values_layer=ValuesLayer(
            values=["真实", "自主"],
            motivational_drivers=["理解底层逻辑", "减少噪声"],
        ),
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="国际局势",
                    weight=0.9,
                    specifics=[InterestSpecific(name="中东局势", weight=0.8)],
                    first_seen="2026-01-01",
                    last_seen="2026-05-01",
                    source="behavior",
                )
            ],
            dislikes=[
                InterestDomain(
                    domain="标题党",
                    weight=0.9,
                    specifics=[InterestSpecific(name="低质混剪", weight=0.8)],
                )
            ],
            favorite_up_users=["小约翰可汗"],
        ),
        role=RoleLayer(life_stage="工作稳定期", current_phase="重新整理信息源"),
        surface=SurfaceLayer(
            cognitive_style=["喜欢结构化拆解", "先看证据再下判断"],
            style=StylePreference(
                preferred_duration="long",
                preferred_pace="moderate",
                quality_sensitivity=0.82,
                humor_preference=0.2,
                depth_preference=0.9,
            ),
            context=ContextMode(session_type="deep_dive"),
            exploration_openness=0.66,
        ),
        source_platform_mix={"bilibili": 0.7, "youtube": 0.3},
        recent_awareness=[
            AwarenessNote(
                date="2026-05-17",
                observation="最近避开标题党内容。",
                trend="更偏向可信来源。",
                emotion_guess="可能在降噪。",
            )
        ],
        active_insights=[
            InsightHypothesis(
                hypothesis="用户最近在主动收敛信息源。",
                evidence=["连续 dislike 低质混剪"],
                confidence=0.83,
                validated=True,
            )
        ],
    )

    summary = build_profile_summary(profile)

    assert summary["cognitive_style"] == ["喜欢结构化拆解", "先看证据再下判断"]
    assert summary["values"] == ["真实", "自主"]
    assert summary["motivational_drivers"] == ["理解底层逻辑", "减少噪声"]
    assert summary["current_phase"] == "重新整理信息源"
    assert summary["life_stage"] == "工作稳定期"
    assert summary["source_platform_mix"] == {"bilibili": 0.7, "youtube": 0.3}
    assert summary["style"]["quality_sensitivity"] == 0.82
    assert summary["disliked_topics"] == ["标题党", "低质混剪"]
    assert summary["mbti"] == {
        "type": "INTJ",
        "confidence": 0.76,
        "dimensions": {"EI": {"pole": "I", "strength": 0.8}},
        "inferred_from": ["长期观看模式"],
    }
    assert summary["recent_awareness"] == [
        {
            "date": "2026-05-17",
            "observation": "最近避开标题党内容。",
            "trend": "更偏向可信来源。",
            "emotion_guess": "可能在降噪。",
        }
    ]
    assert summary["active_insights"] == [
        {
            "hypothesis": "用户最近在主动收敛信息源。",
            "evidence": ["连续 dislike 低质混剪"],
            "confidence": 0.83,
            "validated": True,
        }
    ]
    assert summary["interest_domains"][0]["source"] == "behavior"
    assert summary["interest_domains"][0]["first_seen"] == "2026-01-01"
    assert summary["interests"][0]["name"] == "国际局势"


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
        caller: str = "",
        reasoning_effort: str | None = None,
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
async def test_search_strategy_skips_llm_query_generation_during_search_cooldown() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    class CoolingSearchClient(FakeBilibiliClient):
        def search_cooldown_remaining(self) -> float:
            return 120.0

    llm_service = FakeLLMService('{"queries": ["纪录片 原理"]}')
    bilibili_client = CoolingSearchClient({})
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert results == []
    assert llm_service.calls == []
    assert bilibili_client.calls == []


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
async def test_search_strategy_passes_disliked_topics_to_query_prompt() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["摄影 vlog"]}')
    profile = _build_profile()
    profile.preferences.disliked_topics = ["标题党", "低质混剪"]
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=FakeBilibiliClient({}),
        llm_evaluation=False,
    )

    await strategy._generate_queries(profile)

    user_input = str(llm_service.calls[0]["user_input"])
    assert '"disliked_topics": [' in user_input
    assert "标题党" in user_input
    assert "低质混剪" in user_input


@pytest.mark.asyncio
async def test_search_strategy_passes_pool_snapshot_to_query_prompt() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["人物纪录 审美体验"]}')
    snapshot = PoolDistributionSnapshot(
        pool_target_count=100,
        pool_available_count=80,
        source_targets={"search": 25},
        source_counts={"search": 20},
        source_deficits={"search": 5},
        saturated_topics=("AI 编程",),
        saturated_styles=("deep_dive",),
        undercovered_axes=("人物纪录",),
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=FakeBilibiliClient({}),
        llm_evaluation=False,
    )

    await strategy.discover(_build_profile(), limit=20, pool_snapshot=snapshot)

    user_input = str(llm_service.calls[0]["user_input"])
    assert "pool_distribution_hints" in user_input


@pytest.mark.asyncio
async def test_search_strategy_drops_bad_pool_hints_and_uses_llm_queries() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    class BadPoolSnapshot:
        def to_prompt_hints(self) -> dict[str, object]:
            raise RuntimeError("bad hints")

    llm_service = FakeLLMService('{"queries": ["纪录片 人物故事"]}')
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=FakeBilibiliClient({}),
        queries_per_run=2,
        llm_evaluation=False,
    )

    queries = await strategy._generate_queries(_build_profile(), pool_snapshot=BadPoolSnapshot())

    assert queries == ["纪录片 人物故事"]
    assert len(llm_service.calls) == 1
    assert "pool_distribution_hints" not in str(llm_service.calls[0]["user_input"])


@pytest.mark.asyncio
async def test_search_strategy_drops_unserializable_pool_hints_and_uses_llm_queries() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    class UnserializablePoolSnapshot:
        def to_prompt_hints(self) -> dict[str, object]:
            return {"avoid_topics": [object()]}

    llm_service = FakeLLMService('{"queries": ["城市纪录片 日常"]}')
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=FakeBilibiliClient({}),
        queries_per_run=2,
        llm_evaluation=False,
    )

    queries = await strategy._generate_queries(
        _build_profile(),
        pool_snapshot=UnserializablePoolSnapshot(),
    )

    assert queries == ["城市纪录片 日常"]
    assert len(llm_service.calls) == 1
    assert "pool_distribution_hints" not in str(llm_service.calls[0]["user_input"])


@pytest.mark.asyncio
async def test_search_strategy_dedicated_client_preserves_auth_cookie() -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    shared_client = BilibiliAPIClient(cookie="SESSDATA=test-cookie")
    strategy = SearchStrategy(
        llm_service=FakeLLMService("{}"),
        bilibili_client=shared_client,
        llm_evaluation=False,
    )

    search_client = strategy._create_search_client()

    try:
        assert search_client is not shared_client
        assert getattr(search_client, "is_authenticated", False) is True
    finally:
        close = getattr(search_client, "close", None)
        if callable(close):
            await close()
        await shared_client.close()


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


@pytest.mark.asyncio
async def test_search_strategy_caps_llm_eval_candidates_for_small_limit() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    class BatchRecordingLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

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
            del system_instruction, history, temperature, max_tokens, caller, reasoning_effort
            if "<content_batch>" not in user_input:
                return _FakeResponse('{"queries": ["q0", "q1", "q2", "q3"]}')
            batch = json.loads(user_input.split("<content_batch>")[1].split("</content_batch>")[0])
            self.batch_sizes.append(len(batch))
            return _FakeResponse(
                json.dumps(
                    [{"score": 0.82, "reason": "ok", "style_key": "deep_dive"} for _ in batch]
                )
            )

    llm_service = BatchRecordingLLM()
    bilibili_client = FakeBilibiliClient(
        {
            f"q{query_index}": [
                {
                    "bvid": f"BVQ{query_index}_{item_index}",
                    "title": f"q{query_index}-{item_index}",
                    "author": f"UP{query_index}",
                    "mid": item_index,
                }
                for item_index in range(20)
            ]
            for query_index in range(4)
        }
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        score_threshold=0.65,
    )

    results = await strategy.discover(_build_profile(), limit=3)

    assert llm_service.batch_sizes == [6]
    assert [item.bvid for item in results] == ["BVQ0_0", "BVQ1_0", "BVQ2_0"]
