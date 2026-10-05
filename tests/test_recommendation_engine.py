"""Tests for recommendation ranking engine."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.base import LLMFallbackError, LLMProviderError, LLMRateLimitError, LLMResponse
from openbiliclaw.llm.prompts import build_batch_expression_prompt
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.recommendation.curator import PoolCurator
from openbiliclaw.recommendation.engine import (
    ExpressionBatchMalformed,
    ExpressionCopyTransientError,
    RecommendationEngine,
    _recommendation_profile_summary,
)
from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
from openbiliclaw.runtime.expression_copy import ExpressionCopyCoordinator
from openbiliclaw.soul.profile import (
    AwarenessNote,
    InsightHypothesis,
    InterestTag,
    PreferenceLayer,
    SoulProfile,
)
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


class _CopyReadyExpressionLLM:
    """Deterministic batch-copy provider used by watermark integration tests."""

    def __init__(self, *, fail_first: bool = False, block_first: bool = False) -> None:
        self.fail_first = fail_first
        self.block_first = block_first
        self.calls = 0
        self.batch_sizes: list[int] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete_structured_task(
        self,
        *,
        user_input: str,
        **_kwargs: object,
    ) -> LLMResponse:
        self.calls += 1
        batch = _content_batch_from_prompt(user_input)
        self.batch_sizes.append(len(batch))
        if self.fail_first and self.calls == 1:
            raise TimeoutError("copy provider timed out")
        if self.block_first and self.calls == 1:
            self.entered.set()
            await self.release.wait()
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "bvid": item["bvid"],
                        "expression": f"{item['bvid']} 的按需推荐文案。",
                        "topic_label": "按需补文案",
                    }
                    for item in batch
                ],
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


def _build_maxed_prompt_profile() -> SoulProfile:
    profile = SoulProfile(
        personality_portrait="一个有大量历史行为、兴趣和近期洞察的人。",
        core_traits=[f"trait-{index}-" + ("a" * 24) for index in range(35)],
        cognitive_style=[f"style-{index}-" + ("b" * 24) for index in range(35)],
        values=[f"value-{index}-" + ("c" * 24) for index in range(35)],
        motivational_drivers=[f"driver-{index}-" + ("d" * 24) for index in range(35)],
        deep_needs=[f"need-{index}-" + ("e" * 24) for index in range(35)],
        current_phase="正在整理大量输入",
        life_stage="高密度探索期",
        preferences=PreferenceLayer(
            interests=[
                InterestTag(
                    name=f"interest-{index:03d}-" + ("tag" * 6),
                    category=f"domain-{index:03d}",
                    weight=1.0 - index / 1000,
                    source="test",
                )
                for index in range(180)
            ],
            disliked_topics=[f"avoid-{index}" for index in range(128)],
            source_platform_mix={"bilibili": 0.5, "twitter": 0.5},
        ),
        recent_awareness=[
            AwarenessNote(
                date=f"2026-07-{(index % 28) + 1:02d}",
                observation=f"awareness-{index}-" + ("x" * 40),
                trend=f"trend-{index}",
            )
            for index in range(30)
        ],
        active_insights=[
            InsightHypothesis(
                hypothesis=f"insight-{index}-" + ("y" * 40),
                evidence=[f"evidence-{index}-{item}-" + ("z" * 24) for item in range(30)],
                created_at=f"2026-07-{(index % 28) + 1:02d}T12:00:00",
            )
            for index in range(30)
        ],
    )
    profile.preferences.style.depth_preference = 0.9
    profile.preferences.context.session_type = "deep_dive"
    profile._active_speculations = [  # type: ignore[attr-defined]
        {"domain": f"spec-{index}", "reason": "maybe " + ("r" * 24)} for index in range(30)
    ]
    return profile


def _content_batch_from_prompt(user_input: str) -> list[dict[str, object]]:
    batch_json = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
    payload = json.loads(batch_json.strip())
    assert isinstance(payload, list)
    return [dict(item) for item in payload if isinstance(item, dict)]


def test_recommendation_profile_summary_includes_disliked_topics() -> None:
    profile = _build_profile()
    profile.preferences.disliked_topics = [f"话题{i}" for i in range(1, 141)]

    summary = _recommendation_profile_summary(profile)

    # Capped at 128 == _DISLIKED_TOPICS_STORE_CAP, so every stored
    # avoid-topic reaches prompts; only an over-cap store (impossible in
    # practice) would be cut.
    assert summary["disliked_topics"] == [f"话题{i}" for i in range(1, 129)]


def test_recommendation_profile_summary_compacts_maxed_profile() -> None:
    from openbiliclaw.discovery.strategies._utils import compact_content_prompt_profile_summary

    profile = _build_maxed_prompt_profile()

    summary = _recommendation_profile_summary(profile)
    expected = compact_content_prompt_profile_summary(build_profile_summary(profile))

    assert summary == expected
    assert len(summary["core_traits"]) == 20
    assert len(summary["interests"]) == 48
    assert len(summary["interest_domains"]) == 32


def test_recommendation_profile_summary_compacts_after_interests_substitution() -> None:
    profile = _build_maxed_prompt_profile()
    selected_interests = [
        {
            "name": f"selected-interest-{index:02d}",
            "category": "selected",
            "weight": 1.0 - index / 1000,
        }
        for index in range(110)
    ]

    summary = _recommendation_profile_summary(profile, interests=selected_interests)

    assert len(summary["interests"]) == 48
    assert summary["interests"][0]["name"] == "selected-interest-00"
    assert summary["interests"][-1]["name"] == "selected-interest-47"
    assert "interest-000-" not in json.dumps(summary["interests"], ensure_ascii=False)


def test_batch_expression_prompt_shrinks_with_compacted_recommendation_profile() -> None:
    profile = _build_maxed_prompt_profile()
    full_summary = build_profile_summary(profile)
    compact_summary = _recommendation_profile_summary(profile)
    content_items = [
        {
            "bvid": "BV_SHRINK",
            "title": "结构化工作流复盘",
            "up_name": "效率实验室",
            "description": "如何把复杂问题拆成稳定系统。",
        }
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        full_prompt = build_batch_expression_prompt(
            profile_summary=full_summary,
            profile_blocks=engine._profile_blocks(full_summary, cache_key="batch_expression"),
            content_items=content_items,
            tone_profile=None,
        )[1]["content"]
        compact_prompt = build_batch_expression_prompt(
            profile_summary=compact_summary,
            profile_blocks=engine._profile_blocks(compact_summary, cache_key="batch_expression"),
            content_items=content_items,
            tone_profile=None,
        )[1]["content"]

    assert len(compact_prompt) <= len(full_prompt) * 0.5


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
    kwargs.setdefault("relevance_score", 0.9)
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


@pytest.mark.asyncio
async def test_select_diversified_batch_async_matches_sync_output() -> None:
    candidates = [
        DiscoveredContent(
            bvid=f"BV{i}",
            title=f"candidate-{i}",
            topic_group=f"topic-{i % 3}",
            style_key="tutorial" if i % 2 else "deep_dive",
            relevance_score=1.0 - i / 20,
        )
        for i in range(8)
    ]
    embeddings = {
        item.scoring_key: [float(index % 3 == axis) for axis in range(3)]
        for index, item in enumerate(candidates)
    }

    expected = RecommendationEngine._select_diversified_batch(
        candidates,
        limit=5,
        embeddings=embeddings,
    )
    actual = await RecommendationEngine._select_diversified_batch_async(
        candidates,
        limit=5,
        embeddings=embeddings,
    )

    assert [item.bvid for item in actual] == [item.bvid for item in expected]


@pytest.mark.asyncio
async def test_select_diversified_batch_async_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_thread = threading.get_ident()
    worker_threads: list[int] = []
    heartbeat_ran = asyncio.Event()

    def blocking_selector(
        cls: type[RecommendationEngine],
        candidates: list[DiscoveredContent],
        **kwargs: object,
    ) -> list[DiscoveredContent]:
        del cls, kwargs
        worker_threads.append(threading.get_ident())
        time.sleep(0.08)
        return candidates[:1]

    monkeypatch.setattr(
        RecommendationEngine,
        "_select_diversified_batch",
        classmethod(blocking_selector),
    )

    async def heartbeat() -> None:
        await asyncio.sleep(0.01)
        heartbeat_ran.set()

    heartbeat_task = asyncio.create_task(heartbeat())
    result = await RecommendationEngine._select_diversified_batch_async(
        [DiscoveredContent(bvid="BV1", title="one")],
        limit=1,
    )
    await heartbeat_task

    assert heartbeat_ran.is_set()
    assert [item.bvid for item in result] == ["BV1"]
    assert worker_threads and worker_threads[0] != main_thread


@pytest.mark.asyncio
async def test_supergroup_canonical_map_async_matches_sync_output() -> None:
    embeddings = {
        "动漫": [1.0, 0.0],
        "动漫文化": [0.99, 0.01],
        "科技": [0.0, 1.0],
    }
    kwargs = {
        "strict": 0.90,
        "loose": 0.80,
        "prefix_len": 2,
    }

    expected = RecommendationEngine._build_supergroup_canonical_map(
        embeddings,
        **kwargs,
    )
    actual = await RecommendationEngine._build_supergroup_canonical_map_async(
        embeddings,
        **kwargs,
    )

    assert actual == expected == {"动漫文化": "动漫"}


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


def test_expression_tone_profile_does_not_soften_based_on_style_key() -> None:
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

    assert tone["density"] == "dense"
    assert tone["playfulness"] == "low"


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


def test_curator_scoring_records_temporal_shadow_without_changing_scores() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()

        class RecordingCurator:
            def __init__(self) -> None:
                self.audit_calls: list[tuple[object, object, object]] = []

            def build_context(self) -> object:
                return SimpleNamespace(over_budget_amplification_keys=frozenset())

            def score_candidates(
                self,
                candidates: list[DiscoveredContent],
                context: object,
            ) -> dict[str, float]:
                del context
                return {item.scoring_key: 0.73 for item in candidates}

            def record_temporal_ranking_shadow_audit(
                self,
                candidates: object,
                scores: object,
                context: object,
            ) -> None:
                self.audit_calls.append((candidates, scores, context))

        curator = RecordingCurator()
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            curator=curator,  # type: ignore[arg-type]
        )
        candidates = [DiscoveredContent(bvid="BV1", relevance_score=0.7)]

        scores, amplification = engine._score_candidates_with_curator(candidates)

        assert scores == {candidates[0].scoring_key: 0.73}
        assert amplification == frozenset()
        assert len(curator.audit_calls) == 1
        assert curator.audit_calls[0][1] is scores


@pytest.mark.asyncio
async def test_serve_filters_profile_disliked_topics_before_pool_purge_finishes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        try:
            _seed_visible(
                db,
                "BV1CASE",
                title="刑事案件纪实：真实大案复盘",
                up_name="案件频道",
                source="search",
                relevance_score=0.99,
                topic_key="刑事案件",
                topic_group="法律案件",
                pool_topic_label="刑事案件",
            )
            _seed_visible(
                db,
                "BV1DOC",
                title="博物馆纪录片：一件瓷器的百年旅程",
                up_name="纪录片频道",
                source="search",
                relevance_score=0.72,
                topic_key="人文纪录片",
                topic_group="纪录片",
                pool_topic_label="人文纪录片",
            )
            profile = _build_profile()
            profile.preferences.disliked_topics = ["刑事案件", "法律案件"]
            engine = RecommendationEngine(llm=_DummyLLM(), database=db)

            recommendations = await engine.serve(profile, limit=1)
            await asyncio.sleep(0)
        finally:
            db.close()

        assert [item.content.bvid for item in recommendations] == ["BV1DOC"]


@pytest.mark.asyncio
async def test_serve_falls_back_to_exact_topics_when_fuzzy_dislikes_starve_window() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        try:
            _seed_visible(
                db,
                "BV1SCIENCE",
                title="天文学入门",
                description="一条讲解恒星演化的视频",
                topic_key="天文学",
                topic_group="自然科学",
                pool_topic_label="天文学",
            )
            _seed_visible(
                db,
                "BV1HISTORY",
                title="城市史档案",
                description="一条梳理城市变迁的视频",
                topic_key="城市史",
                topic_group="人文历史",
                pool_topic_label="城市史",
                relevance_score=0.8,
            )
            profile = _build_profile()
            profile.preferences.disliked_topics = ["视频"]
            engine = RecommendationEngine(llm=_DummyLLM(), database=db)

            recommendations = await engine.serve(profile, limit=1)
            await asyncio.sleep(0)
        finally:
            db.close()

        assert [item.content.bvid for item in recommendations] == ["BV1SCIENCE"]


@pytest.mark.asyncio
async def test_serve_fail_safe_never_restores_exact_disliked_topics() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        try:
            for index in range(2):
                _seed_visible(
                    db,
                    f"BV1CASE{index}",
                    title=f"案件复盘 {index}",
                    topic_key="刑事案件",
                    topic_group="法律案件",
                    pool_topic_label="刑事案件",
                    relevance_score=0.9 - index * 0.1,
                )
            profile = _build_profile()
            profile.preferences.disliked_topics = ["刑事案件"]
            engine = RecommendationEngine(llm=_DummyLLM(), database=db)

            recommendations = await engine.serve(profile, limit=1)
            await asyncio.sleep(0)
        finally:
            db.close()

        assert recommendations == []


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
async def test_generate_expression_normalizes_style_key_for_prompt() -> None:
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
        assert '"style_key": "daily_wander"' in user_input
        assert '"style_key": "lifestyle"' not in user_input
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

        body_text = "H" * 300 + "MIDDLE_BODY_MARKER" + "T" * 200
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
                body_text=body_text,
                style_key="deep_dive",
                topic_group="系统设计",
                relevance_score=0.8,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert body_text in user_input
        assert '"body_text"' in user_input
        # body_text must never leak into the cached system prompt.
        assert "MIDDLE_BODY_MARKER" not in str(llm.calls[0]["system_instruction"])


@pytest.mark.asyncio
async def test_generate_expression_grounds_copy_in_evaluation_clock() -> None:
    """Realtime copy must see publication time plus the evaluation clock,
    never the wall clock from when the expression was generated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1TIME",
                title="2024 年的老片重映",
                up_name="某UP",
                published_at="2024-05-01T12:00:00Z",
                published_label="2024-05-01",
                temporal_evaluated_at="2026-01-02T03:04:05Z",
                relevance_score=0.8,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert '"published_at": "2024-05-01T12:00:00Z"' in user_input
        assert '"published_label": "2024-05-01"' in user_input
        assert '"evaluated_at": "2026-01-02T03:04:05Z"' in user_input
        # Per-call temporal data stays out of the cached static prefix.
        assert "2026-01-02T03:04:05Z" not in str(llm.calls[0]["system_instruction"])


