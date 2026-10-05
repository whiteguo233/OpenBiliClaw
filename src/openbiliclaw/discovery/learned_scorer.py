"""Learned relevance scorer for the discovery pipeline.

This opt-in scorer embeds the candidate content and the profile interest
labels, scores each candidate by its maximum cosine similarity to any interest
anchor, and fuses that dense signal with a lexical BM25 score over the
candidate text. In calibration modes the engine still runs the complete LLM
evaluator for temporal and diversity metadata; malformed or unavailable
learned results leave the LLM relevance score authoritative.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.bm25 import BM25Index, cjk_tokenize

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


def _validate_bm25_weight(value: object) -> float:
    """Return ``value`` as a float in [0, 1], rejecting anything else."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("bm25_weight must be a real number in [0, 1]")
    weight = float(value)
    if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
        raise ValueError("bm25_weight must be a finite value within [0, 1]")
    return weight


class LearnedRelevanceScorer:
    """Hybrid learned relevance scorer (dense embedding + sparse BM25).

    Fuses each candidate's maximum cosine similarity to any profile interest
    anchor with a lexical BM25 score over the candidate text, weighted by
    ``bm25_weight``. Returns ``available=False`` when the embedding service is
    missing, the profile has no usable interest anchors, or candidate embedding
    fails (allowing the engine to fail open to the LLM).

    The BM25 signal is batch-relative: its idf and max-normalisation are
    estimated from the candidate set passed to one ``score_batch`` call, so the
    same candidate can receive a slightly different score when its batch peers
    change (batching, cache hits, prefilter). Callers that need reproducible
    absolute scores (e.g. shadow-audit cohorts) must freeze the batching.
    """

    def __init__(
        self,
        embedding_service: SupportsEmbeddingService | None = None,
        *,
        bm25_weight: float = 0.3,
    ) -> None:
        weight = _validate_bm25_weight(bm25_weight)
        self._embedding_service = embedding_service
        # 默认 0.3 未校准：偏重稠密信号，等 learned/shadow 审计数据跑出后回调。
        self._bm25_weight = weight

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

            cosine_scores: list[float] = []
            for item in contents:
                content_vector = _as_float_vector(await service.embed(_content_text(item)))
                if len(content_vector) != expected_dimension:
                    return None
                best = max(
                    cosine_similarity(content_vector, interest) for interest in interest_vectors
                )
                cosine_scores.append(_normalise_cosine(float(best)))

            scores = self._fuse_scores(labels, contents, cosine_scores)
        except Exception:  # noqa: BLE001 - fail open to the LLM
            return None

        return LearnedBatchResult(
            scores=scores,
            available=True,
            features_digest=_features_digest(labels, contents, self._bm25_weight),
        )

    def _fuse_scores(
        self,
        labels: list[str],
        contents: Sequence[Mapping[str, Any]],
        cosine_scores: list[float],
    ) -> list[float]:
        """Fuse dense cosine scores with sparse BM25 scores by ``bm25_weight``.

        BM25 分数按批内 max 归一化到 [0,1]，再与 cosine 加权。归一化是批内相对
        的：单候选、权重为 0、或本批完全没有词面命中（稀疏信号恒为 0）时，直接
        返回 cosine 分，避免把稠密分无意义地整体缩放 ``(1 - bm25_weight)``。
        """
        if self._bm25_weight <= 0.0 or len(contents) == 1:
            return cosine_scores
        bm25_scores = self._bm25_scores(labels, contents)
        peak = max(bm25_scores, default=0.0)
        if peak <= 0.0:
            return cosine_scores
        bm25_norm = [raw / peak for raw in bm25_scores]
        dense_weight = 1.0 - self._bm25_weight
        return [
            dense_weight * cosine + self._bm25_weight * lexical
            for cosine, lexical in zip(cosine_scores, bm25_norm, strict=True)
        ]

    def _bm25_scores(
        self,
        labels: list[str],
        contents: Sequence[Mapping[str, Any]],
    ) -> list[float]:
        """Return per-candidate raw BM25 scores against the interest-label query."""
        query = [token for label in labels for token in cjk_tokenize(label)]
        docs = [cjk_tokenize(_content_text(item)) for item in contents]
        index = BM25Index(docs)
        return [index.score(query, i) for i in range(len(docs))]

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


def _features_digest(
    labels: list[str],
    contents: Sequence[Mapping[str, Any]],
    bm25_weight: float,
) -> str:
    """Digest prompt-local scorer inputs without persisting profile/content text."""

    payload = "\0".join(
        [
            "learned-features-v2",
            str(bm25_weight),
            *labels,
            *(_content_text(item) for item in contents),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
