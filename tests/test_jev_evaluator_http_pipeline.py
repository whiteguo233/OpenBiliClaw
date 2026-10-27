"""Native HTTP-contract to SQLite acceptance, with MOCK Jev responses only.

This exercises the real adapters, request/response wire, runtime gate, evaluator,
discovery engine and candidate pipeline. MockTransport prevents every network
request; these tests are not evidence of live Jev quality or latency.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from openbiliclaw.config import JevEvaluationConfig
from openbiliclaw.decision.jev_provider import JevDecisionProvider
from openbiliclaw.decision.models import DecisionRequest
from openbiliclaw.decision.runtime import BoundedDecisionProvider
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.storage.database import Database

from .test_jev_full_evaluator import MODEL, native_evidence
from .test_search_strategy import _build_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class BombLLM:
    """Record even swallowed LLM calls, so fallback can never pass unnoticed."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Pure Jev must not call the generative LLM")

    async def complete_multimodal_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Pure text-only Jev must not call the vision LLM")


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "mock-jev-http-pipeline.db")
    db.initialize()
    try:
        yield db
    finally:
        db.close()


def build_pipeline(
    database: Database, client: httpx.AsyncClient
) -> tuple[DiscoveryCandidatePipeline, BoundedDecisionProvider, BombLLM]:
    native = JevDecisionProvider(
        api_key="synthetic-test-key",
        model=MODEL,
        max_attempts=1,
        timeout_seconds=2,
        client=client,
    )
    bounded = BoundedDecisionProvider(native, model=MODEL)
    evaluator = JevContentEvaluator(
        bounded,
        model=MODEL,
        config=JevEvaluationConfig(franchise_labels=["Python"]),
    )
    llm = BombLLM()
    engine = ContentDiscoveryEngine(
        database=database, eval_scorer="jev", jev_evaluator=evaluator, llm_service=llm
    )
    return (
        DiscoveryCandidatePipeline(
            database=database, discovery_engine=engine, pool_target_count=30
        ),
        bounded,
        llm,
    )


def enqueue(database: Database, *, body_text: str = "算法与数据结构的通用概念。") -> None:
    database.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVJEVHTTP1",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVJEVHTTP1",
                title="Python 讲解算法与数据结构",
                body_text=body_text,
                description="通用原理，不依赖工具版本。",
                tags=["Python", "算法"],
            )
        ]
    )


def mock_success(http_request: httpx.Request) -> httpx.Response:
    """Only native fields traverse HTTP; local attribution stays in the client."""
    body = json.loads(http_request.content)
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == MODEL
    assert len(body["questions"]) % 10 == 0
    # Reconstruct typed questions from the ACTUAL outbound JSON. The local IDs
    # below are not part of the response; the production provider binds its own.
    request = DecisionRequest.model_validate_json(
        json.dumps(
            {
                "request_id": "mock-server-fixture",
                "purpose": "EVALUATE",
                "question_schema_version": "mock-server-fixture",
                "state": body["state"],
                "questions": body["questions"],
            },
            ensure_ascii=False,
        )
    )
    fixture = native_evidence(request, choices={"franchise": "f0"})
    return httpx.Response(
        200,
        json={
            "model": MODEL,
            "answers": {
                key: answer.model_dump(mode="json") for key, answer in fixture.answers.items()
            },
            "usage": {"input_tokens": 2000, "output_tokens": 100},
        },
    )


async def test_mock_native_http_complete_evaluation_is_admitted_to_real_sqlite(
    database: Database,
) -> None:
    http_calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        return mock_success(request)

    enqueue(database)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
        pipeline, runtime, llm = build_pipeline(database, client)
        try:
            claim = pipeline.claim_batch(limit=1)
            assert claim is not None
            outcome = await pipeline.evaluate_claim(claim, _build_profile())
            result = await pipeline.complete_claim(outcome, admission_limit=10)
            assert result == {"evaluated": 1, "cached": 1, "rejected": 0, "stale": 0}
            assert llm.calls == 0
            assert len(http_calls) == 1
            assert http_calls[0].method == "POST"
            assert str(http_calls[0].url) == "https://api.typesafe.ai/v1/systemone"
            body = json.loads(http_calls[0].content)
            assert body["state"]["candidate_profiles"]["c0"]["interests"]
            assert "interests" not in body["state"]["profile"]  # only common mandatory facts
            sent = body["state"]["untrusted_candidates"][0]["content"]
            assert "算法与数据结构的通用概念。" in [
                span["text"] for span in sent["text_spans"].values()
            ]
            assert sent["text_coverage"]["is_partial"] is False
            assert pipeline.discovery_engine._eval_cache

            fields = (
                "relevance_score, relevance_reason, topic_group, style_key, franchise_key, "
                "temporal_class, temporal_confidence, temporal_reason, temporal_validity_mode, "
                "temporal_valid_until, temporal_scope, temporal_evidence, temporal_state, "
                "temporal_evidence_complete, temporal_evaluated_at"
            )
            for table in ("discovery_candidates", "content_cache"):
                row = database.conn.execute(f"SELECT {fields} FROM {table}").fetchone()
                assert row["relevance_score"] == 0.75
                assert row["relevance_reason"] == "内容对应明确兴趣"
                assert row["topic_group"] == "编程"
                assert row["style_key"] == "deep_focus"
                assert row["franchise_key"] == "Python"
                assert row["temporal_class"] == "evergreen"
                assert row["temporal_confidence"] == 0.75
                assert row["temporal_reason"] == "核心价值不依赖当前时间"
                assert row["temporal_validity_mode"] == "none"
                assert row["temporal_scope"] == "none"
                assert row["temporal_state"] == "unknown"
                assert row["temporal_evidence"] == row["temporal_valid_until"] == ""
                assert row["temporal_evidence_complete"] == 1
                assert row["temporal_evaluated_at"]
        finally:
            await runtime.aclose()