@pytest.mark.asyncio
async def test_generate_expression_requests_no_core_memory_injection_when_supported() -> None:
    class _CoreMemoryRecordingLLM:
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
            inject_core_memory: bool = True,
        ) -> LLMResponse:
            self.calls.append(
                {
                    "caller": caller,
                    "inject_core_memory": inject_core_memory,
                }
            )
            return LLMResponse(
                content=json.dumps(
                    {
                        "expression": "这条会接上你最近想把系统拆明白的状态。",
                        "topic_label": "系统拆解",
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
        llm = _CoreMemoryRecordingLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV_EXPR_NO_CORE",
                title="系统拆解方法",
                up_name="系统笔记",
                description="把复杂问题拆成可执行结构。",
                relevance_score=0.9,
            ),
            _build_profile(),
        )

        assert llm.calls == [
            {
                "caller": "recommendation.expression",
                "inject_core_memory": False,
            }
        ]


@pytest.mark.asyncio
async def test_generate_expression_uses_layered_profile_prefix() -> None:
    class _LayerRecordingLLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            self.calls.append(dict(kwargs))
            return LLMResponse(
                content=json.dumps(
                    {
                        "expression": "这条能接住你喜欢拆复杂系统的劲头。",
                        "topic_label": "系统拆解",
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
        llm = _LayerRecordingLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV_EXPR_LAYERED",
                title="系统拆解方法",
                up_name="系统笔记",
                description="把复杂问题拆成可执行结构。",
                relevance_score=0.9,
            ),
            _build_profile(),
        )

    user_input = str(llm.calls[0]["user_input"])
    assert "<profile_summary>" not in user_input
    assert user_input.index("<profile_core>") < user_input.index("<profile_interests>")
    assert user_input.index("<profile_interests>") < user_input.index("<source_platform>")
    assert user_input.index("<source_platform>") < user_input.index("<content_summary>")


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
async def test_append_recommendations_preserves_x_text_shape_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "2064073338197328079",
            title="A 12 step program for cats who can’t stop eating and licking plastic.",
            up_name="@UprootedTexan99",
            source="x-search",
            source_platform="twitter",
            content_id="2064073338197328079",
            content_url="https://x.com/UprootedTexan99/status/2064073338197328079",
            content_type="tweet",
            body_text=(
                "A 12 step program for cats who can’t stop eating and licking plastic.\n\n"
                "Is this anything?"
            ),
            cover_url="",
            relevance_score=0.67,
            relevance_reason="X text item should keep the tweet body through append.",
            pool_expression="这条是纯文字 X 推荐。",
            pool_topic_label="猫咪离谱癖好梗",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        recommendations = await engine.append_recommendations(
            profile=_build_profile(),
            excluded_bvids=[],
            limit=1,
        )

        assert len(recommendations) == 1
        content = recommendations[0].content
        assert content.source_platform == "twitter"
        assert content.content_type == "tweet"
        assert content.body_text == (
            "A 12 step program for cats who can’t stop eating and licking plastic.\n\n"
            "Is this anything?"
        )
        assert content.content_url == "https://x.com/UprootedTexan99/status/2064073338197328079"
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
async def test_serve_pool_reads_run_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = RecommendationEngine(llm=_DummyLLM(), database=object())  # type: ignore[arg-type]
    event_loop_thread_id = threading.get_ident()
    worker_thread_ids: list[int] = []
    heartbeat_ran = asyncio.Event()

    def readiness() -> dict[str, int]:
        worker_thread_ids.append(threading.get_ident())
        return {"available": 1, "raw": 1, "pending": 0}

    def load_candidates(
        _profile: SoulProfile,
        *,
        limit: int,
        excluded_bvids: frozenset[str],
    ) -> tuple[list[DiscoveredContent], int, int, int, int]:
        del limit, excluded_bvids
        worker_thread_ids.append(threading.get_ident())
        time.sleep(0.08)
        return [], 0, 0, 0, 0

    async def heartbeat() -> None:
        await asyncio.sleep(0.01)
        heartbeat_ran.set()

    monkeypatch.setattr(engine, "_pool_readiness_counts", readiness)
    monkeypatch.setattr(engine, "_load_filtered_serve_candidates", load_candidates)
    heartbeat_task = asyncio.create_task(heartbeat())

    recommendations = await engine.serve(_build_profile(), limit=1)
    await heartbeat_task

    assert recommendations == []
    assert heartbeat_ran.is_set()
    assert worker_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in worker_thread_ids)


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
    assert summary["styles"] == {"deep_focus": 2, "quick_scan": 1}
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
            self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
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
        full_body_text = "H" * 300 + "CLASSIFY_MIDDLE_MARKER" + "T" * 200

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
            published_at="2026-08-04T08:00:00Z",
            body_text=full_body_text,
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
        assert full_body_text in str(llm.calls[0]["user_input"])
        assert "CLASSIFY_MIDDLE_MARKER" not in str(llm.calls[0]["system_instruction"])

        # Verify DB was updated
        rows = db.get_pool_candidates(limit=10)
        by_bvid = {r["bvid"]: r for r in rows}

        xhs1 = by_bvid.get("xhs_001")
        assert xhs1 is not None
        assert xhs1["style_key"] == "daily_wander"
        assert xhs1["topic_group"] == "美食烹饪"
        assert xhs1["topic_key"] == "美食烹饪"  # backfilled from topic_group
        assert float(xhs1["relevance_score"]) == pytest.approx(0.85)

        classification_prompt = str(llm.calls[0]["user_input"])
        assert '"published_at": "2026-08-04T08:00:00Z"' in classification_prompt
        assert '"evaluated_at": "' in classification_prompt

        xhs2 = by_bvid.get("xhs_002")
        assert xhs2 is not None
        assert xhs2["style_key"] == "hands_on"
        assert xhs2["topic_group"] == "游戏攻略"


@pytest.mark.asyncio
async def test_classify_batch_split_retry_halves_on_reasoning_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reasoning-only + finish_reason=length is size-dependent: halve and retry."""
    calls: list[int] = []

    async def _fake_classify(batch: list[DiscoveredContent], _profile: SoulProfile) -> None:
        calls.append(len(batch))
        if len(batch) > 1:
            raise LLMProviderExecutionError(
                "openai_compatible returned reasoning but no final content "
                "(finish_reason=length); disable thinking/reasoning or increase max_tokens"
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)
        monkeypatch.setattr(engine, "_classify_batch", _fake_classify)
        items = [
            DiscoveredContent(bvid=f"BV_SPLIT_{index}", title=f"t{index}") for index in range(4)
        ]

        await engine._classify_batch_with_split_retry(items, _build_profile())

    assert calls == [4, 2, 1, 1, 2, 1, 1]


@pytest.mark.asyncio
async def test_classify_batch_split_retry_propagates_non_budget_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def _fake_classify(batch: list[DiscoveredContent], _profile: SoulProfile) -> None:
        calls.append(len(batch))
        raise LLMProviderExecutionError("Provider returned 429 rate limit")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)
        monkeypatch.setattr(engine, "_classify_batch", _fake_classify)

        with pytest.raises(LLMProviderExecutionError):
            await engine._classify_batch_with_split_retry(
                [
                    DiscoveredContent(bvid="BV_NOSPLIT_A", title="a"),
                    DiscoveredContent(bvid="BV_NOSPLIT_B", title="b"),
                ],
                _build_profile(),
            )

    assert calls == [2]


@pytest.mark.asyncio
async def test_classify_pool_backlog_retires_legacy_temporally_stale_rows() -> None:
    class _TemporalClassifyLLM:
        async def complete_structured_task(self, **_kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "score": 0.95,
                            "reason": "非常匹配用户兴趣",
                            "topic_group": "即时事件",
                            "style_key": "tutorial",
                            "temporal_class": "breaking",
                            "temporal_confidence": 0.96,
                            "temporal_reason": "直播已经结束，核心信息失效",
                            "temporal_validity_mode": "event_state",
                            "temporal_valid_until": "",
                            "temporal_scope": "core",
                            "temporal_evidence": "直播已经结束",
                            "temporal_state": "expired",
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
            "BVLEGACYSTALE",
            title="直播已经结束：旧库里尚未补分类的突发内容",
            source="search",
            style_key="",
            topic_group="",
            relevance_score=0.0,
            published_at="2000-01-01T00:00:00Z",
        )
        engine = RecommendationEngine(llm=_TemporalClassifyLLM(), database=db)

        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 1
        row = db.conn.execute(
            """
            SELECT pool_status, temporal_class, temporal_confidence, temporal_reason
            FROM content_cache
            WHERE bvid = 'BVLEGACYSTALE'
            """
        ).fetchone()
        assert row["pool_status"] == "stale"
        assert row["temporal_class"] == "breaking"
        assert float(row["temporal_confidence"]) == pytest.approx(0.96)
        assert row["temporal_reason"] == "直播已经结束，核心信息失效"
        assert db.get_pool_candidates(limit=10) == []
        db.close()


@pytest.mark.asyncio
async def test_classify_pool_backlog_temporal_grounding_uses_prompt_projection() -> None:
    evidence = "赛事已经结束"

    class _TemporalClassifyLLM:
        def __init__(self) -> None:
            self.user_input = ""

        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            self.user_input = str(kwargs.get("user_input", ""))
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "score": 0.95,
                            "reason": "匹配用户兴趣",
                            "topic_group": "赛事",
                            "style_key": "deep_focus",
                            "temporal_class": "current",
                            "temporal_confidence": 0.96,
                            "temporal_reason": "赛事终态会使核心信息失效",
                            "temporal_validity_mode": "event_state",
                            "temporal_valid_until": "",
                            "temporal_scope": "core",
                            "temporal_evidence": evidence,
                            "temporal_state": "expired",
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
            "BVPROMPTPROJECTION",
            title="普通赛事介绍",
            description=("甲" * 401) + evidence,
            source="search",
            style_key="",
            topic_group="",
            relevance_score=0.0,
        )
        llm = _TemporalClassifyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 1
        assert evidence not in llm.user_input
        row = db.conn.execute(
            "SELECT pool_status, temporal_validity_mode, temporal_state "
            "FROM content_cache WHERE bvid = 'BVPROMPTPROJECTION'"
        ).fetchone()
        assert dict(row) == {
            "pool_status": "fresh",
            "temporal_validity_mode": "freshness_only",
            "temporal_state": "unknown",
        }
        db.close()


@pytest.mark.asyncio
async def test_classify_pool_backlog_missing_temporal_fields_preserves_prior_evidence() -> None:
    class _MissingTemporalClassifyLLM:
        async def complete_structured_task(self, **_kwargs: Any) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "score": 0.95,
                            "reason": "相关性仍然有效",
                            "topic_group": "即时事件",
                            "style_key": "daily_wander",
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
            "BVLEGACYTEMPORAL",
            title="旧池中的过期突发内容",
            source="search",
            style_key="",
            topic_group="",
            relevance_score=0.0,
            published_at="2000-01-01T00:00:00Z",
        )
        # Simulate a pre-v2 row that still uses the old age-only evidence.
        # The old 3-day window now schedules a review instead of claiming that
        # the content is factually expired.
        db._execute_write(
            """
            UPDATE content_cache
            SET temporal_class = 'breaking',
                temporal_confidence = 0.95,
                temporal_reason = '核心价值依赖事件仍在发生',
                pool_status = 'fresh'
            WHERE bvid = 'BVLEGACYTEMPORAL'
            """
        )
        engine = RecommendationEngine(llm=_MissingTemporalClassifyLLM(), database=db)

        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=10)

        assert classified == 1
        row = db.conn.execute(
            """
            SELECT pool_status, temporal_class, temporal_confidence, temporal_reason,
                   relevance_score, style_key
            FROM content_cache
            WHERE bvid = 'BVLEGACYTEMPORAL'
            """
        ).fetchone()
        assert row["pool_status"] == "temporal_review_hold"
        assert row["temporal_class"] == "breaking"
        assert float(row["temporal_confidence"]) == pytest.approx(0.95)
        assert row["temporal_reason"] == "核心价值依赖事件仍在发生"
        assert float(row["relevance_score"]) == pytest.approx(0.95)
        assert row["style_key"] == "daily_wander"
        assert db.get_pool_candidates(limit=10) == []
        db.close()


@pytest.mark.asyncio
async def test_classify_pool_backlog_default_batch_size_is_30_and_override_still_applies() -> None:
    class _BatchSizingClassifyLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

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
            batch = _content_batch_from_prompt(user_input)
            self.batch_sizes.append(len(batch))
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "score": 0.71,
                            "reason": "batch classification",
                            "topic_group": "批量分类",
                            "style_key": "deep_dive",
                        }
                        for _item in batch
                    ],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    def seed_unclassified_rows(db: Database, count: int) -> None:
        for index in range(count):
            _seed_visible(
                db,
                f"BV_classify_{index}",
                title=f"待分类内容 {index}",
                up_name="UP主",
                source="search",
                style_key="",
                topic_group="",
                topic_key="",
                relevance_score=0.0,
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "default.db")
        db.initialize()
        seed_unclassified_rows(db, 60)
        llm = _BatchSizingClassifyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine.classify_pool_backlog(profile=_build_profile(), limit=60)

        assert classified == 60
        assert llm.batch_sizes == [30, 30]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "override.db")
        db.initialize()
        seed_unclassified_rows(db, 60)
        llm = _BatchSizingClassifyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        classified = await engine.classify_pool_backlog(
            profile=_build_profile(),
            limit=60,
            batch_size=25,
        )

        assert classified == 60
        assert llm.batch_sizes == [25, 25, 10]


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
                                "score": 0.65,
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
                else:  # classify (evaluate_batch)
                    content = json.dumps(
                        [
                            {
                                "score": 0.65,
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
async def test_prewarm_uses_configured_keyframe_and_danmaku_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Embedding:
        similarity_threshold = 0.82

        async def embed(self, _text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        def lookup_cached(self, _text: str) -> list[float] | None:
            return None

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "limits.db")
        db.initialize()
        _seed_visible(db, "BVLIMIT", title="测试视频", up_name="UP", source="search")
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_Embedding(),  # type: ignore[arg-type]
            keyframe_enabled=True,
            keyframe_fetch_limit=7,
            danmaku_enabled=True,
            danmaku_fetch_limit=9,
            bilibili_client=object(),
        )
        monkeypatch.setattr(engine, "_keyframe_active", lambda: True)
        monkeypatch.setattr(engine, "_danmaku_active", lambda: True)
        seen: dict[str, int] = {}

        async def _keyframes(*, limit: int) -> int:
            seen["keyframes"] = limit
            return 0

        async def _danmaku(*, limit: int) -> int:
            seen["danmaku"] = limit
            return 0

        monkeypatch.setattr(engine, "prewarm_pool_keyframes", _keyframes)
        monkeypatch.setattr(engine, "prewarm_pool_danmaku", _danmaku)
        assert await engine.prewarm_pool_mmr_embeddings() > 0
        assert seen == {"keyframes": 7, "danmaku": 9}
        db.close()


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
        assert rows["xhs_jsonl_001"]["style_key"] == "daily_wander"
        assert rows["xhs_jsonl_002"]["style_key"] == "deep_focus"
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
        assert row["style_key"] == "deep_focus"
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
async def test_precompute_batch_requests_no_core_memory_injection_when_supported() -> None:
    class _CoreMemoryRecordingBatchLLM:
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
            inject_core_memory: bool = True,
        ) -> LLMResponse:
            self.calls.append(
                {
                    "caller": caller,
                    "inject_core_memory": inject_core_memory,
                }
            )
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV_BATCH_NO_CORE",
                            "expression": "这条能接住你最近想拆流程的劲头。",
                            "topic_label": "流程拆解",
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
        item = DiscoveredContent(
            bvid="BV_BATCH_NO_CORE",
            title="自动化流程怎么拆",
            up_name="效率实验室",
            description="把一个复杂流程拆成可执行的小步骤。",
            relevance_score=0.88,
        )
        _seed_pool(db, [item], precomputed=False)
        llm = _CoreMemoryRecordingBatchLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._precompute_batch([item], _build_profile())

        assert completed == 1
        assert llm.calls == [
            {
                "caller": "recommendation.write_expression",
                "inject_core_memory": False,
            }
        ]


@pytest.mark.asyncio
async def test_precompute_batch_preserves_full_body_text() -> None:
    class _BodyRecordingBatchLLM:
        def __init__(self) -> None:
            self.user_inputs: list[str] = []

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
            self.user_inputs.append(user_input)
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV_EXPR_BODY",
                            "expression": "这条能接住你最近想拆流程的劲头。",
                            "topic_label": "流程拆解",
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
        body_text = "H" * 300 + "T" * 200
        item = DiscoveredContent(
            bvid="BV_EXPR_BODY",
            title="长线程怎么拆",
            up_name="效率实验室",
            source_platform="twitter",
            content_type="thread",
            body_text=body_text,
            relevance_score=0.88,
        )
        _seed_pool(db, [item], precomputed=False)
        llm = _BodyRecordingBatchLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._precompute_batch([item], _build_profile())

        assert completed == 1
        batch = _content_batch_from_prompt(llm.user_inputs[0])
        assert batch[0]["body_text"] == body_text


@pytest.mark.asyncio
async def test_precompute_batch_grounds_copy_in_evaluation_clock() -> None:
    """Recommendation copy must see the source publication time plus the
    evaluation clock, never the copy-generation wall clock."""

    class _TemporalRecordingBatchLLM:
        def __init__(self) -> None:
            self.user_inputs: list[str] = []

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
            self.user_inputs.append(user_input)
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV_EXPR_TIME",
                            "expression": "这条老片的价值不靠发布时间撑场。",
                            "topic_label": "旧片重看",
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
        item = DiscoveredContent(
            bvid="BV_EXPR_TIME",
            title="2024 年的老片重映",
            up_name="某UP",
            published_at="2024-05-01T12:00:00Z",
            published_label="2024-05-01",
            temporal_evaluated_at="2026-01-02T03:04:05Z",
            relevance_score=0.82,
        )
        _seed_pool(db, [item], precomputed=False)
        llm = _TemporalRecordingBatchLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._precompute_batch([item], _build_profile())

        assert completed == 1
        batch = _content_batch_from_prompt(llm.user_inputs[0])
        assert batch[0]["published_at"] == "2024-05-01T12:00:00Z"
        assert batch[0]["published_label"] == "2024-05-01"
        assert batch[0]["evaluated_at"] == "2026-01-02T03:04:05Z"


@pytest.mark.asyncio
async def test_drain_expression_copy_limits_batch_concurrency_to_two_by_default() -> None:
    class _ConcurrencyRecordingExpressionLLM:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

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
            inject_core_memory: bool = True,
        ) -> LLMResponse:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return LLMResponse(
                    content=json.dumps(
                        [
                            {
                                "expression": "这条会接上你最近想拆流程的状态。",
                                "topic_label": "流程拆解",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            finally:
                self.active -= 1

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        items = [
            DiscoveredContent(
                bvid=f"BV_EXPR_CONCURRENCY_{index}",
                title=f"流程视频 {index}",
                up_name="效率实验室",
                description="把一个复杂流程拆成可执行的小步骤。",
                style_key="deep_dive",
                topic_group="效率工具",
                relevance_score=0.88,
            )
            for index in range(4)
        ]
        _seed_pool(db, items, precomputed=False)
        llm = _ConcurrencyRecordingExpressionLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._drain_expression_copy(
            profile=_build_profile(),
            limit=4,
            batch_size=1,
        )

        assert completed == 4
        assert llm.max_active == 2


@pytest.mark.asyncio
async def test_drain_expression_copy_default_batch_size_is_30() -> None:
    class _BatchSizeRecordingExpressionLLM:
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
            inject_core_memory: bool = True,
        ) -> LLMResponse:
            batch = _content_batch_from_prompt(user_input)
            self.batch_sizes.append(len(batch))
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": item["bvid"],
                            "expression": f"{item['bvid']} 的专属推荐文案。",
                            "topic_label": "流程拆解",
                        }
                        for item in batch
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
            DiscoveredContent(
                bvid=f"BV_EXPR_DEFAULT_BATCH_{index:02d}",
                title=f"流程视频 {index}",
                up_name="效率实验室",
                description="把一个复杂流程拆成可执行的小步骤。",
                style_key="deep_dive",
                topic_group="效率工具",
                relevance_score=0.88,
            )
            for index in range(46)
        ]
        _seed_pool(db, items, precomputed=False)
        llm = _BatchSizeRecordingExpressionLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._drain_expression_copy(
            profile=_build_profile(),
            limit=46,
        )

        assert completed == 46
        assert sorted(llm.batch_sizes, reverse=True) == [30, 16]


@pytest.mark.asyncio
async def test_public_expression_drain_caps_sixty_then_processes_tail() -> None:
    class _RecordingLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []
            self.active = 0
            self.max_active = 0

        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                batch = _content_batch_from_prompt(str(kwargs["user_input"]))
                self.batch_sizes.append(len(batch))
                await asyncio.sleep(0)
                return LLMResponse(
                    content=json.dumps(
                        [
                            {
                                "bvid": item["bvid"],
                                "expression": f"{item['bvid']} 的推荐文案。",
                                "topic_label": "持续补货",
                            }
                            for item in batch
                        ],
                        ensure_ascii=False,
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            finally:
                self.active -= 1

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_EXPR_PUBLIC_{index:02d}",
                    title=f"持续补货 {index}",
                    up_name="效率实验室",
                    style_key="deep_dive",
                    topic_group="效率工具",
                    relevance_score=0.88,
                )
                for index in range(75)
            ],
            precomputed=False,
        )
        llm = _RecordingLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=75) == 60
        assert sorted(llm.batch_sizes) == [30, 30]
        assert llm.max_active == 2
        llm.batch_sizes.clear()
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 15
        assert llm.batch_sizes == [15]


@pytest.mark.asyncio
async def test_copy_ready_target_fills_only_deficit_and_leaves_deep_backlog() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(4):
            _seed_visible(
                db,
                f"BV_READY_{index}",
                title=f"ready {index}",
                topic_group=f"ready-{index}",
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group=f"按需文案-{index}",
                    relevance_score=0.9,
                )
                for index in range(10)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=6,
        )

        assert engine.count_pending_expression_copy_demand() == 2
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 2
        assert db.count_pool_readiness()["available"] == 6
        assert db.count_pool_readiness()["admitted_pending_copy"] == 8
        assert engine.count_pending_expression_copy_demand() == 0
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 0
        assert llm.batch_sizes == [2]
        db.close()


@pytest.mark.asyncio
async def test_copy_ready_target_does_not_drain_backlog_hidden_by_topic_window() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(5):
            _seed_visible(
                db,
                f"BV_TOPIC_READY_{index}",
                title=f"ready {index}",
                topic_group="concentrated-topic",
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_TOPIC_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group="concentrated-topic",
                    relevance_score=0.8,
                )
                for index in range(4)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=5,
            pool_available_target_count=5,
        )

        readiness = db.count_pool_readiness()
        assert readiness["available"] == 3
        assert readiness["copy_ready"] == 5
        assert readiness["admitted_pending_copy"] == 4
        assert engine.count_pending_expression_copy_demand() == 0
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 0
        assert llm.calls == 0
        assert db.count_pool_readiness()["admitted_pending_copy"] == 4
        db.close()


@pytest.mark.asyncio
async def test_copy_watermark_fills_public_pool_deficit_with_eligible_topic_first() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(5):
            _seed_visible(
                db,
                f"BV_PUBLIC_READY_{index}",
                title=f"ready {index}",
                topic_group="saturated-topic",
                relevance_score=0.95 - index * 0.01,
            )
        for index in range(3):
            _seed_pool(
                db,
                [
                    DiscoveredContent(
                        bvid=f"BV_PUBLIC_BLOCKED_{index}",
                        title=f"blocked {index}",
                        style_key="deep_focus",
                        topic_group="saturated-topic",
                        relevance_score=0.9 - index * 0.01,
                    )
                ],
                precomputed=False,
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid="BV_PUBLIC_ELIGIBLE",
                    title="eligible public slot",
                    style_key="deep_focus",
                    topic_group="open-topic",
                    relevance_score=0.65,
                )
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=7,
            pool_available_target_count=4,
        )

        readiness = db.count_pool_readiness()
        assert readiness["available"] == 3
        assert readiness["copy_ready"] == 5
        assert readiness["admitted_pending_available"] == 1
        # One eligible row fills the public gap while a second row fills the
        # deeper copy-ready watermark; eligible-first must satisfy both with
        # the same two-row batch.
        assert engine.count_pending_expression_copy_demand() == 2

        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 2

        readiness = db.count_pool_readiness()
        assert readiness["available"] == 4
        assert readiness["copy_ready"] == 7
        assert readiness["admitted_pending_copy"] == 2
        assert readiness["admitted_pending_available"] == 0
        assert engine.count_pending_expression_copy_demand() == 0
        assert llm.batch_sizes == [2]
        copied = db.conn.execute(
            "SELECT pool_expression FROM content_cache WHERE bvid = 'BV_PUBLIC_ELIGIBLE'"
        ).fetchone()
        assert str(copied["pool_expression"]).strip()
        copied_blocked_count = int(
            db.conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM content_cache
                WHERE bvid LIKE 'BV_PUBLIC_BLOCKED_%'
                  AND COALESCE(pool_expression, '') != ''
                """
            ).fetchone()["count"]
        )
        assert copied_blocked_count == 1
        db.close()


