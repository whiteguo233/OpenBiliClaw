"""Learned relevance scorer for the discovery pipeline.

This opt-in scorer embeds the candidate content and the profile interest
labels, then scores each candidate by its maximum cosine similarity to any
interest anchor, normalised to [0, 1]. In calibration modes the engine still
runs the complete LLM evaluator for temporal and diversity metadata; malformed
or unavailable learned results leave the LLM relevance score authoritative.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile

_MAX_INTEREST_ANCHORS = 12


@dataclass
class LearnedBatchResult:
    """Outcome of scoring one batch of candidate contents.

    Attributes:
        scores: One relevance score per input content, in input order.
        available: Whether a learned score was actually produced for this
            batch (False when the model is not ready / skipped).
        features_digest: Stable privacy-safe digest of the scorer inputs,
            used to bind audit evidence; None when no features were built.
    """

    scores: list[float]
    available: bool
    features_digest: str | None = None


def _content_text(item: Mapping[str, Any]) -> str:
    title = str(item.get("title") or "")
    description = str(item.get("description") or "")
    return f"{title} {description}".strip()


def _as_float_vector(value: Any) -> list[float]:
    """Return a complete finite embedding vector or ``[]`` when malformed."""

    raw = value
    if not isinstance(raw, (list, tuple)):
        tail = getattr(raw, "tolist", None)
        if not callable(tail):
            return []
        try:
            raw = tail()
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in raw):
        return []
    vector = [float(item) for item in raw]
    return vector if all(math.isfinite(item) for item in vector) else []


def _interest_weight(item: object) -> float:
    value = getattr(item, "weight", 0.0)
    if isinstance(value, bool):
        return 0.0
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 0.0
    return weight if math.isfinite(weight) else 0.0


class LearnedRelevanceScorer:
    """Embedding-based prototype learned relevance scorer.

    Scores each candidate by its maximum cosine similarity to any profile
    interest anchor, normalised to [0, 1]. Returns ``available=False`` when the
    embedding service is missing, the profile has no usable interest anchors,
    or candidate embedding fails (allowing the engine to fail open to the LLM).
    """

    def __init__(self, embedding_service: SupportsEmbeddingService | None = None) -> None:
        self._embedding_service = embedding_service

    def _interest_labels(self, profile: SoulProfile) -> list[str]:
        """Return the strongest active distinct interest labels."""
        labels: list[str] = []
        seen: set[str] = set()
        preferences = getattr(profile, "preferences", None)
        interests = getattr(preferences, "interests", None) or []
        ranked = sorted(interests, key=_interest_weight, reverse=True)
        for item in ranked:
            if str(getattr(item, "state", "active") or "active").strip().lower() == "archived":
                continue
            name = str(getattr(item, "name", None) or "").strip()
            if name and name not in seen:
                labels.append(name)
                seen.add(name)
            if len(labels) >= _MAX_INTEREST_ANCHORS:
                break
        return labels

    async def score_batch(
        self,
        contents: Sequence[Mapping[str, Any]],
        profile: SoulProfile,
        *,
        source_context: str = "",
    ) -> LearnedBatchResult | None:
        """Score a batch of candidate contents against the user profile.

        Returns ``None`` (engine fails open to the LLM) when scoring is
        unavailable; otherwise a ``LearnedBatchResult`` with one score per
        content in input order.
        """
        del source_context
        service = getattr(self, "_embedding_service", None)
        labels = self._interest_labels(profile)
        if service is None or not labels or not contents:
            return None

        try:
            from openbiliclaw.llm.embedding import cosine_similarity

            interest_vectors: list[list[float]] = []
            for label in labels:
                vector = _as_float_vector(await service.embed(label))
                if vector:
                    interest_vectors.append(vector)
            if not interest_vectors:
                return None
            dimensions = {len(vector) for vector in interest_vectors}
            if len(dimensions) != 1:
                return None
            expected_dimension = next(iter(dimensions))

            scores: list[float] = []
            for item in contents:
                content_vector = _as_float_vector(await service.embed(_content_text(item)))
                if len(content_vector) != expected_dimension:
                    return None
                best = max(
                    cosine_similarity(content_vector, interest) for interest in interest_vectors
                )
                scores.append(_normalise_cosine(float(best)))
        except Exception:  # noqa: BLE001 - fail open to the LLM
            return None

        return LearnedBatchResult(
            scores=scores,
            available=True,
            features_digest=_features_digest(labels, contents),
        )

    def extract_candidate_features(
        self, item: Mapping[str, Any], profile: SoulProfile
    ) -> dict[str, Any]:
        """Extract learnable features from a single candidate item."""
        del profile
        text = _content_text(item)
        return {"text": text, "title": str(item.get("title") or ""), "length": len(text)}


def _normalise_cosine(similarity: float) -> float:
    """Map cosine in [-1, 1] to [0, 1], clamped."""
    if not math.isfinite(similarity):
        raise ValueError("cosine similarity must be finite")
    return max(0.0, min(1.0, (similarity + 1.0) / 2.0))


def _features_digest(labels: list[str], contents: Sequence[Mapping[str, Any]]) -> str:
    """Digest prompt-local scorer inputs without persisting profile/content text."""

    payload = "\0".join(
        ["learned-features-v1", *labels, *(_content_text(item) for item in contents)]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
