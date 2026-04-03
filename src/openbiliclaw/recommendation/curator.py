"""Pool Curator — recommendation-side scoring independent of Discovery.

Sits between the RecommendationEngine and the database to compute a
composite ``rec_score`` that accounts for freshness, topic fatigue,
source monotony, serendipity, and feedback signals — factors that
Discovery's relevance_score does not capture.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.storage.database import Database


# ---------------------------------------------------------------------------
# Immutable configuration & context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoringWeights:
    """Tuneable weights for the composite rec_score."""

    relevance: float = 0.40
    freshness: float = 0.20
    topic_fatigue: float = 0.15
    source_monotony: float = 0.15
    serendipity: float = 0.10


@dataclass(frozen=True)
class FeedbackSignals:
    """Immutable snapshot of recent feedback for score adjustments."""

    disliked_up_mids: frozenset[int] = field(default_factory=frozenset)
    disliked_topic_keys: frozenset[str] = field(default_factory=frozenset)
    liked_topic_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ScoringContext:
    """Immutable snapshot of recent recommendation history."""

    recent_topic_keys: tuple[str, ...] = ()
    recent_sources: tuple[str, ...] = ()
    feedback: FeedbackSignals = field(default_factory=FeedbackSignals)
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FRESHNESS_HALF_LIFE_DAYS: float = 3.0
_FEEDBACK_DISLIKE_UP_PENALTY: float = 0.20
_FEEDBACK_DISLIKE_TOPIC_PENALTY: float = 0.10
_FEEDBACK_LIKE_TOPIC_BONUS: float = 0.05
_POOL_LOW_THRESHOLD: int = 50


# ---------------------------------------------------------------------------
# PoolCurator
# ---------------------------------------------------------------------------

class PoolCurator:
    """Manages recommendation-side scoring and pool health.

    The curator never mutates its inputs — it returns new score mappings
    that the engine uses as an overlay on top of the raw candidates.
    """

    def __init__(
        self,
        database: Database,
        *,
        weights: ScoringWeights = ScoringWeights(),
        history_window: int = 30,
    ) -> None:
        self._database = database
        self._weights = weights
        self._history_window = history_window

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context(self) -> ScoringContext:
        """Build a scoring context from recent recommendation history."""
        signals = self._database.get_recent_recommendation_signals(
            limit=self._history_window,
        )
        topic_keys = tuple(
            str(row.get("topic_key", "")).strip()
            for row in signals
            if str(row.get("topic_key", "")).strip()
        )
        sources = tuple(
            str(row.get("source", "")).strip()
            for row in signals
            if str(row.get("source", "")).strip()
        )

        feedback_rows = self._database.get_feedback_signals(
            limit=self._history_window,
        )
        disliked_ups: set[int] = set()
        disliked_topics: set[str] = set()
        liked_topics: set[str] = set()
        for row in feedback_rows:
            ftype = str(row.get("feedback_type", "")).strip()
            if ftype == "dislike":
                up_mid = row.get("up_mid")
                if isinstance(up_mid, int) and up_mid > 0:
                    disliked_ups.add(up_mid)
                topic = str(row.get("topic_key", "")).strip()
                if topic:
                    disliked_topics.add(topic)
            elif ftype in ("like", "save"):
                topic = str(row.get("topic_key", "")).strip()
                if topic:
                    liked_topics.add(topic)

        return ScoringContext(
            recent_topic_keys=topic_keys,
            recent_sources=sources,
            feedback=FeedbackSignals(
                disliked_up_mids=frozenset(disliked_ups),
                disliked_topic_keys=frozenset(disliked_topics),
                liked_topic_keys=frozenset(liked_topics),
            ),
        )

    def score_candidates(
        self,
        candidates: list[DiscoveredContent],
        context: ScoringContext,
    ) -> dict[str, float]:
        """Return a bvid → rec_score mapping for the given candidates.

        The returned dict can be passed as ``score_override`` to the
        engine's diversified batch selector.
        """
        w = self._weights
        scores: dict[str, float] = {}
        for item in candidates:
            base = item.relevance_score * w.relevance
            fresh = self._freshness_score(
                item.discovered_at or item.last_scored_at, context.now,
            ) * w.freshness
            fatigue = self._topic_fatigue(
                item.topic_group or item.topic_key, context.recent_topic_keys,
            ) * w.topic_fatigue
            monotony = self._source_monotony(
                item.source_strategy, context.recent_sources,
            ) * w.source_monotony
            bonus = self._serendipity_bonus(item.source_strategy) * w.serendipity

            score = base + fresh - fatigue - monotony + bonus

            # Feedback adjustments (additive, outside weight system)
            score += self._feedback_adjustment(item, context.feedback)

            scores[item.bvid] = max(0.0, score)
        return scores

    def needs_replenishment(self, *, threshold: int = _POOL_LOW_THRESHOLD) -> bool:
        """True when the pool is getting thin."""
        return self._database.count_pool_candidates() < threshold

    def pool_count(self) -> int:
        """Current number of fresh pool candidates."""
        return self._database.count_pool_candidates()

    # ------------------------------------------------------------------
    # Scoring components (all pure functions)
    # ------------------------------------------------------------------

    @staticmethod
    def _freshness_score(timestamp_str: str, now: datetime) -> float:
        """Sigmoid decay: ~1.0 at age 0, ~0.5 at half-life, ~0.1 at 2× half-life."""
        if not timestamp_str:
            return 0.5
        try:
            discovered = datetime.fromisoformat(
                timestamp_str.replace(" ", "T"),
            )
            if discovered.tzinfo is None:
                discovered = discovered.replace(tzinfo=timezone.utc)
        except ValueError:
            return 0.5
        age_days = max(0.0, (now - discovered).total_seconds() / 86400.0)
        return 1.0 / (1.0 + math.exp((age_days - _FRESHNESS_HALF_LIFE_DAYS) / 1.0))

    @staticmethod
    def _topic_fatigue(topic_key: str, recent_topics: tuple[str, ...]) -> float:
        """Normalised frequency of topic_key in recent recommendations."""
        if not topic_key or not recent_topics:
            return 0.0
        count = sum(1 for t in recent_topics if t == topic_key)
        return min(1.0, count / max(1, len(recent_topics)) * 3.0)

    @staticmethod
    def _source_monotony(source: str, recent_sources: tuple[str, ...]) -> float:
        """Normalised frequency of source in recent recommendations."""
        if not source or not recent_sources:
            return 0.0
        count = sum(1 for s in recent_sources if s == source)
        return min(1.0, count / max(1, len(recent_sources)) * 2.5)

    @staticmethod
    def _serendipity_bonus(source_strategy: str) -> float:
        """Bonus for cross-domain exploration content."""
        return 1.0 if source_strategy == "explore" else 0.0

    @staticmethod
    def _feedback_adjustment(
        item: DiscoveredContent,
        feedback: FeedbackSignals,
    ) -> float:
        """Additive score adjustment based on recent user feedback."""
        adj = 0.0
        if item.up_mid and item.up_mid in feedback.disliked_up_mids:
            adj -= _FEEDBACK_DISLIKE_UP_PENALTY
        topic = (item.topic_group or item.topic_key).strip()
        if topic and topic in feedback.disliked_topic_keys:
            adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
        if topic and topic in feedback.liked_topic_keys:
            adj += _FEEDBACK_LIKE_TOPIC_BONUS
        return adj

    async def score_candidates_async(
        self,
        candidates: list[DiscoveredContent],
        context: ScoringContext,
        *,
        embedding_service: object | None = None,
    ) -> dict[str, float]:
        """Async version of score_candidates with embedding-based fatigue/feedback.

        Uses embedding cosine similarity instead of exact string match for
        topic_fatigue and feedback_adjustment when embedding_service is available.
        """
        w = self._weights
        scores: dict[str, float] = {}

        # Pre-embed recent topics and feedback topics for reuse
        _recent_vecs: dict[str, list[float]] = {}
        _disliked_vecs: dict[str, list[float]] = {}
        _liked_vecs: dict[str, list[float]] = {}
        if embedding_service is not None:
            for t in set(context.recent_topic_keys):
                if t.strip():
                    vec = await embedding_service.embed(t)
                    if vec:
                        _recent_vecs[t] = vec
            for t in context.feedback.disliked_topic_keys:
                vec = await embedding_service.embed(t)
                if vec:
                    _disliked_vecs[t] = vec
            for t in context.feedback.liked_topic_keys:
                vec = await embedding_service.embed(t)
                if vec:
                    _liked_vecs[t] = vec

        from openbiliclaw.llm.embedding import cosine_similarity

        for item in candidates:
            base = item.relevance_score * w.relevance
            fresh = self._freshness_score(
                item.discovered_at or item.last_scored_at, context.now,
            ) * w.freshness
            monotony = self._source_monotony(
                item.source_strategy, context.recent_sources,
            ) * w.source_monotony
            bonus = self._serendipity_bonus(item.source_strategy) * w.serendipity

            # Embedding-based topic fatigue
            topic = (item.topic_group or item.topic_key).strip()
            if embedding_service is not None and topic:
                topic_vec = await embedding_service.embed(topic)
                if topic_vec and _recent_vecs:
                    sim_count = sum(
                        1 for rv in _recent_vecs.values()
                        if cosine_similarity(topic_vec, rv) >= embedding_service.similarity_threshold
                    )
                    fatigue = min(1.0, sim_count / max(1, len(context.recent_topic_keys)) * 3.0)
                else:
                    fatigue = self._topic_fatigue(topic, context.recent_topic_keys)
            else:
                fatigue = self._topic_fatigue(topic, context.recent_topic_keys)
            fatigue *= w.topic_fatigue

            score = base + fresh - fatigue - monotony + bonus

            # Embedding-based feedback adjustment
            if embedding_service is not None and topic:
                topic_vec = await embedding_service.embed(topic)
                adj = 0.0
                if item.up_mid and item.up_mid in context.feedback.disliked_up_mids:
                    adj -= _FEEDBACK_DISLIKE_UP_PENALTY
                if topic_vec:
                    for dv in _disliked_vecs.values():
                        if cosine_similarity(topic_vec, dv) >= embedding_service.similarity_threshold:
                            adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
                            break
                    for lv in _liked_vecs.values():
                        if cosine_similarity(topic_vec, lv) >= embedding_service.similarity_threshold:
                            adj += _FEEDBACK_LIKE_TOPIC_BONUS
                            break
                score += adj
            else:
                score += self._feedback_adjustment(item, context.feedback)

            scores[item.bvid] = max(0.0, score)
        return scores
