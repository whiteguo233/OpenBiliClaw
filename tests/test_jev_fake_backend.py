"""Real loopback HTTP acceptance; deterministic fixtures, never real Jev inference."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from scripts.jev_fake_backend import FakeJevBackend

from openbiliclaw.config import JevEvaluationConfig
from openbiliclaw.decision.jev_provider import JevDecisionProvider
from openbiliclaw.decision.models import (
    ChoiceQuestion,
    DecisionRequest,
    NoulQuestion,
    ScoreQuestion,
)
from openbiliclaw.decision.runtime import BoundedDecisionProvider
from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import ContentDiscoveryEngine, DiscoveredContent
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.storage.database import Database

from .test_jev_evaluator_http_pipeline import BombLLM

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MODEL = "jev-1.13.0"
FIELDS = {
    "score",
    "reason",
    "topic",
    "franchise",
    "style",
    "temporal_class",
    "temporal_confidence",
    "validity",
    "evidence",
    "deadline",
}
NEGATIVE = "只屏蔽承诺保证收益的广告；风险分析和批判不属于此项"


def _build_profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag("算法与数据结构", "编程", 0.9)],
            disliked_topics=[NEGATIVE],
        )
    )


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "loopback-jev.db")
    db.initialize()
    try:
        yield db
    finally:
        db.close()


def enqueue(database: Database, bodies: list[str]) -> None:
    database.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVJEVLOCAL{i}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"BVJEVLOCAL{i}",
                title=f"Python 算法与数据结构原理 {i}",
                body_text=body,
            )
            for i, body in enumerate(bodies)
        ]
    )


async def test_description_changes_after_400_characters_invalidate_jev_cache() -> None:
    prefix = "算法基础的背景解释。\n" * 60
    before = "只适用于离线教学；生产环境不可用。"
    after = "已支持生产环境，但必须先备份。"
    with FakeJevBackend(model=MODEL) as backend:
        native = JevDecisionProvider(
            api_key="synthetic-test-key", model=MODEL, endpoint_url=backend.url, max_attempts=1
        )
        engine = ContentDiscoveryEngine(
            eval_scorer="jev",
            jev_evaluator=JevContentEvaluator(native, model=MODEL, config=JevEvaluationConfig()),
        )

        def content(tail: str) -> DiscoveredContent:
            return DiscoveredContent(
                bvid="BVJEVTAIL",
                title="算法原理",
                description=prefix + tail,
                source_strategy="search",
            )

        try:
            assert await engine.evaluate_content_batch([content(before)], _build_profile()) == [
                0.75
            ]
            assert await engine.evaluate_content_batch([content(before)], _build_profile()) == [
                0.75
            ]
            assert await engine.evaluate_content_batch([content(after)], _build_profile()) == [0.75]
            # An identical source reuses its result; a changed tail requires new evidence.
            assert len(backend.requests) == 2
            for request, expected in zip(backend.requests, [before, after], strict=True):
                visible = "".join(
                    span["text"]
                    for span in request["state"]["untrusted_candidates"][0]["content"][
                        "text_spans"
                    ].values()
                )
                assert expected in visible
        finally:
            await native.aclose()


async def evaluate(
    database: Database,
    backend: FakeJevBackend,
    *,
    config: JevEvaluationConfig | None = None,
) -> tuple[dict[str, int], BombLLM, list[dict[str, Any]]]:
    async with backend.client() as client:
        native = JevDecisionProvider(
            api_key="synthetic-test-key",
            model=backend.model,
            max_attempts=1,
            timeout_seconds=3,
            client=client,
        )
        runtime = BoundedDecisionProvider(native, model=backend.model)
        llm = BombLLM()
        engine = ContentDiscoveryEngine(
            database=database,
            eval_scorer="jev",
            llm_service=llm,
            jev_evaluator=JevContentEvaluator(
                runtime,
                model=backend.model,
                config=config or JevEvaluationConfig(),
            ),
        )
        pipeline = DiscoveryCandidatePipeline(
            database=database,
            discovery_engine=engine,
            pool_target_count=30,
        )
        try:
            claim = pipeline.claim_batch(limit=10)
            assert claim is not None
            outcome = await pipeline.evaluate_claim(claim, _build_profile())
            receipt = await pipeline.complete_claim(outcome, admission_limit=10)
            rows = database.get_discovery_candidates_by_ids([row["id"] for row in claim.rows])
            return receipt, llm, rows
        finally:
            await runtime.aclose()


async def test_loopback_backend_accepts_native_wire_and_returns_all_three_primitives() -> None:
    request = DecisionRequest(
        request_id="local-fixture",
        purpose="EVALUATE",
        question_schema_version="local-fixture-v1",
        state={"text": "这是本机合成数据。"},
        questions={
            "pick": ChoiceQuestion(instructions="Select a fixture.", criteria={"a": "A", "b": "B"}),
            "rate": ScoreQuestion(
                instructions="Rate a fixture.", criteria=("low", "medium", "high")
            ),
            "yes": NoulQuestion(instructions="Is this synthetic?"),
        },
    )
    with FakeJevBackend(model=MODEL) as backend:
        async with backend.client() as client:
            provider = JevDecisionProvider(
                api_key="synthetic-test-key", model=MODEL, max_attempts=1, client=client
            )
            result = await provider.evaluate(request)
            await provider.aclose()
            assert not client.is_closed  # The real provider borrows the client.
        assert result.status == "OK"
        assert result.input_digest == request.input_digest
        assert result.actual_model == MODEL
        assert set(result.answers) == set(request.questions)
        assert result.answers["pick"].type == "choice"
        assert result.answers["rate"].type == "score"
        assert result.answers["yes"].type == "noul"
        assert result.attempts == 1 and result.http_status == 200
        assert len(backend.requests) == 1
        assert set(backend.requests[0]) == {"model", "state", "questions"}
        assert backend.records[0]["status"] == 200
        assert backend.url.startswith("http://127.0.0.1:")


@pytest.mark.parametrize(
    ("model", "cap"),
    [(MODEL, 12_000), (MODEL, 18_000), ("jev-1.14.0", 12_000)],
)
async def test_full_pipeline_adapts_to_configured_capacity_without_repeated_candidates(
    database: Database,
    model: str,
    cap: int,
) -> None:
    paragraphs = [f"背景段落{i}讲解算法设计的通用原理。" for i in range(350)]
    paragraphs[200] = "如果用于生产，必须先备份；否则不适用。"
    original = "\n".join(paragraphs)
    enqueue(database, ["排序算法的基本概念。", "链表的通用原理。", original])
    with FakeJevBackend(model=model, max_state_question_bytes=cap) as backend:
        receipt, llm, rows = await evaluate(
            database,
            backend,
            config=JevEvaluationConfig(max_state_question_bytes=cap),
        )
        assert receipt == {"evaluated": 3, "cached": 3, "rejected": 0, "stale": 0}
        assert llm.calls == 0
        assert len(backend.requests) >= 2
        seen = []
        partial = []
        for request, record in zip(backend.requests, backend.records, strict=True):
            assert record["status"] == 200
            assert record["max_state_question_bytes"] <= cap
            assert record["request_bytes"] <= 48_000
            assert request["model"] == model
            assert len(request["questions"]) % 10 == 0
            assert request["state"]["profile"]
            assert request["state"]["profile"]["disliked_topics"] == [NEGATIVE]
            assert request["state"]["candidate_profiles"]
            for candidate in request["state"]["untrusted_candidates"]:
                ref = candidate["candidate_ref"]
                fields = {
                    key.split(".")[1] for key in request["questions"] if key.startswith(ref + ".")
                }
                assert fields == FIELDS
                seen.append(candidate["content"]["content_id"])
                partial.append(candidate["content"]["text_coverage"]["is_partial"])
                if candidate["content"]["content_id"] == "BVJEVLOCAL2":
                    visible = "".join(
                        span["text"] for span in candidate["content"]["text_spans"].values()
                    )
                    assert "如果用于生产，必须先备份；" in visible
                    assert "否则不适用。" in visible
        assert sorted(seen) == [f"BVJEVLOCAL{i}" for i in range(3)]
        assert any(partial)
        for source in (rows, database.get_cached_content()):
            row = next(row for row in source if row["content_id"] == "BVJEVLOCAL2")
            assert row["body_text"] == original
            assert row["relevance_score"] == 0.75
            assert row["temporal_evidence_complete"] == 1


@pytest.mark.parametrize(
    "mode",
    [
        "server_error",
        "missing_answer",
        "wrong_model",
        "unknown_choice",
        "bad_score_legend",
        "bad_distribution",
        "extra_answer",
    ],
)
async def test_invalid_or_failed_backend_leaves_the_whole_batch_pending(
    database: Database,
    mode: str,
) -> None:
    enqueue(database, ["算法基础概念。", "链表通用原理。"])
    with FakeJevBackend(model=MODEL, mode=mode) as backend:
        receipt, llm, rows = await evaluate(database, backend)
        assert receipt["cached"] == receipt["rejected"] == 0
        assert llm.calls == 0
        assert len(backend.requests) == 1
        assert len(rows) == 2
        assert all(row["status"] == "pending_eval" for row in rows)
        assert all(row["eval_error"] == "evaluation_response_missing" for row in rows)
        assert all(row["temporal_evidence_complete"] == 0 for row in rows)
        assert not database.get_cached_content()


async def test_remote_capacity_rejection_is_not_a_score_or_an_automatic_second_evaluation(
    database: Database,
) -> None:
    enqueue(database, ["算法基础概念。"])
    with FakeJevBackend(model=MODEL, max_request_bytes=1) as backend:
        receipt, llm, rows = await evaluate(database, backend)
        assert receipt["cached"] == receipt["rejected"] == 0
        assert len(backend.requests) == 1
        assert backend.records[0]["status"] == 422
        assert llm.calls == 0
        assert rows[0]["status"] == "pending_eval"


@pytest.mark.parametrize("guard", ["indivisible_source", "question_bytes", "mandatory_profile"])
async def test_local_overflow_does_not_reach_the_real_socket(
    database: Database,
    guard: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "无法安全拆开的完整正文" * 3000 if guard == "indivisible_source" else "算法基础。"
    enqueue(database, [original])
    config = JevEvaluationConfig(max_question_bytes=256 if guard == "question_bytes" else 0)
    if guard == "mandatory_profile":
        profile = _build_profile()
        profile.preferences.disliked_topics = ["不能删除的完整明确负偏好" * 3000]
        monkeypatch.setattr(__name__ + "._build_profile", lambda: profile)
    with FakeJevBackend(model=MODEL) as backend:
        receipt, llm, rows = await evaluate(database, backend, config=config)
        assert receipt["cached"] == receipt["rejected"] == 0
        assert not backend.requests and not backend.records
        assert llm.calls == 0
        row = rows[0]
        assert row["status"] == "pending_eval" and row["body_text"] == original


async def test_bridge_rejects_non_native_destinations_and_real_credentials() -> None:
    with FakeJevBackend(model=MODEL) as backend:
        async with backend.client() as client:
            for url, key in [
                ("https://example.com/", "synthetic-test-key"),
                ("https://api.typesafe.ai/v1/systemone", "not-the-synthetic-key"),
            ]:
                with pytest.raises(ValueError, match="synthetic key"):
                    await client.post(url, json={}, headers={"Authorization": f"Bearer {key}"})
        async with httpx.AsyncClient(trust_env=False) as direct:
            response = await direct.post(backend.url, json={})
            assert response.status_code == 401
        assert not backend.requests


async def test_fake_runner_reports_two_successful_cohorts_with_no_real_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.validate_jev_single_pass import run_fake

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    report = await run_fake(MODEL)
    assert report["status"] == "passed"
    assert report["live"] is False
    assert report["backend"] == "local_fake"
    assert report["usage_is_synthetic"] is True
    assert len(report["cases"]) == 2
    assert all(case["pipeline"]["cached"] == 3 for case in report["cases"])
    assert all(case["pending"] == 0 and case["original_preserved"] for case in report["cases"])
    planned = [request for case in report["cases"] for request in case["requests"]]
    assert len(planned) == len(report["local_http"]) == 4
    assert all(row["status"] == 200 for row in report["local_http"])
    assert [row["request_bytes"] for row in planned] == [
        row["request_bytes"] for row in report["local_http"]
    ]


async def test_valid_low_score_is_rejected_not_confused_with_a_technical_failure(
    database: Database,
) -> None:
    enqueue(database, ["算法基础概念。", "链表通用原理。"])
    with FakeJevBackend(model=MODEL, mode="low_score") as backend:
        receipt, llm, rows = await evaluate(database, backend)
        assert receipt == {"evaluated": 2, "cached": 0, "rejected": 2, "stale": 0}
        assert len(backend.requests) == 1 and llm.calls == 0
        assert all(row["status"] == "rejected_low_score" for row in rows)
        assert all(row["temporal_evidence_complete"] == 1 for row in rows)
        assert not database.get_cached_content()
