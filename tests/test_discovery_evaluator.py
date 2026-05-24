"""Tests for discovery evaluator scoring functions."""

from __future__ import annotations

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.eval.discovery_evaluator import (
    DISCOVERY_FIELD_TO_PARAM,
    DiscoveryEvalReport,
    DiscoveryEvaluator,
    StrategyEvalReport,
    _score_diversity,
    _score_explanation_quality,
    _score_no_echo_chamber,
    _score_novelty,
)
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _build_profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="深度内容爱好者",
        core_traits=["理性", "好奇"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="纪录片", category="知识", weight=0.9),
                InterestTag(name="摄影", category="创作", weight=0.8),
            ],
        ),
    )


def _make_items(
    topics: list[str],
    styles: list[str] | None = None,
    reasons: list[str] | None = None,
    scores: list[float] | None = None,
) -> list[DiscoveredContent]:
    items: list[DiscoveredContent] = []
    for i, topic in enumerate(topics):
        items.append(
            DiscoveredContent(
                bvid=f"BV{i:03d}",
                title=f"Video about {topic}",
                topic_key=topic,
                style_key=(styles[i] if styles and i < len(styles) else ""),
                relevance_reason=(reasons[i] if reasons and i < len(reasons) else ""),
                relevance_score=(scores[i] if scores and i < len(scores) else 0.5),
                source_strategy="search",
            )
        )
    return items


# ---------------------------------------------------------------------------
# Algorithmic scoring functions
# ---------------------------------------------------------------------------


def test_diversity_empty() -> None:
    assert _score_diversity([]) == 0.0


def test_diversity_single_item() -> None:
    items = _make_items(["纪录片"])
    assert _score_diversity(items) == 0.0


def test_diversity_all_same_topic() -> None:
    items = _make_items(["纪录片", "纪录片", "纪录片"])
    assert _score_diversity(items) == 0.0


def test_diversity_all_different() -> None:
    items = _make_items(["纪录片", "摄影", "游戏", "音乐"])
    score = _score_diversity(items)
    assert score > 0.9


def test_diversity_mixed() -> None:
    items = _make_items(["纪录片", "纪录片", "摄影", "游戏"])
    score = _score_diversity(items)
    assert 0.3 < score < 1.0


def test_novelty_empty() -> None:
    profile = _build_profile()
    assert _score_novelty([], profile) == 0.0


def test_novelty_all_known() -> None:
    profile = _build_profile()
    items = _make_items(["纪录片", "纪录片", "摄影"])
    score = _score_novelty(items, profile)
    assert score < 0.2


def test_novelty_all_new() -> None:
    profile = _build_profile()
    items = _make_items(["量子力学", "古典音乐", "园艺"])
    score = _score_novelty(items, profile)
    assert score > 0.8


def test_novelty_mixed() -> None:
    profile = _build_profile()
    items = _make_items(["纪录片", "量子力学", "摄影", "园艺"])
    score = _score_novelty(items, profile)
    assert 0.3 < score < 0.8


def test_no_echo_chamber_single() -> None:
    items = _make_items(["纪录片"])
    assert _score_no_echo_chamber(items) == 1.0


def test_no_echo_chamber_dominated() -> None:
    items = _make_items(["纪录片"] * 8 + ["摄影", "游戏"])
    score = _score_no_echo_chamber(items)
    assert score < 0.3


def test_no_echo_chamber_diverse() -> None:
    items = _make_items(["纪录片", "摄影", "游戏", "音乐", "科技"])
    score = _score_no_echo_chamber(items)
    assert score > 0.7


def test_explanation_quality_empty() -> None:
    assert _score_explanation_quality([]) == 0.0


def test_explanation_quality_all_good() -> None:
    items = _make_items(
        ["a", "b", "c"],
        reasons=[
            "这个视频深度讲解了原理，适合深度学习者",
            "摄影构图入门非常实用，值得一看",
            "游戏攻略详细清晰，条理分明",
        ],
    )
    assert _score_explanation_quality(items) == 1.0


def test_explanation_quality_mixed() -> None:
    items = _make_items(
        ["a", "b", "c"],
        reasons=["这个视频深度讲解了原理，适合深度学习者", "", "短"],
    )
    score = _score_explanation_quality(items)
    assert 0.2 < score < 0.5


# ---------------------------------------------------------------------------
# FIELD_TO_PARAM mapping
# ---------------------------------------------------------------------------


def test_field_to_param_has_all_strategies() -> None:
    strategies = {"search", "trending", "related_chain", "explore", "cross"}
    covered = {key.split(".")[0] for key in DISCOVERY_FIELD_TO_PARAM}
    assert strategies == covered


def test_field_to_param_values_are_valid_prompts() -> None:
    valid_prompts = {
        "search_queries_prompt",
        "trending_rids_prompt",
        "content_evaluation_prompt",
        "explore_domains_prompt",
        "recommendation_expression_prompt",
    }
    for key, value in DISCOVERY_FIELD_TO_PARAM.items():
        assert value in valid_prompts, f"{key} maps to unknown prompt: {value}"


# ---------------------------------------------------------------------------
# Evaluator integration (without LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluator_strategy_no_llm() -> None:
    evaluator = DiscoveryEvaluator(llm_service=None)
    profile = _build_profile()
    items = _make_items(
        ["纪录片", "摄影", "游戏", "音乐"],
        reasons=["深度讲解适合好奇心强的用户"] * 4,
        scores=[0.8, 0.7, 0.6, 0.5],
    )

    report = await evaluator.evaluate_strategy("search", items, profile)

    assert isinstance(report, StrategyEvalReport)
    assert report.strategy_name == "search"
    assert report.item_count == 4
    assert 0.0 < report.overall_score <= 1.0
    assert len(report.dimension_scores) > 0


@pytest.mark.asyncio
async def test_evaluator_all_no_llm() -> None:
    evaluator = DiscoveryEvaluator(llm_service=None)
    profile = _build_profile()

    strategy_results = {
        "search": _make_items(["纪录片", "摄影"], scores=[0.8, 0.7]),
        "trending": _make_items(["热点1", "热点2"], scores=[0.6, 0.5]),
    }

    report = await evaluator.evaluate_all(strategy_results, profile)

    assert isinstance(report, DiscoveryEvalReport)
    assert "search" in report.strategy_reports
    assert "trending" in report.strategy_reports
    assert len(report.cross_strategy_scores) == 1
    assert 0.0 < report.overall_score <= 1.0
    assert report.timestamp


@pytest.mark.asyncio
async def test_evaluator_empty_results() -> None:
    evaluator = DiscoveryEvaluator(llm_service=None)
    profile = _build_profile()

    report = await evaluator.evaluate_strategy("search", [], profile)

    assert report.overall_score == 0.0
    assert report.item_count == 0


@pytest.mark.asyncio
async def test_evaluate_with_human() -> None:
    evaluator = DiscoveryEvaluator()
    items = _make_items(["a", "b"], scores=[0.8, 0.7])
    strategy_results = {"search": items}

    feedback = {
        "search.relevance": {"score": 0.8, "note": "Good matches"},
        "search.diversity": {"score": 0.6, "note": "Could be more diverse"},
        "search.specificity": {"score": 0.7, "note": ""},
        "search.query_quality": {"score": 0.5, "note": "Too generic"},
        "search.no_echo_chamber": {"score": 0.9, "note": ""},
    }

    report = await evaluator.evaluate_with_human(strategy_results, feedback)

    assert isinstance(report, DiscoveryEvalReport)
    assert "search" in report.strategy_reports
    assert report.strategy_reports["search"].overall_score > 0
    assert report.overall_score > 0
