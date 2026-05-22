"""Pool Curator — recommendation-side scoring independent of Discovery.

Sits between the RecommendationEngine and the database to compute a
composite ``rec_score`` that accounts for freshness, topic fatigue,
source monotony, serendipity, and feedback signals — factors that
Discovery's relevance_score does not capture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.storage.database import Database


# ---------------------------------------------------------------------------
# Immutable configuration & context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringWeights:
    """Tuneable weights for the composite rec_score.

    Serendipity is weighted higher (0.20) to ensure cross-domain explore
    content surfaces in recommendations, not just high-relevance safe picks.

    ``topic_fatigue`` was raised from 0.15 to 0.25 after observing that
    high-relevance candidates for "洛克王国"/"动漫"/etc. kept winning the
    top-K reshuffle batches because the per-key fatigue penalty (~0.045)
    couldn't overcome the relevance weight advantage (~0.28). Combined
    with the steeper fatigue curve (now ``count^1.5/len*5``) and the new
    topic_group axis, the same candidate now takes a 3-4x harder hit
    when it has appeared ≥2 times in recent history.
    """

    relevance: float = 0.30
    freshness: float = 0.20
    topic_fatigue: float = 0.25
    source_monotony: float = 0.15
    serendipity: float = 0.20


@dataclass(frozen=True)
class FeedbackSignals:
    """Immutable snapshot of recent feedback for score adjustments."""

    disliked_up_mids: frozenset[int] = field(default_factory=frozenset)
    disliked_topic_keys: frozenset[str] = field(default_factory=frozenset)
    liked_topic_keys: frozenset[str] = field(default_factory=frozenset)
    # Franchises (e.g. 原神 / 星穹铁道) extracted from disliked items'
    # titles via :mod:`openbiliclaw.recommendation.franchise`. Without
    # this axis, disliking one 原神 video only blocks that exact bvid;
    # other 原神 candidates from related_chain keep coming through. With
    # it the curator subtracts a soft penalty from any candidate whose
    # title hits the same franchise.
    disliked_franchises: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ScoringContext:
    """Immutable snapshot of recent recommendation history."""

    recent_topic_keys: tuple[str, ...] = ()
    recent_topic_groups: tuple[str, ...] = ()
    recent_sources: tuple[str, ...] = ()
    feedback: FeedbackSignals = field(default_factory=FeedbackSignals)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FRESHNESS_HALF_LIFE_DAYS: float = 3.0
_FEEDBACK_DISLIKE_UP_PENALTY: float = 0.20
_FEEDBACK_DISLIKE_TOPIC_PENALTY: float = 0.10
# Softer than topic penalty — franchise propagation is a heuristic
# (substring match on title), so we don't want a single 原神 dislike
# to brick all gaming content forever. With combined fatigue + topic
# penalty, this 0.07 is enough to push 原神 candidates below other
# fresh content but doesn't outright suppress.
_FEEDBACK_DISLIKE_FRANCHISE_PENALTY: float = 0.07
_FEEDBACK_LIKE_TOPIC_BONUS: float = 0.05
_POOL_LOW_THRESHOLD: int = 50
_DEFAULT_WEIGHTS = ScoringWeights()


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
        weights: ScoringWeights = _DEFAULT_WEIGHTS,
        history_window: int = 30,
        marketing_demote_weight: float = 0.0,
    ) -> None:
        self._database = database
        self._weights = weights
        self._history_window = history_window
        # When non-zero, the curator subtracts
        # ``marketing_demote_weight * marketing_score`` from every
        # candidate's composite score. ``marketing_score`` is the
        # output of the 营销号 heuristic (0–1), so the maximum
        # demote applied to any single candidate is the weight
        # itself — i.e. with the default 8.0 a fully clickbait
        # title (score 1.0) loses 8 points off its rec_score,
        # usually enough to push it well below organic peers
        # without removing it from the pool.
        #
        # Defaults to 0.0 so callers that don't opt in (existing
        # tests, in-process usage from other entry points) get
        # backward-compatible behavior. The runtime wires this
        # from Config.recommendation.marketing_filter_demote_weight.
        self._marketing_demote_weight = max(0.0, float(marketing_demote_weight))

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
        topic_groups = tuple(
            str(row.get("topic_group", "")).strip()
            for row in signals
            if str(row.get("topic_group", "")).strip()
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
        # ``franchise_key`` is the LLM-tagged IP / franchise / series
        # column on content_cache (added in v0.3.18). When the user
        # dislikes any item, every other candidate sharing the same
        # franchise_key gets a soft penalty in _feedback_adjustment —
        # so disliking one 原神 video also down-ranks 提瓦特, 蒙德, etc.
        disliked_franchises: set[str] = set()
        for row in feedback_rows:
            ftype = str(row.get("feedback_type", "")).strip()
            if ftype == "dislike":
                up_mid = row.get("up_mid")
                if isinstance(up_mid, int) and up_mid > 0:
                    disliked_ups.add(up_mid)
                topic = str(row.get("topic_key", "")).strip()
                if topic:
                    disliked_topics.add(topic)
                franchise = str(row.get("franchise_key", "")).strip()
                if franchise:
                    disliked_franchises.add(franchise)
            elif ftype in ("like", "save"):
                topic = str(row.get("topic_key", "")).strip()
                if topic:
                    liked_topics.add(topic)

        return ScoringContext(
            recent_topic_keys=topic_keys,
            recent_topic_groups=topic_groups,
            recent_sources=sources,
            feedback=FeedbackSignals(
                disliked_up_mids=frozenset(disliked_ups),
                disliked_topic_keys=frozenset(disliked_topics),
                liked_topic_keys=frozenset(liked_topics),
                disliked_franchises=frozenset(disliked_franchises),
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
        # Local import to avoid loading the pattern bank at module
        # import time for processes that never call into the
        # curator (e.g. CLI subcommands that just print config).
        from openbiliclaw.recommendation.marketing_filter import (
            score_marketing_signal,
        )

        w = self._weights
        marketing_weight = self._marketing_demote_weight
        scores: dict[str, float] = {}
        for item in candidates:
            base = item.relevance_score * w.relevance
            fresh = (
                self._freshness_score(
                    item.discovered_at or item.last_scored_at,
                    context.now,
                )
                * w.freshness
            )
            fatigue = (
                self._combined_topic_fatigue(item, context)
                * w.topic_fatigue
            )
            monotony = (
                self._source_monotony(
                    item.source_strategy,
                    context.recent_sources,
                )
                * w.source_monotony
            )
            bonus = self._serendipity_bonus(item.source_strategy) * w.serendipity

            score = base + fresh - fatigue - monotony + bonus

            # Feedback adjustments (additive, outside weight system)
            score += self._feedback_adjustment(item, context.feedback)

            # 营销号 (clickbait) demote — only when caller opted in
            # with a non-zero demote weight at construction time.
            # Cheap (string-only heuristic) but skip the call
            # entirely in the disabled case to keep this path tight.
            if marketing_weight > 0:
                marketing = score_marketing_signal(
                    str(getattr(item, "title", "") or ""),
                    description=str(getattr(item, "description", "") or ""),
                )
                if marketing.score > 0:
                    score -= marketing_weight * marketing.score

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
                discovered = discovered.replace(tzinfo=UTC)
        except ValueError:
            return 0.5
        age_days = max(0.0, (now - discovered).total_seconds() / 86400.0)
        return 1.0 / (1.0 + math.exp((age_days - _FRESHNESS_HALF_LIFE_DAYS) / 1.0))

    @staticmethod
    def _topic_fatigue(topic: str, recent_topics: tuple[str, ...]) -> float:
        """Saturating fatigue from how often *topic* appeared in recent history.

        Curve (with the canonical ``len(recent)=30``):
          count=0 → 0.0          count=1 → 0.17
          count=2 → 0.47         count=3 → 0.87
          count≥4 → saturates at 1.0

        Derived from ``count^1.5 / len * 5``: linear-style first-occurrence
        cost, but quadratic-ish growth thereafter so a topic that's been
        served twice already gets a noticeably bigger penalty than one that
        was served once. The previous ``count/len*3`` curve only hit 1.0 at
        count≈10/30, which let high-relevance candidates re-win indefinitely
        even after appearing 3 times in a row.
        """
        if not topic or not recent_topics:
            return 0.0
        count = sum(1 for t in recent_topics if t == topic)
        if count == 0:
            return 0.0
        return float(min(1.0, (count**1.5) / max(1, len(recent_topics)) * 5.0))

    @classmethod
    def _combined_topic_fatigue(
        cls,
        item: DiscoveredContent,
        context: ScoringContext,
    ) -> float:
        """Fatigue across both topic_key (fine) and topic_group (coarse).

        Either axis flagging the candidate as "we've shown this kind a
        lot recently" should suffice — so we take the max. This catches
        the case where ``topic_key`` siblings (动漫杂谈 / 动漫补番 /
        动漫解说) keep escaping per-key fatigue but together saturate
        the user's tolerance for one ``topic_group``.
        """
        key_fatigue = cls._topic_fatigue(
            (item.topic_key or "").strip(),
            context.recent_topic_keys,
        )
        group_fatigue = cls._topic_fatigue(
            (item.topic_group or "").strip(),
            context.recent_topic_groups,
        )
        return max(key_fatigue, group_fatigue)

    @staticmethod
    def _source_monotony(source: str, recent_sources: tuple[str, ...]) -> float:
        """Normalised frequency of source in recent recommendations."""
        if not source or not recent_sources:
            return 0.0
        count = sum(1 for s in recent_sources if s == source)
        return min(1.0, count / max(1, len(recent_sources)) * 2.5)

    @staticmethod
    def _serendipity_bonus(source_strategy: str) -> float:
        """Bonus for content that brings surprise/novelty.

        explore gets full bonus (cross-domain discovery),
        trending gets partial bonus (popular but potentially new topics).
        """
        if source_strategy == "explore":
            return 1.0
        if source_strategy == "trending":
            return 0.5
        return 0.0

    @staticmethod
    def _feedback_adjustment(
        item: DiscoveredContent,
        feedback: FeedbackSignals,
    ) -> float:
        """Additive score adjustment based on recent user feedback.

        Franchise penalty (since v0.3.18): if the user disliked any
        item whose ``franchise_key`` is X, every candidate with the
        same ``franchise_key`` takes a soft hit. Without this layer,
        disliking one 原神 video only blocks that exact bvid; the
        related_chain strategy keeps surfacing other 原神 content.

        ``franchise_key`` is the LLM-tagged IP / series column on
        ``content_cache`` (populated by the content evaluator). It's
        empty for general-interest content (e.g. 番茄炒蛋 教程), so
        most rows pay zero franchise penalty — only matched IPs do.
        """
        adj = 0.0
        if item.up_mid and item.up_mid in feedback.disliked_up_mids:
            adj -= _FEEDBACK_DISLIKE_UP_PENALTY
        topic = (item.topic_group or item.topic_key).strip()
        if topic and topic in feedback.disliked_topic_keys:
            adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
        if topic and topic in feedback.liked_topic_keys:
            adj += _FEEDBACK_LIKE_TOPIC_BONUS
        item_franchise = (getattr(item, "franchise_key", "") or "").strip()
        if item_franchise and item_franchise in feedback.disliked_franchises:
            adj -= _FEEDBACK_DISLIKE_FRANCHISE_PENALTY
        return adj

    async def score_candidates_async(
        self,
        candidates: list[DiscoveredContent],
        context: ScoringContext,
        *,
        embedding_service: SupportsEmbeddingService | None = None,
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
            fresh = (
                self._freshness_score(
                    item.discovered_at or item.last_scored_at,
                    context.now,
                )
                * w.freshness
            )
            monotony = (
                self._source_monotony(
                    item.source_strategy,
                    context.recent_sources,
                )
                * w.source_monotony
            )
            bonus = self._serendipity_bonus(item.source_strategy) * w.serendipity

            # Embedding-based topic fatigue (when available) or the
            # exact-string fallback. Either path takes both axes (topic_key
            # for fine, topic_group for coarse) and uses the max — so a
            # candidate trips fatigue if EITHER its specific topic OR its
            # broader cluster has been served too often recently.
            topic_label = (item.topic_group or item.topic_key).strip()
            if embedding_service is not None and topic_label:
                topic_vec = await embedding_service.embed(topic_label)
                if topic_vec and _recent_vecs:
                    sim_count = sum(
                        cosine_similarity(topic_vec, rv) >= embedding_service.similarity_threshold
                        for rv in _recent_vecs.values()
                    )
                    fatigue = min(
                        1.0,
                        (sim_count**1.5) / max(1, len(context.recent_topic_keys)) * 5.0,
                    )
                else:
                    fatigue = self._combined_topic_fatigue(item, context)
            else:
                fatigue = self._combined_topic_fatigue(item, context)
            fatigue *= w.topic_fatigue

            score = base + fresh - fatigue - monotony + bonus

            # Embedding-based feedback adjustment
            if embedding_service is not None and topic_label:
                topic_vec = await embedding_service.embed(topic_label)
                adj = 0.0
                if item.up_mid and item.up_mid in context.feedback.disliked_up_mids:
                    adj -= _FEEDBACK_DISLIKE_UP_PENALTY
                if topic_vec:
                    for dv in _disliked_vecs.values():
                        if (
                            cosine_similarity(topic_vec, dv)
                            >= embedding_service.similarity_threshold
                        ):
                            adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
                            break
                    for lv in _liked_vecs.values():
                        if (
                            cosine_similarity(topic_vec, lv)
                            >= embedding_service.similarity_threshold
                        ):
                            adj += _FEEDBACK_LIKE_TOPIC_BONUS
                            break
                score += adj
            else:
                score += self._feedback_adjustment(item, context.feedback)

            scores[item.bvid] = max(0.0, score)
        return scores
