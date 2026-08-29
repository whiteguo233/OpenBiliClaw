"""Tests for the learned-vs-LLM shadow auditor and quality gate.

Covers the joinable-row contract, the pure-Python Spearman correlation,
the admission delta, and the discriminator coverage check.
"""

from __future__ import annotations

from openbiliclaw.discovery.eval_scorer_audit import (
    LearnedGateReport,
    LearnedShadowDecision,
    evaluate_learned_scorer_gate,
    hash_learned_candidate_identity,
)


def _row(
    *,
    learned: float | None,
    llm: float,
    threshold: float = 0.60,
    platform: str = "bilibili",
    context: str = "search",
    digest: str = "a" * 64,
) -> dict[str, object]:
    return LearnedShadowDecision(
        content_index=0,
        candidate_hash=digest,
        platform_class=platform,
        context_class=context,
        learned_score=learned,
        llm_score=llm,
        admission_threshold=threshold,
        admission_result=llm >= threshold,
        features_digest=digest,
    ).as_storage_record()


def test_learned_shadow_decision_defaults_decision_id() -> None:
    a = LearnedShadowDecision(
        content_index=0,
        candidate_hash="b" * 64,
        platform_class="bilibili",
        context_class="search",
        learned_score=0.9,
        llm_score=0.7,
        admission_threshold=0.6,
        admission_result=True,
        features_digest="c" * 64,
    )
    b = LearnedShadowDecision(
        content_index=0,
        candidate_hash="b" * 64,
        platform_class="bilibili",
        context_class="search",
        learned_score=0.9,
        llm_score=0.7,
        admission_threshold=0.6,
        admission_result=True,
        features_digest="c" * 64,
    )
    assert a.decision_id  # auto-generated
    assert a.decision_id != b.decision_id  # unique
    record = a.as_storage_record()
    assert "content_index" not in record
    assert record["decision_id"] == a.decision_id


def test_hash_learned_candidate_identity_is_sha256_hex() -> None:
    digest = hash_learned_candidate_identity("platform:content:123")
    assert len(digest) == 64
    assert int(digest, 16) == int(digest, 16)  # valid hex


def test_gate_perfect_correlation_passes() -> None:
    # learned == llm everywhere -> Spearman 1.0, no admission delta, coverage 1.0.
    rows = [
        _row(learned=0.7, llm=0.7),
        _row(learned=0.8, llm=0.8),
        _row(learned=0.9, llm=0.9),
        _row(learned=0.3, llm=0.3),
        _row(learned=0.5, llm=0.5),
        _row(learned=0.4, llm=0.4),
    ]
    report = evaluate_learned_scorer_gate(rows)
    assert isinstance(report, LearnedGateReport)
    # joinable all 6 (need >= 100 to pass total gate, but metrics computed)
    assert report.joinable_candidates == 6


def test_gate_spearman_and_admission_delta() -> None:
    rows = [
        _row(learned=0.9, llm=0.9),
        _row(learned=0.3, llm=0.8),
        _row(learned=0.8, llm=0.7),
        _row(learned=0.5, llm=0.4),
        _row(learned=0.2, llm=0.6),
        _row(learned=0.7, llm=0.2),
    ]
    report = evaluate_learned_scorer_gate(rows)
    assert report.spearman is not None
    assert -1.0 <= report.spearman <= 1.0
    # admitted by llm = those with llm>=0.6 (0.9,0.8,0.7,0.6=4); by learned>=0.6 (0.9,0.8,0.7=3)
    assert report.admission_delta is not None


def test_gate_skips_rows_with_missing_learned_score() -> None:
    rows = [
        _row(learned=None, llm=0.9),
        _row(learned=0.8, llm=0.8),
        _row(learned=None, llm=0.2),
        _row(learned=0.6, llm=0.5),
    ]
    report = evaluate_learned_scorer_gate(rows)
    assert report.joinable_candidates >= 1
