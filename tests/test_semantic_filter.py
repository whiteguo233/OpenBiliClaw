"""Independent semantic-filter evidence, shadow joins, and nonterminal refusal."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import SemanticFilterConfig
from openbiliclaw.decision.models import (
    DecisionEvidence,
    DecisionRequest,
    DecisionUsage,
    NoulAnswer,
)
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.semantic_filter import JevSemanticFilter
from openbiliclaw.storage.database import Database

from .test_discovery_candidate_pipeline import _ScoringLLM
from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from pathlib import Path


class EvidenceProvider:
    def __init__(self, score: float = 1.0, *, broken: str = "") -> None:
        self.score = score
        self.broken = broken
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request)
        if self.broken == "raise":
            raise RuntimeError("secret transport error")
        if self.broken == "timeout":
            await asyncio.sleep(1)
        if self.broken == "cancel":
            raise asyncio.CancelledError
        values: dict[str, Any] = {
            "status": "OK",
            "reason_code": "ok",
            "provider": "jev",
            "request_id": request.request_id,
            "purpose": request.purpose,
            "question_schema_version": request.question_schema_version,
            "input_digest": request.input_digest,
            "requested_model": "jev-1.13.0",
            "actual_model": "jev-1.13.0",
            "latency_ms": 0.0,
            "attempts": 1,
            "usage": DecisionUsage(input_tokens=20, output_tokens=2),
            "answers": {key: NoulAnswer(noul=self.score) for key in request.questions},
        }
        if self.broken in {"request_id", "input_digest", "question_schema_version", "actual_model"}:
            values[self.broken] = "wrong"
        if self.broken == "partial":
            values["answers"].pop(next(iter(request.questions)))
            values["answers"]["unrequested"] = NoulAnswer(noul=1.0)
        if self.broken == "mutate_request":
            request.state["source_context"] = "tampered"  # type: ignore[index]
            values["input_digest"] = request.input_digest
        if self.broken == "malformed_answer":
            values["answers"] = {
                key: NoulAnswer.model_construct(noul=99.0) for key in request.questions
            }
            return DecisionEvidence.model_construct(**values)
        return DecisionEvidence(**values)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(str(tmp_path / "filter.db"))
    db.initialize()
    return db


def _filter(
    database: Database | None, provider: EvidenceProvider, **kwargs: Any
) -> JevSemanticFilter:
    config = SemanticFilterConfig(
        mode="enforce",
        calibration_tag="test-fixture",
        calibration_model="jev-1.13.0",
        **kwargs,
    )
    return JevSemanticFilter(config, provider, model="jev-1.13.0", database=database)


def _contents(count: int = 2) -> list[DiscoveredContent]:
    return [
        DiscoveredContent(
            bvid=f"BVfilter{index}", title=f"candidate {index}", source_strategy="search"
        )
        for index in range(count)
    ]


async def test_filter_enforces_only_valid_persisted_explicit_evidence(database: Database) -> None:
    provider = EvidenceProvider()
    filtering = _filter(database, provider)
    batch = await filtering.evaluate(_contents(), _build_profile())
    assert batch.suppressed_indices == {0, 1}
    assert batch.persisted
    request = provider.requests[0]
    for key, question in request.questions.items():
        assert f"candidate_ref: {key}" in question.instructions
    rows = database.conn.execute("SELECT * FROM discovery_semantic_filter_audit").fetchall()
    assert len(rows) == 2
    assert all(row["llm_score"] is None for row in rows)
    assert all("candidate 0" not in str(dict(row)) for row in rows)
    assert "personality_portrait" not in str(request.state)


async def test_shadow_preserves_all_and_joins_only_actual_labels(database: Database) -> None:
    filtering = _filter(database, EvidenceProvider())
    batch = await filtering.evaluate(_contents(), _build_profile(), calibration=True)
    assert not batch.suppressed_indices
    assert all(row.would_filter for row in batch.decisions)
    filtering.complete(batch, llm_scores={0: 0.8}, admission_thresholds={0: 0.6, 1: 0.6})
    rows = database.conn.execute("SELECT * FROM discovery_semantic_filter_audit").fetchall()
    assert rows[0]["llm_score"] == 0.8
    assert rows[0]["admission_result"] == 1
    assert rows[1]["llm_score"] is None


@pytest.mark.parametrize(
    "broken",
    [
        "raise",
        "timeout",
        "request_id",
        "input_digest",
        "question_schema_version",
        "actual_model",
        "partial",
        "mutate_request",
        "malformed_answer",
    ],
)
async def test_failure_invalid_or_partial_evidence_is_fail_open(
    database: Database, broken: str
) -> None:
    batch = await _filter(
        database, EvidenceProvider(broken=broken), decision_budget_seconds=0.01
    ).evaluate(
        _contents(),
        _build_profile(),
    )
    assert not batch.suppressed_indices
    assert all(row.refusal_score is None for row in batch.decisions)


async def test_cancellation_propagates(database: Database) -> None:
    with pytest.raises(asyncio.CancelledError):
        await _filter(database, EvidenceProvider(broken="cancel")).evaluate(
            _contents(), _build_profile()
        )


async def test_audit_failure_exploration_abstention_and_call_budget_preserve_candidates(
    database: Database,
) -> None:
    provider = EvidenceProvider()
    missing_audit = await _filter(None, provider).evaluate(_contents(), _build_profile())
    assert not missing_audit.suppressed_indices
    contents = _contents(35)
    contents[0].source_strategy = "explore"
    filtering = _filter(database, provider, max_candidates_per_cycle=33)
    provider.requests.clear()
    batch = await filtering.evaluate(contents, _build_profile())
    assert len(provider.requests) == 1
    assert len(batch.suppressed_indices) == 30
    assert batch.decisions[0].reason == "exploration_preserved"
    assert batch.decisions[-1].reason == "budget_exhausted"
    keep = await _filter(database, EvidenceProvider(0.4)).evaluate(_contents(), _build_profile())
    assert not keep.suppressed_indices


async def test_oversized_input_is_local_fail_open_without_provider_call(database: Database) -> None:
    provider = EvidenceProvider()
    contents = _contents(30)
    for item in contents:
        item.description = "内容" * 1000
    batch = await _filter(database, provider).evaluate(contents, _build_profile())
    assert not provider.requests
    assert not batch.suppressed_indices
    assert all(row.reason == "input_budget_exhausted" for row in batch.decisions)


async def test_disabled_filter_never_calls_provider_or_creates_audit(database: Database) -> None:
    provider = EvidenceProvider()
    filtering = JevSemanticFilter(
        SemanticFilterConfig(),
        provider,
        model="jev-1.13.0",
        database=database,
    )
    result = await filtering.evaluate(_contents(), _build_profile())
    assert not result.decisions
    assert not provider.requests
    assert (
        database.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='discovery_semantic_filter_audit'"
        ).fetchone()
        is None
    )


async def test_engine_calibration_freezes_cohort_and_preserves_llm_metadata(
    database: Database,
) -> None:
    provider = EvidenceProvider()
    filtering = _filter(database, provider)
    llm = _ScoringLLM(
        [
            {
                "score": 0.8,
                "reason": "matches interests",
                "topic_group": "programming",
                "style_key": "hands_on",
            },
            {
                "score": 0.7,
                "reason": "matches interests",
                "topic_group": "science",
                "style_key": "deep_dive",
            },
        ]
    )
    engine = ContentDiscoveryEngine(
        llm_service=llm, database=database, semantic_filter=filtering, eval_prefilter_mode="shadow"
    )
    contents = _contents()
    scores = await engine.evaluate_content_batch(contents, _build_profile())
    assert scores == [0.8, 0.7]
    assert contents[0].topic_group == "programming"
    assert len(llm.calls) == 1
    rows = database.conn.execute("SELECT * FROM discovery_semantic_filter_audit").fetchall()
    assert all(row["calibration"] and row["llm_score"] is not None for row in rows)


async def test_enforced_refusal_requeues_once_with_fencing_then_full_review(
    database: Database,
) -> None:
    provider = EvidenceProvider()
    llm = _ScoringLLM([{"score": 0.8, "reason": "matches interests", "topic_group": "programming"}])
    engine = ContentDiscoveryEngine(
        llm_service=llm,
        database=database,
        semantic_filter=_filter(database, provider),
        eval_prefilter_mode="off",
    )
    pipeline = DiscoveryCandidatePipeline(database=database, discovery_engine=engine)
    pipeline.enqueue_candidates(_contents(1))
    claim = pipeline.claim_batch(limit=1)
    assert claim is not None
    outcome = await pipeline.evaluate_claim(claim, _build_profile())
    assert outcome.claim.items[0].semantic_filter_deferred
    completion = await pipeline.complete_claim(outcome)
    assert completion["deferred"] == 1
    assert completion["rejected"] == completion["stale"] == completion["evaluated"] == 0
    row = database.get_discovery_candidates_by_ids([int(claim.rows[0]["id"])])[0]
    assert row["status"] == "pending_eval"
    assert row["eval_error"] == "semantic_filter_deferred"
    assert row["eval_attempts"] == 0
    assert row["batch_eval_attempts"] == 0
    assert not llm.calls
    review = pipeline.claim_batch(limit=1)
    assert review is not None and review.items[0].semantic_filter_review
    assert (
        database.defer_claimed_semantic_filter_candidates([int(row["id"])], claim_token=claim.token)
        == 0
    )
    reviewed = await pipeline.evaluate_claim(review, _build_profile())
    assert not reviewed.claim.items[0].semantic_filter_deferred
    await pipeline.complete_claim(reviewed)
    assert len(llm.calls) == 1
    row = database.get_discovery_candidates_by_ids([int(row["id"])])[0]
    assert row["status"] == "cached"