@pytest.mark.asyncio
async def test_copy_refill_is_not_blocked_by_seen_rows_ahead_in_same_topic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(5):
            _seed_visible(
                db,
                f"BV_OTHER_READY_{index}",
                title=f"other ready {index}",
                topic_group=f"other-ready-{index}",
            )
        for index in range(3):
            bvid = f"BV_SEEN_HEAD_{index}"
            _seed_visible(
                db,
                bvid,
                title=f"seen head {index}",
                topic_group="seen-head-topic",
                relevance_score=0.99 - index * 0.01,
            )
            db.insert_event(
                "view",
                title=f"seen head {index}",
                url=f"https://www.bilibili.com/video/{bvid}",
                metadata={"bvid": bvid},
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid="BV_PENDING_BELOW_SEEN",
                    title="pending below seen heads",
                    style_key="deep_focus",
                    topic_group="seen-head-topic",
                    relevance_score=0.65,
                )
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=5,
            pool_available_target_count=6,
        )

        readiness = db.count_pool_readiness()
        assert readiness["available"] == 5
        assert readiness["copy_ready"] == 5
        assert readiness["admitted_pending_available"] == 1
        assert engine.count_pending_expression_copy_demand() == 1

        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 1

        assert db.count_pool_readiness()["available"] == 6
        assert llm.batch_sizes == [1]
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, 0])
async def test_copy_ready_target_disabled_preserves_legacy_backlog_drain(
    target: int | None,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(3):
            _seed_visible(
                db,
                f"BV_LEGACY_READY_{index}",
                title=f"ready {index}",
                topic_group=f"legacy-ready-{index}",
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_LEGACY_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group=f"legacy-pending-{index}",
                    relevance_score=0.9,
                )
                for index in range(7)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=target,
        )

        assert engine.count_pending_expression_copy_demand() == 7
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 7
        assert db.count_pool_readiness()["available"] == 10
        assert db.count_pool_readiness()["admitted_pending_copy"] == 0
        assert llm.batch_sizes == [7]
        db.close()


@pytest.mark.asyncio
async def test_copy_ready_target_rechecks_deficit_under_expression_lock() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_LOCK_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group=f"lock-{index}",
                    relevance_score=0.9,
                )
                for index in range(8)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM(block_first=True)
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=4,
        )

        first = asyncio.create_task(
            engine.drain_pending_expression_copy(profile=_build_profile(), limit=60)
        )
        await asyncio.wait_for(llm.entered.wait(), timeout=1)
        second = asyncio.create_task(
            engine.drain_pending_expression_copy(profile=_build_profile(), limit=60)
        )
        await asyncio.sleep(0)
        llm.release.set()

        assert await first == 4
        assert await second == 0
        assert db.count_pool_readiness()["available"] == 4
        assert db.count_pool_readiness()["admitted_pending_copy"] == 4
        assert llm.batch_sizes == [4]
        db.close()


@pytest.mark.asyncio
async def test_copy_ready_target_failure_keeps_pending_for_next_success() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_RETRY_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group="retry",
                    relevance_score=0.9,
                )
                for index in range(2)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM(fail_first=True)
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=2,
        )

        with pytest.raises(ExpressionCopyTransientError) as exc_info:
            await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60)
        assert exc_info.value.kind == "timeout"
        assert db.count_pool_readiness()["available"] == 0
        assert db.count_pool_readiness()["admitted_pending_copy"] == 2

        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 2
        assert db.count_pool_readiness()["available"] == 2
        assert db.count_pool_readiness()["admitted_pending_copy"] == 0
        db.close()


@pytest.mark.asyncio
async def test_lower_copy_ready_target_keeps_existing_copy_and_pending_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(5):
            _seed_visible(
                db,
                f"BV_LOWER_READY_{index}",
                title=f"ready {index}",
                topic_group=f"lower-ready-{index}",
            )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid=f"BV_LOWER_PENDING_{index}",
                    title=f"pending {index}",
                    style_key="deep_focus",
                    topic_group=f"lower-pending-{index}",
                    relevance_score=0.9,
                )
                for index in range(3)
            ],
            precomputed=False,
        )
        llm = _CopyReadyExpressionLLM()
        engine = RecommendationEngine(
            llm=llm,
            database=db,
            copy_ready_target_count=3,
        )

        assert engine.count_pending_expression_copy_demand() == 0
        assert await engine.drain_pending_expression_copy(profile=_build_profile(), limit=60) == 0
        readiness = db.count_pool_readiness()
        assert readiness["available"] == 5
        assert readiness["admitted_pending_copy"] == 3
        assert llm.calls == 0
        db.close()


