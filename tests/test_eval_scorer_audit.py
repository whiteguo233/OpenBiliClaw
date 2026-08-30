"""Tests for the learned-vs-LLM shadow auditor and quality gate.

Covers the joinable-row contract, the pure-Python Spearman correlation,
the admission delta, and the discriminator coverage check.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.eval_scorer_audit import (
    LearnedGateReport,
    LearnedShadowDecision,
    evaluate_learned_scorer_gate,
    hash_learned_candidate_identity,
    sanitize_learned_platform,
)
from openbiliclaw.discovery.learned_scorer import LearnedBatchResult
from openbiliclaw.soul.profile import InterestTag, SoulProfile
from openbiliclaw.storage.database import Database


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


@pytest.mark.parametrize("platform", ("linuxdo", "v2ex"))
def test_learned_audit_keeps_supported_platform_classes(platform: str) -> None:
    assert sanitize_learned_platform(platform) == platform


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
    rows = [_row(learned=0.8, llm=0.8) for _index in range(100)]
    for row in rows[:98]:
        row["learned_score"] = None
    report = evaluate_learned_scorer_gate(rows)
    assert report.passed is False
    assert report.joinable_candidates == 2
    assert report.telemetry_coverage == pytest.approx(0.02)
    assert "joinable_candidates_below_100" in report.reasons
    assert "telemetry_coverage_below_1.0" in report.reasons


def test_gate_rejects_cohort_without_llm_admissions() -> None:
    rows = [
        _row(learned=0.1 + (index % 40) / 100, llm=0.1 + (index % 40) / 100) for index in range(100)
    ]

    report = evaluate_learned_scorer_gate(rows)

    assert report.passed is False
    assert "admitted_candidates_missing" in report.reasons
    assert "admission_delta_missing" in report.reasons
    assert "coverage_missing" in report.reasons


def test_gate_passes_only_with_one_hundred_complete_pairs_and_positive_labels() -> None:
    rows = []
    for index in range(100):
        score = 0.7 + (index % 20) / 100 if index < 50 else 0.2 + (index % 20) / 100
        rows.append(_row(learned=score, llm=score))

    report = evaluate_learned_scorer_gate(rows)

    assert report.passed is True
    assert report.joinable_candidates == 100
    assert report.telemetry_coverage == 1.0
    assert report.coverage == 1.0


class _StaticLearnedScorer:
    def __init__(self, score: float) -> None:
        self.score = score

    async def score_batch(
        self,
        contents: Sequence[Mapping[str, object]],
        profile: object,
        *,
        source_context: str = "",
    ) -> LearnedBatchResult:
        del profile, source_context
        return LearnedBatchResult(
            scores=[self.score] * len(contents),
            available=True,
            features_digest="f" * 64,
        )


class _MetadataLLM:
    def __init__(self, score: float = 0.7) -> None:
        self.score = score
        self.calls = 0

    async def complete_structured_task(self, **kwargs: object) -> object:
        self.calls += 1
        user_input = str(kwargs["user_input"])
        raw_batch = user_input.split("<content_batch>", 1)[1].split("</content_batch>", 1)[0]
        envelope = json.loads(raw_batch.strip())
        items = envelope["items"]
        return SimpleNamespace(
            content=json.dumps(
                [
                    {
                        "id": str(item["id"]),
                        "score": self.score,
                        "reason": "LLM metadata",
                        "topic_group": "安全元数据",
                        "style_key": "deep_focus",
                        "franchise_key": "safe-series",
                        "temporal_class": "current",
                        "temporal_confidence": 0.9,
                        "temporal_reason": "current information",
                        "temporal_validity_mode": "freshness_only",
                        "temporal_valid_until": "",
                        "temporal_scope": "core",
                        "temporal_evidence": "私密正文",
                        "temporal_state": "unknown",
                    }
                    for item in items
                ]
            )
        )


def _profile() -> SoulProfile:
    profile = SoulProfile()
    profile.preferences.interests = [InterestTag(name="纪录片", category="知识", weight=1.0)]
    return profile


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_score"),
    (("shadow", 0.7), ("learned", 0.9)),
)
async def test_engine_preserves_llm_metadata_and_persists_complete_pairs(
    tmp_path: Path,
    mode: str,
    expected_score: float,
) -> None:
    database = Database(tmp_path / f"learned-{mode}.db")
    database.initialize()
    llm = _MetadataLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        database=database,
        eval_prefilter_mode="off",
        eval_scorer=mode,
        learned_scorer=_StaticLearnedScorer(0.9),  # type: ignore[arg-type]
    )
    content = DiscoveredContent(
        bvid="BVPRIVATE",
        title="私密标题",
        description="私密正文",
        source_strategy="search",
    )

    scores = await engine.evaluate_content_batch([content], _profile())

    assert scores == [expected_score]
    assert llm.calls == 1
    assert content.topic_group == "安全元数据"
    assert content.style_key == "deep_focus"
    assert content.franchise_key == "safe-series"
    assert content.temporal_class == "current"
    rows = database.query_learned_scorer_shadow_audit()
    assert len(rows) == 1
    assert float(rows[0]["learned_score"]) == 0.9
    assert float(rows[0]["llm_score"]) == 0.7
    assert int(rows[0]["admission_result"]) == 1
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "BVPRIVATE" not in serialized
    assert "私密标题" not in serialized


@pytest.mark.asyncio
async def test_engine_learned_mode_falls_back_when_audit_storage_is_unavailable() -> None:
    llm = _MetadataLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        eval_prefilter_mode="off",
        eval_scorer="learned",
        learned_scorer=_StaticLearnedScorer(0.9),  # type: ignore[arg-type]
    )
    content = DiscoveredContent(
        bvid="BVNOAUDIT",
        title="候选",
        description="正文",
        source_strategy="search",
    )

    scores = await engine.evaluate_content_batch([content], _profile())

    assert scores == [0.7]
    assert llm.calls == 1
    assert content.relevance_reason == "LLM metadata"


@pytest.mark.asyncio
async def test_engine_rejects_nonfinite_learned_score(tmp_path: Path) -> None:
    database = Database(tmp_path / "nonfinite-learned.db")
    database.initialize()
    llm = _MetadataLLM()
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        database=database,
        eval_prefilter_mode="off",
        eval_scorer="learned",
        learned_scorer=_StaticLearnedScorer(float("nan")),  # type: ignore[arg-type]
    )
    content = DiscoveredContent(
        bvid="BVNONFINITE",
        title="候选",
        description="正文",
        source_strategy="search",
    )

    scores = await engine.evaluate_content_batch([content], _profile())

    assert scores == [0.7]
    assert database.query_learned_scorer_shadow_audit() == []


def test_database_rejects_raw_learned_audit_identity(tmp_path: Path) -> None:
    database = Database(tmp_path / "unsafe-learned.db")
    database.initialize()
    unsafe = _row(learned=0.8, llm=0.7)
    unsafe["candidate_hash"] = "private-title"

    with pytest.raises(ValueError, match="privacy-safe"):
        database.record_learned_scorer_shadow_audit([unsafe])
