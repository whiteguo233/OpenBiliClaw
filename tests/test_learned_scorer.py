"""Tests for the learned relevance scorer skeleton.

Covers the S1a skeleton contract: the dataclass shape, the no-op
``score_batch`` fallback (returns None), and the empty feature extractor.
"""

from __future__ import annotations

from openbiliclaw.discovery.learned_scorer import (
    LearnedBatchResult,
    LearnedRelevanceScorer,
)


def test_learned_batch_result_constructs() -> None:
    result = LearnedBatchResult(
        scores=[0.5, 0.9],
        available=True,
        features_digest="abc",
    )
    assert result.scores == [0.5, 0.9]
    assert result.available is True
    assert result.features_digest == "abc"


def test_learned_batch_result_defaults_none_digest() -> None:
    result = LearnedBatchResult(scores=[], available=False)
    assert result.features_digest is None


async def test_score_batch_returns_none_while_implemented_elsewhere() -> None:
    # Skeleton: score_batch is a no-op that reports not-available so the
    # engine falls through to the unchanged LLM path. Real scoring lands in a
    # later milestone; until then this must always return None.
    scorer = LearnedRelevanceScorer(embedding_service=None)
    result = await scorer.score_batch(
        contents=[],  # type: ignore[arg-type]
        profile=None,  # type: ignore[arg-type]
        source_context="trending",
    )
    assert result is None


def test_extract_candidate_features_empty_placeholder() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=None)
    assert (
        scorer.extract_candidate_features(
            item={},  # type: ignore[arg-type]
            profile=None,  # type: ignore[arg-type]
        )
        == {}
    )