@pytest.mark.asyncio
async def test_copy_ready_target_keeps_empty_copy_rows_out_of_serve_and_notifies_consumption() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV_SERVE_READY",
            title="ready",
            pool_expression="正式推荐文案",
            pool_topic_label="正式主题",
        )
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid="BV_SERVE_PENDING",
                    title="pending",
                    style_key="deep_focus",
                    topic_group="serve",
                    relevance_score=0.9,
                )
            ],
            precomputed=False,
        )
        notifications: list[str] = []
        engine = RecommendationEngine(
            llm=_CopyReadyExpressionLLM(),
            database=db,
            copy_ready_target_count=1,
        )
        engine.set_copy_pending_callback(notifications.append)

        recommendations = await engine.serve(_build_profile(), limit=2)
        await asyncio.sleep(0)

        assert [item.content.bvid for item in recommendations] == ["BV_SERVE_READY"]
        assert all(item.expression.strip() and item.topic_label.strip() for item in recommendations)
        assert db.count_pool_readiness()["admitted_pending_copy"] == 1
        assert notifications == ["inventory_consumed"]
        db.close()


@pytest.mark.asyncio
async def test_empty_ready_serve_notifies_bounded_copy_refill() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(
            db,
            [
                DiscoveredContent(
                    bvid="BV_ONLY_PENDING_COPY",
                    title="pending only",
                    style_key="deep_focus",
                    topic_group="serve-empty",
                    relevance_score=0.9,
                )
            ],
            precomputed=False,
        )
        notifications: list[str] = []
        engine = RecommendationEngine(
            llm=_CopyReadyExpressionLLM(),
            database=db,
            copy_ready_target_count=1,
        )
        engine.set_copy_pending_callback(notifications.append)

        result = await engine.serve(_build_profile(), limit=1)

        assert result == []
        assert notifications == ["serve_insufficient"]
        assert engine.count_pending_expression_copy_demand() == 1
        assert db.count_pool_readiness()["admitted_pending_copy"] == 1
        db.close()


@pytest.mark.asyncio
async def test_drain_expression_copy_splits_failed_batch_before_single_fallback() -> None:
    class _SplitRetryExpressionLLM:
        def __init__(self) -> None:
            self.write_batch_sizes: list[int] = []
            self.single_calls = 0

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
            inject_core_memory: bool = True,
        ) -> LLMResponse:
            if caller != "recommendation.write_expression":
                self.single_calls += 1
                raise AssertionError("split retry should avoid single fallback while halves pass")
            batch = _content_batch_from_prompt(user_input)
            self.write_batch_sizes.append(len(batch))
            if len(batch) > 2:
                return LLMResponse(content="not json", provider="test", model="dummy", usage={})
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": item["bvid"],
                            "expression": f"{item['bvid']} 拆半后生成的推荐文案。",
                            "topic_label": "流程拆解",
                        }
                        for item in batch
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
            DiscoveredContent(
                bvid=f"BV_EXPR_SPLIT_{index}",
                title=f"流程视频 {index}",
                up_name="效率实验室",
                description="把一个复杂流程拆成可执行的小步骤。",
                style_key="deep_dive",
                topic_group="效率工具",
                relevance_score=0.88,
            )
            for index in range(4)
        ]
        _seed_pool(db, items, precomputed=False)
        llm = _SplitRetryExpressionLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._drain_expression_copy(
            profile=_build_profile(),
            limit=4,
            batch_size=4,
        )

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        assert completed == 4
        assert llm.write_batch_sizes == [4, 2, 2]
        assert llm.single_calls == 0
        assert rows["BV_EXPR_SPLIT_0"]["pool_expression"].startswith("BV_EXPR_SPLIT_0")
        assert rows["BV_EXPR_SPLIT_3"]["pool_expression"].startswith("BV_EXPR_SPLIT_3")


@pytest.mark.asyncio
async def test_expression_malformed_retry_is_bounded_to_seven_batch_calls() -> None:
    class _MalformedLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            self.calls += 1
            return LLMResponse(content="not json", provider="test", model="dummy", usage={})

    items = [DiscoveredContent(bvid=f"BV_BOUND_{i}", title=str(i)) for i in range(8)]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        llm = _MalformedLLM()
        engine = RecommendationEngine(llm=llm, database=db)
        assert await engine._precompute_batch_with_split_retry(items, _build_profile()) == 0
    assert llm.calls == 7


