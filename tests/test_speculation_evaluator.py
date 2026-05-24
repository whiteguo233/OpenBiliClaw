"""Tests for speculation evaluator — diversity scoring and persona resonance."""

from __future__ import annotations

import pytest

from openbiliclaw.eval.persona_judge import (
    PersonaJudgment,
    ResonanceVerdict,
    _fuzzy_match_verdict,
    _parse_verdicts,
)
from openbiliclaw.eval.speculation_evaluator import (
    SpeculationEvaluator,
    _confirmation_rate_score,
    _no_hallucination_score,
    _score_diversity,
)
from openbiliclaw.soul.speculator import SpeculativeInterest

# ---------------------------------------------------------------------------
# _score_diversity
# ---------------------------------------------------------------------------


def _spec(domain: str, category: str = "") -> SpeculativeInterest:
    return SpeculativeInterest(domain=domain, category=category, status="active")


def test_diversity_single_item_returns_one() -> None:
    assert _score_diversity([_spec("博弈论", "知识")]) == 1.0


def test_diversity_all_same_category_scores_low() -> None:
    specs = [
        _spec("博弈论科普", "知识"),
        _spec("纳什均衡讲解", "知识"),
        _spec("策略模型分析", "知识"),
    ]
    score = _score_diversity(specs)
    assert score <= 0.5, f"Same-category diversity should be low, got {score}"


def test_diversity_different_categories_scores_high() -> None:
    specs = [
        _spec("博弈论科普", "知识"),
        _spec("手工木工", "手工"),
        _spec("独立电影", "影视"),
        _spec("户外攀岩", "运动"),
    ]
    score = _score_diversity(specs)
    assert score > 0.7, f"Different-category diversity should be high, got {score}"


def test_diversity_overlapping_domains_penalized() -> None:
    specs = [
        _spec("博弈论科普", "数学"),
        _spec("博弈论应用", "经济"),
    ]
    score = _score_diversity(specs)
    # Categories differ (good) but domains overlap heavily (bad)
    assert score < 0.8


def test_diversity_empty_returns_one() -> None:
    assert _score_diversity([]) == 1.0


# ---------------------------------------------------------------------------
# _confirmation_rate_score
# ---------------------------------------------------------------------------


def test_confirmation_rate_optimal_at_half() -> None:
    assert _confirmation_rate_score(0.5) == 1.0


def test_confirmation_rate_zero_is_zero() -> None:
    assert _confirmation_rate_score(0.0) == 0.0


def test_confirmation_rate_one_is_zero() -> None:
    assert _confirmation_rate_score(1.0) == 0.0


# ---------------------------------------------------------------------------
# _no_hallucination_score
# ---------------------------------------------------------------------------


def test_no_hallucination_exact_overlap() -> None:
    assert _no_hallucination_score("科技", ["科技", "历史"]) == 0.0


def test_no_hallucination_no_overlap() -> None:
    assert _no_hallucination_score("手工木工", ["科技", "历史"]) == 1.0


# ---------------------------------------------------------------------------
# Persona judge parsing
# ---------------------------------------------------------------------------


def test_parse_verdicts_alignment() -> None:
    specs = [
        {"domain": "博弈论"},
        {"domain": "手工木工"},
    ]
    raw = [
        {"domain": "博弈论", "would_click": True, "resonance_score": 0.8, "reasoning": "test"},
        {"domain": "手工木工", "would_click": False, "resonance_score": 0.2, "reasoning": "nah"},
    ]
    verdicts = _parse_verdicts(raw, specs)
    assert len(verdicts) == 2
    assert verdicts[0].domain == "博弈论"
    assert verdicts[0].resonance_score == 0.8
    assert verdicts[1].resonance_score == 0.2


def test_parse_verdicts_missing_fills_default() -> None:
    specs = [{"domain": "博弈论"}, {"domain": "手工木工"}]
    raw = [{"domain": "博弈论", "resonance_score": 0.7}]
    verdicts = _parse_verdicts(raw, specs)
    assert len(verdicts) == 2
    assert verdicts[1].resonance_score == 0.5  # default


def test_fuzzy_match_verdict() -> None:
    verdict_map = {
        "博弈论科普": ResonanceVerdict(domain="博弈论科普", resonance_score=0.8),
    }
    result = _fuzzy_match_verdict("博弈论", verdict_map)
    assert result is not None
    assert result.resonance_score == 0.8


def test_fuzzy_match_no_match() -> None:
    verdict_map = {
        "手工木工": ResonanceVerdict(domain="手工木工", resonance_score=0.3),
    }
    assert _fuzzy_match_verdict("博弈论", verdict_map) is None


# ---------------------------------------------------------------------------
# SpeculationEvaluator.evaluate (without LLM — mock the LLM call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_includes_persona_resonance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify persona judgment scores flow into the evaluation report."""
    from openbiliclaw.eval import speculation_evaluator

    # Mock LLM eval to return fixed scores
    async def _mock_llm_eval(domain: str, reason: str, ctx: str) -> dict[str, float]:
        return {"plausibility": 0.7, "novelty": 0.6, "specificity": 0.8}

    monkeypatch.setattr(speculation_evaluator, "_llm_eval_speculation", _mock_llm_eval)

    from openbiliclaw.soul.profile import OnionProfile

    profile = OnionProfile(personality_portrait="test user")

    speculations = [
        SpeculativeInterest(domain="博弈论", category="数学", reason="test", status="active"),
        SpeculativeInterest(domain="手工木工", category="手工", reason="test2", status="active"),
    ]

    judgment = PersonaJudgment(
        persona_summary="test",
        verdicts=(
            ResonanceVerdict(domain="博弈论", would_click=True, resonance_score=0.9),
            ResonanceVerdict(domain="手工木工", would_click=False, resonance_score=0.2),
        ),
        mean_resonance=0.55,
    )

    evaluator = SpeculationEvaluator()
    report = await evaluator.evaluate(
        speculations,
        profile,
        persona_judgment=judgment,
    )

    assert report.mean_persona_resonance == pytest.approx(0.55, abs=0.01)
    assert report.diversity_score > 0.5  # different categories
    assert report.overall_score > 0


@pytest.mark.asyncio
async def test_evaluate_without_persona_defaults_to_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.eval import speculation_evaluator

    async def _mock_llm_eval(domain: str, reason: str, ctx: str) -> dict[str, float]:
        return {"plausibility": 0.7, "novelty": 0.6, "specificity": 0.8}

    monkeypatch.setattr(speculation_evaluator, "_llm_eval_speculation", _mock_llm_eval)

    from openbiliclaw.soul.profile import OnionProfile

    profile = OnionProfile(personality_portrait="test user")
    speculations = [
        SpeculativeInterest(domain="test", category="cat", reason="r", status="active"),
    ]

    evaluator = SpeculationEvaluator()
    report = await evaluator.evaluate(speculations, profile)

    assert report.mean_persona_resonance == 0.5


@pytest.mark.asyncio
async def test_evaluate_with_human_includes_resonance() -> None:
    speculations = [
        SpeculativeInterest(domain="博弈论", category="数学", reason="t", status="active"),
    ]
    feedback = {
        "博弈论": {
            "plausibility": 0.8,
            "novelty": 0.7,
            "specificity": 0.9,
            "persona_resonance": 0.6,
        },
    }
    evaluator = SpeculationEvaluator()
    report = await evaluator.evaluate_with_human(speculations, feedback)

    assert report.mean_persona_resonance == pytest.approx(0.6, abs=0.01)
    assert report.overall_score > 0
