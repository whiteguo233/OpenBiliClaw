"""Tests for recommendation ranking engine."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.recommendation.engine import (
    RecommendationEngine,
    _recommendation_profile_summary,
)
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
        caller: str = "",
        reasoning_effort: str | None = None,
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


def test_recommendation_profile_summary_includes_disliked_topics() -> None:
    profile = _build_profile()
    profile.preferences.disliked_topics = [f"话题{i}" for i in range(1, 141)]

    summary = _recommendation_profile_summary(profile)

    # Capped at 128 == _DISLIKED_TOPICS_STORE_CAP, so every stored
    # avoid-topic reaches prompts; only an over-cap store (impossible in
    # practice) would be cut.
    assert summary["disliked_topics"] == [f"话题{i}" for i in range(1, 129)]


def _seed_pool(
    db: Database,
    items: list[DiscoveredContent],
    *,
    precomputed: bool = True,
) -> None:
    """Insert DiscoveredContent items into content_cache for pool-based tests.

    v0.3.57+: ``get_pool_candidates`` now requires ``pool_expression`` and
    ``pool_topic_label`` non-empty before returning a row. This helper
    fills both with placeholder strings by default so legacy tests stay
    green; pass ``precomputed=False`` to assert pool-gate behavior on
    incomplete rows.
    """
    for item in items:
        kwargs: dict[str, Any] = {
            "title": item.title,
            "up_name": item.up_name,
            "up_mid": item.up_mid,
            "duration": item.duration,
            "tags": item.tags,
            "topic_key": item.topic_key,
            "topic_group": item.topic_group,
            "style_key": item.style_key,
            "description": item.description,
            "cover_url": item.cover_url,
            "view_count": item.view_count,
            "like_count": item.like_count,
            "relevance_score": item.relevance_score,
            "relevance_reason": item.relevance_reason,
            "candidate_tier": item.candidate_tier,
            "source": item.source_strategy,
        }
        if precomputed:
            kwargs["pool_expression"] = item.pool_expression or "测试推荐文案"
            kwargs["pool_topic_label"] = item.pool_topic_label or "测试主题"
            kwargs["style_key"] = item.style_key or "tutorial"
            kwargs["topic_group"] = item.topic_group or "测试分组"
        # Use cache_content directly so precomputed=False genuinely leaves
        # pool copy empty (helpful for future gate-behavior tests).
        db.cache_content(item.bvid, **kwargs)


def _seed_visible(db: Database, bvid: str, **kwargs: Any) -> None:
    """v0.3.57+ shorthand: cache a content row visible to the pool gate.

    Equivalent to ``cache_content`` but ``pool_expression`` /
    ``pool_topic_label`` default to non-empty placeholders so the row
    passes ``get_pool_candidates``'s precompute gate. Tests that
    explicitly assert the gate hides empty rows must use ``cache_content``
    directly.
    """
    kwargs.setdefault("pool_expression", "测试推荐文案")
    kwargs.setdefault("pool_topic_label", "测试主题")
    kwargs.setdefault("style_key", "tutorial")
    kwargs.setdefault("topic_group", "测试分组")
    db.cache_content(bvid, **kwargs)


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
        item.style_key
        in {
            "story_doc",
            "review_roundup",
            "lifestyle",
            "light_chat",
            "fun_variety",
            "visual_showcase",
        }
        for item in batch
    )


def test_select_diversified_batch_caps_newly_confirmed_amplification_direction() -> None:
    def item(bvid: str, *, topic_group: str) -> DiscoveredContent:
        return DiscoveredContent(
            bvid=bvid,
            title=bvid,
            topic_group=topic_group,
            style_key="tutorial",
            relevance_score=0.9,
        )

    items = [
        item("A1", topic_group="城市基础设施观察"),
        item("A2", topic_group="城市基础设施观察"),
        item("A3", topic_group="城市基础设施观察"),
        item("B1", topic_group="游戏推荐"),
        item("C1", topic_group="手工木工"),
    ]

    selected = RecommendationEngine._select_diversified_batch(
        items,
        limit=4,
        amplification_guard={"城市基础设施观察"},
    )

    assert sum(i.topic_group == "城市基础设施观察" for i in selected) <= 1


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

        _seed_pool(
            db,
            [
                DiscoveredContent(bvid="BV1A", title="A", relevance_score=0.71),
                DiscoveredContent(bvid="BV1B", title="B", relevance_score=0.92),
                DiscoveredContent(bvid="BV1C", title="C", relevance_score=0.83),
            ],
        )

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
        _seed_visible(
            db,
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        _seed_visible(
            db,
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
async def test_serve_zero_candidates_warning_includes_readiness_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1READY",
            title="已经在池子里",
            source="search",
            relevance_score=0.9,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            caplog.set_level(logging.WARNING)
            result = await engine.serve(
                _build_profile(),
                limit=1,
                excluded_bvids=frozenset({"BV1READY"}),
            )

            assert result == []
            assert "raw=1" in caplog.text
            assert "servable=1" in caplog.text
            assert "pending=0" in caplog.text
        finally:
            db.close()


@pytest.mark.asyncio
async def test_serve_empty_after_exclusions_skips_curator_context() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1READY",
            title="宸茬粡鍦ㄦ睜瀛愰噷",
            source="search",
            relevance_score=0.9,
        )

        class ExplodingCurator:
            def build_context(self) -> object:
                raise AssertionError("empty candidate batch should return before curator")

            def score_candidates(self, candidates: object, context: object) -> dict[str, float]:
                raise AssertionError("empty candidate batch should return before scoring")

        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            curator=ExplodingCurator(),  # type: ignore[arg-type]
        )

        try:
            result = await engine.serve(
                _build_profile(),
                limit=1,
                excluded_bvids=frozenset({"BV1READY"}),
            )
        finally:
            db.close()

        assert result == []


@pytest.mark.asyncio
async def test_generate_recommendations_prefers_primary_then_relevance_then_recency() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        _seed_pool(
            db,
            [
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
            ],
        )

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
        _seed_visible(
            db,
            "BV1LOW",
            title="低相关高播放",
            up_name="UPA",
            source="search",
            view_count=1000,
            relevance_score=0.41,
            candidate_tier="primary",
        )
        _seed_visible(
            db,
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

        _seed_pool(
            db,
            [
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
            ],
        )

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=10,
        )

        picked_groups = [item.content.topic_group for item in recommendations]
        # With strict broad_cap, no single topic_group should dominate
        assert picked_groups.count("强化学习") <= 3


@pytest.mark.asyncio
async def test_generate_recommendations_balances_topics_from_cache() -> None:
    """Source-agnostic content balance: when one source dominates the
    relevance head with many duplicate topics, the candidate window still
    spreads across distinct topic_groups so the picked batch isn't a
    single-topic flood.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        # Dominant source + dominant topic at the relevance head
        for index in range(25):
            _seed_visible(
                db,
                f"BVAI{index}",
                title=f"AI 高分候选 {index}",
                up_name="AI 频道",
                source="related_chain",
                relevance_score=0.99 - index * 0.001,
                relevance_reason="ai high score",
                style_key="practical_guide",
                topic_key=f"ai:variant:{index}",
                topic_group="人工智能",
            )
        # Long tail: lower scores but distinct topic groups
        for index in range(5):
            _seed_visible(
                db,
                f"BVGAME{index}",
                title=f"游戏候选 {index}",
                up_name="游戏频道",
                source="trending",
                relevance_score=0.89 - index * 0.001,
                relevance_reason="game candidate",
                style_key="game_strategy",
                topic_key=f"game:{index}",
                topic_group="游戏",
            )
        for index in range(5):
            _seed_visible(
                db,
                f"BVDOC{index}",
                title=f"纪录片候选 {index}",
                up_name="纪录片频道",
                source="search",
                relevance_score=0.88 - index * 0.001,
                relevance_reason="doc candidate",
                style_key="story_doc",
                topic_key=f"doc:{index}",
                topic_group="纪录片",
            )
        for index in range(5):
            _seed_visible(
                db,
                f"BVHIST{index}",
                title=f"历史候选 {index}",
                up_name="历史频道",
                source="explore",
                relevance_score=0.87 - index * 0.001,
                relevance_reason="history candidate",
                style_key="deep_dive",
                topic_key=f"hist:{index}",
                topic_group="人文历史",
            )

        recommendations = await engine.generate_recommendations(
            discovered=None,
            profile=_build_profile(),
            limit=10,
        )

        picked_groups = [item.content.topic_group for item in recommendations]

        # AI cannot dominate the batch even though it owns the relevance head
        assert picked_groups.count("人工智能") <= 3
        # Tail topics still surface
        assert "游戏" in picked_groups
        assert "纪录片" in picked_groups or "人文历史" in picked_groups


