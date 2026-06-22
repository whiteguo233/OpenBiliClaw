"""Tests for search-based discovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

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


def test_search_strategy_map_search_result_maps_available_metrics() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    strategy = SearchStrategy(
        llm_service=FakeLLMService("{}"),
        bilibili_client=FakeBilibiliClient({}),
        llm_evaluation=False,
    )

    content = strategy._map_search_result(
        {
            "bvid": "BV1metrics",
            "title": "指标视频",
            "author": "UP",
            "play": "1,200",
            "like": "100",
            "favorites": "90",
            "video_review": "80",
            "review": "70",
            "description": "desc",
        },
        query="纪录片",
        query_index=0,
        item_index=0,
        interest_anchors=[],
    )

    assert content is not None
    assert content.view_count == 1200
    assert content.like_count == 100
    assert content.favorite_count == 90
    assert content.danmaku_count == 80
    assert content.comment_count == 70


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
async def test_search_strategy_maps_bilibili_publish_time() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    pubdate = 1_710_000_000
    llm_service = FakeLLMService('{"queries": ["纪录片"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "纪录片": [
                {
                    "bvid": "BV1PUB",
                    "title": "发布较新的纪录片",
                    "author": "UP",
                    "pubdate": pubdate,
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

    assert results[0].published_at == datetime.fromtimestamp(pubdate, UTC).isoformat()


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


def test_search_backfill_does_not_drop_below_normal_admission_floor() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    strategy = SearchStrategy(
        llm_service=FakeLLMService([]),
        bilibili_client=FakeBilibiliClient({}),
        score_threshold=0.65,
    )

    backfill = strategy.create_backfill_strategy()

    assert backfill is not None
    assert backfill.score_threshold == 0.60


def test_search_default_score_threshold_is_normal_admission_floor() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    strategy = SearchStrategy(
        llm_service=FakeLLMService([]),
        bilibili_client=FakeBilibiliClient({}),
    )

    assert strategy.score_threshold == 0.60


def test_build_profile_summary_keeps_newest_window_and_all_dislikes() -> None:
    profile = _build_profile()
    profile.preferences.disliked_topics = [f"避雷{i}" for i in range(1, 141)]
    # Windows are chronological oldest→newest (cognition_cycle keeps the
    # tail); the summary must surface the newest entries, not the stalest.
    # 31 entries against a 30-wide window: the oldest one must drop, proving
    # the summary surfaces the newest 30 (tail), not the stalest.
    profile.recent_awareness = [
        AwarenessNote(
            date=f"2026-06-{day:02d}",
            observation=f"观察{day}",
            trend="",
            emotion_guess="",
        )
        for day in range(1, 32)
    ]
    profile.active_insights = [
        InsightHypothesis(hypothesis=f"洞察{i}", evidence=["证据"], confidence=0.5)
        for i in range(1, 32)
    ]

    summary = build_profile_summary(profile)

    # Dislike cap == store cap (128): nothing stored is hidden from prompts.
    assert summary["disliked_topics"] == [f"避雷{i}" for i in range(1, 129)]
    assert [n["observation"] for n in summary["recent_awareness"]] == [
        f"观察{day}" for day in range(2, 32)
    ]
    assert [i["hypothesis"] for i in summary["active_insights"]] == [
        f"洞察{i}" for i in range(2, 32)
    ]


def test_extract_interest_tags_fills_specifics_by_global_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.discovery.strategies import _utils

    profile = OnionProfile(
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="娱乐",
                    weight=0.9,
                    specifics=[
                        InterestSpecific(name="娱乐头部", weight=0.85),
                        InterestSpecific(name="娱乐次级", weight=0.3),
                    ],
                ),
                InterestDomain(
                    domain="小域",
                    weight=0.5,
                    specifics=[
                        # Same name as its domain: must not duplicate.
                        InterestSpecific(name="小域", weight=0.95),
                        InterestSpecific(name="小域强项", weight=0.8),
                    ],
                ),
            ]
        )
    )
    monkeypatch.setattr(_utils, "_INTEREST_TAG_CAP", 4)

    summary = build_profile_summary(profile)
    names = [str(i["name"]) for i in summary["interests"]]

    # Domain tags first, then remaining slots go to the globally
    # highest-weight specifics: 小域强项 (0.8) beats 娱乐次级 (0.3) even
    # though its domain ranks lower. The old per-domain fill order
    # admitted 娱乐次级 here and hid 小域强项.
    assert names == ["娱乐", "小域", "娱乐头部", "小域强项"]


# ── P1.5 strategy keyword injection ──────────────────────────────────


@pytest.mark.asyncio
async def test_search_strategy_injected_queries_skip_llm_generation() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["不应被使用"]}')
    bilibili_client = FakeBilibiliClient(
        {
            "机械键盘 测评": [
                {"bvid": "BV1A", "title": "键盘测评", "author": "UP", "mid": 1, "play": 1}
            ],
            "城市纪录片": [
                {"bvid": "BV1B", "title": "城市纪录", "author": "UP", "mid": 2, "play": 2}
            ],
        }
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )

    await strategy.discover(
        _build_profile(),
        limit=20,
        queries=["机械键盘 测评", "城市纪录片"],
    )

    # Searched exactly the injected queries, and made NO keyword-gen LLM call.
    assert bilibili_client.calls == ["机械键盘 测评", "城市纪录片"]
    assert llm_service.calls == []
    assert strategy.last_intermediates == {"queries": ["机械键盘 测评", "城市纪录片"]}


@pytest.mark.asyncio
async def test_search_strategy_injected_queries_are_deduped() -> None:
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["x"]}')
    bilibili_client = FakeBilibiliClient({})
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )

    await strategy.discover(
        _build_profile(),
        limit=20,
        queries=["  纪录片  ", "纪录片", "", "摄影"],
    )

    assert bilibili_client.calls == ["纪录片", "摄影"]
    assert llm_service.calls == []


@pytest.mark.asyncio
async def test_search_strategy_without_injection_still_generates() -> None:
    # Flag-off / no-injection regression: queries=None → legacy LLM gen runs.
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    llm_service = FakeLLMService('{"queries": ["纪录片 原理"]}')
    bilibili_client = FakeBilibiliClient(
        {"纪录片 原理": [{"bvid": "BV1A", "title": "t", "author": "u", "mid": 1, "play": 1}]}
    )
    strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        llm_evaluation=False,
    )

    await strategy.discover(_build_profile(), limit=20)

    assert bilibili_client.calls == ["纪录片 原理"]
    assert len(llm_service.calls) == 1


# ---------------------------------------------------------------------------
# End-to-end: graded search cooldown through the *real* BilibiliAPIClient.
#
# These drive SearchStrategy / ExploreStrategy against a real BilibiliAPIClient
# (so the real cooldown logic + the strategy's own storm-abort both run),
# faking only the HTTP boundary. They prove the v0.3.124 fix: a single
# v_voucher-challenged keyword no longer aborts the whole search round + the
# explore strategy that shares the process-wide cooldown, while a sustained
# storm still backs off.
# ---------------------------------------------------------------------------

_E2E_NAV_PAYLOAD = {
    "code": 0,
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    },
}


class _HttpResp:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _ScriptedSearchHTTP:
    """Fake httpx client: /nav always OK; search/type pops a scripted queue."""

    def __init__(self, search_payloads: list[dict[str, object]]) -> None:
        self._search = list(search_payloads)
        self.search_calls = 0

    async def get(
        self,
        url: str,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _HttpResp:
        if url.endswith("/x/web-interface/nav"):
            return _HttpResp(_E2E_NAV_PAYLOAD)
        if url.endswith("/x/web-interface/wbi/search/type"):
            self.search_calls += 1
            if not self._search:
                raise AssertionError("search/type called more times than scripted")
            return _HttpResp(self._search.pop(0))
        raise AssertionError(f"Unexpected URL: {url}")

    async def aclose(self) -> None:
        return None


def _voucher_payload() -> dict[str, object]:
    return {"code": 0, "data": {"v_voucher": "challenge", "result": None}}


def _results_payload(*bvids: str) -> dict[str, object]:
    return {
        "code": 0,
        "data": {
            "result": [
                {
                    "bvid": bvid,
                    "title": f"标题 {bvid}",
                    "author": f"UP {bvid}",
                    "mid": 1000 + index,
                    "description": "纪录片相关内容",
                    "pic": "https://example.com/cover.jpg",
                    "play": 12345,
                    "duration": "10:00",
                }
                for index, bvid in enumerate(bvids)
            ]
        },
    }


def _real_search_client(search_payloads: list[dict[str, object]]) -> object:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    client = BilibiliAPIClient(cookie="SESSDATA=e2e")
    client._client = _ScriptedSearchHTTP(search_payloads)  # type: ignore[assignment]
    return client


class _RaisingLLM:
    """Fails loudly if any LLM call slips through (proves a gate short-circuited)."""

    async def complete_structured_task(self, **_kwargs: object) -> object:
        raise AssertionError("LLM must not be called on this path")


@pytest.fixture(autouse=True)
def _reset_search_cooldown_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from openbiliclaw.bilibili.api import BilibiliAPIClient

    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", 0.0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_level", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_voucher_block_streak", 0)
    monkeypatch.setattr(BilibiliAPIClient, "_search_dom_fallback_until", 0.0)


@pytest.mark.asyncio
async def test_e2e_single_v_voucher_keyword_does_not_abort_search_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One challenged keyword is dropped; the rest of the round still searches."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    # q1 storms (3 attempts → 3 vouchers), q2 then q3 succeed. q2 runs with
    # streak>0 so it fast-fails to a single probe — which here returns results.
    client = _real_search_client(
        [
            _voucher_payload(),
            _voucher_payload(),
            _voucher_payload(),
            _results_payload("BV2"),
            _results_payload("BV3"),
        ]
    )
    monkeypatch.setattr(SearchStrategy, "_create_search_client", lambda self: client)

    strategy = SearchStrategy(
        llm_service=_RaisingLLM(),  # never called: queries injected + eval off
        bilibili_client=client,
        llm_evaluation=False,
    )

    results = await strategy.discover(_build_profile(), limit=20, queries=["q1", "q2", "q3"])

    # The round was NOT aborted by q1's storm: q2 + q3 results came through.
    assert {item.bvid for item in results} == {"BV2", "BV3"}
    # A lone v_voucher keyword must not trip the shared process-wide cooldown.
    assert BilibiliAPIClient.search_cooldown_remaining() == 0.0
    assert client._client.search_calls == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_e2e_v_voucher_storm_trips_cooldown_and_aborts_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consecutive challenged keywords past the threshold still back off."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.discovery.strategies.strategies import SearchStrategy

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    # q1 (3 vouchers) → streak 1; q2 (1 probe) → streak 2; q3 (1 probe) →
    # streak 3 == threshold → cooldown trips. q4 must never hit the network.
    client = _real_search_client(
        [
            _voucher_payload(),
            _voucher_payload(),
            _voucher_payload(),
            _voucher_payload(),
            _voucher_payload(),
        ]
    )
    monkeypatch.setattr(SearchStrategy, "_create_search_client", lambda self: client)

    strategy = SearchStrategy(
        llm_service=_RaisingLLM(),
        bilibili_client=client,
        llm_evaluation=False,
    )

    results = await strategy.discover(_build_profile(), limit=20, queries=["q1", "q2", "q3", "q4"])

    assert results == []
    assert BilibiliAPIClient.search_cooldown_remaining() > 0  # storm backed off
    # q4 skipped — never searched once the cooldown tripped.
    assert client._client.search_calls == 5  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_e2e_explore_skips_while_search_cooldown_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explore shares the same process-wide cooldown and skips when it's hot."""
    import time

    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.discovery.strategies.strategies import ExploreStrategy

    # Trip the shared cooldown directly (as a real storm would have).
    monkeypatch.setattr(BilibiliAPIClient, "_search_cooldown_until", time.monotonic() + 120.0)
    client = _real_search_client([])

    strategy = ExploreStrategy(
        llm_service=_RaisingLLM(),  # must NOT be reached: gate returns first
        bilibili_client=client,
    )

    results = await strategy.discover(_build_profile(), limit=20)

    assert results == []
    assert strategy.last_intermediates.get("skipped") == "search_cooldown"