@pytest.mark.asyncio
async def test_expression_precompute_accepts_pretty_printed_singleton_object() -> None:
    class _PrettySingletonLLM:
        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            return LLMResponse(
                content=json.dumps(
                    {
                        "bvid": "BV_SINGLE_VALID",
                        "expression": "这条会把浏览器自动化的 token 成本拆给你看。",
                        "topic_label": "Agent 浏览器调优",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    item = DiscoveredContent(
        bvid="BV_SINGLE_VALID",
        title="浏览器自动化工具",
        style_key="deep_focus",
        topic_group="人工智能",
        relevance_score=0.92,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, [item], precomputed=False)
        engine = RecommendationEngine(llm=_PrettySingletonLLM(), database=db)

        completed = await engine._precompute_batch_with_split_retry([item], _build_profile())

        row = next(row for row in db.get_cached_content(limit=10) if row["bvid"] == item.bvid)
    assert completed == 1
    assert row["pool_expression"] == "这条会把浏览器自动化的 token 成本拆给你看。"
    assert row["pool_topic_label"] == "Agent 浏览器调优"


@pytest.mark.asyncio
async def test_expression_malformed_singleton_stays_pending_without_single_fallback() -> None:
    class _MalformedLLM:
        def __init__(self) -> None:
            self.callers: list[str] = []

        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            self.callers.append(str(kwargs.get("caller", "")))
            return LLMResponse(content="not json", provider="test", model="dummy", usage={})

    item = DiscoveredContent(bvid="BV_SINGLE_PENDING", title="pending")
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, [item], precomputed=False)
        llm = _MalformedLLM()
        engine = RecommendationEngine(llm=llm, database=db)
        assert await engine._precompute_batch_with_split_retry([item], _build_profile()) == 0
        row = next(row for row in db.get_cached_content(limit=10) if row["bvid"] == item.bvid)
    assert llm.callers == ["recommendation.write_expression"]
    assert row["pool_expression"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (LLMRateLimitError("429 too many requests"), "rate_limited"),
        (TimeoutError("request timed out"), "timeout"),
        (ConnectionError("connection reset"), "connection"),
        (LLMProviderExecutionError("upstream HTTP 503"), "server_error"),
    ],
)
async def test_public_expression_drain_propagates_transient_after_sibling_progress(
    error: BaseException, kind: str
) -> None:
    error.retry_after = 45  # type: ignore[attr-defined]

    class _OneFailedBatchLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        async def complete_structured_task(
            self, *, user_input: str, **kwargs: object
        ) -> LLMResponse:
            batch = _content_batch_from_prompt(user_input)
            self.batch_sizes.append(len(batch))
            if len(batch) > 1:
                raise error
            item = batch[0]
            return LLMResponse(
                content=json.dumps(
                    [{"bvid": item["bvid"], "expression": "sibling", "topic_label": "ok"}]
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    items = [
        DiscoveredContent(
            bvid=f"BV_PUBLIC_TRANSIENT_{i:02d}",
            title=str(i),
            style_key="deep_dive",
            topic_group="test",
            relevance_score=0.8,
        )
        for i in range(31)
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        llm = _OneFailedBatchLLM()
        engine = RecommendationEngine(llm=llm, database=db)
        with pytest.raises(ExpressionCopyTransientError) as exc_info:
            await engine.drain_pending_expression_copy(profile=_build_profile(), limit=31)
    assert sorted(llm.batch_sizes) == [1, 30]
    assert exc_info.value.kind == kind
    assert exc_info.value.completed == 1
    assert exc_info.value.retry_after == 45.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        LLMProviderError("HTTP 401 unauthorized: invalid api key"),
        LLMFallbackError("No provider was available to process the request."),
    ],
)
async def test_public_expression_drain_propagates_auth_and_no_provider(
    error: BaseException,
) -> None:
    class _UnavailableLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            self.calls += 1
            raise error

    items = [
        DiscoveredContent(
            bvid=f"BV_PUBLIC_AUTH_{i}",
            title=str(i),
            style_key="deep_dive",
            topic_group="test",
            relevance_score=0.8,
        )
        for i in range(2)
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        llm = _UnavailableLLM()
        engine = RecommendationEngine(llm=llm, database=db)
        with pytest.raises(type(error)):
            await engine.drain_pending_expression_copy(profile=_build_profile(), limit=2)
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_public_expression_drain_carries_partial_progress_into_missing_retry_transient() -> (
    None
):
    class _PartialThenTransientLLM:
        def __init__(self) -> None:
            self.request_ids: list[tuple[str, ...]] = []

        async def complete_structured_task(
            self, *, user_input: str, **kwargs: object
        ) -> LLMResponse:
            batch = _content_batch_from_prompt(user_input)
            ids = tuple(str(item["bvid"]) for item in batch)
            self.request_ids.append(ids)
            if len(self.request_ids) == 1:
                return LLMResponse(
                    content=json.dumps(
                        [
                            {"bvid": key, "expression": f"copy-{key}", "topic_label": "ok"}
                            for key in ids[:2]
                        ]
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            error = TimeoutError("missing subset timed out")
            error.retry_after = 45  # type: ignore[attr-defined]
            raise error

    items = [
        DiscoveredContent(
            bvid=key,
            title=key,
            style_key="deep_dive",
            topic_group="test",
            relevance_score=0.8,
        )
        for key in ("A", "B", "C", "D")
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        llm = _PartialThenTransientLLM()
        engine = RecommendationEngine(llm=llm, database=db)
        with pytest.raises(ExpressionCopyTransientError) as exc_info:
            await engine.drain_pending_expression_copy(profile=_build_profile(), limit=4)
        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
    assert llm.request_ids == [("A", "B", "C", "D"), ("C", "D")]
    assert exc_info.value.completed == 2
    assert exc_info.value.retry_after == 45.0
    assert rows["A"]["pool_expression"] == "copy-A"
    assert rows["B"]["pool_expression"] == "copy-B"
    assert rows["C"]["pool_expression"] == ""
    assert rows["D"]["pool_expression"] == ""


@pytest.mark.asyncio
async def test_expression_partial_progress_is_attached_to_downstream_auth_failure() -> None:
    class _PartialThenAuthLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self, *, user_input: str, **kwargs: object
        ) -> LLMResponse:
            self.calls += 1
            ids = tuple(str(item["bvid"]) for item in _content_batch_from_prompt(user_input))
            if self.calls == 1:
                return LLMResponse(
                    content=json.dumps(
                        [
                            {"bvid": key, "expression": f"copy-{key}", "topic_label": "ok"}
                            for key in ids[:2]
                        ]
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            raise LLMProviderError("HTTP 401 unauthorized: invalid api key")

    items = [
        DiscoveredContent(
            bvid=key,
            title=key,
            style_key="deep_dive",
            topic_group="test",
            relevance_score=0.8,
        )
        for key in ("A", "B", "C", "D")
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=_PartialThenAuthLLM(), database=db)
        with pytest.raises(LLMProviderError) as exc_info:
            await engine.drain_pending_expression_copy(profile=_build_profile(), limit=4)
    assert getattr(exc_info.value, "completed", 0) == 2


@pytest.mark.asyncio
async def test_expression_coordinator_reports_same_branch_partial_progress() -> None:
    class _PartialThenTimeoutLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_structured_task(
            self, *, user_input: str, **kwargs: object
        ) -> LLMResponse:
            self.calls += 1
            ids = tuple(str(item["bvid"]) for item in _content_batch_from_prompt(user_input))
            if self.calls == 1:
                return LLMResponse(
                    content=json.dumps(
                        [
                            {"bvid": key, "expression": f"copy-{key}", "topic_label": "ok"}
                            for key in ids[:2]
                        ]
                    ),
                    provider="test",
                    model="dummy",
                    usage={},
                )
            raise TimeoutError("missing subset timed out")

    items = [
        DiscoveredContent(
            bvid=key,
            title=key,
            style_key="deep_dive",
            topic_group="test",
            relevance_score=0.8,
        )
        for key in ("A", "B", "C", "D")
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=_PartialThenTimeoutLLM(), database=db)
        coordinator = ExpressionCopyCoordinator(
            pending_count_provider=lambda: 4,
            drain_callback=lambda limit: engine.drain_pending_expression_copy(
                profile=_build_profile(), limit=limit
            ),
            min_items=1,
            safety_wake_seconds=0.01,
        )
        task = asyncio.create_task(coordinator.run_forever())
        async with asyncio.timeout(2):
            while coordinator.status_payload()["expression_batch_state"] != "backoff":
                await asyncio.sleep(0)
        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        assert coordinator.status_payload()["expression_last_completed"] == 2
        assert rows["A"]["pool_expression"] == "copy-A"
        assert rows["B"]["pool_expression"] == "copy-B"
        assert rows["C"]["pool_expression"] == ""
        assert rows["D"]["pool_expression"] == ""
        await coordinator.stop()
        await task


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

        with pytest.raises(ExpressionCopyTransientError) as exc_info:
            await engine._precompute_batch_with_split_retry(items, _build_profile())

    assert exc_info.value.kind == "rate_limited"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_precompute_batch_split_retry_halves_on_reasoning_budget() -> None:
    """A JSON copy batch that dies on reasoning budget splits into singles."""

    class _ReasoningBudgetExpressionLLM:
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
        ) -> LLMResponse:
            bvids = [chunk.split('"', 1)[0] for chunk in user_input.split('"bvid": "')[1:]]
            self.batch_sizes.append(len(bvids))
            if len(bvids) > 1:
                raise LLMProviderExecutionError(
                    "openai_compatible returned reasoning but no final content "
                    "(finish_reason=length); disable thinking/reasoning or increase max_tokens"
                )
            assert bvids
            return LLMResponse(
                content=json.dumps(
                    [{"bvid": bvids[0], "expression": f"copy-{bvids[0]}", "topic_label": "t"}],
                    ensure_ascii=False,
                ),
                provider="test",
                model="dummy",
                usage={},
            )

    items = [
        DiscoveredContent(bvid="BV_REASON_A", title="A", relevance_score=0.8),
        DiscoveredContent(bvid="BV_REASON_B", title="B", relevance_score=0.7),
    ]
    llm = _ReasoningBudgetExpressionLLM()

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_pool(db, items, precomputed=False)
        engine = RecommendationEngine(llm=llm, database=db)

        completed = await engine._precompute_batch_with_split_retry(items, _build_profile())

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}

    assert completed == 2
    assert llm.batch_sizes == [2, 1, 1]
    assert rows["BV_REASON_A"]["pool_expression"] == "copy-BV_REASON_A"
    assert rows["BV_REASON_B"]["pool_expression"] == "copy-BV_REASON_B"


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

        with pytest.raises(ExpressionBatchMalformed) as exc_info:
            await engine._precompute_batch(items, _build_profile())

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        assert exc_info.value.completed == 2
        assert [item.bvid for item in exc_info.value.missing_items] == ["BV_EXPR_A"]
        assert rows["BV_EXPR_A"]["pool_expression"] == ""
        assert rows["BV_EXPR_B"]["pool_expression"] == "B 视频自己的推荐文案。"
        assert rows["BV_EXPR_C"]["pool_expression"] == "C 视频自己的推荐文案。"


@pytest.mark.asyncio
async def test_precompute_batch_keeps_no_id_multi_item_response_pending(
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
        with pytest.raises(ExpressionBatchMalformed):
            await engine._precompute_batch(items, _build_profile())

        # One batch attempt, then one single regeneration per item.
        assert llm.callers == ["recommendation.write_expression"]
        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        # Each row got copy via the keyed single path — never positional batch.
        assert rows["BV_NOID_A"]["pool_expression"] == ""
        assert rows["BV_NOID_B"]["pool_expression"] == ""


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
        with pytest.raises(ExpressionBatchMalformed) as exc_info:
            await engine._precompute_batch(items, _build_profile())

        rows = {row["bvid"]: dict(row) for row in db.get_cached_content(limit=10)}
        # Only the unique copy survives; the shared sentence is dropped for both.
        assert exc_info.value.completed == 1
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


def test_recommendation_engine_no_longer_exposes_delight_reason_llm_helper() -> None:
    assert not hasattr(RecommendationEngine, "_generate_delight_reason")


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
        assert row["style_key"] == "daily_wander"
        assert row["topic_group"] == "美食烹饪"
        assert row["topic_key"] == "美食烹饪"
        assert float(row["relevance_score"]) == pytest.approx(0.85)
        assert row["relevance_reason"] == "美食烹饪类内容"


@pytest.mark.asyncio
async def test_precompute_delight_scores_reuses_evo_result_without_llm_call() -> None:
    """Delight scoring should reuse Evo's relevance result instead of paying
    another LLM scoring pass.
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
            raise AssertionError(f"unexpected LLM call during Evo backfill: {caller}")

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
            relevance_reason="Evo 判断这条能接住你最近想拆清楚系统结构的劲头。",
            topic_group="复杂系统",
            pool_topic_label="系统结构",
            pool_expression="这条会把你最近想拆清楚系统结构的劲头接住。",
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
        assert candidate["delight_score"] == pytest.approx(0.91)
        assert candidate["delight_reason"] == "这条会把你最近想拆清楚系统结构的劲头接住。"
        assert candidate["delight_hook"] == "系统结构"


@pytest.mark.asyncio
async def test_precompute_delight_waits_for_pool_copy_and_repairs_legacy_eval_reason() -> None:
    """Delight admission starts only after formal recommendation copy is ready."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        db.cache_content(
            "BV1COPYRACE",
            title="先完成评估、推荐词仍在生成",
            source="search",
            relevance_score=0.91,
            relevance_reason="评估器判断：该内容与用户近期兴趣高度相关。",
            style_key="deep_focus",
            topic_group="复杂系统",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        scored = await engine.precompute_delight_scores(profile=_build_profile(), limit=10)

        assert scored == 0
        row = db.conn.execute(
            """
            SELECT delight_score, delight_reason, delight_hook
            FROM content_cache
            WHERE bvid = 'BV1COPYRACE'
            """
        ).fetchone()
        assert row is not None
        assert float(row["delight_score"]) == 0.0
        assert row["delight_reason"] == ""
        assert row["delight_hook"] == ""
        assert db.get_delight_candidate(min_delight_score=0.70) is None
        assert "BV1COPYRACE" not in {
            item["bvid"]
            for item in db.get_pool_candidates_needing_delight_score(
                limit=10,
                min_delight_score_for_reason=0.75,
            )
        }
        assert "BV1COPYRACE" in {
            item["bvid"] for item in db.get_pool_candidates_needing_copy(limit=10)
        }

        # The durable writer also rejects the old evaluator-reason path.
        assert (
            db.update_delight_score(
                "BV1COPYRACE",
                delight_score=0.91,
                delight_reason="评估器判断：该内容与用户近期兴趣高度相关。",
                delight_hook="复杂系统",
            )
            is False
        )

        # Seed one legacy row directly to exercise upgrade repair. It remains
        # outside delight scoring while copy is absent.
        db.conn.execute(
            """
            UPDATE content_cache
            SET delight_score = 0.91,
                delight_reason = '评估器判断：该内容与用户近期兴趣高度相关。',
                delight_hook = '复杂系统'
            WHERE bvid = 'BV1COPYRACE'
            """
        )
        db.conn.commit()
        assert await engine.precompute_delight_scores(profile=_build_profile(), limit=10) == 0
        assert db.get_delight_candidate(min_delight_score=0.70) is None

        db.update_pool_copy(
            "BV1COPYRACE",
            expression="这条会接住你最近想拆清复杂系统的那股劲。",
            topic_label="系统拆解",
        )
        assert db.get_delight_candidate(min_delight_score=0.70) is None

        repaired = await engine.precompute_delight_scores(profile=_build_profile(), limit=10)

        assert repaired == 1
        candidate = db.get_delight_candidate(min_delight_score=0.70)
        assert candidate is not None
        assert candidate["delight_reason"] == "这条会接住你最近想拆清复杂系统的那股劲。"
        assert candidate["delight_hook"] == "系统拆解"


@pytest.mark.asyncio
async def test_precompute_delight_preserves_conservative_profile_threshold_in_feed() -> None:
    """Raising the profile floor to 0.80 revokes an old 0.75-floor snapshot."""

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1CONSERVATIVE",
            title="正式推荐词已完成但没达到保守惊喜门槛",
            source="search",
            relevance_score=0.77,
            pool_expression="这条可以放在正常推荐里慢慢看。",
            pool_topic_label="正常推荐",
            style_key="deep_focus",
            topic_group="复杂系统",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        profile = _build_profile()
        initially_scored = await engine.precompute_delight_scores(profile=profile, limit=10)

        assert initially_scored == 1
        assert db.get_delight_candidate(min_delight_score=0.75) is not None
        assert db.get_pool_candidates(limit=10, max_per_topic_group=0) == []

        profile.preferences.exploration_openness = 0.2
        rescored = await engine.precompute_delight_scores(profile=profile, limit=10)

        assert rescored == 1
        assert db.get_delight_candidate(min_delight_score=0.80) is None
        snapshot = db.conn.execute(
            """
            SELECT delight_reason, delight_hook
            FROM content_cache
            WHERE bvid = 'BV1CONSERVATIVE'
            """
        ).fetchone()
        assert snapshot is not None
        assert snapshot["delight_reason"] == ""
        assert snapshot["delight_hook"] == ""
        rows = db.get_pool_candidates(limit=10, max_per_topic_group=0)
        assert [row["bvid"] for row in rows] == ["BV1CONSERVATIVE"]


@pytest.mark.asyncio
async def test_precompute_delight_scores_adds_bounded_visual_cover_bonus() -> None:
    """When image embedding is active, an on-style cover lifts delight_score by
    a small bounded bonus — reusing the warmed URL-keyed vector (no re-fetch).
    """

    class _ImageEmb:
        multimodal_enabled = True
        supports_image_embedding = True
        similarity_threshold = 0.82

        def __init__(self) -> None:
            self.fetched = False

        def image_embedding_active(self) -> bool:
            return True

        async def embed(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0]

        def lookup_cached_image(self, cache_key: str) -> list[float]:
            # Simulate the discovery warmer having already stored the vector:
            # same direction as the interest anchor → cosine 1.0 → full bonus.
            return [1.0, 0.0, 0.0]

        async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
            self.fetched = True
            return [1.0, 0.0, 0.0]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1COVER",
            title="纪录片：一件瓷器的百年旅程",
            source="search",
            relevance_score=0.91,
            pool_expression="这条会沿着一件瓷器，把百年工艺脉络慢慢拆给你看。",
            pool_topic_label="工艺脉络",
            cover_url="https://i0.hdslb.com/bfs/archive/cover.jpg",
        )
        emb = _ImageEmb()
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=emb,  # type: ignore[arg-type]
        )
        try:
            scored = await engine.precompute_delight_scores(profile=_build_profile(), limit=10)
            assert scored == 1
            candidate = db.get_delight_candidate(min_delight_score=0.70)
            assert candidate is not None
            # 0.91 + full 0.05 bonus, clamped to <= 1.0.
            assert candidate["delight_score"] == pytest.approx(0.96)
            # Warm cache hit means the cold fetch/embed path never ran.
            assert emb.fetched is False
        finally:
            db.close()


@pytest.mark.asyncio
async def test_precompute_delight_scores_no_visual_bonus_when_inactive() -> None:
    """Cover present but image embedding inactive => delight_score unchanged."""

    class _TextOnlyEmb:
        multimodal_enabled = False
        supports_image_embedding = False
        similarity_threshold = 0.82

        def image_embedding_active(self) -> bool:
            return False

        async def embed(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0]

        async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
            raise AssertionError("embed_image must not run when inactive")

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "BV1PLAIN",
            title="纪录片：一件瓷器的百年旅程",
            source="search",
            relevance_score=0.91,
            pool_expression="这条会沿着一件瓷器，把百年工艺脉络慢慢拆给你看。",
            pool_topic_label="工艺脉络",
            cover_url="https://i0.hdslb.com/bfs/archive/cover.jpg",
        )
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_TextOnlyEmb(),  # type: ignore[arg-type]
        )
        try:
            scored = await engine.precompute_delight_scores(profile=_build_profile(), limit=10)
            assert scored == 1
            candidate = db.get_delight_candidate(min_delight_score=0.70)
            assert candidate is not None
            assert candidate["delight_score"] == pytest.approx(0.91)
        finally:
            db.close()


class _CoverVisualEmb:
    """Fake multimodal embedding service for serve()/delight cover-visual tests.

    ``embed`` gives every text anchor the same direction; ``lookup_cached``
    returns [] so MMR is skipped (base tuple ranking is exercised); cover
    vectors come from a URL-keyed map. ``embed_image`` raises so a test fails
    loudly if serve() ever tries to fetch a cover on its hot path.
    """

    multimodal_enabled = True
    supports_image_embedding = True
    similarity_threshold = 0.82

    def __init__(self, key_to_vec: dict[str, list[float]], *, active: bool = True) -> None:
        self._map = key_to_vec
        self._active = active

    def image_embedding_active(self) -> bool:
        return self._active

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def lookup_cached(self, text: str) -> list[float]:
        return []

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        return list(self._map.get(cache_key, []))

    async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
        raise AssertionError("serve() hot path must be lookup-only (no cover fetch)")


@pytest.mark.asyncio
async def test_serve_cover_visual_bonus_reorders_when_active() -> None:
    """When multimodal embedding is on, an on-style cover's bounded bonus can
    overtake a slightly-higher-relevance candidate — consistent with delight.
    Off (default), the ranking is unchanged.
    """
    from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

    align_url = "https://i0.hdslb.com/bfs/archive/align.jpg"
    plain_url = "https://i0.hdslb.com/bfs/archive/plain.jpg"
    key_map = {
        image_embedding_cache_key_for_url(align_url): [1.0, 0.0, 0.0],  # cos≈1 → +0.05
        image_embedding_cache_key_for_url(plain_url): [0.0, 1.0, 0.0],  # cos≈0 → +0.0
    }

    def _seed(db: Database) -> None:
        # ALIGN has LOWER base relevance; only the visual bonus can lift it
        # above PLAIN — so the assertion is robust to timestamp/bvid tiebreaks.
        _seed_visible(
            db,
            "BV1ALIGN",
            title="对味封面",
            source="search",
            relevance_score=0.80,
            cover_url=align_url,
        )
        _seed_visible(
            db,
            "BV1PLAIN",
            title="普通封面",
            source="search",
            relevance_score=0.83,
            cover_url=plain_url,
        )

    # Active → bonus flips ALIGN above PLAIN.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "a.db")
        db.initialize()
        _seed(db)
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_CoverVisualEmb(key_map, active=True),  # type: ignore[arg-type]
        )
        try:
            recs = await engine.serve(_build_profile(), limit=1)
        finally:
            db.close()
        assert [r.content.bvid for r in recs] == ["BV1ALIGN"]

    # Inactive (text-only / default) → no bonus, higher-relevance PLAIN wins.
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "b.db")
        db.initialize()
        _seed(db)
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_CoverVisualEmb(key_map, active=False),  # type: ignore[arg-type]
        )
        try:
            recs = await engine.serve(_build_profile(), limit=1)
        finally:
            db.close()
        assert [r.content.bvid for r in recs] == ["BV1PLAIN"]


@pytest.mark.asyncio
async def test_visual_bonus_map_empty_when_inactive() -> None:
    from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

    url = "https://i0.hdslb.com/bfs/archive/x.jpg"
    key_map = {image_embedding_cache_key_for_url(url): [1.0, 0.0, 0.0]}
    cands = [DiscoveredContent(bvid="BVX", title="t", cover_url=url)]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "c.db")
        db.initialize()
        active = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_CoverVisualEmb(key_map, active=True),  # type: ignore[arg-type]
        )
        inactive = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_CoverVisualEmb(key_map, active=False),  # type: ignore[arg-type]
        )
        try:
            assert (await active._visual_bonus_map(cands, _build_profile())).get(
                cands[0].scoring_key, 0.0
            ) > 0.0
            assert await inactive._visual_bonus_map(cands, _build_profile()) == {}
        finally:
            db.close()


@pytest.mark.asyncio
async def test_visual_bonus_map_withheld_when_pool_mostly_unwarmed() -> None:
    """Fairness guard: right after enabling multimodal on an existing pool, most
    covers aren't warmed yet. serve() must withhold the bonus for the whole batch
    (rather than favour the warmed minority) until coverage is sufficient.
    """
    from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

    urls = [f"https://i0.hdslb.com/bfs/archive/{i}.jpg" for i in range(3)]
    cands = [DiscoveredContent(bvid=f"BV{i}", title="t", cover_url=urls[i]) for i in range(3)]

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "d.db")
        db.initialize()
        try:
            # Only 1/3 warmed → coverage 0.33 < 0.6 → withhold everything.
            one = {image_embedding_cache_key_for_url(urls[0]): [1.0, 0.0, 0.0]}
            engine = RecommendationEngine(
                llm=_DummyLLM(),
                database=db,
                embedding_service=_CoverVisualEmb(one, active=True),  # type: ignore[arg-type]
            )
            assert await engine._visual_bonus_map(cands, _build_profile()) == {}

            # All 3 warmed → coverage 1.0 → bonus applies.
            full = {image_embedding_cache_key_for_url(u): [1.0, 0.0, 0.0] for u in urls}
            engine_full = RecommendationEngine(
                llm=_DummyLLM(),
                database=db,
                embedding_service=_CoverVisualEmb(full, active=True),  # type: ignore[arg-type]
            )
            applied = await engine_full._visual_bonus_map(cands, _build_profile())
            assert len(applied) == 3 and all(v > 0.0 for v in applied.values())
        finally:
            db.close()


