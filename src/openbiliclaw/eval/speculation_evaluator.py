"""SpeculationEvaluator — multi-dimension scoring of speculative interest quality.

Evaluates speculations across 7 dimensions: plausibility, novelty,
specificity, confirmation rate, no-hallucination, diversity, and
persona resonance. Supports automated (LLM + persona judge),
simulated-event, and human-feedback evaluation modes.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.eval.persona_judge import PersonaJudgment
    from openbiliclaw.soul.profile import OnionProfile
    from openbiliclaw.soul.speculator import SpeculativeInterest

logger = logging.getLogger(__name__)

# Dimension weights for overall score
_DIM_WEIGHTS: dict[str, float] = {
    "plausibility": 0.20,
    "novelty": 0.15,
    "specificity": 0.10,
    "confirmation_rate": 0.15,
    "no_hallucination": 0.10,
    "diversity": 0.15,
    "persona_resonance": 0.15,
}

# All dimensions map to the same prompt (only LLM-controlled variable)
SPECULATION_FIELD_TO_PARAM: dict[str, str] = {
    k: "speculation_generation_prompt" for k in _DIM_WEIGHTS
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SpeculationScore:
    """Score for a single speculation."""

    domain: str = ""
    plausibility: float = 0.0
    novelty: float = 0.0
    specificity: float = 0.0
    no_hallucination: float = 0.0
    persona_resonance: float = 0.0
    overall: float = 0.0
    details: str = ""


@dataclass
class SpeculationEvalReport:
    """Complete evaluation report for one speculation generation run."""

    speculation_scores: list[SpeculationScore] = field(default_factory=list)
    confirmation_rate: float = 0.0
    diversity_score: float = 0.0
    mean_plausibility: float = 0.0
    mean_novelty: float = 0.0
    mean_specificity: float = 0.0
    mean_no_hallucination: float = 0.0
    mean_persona_resonance: float = 0.0
    overall_score: float = 0.0
    worst_dimensions: list[dict[str, Any]] = field(default_factory=list)
    attributions: list[str] = field(default_factory=list)
    persona_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "overall_score": self.overall_score,
            "confirmation_rate": self.confirmation_rate,
            "diversity_score": self.diversity_score,
            "mean_plausibility": self.mean_plausibility,
            "mean_novelty": self.mean_novelty,
            "mean_specificity": self.mean_specificity,
            "mean_no_hallucination": self.mean_no_hallucination,
            "mean_persona_resonance": self.mean_persona_resonance,
            "speculation_scores": [
                {
                    "domain": s.domain,
                    "plausibility": s.plausibility,
                    "novelty": s.novelty,
                    "specificity": s.specificity,
                    "no_hallucination": s.no_hallucination,
                    "persona_resonance": s.persona_resonance,
                    "overall": s.overall,
                    "details": s.details,
                }
                for s in self.speculation_scores
            ],
            "worst_dimensions": self.worst_dimensions,
            "attributions": self.attributions,
            "persona_id": self.persona_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _no_hallucination_score(
    domain: str,
    confirmed_domains: list[str],
) -> float:
    """Check if speculation restates an existing confirmed interest. 0.0 = hallucination."""
    domain_lower = domain.lower()
    for confirmed in confirmed_domains:
        confirmed_lower = confirmed.lower()
        if domain_lower in confirmed_lower or confirmed_lower in domain_lower:
            return 0.0
    # Token overlap check
    from openbiliclaw.soul.speculator import _tokenize

    domain_tokens = _tokenize(domain)
    for confirmed in confirmed_domains:
        conf_tokens = _tokenize(confirmed)
        if domain_tokens and conf_tokens:
            overlap = len(domain_tokens & conf_tokens) / len(domain_tokens)
            if overlap >= 0.6:
                return 0.2
    return 1.0


def _confirmation_rate_score(rate: float) -> float:
    """Score confirmation rate. Target is 0.3-0.7; penalize both extremes."""
    return max(0.0, 1.0 - 2.0 * abs(rate - 0.5))


def _score_diversity(speculations: list[SpeculativeInterest]) -> float:
    """Score how dispersed the speculations are across different categories.

    Combines category entropy (how spread across categories) with
    pairwise domain distance (how different the domain names are).
    """
    if len(speculations) <= 1:
        return 1.0

    # 1. Category entropy (unique categories → higher score)
    categories = [s.category.strip().lower() for s in speculations if s.category.strip()]
    if categories:
        cat_counts: dict[str, int] = {}
        for cat in categories:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        total = len(categories)
        entropy = -sum((c / total) * math.log2(c / total) for c in cat_counts.values() if c > 0)
        max_entropy = math.log2(total) if total > 1 else 1.0
        cat_score = entropy / max_entropy if max_entropy > 0 else 0.0
    else:
        cat_score = 0.0

    # 2. Pairwise domain distance (character-level overlap for Chinese)
    domains = [s.domain for s in speculations]
    pair_scores: list[float] = []
    for i in range(len(domains)):
        for j in range(i + 1, len(domains)):
            pair_scores.append(_domain_distance(domains[i], domains[j]))

    dist_score = sum(pair_scores) / len(pair_scores) if pair_scores else 1.0

    return round(cat_score * 0.5 + dist_score * 0.5, 4)


def _domain_distance(a: str, b: str) -> float:
    """Character-level distance between two domain names. 0=identical, 1=no overlap."""
    a_lower = re.sub(r"\s+", "", a.lower())
    b_lower = re.sub(r"\s+", "", b.lower())
    if not a_lower or not b_lower:
        return 1.0
    if a_lower == b_lower:
        return 0.0
    # Substring containment
    if a_lower in b_lower or b_lower in a_lower:
        shorter = min(len(a_lower), len(b_lower))
        longer = max(len(a_lower), len(b_lower))
        return min(1.0, (longer - shorter) / max(longer, 1) * 1.5)
    # Character set overlap (works for Chinese without word segmentation)
    chars_a = set(a_lower)
    chars_b = set(b_lower)
    overlap = len(chars_a & chars_b) / max(len(chars_a | chars_b), 1)
    return 1.0 - overlap


async def _llm_eval_speculation(
    spec_domain: str,
    spec_reason: str,
    profile_context: str,
) -> dict[str, float]:
    """Use LLM to score plausibility, novelty, and specificity."""
    try:
        from openbiliclaw.eval.agents import collect_json
        from openbiliclaw.llm.sponsored_router import sponsored_enabled

        if sponsored_enabled():
            from .sponsored_agents import agent_options
        else:
            agent_options = import_module("claude_agent_sdk").ClaudeAgentOptions

        result = await collect_json(
            prompt=(
                f"评估以下推测兴趣的质量。\n\n"
                f"用户画像:\n{profile_context[:1000]}\n\n"
                f"推测方向: {spec_domain}\n"
                f"推理依据: {spec_reason}\n\n"
                f"请从三个维度评分 (0-1):\n"
                f"1. plausibility: 心理桥接推理是否合理？能否从已有兴趣自然推导出来？\n"
                f"2. novelty: 是否真正跨域？(0.1=已有兴趣的简单延伸, 0.9=创造性的交叉推理)\n"
                f"3. specificity: 能否在B站搜到这类内容？(0.1=太抽象, 0.9=可直接搜索)\n\n"
                f'{{"plausibility": 0.0, "novelty": 0.0, "specificity": 0.0, '
                f'"reasoning": "简要说明"}}'
            ),
            options=agent_options(
                system_prompt=(
                    "你是推测兴趣质量评估器。客观评分：完全合理=0.8+，"
                    "部分合理=0.5-0.7，不合理=0-0.4。只返回 JSON。"
                ),
                max_turns=1,
            ),
            max_retries=1,
            label="spec_llm_eval",
        )
        return {
            "plausibility": max(0.0, min(1.0, float(result.get("plausibility", 0.5)))),
            "novelty": max(0.0, min(1.0, float(result.get("novelty", 0.5)))),
            "specificity": max(0.0, min(1.0, float(result.get("specificity", 0.5)))),
        }
    except Exception:
        logger.warning("LLM eval failed for %s, using defaults", spec_domain)
        return {"plausibility": 0.5, "novelty": 0.5, "specificity": 0.5}


# ---------------------------------------------------------------------------
# SpeculationEvaluator
# ---------------------------------------------------------------------------


class SpeculationEvaluator:
    """Evaluate speculative interest generation quality."""

    def __init__(self, *, dim_weights: dict[str, float] | None = None) -> None:
        self._weights = dim_weights or dict(_DIM_WEIGHTS)

    async def evaluate(
        self,
        speculations: list[SpeculativeInterest],
        profile: OnionProfile,
        confirmation_results: dict[str, bool] | None = None,
        persona_judgment: PersonaJudgment | None = None,
    ) -> SpeculationEvalReport:
        """Full automated evaluation of speculations against a profile.

        Args:
            speculations: Generated speculative interests to evaluate.
            profile: The persona profile used for generation.
            confirmation_results: Optional domain → promoted mapping from
                simulated event observation.
            persona_judgment: Optional PersonaJudgment from persona_judge.
                When provided, per-speculation resonance scores are used.
                When absent, persona_resonance defaults to 0.5.
        """
        if not speculations:
            return SpeculationEvalReport(timestamp=datetime.now().isoformat())

        # Collect confirmed interest domains for hallucination check
        confirmed_domains = [d.domain for d in profile.interest.likes]
        profile_ctx = profile.to_llm_context()

        # Build resonance lookup from persona judgment
        resonance_map: dict[str, float] = {}
        if persona_judgment is not None:
            for verdict in persona_judgment.verdicts:
                resonance_map[verdict.domain] = verdict.resonance_score

        scores: list[SpeculationScore] = []
        for spec in speculations:
            # LLM scoring for plausibility/novelty/specificity
            llm_scores = await _llm_eval_speculation(
                spec.domain,
                spec.reason,
                profile_ctx,
            )
            # Algorithmic no-hallucination check
            nh = _no_hallucination_score(spec.domain, confirmed_domains)
            # Persona resonance
            resonance = resonance_map.get(spec.domain, 0.5)

            per_spec_overall = (
                llm_scores["plausibility"] * 0.30
                + llm_scores["novelty"] * 0.25
                + llm_scores["specificity"] * 0.15
                + nh * 0.10
                + resonance * 0.20
            )
            scores.append(
                SpeculationScore(
                    domain=spec.domain,
                    plausibility=llm_scores["plausibility"],
                    novelty=llm_scores["novelty"],
                    specificity=llm_scores["specificity"],
                    no_hallucination=nh,
                    persona_resonance=resonance,
                    overall=round(per_spec_overall, 4),
                )
            )

        # Confirmation rate
        conf_rate = 0.5  # default if no simulation data
        if confirmation_results:
            total = len(confirmation_results)
            promoted = sum(1 for v in confirmation_results.values() if v)
            conf_rate = promoted / total if total > 0 else 0.5
        conf_rate_score = _confirmation_rate_score(conf_rate)

        # Diversity (algorithmic)
        diversity = _score_diversity(speculations)

        # Means
        n = len(scores)
        mean_p = sum(s.plausibility for s in scores) / n
        mean_n = sum(s.novelty for s in scores) / n
        mean_s = sum(s.specificity for s in scores) / n
        mean_nh = sum(s.no_hallucination for s in scores) / n
        mean_pr = sum(s.persona_resonance for s in scores) / n

        overall = (
            self._weights["plausibility"] * mean_p
            + self._weights["novelty"] * mean_n
            + self._weights["specificity"] * mean_s
            + self._weights["confirmation_rate"] * conf_rate_score
            + self._weights["no_hallucination"] * mean_nh
            + self._weights["diversity"] * diversity
            + self._weights["persona_resonance"] * mean_pr
        )

        # Worst dimensions
        dim_scores: list[tuple[str, float]] = [
            ("plausibility", mean_p),
            ("novelty", mean_n),
            ("specificity", mean_s),
            ("confirmation_rate", conf_rate_score),
            ("no_hallucination", mean_nh),
            ("diversity", diversity),
            ("persona_resonance", mean_pr),
        ]
        dim_scores.sort(key=lambda t: t[1])
        worst: list[dict[str, Any]] = [
            {"dimension": name, "score": score} for name, score in dim_scores[:3]
        ]

        attributions = [
            f"{d['dimension']} ({d['score']:.2f}) -> speculation_generation_prompt"
            for d in worst
            if d["score"] < 0.7
        ]

        return SpeculationEvalReport(
            speculation_scores=scores,
            confirmation_rate=round(conf_rate, 4),
            diversity_score=round(diversity, 4),
            mean_plausibility=round(mean_p, 4),
            mean_novelty=round(mean_n, 4),
            mean_specificity=round(mean_s, 4),
            mean_no_hallucination=round(mean_nh, 4),
            mean_persona_resonance=round(mean_pr, 4),
            overall_score=round(overall, 4),
            worst_dimensions=worst,
            attributions=attributions,
            timestamp=datetime.now().isoformat(),
        )

    async def evaluate_with_human(
        self,
        speculations: list[SpeculativeInterest],
        human_feedback: dict[str, dict[str, float]],
    ) -> SpeculationEvalReport:
        """Build report from human per-speculation feedback.

        human_feedback format:
        {
            "博弈论科普": {"plausibility": 0.8, "novelty": 0.6, "specificity": 0.9},
            ...
        }
        """
        scores: list[SpeculationScore] = []
        for spec in speculations:
            fb = human_feedback.get(spec.domain, {})
            if not isinstance(fb, dict):
                continue
            p = float(fb.get("plausibility", 0.5))
            n = float(fb.get("novelty", 0.5))
            s = float(fb.get("specificity", 0.5))
            pr = float(fb.get("persona_resonance", 0.5))
            per_overall = p * 0.30 + n * 0.25 + s * 0.15 + pr * 0.30
            scores.append(
                SpeculationScore(
                    domain=spec.domain,
                    plausibility=p,
                    novelty=n,
                    specificity=s,
                    no_hallucination=1.0,  # human review assumed no hallucination
                    persona_resonance=pr,
                    overall=round(per_overall, 4),
                    details=str(fb.get("note", "")),
                )
            )

        if not scores:
            return SpeculationEvalReport(timestamp=datetime.now().isoformat())

        diversity = _score_diversity(speculations)
        count = len(scores)
        mean_p = sum(s.plausibility for s in scores) / count
        mean_n = sum(s.novelty for s in scores) / count
        mean_s = sum(s.specificity for s in scores) / count
        mean_pr = sum(s.persona_resonance for s in scores) / count
        overall = mean_p * 0.30 + mean_n * 0.20 + mean_s * 0.15 + mean_pr * 0.20 + diversity * 0.15

        human_dim_scores: list[tuple[str, float]] = [
            ("plausibility", mean_p),
            ("novelty", mean_n),
            ("specificity", mean_s),
            ("diversity", diversity),
            ("persona_resonance", mean_pr),
        ]
        human_dim_scores.sort(key=lambda t: t[1])
        worst_h: list[dict[str, Any]] = [
            {"dimension": name, "score": score} for name, score in human_dim_scores[:3]
        ]

        return SpeculationEvalReport(
            speculation_scores=scores,
            diversity_score=round(diversity, 4),
            mean_plausibility=round(mean_p, 4),
            mean_novelty=round(mean_n, 4),
            mean_specificity=round(mean_s, 4),
            mean_no_hallucination=1.0,
            mean_persona_resonance=round(mean_pr, 4),
            overall_score=round(overall, 4),
            worst_dimensions=worst_h,
            attributions=[
                f"{d['dimension']} ({d['score']:.2f}) -> speculation_generation_prompt"
                for d in worst_h
                if d["score"] < 0.7
            ],
            timestamp=datetime.now().isoformat(),
        )