async def test_mock_native_http_503_stays_pending_without_cache_or_llm_fallback(
    database: Database, caplog: Any
) -> None:
    http_calls: list[httpx.Request] = []

    def unavailable(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        return httpx.Response(503, json={"error": "synthetic unavailable"})

    enqueue(database)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unavailable), trust_env=False
    ) as client:
        pipeline, runtime, llm = build_pipeline(database, client)
        try:
            claim = pipeline.claim_batch(limit=1)
            assert claim is not None
            outcome = await pipeline.evaluate_claim(claim, _build_profile())
            result = await pipeline.complete_claim(outcome, admission_limit=10)
            assert result["cached"] == 0
            assert llm.calls == 0
            assert len(http_calls) == 1
            assert not pipeline.discovery_engine._eval_cache
            assert database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0
            row = database.conn.execute(
                "SELECT status, eval_error, relevance_score, temporal_evidence_complete "
                "FROM discovery_candidates"
            ).fetchone()
            assert row["status"] == "pending_eval"
            assert row["eval_error"] == "evaluation_response_missing"
            assert row["temporal_evidence_complete"] == 0
            assert "server_error" in caplog.text
        finally:
            await runtime.aclose()


async def test_long_chinese_excerpts_are_evaluated_once_and_keep_original_source(
    database: Database,
) -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return mock_success(request)

    original = "\n".join(f"背景第{i}段讲解算法的通用概念。" for i in range(350))
    enqueue(database, body_text=original)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False) as client:
        pipeline, runtime, llm = build_pipeline(database, client)
        try:
            claim = pipeline.claim_batch(limit=1)
            assert claim is not None
            outcome = await pipeline.evaluate_claim(claim, _build_profile())
            result = await pipeline.complete_claim(outcome, admission_limit=10)
            assert result["cached"] == 1
            assert len(calls) == 1 and llm.calls == 0
            state = json.loads(calls[0].content)["state"]
            assert state["untrusted_candidates"][0]["content"]["text_coverage"]["is_partial"]
            # Original source survives admission; the model's local view never
            # replaces durable candidate or content-cache text.
            assert (
                database.conn.execute("SELECT body_text FROM discovery_candidates").fetchone()[0]
                == original
            )
            assert (
                database.conn.execute("SELECT body_text FROM content_cache").fetchone()[0]
                == original
            )
        finally:
            await runtime.aclose()


async def test_long_chinese_candidate_is_pending_without_any_http_request(
    database: Database, caplog: Any
) -> None:
    http_calls: list[httpx.Request] = []

    def forbidden(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        raise AssertionError("Oversized context must never reach the HTTP provider")

    body_text = "完整中文不能悄悄截断" * 3000
    enqueue(database, body_text=body_text)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(forbidden), trust_env=False
    ) as client:
        pipeline, runtime, llm = build_pipeline(database, client)
        try:
            claim = pipeline.claim_batch(limit=1)
            assert claim is not None
            outcome = await pipeline.evaluate_claim(claim, _build_profile())
            result = await pipeline.complete_claim(outcome, admission_limit=10)
            assert result["cached"] == 0
            assert not http_calls
            assert llm.calls == 0
            assert not pipeline.discovery_engine._eval_cache
            row = database.conn.execute(
                "SELECT status, eval_error, body_text FROM discovery_candidates"
            ).fetchone()
            assert row["status"] == "pending_eval"
            assert row["eval_error"] == "evaluation_response_missing"
            assert row["body_text"] == body_text
            assert database.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 0
            assert "input_budget_exhausted" in caplog.text
        finally:
            await runtime.aclose()
