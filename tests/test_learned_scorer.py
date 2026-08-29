"""Tests for the learned relevance scorer (embedding prototype)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.discovery.learned_scorer import (
    LearnedBatchResult,
    LearnedRelevanceScorer,
    _normalise_cosine,
)


class _FakeEmbedding:
    """Minimal embedding service stub: returns a fixed vector for any text."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = list(vector)

    async def embed(self, text: str) -> list[float]:
        del text
        return list(self._vector)


def _profile(*names: str) -> SimpleNamespace:
    interests = [SimpleNamespace(name=name) for name in names]
    return SimpleNamespace(preferences=SimpleNamespace(interests=interests))


def _item(title: str = "", description: str = "") -> dict[str, str]:
    return {"title": title, "description": description}


def test_learned_batch_result_constructs() -> None:
    result = LearnedBatchResult(scores=[0.5, 0.9], available=True)
    assert result.scores == [0.5, 0.9]
    assert result.available is True
    assert result.features_digest is None


def test_normalise_cosine_clamps() -> None:
    assert _normalise_cosine(1.0) == pytest.approx(1.0)
    assert _normalise_cosine(-1.0) == pytest.approx(0.0)
    assert 0.0 <= _normalise_cosine(0.3) <= 1.0


async def test_score_batch_returns_available_scores() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0, 0.0, 0.0]))
    profile = _profile("coding", "math")
    contents = [_item("a", "b"), _item("c", "d"), _item("e", "f")]
    result = await scorer.score_batch(contents, profile)
    assert isinstance(result, LearnedBatchResult)
    assert result.available is True
    assert len(result.scores) == 3
    assert all(0.0 <= score <= 1.0 for score in result.scores)
    assert result.features_digest


async def test_score_batch_none_without_embedding() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=None)
    profile = _profile("coding")
    assert await scorer.score_batch([_item("a", "b")], profile) is None


async def test_score_batch_none_without_interests() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0, 0.0]))
    profile = _profile()
    assert await scorer.score_batch([_item("a", "b")], profile) is None


def test_extract_candidate_features() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0]))
    feats = scorer.extract_candidate_features(
        {"title": "hi", "description": "there"}, _profile("x")
    )
    assert feats["title"] == "hi"
    assert feats["length"] == len("hi there")
