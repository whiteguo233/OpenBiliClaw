"""Delight Scorer — identifies content that would surprise and delight the user.

Computes a composite ``delight_score`` that measures how deeply a piece of
content resonates with the user's soul profile — not just surface interests,
but deep needs, active insight hypotheses, and latent curiosity patterns.

This score is deliberately separate from the PoolCurator's ``rec_score``
(which handles freshness/fatigue/monotony for the regular recommendation
batch). The delight score focuses on **deep resonance**, not recency or
diversity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from openbiliclaw.llm.embedding import SupportsEmbeddingService

logger = logging.getLogger(__name__)


class SupportsDelightCandidate(Protocol):
    bvid: str
    title: str
    description: str
    view_count: int
    like_count: int
    topic_key: str
    topic_group: str
    source_strategy: str
    relevance_score: float


class SupportsRecommendationSignalStore(Protocol):
    def get_recent_recommendation_signals(self, *, limit: int = ...) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class DelightSignals:
    """Individual signal components that compose the delight score."""

    deep_need_alignment: float = 0.0
    insight_resonance: float = 0.0
    novelty_factor: float = 0.0
    quality_indicator: float = 0.0
    exploration_match: float = 0.0


@dataclass(frozen=True)
class DelightWeights:
    """Tuneable weights for the composite delight score."""

    deep_need: float = 0.30
    insight: float = 0.25
    novelty: float = 0.20
    quality: float = 0.10
    exploration: float = 0.15


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD: float = 0.80
_CONSERVATIVE_THRESHOLD: float = 0.90
_LOW_EXPLORATION_OPENNESS: float = 0.3
_DEFAULT_WEIGHTS = DelightWeights()


class DelightScorer:
    """Computes a delight score for content based on deep profile resonance.

    The scorer uses embedding similarity to match content against the user's
    deep_needs and active_insights, combined with novelty and quality signals.
    """

    def __init__(
        self,
        embedding_service: SupportsEmbeddingService | None,
        database: SupportsRecommendationSignalStore,
        *,
        weights: DelightWeights = _DEFAULT_WEIGHTS,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self._embedding = embedding_service
        self._database = database
        self._weights = weights
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def effective_threshold(self, exploration_openness: float) -> float:
        """Return a possibly raised threshold for conservative users."""
        if exploration_openness < _LOW_EXPLORATION_OPENNESS:
            return max(self._threshold, _CONSERVATIVE_THRESHOLD)
        return self._threshold

    async def score(
        self,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> tuple[float, DelightSignals, str]:
        """Compute a delight score for a candidate given the soul profile.

        Returns:
            A tuple of (delight_score, signals, reason_stub).
            reason_stub is a short hint for the LLM to expand into the
            full delight_reason.
        """
        w = self._weights
        signals = await self._compute_signals(candidate, profile)

        score = (
            signals.deep_need_alignment * w.deep_need
            + signals.insight_resonance * w.insight
            + signals.novelty_factor * w.novelty
            + signals.quality_indicator * w.quality
            + signals.exploration_match * w.exploration
        )

        reason_stub = self._build_reason_stub(signals, candidate, profile)
        return (min(1.0, max(0.0, score)), signals, reason_stub)

    async def _compute_signals(
        self,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> DelightSignals:
        """Compute individual delight signal components."""
        content_text = f"{candidate.title} {candidate.description or ''}"

        deep_need = await self._deep_need_alignment(content_text, profile)
        insight = await self._insight_resonance(content_text, profile)
        novelty = self._novelty_factor(candidate)
        quality = self._quality_indicator(candidate)
        exploration = self._exploration_match(candidate, profile, novelty)

        return DelightSignals(
            deep_need_alignment=deep_need,
            insight_resonance=insight,
            novelty_factor=novelty,
            quality_indicator=quality,
            exploration_match=exploration,
        )

    async def _deep_need_alignment(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Score alignment between content and user's deep needs."""
        if self._embedding is None:
            return 0.0

        deep_needs = getattr(profile, "deep_needs", [])
        if not deep_needs:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_sim = 0.0
        for need in deep_needs[:5]:
            need_text = str(need).strip()
            if not need_text:
                continue
            need_vec = await self._embedding.embed(need_text)
            if not need_vec:
                continue
            sim = cosine_similarity(content_vec, need_vec)
            max_sim = max(max_sim, sim)

        # Normalize: similarity 0.5 → 0.0, similarity 1.0 → 1.0
        return max(0.0, min(1.0, (max_sim - 0.5) * 2.0))

    async def _insight_resonance(
        self,
        content_text: str,
        profile: Any,
    ) -> float:
        """Score alignment between content and active insight hypotheses."""
        if self._embedding is None:
            return 0.0

        active_insights = getattr(profile, "active_insights", [])
        if not active_insights:
            return 0.0

        from openbiliclaw.llm.embedding import cosine_similarity

        content_vec = await self._embedding.embed(content_text)
        if not content_vec:
            return 0.0

        max_sim = 0.0
        for insight in active_insights[:5]:
            hypothesis = str(getattr(insight, "hypothesis", "")).strip()
            if not hypothesis:
                continue
            insight_vec = await self._embedding.embed(hypothesis)
            if not insight_vec:
                continue
            sim = cosine_similarity(content_vec, insight_vec)
            # Weight by confidence
            confidence = float(getattr(insight, "confidence", 0.5))
            weighted_sim = sim * (0.5 + confidence * 0.5)
            max_sim = max(max_sim, weighted_sim)

        return max(0.0, min(1.0, (max_sim - 0.4) * 2.5))

    def _novelty_factor(self, candidate: SupportsDelightCandidate) -> float:
        """Score novelty based on discovery strategy and topic freshness."""
        # Explore strategy inherently carries more novelty
        strategy_novelty = {
            "explore": 0.9,
            "trending": 0.5,
            "related_chain": 0.3,
            "search": 0.2,
        }
        base_novelty = strategy_novelty.get(candidate.source_strategy, 0.3)

        # Check how often this topic has been recommended
        signals = self._database.get_recent_recommendation_signals(limit=30)
        topic = (candidate.topic_group or candidate.topic_key).strip().lower()
        if topic and signals:
            topic_count = sum(
                1 for s in signals if str(s.get("topic_key", "")).strip().lower() == topic
            )
            # Penalize if topic has been seen often
            repetition_penalty = min(1.0, topic_count / 5.0)
            base_novelty = base_novelty * (1.0 - repetition_penalty * 0.5)

        return max(0.0, min(1.0, base_novelty))

    @staticmethod
    def _quality_indicator(candidate: SupportsDelightCandidate) -> float:
        """Score content quality from engagement signals."""
        view_count = max(1, candidate.view_count)
        like_count = candidate.like_count

        if view_count < 100:
            return 0.3  # Not enough data

        like_ratio = like_count / view_count
        # Normalize: 0.01 → 0.2, 0.05 → 0.7, 0.10+ → 1.0
        quality = min(1.0, like_ratio * 12.0)

        # Blend with relevance_score
        return quality * 0.5 + candidate.relevance_score * 0.5

    @staticmethod
    def _exploration_match(
        candidate: SupportsDelightCandidate,
        profile: Any,
        novelty: float,
    ) -> float:
        """Score based on user's exploration openness and content novelty."""
        prefs = getattr(profile, "preferences", None)
        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        if exploration_openness > 0.6:
            # Open users delight in novel cross-domain content
            return novelty * exploration_openness
        else:
            # Conservative users delight in deep dives in known domains
            # High relevance in a known domain = deep satisfaction
            depth_signal = candidate.relevance_score * (1.0 - novelty)
            return depth_signal * (1.0 - exploration_openness * 0.5)

    @staticmethod
    def _build_reason_stub(
        signals: DelightSignals,
        candidate: SupportsDelightCandidate,
        profile: Any,
    ) -> str:
        """Build a structured reason stub for LLM expansion."""
        parts: list[str] = []

        if signals.deep_need_alignment >= 0.6:
            deep_needs = getattr(profile, "deep_needs", [])
            if deep_needs:
                parts.append(f"deep_need:{deep_needs[0]}")

        if signals.insight_resonance >= 0.6:
            insights = getattr(profile, "active_insights", [])
            if insights:
                hypothesis = str(getattr(insights[0], "hypothesis", ""))
                if hypothesis:
                    parts.append(f"insight:{hypothesis[:60]}")

        if signals.novelty_factor >= 0.7:
            parts.append(f"novelty:{candidate.source_strategy}")

        if signals.exploration_match >= 0.7:
            parts.append("exploration:cross_domain")

        if not parts:
            parts.append(f"relevance:{candidate.relevance_score:.2f}")

        return "|".join(parts)
