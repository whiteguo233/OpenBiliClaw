"""Learned relevance scorer skeleton.

Placeholder for the learned ranking layer of the discovery pipeline.
Later milestones will plug in embedding-based semantic similarity,
semantic tags, portrait alignment, and the five-dimension scoring
model. Until those stages land, ``LearnedRelevanceScorer`` stays a
no-op that reports "not available".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile


@dataclass
class LearnedBatchResult:
    """Outcome of scoring one batch of candidate contents.

    Attributes:
        scores: One relevance score per input content, in input order.
        available: Whether a learned score was actually produced for
            this batch (False when the model is not ready or skipped).
        features_digest: Stable digest of the extracted candidate
            features, used for caching; None when no features were built.
    """

    scores: list[float]
    available: bool
    features_digest: str | None = None


class LearnedRelevanceScorer:
    """Learned relevance scorer (skeleton).

    The planned pipeline is: embed content -> extract semantic tags ->
    align against the soul portrait -> produce the five-dimension
    relevance score. Until those stages land, ``score_batch`` is a
    no-op that always returns None.
    """

    def __init__(self, embedding_service: SupportsEmbeddingService | None = None) -> None:
        """Initialize the scorer.

        Args:
            embedding_service: Optional embedding service used later for
                semantic similarity; wired in now so callers can inject
                dependencies without later signature changes.
        """
        self._embedding_service = embedding_service

    async def score_batch(
        self,
        contents: Sequence[Mapping[str, Any]],
        profile: SoulProfile,
        *,
        source_context: str = "",
    ) -> LearnedBatchResult | None:
        """Score a batch of candidate contents against the user profile.

        Skeleton: always returns None. The future implementation will
        embed each content item, extract its semantic tags, compare them
        against the soul portrait, and combine the five dimensions into
        per-item relevance scores.

        Args:
            contents: Candidate content items (raw Bilibili-style dicts).
            profile: The user's soul profile to score against.
            source_context: Optional source label (e.g. "trending"),
                reserved for provenance-aware scoring.

        Returns:
            A ``LearnedBatchResult`` when learned scoring is available,
            otherwise None (model not ready / stage not implemented).
        """
        del contents, profile, source_context  # reserved for future stages
        return None

    def extract_candidate_features(
        self, item: Mapping[str, Any], profile: SoulProfile
    ) -> dict[str, Any]:
        """Extract learnable features from a single candidate item.

        Skeleton: returns an empty dict. The future implementation will
        produce the embedding vector, semantic tags, portrait-alignment
        signals, and the five-dimension feature block.

        Args:
            item: A single candidate content item (raw content dict).
            profile: The user's soul profile used as feature context.

        Returns:
            Feature dict keyed by feature name; empty while unimplemented.
        """
        del item, profile  # reserved for future stages
        return {}