@pytest.mark.asyncio
async def test_prewarm_pool_covers_backfills_unwarmed_only(monkeypatch) -> None:
    """Pool cover prewarm embeds old content whose cover was never warmed
    (multimodal enabled after admission), skips already-warmed and cover-less.
    """
    from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url

    warm_url = "https://i0.hdslb.com/bfs/archive/warm.jpg"
    cold_url = "https://i0.hdslb.com/bfs/archive/cold.jpg"
    warm_key = image_embedding_cache_key_for_url(warm_url)

    class _Emb:
        multimodal_enabled = True
        supports_image_embedding = True

        def __init__(self) -> None:
            self.embedded_keys: list[str] = []

        def image_embedding_active(self) -> bool:
            return True

        def lookup_cached_image(self, key: str) -> list[float]:
            return [0.1, 0.0] if key == warm_key else []

        async def embed_image(
            self, image_bytes: bytes, *, mime_type: str = "image/jpeg", cache_key: str = ""
        ) -> list[float]:
            self.embedded_keys.append(cache_key)
            return [0.2, 0.3]

    async def fake_prepare(cover_url, *, max_px, quality, timeout_seconds):
        return b"jpeg-bytes", "image/jpeg"

    monkeypatch.setattr(
        "openbiliclaw.discovery.multimodal.prepare_cover_bytes_for_embedding", fake_prepare
    )

    emb = _Emb()
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "e.db")
        db.initialize()
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=emb,  # type: ignore[arg-type]
        )
        cands = [
            DiscoveredContent(bvid="BVWARM", title="t", cover_url=warm_url),
            DiscoveredContent(bvid="BVCOLD", title="t", cover_url=cold_url),
            DiscoveredContent(bvid="BVNONE", title="t", cover_url=""),
        ]
        try:
            warmed = await engine._prewarm_pool_covers(cands)
        finally:
            db.close()

    assert warmed == 1  # only the cold-missing cover got embedded
    assert emb.embedded_keys == [image_embedding_cache_key_for_url(cold_url)]


@pytest.mark.asyncio
async def test_precompute_delight_scores_uses_dynamic_pool_top_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(40):
            _seed_visible(
                db,
                f"BV1DYN{index:02d}",
                title=f"动态样本 {index}",
                relevance_score=0.50 + (index * 0.01),
                relevance_reason="pool sample",
                topic_group="动态样本",
                pool_topic_label="动态样本",
            )
        _seed_visible(
            db,
            "BV1MID",
            title="不够惊喜的中等匹配",
            relevance_score=0.72,
            relevance_reason="按旧阈值会通过",
            topic_group="中等匹配",
            pool_topic_label="中等匹配",
            pool_expression="这条按旧阈值会被写成惊喜文案。",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        scored = await engine.precompute_delight_scores(
            profile=_build_profile(),
            limit=50,
        )

        assert scored > 0
        row = db.conn.execute(
            """
            SELECT delight_score, delight_reason, delight_hook
            FROM content_cache
            WHERE bvid = 'BV1MID'
            """
        ).fetchone()
        assert row is not None
        assert float(row["delight_score"]) == pytest.approx(0.72)
        assert row["delight_reason"] == ""
        assert row["delight_hook"] == ""


@pytest.mark.asyncio
async def test_precompute_delight_scores_resyncs_legacy_stale_delight_score() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(40):
            _seed_visible(
                db,
                f"BV1DYN{index:02d}",
                title=f"动态样本 {index}",
                relevance_score=0.50 + (index * 0.01),
                relevance_reason="pool sample",
                topic_group="动态样本",
                pool_topic_label="动态样本",
            )
        _seed_visible(
            db,
            "BV1STALE",
            title="旧分数但新相关度很高",
            relevance_score=0.92,
            relevance_reason="Evo 已判断它进入当前 Top 10%",
            topic_group="旧分数迁移",
            pool_topic_label="旧分数迁移",
            pool_expression="这条应该按新的 Evo relevance 重新成为惊喜候选。",
        )
        db.update_delight_score(
            "BV1STALE",
            delight_score=0.72,
            delight_reason="旧阈值留下的理由",
            delight_hook="旧钩子",
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        scored = await engine.precompute_delight_scores(
            profile=_build_profile(),
            limit=50,
        )

        assert scored > 0
        row = db.conn.execute(
            """
            SELECT delight_score, delight_reason, delight_hook
            FROM content_cache
            WHERE bvid = 'BV1STALE'
            """
        ).fetchone()
        assert row is not None
        assert float(row["delight_score"]) == pytest.approx(0.92)
        assert row["delight_reason"] == "这条应该按新的 Evo relevance 重新成为惊喜候选。"
        assert row["delight_hook"] == "旧分数迁移"


# ── Fix 3: serve-window platform floor ──────────────────────────────


class _PlatformFloorStubDB:
    """Minimal DB stub exercising ``_apply_platform_floor`` in isolation."""

    def __init__(
        self,
        servable_platforms: list[str],
        platform_rows: dict[str, list[dict[str, Any]]],
    ) -> None:
        self._servable_platforms = servable_platforms
        self._platform_rows = platform_rows
        self.fetch_calls: list[tuple[str, int]] = []

    def list_servable_pool_platforms(self, *, xhs_self_nickname: str = "") -> list[str]:
        return list(self._servable_platforms)

    def get_pool_candidates_for_platform(
        self, platform: str, limit: int = 5, *, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        self.fetch_calls.append((platform, limit))
        return [dict(row) for row in self._platform_rows.get(platform, [])][:limit]


def test_apply_platform_floor_tops_up_missing_platform() -> None:
    stub = _PlatformFloorStubDB(
        servable_platforms=["bilibili", "zhihu"],
        platform_rows={
            "zhihu": [{"bvid": "ZH01", "title": "zhihu answer", "source_platform": "zhihu"}]
        },
    )
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]
    window = [
        DiscoveredContent(bvid=f"BV{i:02d}", title=f"bili {i}", source_platform="bilibili")
        for i in range(5)
    ]

    result = engine._apply_platform_floor(window)

    tokens = {RecommendationEngine._platform_token(item) for item in result}
    assert "zhihu" in tokens
    assert any(item.bvid == "ZH01" for item in result)
    # Only the missing platform is topped up, capped at 5 rows.
    assert stub.fetch_calls == [("zhihu", 5)]


def test_apply_platform_floor_skips_single_platform_pool() -> None:
    stub = _PlatformFloorStubDB(
        servable_platforms=["bilibili"],
        platform_rows={"zhihu": [{"bvid": "ZH01", "title": "unused", "source_platform": "zhihu"}]},
    )
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]
    window = [DiscoveredContent(bvid="BV01", title="bili", source_platform="bilibili")]

    result = engine._apply_platform_floor(window)

    assert [item.bvid for item in result] == ["BV01"]
    # Single-platform pool must not issue any per-platform query.
    assert stub.fetch_calls == []


def test_apply_platform_floor_no_fetch_when_all_platforms_present() -> None:
    stub = _PlatformFloorStubDB(
        servable_platforms=["bilibili", "zhihu"],
        platform_rows={"zhihu": [{"bvid": "ZH99", "title": "unused", "source_platform": "zhihu"}]},
    )
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]
    window = [
        DiscoveredContent(bvid="BV01", title="bili", source_platform="bilibili"),
        DiscoveredContent(bvid="ZH01", title="zhihu", source_platform="zhihu"),
    ]

    result = engine._apply_platform_floor(window)

    assert [item.bvid for item in result] == ["BV01", "ZH01"]
    assert stub.fetch_calls == []


def test_get_pool_candidates_for_platform_returns_only_that_platform() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "pool.db")
        db.initialize()
        for i in range(3):
            _seed_visible(
                db,
                f"BV{i:02d}",
                title=f"bili {i}",
                source="search",
                source_platform="bilibili",
                relevance_score=0.9,
            )
        _seed_visible(
            db,
            "ZH01",
            title="zhihu servable",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_id="zh1",
            content_url="https://www.zhihu.com/question/1/answer/1",
            # Its own topic_group: the canonical available set applies a global
            # per-group window, so sharing 测试分组 with three higher-scoring
            # bilibili rows would (correctly) leave zhihu with zero inventory.
            topic_group="知乎问答",
            relevance_score=0.85,
        )

        rows = db.get_pool_candidates_for_platform("zhihu", limit=5)

        assert [row["bvid"] for row in rows] == ["ZH01"]
        assert all(str(row.get("source_platform")) == "zhihu" for row in rows)
        # The strict reader and the advertised inventory must agree.
        assert db.load_pool_platform_availability().by_platform["zhihu"] == 1


def test_get_pool_candidates_for_platform_never_exceeds_advertised_inventory() -> None:
    """A row the topic window excludes is not stock, so it must not be served.

    Regression guard for the "tab says 0 but the picker still hands one out"
    split brain: the strict reader used to run its own servable query without
    the per-group window that ``count_pool_candidates`` applies.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "pool.db")
        db.initialize()
        for i in range(3):
            _seed_visible(
                db,
                f"BV{i:02d}",
                title=f"bili {i}",
                source="search",
                source_platform="bilibili",
                relevance_score=0.9,
            )
        _seed_visible(
            db,
            "ZH01",
            title="zhihu crowded out of the shared topic window",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_id="zh1",
            content_url="https://www.zhihu.com/question/1/answer/1",
            relevance_score=0.85,
        )

        availability = db.load_pool_platform_availability()

        assert availability.by_platform.get("zhihu", 0) == 0
        assert db.get_pool_candidates_for_platform("zhihu", limit=5) == []
        assert db.list_servable_pool_platforms() == ["bilibili"]
        assert availability.total_available == db.count_pool_candidates() == 3


def test_get_pool_candidates_for_platform_excludes_non_servable_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "pool.db")
        db.initialize()
        # Fully-classified zhihu row — genuinely servable.
        _seed_visible(
            db,
            "ZHOK",
            title="ok",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_url="https://www.zhihu.com/question/1/answer/1",
            relevance_score=0.85,
        )
        # Non-servable zhihu row: no pool_expression / pool_topic_label, so
        # serve() could never load it. cache_content leaves them empty.
        db.cache_content(
            "ZHBAD",
            title="bad",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_url="https://www.zhihu.com/question/2/answer/2",
            relevance_score=0.85,
            style_key="tutorial",
            topic_group="g",
        )

        rows = db.get_pool_candidates_for_platform("zhihu", limit=5)

        assert [row["bvid"] for row in rows] == ["ZHOK"]


def test_list_servable_pool_platforms_returns_distinct_tokens() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "pool.db")
        db.initialize()
        _seed_visible(
            db,
            "BV01",
            title="b",
            source="search",
            source_platform="bilibili",
            relevance_score=0.9,
        )
        _seed_visible(
            db,
            "BV02",
            title="b2",
            source="search",
            source_platform="bilibili",
            relevance_score=0.9,
        )
        _seed_visible(
            db,
            "ZH01",
            title="z",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_url="https://www.zhihu.com/question/1/answer/1",
            relevance_score=0.85,
        )

        assert db.list_servable_pool_platforms() == ["bilibili", "zhihu"]


# ── Platform-scoped recommendation requests ─────────────────────────


def _seed_platform_mix(db: Database) -> None:
    """Seed a mixed pool with per-row topic groups so nothing is windowed out."""
    for index in range(4):
        _seed_visible(
            db,
            f"BVMIX{index:02d}",
            title=f"B 站候选 {index}",
            source="search",
            source_platform="bilibili",
            topic_group=f"B 组 {index}",
            pool_expression=f"B 站候选 {index} 的文案。",
            relevance_score=0.95 - index / 100,
        )
    for index in range(4):
        _seed_visible(
            db,
            f"ZHMIX{index:02d}",
            title=f"知乎候选 {index}",
            source="zhihu-extension-task",
            source_platform="zhihu",
            content_id=f"zh-mix-{index}",
            content_url=f"https://www.zhihu.com/question/{index}/answer/{index}",
            topic_group=f"知乎组 {index}",
            pool_expression=f"知乎候选 {index} 的文案。",
            relevance_score=0.90 - index / 100,
        )


@pytest.mark.asyncio
async def test_serve_with_source_platform_returns_only_that_platform() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            recommendations = await engine.serve(
                _build_profile(),
                limit=4,
                source_platform="zhihu",
            )

            assert recommendations
            assert {item.content.source_platform for item in recommendations} == {"zhihu"}
            # Still a real serve: copy is read from the pool and history is written.
            assert all(item.expression for item in recommendations)
            history = {row["bvid"] for row in db.get_recommendations(limit=20)}
            assert history == {item.content.bvid for item in recommendations}
            assert all(bvid.startswith("ZHMIX") for bvid in history)
        finally:
            db.close()


@pytest.mark.asyncio
async def test_serve_with_source_platform_normalizes_aliases() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            recommendations = await engine.serve(
                _build_profile(),
                limit=4,
                source_platform="zh",
            )

            assert recommendations
            assert {item.content.source_platform for item in recommendations} == {"zhihu"}
        finally:
            db.close()


@pytest.mark.asyncio
async def test_serve_without_platform_keeps_mixed_batch() -> None:
    """The no-platform path must keep its existing cross-platform behaviour."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            recommendations = await engine.serve(_build_profile(), limit=8)

            platforms = {item.content.source_platform for item in recommendations}
            assert platforms == {"bilibili", "zhihu"}
        finally:
            db.close()


@pytest.mark.asyncio
async def test_serve_scoped_to_empty_platform_returns_empty_without_leaking() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            recommendations = await engine.serve(
                _build_profile(),
                limit=5,
                source_platform="reddit",
            )

            # No cross-platform floor top-up may rescue an empty platform.
            assert recommendations == []
            assert db.get_recommendations(limit=10) == []
        finally:
            db.close()


class _SnapshotSpyDB:
    """Database double recording how the serve snapshot is requested."""

    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot
        self.snapshot_kwargs: list[dict[str, Any]] = []

    async def load_pool_serve_snapshot_async(self, **kwargs: Any) -> Any:
        self.snapshot_kwargs.append(dict(kwargs))
        return self._snapshot

    async def persist_pool_serve_async(
        self, items: list[dict[str, Any]], shown_bvids: list[str]
    ) -> Any:
        from openbiliclaw.storage.database import PoolServePersistResult

        return PoolServePersistResult(recommendation_ids=tuple(range(1, len(items) + 1)))


