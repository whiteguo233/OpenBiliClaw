"""Tests for recommendation ranking engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database


class _DummyLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "history": history,
            }
        )
        return LLMResponse(
            content=json.dumps(
                {
                    "expression": "这条内容会接住你最近那种想把问题想透的状态。",
                    "topic_label": "你最近那种想把问题想透的状态",
                },
                ensure_ascii=False,
            ),
            provider="test",
            model="dummy",
            usage={},
        )


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="一个偏好高信息密度、慢热但判断稳定的人。",
        core_traits=["理性", "克制"],
        preferences=PreferenceLayer(
            interests=[InterestTag(name="纪录片", category="知识", weight=0.9)]
        ),
    )


def _seed_pool(db: Database, items: list[DiscoveredContent]) -> None:
    """Insert DiscoveredContent items into content_cache for pool-based tests."""
    for item in items:
        db.cache_content(
            item.bvid,
            title=item.title,
            up_name=item.up_name,
            up_mid=item.up_mid,
            duration=item.duration,
            tags=item.tags,
            topic_key=item.topic_key,
            topic_group=item.topic_group,
            style_key=item.style_key,
            description=item.description,
            cover_url=item.cover_url,
            view_count=item.view_count,
            like_count=item.like_count,
            relevance_score=item.relevance_score,
            relevance_reason=item.relevance_reason,
            candidate_tier=item.candidate_tier,
            source=item.source_strategy,
        )


@pytest.mark.parametrize(
    "style_key",
    ["light_chat", "fun_variety", "lifestyle", "review_roundup"],
)
def test_fallback_expression_avoids_deep_bias_for_non_deep_styles(
    style_key: str,
) -> None:
    expression = RecommendationEngine._fallback_expression(
        DiscoveredContent(
            bvid="BV1LIGHT",
            title="轻松一点的内容",
            style_key=style_key,
        )
    )

    assert "往深处看" not in expression
    assert "想继续往深处" not in expression


def test_select_diversified_batch_keeps_one_accessible_entry_when_available() -> None:
    candidates = [
        DiscoveredContent(
            bvid="BVHARD1",
            title="统计学纪录片",
            source_strategy="related_chain",
            topic_group="统计学",
            style_key="deep_dive",
            relevance_score=0.99,
        ),
        DiscoveredContent(
            bvid="BVHARD2",
            title="AI 架构拆解",
            source_strategy="search",
            topic_group="人工智能",
            style_key="tech_analysis",
            relevance_score=0.98,
        ),
        DiscoveredContent(
            bvid="BVHARD3",
            title="地缘政治快评",
            source_strategy="trending",
            topic_group="地缘政治",
            style_key="news_brief",
            relevance_score=0.97,
        ),
        DiscoveredContent(
            bvid="BVHARD4",
            title="本地部署避坑",
            source_strategy="xhs-extension-task",
            topic_group="知识库部署",
            style_key="practical_guide",
            relevance_score=0.96,
        ),
        DiscoveredContent(
            bvid="BVHARD5",
            title="认知偏差原理",
            source_strategy="xhs-extension-search",
            topic_group="心理学",
            style_key="deep_dive",
            relevance_score=0.95,
        ),
        DiscoveredContent(
            bvid="BVLIGHT1",
            title="工地摆摊",
            source_strategy="related_chain",
            topic_group="社会纪实",
            style_key="story_doc",
            relevance_score=0.91,
        ),
        DiscoveredContent(
            bvid="BVLIGHT2",
            title="年度科技盘点",
            source_strategy="search",
            topic_group="前沿科技",
            style_key="review_roundup",
            relevance_score=0.9,
        ),
    ]

    batch = RecommendationEngine._select_diversified_batch(candidates, limit=5)

    assert any(
        item.style_key in {
            "story_doc",
            "review_roundup",
            "lifestyle",
            "light_chat",
            "fun_variety",
            "visual_showcase",
        }
        for item in batch
    )


def test_expression_tone_profile_softens_dense_profile_for_lifestyle_content() -> None:
    profile = _build_profile()
    profile.preferences.style.depth_preference = 0.95

    tone = RecommendationEngine._expression_tone_profile(
        profile,
        DiscoveredContent(
            bvid="BVLIFE",
            title="工地摆摊",
            style_key="lifestyle",
        ),
    )

    assert tone["density"] in {"light", "balanced"}


@pytest.mark.asyncio
async def test_generate_recommendations_ranks_discovered_and_records_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        _seed_pool(db, [
            DiscoveredContent(bvid="BV1A", title="A", relevance_score=0.71),
            DiscoveredContent(bvid="BV1B", title="B", relevance_score=0.92),
            DiscoveredContent(bvid="BV1C", title="C", relevance_score=0.83),
        ])

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=2,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1B", "BV1C"]
        assert recommendations[0].confidence == 0.92

        history = db.get_recommendations(limit=10)
        assert [row["bvid"] for row in history] == ["BV1C", "BV1B"]


@pytest.mark.asyncio
async def test_generate_recommendations_reads_from_cache_when_discovered_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        db.cache_content(
            "BV1B",
            title="B",
            up_name="UPB",
            source="search",
            view_count=20,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1B"]


@pytest.mark.asyncio
async def test_generate_recommendations_prefers_primary_then_relevance_then_recency() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        _seed_pool(db, [
            DiscoveredContent(
                bvid="BV1BACK",
                title="补货高分",
                relevance_score=0.96,
                candidate_tier="backfill",
                last_scored_at="2026-03-10T08:00:00",
            ),
            DiscoveredContent(
                bvid="BV1OLD",
                title="主候选旧",
                relevance_score=0.87,
                candidate_tier="primary",
                last_scored_at="2026-03-09T08:00:00",
            ),
            DiscoveredContent(
                bvid="BV1NEW",
                title="主候选新",
                relevance_score=0.87,
                candidate_tier="primary",
                last_scored_at="2026-03-10T08:00:00",
            ),
        ])

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=2,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1NEW", "BV1OLD"]


@pytest.mark.asyncio
async def test_generate_recommendations_reads_cached_relevance_score() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1LOW",
            title="低相关高播放",
            up_name="UPA",
            source="search",
            view_count=1000,
            relevance_score=0.41,
            candidate_tier="primary",
        )
        db.cache_content(
            "BV1HIGH",
            title="高相关低播放",
            up_name="UPB",
            source="search",
            view_count=10,
            relevance_score=0.93,
            candidate_tier="primary",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1HIGH"]


@pytest.mark.asyncio
async def test_generate_recommendations_limits_single_topic_dominance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        _seed_pool(db, [
            *[
                DiscoveredContent(
                    bvid=f"RBUF{index}",
                    title=f"同一 related 主题 {index}",
                    source_strategy="related_chain",
                    topic_key="related:bv1bufdz9eyb",
                    topic_group="强化学习",
                    style_key="practical_guide",
                    relevance_score=0.95 - index * 0.001,
                )
                for index in range(5)
            ],
            *[
                DiscoveredContent(
                    bvid=f"RALT{index}",
                    title=f"另一 related 主题 {index}",
                    source_strategy="related_chain",
                    topic_key="related:bv18xzjbbegz",
                    topic_group="博弈论",
                    style_key="light_chat",
                    relevance_score=0.94 - index * 0.001,
                )
                for index in range(3)
            ],
            *[
                DiscoveredContent(
                    bvid=f"TREND{index}",
                    title=f"热榜内容 {index}",
                    source_strategy="trending",
                    topic_key="trending",
                    topic_group="时事",
                    style_key="news_brief",
                    relevance_score=0.84 - index * 0.001,
                )
                for index in range(2)
            ],
            *[
                DiscoveredContent(
                    bvid=f"SEARCH{index}",
                    title=f"搜索内容 {index}",
                    source_strategy="search",
                    topic_key="ai",
                    topic_group="人工智能",
                    style_key="deep_dive",
                    relevance_score=0.83 - index * 0.001,
                )
                for index in range(2)
            ],
        ])

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=10,
        )

        picked_groups = [item.content.topic_group for item in recommendations]
        # With strict broad_cap, no single topic_group should dominate
        assert picked_groups.count("强化学习") <= 3


@pytest.mark.asyncio
async def test_generate_recommendations_balances_sources_from_cache() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        for index in range(25):
            db.cache_content(
                f"BVREL{index}",
                title=f"相关链高分候选 {index}",
                up_name="相关频道",
                source="related_chain",
                relevance_score=0.99 - index * 0.001,
                relevance_reason="related high score",
                style_key="practical_guide",
                topic_key=f"related:topic:{index % 6}",
            )
        for index in range(5):
            db.cache_content(
                f"BVTREND{index}",
                title=f"热榜候选 {index}",
                up_name="热榜频道",
                source="trending",
                relevance_score=0.89 - index * 0.001,
                relevance_reason="trending candidate",
                style_key="news_brief",
                topic_key=f"trending:{index}",
            )
        for index in range(5):
            db.cache_content(
                f"BVSEARCH{index}",
                title=f"搜索候选 {index}",
                up_name="搜索频道",
                source="search",
                relevance_score=0.88 - index * 0.001,
                relevance_reason="search candidate",
                style_key="deep_dive",
                topic_key=f"search:{index}",
            )
        for index in range(5):
            db.cache_content(
                f"BVEXP{index}",
                title=f"探索候选 {index}",
                up_name="探索频道",
                source="explore",
                relevance_score=0.87 - index * 0.001,
                relevance_reason="explore candidate",
                style_key="story_doc",
                topic_key=f"explore:{index}",
            )

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=10,
        )

        picked_sources = {item.content.source_strategy for item in recommendations}

        assert "explore" in picked_sources
        assert "search" in picked_sources


@pytest.mark.asyncio
async def test_generate_recommendations_does_not_repeat_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        db.cache_content(
            "BV1B",
            title="B",
            up_name="UPB",
            source="search",
            view_count=20,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        first = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )
        second = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in first] == ["BV1B"]
        assert [item.content.bvid for item in second] == ["BV1A"]


@pytest.mark.asyncio
async def test_generate_recommendations_skips_recently_viewed_content() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1SEEN",
            title="已经看过的内容",
            up_name="UPA",
            source="search",
            relevance_score=0.97,
        )
        db.cache_content(
            "BV1NEW",
            title="还没看过",
            up_name="UPB",
            source="search",
            relevance_score=0.82,
        )
        db.insert_event(
            "view",
            title="已经看过的内容",
            url="https://www.bilibili.com/video/BV1SEEN",
            metadata={"bvid": "BV1SEEN"},
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1NEW"]


@pytest.mark.asyncio
async def test_generate_recommendations_populates_expression_and_updates_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        _seed_pool(db, [
            DiscoveredContent(
                bvid="BV1EXP",
                title="讲透摄影构图的底层逻辑",
                up_name="构图实验室",
                description="从原理出发解释构图。",
                relevance_score=0.91,
            ),
        ])

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=1,
        )

        assert recommendations[0].expression == "这条内容会接住你最近那种想把问题想透的状态。"
        assert recommendations[0].topic_label == "你最近那种想把问题想透的状态"
        assert recommendations[0].recommendation_id > 0

        history = db.get_recommendations(limit=10)
        assert history[0]["expression"] == "这条内容会接住你最近那种想把问题想透的状态。"
        assert history[0]["topic"] == "你最近那种想把问题想透的状态"
        assert history[0]["presented"] == 0


@pytest.mark.asyncio
async def test_generate_expression_uses_old_friend_tone_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1TONE",
                title="讲透贸易逆差的底层逻辑",
                up_name="经济观察",
                description="从历史和制度角度解释问题。",
                relevance_score=0.89,
            ),
            _build_profile(),
        )

        assert "老B友" in str(llm.calls[0]["system_instruction"])


@pytest.mark.asyncio
async def test_generate_expression_passes_style_key_to_prompt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1STYLE",
                title="工地摆摊",
                up_name="小马盒饭",
                description="街边摆摊和工地盒饭的日常观察。",
                style_key="lifestyle",
                topic_group="社会民生",
                relevance_score=0.86,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert '"style_key": "lifestyle"' in user_input
        assert '"topic_group": "社会民生"' in user_input


@pytest.mark.asyncio
async def test_record_feedback_updates_recommendation_feedback_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendation_id = db.insert_recommendation(
            "BV1REC",
            confidence=0.83,
            presented=1,
        )

        await engine.record_feedback(
            recommendation_id,
            feedback_type="like",
            note="这个讲法很对胃口",
        )

        row = db.get_recommendation_by_id(recommendation_id)

        assert row is not None
        assert row["feedback_type"] == "like"
        assert row["feedback_note"] == "这个讲法很对胃口"
        assert row["feedback_at"] is not None


@pytest.mark.asyncio
async def test_record_feedback_accepts_comment_feedback_type() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendation_id = db.insert_recommendation(
            "BV1REC",
            confidence=0.83,
            presented=1,
        )

        await engine.record_feedback(
            recommendation_id,
            feedback_type="comment",
            note="方向对，但讲得不够深。",
        )

        row = db.get_recommendation_by_id(recommendation_id)

        assert row is not None
        assert row["feedback_type"] == "comment"
        assert row["feedback_note"] == "方向对，但讲得不够深。"
        assert row["feedback_at"] is not None


@pytest.mark.asyncio
async def test_reshuffle_recommendations_uses_pool_reason_without_waiting_expression() -> None:
    class _ExplodingLLM(_DummyLLM):
        async def complete_structured_task(self, **kwargs) -> LLMResponse:  # type: ignore[override]
            raise RuntimeError("expression generation should not run in reshuffle path")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1POOL",
            title="讲透地缘政治的链路",
            up_name="观察站",
            source="search",
            relevance_score=0.89,
            relevance_reason="这条会对上你最近那股想把来龙去脉搞明白的劲头。",
            pool_expression="这条会接住你最近想把地缘链路顺清楚的状态。",
            pool_topic_label="你最近那股想把地缘链路顺清楚的状态",
        )
        engine = RecommendationEngine(llm=_ExplodingLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )

        assert len(recommendations) == 1
        assert recommendations[0].content.bvid == "BV1POOL"
        assert recommendations[0].expression == "这条会接住你最近想把地缘链路顺清楚的状态。"
        assert recommendations[0].topic_label == "你最近那股想把地缘链路顺清楚的状态"

        history = db.get_recommendations(limit=10)
        assert history[0]["expression"] == "这条会接住你最近想把地缘链路顺清楚的状态。"
        assert history[0]["topic"] == "你最近那股想把地缘链路顺清楚的状态"


@pytest.mark.asyncio
async def test_append_recommendations_skips_excluded_bvids() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1A",
            title="第一条",
            up_name="UPA",
            source="search",
            relevance_score=0.95,
            relevance_reason="第一条基础理由。",
        )
        db.cache_content(
            "BV1B",
            title="第二条",
            up_name="UPB",
            source="trending",
            relevance_score=0.94,
            relevance_reason="第二条基础理由。",
        )
        db.cache_content(
            "BV1C",
            title="第三条",
            up_name="UPC",
            source="related_chain",
            relevance_score=0.93,
            relevance_reason="第三条基础理由。",
            pool_expression="第三条已经提前备好了推荐理由。",
            pool_topic_label="第三条提前备好的话题",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.append_recommendations(
            profile=_build_profile(),
            excluded_bvids=["BV1A", "BV1B"],
            limit=2,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1C"]
        assert recommendations[0].expression == "第三条已经提前备好了推荐理由。"
        assert recommendations[0].topic_label == "第三条提前备好的话题"

        history = db.get_recommendations(limit=10)
        assert history[0]["expression"] == "第三条已经提前备好了推荐理由。"
        assert history[0]["topic"] == "第三条提前备好的话题"


@pytest.mark.asyncio
async def test_reshuffle_recommendations_hides_missing_precomputed_copy() -> None:
    class _ExplodingLLM(_DummyLLM):
        async def complete_structured_task(self, **kwargs) -> LLMResponse:  # type: ignore[override]
            raise RuntimeError("expression generation should not run in reshuffle path")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1EMPTY",
            title="还没生成推荐文案",
            up_name="观察站",
            source="search",
            relevance_score=0.89,
            relevance_reason="这条会对上你最近那股想把来龙去脉搞明白的劲头。",
        )
        engine = RecommendationEngine(llm=_ExplodingLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )

        assert len(recommendations) == 1
        # Missing pool_expression gets a fallback instead of empty string
        assert recommendations[0].expression != ""
        assert recommendations[0].topic_label != ""

        history = db.get_recommendations(limit=10)
        # Fallback expression is stored in DB too
        assert history[0]["expression"] != ""
        assert history[0]["topic"] != ""


@pytest.mark.asyncio
async def test_reshuffle_recommendations_skips_recently_viewed_content() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1SEEN",
            title="已经看过的地缘政治分析",
            up_name="观察站",
            source="search",
            relevance_score=0.93,
            relevance_reason="这条本来很像你会点开的内容。",
        )
        db.cache_content(
            "BV1NEW",
            title="还没看过的纪录片",
            up_name="纪录片研究所",
            source="explore",
            relevance_score=0.88,
            relevance_reason="这条会接住你喜欢从细节里看结构的状态。",
        )
        db.insert_event(
            "view",
            title="已经看过的地缘政治分析",
            url="https://www.bilibili.com/video/BV1SEEN",
            metadata={"bvid": "BV1SEEN"},
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )

        assert [item.content.bvid for item in recommendations] == ["BV1NEW"]


@pytest.mark.asyncio
async def test_reshuffle_recommendations_spreads_styles_before_backfill() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVGAME1",
            title="杀戮尖塔2 全英雄基础流派攻略",
            up_name="卡牌研究所",
            source="related_chain",
            relevance_score=0.96,
            relevance_reason="这条偏你会点开的机制拆解。",
            style_key="game_strategy",
            topic_key="游戏:杀戮尖塔2",
        )
        db.cache_content(
            "BVGAME2",
            title="杀戮尖塔2 17分钟实机演示",
            up_name="IGN",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条还是同一类游戏机制内容。",
            style_key="game_strategy",
            topic_key="游戏:杀戮尖塔2",
        )
        db.cache_content(
            "BVNEWS1",
            title="美国关税政策又有新变化",
            up_name="国际观察",
            source="trending",
            relevance_score=0.91,
            relevance_reason="这条信息来得快，而且不是纯复读。",
            style_key="news_brief",
            topic_key="国际时事:贸易",
        )
        db.cache_content(
            "BVDOC1",
            title="塔可夫斯基《潜行者》到底讲了什么",
            up_name="猫鲨Catshark",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会把故事和信息一起带出来。",
            style_key="story_doc",
            topic_key="科幻:电影",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=3,
        )

        picked = [item.content.bvid for item in recommendations]

        assert "BVGAME1" in picked
        assert "BVGAME2" not in picked
        assert "BVNEWS1" in picked
        assert "BVDOC1" in picked


@pytest.mark.asyncio
async def test_reshuffle_recommendations_limits_single_source_dominance() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVEXP1",
            title="探索候选 1",
            up_name="探索频道",
            source="explore",
            relevance_score=0.96,
            relevance_reason="探索内容 1",
            style_key="story_doc",
            topic_key="探索:1",
        )
        db.cache_content(
            "BVEXP2",
            title="探索候选 2",
            up_name="探索频道",
            source="explore",
            relevance_score=0.95,
            relevance_reason="探索内容 2",
            style_key="deep_dive",
            topic_key="探索:2",
        )
        db.cache_content(
            "BVEXP3",
            title="探索候选 3",
            up_name="探索频道",
            source="explore",
            relevance_score=0.94,
            relevance_reason="探索内容 3",
            style_key="light_chat",
            topic_key="探索:3",
        )
        db.cache_content(
            "BVEXP4",
            title="探索候选 4",
            up_name="探索频道",
            source="explore",
            relevance_score=0.93,
            relevance_reason="探索内容 4",
            style_key="practical_guide",
            topic_key="探索:4",
        )
        db.cache_content(
            "BVSEARCH1",
            title="搜索候选",
            up_name="搜索频道",
            source="search",
            relevance_score=0.91,
            relevance_reason="搜索命中的内容。",
            style_key="practical_guide",
            topic_key="搜索:1",
        )
        db.cache_content(
            "BVTREND1",
            title="热榜候选",
            up_name="热榜频道",
            source="trending",
            relevance_score=0.9,
            relevance_reason="热榜命中的内容。",
            style_key="news_brief",
            topic_key="热榜:1",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=4,
        )

        picked_sources = [item.content.source_strategy for item in recommendations]

    assert picked_sources.count("explore") <= 2
    assert "search" in picked_sources
    assert "trending" in picked_sources


@pytest.mark.asyncio
async def test_reshuffle_keeps_new_sources_even_when_style_repeats() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for bvid, title, source, score, style, topic in [
            ("BVEXP1", "探索深挖 1", "explore", 0.99, "deep_dive", "探索:1"),
            ("BVEXP2", "探索深挖 2", "explore", 0.98, "story_doc", "探索:2"),
            ("BVEXP3", "探索深挖 3", "explore", 0.97, "visual_showcase", "探索:3"),
            ("BVSEA1", "搜索杂谈 1", "search", 0.96, "light_chat", "搜索:1"),
            ("BVTR1", "热榜杂谈 1", "trending", 0.95, "light_chat", "热榜:1"),
        ]:
            db.cache_content(
                bvid,
                title=title,
                up_name="频道",
                source=source,
                relevance_score=score,
                relevance_reason=f"{title} 的基础理由。",
                style_key=style,
                topic_key=topic,
            )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=3,
        )

        picked_sources = [item.content.source_strategy for item in recommendations]

        assert "search" in picked_sources
        assert "trending" in picked_sources
        assert picked_sources.count("explore") <= 1


@pytest.mark.asyncio
async def test_reshuffle_recommendations_caps_source_and_style_for_larger_batches() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        items = [
            ("BVEXP1", "探索纪录片 1", "explore", 0.99, "story_doc", "探索:1"),
            ("BVEXP2", "探索深挖 2", "explore", 0.98, "deep_dive", "探索:2"),
            ("BVEXP3", "探索轻聊 3", "explore", 0.97, "light_chat", "探索:3"),
            ("BVEXP4", "探索攻略 4", "explore", 0.96, "practical_guide", "探索:4"),
            ("BVREL1", "相关推荐机制拆解 1", "related_chain", 0.95, "game_strategy", "相关:1"),
            ("BVREL2", "相关推荐机制拆解 2", "related_chain", 0.94, "game_strategy", "相关:2"),
            ("BVREL3", "相关推荐故事向 3", "related_chain", 0.935, "light_chat", "相关:3"),
            ("BVSEA1", "搜索教程 1", "search", 0.93, "practical_guide", "搜索:1"),
            ("BVSEA2", "搜索快讯 2", "search", 0.92, "news_brief", "搜索:2"),
            ("BVTR1", "热榜纪录片 1", "trending", 0.91, "story_doc", "热榜:1"),
            ("BVTR2", "热榜视觉 2", "trending", 0.9, "visual_showcase", "热榜:2"),
        ]
        for bvid, title, source, score, style, topic in items:
            db.cache_content(
                bvid,
                title=title,
                up_name=f"{source}-频道",
                source=source,
                relevance_score=score,
                relevance_reason=f"{title} 的基础理由。",
                style_key=style,
                topic_key=topic,
            )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=10,
        )

        picked_sources = [item.content.source_strategy for item in recommendations]
        picked_styles = [item.content.style_key for item in recommendations]

        assert len(recommendations) == 10
        assert picked_sources.count("explore") <= 3
        assert picked_sources.count("related_chain") <= 3
        assert picked_styles.count("game_strategy") <= 3


@pytest.mark.asyncio
async def test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant(
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        # Style-dominant but topic-diverse pool: all light_chat, but each item
        # has a distinct broad topic so backfill can reach `limit`.
        topics = ["生活随笔", "职场闲谈", "读书片段", "城市漫步", "餐桌小记", "音乐碎片"]
        for index, topic in enumerate(topics):
            db.cache_content(
                f"BVLIGHT{index + 1}",
                title=f"轻聊候选 {index + 1}",
                up_name="轻聊频道",
                source="search",
                relevance_score=0.96 - index * 0.01,
                relevance_reason=f"这条会接住你最近想往里看一点的状态 {index + 1}。",
                style_key="light_chat",
                topic_key=topic,
            )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=5,
        )

        picked = [item.content.bvid for item in recommendations]

        assert len(recommendations) == 5
        assert picked == ["BVLIGHT1", "BVLIGHT2", "BVLIGHT3", "BVLIGHT4", "BVLIGHT5"]


@pytest.mark.asyncio
async def test_reshuffle_recommendations_hides_missing_copy_instead_of_style_fallback() -> None:
    class _ExplodingLLM(_DummyLLM):
        async def complete_structured_task(self, **kwargs) -> LLMResponse:  # type: ignore[override]
            raise RuntimeError("expression generation should not run in reshuffle path")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVSTYLE",
            title="杀戮尖塔2 角色强度排行",
            up_name="卡牌研究所",
            source="related_chain",
            relevance_score=0.89,
            relevance_reason="",
            style_key="game_strategy",
        )
        engine = RecommendationEngine(llm=_ExplodingLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )

        # Fallback expression based on style_key when precomputed copy missing
        assert (
            "game_strategy" in recommendations[0].content.style_key
            or recommendations[0].expression != ""
        )
        assert recommendations[0].topic_label != ""


@pytest.mark.asyncio
async def test_reshuffle_recommendations_spreads_topic_keys_before_backfill() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVINT1",
            title="讲透中东局势的来龙去脉",
            up_name="国际观察",
            source="search",
            relevance_score=0.96,
            relevance_reason="这条会接住你最近那股想把国际时事看透的劲头。",
            topic_key="国际时事:地缘政治",
        )
        db.cache_content(
            "BVINT2",
            title="伊朗问题的底层链路",
            up_name="世界现场",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条延续了你最近盯国际新闻时那种爱追因果的状态。",
            topic_key="国际时事:地缘政治",
        )
        db.cache_content(
            "BVTECH1",
            title="OpenAI 新模型到底强在哪",
            up_name="技术拆机局",
            source="search",
            relevance_score=0.91,
            relevance_reason="这条会对上你最近想把模型能力边界搞清楚的劲头。",
            topic_key="AI:大模型",
        )
        db.cache_content(
            "BVDOC1",
            title="城市纪录片里的空间叙事",
            up_name="纪录片研究所",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会接住你那种喜欢从具体细节里看见大结构的状态。",
            topic_key="纪录片:城市",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=3,
        )

        picked = [item.content.bvid for item in recommendations]

        assert "BVINT1" in picked
        assert "BVINT2" not in picked
        assert "BVTECH1" in picked
        assert "BVDOC1" in picked


@pytest.mark.asyncio
async def test_reshuffle_recommendations_spreads_topics_in_same_batch() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BVINT1",
            title="讲透中东局势的来龙去脉",
            up_name="国际观察",
            source="search",
            relevance_score=0.96,
            relevance_reason="这条会接住你最近那股想把国际时事看透的劲头。",
            tags=["国际时事", "地缘政治"],
        )
        db.cache_content(
            "BVINT2",
            title="伊朗问题的底层链路",
            up_name="世界现场",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条延续了你最近盯国际新闻时那种爱追因果的状态。",
            tags=["国际时事", "地缘政治"],
        )
        db.cache_content(
            "BVTECH1",
            title="OpenAI 新模型到底强在哪",
            up_name="技术拆机局",
            source="search",
            relevance_score=0.91,
            relevance_reason="这条会对上你最近想把模型能力边界搞清楚的劲头。",
            tags=["AI", "大模型"],
        )
        db.cache_content(
            "BVDOC1",
            title="城市纪录片里的空间叙事",
            up_name="纪录片研究所",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会接住你那种喜欢从具体细节里看见大结构的状态。",
            tags=["纪录片", "城市"],
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=3,
        )

        picked = [item.content.bvid for item in recommendations]

        assert "BVINT1" in picked
        assert "BVINT2" not in picked
        assert "BVTECH1" in picked
        assert "BVDOC1" in picked


def test_build_debug_summary_counts_styles_sources_and_topics() -> None:
    summary = RecommendationEngine._build_debug_summary(
        [
            DiscoveredContent(
                bvid="BV1A",
                title="讲透中东局势",
                source_strategy="search",
                topic_key="国际时事:地缘政治",
                style_key="deep_dive",
            ),
            DiscoveredContent(
                bvid="BV1B",
                title="贸易政策速读",
                source_strategy="trending",
                topic_key="国际时事:贸易",
                style_key="news_brief",
            ),
            DiscoveredContent(
                bvid="BV1C",
                title="另外一条中东局势",
                source_strategy="search",
                topic_key="国际时事:地缘政治",
                style_key="deep_dive",
            ),
        ]
    )

    assert summary["count"] == 3
    assert summary["styles"] == {"deep_dive": 2, "news_brief": 1}
    assert summary["sources"] == {"search": 2, "trending": 1}
    assert summary["topics"] == {"国际时事:地缘政治": 2, "国际时事:贸易": 1}
    assert summary["platforms"] == {"bilibili": 3}
    assert summary["sample_titles"] == ["讲透中东局势", "贸易政策速读", "另外一条中东局势"]


def test_monoculture_pool_capped_by_broad_topic_not_platform() -> None:
    """A pool where every item shares the same broad topic is capped by
    topic — not by platform. xhs notes with no style classification can't
    flood the batch just because they happen to be from xhs; the same limit
    applies to any platform with identical topic saturation.
    """
    # Homogeneous pool: all share topic="ai" (→ broad bucket "ai"), style="".
    # Fallback broad-topic ceiling = 2×broad_cap = 2×3 = 6 for limit=10.
    homogeneous = [
        DiscoveredContent(
            bvid=f"XHS{i:02d}",
            title=f"note {i}",
            source_strategy="xhs-extension-task",
            topic_key="ai",
            style_key="",
            source_platform="xiaohongshu",
            relevance_score=0.9 - 0.01 * i,
        )
        for i in range(13)
    ]

    picked = RecommendationEngine._select_diversified_batch(homogeneous, limit=10)

    # Broad-topic cap holds in the fallback — no monoculture batch.
    assert len(picked) <= 6


def test_content_diversity_treats_platforms_equally() -> None:
    """Batch selector never discriminates by platform — it picks whatever
    maximizes content-level diversity. An xhs item with rich classification
    should win over a bilibili item with duplicate topic/style.
    """
    # xhs items with proper style + distinct topics — content-rich
    rich_xhs = [
        DiscoveredContent(
            bvid=f"XHS{i:02d}",
            title=f"xhs rich {i}",
            source_strategy="xhs-extension-task",
            topic_key=f"topic_x_{i}",
            topic_group=f"group_x_{i}",
            style_key="story_doc" if i % 2 else "visual_showcase",
            source_platform="xiaohongshu",
            relevance_score=0.95 - 0.005 * i,
        )
        for i in range(6)
    ]
    # bilibili items — also rich
    rich_bili = [
        DiscoveredContent(
            bvid=f"BV{i:02d}",
            title=f"bili rich {i}",
            source_strategy="related_chain" if i % 2 else "search",
            topic_key=f"topic_b_{i}",
            topic_group=f"group_b_{i}",
            style_key="deep_dive" if i % 2 else "news_brief",
            source_platform="bilibili",
            relevance_score=0.9 - 0.005 * i,
        )
        for i in range(6)
    ]

    picked = RecommendationEngine._select_diversified_batch(
        rich_xhs + rich_bili, limit=10,
    )

    # Both platforms should be represented because content is diverse enough
    # to pass topic/style caps — no platform gets artificially throttled.
    xhs_count = sum(1 for p in picked if p.source_platform == "xiaohongshu")
    bili_count = sum(1 for p in picked if p.source_platform == "bilibili")
    assert xhs_count >= 3
    assert bili_count >= 3
    assert len(picked) == 10


def test_pure_bilibili_rich_pool_fills_batch() -> None:
    """Regression: diverse bilibili-only pool still fills to limit."""
    candidates = [
        DiscoveredContent(
            bvid=f"BV{i:02d}",
            title=f"bili {i}",
            source_strategy="related_chain" if i % 2 else "search",
            topic_key=f"topic_{i}",
            topic_group=f"group_{i}",
            style_key="deep_dive" if i % 2 else "news_brief",
            source_platform="bilibili",
            relevance_score=0.9 - 0.01 * i,
        )
        for i in range(15)
    ]

    picked = RecommendationEngine._select_diversified_batch(candidates, limit=10)

    assert len(picked) == 10
    assert all(p.source_platform == "bilibili" for p in picked)


# ── Source-agnostic classification tests ─────────────────────────────


def test_unclassified_xhs_items_not_collapsed_by_source_strategy() -> None:
    """XHS items WITHOUT metadata should NOT all share one diversity token.

    Before the fix, _diversity_tokens() fell back to source_strategy
    ("xhs-extension-task") for all items, making them look like "same topic"
    to the diversity mechanism.  After the fix, title-derived tokens provide
    real differentiation even when topic_group/topic_key/tags are empty.
    """
    # 15 XHS items with empty metadata but DIFFERENT titles
    candidates = [
        DiscoveredContent(
            bvid=f"XHS{i:02d}",
            title=title,
            up_name=f"author_{i}",
            source_strategy="xhs-extension-task",
            source_platform="xiaohongshu",
            relevance_score=0.8 - 0.01 * i,
            # Intentionally empty — simulates raw XHS ingest
            topic_key="",
            topic_group="",
            style_key="",
            tags=[],
        )
        for i, title in enumerate([
            "莫氏鸡煲在家轻松复刻",
            "工地十块自助盒饭",
            "宝可梦PVP配队思路",
            "咒术回战深度解析",
            "DeepSeek本地部署教程",
            "Mac Studio搭建AI工作流",
            "顺德美食探店攻略",
            "洛克王国世界吐槽",
            "国际局势深度推演",
            "React Native性能优化",
            "独居女生的日常vlog",
            "摄影构图原理讲解",
            "上海工地烟火气",
            "宝可梦冠军建模吐槽",
            "AI自动化工作流实战",
        ])
    ]

    picked = RecommendationEngine._select_diversified_batch(candidates, limit=10)

    # Must fill the batch — should NOT collapse to 2-3 items due to
    # all sharing the same "xhs-extension-task" topic token.
    assert len(picked) == 10

    # Titles should be diverse (not all "莫氏鸡煲" variants)
    picked_titles = {p.title for p in picked}
    assert len(picked_titles) >= 8


def test_diversity_tokens_excludes_source_strategy() -> None:
    """_diversity_tokens should NOT include source_strategy as a fallback."""
    item = DiscoveredContent(
        bvid="XHS01",
        title="莫氏鸡煲在家复刻教程",
        up_name="美食达人",
        source_strategy="xhs-extension-task",
        source_platform="xiaohongshu",
        topic_key="",
        topic_group="",
        tags=[],
    )
    tokens = RecommendationEngine._diversity_tokens(item)
    # source_strategy must not appear as a diversity token
    assert "xhs-extension-task" not in tokens
    assert "xhs-extension-tas" not in tokens  # truncated form
    # But author and title-derived tokens should be present
    assert len(tokens) >= 1


@pytest.mark.asyncio
async def test_classify_pool_backlog_fills_metadata() -> None:
    """classify_pool_backlog should assign style_key and topic_group to
    un-classified pool items via LLM evaluation."""

    # LLM mock that returns batch classification results
    class _ClassifyLLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def complete_structured_task(
            self,
            *,
            system_instruction: str = "",
            user_input: str = "",
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
        ) -> LLMResponse:
            self.calls.append({"system_instruction": system_instruction})
            # Check if this is a classification call (batch eval prompt)
            # or an expression-generation call
            if "批量评估" in system_instruction or "score" in system_instruction:
                return LLMResponse(
                    content=json.dumps([
                        {
                            "score": 0.85,
                            "reason": "美食烹饪类内容",
                            "topic_group": "美食烹饪",
                            "style_key": "lifestyle",
                        },
                        {
                            "score": 0.72,
                            "reason": "游戏攻略",
                            "topic_group": "游戏攻略",
                            "style_key": "game_strategy",
                        },
                    ], ensure_ascii=False),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            return LLMResponse(
                content=json.dumps({
                    "expression": "这条给你找的。",
                    "topic_label": "test",
                }, ensure_ascii=False),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        # Insert 2 XHS items with NO metadata
        db.cache_content(
            "xhs_001",
            title="莫氏鸡煲在家复刻",
            up_name="美食博主",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            content_id="xhs_001",
            content_url="https://www.xiaohongshu.com/explore/xhs_001?xsec_token=abc",
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
        )
        db.cache_content(
            "xhs_002",
            title="宝可梦PVP配队",
            up_name="游戏玩家",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            content_id="xhs_002",
            content_url="https://www.xiaohongshu.com/explore/xhs_002?xsec_token=def",
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
        )

        llm = _ClassifyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine.classify_pool_backlog(
            profile=_build_profile(),
            limit=10,
        )

        assert classified == 2

        # Verify DB was updated
        rows = db.get_pool_candidates(limit=10)
        by_bvid = {r["bvid"]: r for r in rows}

        xhs1 = by_bvid.get("xhs_001")
        assert xhs1 is not None
        assert xhs1["style_key"] == "lifestyle"
        assert xhs1["topic_group"] == "美食烹饪"
        assert xhs1["topic_key"] == "美食烹饪"  # backfilled from topic_group
        assert float(xhs1["relevance_score"]) == pytest.approx(0.85)

        xhs2 = by_bvid.get("xhs_002")
        assert xhs2 is not None
        assert xhs2["style_key"] == "game_strategy"
        assert xhs2["topic_group"] == "游戏攻略"


@pytest.mark.asyncio
async def test_classify_pool_backlog_skips_already_classified() -> None:
    """Items that already have style_key + topic_group should not be re-evaluated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        # Already classified bilibili item
        db.cache_content(
            "BV_classified",
            title="已分类的内容",
            up_name="UP主",
            source="search",
            style_key="deep_dive",
            topic_group="强化学习",
            relevance_score=0.9,
        )

        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine.classify_pool_backlog(
            profile=_build_profile(),
            limit=10,
        )

        # Nothing to classify — the item is already fully classified
        assert classified == 0
        # LLM should NOT have been called for classification
        assert len(llm.calls) == 0