@pytest.mark.asyncio
async def test_generate_recommendations_does_not_repeat_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1A",
            title="A",
            up_name="UPA",
            source="search",
            view_count=10,
        )
        _seed_visible(
            db,
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
        _seed_visible(
            db,
            "BV1SEEN",
            title="已经看过的内容",
            up_name="UPA",
            source="search",
            relevance_score=0.97,
        )
        _seed_visible(
            db,
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

        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid="BV1EXP",
                    title="讲透摄影构图的底层逻辑",
                    up_name="构图实验室",
                    description="从原理出发解释构图。",
                    relevance_score=0.91,
                ),
            ],
        )

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

        # v0.3.28+: 老B友 moved from system_instruction to user_input
        # (tone block) so the system prefix stays cache-stable across
        # users with different platform mixes.
        assert "老B友" in str(llm.calls[0]["user_input"])


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
async def test_generate_expression_passes_body_text_for_text_items() -> None:
    """Task 11: text-first X items have low-information titles, so the
    expression builder must see ``body_text`` in its USER message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="1790000000000000001",
                content_id="1790000000000000001",
                title="1/ on resilient systems",
                up_name="@handle",
                source_platform="twitter",
                content_type="thread",
                cover_url="",
                duration=0,
                body_text="1/ BODY_MARKER long-form note_tweet on systems design ...",
                style_key="deep_dive",
                topic_group="系统设计",
                relevance_score=0.8,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert "BODY_MARKER" in user_input
        assert '"body_text"' in user_input
        # body_text must never leak into the cached system prompt.
        assert "BODY_MARKER" not in str(llm.calls[0]["system_instruction"])


@pytest.mark.asyncio
async def test_select_diversified_batch_tolerates_text_items_without_cover() -> None:
    """Task 11: ranking / diversity / MMR must not assume a cover_url or a
    non-zero duration. Text-first X items (empty cover, duration 0) must
    rank without raising or dropping out."""
    text_items = [
        DiscoveredContent(
            bvid=f"179000000000000000{i}",
            content_id=f"179000000000000000{i}",
            title=f"{i}/ thread on systems",
            up_name="@handle",
            source_platform="twitter",
            content_type="thread",
            cover_url="",
            duration=0,
            body_text=f"{i}/ long-form body about topic {i}",
            topic_group=f"主题{i}",
            relevance_score=0.9 - i * 0.05,
        )
        for i in range(5)
    ]

    selected = RecommendationEngine._select_diversified_batch(text_items, limit=3)

    assert len(selected) == 3
    assert all(item.source_platform == "twitter" for item in selected)


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
        _seed_visible(
            db,
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
        db.close()


@pytest.mark.asyncio
async def test_append_recommendations_skips_excluded_bvids() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1A",
            title="第一条",
            up_name="UPA",
            source="search",
            relevance_score=0.95,
            relevance_reason="第一条基础理由。",
        )
        _seed_visible(
            db,
            "BV1B",
            title="第二条",
            up_name="UPB",
            source="trending",
            relevance_score=0.94,
            relevance_reason="第二条基础理由。",
        )
        _seed_visible(
            db,
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
        db.close()


@pytest.mark.asyncio
async def test_serve_returns_immediately_when_pool_available_count_is_zero() -> None:
    class EmptyPoolDatabase:
        def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]:
            return {"available": 0, "raw": 0, "pending": 0}

        def get_pool_candidates(
            self, *, limit: int, xhs_self_nickname: str = ""
        ) -> list[dict[str, object]]:
            raise AssertionError("empty available pool should not load candidates")

        def batch_insert_recommendations(self, rows: list[dict[str, object]]) -> list[int]:
            raise AssertionError("empty available pool should not write recommendations")

    engine = RecommendationEngine(llm=_DummyLLM(), database=EmptyPoolDatabase())  # type: ignore[arg-type]

    recommendations = await engine.serve(_build_profile(), limit=5)

    assert recommendations == []


@pytest.mark.asyncio
async def test_append_returns_immediately_when_exclusions_empty_candidates() -> None:
    class ExplodingCurator:
        def build_context(self) -> object:
            raise AssertionError("empty candidate list should not call curator")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1ONLY",
            title="唯一候选",
            up_name="UPA",
            source="search",
            relevance_score=0.95,
            relevance_reason="唯一候选理由。",
        )
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            curator=ExplodingCurator(),  # type: ignore[arg-type]
        )

        recommendations = await engine.append_recommendations(
            profile=_build_profile(),
            excluded_bvids=["BV1ONLY"],
            limit=5,
        )

        assert recommendations == []
        assert db.get_recommendations(limit=10) == []


@pytest.mark.asyncio
async def test_reshuffle_recommendations_hides_missing_precomputed_copy() -> None:
    """v0.3.57+: rows without pool_expression/pool_topic_label are hidden by
    the pool gate; reshuffle should return zero recommendations rather than
    falling back to a placeholder template."""

    class _ExplodingLLM(_DummyLLM):
        async def complete_structured_task(self, **kwargs) -> LLMResponse:  # type: ignore[override]
            raise RuntimeError("expression generation should not run in reshuffle path")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        # v0.3.57 gate test: must use cache_content directly so pool
        # copy stays empty and the gate hides the row.
        db.cache_content(
            "BV1EMPTY",
            title="还没生成推荐文案",
            up_name="观察站",
            source="search",
            relevance_score=0.89,
            relevance_reason="这条会对上你最近那股想把来龙去脉搞明白的劲头。",
            style_key="deep_dive",
            topic_group="测试分组",
        )
        engine = RecommendationEngine(llm=_ExplodingLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )

        # Pool gate hides the row entirely — no fallback fires.
        assert recommendations == []

        # Once precompute fills the copy, the row becomes visible.
        db.update_pool_copy("BV1EMPTY", expression="LLM 文案", topic_label="LLM topic")
        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=1,
        )
        assert len(recommendations) == 1
        assert recommendations[0].expression == "LLM 文案"
        assert recommendations[0].topic_label == "LLM topic"

        history = db.get_recommendations(limit=10)
        assert history[0]["expression"] == "LLM 文案"
        assert history[0]["topic"] == "LLM topic"


@pytest.mark.asyncio
async def test_reshuffle_recommendations_skips_recently_viewed_content() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1SEEN",
            title="已经看过的地缘政治分析",
            up_name="观察站",
            source="search",
            relevance_score=0.93,
            relevance_reason="这条本来很像你会点开的内容。",
        )
        _seed_visible(
            db,
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
        _seed_visible(
            db,
            "BVGAME1",
            title="杀戮尖塔2 全英雄基础流派攻略",
            up_name="卡牌研究所",
            source="related_chain",
            relevance_score=0.96,
            relevance_reason="这条偏你会点开的机制拆解。",
            style_key="game_strategy",
            topic_key="游戏:杀戮尖塔2",
            topic_group="游戏",
        )
        _seed_visible(
            db,
            "BVGAME2",
            title="杀戮尖塔2 17分钟实机演示",
            up_name="IGN",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条还是同一类游戏机制内容。",
            style_key="game_strategy",
            topic_key="游戏:杀戮尖塔2",
            topic_group="游戏",
        )
        _seed_visible(
            db,
            "BVNEWS1",
            title="美国关税政策又有新变化",
            up_name="国际观察",
            source="trending",
            relevance_score=0.91,
            relevance_reason="这条信息来得快，而且不是纯复读。",
            style_key="news_brief",
            topic_key="国际时事:贸易",
            topic_group="国际时事",
        )
        _seed_visible(
            db,
            "BVDOC1",
            title="塔可夫斯基《潜行者》到底讲了什么",
            up_name="猫鲨Catshark",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会把故事和信息一起带出来。",
            style_key="story_doc",
            topic_key="科幻:电影",
            topic_group="科幻",
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
async def test_reshuffle_recommendations_caps_topic_and_style_for_larger_batches() -> None:
    """Larger batches enforce per-topic and per-style caps regardless of source.

    A batch should not collapse into a single broad topic or a single style
    just because the relevance head happens to be source-homogeneous.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        # Topic head is "游戏" (4 items, mixed styles) — broad_cap (3) must
        # bind. Style "game_strategy" appears 3× — style_cap (3) is at edge.
        items = [
            ("BVGAME1", "游戏攻略 1", "explore", 0.99, "story_doc", "游戏:1", "游戏"),
            ("BVGAME2", "游戏深挖 2", "explore", 0.98, "deep_dive", "游戏:2", "游戏"),
            ("BVGAME3", "游戏轻聊 3", "explore", 0.97, "light_chat", "游戏:3", "游戏"),
            ("BVGAME4", "游戏拆解 4", "explore", 0.96, "practical_guide", "游戏:4", "游戏"),
            ("BVAI1", "AI 拆解 1", "related_chain", 0.95, "game_strategy", "ai:1", "人工智能"),
            ("BVAI2", "AI 拆解 2", "related_chain", 0.94, "game_strategy", "ai:2", "人工智能"),
            ("BVAI3", "AI 故事向 3", "related_chain", 0.935, "light_chat", "ai:3", "人工智能"),
            ("BVDOC1", "纪录片教程 1", "search", 0.93, "practical_guide", "doc:1", "纪录片"),
            ("BVNEWS1", "时事快讯 1", "search", 0.92, "news_brief", "news:1", "时事"),
            ("BVHIST1", "历史纪录 1", "trending", 0.91, "story_doc", "hist:1", "人文历史"),
            ("BVMUSIC1", "音乐视觉 1", "trending", 0.9, "visual_showcase", "music:1", "音乐"),
        ]
        for bvid, title, source, score, style, topic, group in items:
            _seed_visible(
                db,
                bvid,
                title=title,
                up_name=f"{source}-频道",
                source=source,
                relevance_score=score,
                relevance_reason=f"{title} 的基础理由。",
                style_key=style,
                topic_key=topic,
                topic_group=group,
            )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.reshuffle_recommendations(
            profile=_build_profile(),
            limit=10,
        )

        picked_groups = [item.content.topic_group for item in recommendations]
        picked_styles = [item.content.style_key for item in recommendations]

        assert len(recommendations) == 10
        assert picked_groups.count("游戏") <= 3
        assert picked_groups.count("人工智能") <= 3
        assert picked_styles.count("game_strategy") <= 3


