"""Tests for the learned relevance scorer (embedding prototype)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openbiliclaw.discovery.bm25 import BM25Index, cjk_tokenize
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


def test_normalise_cosine_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        _normalise_cosine(float("nan"))


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


@pytest.mark.parametrize(
    "vector",
    ([float("nan"), 1.0], [float("inf"), 1.0], [True, 1.0]),
)
async def test_score_batch_fails_open_on_malformed_vectors(vector: list[float]) -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding(vector))
    assert await scorer.score_batch([_item("a", "b")], _profile("coding")) is None


async def test_features_digest_binds_candidate_inputs_not_only_scores() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0, 0.0]))
    first = await scorer.score_batch([_item("first")], _profile("coding"))
    second = await scorer.score_batch([_item("second")], _profile("coding"))

    assert first is not None
    assert second is not None
    assert first.scores == second.scores
    assert first.features_digest != second.features_digest


def test_extract_candidate_features() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0]))
    feats = scorer.extract_candidate_features(
        {"title": "hi", "description": "there"}, _profile("x")
    )
    assert feats["title"] == "hi"
    assert feats["length"] == len("hi there")


def test_cjk_tokenize_splits_latin_words_and_cjk_unigrams_bigrams() -> None:
    assert cjk_tokenize("机器学习") == ["机", "器", "学", "习", "机器", "器学", "学习"]
    assert cjk_tokenize("Machine Learning") == ["machine", "learning"]
    assert cjk_tokenize("Python3 教程") == ["python3", "教", "程", "教程"]
    assert cjk_tokenize("hello, world!") == ["hello", "world"]
    assert cjk_tokenize("一") == ["一"]


def test_bm25_index_ranks_lexical_match_highest() -> None:
    index = BM25Index([cjk_tokenize("机器学习"), cjk_tokenize("烹饪教程")])
    query = cjk_tokenize("机器学习")
    assert index.score(query, 0) > index.score(query, 1)


def test_bm25_index_empty_docs_are_safe() -> None:
    assert BM25Index([]).score(["x"], 0) == 0.0
    assert BM25Index([[]]).score(["x"], 0) == 0.0


async def test_score_batch_boosts_lexically_matching_candidate() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0, 0.0]), bm25_weight=0.5)
    profile = _profile("机器学习")
    result = await scorer.score_batch([_item("机器学习入门教程"), _item("烹饪教程")], profile)
    assert result is not None
    assert result.available is True
    assert result.scores[0] > result.scores[1]


async def test_score_batch_boosts_single_character_cjk_interest() -> None:
    scorer = LearnedRelevanceScorer(embedding_service=_FakeEmbedding([1.0, 0.0]), bm25_weight=0.5)
    result = await scorer.score_batch([_item("汽车评测"), _item("烹饪教程")], _profile("车"))
    assert result is not None
    assert result.available is True
    assert result.scores[0] > result.scores[1]


async def test_score_batch_without_lexical_overlap_keeps_dense_scores() -> None:
    embedding = _FakeEmbedding([1.0, 0.0])
    profile = _profile("量子计算")
    contents = [_item("烘焙"), _item("旅行")]
    fused = await LearnedRelevanceScorer(embedding, bm25_weight=0.3).score_batch(contents, profile)
    baseline = await LearnedRelevanceScorer(embedding, bm25_weight=0.0).score_batch(
        contents, profile
    )
    assert fused is not None
    assert baseline is not None
    assert fused.scores == pytest.approx(baseline.scores)


@pytest.mark.parametrize("weight", [-0.1, 1.1, float("nan"), float("inf")])
def test_bm25_weight_must_be_finite_within_unit_range(weight: float) -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        LearnedRelevanceScorer(_FakeEmbedding([1.0]), bm25_weight=weight)


@pytest.mark.parametrize("weight", ["0.5", None, True])
def test_bm25_weight_rejects_non_numeric_values(weight: object) -> None:
    with pytest.raises(TypeError):
        LearnedRelevanceScorer(_FakeEmbedding([1.0]), bm25_weight=weight)  # type: ignore[arg-type]