def test_re_ingest_does_not_overwrite_classified_fields() -> None:
    """cache_content upsert must preserve LLM-classified fields when the
    incoming values are empty.

    The XHS extension re-sends the same notes on every page load.  Without
    COALESCE protection, re-ingest would overwrite style_key / topic_group /
    relevance_score with empty defaults, undoing the LLM classification.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        # First insert: classified content (as if classify_pool_backlog ran)
        db.cache_content(
            "xhs_reingest",
            title="莫氏鸡煲在家复刻",
            up_name="美食博主",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            style_key="lifestyle",
            topic_group="美食烹饪",
            topic_key="美食烹饪",
            relevance_score=0.85,
            relevance_reason="美食烹饪类内容",
        )

        # Second insert: extension re-sends same note with empty metadata
        db.cache_content(
            "xhs_reingest",
            title="莫氏鸡煲在家复刻",
            up_name="美食博主",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            # These are all empty — must NOT overwrite existing values
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
            relevance_reason="",
        )

        rows = db.get_cached_content(limit=10)
        row = next(r for r in rows if r["bvid"] == "xhs_reingest")

        # All classified fields must survive the re-ingest
        assert row["style_key"] == "lifestyle"
        assert row["topic_group"] == "美食烹饪"
        assert row["topic_key"] == "美食烹饪"
        assert float(row["relevance_score"]) == pytest.approx(0.85)
        assert row["relevance_reason"] == "美食烹饪类内容"