class _PartialTemporalCommitDB(_SnapshotSpyDB):
    """Simulate one selected row expiring between snapshot and final commit."""

    async def persist_pool_serve_async(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> Any:
        from openbiliclaw.storage.database import PoolServePersistResult

        assert [str(item["bvid"]) for item in items] == shown_bvids
        committed = [bvid for bvid in shown_bvids if bvid == "BVCOMMITTED"]
        stale = [bvid for bvid in shown_bvids if bvid != "BVCOMMITTED"]
        return PoolServePersistResult(
            recommendation_ids=(101,) if committed else (),
            committed_bvids=tuple(committed),
            temporally_stale_bvids=tuple(stale),
        )


class _SkippedFinalCommitDB(_SnapshotSpyDB):
    """Simulate another process consuming the selected row before commit."""

    async def persist_pool_serve_async(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> Any:
        from openbiliclaw.storage.database import PoolServePersistResult

        return PoolServePersistResult(
            recommendation_ids=(),
            committed_bvids=(),
            skipped_bvids=tuple(shown_bvids),
        )


def _snapshot_with(rows: list[dict[str, Any]]) -> Any:
    from openbiliclaw.storage.database import PoolServeSnapshot

    return PoolServeSnapshot(
        readiness={"available": len(rows), "raw": len(rows), "pending": 0},
        candidate_rows=tuple(rows),
        loaded_count=len(rows),
        platform_topups=(),
        seen_bvids=frozenset(),
        curator_signals=(),
        feedback_signals=(),
    )


def _pool_row(bvid: str, platform: str) -> dict[str, Any]:
    return {
        "bvid": bvid,
        "title": f"{platform} {bvid}",
        "source_platform": platform,
        "source": f"{platform}-task",
        "content_id": bvid,
        "content_url": f"https://example.com/{bvid}",
        "relevance_score": 0.9,
        "pool_expression": f"{bvid} 的文案。",
        "pool_topic_label": "主题",
        "style_key": "tutorial",
        "topic_group": f"组 {bvid}",
    }


@pytest.mark.asyncio
async def test_serve_omits_platform_keyword_when_no_scope_requested() -> None:
    """Old fakes/adapters must keep seeing the historical call shape."""
    stub = _SnapshotSpyDB(_snapshot_with([_pool_row("BV01", "bilibili")]))
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    await engine.serve(_build_profile(), limit=1)

    assert stub.snapshot_kwargs
    assert "source_platform" not in stub.snapshot_kwargs[0]


@pytest.mark.asyncio
async def test_serve_forwards_canonical_platform_to_snapshot_loader() -> None:
    stub = _SnapshotSpyDB(_snapshot_with([_pool_row("ZH01", "zhihu")]))
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    await engine.serve(_build_profile(), limit=1, source_platform="zh")

    assert stub.snapshot_kwargs[0]["source_platform"] == "zhihu"


@pytest.mark.asyncio
async def test_serve_returns_only_rows_confirmed_by_final_temporal_commit() -> None:
    rows = [_pool_row("BVEXPIRED", "bilibili"), _pool_row("BVCOMMITTED", "bilibili")]
    stub = _PartialTemporalCommitDB(_snapshot_with(rows))
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    result = await engine.serve_with_result(_build_profile(), limit=2)

    assert [item.content.bvid for item in result.items] == ["BVCOMMITTED"]
    assert result.items[0].recommendation_id == 101
    assert engine._last_served_bvids == frozenset({"BVCOMMITTED"})
    assert result.pool_counts_after["available"] == 0


@pytest.mark.asyncio
async def test_serve_inventory_accounts_for_rows_consumed_before_final_commit() -> None:
    stub = _SkippedFinalCommitDB(_snapshot_with([_pool_row("BVCONSUMED", "bilibili")]))
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    result = await engine.serve_with_result(_build_profile(), limit=1)

    assert result.items == []
    assert result.pool_counts_after["available"] == 0
    assert result.pool_counts_after["raw"] == 0
    assert engine._last_served_bvids == frozenset()


@pytest.mark.asyncio
async def test_serve_rejects_cross_platform_rows_leaked_by_the_snapshot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A scoped batch never leaks another platform, and says so loudly."""
    stub = _SnapshotSpyDB(
        _snapshot_with([_pool_row("ZH01", "zhihu"), _pool_row("BV99", "bilibili")])
    )
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    caplog.set_level(logging.ERROR)
    recommendations = await engine.serve(_build_profile(), limit=5, source_platform="zhihu")

    assert [item.content.bvid for item in recommendations] == ["ZH01"]
    assert "BV99" in caplog.text
    assert "zhihu" in caplog.text


@pytest.mark.asyncio
async def test_snapshot_serving_applies_strict_publication_date_filter() -> None:
    """The real snapshot path must enforce strict Bilibili date eligibility."""
    in_range = _pool_row("BVINRANGE", "bilibili")
    in_range["published_at"] = "2026-01-15T12:00:00+00:00"
    out_of_range = _pool_row("BVOUTOFRANGE", "bilibili")
    out_of_range["published_at"] = "2025-12-31T12:00:00+00:00"
    stub = _SnapshotSpyDB(_snapshot_with([in_range, out_of_range]))
    curator = PoolCurator(
        object(),  # type: ignore[arg-type]
        publication_preference=PublicationDatePreference(
            preset="custom",
            start_date="2026-01-01",
            end_date="2026-01-31",
            weight=1.0,
        ),
    )
    engine = RecommendationEngine(
        llm=_DummyLLM(),
        database=stub,  # type: ignore[arg-type]
        curator=curator,
    )

    result = await engine.serve_with_result(_build_profile(), limit=2)

    assert [item.content.bvid for item in result.items] == ["BVINRANGE"]
    assert stub.snapshot_kwargs


def test_pool_rows_derive_platform_from_source_when_platform_is_missing() -> None:
    engine = RecommendationEngine(
        llm=_DummyLLM(),
        database=object(),  # type: ignore[arg-type]
    )

    [item] = engine._rows_to_discovered(
        [
            {
                "bvid": "YT_LEGACY",
                "source": "youtube-search",
                "source_platform": "",
            }
        ]
    )

    assert item.source_platform == "youtube"
    assert item.source_strategy == "youtube-search"


class _LegacyCompatDB:
    """Adapter without isolated snapshots — exercises the compatibility path."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.platform_calls: list[str] = []
        self.plain_calls: list[int] = []
        self.inserted: list[dict[str, Any]] = []
        self.insert_calls = 0
        self.shown_calls: list[list[str]] = []

    def count_pool_readiness(self, **_kwargs: Any) -> dict[str, int]:
        return {"available": len(self._rows), "raw": len(self._rows), "pending": 0}

    def get_pool_candidates(self, limit: int = 20, **_kwargs: Any) -> list[dict[str, Any]]:
        self.plain_calls.append(limit)
        return [dict(row) for row in self._rows][:limit]

    def get_pool_candidates_for_platform(
        self, platform: str, limit: int = 5, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        self.platform_calls.append(platform)
        rows = [dict(row) for row in self._rows if row["source_platform"] == platform]
        return rows[:limit]

    def list_servable_pool_platforms(self, **_kwargs: Any) -> list[str]:
        return sorted({str(row["source_platform"]) for row in self._rows})

    def get_recent_viewed_bvids(self) -> set[str]:
        return set()

    def batch_insert_recommendations(self, rows: list[dict[str, Any]]) -> list[int]:
        self.insert_calls += 1
        self.inserted.extend(rows)
        return list(range(1, len(rows) + 1))

    def mark_pool_items_shown(self, bvids: list[str]) -> None:
        self.shown_calls.append(list(bvids))


@pytest.mark.asyncio
async def test_serve_compatibility_path_scopes_to_requested_platform() -> None:
    stub = _LegacyCompatDB([_pool_row("BV01", "bilibili"), _pool_row("ZH01", "zhihu")])
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    recommendations = await engine.serve(_build_profile(), limit=5, source_platform="zhihu")

    assert [item.content.bvid for item in recommendations] == ["ZH01"]
    assert stub.platform_calls == ["zhihu"]
    # Strict mode must not fall back to the cross-platform window.
    assert stub.plain_calls == []


@pytest.mark.asyncio
async def test_serve_compatibility_path_keeps_legacy_shape_without_scope() -> None:
    stub = _LegacyCompatDB([_pool_row("BV01", "bilibili"), _pool_row("ZH01", "zhihu")])
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]

    await engine.serve(_build_profile(), limit=5)

    assert stub.plain_calls  # the historical cross-platform window
    assert stub.platform_calls == []  # platform floor sees both platforms present


@pytest.mark.asyncio
async def test_realtime_legacy_serve_rechecks_temporal_gate_after_provider_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_check = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    row = _pool_row("BVEXPIRING", "bilibili")
    row.update(
        temporal_class="breaking",
        temporal_confidence=0.95,
        temporal_reason="核心价值依赖事件仍在发生",
        temporal_policy_version="v1",
        published_at=(first_check - timedelta(days=3) + timedelta(seconds=1)).isoformat(),
    )
    checks = iter((first_check, first_check + timedelta(seconds=2)))

    class _SteppedDateTime:
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            assert tz is UTC
            return next(checks)

        @classmethod
        def fromisoformat(cls, value: str) -> datetime:
            return datetime.fromisoformat(value)

    monkeypatch.setattr("openbiliclaw.recommendation.engine.datetime", _SteppedDateTime)
    llm = _DummyLLM()
    stub = _LegacyCompatDB([row])
    engine = RecommendationEngine(llm=llm, database=stub)  # type: ignore[arg-type]

    result = await engine.serve_with_result(
        _build_profile(),
        limit=1,
        expression_mode="realtime",
    )
    await asyncio.sleep(0)

    assert len(llm.calls) == 1  # The row crossed its TTL during provider I/O.
    assert result.items == []
    assert stub.insert_calls == 0
    assert stub.inserted == []
    assert stub.shown_calls == []
    assert engine._last_served_bvids == frozenset()


@pytest.mark.asyncio
async def test_serve_scope_keeps_legacy_rows_that_only_a_source_prefix_identifies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy rows carry their platform in ``source``, not ``source_platform``.

    Storage resolves such a row to zhihu via the ``zhihu-`` strategy prefix, so
    it is genuinely zhihu inventory. The engine's scope check must agree — a
    stricter reading of ``source_platform`` alone would classify it as bilibili
    (``_rows_to_discovered`` defaults blanks to bilibili), drop real stock as a
    "leak", and log a phantom contract violation on every scoped request.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(
            db,
            "zhihu-legacy-row",
            title="旧策略知乎回答",
            source="zhihu-hot",
            content_id="zhihu-legacy-row",
            content_url="https://www.zhihu.com/answer/legacy",
            topic_group="知乎组",
            pool_expression="旧策略知乎行的文案。",
            relevance_score=0.9,
        )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            assert db.load_pool_platform_availability().by_platform == {"zhihu": 1}

            caplog.set_level(logging.ERROR)
            recommendations = await engine.serve(
                _build_profile(),
                limit=5,
                source_platform="zhihu",
            )

            assert [item.content.bvid for item in recommendations] == ["zhihu-legacy-row"]
            assert "leaked" not in caplog.text
        finally:
            db.close()


@pytest.mark.asyncio
async def test_unscoped_serve_calls_candidate_loader_with_the_legacy_signature() -> None:
    """Subclasses/doubles overriding the loader keep the pre-feature call shape."""
    stub = _LegacyCompatDB([_pool_row("BV01", "bilibili")])
    engine = RecommendationEngine(llm=_DummyLLM(), database=stub)  # type: ignore[arg-type]
    seen: list[dict[str, Any]] = []

    def legacy_loader(
        profile: Any,
        *,
        limit: int,
        excluded_bvids: frozenset[str],
    ) -> tuple[list[Any], int, int, int, int]:
        seen.append({"limit": limit, "excluded_bvids": excluded_bvids})
        return ([], 0, 0, 0, 0)

    engine._load_filtered_serve_candidates = legacy_loader  # type: ignore[method-assign]

    await engine.serve(_build_profile(), limit=3)

    assert seen and "source_platform" not in seen[0]


@pytest.mark.asyncio
async def test_reshuffle_and_append_forward_platform_scope() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            reshuffled = await engine.reshuffle_recommendations(
                profile=_build_profile(),
                excluded_bvids=[],
                limit=2,
                source_platform="zhihu",
            )
            assert reshuffled
            assert {item.content.source_platform for item in reshuffled} == {"zhihu"}

            appended = await engine.append_recommendations(
                profile=_build_profile(),
                excluded_bvids=[item.content.bvid for item in reshuffled],
                limit=2,
                source_platform="zhihu",
            )
            assert appended
            assert {item.content.source_platform for item in appended} == {"zhihu"}
            assert not {item.content.bvid for item in appended} & {
                item.content.bvid for item in reshuffled
            }
        finally:
            db.close()


@pytest.mark.asyncio
async def test_reshuffle_and_append_with_result_expose_platform_scope() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_platform_mix(db)
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        try:
            reshuffled = await engine.reshuffle_recommendations_with_result(
                profile=_build_profile(),
                excluded_bvids=[],
                limit=2,
                source_platform="bilibili",
            )
            assert {item.content.source_platform for item in reshuffled.items} == {"bilibili"}

            appended = await engine.append_recommendations_with_result(
                profile=_build_profile(),
                excluded_bvids=[item.content.bvid for item in reshuffled.items],
                limit=2,
                source_platform="bilibili",
            )
            assert {item.content.source_platform for item in appended.items} == {"bilibili"}
            # Inventory metadata stays pool-wide, as the API contract expects.
            assert appended.pool_counts_after["available"] >= 0
        finally:
            db.close()


@pytest.mark.asyncio
async def test_reshuffle_exclusions_refill_beyond_the_old_candidate_window() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        for index in range(60):
            _seed_visible(
                db,
                f"BV{index:04d}",
                title=f"候选 {index}",
                up_name=f"UP {index}",
                source="search",
                relevance_score=1.0 - index / 1000,
                relevance_reason=f"候选 {index} 的理由。",
                pool_expression=f"候选 {index} 的预生成文案。",
                pool_topic_label=f"主题 {index}",
                topic_group=f"主题组 {index}",
            )
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)
        excluded = frozenset(f"BV{index:04d}" for index in range(40))

        recommendations = await engine.serve(
            _build_profile(),
            limit=10,
            excluded_bvids=excluded,
            expression_mode="precomputed",
        )

        assert len(recommendations) == 10
        assert not ({item.content.bvid for item in recommendations} & excluded)
        db.close()


@pytest.mark.asyncio
async def test_isolated_pool_snapshot_preserves_legacy_bvid_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_db = Database(tmp_path / "isolated.db")
    legacy_db = Database(tmp_path / "legacy.db")
    isolated_db.initialize()
    legacy_db.initialize()
    for index in range(18):
        for db in (isolated_db, legacy_db):
            _seed_visible(
                db,
                f"BV_ORDER_{index:02d}",
                title=f"顺序候选 {index}",
                source="search",
                relevance_score=0.99 - index / 100,
                topic_group=f"顺序组 {index}",
                pool_topic_label=f"顺序主题 {index}",
            )

    isolated_engine = RecommendationEngine(llm=_DummyLLM(), database=isolated_db)
    legacy_engine = RecommendationEngine(llm=_DummyLLM(), database=legacy_db)
    monkeypatch.setattr(legacy_db, "load_pool_serve_snapshot_async", None)
    monkeypatch.setattr(legacy_db, "persist_pool_serve_async", None)

    isolated_result = await isolated_engine.serve_with_result(_build_profile(), limit=10)
    isolated = isolated_result.items
    legacy = await legacy_engine.serve(_build_profile(), limit=10)
    await asyncio.sleep(0)

    assert [item.content.bvid for item in isolated] == [item.content.bvid for item in legacy]
    assert isolated_result.pool_counts_after == {
        "available": 8,
        "copy_ready": 8,
        "raw": 8,
        "pending": 0,
        "admitted_pending_copy": 0,
        "admitted_pending_available": 0,
        "pending_eval": 0,
        "evaluated_pending": 0,
    }
    assert isolated_result.timings.pool_snapshot_ms > 0
    assert isolated_result.timings.persist_ms > 0
    isolated_db.close()
    legacy_db.close()


@pytest.mark.asyncio
async def test_serve_notifies_inventory_only_after_isolated_shown_commit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(db, "BV1COMMIT", title="durable", relevance_score=0.95)
        committed = asyncio.Event()
        observed_counts: list[int] = []

        async def on_commit() -> None:
            observed_counts.append(db.count_pool_candidates())
            committed.set()

        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            pool_inventory_commit_callback=on_commit,
        )

        recommendations = await engine.serve(_build_profile(), limit=1)
        assert len(recommendations) == 1
        await asyncio.wait_for(committed.wait(), timeout=1)
        assert observed_counts == [0]
        db.close()


