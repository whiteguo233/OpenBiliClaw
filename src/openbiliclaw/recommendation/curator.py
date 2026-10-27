"""Pool Curator — recommendation-side scoring independent of Discovery.

Sits between the RecommendationEngine and the database to compute a
composite ``rec_score`` that accounts for publication-time value, topic
fatigue, source monotony, serendipity, and feedback signals — factors
that Discovery's relevance_score does not capture.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from openbiliclaw.discovery.temporal import (
    TEMPORAL_CLASSES,
    temporal_bonus_component,
    trusted_publication_datetime,
)
from openbiliclaw.recommendation.publication_preference import (
    PublicationDateDecision,
    PublicationDatePreference,
    evaluate_publication_preference,
)
from openbiliclaw.sources.platforms import source_family

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


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

    ``freshness`` is retained as a compatibility-friendly name, but now
    weights a positive publication-time bonus.  It never represents cache
    insertion or evaluation recency.
    """

    relevance: float = 0.30
    freshness: float = 0.10
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
    newly_confirmed_amplification_keys: frozenset[str] = field(default_factory=frozenset)
    over_budget_amplification_keys: frozenset[str] = field(default_factory=frozenset)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class TemporalTopKShadowMetrics:
    """Privacy-safe before/after aggregates for one ranking cut."""

    requested_top_k: int
    effective_top_k: int
    overlap_count: int
    jaccard: float
    positional_match_count: int
    baseline_parseable_published: int
    effective_parseable_published: int
    baseline_bonus_eligible: int
    effective_bonus_eligible: int
    baseline_classes: dict[str, int]
    effective_classes: dict[str, int]
    entered_classes: dict[str, int]
    exited_classes: dict[str, int]
    baseline_sources: dict[str, int]
    effective_sources: dict[str, int]
    entered_sources: dict[str, int]
    exited_sources: dict[str, int]
    baseline_age_buckets: dict[str, int]
    effective_age_buckets: dict[str, int]
    entered_age_buckets: dict[str, int]
    exited_age_buckets: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe aggregate."""

        return {
            "requested_top_k": self.requested_top_k,
            "effective_top_k": self.effective_top_k,
            "overlap_count": self.overlap_count,
            "jaccard": self.jaccard,
            "positional_match_count": self.positional_match_count,
            "baseline_parseable_published": self.baseline_parseable_published,
            "effective_parseable_published": self.effective_parseable_published,
            "baseline_bonus_eligible": self.baseline_bonus_eligible,
            "effective_bonus_eligible": self.effective_bonus_eligible,
            "baseline_classes": self.baseline_classes,
            "effective_classes": self.effective_classes,
            "entered_classes": self.entered_classes,
            "exited_classes": self.exited_classes,
            "baseline_sources": self.baseline_sources,
            "effective_sources": self.effective_sources,
            "entered_sources": self.entered_sources,
            "exited_sources": self.exited_sources,
            "baseline_age_buckets": self.baseline_age_buckets,
            "effective_age_buckets": self.effective_age_buckets,
            "entered_age_buckets": self.entered_age_buckets,
            "exited_age_buckets": self.exited_age_buckets,
        }


@dataclass(frozen=True)
class TemporalRankingShadowAudit:
    """One aggregate-only shadow comparison for a curator candidate window."""

    policy_version: str
    candidate_count: int
    parseable_published_count: int
    bonus_eligible_count: int
    class_counts: dict[str, int]
    source_counts: dict[str, int]
    age_bucket_counts: dict[str, int]
    top_k_metrics: tuple[TemporalTopKShadowMetrics, ...]

    def to_storage_record(self) -> dict[str, object]:
        """Return the bounded storage shape; it contains no candidate identity."""

        return {
            "policy_version": self.policy_version,
            "candidate_count": self.candidate_count,
            "parseable_published_count": self.parseable_published_count,
            "bonus_eligible_count": self.bonus_eligible_count,
            "class_counts": self.class_counts,
            "source_counts": self.source_counts,
            "age_bucket_counts": self.age_bucket_counts,
            "top_k_metrics": [metric.to_dict() for metric in self.top_k_metrics],
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPORAL_RANKING_SHADOW_POLICY_VERSION = "temporal-ranking-shadow-v1"
_TEMPORAL_RANKING_SHADOW_TOP_K = (10, 50, 100)
_TEMPORAL_AUDIT_CLASSES = TEMPORAL_CLASSES
_FEEDBACK_DISLIKE_UP_PENALTY: float = 0.20
_FEEDBACK_DISLIKE_TOPIC_PENALTY: float = 0.10
# Softer than topic penalty — franchise propagation is a heuristic
# (substring match on title), so we don't want a single 原神 dislike
# to brick all gaming content forever. With combined fatigue + topic
# penalty, this 0.07 is enough to push 原神 candidates below other
# fresh content but doesn't outright suppress.
_FEEDBACK_DISLIKE_FRANCHISE_PENALTY: float = 0.07
_FEEDBACK_LIKE_TOPIC_BONUS: float = 0.05
_AMPLIFICATION_OVER_BUDGET_PENALTY: float = 0.35
_POOL_LOW_THRESHOLD: int = 50
_DEFAULT_WEIGHTS = ScoringWeights()


def normalize_amplification_key(value: str) -> str:
    """Normalize a topic/domain label used by amplification guards."""
    return " ".join(value.strip().lower().split())


def candidate_amplification_keys(item: DiscoveredContent) -> set[str]:
    """Return v1 amplification keys for a recommendation candidate."""
    keys = {
        normalize_amplification_key(str(getattr(item, "topic_group", "") or "")),
        normalize_amplification_key(str(getattr(item, "topic_key", "") or "")),
    }
    return {key for key in keys if key}


def candidate_feedback_topics(item: DiscoveredContent) -> frozenset[str]:
    """Return normalized fine/coarse topic aliases used by feedback scoring."""
    values = (
        normalize_amplification_key(str(getattr(item, "topic_key", "") or "")),
        normalize_amplification_key(str(getattr(item, "topic_group", "") or "")),
    )
    return frozenset(value for value in values if value)


def _feedback_row_topics(row: dict[str, object]) -> frozenset[str]:
    values = (
        normalize_amplification_key(str(row.get("topic_key", "") or "")),
        normalize_amplification_key(str(row.get("topic_group", "") or "")),
    )
    return frozenset(value for value in values if value)


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
        publication_preference: PublicationDatePreference | None = None,
    ) -> None:
        self._database = database
        self._weights = weights
        self._history_window = history_window
        self._publication_preference = publication_preference or PublicationDatePreference()

    def publication_date_decision(
        self,
        item: DiscoveredContent,
        *,
        now: datetime,
    ) -> PublicationDateDecision:
        """Evaluate the configured publication preference for one candidate."""

        return evaluate_publication_preference(
            source_platform=(
                getattr(item, "source_platform", "")
                or source_family(getattr(item, "source_strategy", ""), "")
            ),
            published_at=getattr(item, "published_at", ""),
            preference=self._publication_preference,
            now=now,
        )

    def filter_candidates_for_serving(
        self,
        candidates: list[DiscoveredContent],
        *,
        now: datetime | None = None,
    ) -> list[DiscoveredContent]:
        """Drop candidates that strict publication preference cannot serve."""

        current = now or datetime.now(UTC)
        return [
            item
            for item in candidates
            if self.publication_date_decision(item, now=current).eligible
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_context(
        self,
        *,
        newly_confirmed_amplification_keys: set[str] | frozenset[str] | None = None,
        rolling_window_hours: int = 24,
    ) -> ScoringContext:
        """Build a scoring context from recent recommendation history."""
        signals = self._database.get_recent_recommendation_signals(
            limit=self._history_window,
        )
        feedback_rows = self._database.get_feedback_signals(
            limit=self._history_window,
        )
        return self.build_context_from_rows(
            signals,
            feedback_rows,
            newly_confirmed_amplification_keys=newly_confirmed_amplification_keys,
            rolling_window_hours=rolling_window_hours,
        )

    def build_context_from_rows(
        self,
        signals: list[dict[str, object]],
        feedback_rows: list[dict[str, object]],
        *,
        newly_confirmed_amplification_keys: set[str] | frozenset[str] | None = None,
        rolling_window_hours: int = 24,
    ) -> ScoringContext:
        """Build scoring context from a caller-owned consistent DB snapshot."""
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
            topics = _feedback_row_topics(row)
            if ftype == "dislike":
                up_mid = row.get("up_mid")
                if isinstance(up_mid, int) and up_mid > 0:
                    disliked_ups.add(up_mid)
                disliked_topics.update(topics)
                franchise = str(row.get("franchise_key", "")).strip()
                if franchise:
                    disliked_franchises.add(franchise)
            elif ftype in ("like", "save"):
                liked_topics.update(topics)

        normalized_amplification_keys = frozenset(
            key
            for key in (
                normalize_amplification_key(value)
                for value in (newly_confirmed_amplification_keys or set())
            )
            if key
        )
        over_budget_keys: set[str] = set()
        if normalized_amplification_keys:
            since = datetime.now(UTC) - timedelta(hours=rolling_window_hours)
            recent_rows = self._database.get_recent_recommendation_signals_since(
                since=since,
            )
            total_recent = max(1, len(recent_rows))
            for key in normalized_amplification_keys:
                matching = 0
                for row in recent_rows:
                    row_keys = {
                        normalize_amplification_key(str(row.get("topic_key", "") or "")),
                        normalize_amplification_key(str(row.get("topic_group", "") or "")),
                    }
                    if key in row_keys:
                        matching += 1
                if matching / total_recent >= 0.25:
                    over_budget_keys.add(key)

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
            newly_confirmed_amplification_keys=normalized_amplification_keys,
            over_budget_amplification_keys=frozenset(over_budget_keys),
        )

    def score_candidates(
        self,
        candidates: list[DiscoveredContent],
        context: ScoringContext,
    ) -> dict[str, float]:
        """Return a scoring_key → rec_score mapping for the given candidates.

        The returned dict can be passed as ``score_override`` to the
        engine's diversified batch selector.
        """
        w = self._weights
        scores: dict[str, float] = {}
        for item in candidates:
            base = item.relevance_score * w.relevance
            temporal_bonus = self._temporal_bonus_component(item, context.now) * w.freshness
            fatigue = self._combined_topic_fatigue(item, context) * w.topic_fatigue
            monotony = (
                self._source_monotony(
                    item.source_strategy,
                    context.recent_sources,
                )
                * w.source_monotony
            )
            bonus = self._serendipity_bonus(item.source_strategy) * w.serendipity

            score = base + temporal_bonus - fatigue - monotony + bonus

            # Feedback adjustments (additive, outside weight system)
            score += self._feedback_adjustment(item, context.feedback)
            if candidate_amplification_keys(item) & context.over_budget_amplification_keys:
                score -= _AMPLIFICATION_OVER_BUDGET_PENALTY

            date_decision = self.publication_date_decision(item, now=context.now)
            scores[item.scoring_key] = max(0.0, score * date_decision.score_multiplier)
        return scores

    def build_temporal_ranking_shadow_audit(
        self,
        candidates: list[DiscoveredContent],
        scores: dict[str, float],
        context: ScoringContext,
    ) -> TemporalRankingShadowAudit | None:
        """Compare temporal-bonus ranking with the exact no-bonus counterfactual.

        The comparison is aggregate-only: no title, URL, author, keyword, bvid,
        or content id leaves this method. It observes curator pre-diversification
        ranking and never changes scores or admission.
        """

        items_by_id: dict[str, DiscoveredContent] = {}
        for item in candidates:
            identity = item.scoring_key
            if identity:
                items_by_id[identity] = item
        if not items_by_id:
            return None

        effective_scores: dict[str, float] = {}
        baseline_scores: dict[str, float] = {}
        parseable: dict[str, bool] = {}
        eligible: dict[str, bool] = {}
        classes: dict[str, str] = {}
        sources: dict[str, str] = {}
        ages: dict[str, str] = {}
        for identity, item in items_by_id.items():
            raw_score = scores.get(identity, 0.0)
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            temporal_bonus = (
                self._temporal_bonus_component(item, context.now) * self._weights.freshness
            )
            published = self._publication_datetime(item, context.now)
            effective_scores[identity] = max(0.0, score)
            baseline_scores[identity] = max(0.0, score - temporal_bonus)
            parseable[identity] = published is not None
            eligible[identity] = temporal_bonus > 0.0
            classes[identity] = self._temporal_class_for_audit(item)
            sources[identity] = self._source_for_audit(item)
            ages[identity] = self._publication_age_bucket(published, context.now)

        baseline_rank = sorted(
            items_by_id,
            key=lambda identity: (-baseline_scores[identity], identity),
        )
        effective_rank = sorted(
            items_by_id,
            key=lambda identity: (-effective_scores[identity], identity),
        )
        metrics: list[TemporalTopKShadowMetrics] = []
        for requested_top_k in _TEMPORAL_RANKING_SHADOW_TOP_K:
            effective_top_k = min(requested_top_k, len(items_by_id))
            baseline_top = baseline_rank[:effective_top_k]
            effective_top = effective_rank[:effective_top_k]
            baseline_set = set(baseline_top)
            effective_set = set(effective_top)
            overlap = baseline_set & effective_set
            union = baseline_set | effective_set
            entered = effective_set - baseline_set
            exited = baseline_set - effective_set
            metrics.append(
                TemporalTopKShadowMetrics(
                    requested_top_k=requested_top_k,
                    effective_top_k=effective_top_k,
                    overlap_count=len(overlap),
                    jaccard=round(len(overlap) / len(union), 6) if union else 1.0,
                    positional_match_count=sum(
                        left == right
                        for left, right in zip(baseline_top, effective_top, strict=True)
                    ),
                    baseline_parseable_published=sum(parseable[key] for key in baseline_top),
                    effective_parseable_published=sum(parseable[key] for key in effective_top),
                    baseline_bonus_eligible=sum(eligible[key] for key in baseline_top),
                    effective_bonus_eligible=sum(eligible[key] for key in effective_top),
                    baseline_classes=self._audit_counts(baseline_top, classes),
                    effective_classes=self._audit_counts(effective_top, classes),
                    entered_classes=self._audit_counts(entered, classes),
                    exited_classes=self._audit_counts(exited, classes),
                    baseline_sources=self._audit_counts(baseline_top, sources),
                    effective_sources=self._audit_counts(effective_top, sources),
                    entered_sources=self._audit_counts(entered, sources),
                    exited_sources=self._audit_counts(exited, sources),
                    baseline_age_buckets=self._audit_counts(baseline_top, ages),
                    effective_age_buckets=self._audit_counts(effective_top, ages),
                    entered_age_buckets=self._audit_counts(entered, ages),
                    exited_age_buckets=self._audit_counts(exited, ages),
                )
            )

        identities = list(items_by_id)
        return TemporalRankingShadowAudit(
            policy_version=_TEMPORAL_RANKING_SHADOW_POLICY_VERSION,
            candidate_count=len(identities),
            parseable_published_count=sum(parseable.values()),
            bonus_eligible_count=sum(eligible.values()),
            class_counts=self._audit_counts(identities, classes),
            source_counts=self._audit_counts(identities, sources),
            age_bucket_counts=self._audit_counts(identities, ages),
            top_k_metrics=tuple(metrics),
        )

    def record_temporal_ranking_shadow_audit(
        self,
        candidates: list[DiscoveredContent],
        scores: dict[str, float],
        context: ScoringContext,
    ) -> bool:
        """Best-effort persistence for the aggregate-only ranking shadow."""

        audit = self.build_temporal_ranking_shadow_audit(candidates, scores, context)
        recorder = getattr(self._database, "record_temporal_ranking_shadow_audit", None)
        if audit is None or not callable(recorder):
            return False
        try:
            return int(recorder(audit.to_storage_record()) or 0) > 0
        except Exception:
            logger.warning("temporal ranking shadow audit write failed", exc_info=True)
            return False

    def needs_replenishment(self, *, threshold: int = _POOL_LOW_THRESHOLD) -> bool:
        """True when the pool is getting thin."""
        return self.needs_replenishment_for_count(
            self._database.count_pool_candidates(),
            threshold=threshold,
        )

    @staticmethod
    def needs_replenishment_for_count(
        available_count: int,
        *,
        threshold: int = _POOL_LOW_THRESHOLD,
    ) -> bool:
        """Pure inventory gate for callers that already own a pool snapshot."""
        return max(0, int(available_count)) < max(0, int(threshold))

    def pool_count(self) -> int:
        """Current number of fresh pool candidates."""
        return self._database.count_pool_candidates()

    # ------------------------------------------------------------------
    # Scoring components (all pure functions)
    # ------------------------------------------------------------------

    @staticmethod
    def _temporal_bonus_component(item: DiscoveredContent, now: datetime) -> float:
        """Return the unweighted, publication-based temporal bonus for *item*.

        Evergreen, historical, unknown, low-confidence, and undated content is
        deliberately neutral.  In particular, ``discovered_at`` and
        ``last_scored_at`` are cache lifecycle clocks and must never stand in
        for the content's publication time.
        """
        return temporal_bonus_component(
            temporal_class=getattr(item, "temporal_class", "unknown"),
            temporal_confidence=getattr(item, "temporal_confidence", 0.0),
            published_at=getattr(item, "published_at", ""),
            now=now,
        )

    @staticmethod
    def _publication_datetime(item: DiscoveredContent, now: datetime) -> datetime | None:
        """Return a trustworthy publication clock, or ``None`` when unknown."""

        return trusted_publication_datetime(getattr(item, "published_at", ""), now=now)

    @staticmethod
    def _temporal_class_for_audit(item: DiscoveredContent) -> str:
        value = str(getattr(item, "temporal_class", "") or "").strip().lower()
        return value if value in _TEMPORAL_AUDIT_CLASSES else "unknown"

    @staticmethod
    def _source_for_audit(item: DiscoveredContent) -> str:
        raw = str(getattr(item, "source_platform", "") or "").strip().lower()
        if not raw and item.bvid:
            raw = "bilibili"
        token = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")[:32]
        return token or "unknown"

    @staticmethod
    def _publication_age_bucket(published: datetime | None, now: datetime) -> str:
        if published is None:
            return "unknown"
        effective_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        age_days = max(0.0, (effective_now - published).total_seconds() / 86400.0)
        if age_days <= 1.0:
            return "<=1d"
        if age_days <= 7.0:
            return "1-7d"
        if age_days <= 30.0:
            return "7-30d"
        if age_days <= 180.0:
            return "30-180d"
        return ">180d"

    @staticmethod
    def _audit_counts(
        identities: list[str] | set[str],
        values: dict[str, str],
    ) -> dict[str, int]:
        return dict(sorted(Counter(values[identity] for identity in identities).items()))

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

        ``explore`` is the sole discovery context allowed a scoring
        privilege (cross-domain discovery). Every other strategy —
        including ``trending`` — is source context only and must not
        earn a rec-score bonus.
        """
        if source_strategy == "explore":
            return 1.0
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
        candidate_topics = candidate_feedback_topics(item)
        if candidate_topics & feedback.disliked_topic_keys:
            adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
        if candidate_topics & feedback.liked_topic_keys:
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
            temporal_bonus = self._temporal_bonus_component(item, context.now) * w.freshness
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

            score = base + temporal_bonus - fatigue - monotony + bonus

            # Embedding-based feedback adjustment
            candidate_topics = candidate_feedback_topics(item)
            candidate_topic_vecs = []
            if embedding_service is not None:
                for candidate_topic in candidate_topics:
                    vector = await embedding_service.embed(candidate_topic)
                    if vector:
                        candidate_topic_vecs.append(vector)

            if embedding_service is not None:
                adj = 0.0
                if item.up_mid and item.up_mid in context.feedback.disliked_up_mids:
                    adj -= _FEEDBACK_DISLIKE_UP_PENALTY
                disliked_topic_match = bool(
                    candidate_topics & context.feedback.disliked_topic_keys
                ) or any(
                    cosine_similarity(topic_vec, disliked_vec)
                    >= embedding_service.similarity_threshold
                    for topic_vec in candidate_topic_vecs
                    for disliked_vec in _disliked_vecs.values()
                )
                if disliked_topic_match:
                    adj -= _FEEDBACK_DISLIKE_TOPIC_PENALTY
                liked_topic_match = bool(
                    candidate_topics & context.feedback.liked_topic_keys
                ) or any(
                    cosine_similarity(topic_vec, liked_vec)
                    >= embedding_service.similarity_threshold
                    for topic_vec in candidate_topic_vecs
                    for liked_vec in _liked_vecs.values()
                )
                if liked_topic_match:
                    adj += _FEEDBACK_LIKE_TOPIC_BONUS
                item_franchise = (getattr(item, "franchise_key", "") or "").strip()
                if item_franchise and item_franchise in context.feedback.disliked_franchises:
                    adj -= _FEEDBACK_DISLIKE_FRANCHISE_PENALTY
                score += adj
            else:
                score += self._feedback_adjustment(item, context.feedback)

            date_decision = self.publication_date_decision(item, now=context.now)
            scores[item.scoring_key] = max(0.0, score * date_decision.score_multiplier)
        return scores