@pytest.mark.asyncio
async def test_reshuffle_recommendations_backfills_to_requested_limit_when_style_is_dominant() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        # Style-dominant but topic-diverse pool: all light_chat, but each item
        # has a distinct broad topic so backfill can reach `limit`.
        topics = ["生活随笔", "职场闲谈", "读书片段", "城市漫步", "餐桌小记", "音乐碎片"]
        for index, topic in enumerate(topics):
            _seed_visible(
                db,
                f"BVLIGHT{index + 1}",
                title=f"轻聊候选 {index + 1}",
                up_name="轻聊频道",
                source="search",
                relevance_score=0.96 - index * 0.01,
                relevance_reason=f"这条会接住你最近想往里看一点的状态 {index + 1}。",
                style_key="light_chat",
                topic_key=topic,
                topic_group=topic,
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
    """v0.3.57+: even when style_key is set, missing pool_expression/topic_label
    keeps the row out of the pool. The old behavior — falling back to a
    style-keyed template — is no longer acceptable."""

    class _ExplodingLLM(_DummyLLM):
        async def complete_structured_task(self, **kwargs) -> LLMResponse:  # type: ignore[override]
            raise RuntimeError("expression generation should not run in reshuffle path")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        # v0.3.57 gate test: cache_content directly, pool copy stays empty.
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

        # Pool gate hides the row regardless of style_key richness.
        assert recommendations == []


@pytest.mark.asyncio
async def test_reshuffle_recommendations_spreads_topic_keys_before_backfill() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BVINT1",
            title="讲透中东局势的来龙去脉",
            up_name="国际观察",
            source="search",
            relevance_score=0.96,
            relevance_reason="这条会接住你最近那股想把国际时事看透的劲头。",
            topic_key="国际时事:地缘政治",
            topic_group="国际时事",
        )
        _seed_visible(
            db,
            "BVINT2",
            title="伊朗问题的底层链路",
            up_name="世界现场",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条延续了你最近盯国际新闻时那种爱追因果的状态。",
            topic_key="国际时事:地缘政治",
            topic_group="国际时事",
        )
        _seed_visible(
            db,
            "BVTECH1",
            title="OpenAI 新模型到底强在哪",
            up_name="技术拆机局",
            source="search",
            relevance_score=0.91,
            relevance_reason="这条会对上你最近想把模型能力边界搞清楚的劲头。",
            topic_key="AI:大模型",
            topic_group="人工智能",
        )
        _seed_visible(
            db,
            "BVDOC1",
            title="城市纪录片里的空间叙事",
            up_name="纪录片研究所",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会接住你那种喜欢从具体细节里看见大结构的状态。",
            topic_key="纪录片:城市",
            topic_group="纪录片",
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
        _seed_visible(
            db,
            "BVINT1",
            title="讲透中东局势的来龙去脉",
            up_name="国际观察",
            source="search",
            relevance_score=0.96,
            relevance_reason="这条会接住你最近那股想把国际时事看透的劲头。",
            tags=["国际时事", "地缘政治"],
            topic_group="国际时事",
        )
        _seed_visible(
            db,
            "BVINT2",
            title="伊朗问题的底层链路",
            up_name="世界现场",
            source="related_chain",
            relevance_score=0.95,
            relevance_reason="这条延续了你最近盯国际新闻时那种爱追因果的状态。",
            tags=["国际时事", "地缘政治"],
            topic_group="国际时事",
        )
        _seed_visible(
            db,
            "BVTECH1",
            title="OpenAI 新模型到底强在哪",
            up_name="技术拆机局",
            source="search",
            relevance_score=0.91,
            relevance_reason="这条会对上你最近想把模型能力边界搞清楚的劲头。",
            tags=["AI", "大模型"],
            topic_group="人工智能",
        )
        _seed_visible(
            db,
            "BVDOC1",
            title="城市纪录片里的空间叙事",
            up_name="纪录片研究所",
            source="explore",
            relevance_score=0.9,
            relevance_reason="这条会接住你那种喜欢从具体细节里看见大结构的状态。",
            tags=["纪录片", "城市"],
            topic_group="纪录片",
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
        rich_xhs + rich_bili,
        limit=10,
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
        for i, title in enumerate(
            [
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
            ]
        )
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
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            self.calls.append({"system_instruction": system_instruction})
            # Check if this is a classification call (batch eval prompt)
            # or an expression-generation call
            if "批量评估" in system_instruction or "score" in system_instruction:
                return LLMResponse(
                    content=json.dumps(
                        [
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
                        ],
                        ensure_ascii=False,
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "expression": "这条给你找的。",
                        "topic_label": "test",
                    },
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        # Insert 2 XHS items with NO metadata
        _seed_visible(
            db,
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
        _seed_visible(
            db,
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
        _seed_visible(
            db,
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


@pytest.mark.asyncio
async def test_safe_classify_pool_backlog_drains_copy_for_newly_classified() -> None:
    """Lever 2b: classify's detached wrapper drains copy for the items it just
    labeled, so they become serveable in the SAME cycle instead of waiting for
    the next refresh-loop precompute tick.

    Before 2b, a freshly-classified item would sit with empty
    ``pool_expression`` (gated out of ``count_pool_candidates``) until the
    next precompute pass; now classify→copy chains in one call.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        # Unclassified + un-copied → needs classify, then needs copy.
        db.cache_content(
            "BV2b",
            title="在家复刻的家常菜",
            up_name="美食博主",
            source="search",
            style_key="",
            topic_group="",
            topic_key="",
            pool_expression="",
            pool_topic_label="",
            relevance_score=0.0,
        )
        assert db.count_pool_candidates() == 0

        class _ClassifyThenCopyLLM:
            def __init__(self) -> None:
                self.callers: list[str] = []

            async def complete_structured_task(
                self, *, caller: str = "", **_kw: object
            ) -> LLMResponse:
                self.callers.append(caller)
                if caller == "recommendation.write_expression":
                    content = json.dumps(
                        [
                            {
                                "bvid": "BV2b",
                                "expression": "这条接住你想下厨的心情。",
                                "topic_label": "你最近想做饭",
                            }
                        ],
                        ensure_ascii=False,
                    )
                else:  # classify → recommendation.evaluate_batch
                    content = json.dumps(
                        [
                            {
                                "score": 0.85,
                                "reason": "美食烹饪",
                                "topic_group": "美食烹饪",
                                "style_key": "lifestyle",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return LLMResponse(content=content, provider="test", model="dummy", usage={})

        llm = _ClassifyThenCopyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine._safe_classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 1
        # Both stages ran in this one call: classify, then the 2b copy drain.
        assert "recommendation.evaluate_batch" in llm.callers
        assert "recommendation.write_expression" in llm.callers
        # The item is now classified AND copied → serveable.
        assert db.count_pool_candidates() == 1


@pytest.mark.asyncio
async def test_e2e_precompute_pool_copy_classifies_then_copies_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E (lever 2b): driving the *production* entry ``precompute_pool_copy``
    on an UNCLASSIFIED candidate makes it fully serveable in one pass.

    precompute_pool_copy spawns classify detached; its own copy drain finds
    nothing yet (item not classified). Before 2b the item would stay
    classified-but-uncopied until the next tick. With 2b, the detached
    classify drains copy for what it just labeled, so after the detached
    chain settles the item is classified AND copied → count 0 → 1. Only the
    LLM is faked.
    """
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV2bE2E",
            title="在家复刻的家常菜",
            up_name="美食博主",
            source="search",
            style_key="",
            topic_group="",
            topic_key="",
            pool_expression="",
            pool_topic_label="",
            relevance_score=0.0,
        )
        assert db.count_pool_candidates() == 0

        class _ClassifyThenCopyLLM:
            def __init__(self) -> None:
                self.callers: list[str] = []

            async def complete_structured_task(
                self, *, caller: str = "", **_kw: object
            ) -> LLMResponse:
                self.callers.append(caller)
                if caller == "recommendation.write_expression":
                    content = json.dumps(
                        [
                            {
                                "bvid": "BV2bE2E",
                                "expression": "这条接住你想下厨的心情。",
                                "topic_label": "你最近想做饭",
                            }
                        ],
                        ensure_ascii=False,
                    )
                else:  # classify (evaluate_batch) + any delight call (harmless)
                    content = json.dumps(
                        [
                            {
                                "score": 0.85,
                                "reason": "美食烹饪",
                                "topic_group": "美食烹饪",
                                "style_key": "lifestyle",
                            }
                        ],
                        ensure_ascii=False,
                    )
                return LLMResponse(content=content, provider="test", model="dummy", usage={})

        llm = _ClassifyThenCopyLLM()
        registry = BackgroundTaskRegistry()
        engine = RecommendationEngine(llm=llm, database=db, task_registry=registry)

        # Capture the detached classify task so we can await its full chain.
        captured: dict[str, asyncio.Task[object]] = {}
        original_track = registry.track

        def _track(name: str, coro: object) -> object:
            task = original_track(name, coro)  # type: ignore[arg-type]
            captured[name] = task
            return task

        registry.track = _track  # type: ignore[method-assign]

        try:
            # Production entry point (what the refresh loop / hot-reload call).
            await engine.precompute_pool_copy(profile=_build_profile(), limit=10)
            # Its own copy drain found nothing (item not classified yet).
            assert db.count_pool_candidates() == 0
            # Await the detached classify → which (2b) drains copy for it.
            await asyncio.wait_for(captured["classify_pool_backlog_detached"], timeout=2.0)

            assert "recommendation.evaluate_batch" in llm.callers
            assert "recommendation.write_expression" in llm.callers
            assert db.count_pool_candidates() == 1  # classified AND copied
        finally:
            await registry.cancel_all()


@pytest.mark.asyncio
async def test_prewarm_pool_mmr_embeddings_signals_distinguish_states() -> None:
    """Lever 4: prewarm's return distinguishes a benign cold start (-1) from a
    genuinely-unreachable embedding backend (0), so operators can tell
    "pool empty / embeddings off" apart from "Ollama is down" — the original
    `warmed=0` ambiguity.
    """

    class _FakeEmbedding:
        def __init__(self, *, working: bool) -> None:
            self.working = working
            self.similarity_threshold = 0.82

        async def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3] if self.working else []

        def lookup_cached(self, _text: str) -> list[float] | None:
            return None

    # (1) No embedding service configured → -1 (benign: embeddings disabled).
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "a.db")
        db.initialize()
        _seed_visible(db, "BVa", title="测试视频", up_name="UP", source="search")
        engine = RecommendationEngine(llm=_DummyLLM(), database=db, embedding_service=None)
        assert await engine.prewarm_pool_mmr_embeddings() == -1

    # (2) Working backend but EMPTY pool → -1 (benign cold start, nothing to warm).
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "b.db")
        db.initialize()
        engine = RecommendationEngine(
            llm=_DummyLLM(), database=db, embedding_service=_FakeEmbedding(working=True)
        )
        assert await engine.prewarm_pool_mmr_embeddings() == -1

    # (3) Candidates present but backend down (embed returns []) → 0 (real failure).
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "c.db")
        db.initialize()
        _seed_visible(db, "BVc", title="测试视频", up_name="UP", source="search")
        engine = RecommendationEngine(
            llm=_DummyLLM(), database=db, embedding_service=_FakeEmbedding(working=False)
        )
        assert await engine.prewarm_pool_mmr_embeddings() == 0

    # (4) Candidates present + working backend → warmed count > 0.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "d.db")
        db.initialize()
        _seed_visible(db, "BVd", title="测试视频", up_name="UP", source="search")
        engine = RecommendationEngine(
            llm=_DummyLLM(), database=db, embedding_service=_FakeEmbedding(working=True)
        )
        assert await engine.prewarm_pool_mmr_embeddings() > 0


@pytest.mark.asyncio
async def test_classify_pool_backlog_accepts_jsonl_output(caplog: pytest.LogCaptureFixture) -> None:
    class _JsonlClassifyLLM:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str = "",
            user_input: str = "",
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                content="\n".join(
                    [
                        json.dumps(
                            {
                                "score": 0.84,
                                "reason": "慢速观察类生活内容",
                                "topic_group": "生活观察",
                                "style_key": "lifestyle",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "score": 0.76,
                                "reason": "偏系统分析的深度内容",
                                "topic_group": "系统分析",
                                "style_key": "deep_dive",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "xhs_jsonl_001",
            title="城市通勤里的小变化",
            up_name="街角观察",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
        )
        _seed_visible(
            db,
            "xhs_jsonl_002",
            title="怎样拆一个复杂系统",
            up_name="系统笔记",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
        )
        engine = RecommendationEngine(llm=_JsonlClassifyLLM(), database=db)

        caplog.set_level(logging.WARNING)
        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 2
        rows = {row["bvid"]: row for row in db.get_cached_content(limit=10)}
        assert rows["xhs_jsonl_001"]["style_key"] == "lifestyle"
        assert rows["xhs_jsonl_002"]["style_key"] == "deep_dive"
        assert "classify_pool_backlog: batch failed" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper_key", ["results", "items"])
async def test_classify_pool_backlog_accepts_wrapped_output(
    wrapper_key: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _WrappedClassifyLLM:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str = "",
            user_input: str = "",
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
            reasoning_effort: str | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        wrapper_key: [
                            {
                                "score": 0.81,
                                "reason": "结构化评测",
                                "topic_group": "工具效率",
                                "style_key": "tech_analysis",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            f"xhs_wrapped_{wrapper_key}",
            title="剪辑工具的自动化流程",
            up_name="效率实验室",
            source="xhs-extension-task",
            source_platform="xiaohongshu",
            style_key="",
            topic_group="",
            topic_key="",
            relevance_score=0.0,
        )
        engine = RecommendationEngine(llm=_WrappedClassifyLLM(), database=db)

        caplog.set_level(logging.WARNING)
        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 1
        row = next(
            r for r in db.get_cached_content(limit=10) if r["bvid"] == f"xhs_wrapped_{wrapper_key}"
        )
        assert row["style_key"] == "tech_analysis"
        assert row["topic_group"] == "工具效率"
        assert "classify_pool_backlog: batch failed" not in caplog.text


@pytest.mark.asyncio
async def test_precompute_batch_accepts_items_wrapper_without_single_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _WrappedExpressionLLM:
        def __init__(self) -> None:
            self.callers: list[str] = []

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
        ) -> LLMResponse:
            self.callers.append(caller)
            return LLMResponse(
                content=json.dumps(
                    {
                        "items": [
                            {
                                "expression": "这条能把你最近想拆流程的劲头接住。",
                                "topic_label": "流程拆解",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        item = DiscoveredContent(
            bvid="BV_EXPR_WRAP",
            title="自动化流程怎么拆",
            up_name="效率实验室",
            description="把一个复杂流程拆成可执行的小步骤。",
            relevance_score=0.88,
        )
        _seed_pool(db, [item], precomputed=False)
        llm = _WrappedExpressionLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        caplog.set_level(logging.WARNING)
        completed = await engine._precompute_batch([item], _build_profile())

        assert completed == 1
        row = next(r for r in db.get_cached_content(limit=10) if r["bvid"] == "BV_EXPR_WRAP")
        assert row["pool_expression"] == "这条能把你最近想拆流程的劲头接住。"
        assert row["pool_topic_label"] == "流程拆解"
        assert llm.callers == ["recommendation.write_expression"]
        assert "Batch expression generation failed" not in caplog.text


@pytest.mark.asyncio
async def test_precompute_batch_skips_single_fallback_during_provider_cooldown() -> None:
    class _CooldownExpressionLLM:
        def __init__(self) -> None:
            self.calls = 0

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
        ) -> LLMResponse:
            self.calls += 1
            raise LLMProviderExecutionError(
                "All providers failed (gemini). Last error: "
                "Provider gemini is cooling down after rate limit."
            )

    items = [
        DiscoveredContent(bvid="BV_COOL_EXPR_A", title="A", relevance_score=0.8),
        DiscoveredContent(bvid="BV_COOL_EXPR_B", title="B", relevance_score=0.7),
    ]
    llm = _CooldownExpressionLLM()

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._precompute_batch(items, _build_profile())

    assert completed == 0
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_precompute_batch_matches_expressions_by_bvid_when_response_reorders() -> None:
    class _ReorderedExpressionLLM:
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
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV_EXPR_C",
                            "expression": "C 视频自己的推荐文案。",
                            "topic_label": "C 主题",
                        },
                        {
                            "bvid": "BV_EXPR_B",
                            "expression": "B 视频自己的推荐文案。",
                            "topic_label": "B 主题",
                        },
                    ],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        items = [
            DiscoveredContent(bvid="BV_EXPR_A", title="A 视频"),
            DiscoveredContent(bvid="BV_EXPR_B", title="B 视频"),
            DiscoveredContent(bvid="BV_EXPR_C", title="C 视频"),
        ]
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=_ReorderedExpressionLLM(), database=db)

        completed = await engine._precompute_batch(items, _build_profile())

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        assert completed == 2
        assert rows["BV_EXPR_A"]["pool_expression"] == ""
        assert rows["BV_EXPR_B"]["pool_expression"] == "B 视频自己的推荐文案。"
        assert rows["BV_EXPR_C"]["pool_expression"] == "C 视频自己的推荐文案。"


@pytest.mark.asyncio
async def test_precompute_batch_falls_back_to_single_when_multi_item_response_lacks_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multi-item batch with no bvid/content_id must not be matched positionally.

    Positional matching silently attaches the wrong (or an identical) reason
    to each video on weak models. The engine should regenerate per item,
    where each single call carries one content and cannot be misaligned.
    """

    class _NoIdBatchLLM:
        def __init__(self) -> None:
            self.callers: list[str] = []

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
        ) -> LLMResponse:
            self.callers.append(caller)
            if caller == "recommendation.write_expression":
                # Batch reply: two entries, NO bvid/content_id at all.
                content = json.dumps(
                    [
                        {"expression": "第一条文案。", "topic_label": "主题一"},
                        {"expression": "第二条文案。", "topic_label": "主题二"},
                    ],
                    ensure_ascii=False,
                )
            else:
                # Per-item single regeneration carries its own bvid context.
                content = json.dumps(
                    {"expression": f"单条重写（{len(self.callers)}）。", "topic_label": "重写主题"},
                    ensure_ascii=False,
                )
            return LLMResponse(content=content, provider="test", model="dummy", usage={})

    items = [
        DiscoveredContent(bvid="BV_NOID_A", title="A 视频", relevance_score=0.9),
        DiscoveredContent(bvid="BV_NOID_B", title="B 视频", relevance_score=0.8),
    ]
    llm = _NoIdBatchLLM()

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=llm, database=db)

        caplog.set_level(logging.WARNING)
        completed = await engine._precompute_batch(items, _build_profile())

        # One batch attempt, then one single regeneration per item.
        assert llm.callers == [
            "recommendation.write_expression",
            "recommendation.expression",
            "recommendation.expression",
        ]
        assert completed == 2
        assert "positional matching is unreliable" in caplog.text
        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        # Each row got copy via the keyed single path — never positional batch.
        assert rows["BV_NOID_A"]["pool_expression"].startswith("单条重写")
        assert rows["BV_NOID_B"]["pool_expression"].startswith("单条重写")


@pytest.mark.asyncio
async def test_precompute_batch_drops_expression_shared_across_distinct_videos(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An identical expression mapped to two different videos must be dropped.

    A weak model that repeats one sentence for every item is the root of the
    "every recommendation reason is the same" symptom. Writing it for several
    videos is worse than writing nothing, so the duplicated copy is rejected
    while genuinely-unique copy still lands.
    """

    class _DuplicateExpressionLLM:
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
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {"bvid": "BV_DUP_A", "expression": "一模一样的文案。", "topic_label": "t"},
                        {"bvid": "BV_DUP_B", "expression": "一模一样的文案。", "topic_label": "t"},
                        {"bvid": "BV_DUP_C", "expression": "C 独有的文案。", "topic_label": "tc"},
                    ],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    items = [
        DiscoveredContent(bvid="BV_DUP_A", title="A 视频"),
        DiscoveredContent(bvid="BV_DUP_B", title="B 视频"),
        DiscoveredContent(bvid="BV_DUP_C", title="C 视频"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=_DuplicateExpressionLLM(), database=db)

        caplog.set_level(logging.WARNING)
        completed = await engine._precompute_batch(items, _build_profile())

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        # Only the unique copy survives; the shared sentence is dropped for both.
        assert completed == 1
        assert rows["BV_DUP_A"]["pool_expression"] == ""
        assert rows["BV_DUP_B"]["pool_expression"] == ""
        assert rows["BV_DUP_C"]["pool_expression"] == "C 独有的文案。"
        assert "shared across" in caplog.text


@pytest.mark.asyncio
async def test_classify_batch_matches_results_by_bvid_when_response_reorders() -> None:
    class _ReorderedClassificationLLM:
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
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV_CLASS_B",
                            "score": 0.82,
                            "reason": "B 视频自己的判断。",
                            "topic_group": "B 类",
                            "style_key": "deep_dive",
                        },
                        {
                            "bvid": "BV_CLASS_A",
                            "score": 0.61,
                            "reason": "A 视频自己的判断。",
                            "topic_group": "A 类",
                            "style_key": "story_doc",
                        },
                    ],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        batch = [
            DiscoveredContent(bvid="BV_CLASS_A", title="A 视频"),
            DiscoveredContent(bvid="BV_CLASS_B", title="B 视频"),
        ]
        engine = RecommendationEngine(llm=_ReorderedClassificationLLM(), database=db)

        await engine._classify_batch(batch, _build_profile())

        assert batch[0].relevance_score == 0.61
        assert batch[0].relevance_reason == "A 视频自己的判断。"
        assert batch[0].topic_group == "A 类"
        assert batch[1].relevance_score == 0.82
        assert batch[1].relevance_reason == "B 视频自己的判断。"
        assert batch[1].topic_group == "B 类"


@pytest.mark.asyncio
async def test_generate_expression_accepts_echoed_schema_before_final_fenced_object(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _EchoedSchemaExpressionLLM:
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
        ) -> LLMResponse:
            return LLMResponse(
                content=(
                    'Schema: {"type":"object","properties":{"expression":{"type":"string"}}}\n'
                    "```json\n"
                    '{"expression":"这条会接上你最近的系统感。","topic_label":"系统感"}\n'
                    "```"
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_EchoedSchemaExpressionLLM(), database=db)

        caplog.set_level(logging.ERROR)
        expression, topic_label = await engine.generate_expression(
            DiscoveredContent(
                bvid="BV_EXPR_ECHO",
                title="系统观察的方法",
                up_name="系统笔记",
                description="用结构化方式理解变化。",
                relevance_score=0.9,
            ),
            _build_profile(),
        )

        assert expression == "这条会接上你最近的系统感。"
        assert topic_label == "系统感"
        assert "Failed to generate recommendation expression" not in caplog.text


@pytest.mark.asyncio
async def test_generate_delight_reason_accepts_result_wrapper() -> None:
    class _WrappedDelightReasonLLM:
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
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "result": {
                            "delight_reason": "这条会把你对系统结构的好奇心接住。",
                            "delight_hook": "结构上头",
                        }
                    },
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_WrappedDelightReasonLLM(), database=db)

        reason, hook = await engine._generate_delight_reason(
            DiscoveredContent(
                bvid="BV_DELIGHT_WRAP",
                title="复杂系统入门",
                up_name="系统观察者",
                description="从连接关系理解复杂系统。",
                relevance_score=0.93,
            ),
            _build_profile(),
            "系统结构",
        )

        assert reason == "这条会把你对系统结构的好奇心接住。"
        assert hook == "结构上头"


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
        _seed_visible(
            db,
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
        _seed_visible(
            db,
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


@pytest.mark.asyncio
async def test_precompute_delight_scores_uses_llm_batch_scorer() -> None:
    """v0.3.34+ — delight scoring is one batched LLM call returning
    score + rationale + hook per candidate (no separate reason call).
    """

    class _DelightLLM:
        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            history: list[dict[str, str]] | None = None,
            temperature: float = 0.7,
            max_tokens: int = 4096,
            caller: str = "",
        ) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV1BACKFILL",
                            "score": 0.78,
                            "rationale": "这条会把你最近那股想搞明白系统结构的劲头接住。",
                            "hook": "结构上头",
                        }
                    ],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1BACKFILL",
            title="讲透复杂系统的连接方式",
            up_name="系统观察者",
            source="explore",
            relevance_score=0.91,
            description="从复杂系统角度解释结构之间如何互相作用。",
            view_count=50000,
            like_count=3200,
        )
        engine = RecommendationEngine(llm=_DelightLLM(), database=db)

        scored = await engine.precompute_delight_scores(
            profile=_build_profile(),
            limit=10,
        )

        assert scored == 1
        candidate = db.get_delight_candidate(min_delight_score=0.70)
        assert candidate is not None
        assert candidate["bvid"] == "BV1BACKFILL"
        assert candidate["delight_reason"] == "这条会把你最近那股想搞明白系统结构的劲头接住。"
        assert candidate["delight_hook"] == "结构上头"