@pytest.mark.asyncio
async def test_serve_does_not_notify_inventory_when_isolated_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(db, "BV1FAIL", title="failure", relevance_score=0.95)
        callback_calls = 0

        def on_commit() -> None:
            nonlocal callback_calls
            callback_calls += 1

        async def fail_persist(
            _rows: list[dict[str, object]],
            _bvids: list[str],
        ) -> None:
            raise RuntimeError("write failed")

        monkeypatch.setattr(db, "persist_pool_serve_async", fail_persist)
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            pool_inventory_commit_callback=on_commit,
        )

        with pytest.raises(RuntimeError, match="write failed"):
            await engine.serve(_build_profile(), limit=1)
        await asyncio.sleep(0)
        assert callback_calls == 0
        db.close()


@pytest.mark.asyncio
async def test_serve_isolated_commit_notifies_after_shown_commit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(db, "BV1SYNC", title="sync", relevance_score=0.95)
        observed_counts: list[int] = []
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            pool_inventory_commit_callback=lambda: observed_counts.append(
                db.count_pool_candidates()
            ),
        )
        recommendations = await engine.serve(_build_profile(), limit=1)
        assert len(recommendations) == 1
        await asyncio.sleep(0)
        assert observed_counts == [0]
        db.close()


@pytest.mark.asyncio
async def test_serve_isolated_commit_does_not_fail_when_commit_callback_fails() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        _seed_visible(db, "BV1CALLBACK", title="callback", relevance_score=0.95)

        def fail_callback() -> None:
            raise RuntimeError("callback failed")

        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            pool_inventory_commit_callback=fail_callback,
        )
        recommendations = await engine.serve(_build_profile(), limit=1)
        assert len(recommendations) == 1
        await asyncio.sleep(0)
        assert db.count_pool_candidates() == 0
        db.close()


@pytest.mark.asyncio
async def test_serve_final_exclusion_blocks_platform_floor_reintroduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)
        allowed = DiscoveredContent(
            bvid="BV1ALLOWED",
            title="允许候选",
            up_name="UP A",
            source_strategy="search",
            relevance_score=0.8,
            relevance_reason="允许候选理由。",
            pool_expression="允许候选文案。",
            pool_topic_label="允许主题",
        )
        blocked = DiscoveredContent(
            bvid="BV1BLOCKED",
            title="应排除候选",
            up_name="UP B",
            source_strategy="search",
            relevance_score=0.9,
            relevance_reason="应排除候选理由。",
            pool_expression="应排除候选文案。",
            pool_topic_label="排除主题",
        )
        monkeypatch.setattr(
            engine,
            "_pool_readiness_counts",
            lambda: {"available": 2, "raw": 2, "pending": 0},
        )
        monkeypatch.setattr(db, "load_pool_serve_snapshot_async", None)
        monkeypatch.setattr(db, "persist_pool_serve_async", None)
        monkeypatch.setattr(engine, "_load_pool_candidates", lambda *, limit: [allowed])
        monkeypatch.setattr(
            engine,
            "_apply_platform_floor",
            lambda candidates: [blocked, *candidates],
        )

        recommendations = await engine.serve(
            _build_profile(),
            limit=1,
            excluded_bvids=frozenset({"BV1BLOCKED"}),
            expression_mode="precomputed",
        )

        assert [item.content.bvid for item in recommendations] == ["BV1ALLOWED"]
        db.close()


def test_rows_to_discovered_round_trips_all_engagement_stats() -> None:
    """池子整理(classify_pool_backlog)经 row → dataclass → cache_content 重写行;
    mapper 漏读任何互动字段都会把该字段清零(与封面被空值抹掉同族缺陷)。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_DummyLLM(), database=db)

        stats = {
            "view_count": 14000,
            "like_count": 673,
            "favorite_count": 1283,
            "collect_count": 1283,
            "comment_count": 65,
            "share_count": 7,
            "danmaku_count": 21,
            "reply_count": 3,
            "retweet_count": 2,
            "bookmark_count": 9,
        }
        db.cache_content(
            "BV1stats",
            title="互动数据齐全的视频",
            author_name="Rayman小何",
            cover_url="//i2.hdslb.com/bfs/archive/x.jpg",
            published_at="2026-07-08T06:30:00Z",
            published_label="3 天前",
            temporal_class="versioned",
            temporal_confidence=0.91,
            temporal_reason="内容依赖具体软件版本",
            temporal_policy_version="v1",
            source="search",
            relevance_score=0.9,
            **stats,
        )
        row = dict(
            db.conn.execute("SELECT * FROM content_cache WHERE bvid = ?", ("BV1stats",)).fetchone()
        )

        (item,) = engine._rows_to_discovered([row])
        kwargs = item.to_cache_kwargs()

        for field_name, expected in stats.items():
            assert kwargs[field_name] == expected, field_name
        assert kwargs["author_name"] == "Rayman小何"
        assert kwargs["published_at"] == "2026-07-08T06:30:00Z"
        assert kwargs["published_label"] == "3 天前"
        assert kwargs["temporal_class"] == "versioned"
        assert kwargs["temporal_confidence"] == 0.91
        assert kwargs["temporal_reason"] == "内容依赖具体软件版本"
        assert kwargs["temporal_policy_version"] == "v1"
        db.close()


class _PoisonExpressionLLM:
    """Answers with the whole batch nested under ``expression`` (real shape)."""

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
        self.calls.append({"user_input": user_input})
        return LLMResponse(
            content=json.dumps(
                {
                    "expression": [
                        {"expression": "文案A", "topic_label": "标签A"},
                        {"expression": "文案B", "topic_label": "标签B"},
                    ],
                    "topic_label": "游戏AI应用",
                },
                ensure_ascii=False,
            ),
            model="dummy",
        )


@pytest.mark.asyncio
async def test_generate_expression_rejects_nested_batch_payload() -> None:
    """A list-valued expression must never be str()'d into card copy.

    Regression: users saw ``[{'expression': ..., 'topic_label': ...}]`` as the
    recommendation text because the repr passed the non-empty check.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        engine = RecommendationEngine(llm=_PoisonExpressionLLM(), database=db)

        result = await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1POISON",
                title="用 AI 做游戏原型",
                up_name="独立开发",
                description="从零搭一个原型。",
                relevance_score=0.8,
                style_key="hands_on",
            ),
            _build_profile(),
        )

        expression, topic_label = result if isinstance(result, tuple) else (result, "")
        for value in (expression, topic_label):
            assert "topic_label" not in value
            assert "expression" not in value
            assert not value.lstrip().startswith(("[", "{"))


def test_evo_delight_reason_uses_only_formal_user_facing_copy() -> None:
    """Evaluator diagnostics must never leak into delight card copy."""
    # pool_expression wins first.
    assert (
        RecommendationEngine._evo_delight_reason(
            DiscoveredContent(
                bvid="BV1", title="x", pool_expression="现成文案", relevance_reason=""
            )
        )
        == "现成文案"
    )
    # Internal evaluator reason is deliberately not a user-facing fallback.
    assert (
        RecommendationEngine._evo_delight_reason(
            DiscoveredContent(bvid="BV2", title="x", relevance_reason="这条正合你的胃口。")
        )
        == ""
    )
    # Topic metadata and empty candidates likewise wait for formal copy.
    assert (
        RecommendationEngine._evo_delight_reason(
            DiscoveredContent(bvid="BV3", title="x", relevance_reason="", topic_group="航天")
        )
        == ""
    )
    assert (
        RecommendationEngine._evo_delight_reason(
            DiscoveredContent(bvid="BV4", title="x", relevance_reason="")
        )
        == ""
    )


async def test_worker_mode_serve_commits_before_return_and_ignores_stale_snapshot(tmp_path):
    """Isolation must not bypass the final DB commit or reuse consumed candidates."""
    from openbiliclaw.runtime.serve_outbox import ServeOutbox
    from openbiliclaw.runtime.serve_snapshot import ServeSnapshotStore

    db = Database(tmp_path / "serve.db")
    db.initialize()
    for index in range(6):
        _seed_visible(
            db,
            f"BVatomic{index}",
            title=f"Unique topic {index}",
            source="search",
            relevance_score=0.9,
            topic_group=f"group{index}",
            pool_expression="Prepared copy",
            pool_topic_label=f"topic{index}",
        )
    store = ServeSnapshotStore(tmp_path / "snapshot.json")
    store.save(await db.load_pool_serve_snapshot_async(limit=40))
    outbox = ServeOutbox(tmp_path / "outbox.jsonl")
    engine = RecommendationEngine(
        llm=_DummyLLM(),
        database=db,
        serve_snapshot_store=store,
        serve_outbox=outbox,
    )
    try:
        batches = [await engine.serve_with_result(_build_profile(), limit=2) for _ in range(3)]
        ids = [rec.content.bvid for batch in batches for rec in batch.items]
        assert len(ids) == len(set(ids)) == 6
        assert all(rec.recommendation_id > 0 for batch in batches for rec in batch.items)
        assert db.count_pool_candidates() == 0
        assert len(db.get_recommendations(limit=10)) == 6
        assert outbox.count() == 0
    finally:
        db.close()


@pytest.mark.parametrize("partial", [False, True])
def test_mmr_reuses_each_cosine_pair_only_within_one_batch(monkeypatch, partial) -> None:
    from collections import Counter

    import openbiliclaw.llm.embedding as embedding_module

    candidates = [
        DiscoveredContent(
            bvid=f"BVCOS{i}",
            title=f"Content {i}",
            source_strategy="search",
            topic_group=f"topic-{i % 7}",
            style_key=["deep_focus", "hands_on", "quick_scan"][i % 3],
            relevance_score=0.9 - i * 0.001,
        )
        for i in range(30)
    ]
    embeddings = {
        item.scoring_key: [0.0 if i % 4 == 0 else float(j == i % 16) for j in range(16)]
        for i, item in enumerate(candidates)
        if not partial or i % 3 != 0
    }
    calls: Counter[tuple[int, int]] = Counter()
    original = embedding_module.cosine_similarity

    def counted(a, b):
        calls[(id(a), id(b))] += 1
        return original(a, b)

    monkeypatch.setattr(embedding_module, "cosine_similarity", counted)
    first = RecommendationEngine._select_diversified_batch(
        candidates,
        limit=10,
        embeddings=embeddings,
    )
    assert len(first) == 10
    assert calls
    assert max(calls.values()) == 1, "including zero similarity, repeated pairs are reused"
    calls.clear()
    second = RecommendationEngine._select_diversified_batch(
        candidates,
        limit=10,
        embeddings=embeddings,
    )
    assert [item.bvid for item in second] == [item.bvid for item in first]
    assert calls, "a later batch must calculate against its own vectors"
    assert max(calls.values()) == 1


@pytest.mark.asyncio
async def test_generate_expression_forwards_reply_style_into_tone_block() -> None:
    """issue #255: RecommendationEngine.reply_style reaches the expression tone block."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db, reply_style="像损友一样毒舌")

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1STYLE255",
                title="讲透贸易逆差的底层逻辑",
                up_name="经济观察",
                description="从历史和制度角度解释问题。",
                relevance_score=0.89,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert "- 回复风格: 像损友一样毒舌" in user_input


@pytest.mark.asyncio
async def test_generate_expression_default_reply_style_leaves_prompt_untouched() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        db.initialize()
        llm = _DummyLLM()
        engine = RecommendationEngine(llm=llm, database=db)

        await engine.generate_expression(
            DiscoveredContent(
                bvid="BV1NODEFAULT255",
                title="讲透贸易逆差的底层逻辑",
                up_name="经济观察",
                description="从历史和制度角度解释问题。",
                relevance_score=0.89,
            ),
            _build_profile(),
        )

        user_input = str(llm.calls[0]["user_input"])
        assert "- 回复风格:" not in user_input


@pytest.mark.asyncio
async def test_precompute_batch_forwards_reply_style_into_tone_block() -> None:
    """issue #255: the batch expression path injects reply_style too."""

    class _BatchLLM:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def complete_structured_task(
            self,
            *,
            system_instruction: str,
            user_input: str,
            **_kwargs: object,
        ) -> LLMResponse:
            self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "bvid": "BV1BATCH255",
                            "expression": "这条会接住你最近想把问题想透的状态。",
                            "topic_label": "想透的状态",
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
        llm = _BatchLLM()
        engine = RecommendationEngine(llm=llm, database=db, reply_style="多用短句")
        batch = [
            DiscoveredContent(
                bvid="BV1BATCH255",
                title="结构化工作流复盘",
                up_name="效率实验室",
                description="如何把复杂问题拆成稳定系统。",
                relevance_score=0.9,
            )
        ]

        await engine._precompute_batch(batch, _build_profile())

        user_input = str(llm.calls[0]["user_input"])
        assert "- 回复风格: 多用短句" in user_input
