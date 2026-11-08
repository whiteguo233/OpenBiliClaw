"""Runtime integration using real SQLite/candidate/controller seams, no model network."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import Config
from openbiliclaw.decision.models import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionEvidence,
    DecisionRequest,
    DecisionUsage,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.director.runtime import DirectorRuntime, build_director_runtime
from openbiliclaw.recommendation.director.store import StoreConflict
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.soul.profile import SoulProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class FakeAgent:
    """External generation stub: a concrete reversed order, not baseline ranking."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("offline fixture unavailable")
        request = json.loads(kwargs["user_input"])
        refs = [card["ref"] for card in reversed(request["untrusted_candidates"])]
        count = request["target_count"]
        screens = []
        for index in range(request["horizon_screens"]):
            selected = refs[index * count : (index + 1) * count]
            if not selected:
                break
            screens.append(
                {
                    "direction": f"Designed chapter {index}",
                    "pairs": [
                        {"card_refs": selected[offset : offset + 2], "intent": "Example then idea"}
                        for offset in range(0, len(selected), 2)
                    ],
                }
            )
        return LLMResponse(
            content=json.dumps(
                {
                    "screens": screens,
                    "completion": {
                        "reason": "target_reached"
                        if len(refs) >= count * request["horizon_screens"]
                        else "supply_exhausted"
                    },
                }
            ),
            model="fixture-agent.v1",
            provider="fixture",
            usage={"input_tokens": 10, "output_tokens": 10},
        )


class FakeDecision:
    """External decision stub: keep legal drafts and choose first legal alternative."""

    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []
        self.closed = False
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request.model_copy(deep=True))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        answers = {}
        for name, question in request.questions.items():
            assert isinstance(question, ChoiceQuestion)
            choice = "keep" if "keep" in question.criteria else next(iter(question.criteria))
            answers[name] = ChoiceAnswer(
                choice=choice,
                probabilities={key: float(key == choice) for key in question.criteria},
                confidence=1.0,
            )
        return DecisionEvidence(
            status="OK",
            reason_code="fixture",
            request_id=request.request_id,
            purpose=request.purpose,
            question_schema_version=request.question_schema_version,
            input_digest=request.input_digest,
            requested_model="fixture-decision.v1",
            actual_model="fixture-decision.v1",
            answers=answers,
            usage=DecisionUsage(input_tokens=10, output_tokens=2),
            latency_ms=1.0,
            attempts=1,
        )

    async def aclose(self) -> None:
        self.closed = True


def seed_database(path: Path, count: int = 24) -> Database:
    database = Database(path)
    database.initialize()
    for index in range(count):
        database.cache_content(
            f"BV{index:010d}",
            title=f"Synthetic card {index}",
            description=f"Example {index}",
            source="search",
            source_platform="bilibili",
            content_id=f"BV{index:010d}",
            content_url=f"https://www.bilibili.com/video/BV{index:010d}",
            relevance_score=0.95 - index / 1000,
            pool_expression=f"Reason {index}",
            pool_topic_label="Testing",
            style_key="tutorial",
            topic_group=f"topic{index % 8}",
            temporal_class="evergreen",
            temporal_confidence=0.99,
            temporal_reason="Timeless synthetic learning material",
        )
    return database


def make_runtime(
    database: Database,
    *,
    strategy: str = "agent_only",
    mode: str = "enforce",
    llm: FakeAgent | None = None,
    provider: FakeDecision | None = None,
    horizon_screens: int = 3,
) -> DirectorRuntime:
    config = Config()
    config.director.mode = mode
    config.director.strategy = strategy
    config.director.planning_horizon_screens = horizon_screens
    agent = llm or FakeAgent()
    return DirectorRuntime(
        config, database, RecommendationEngine(agent, database), agent, provider=provider
    )


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
async def test_three_modes_generate_restore_and_advance_real_screens(
    tmp_path: Path, strategy: str
) -> None:
    db = seed_database(tmp_path / "runtime.db")
    agent, provider = FakeAgent(), FakeDecision()
    runtime = make_runtime(db, strategy=strategy, llm=agent, provider=provider)
    try:
        assert await runtime.restore("stream") is None
        first = await runtime.generate(
            SoulProfile(), stream_ref="stream", request_id="one", limit=4
        )
        assert first is not None and len(first.items) == 4
        assert first.requested_strategy == strategy
        assert first.state_revision == 0
        assert runtime.store.session("stream").real_feedback == ()
        assert await runtime.restore("stream") == first
        assert (
            await runtime.generate(SoulProfile(), stream_ref="stream", request_id="one", limit=4)
            == first
        )
        second = await runtime.generate(
            SoulProfile(), stream_ref="stream", request_id="two", limit=4
        )
        assert second is not None and second.experience_step_seq == 2
        assert not (
            {row["item_key"] for row in first.items} & {row["item_key"] for row in second.items}
        )
        assert db.count_recommendations() == 8
        if strategy == "jev_recurrent":
            assert first.execution_binding == "SESSION_STEP" and first.plan_id is None
            assert not agent.calls
            assert provider.requests
        else:
            assert first.execution_binding == "PLAN_SLOT" and first.plan_id
            assert first.direction == "Designed chapter 0"
            assert second.direction == "Designed chapter 1"
            assert agent.calls
            assert bool(provider.requests) == (strategy == "agent_jev")
    finally:
        await runtime.aclose()
        db.close()


def test_default_routes_do_not_construct_provider_or_director(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    db = seed_database(tmp_path / "off.db", count=0)
    try:
        agent = FakeAgent()
        assert build_director_runtime(Config(), db, RecommendationEngine(agent, db), agent) is None
        assert not agent.calls
        assert db.count_recommendations() == 0
    finally:
        db.close()


async def test_agent_only_requires_no_jev_credentials_and_does_not_call_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    db = seed_database(tmp_path / "agent-only.db")
    runtime = make_runtime(db)
    try:
        batch = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4)
        assert batch is not None and batch.requested_strategy == "agent_only"
        assert batch.realized_strategy == "agent_only"
        assert runtime.provider is None
    finally:
        await runtime.aclose()
        db.close()


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
async def test_stay_keeps_direction_without_consuming_next_experience_step(
    tmp_path: Path, strategy: str
) -> None:
    db = seed_database(tmp_path / "stay.db")
    runtime = make_runtime(db, strategy=strategy, provider=FakeDecision())
    try:
        root = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=4)
        assert root is not None
        replacement = await runtime.generate(
            SoulProfile(), stream_ref="s", request_id="r2", intent="STAY", limit=4
        )
        assert replacement is not None
        assert replacement.direction_ref == root.direction_ref
        assert replacement.root_batch_ref == root.batch_ref
        assert replacement.committed_batch_seq == 2
        assert replacement.experience_step_seq == 1
        assert replacement.execution_binding == (
            "SESSION_STAY" if strategy == "jev_recurrent" else "STAY_PATCH"
        )
        assert not (
            {row["item_key"] for row in root.items} & {row["item_key"] for row in replacement.items}
        )
        assert await runtime.restore("s", root.batch_ref) == root
    finally:
        await runtime.aclose()
        db.close()


async def test_shadow_records_agent_proposal_but_commits_baseline(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "shadow.db")
    runtime = make_runtime(db, mode="shadow")
    try:
        result = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=4)
        assert result is not None and result.realized_strategy == "deterministic"
        assert result.plan_id is None
        trace = json.loads(result.trace_json)
        assert trace["shadow_proposal"]["requested_strategy"] == "agent_only"
        assert trace["served_baseline"]["realized_strategy"] == "deterministic"
    finally:
        await runtime.aclose()
        db.close()


async def test_request_id_cannot_replay_different_exclusions(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "request-id.db")
    runtime = make_runtime(db)
    try:
        await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4)
        with pytest.raises(StoreConflict, match="request_id_reused"):
            await runtime.generate(
                SoulProfile(),
                stream_ref="s",
                request_id="r",
                limit=4,
                excluded_keys=frozenset({"bilibili:BV0000000023"}),
            )
        assert db.count_recommendations() == 4
    finally:
        await runtime.aclose()
        db.close()


async def test_cancelled_generation_never_commits_and_same_request_can_retry(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "cancel.db")
    provider = FakeDecision()
    provider.release = asyncio.Event()
    runtime = make_runtime(db, strategy="jev_recurrent", provider=provider)
    try:
        task = asyncio.create_task(
            runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await runtime.restore("s") is None
        assert db.count_recommendations() == 0
        provider.release.set()
        batch = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4)
        assert batch is not None and batch.committed_batch_seq == 1
    finally:
        await runtime.aclose()
        db.close()


async def test_new_runtime_fences_old_inflight_model_result(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "reload.db")
    provider = FakeDecision()
    provider.release = asyncio.Event()
    old = make_runtime(db, strategy="jev_recurrent", provider=provider)
    new: DirectorRuntime | None = None
    try:
        task = asyncio.create_task(
            old.generate(SoulProfile(), stream_ref="s", request_id="old", limit=4)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        new = make_runtime(db, strategy="agent_only")
        provider.release.set()
        with pytest.raises(StoreConflict, match="generation_stale"):
            await task
        assert db.count_recommendations() == 0
        committed = await new.generate(SoulProfile(), stream_ref="s", request_id="new", limit=4)
        assert committed is not None and committed.requested_strategy == "agent_only"
    finally:
        await old.aclose()
        if new is not None:
            await new.aclose()
        db.close()


async def test_no_candidates_returns_shortage_without_advancing_session(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "empty.db", count=0)
    runtime = make_runtime(db)
    try:
        assert (
            await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4) is None
        )
        assert runtime.store.session("s").experience_step_seq == 0
        assert runtime.store.session("s").committed_batch_seq == 0
        assert runtime.store.load_plan("s") is None
        assert (
            await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4) is None
        )
    finally:
        await runtime.aclose()
        db.close()


async def test_actual_click_enters_next_recurrent_state_without_changing_committed_screen(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "actual-feedback.db")
    provider = FakeDecision()
    runtime = make_runtime(db, strategy="jev_recurrent", provider=provider)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=4)
        assert first is not None
        assert not runtime.store.session("s").real_feedback
        runtime.store.feedback(first.batch_ref, "click1", str(first.items[0]["item_key"]), "click")
        second = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r2", limit=4)
        assert second is not None and second.state_revision == 1
        decisions = [
            request for request in provider.requests if request.request_id.startswith("r2:")
        ]
        assert decisions
        for request in decisions:
            context = request.state["session_context"]
            assert len(context["real_feedback"]) == 1
            assert context["real_feedback"][0]["kind"] == "click"
            assert context["real_feedback"][0]["batch_ref"] == first.batch_ref
        assert await runtime.restore("s", first.batch_ref) == first
    finally:
        await runtime.aclose()
        db.close()


async def test_feedback_during_provider_call_rejects_stale_screen_then_retries_latest_state(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "feedback-race.db")
    provider = FakeDecision()
    runtime = make_runtime(db, strategy="jev_recurrent", provider=provider)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=4)
        assert first is not None
        provider.started.clear()
        provider.release = asyncio.Event()
        task = asyncio.create_task(
            runtime.generate(SoulProfile(), stream_ref="s", request_id="r2", limit=4)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        runtime.store.feedback(
            first.batch_ref, "dislike1", str(first.items[0]["item_key"]), "dislike"
        )
        provider.release.set()
        with pytest.raises(StoreConflict, match="generation_stale"):
            await task
        assert db.count_recommendations() == 4
        assert await runtime.restore("s") == first
        retried = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r2", limit=4)
        assert retried is not None and retried.state_revision == 1
        assert retried.committed_batch_seq == 2
    finally:
        await runtime.aclose()
        db.close()


async def test_agent_failure_falls_back_without_calling_jev_in_pure_agent_mode(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "agent-failure.db")
    agent, provider = FakeAgent(), FakeDecision()
    agent.fail = True
    runtime = make_runtime(db, llm=agent, provider=provider)
    try:
        batch = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r", limit=4)
        assert batch is not None
        assert batch.realized_strategy == "deterministic"
        assert batch.plan_id is None
        assert not provider.requests
        assert runtime.store.load_plan("s") is None
    finally:
        await runtime.aclose()
        db.close()


@pytest.mark.parametrize("count", [4, 10])
async def test_presentation_of_previous_screen_does_not_discard_agent_horizon(
    tmp_path: Path,
    count: int,
) -> None:
    db = seed_database(tmp_path / "present-plan.db", count=40)
    agent = FakeAgent()
    # A two-screen fixture isolates ACK semantics from rolling replenishment
    # requested for a partially supplied third screen in the bounded shortlist.
    runtime = make_runtime(db, llm=agent, horizon_screens=2)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r1", limit=count)
        assert first is not None
        for index, row in enumerate(first.items):
            runtime.store.present(first.batch_ref, f"ack-{index}", [str(row["item_key"])])
        second = await runtime.generate(SoulProfile(), stream_ref="s", request_id="r2", limit=count)
        assert second is not None
        assert second.plan_id == first.plan_id
        assert second.direction == "Designed chapter 1"
        assert second.realized_strategy == "agent_only"
        assert len(agent.calls) == 1
    finally:
        await runtime.aclose()
        db.close()


async def test_same_config_runtime_reconstruction_preserves_unconsumed_plan(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "restart-plan.db")
    first_agent, second_agent = FakeAgent(), FakeAgent()
    first_runtime = make_runtime(db, llm=first_agent)
    second_runtime = None
    try:
        first = await first_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="first", limit=4
        )
        assert first is not None
        saved = first_runtime.store.load_plan("s")
        assert saved is not None
        await first_runtime.aclose()
        second_runtime = make_runtime(db, llm=second_agent)
        assert second_runtime.store.load_plan("s") == saved
        second = await second_runtime.generate(
            SoulProfile(), stream_ref="s", request_id="second", limit=4
        )
        assert second is not None and second.plan_id == first.plan_id
        assert second.direction == "Designed chapter 1"
        assert not second_agent.calls
    finally:
        await first_runtime.aclose()
        if second_runtime is not None:
            await second_runtime.aclose()
        db.close()


async def test_preference_survives_ack_burst_and_only_replans_agent_once(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "ack-burst.db", count=70)
    agent = FakeAgent()
    runtime = make_runtime(db, llm=agent, horizon_screens=2)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="first", limit=10)
        assert first is not None
        runtime.store.feedback(first.batch_ref, "like", str(first.items[0]["item_key"]), "like")
        for index, row in enumerate(first.items):
            runtime.store.present(first.batch_ref, f"first-{index}", [str(row["item_key"])])
        second = await runtime.generate(
            SoulProfile(), stream_ref="s", request_id="second", limit=10
        )
        assert second is not None and second.plan_id != first.plan_id
        assert len(agent.calls) == 2
        for index, row in enumerate(second.items):
            runtime.store.present(second.batch_ref, f"second-{index}", [str(row["item_key"])])
        third = await runtime.generate(SoulProfile(), stream_ref="s", request_id="third", limit=10)
        assert third is not None and third.plan_id == second.plan_id
        assert len(agent.calls) == 2  # The previous like was already captured by this plan.
    finally:
        await runtime.aclose()
        db.close()


async def test_model_inflight_body_text_change_rejects_old_evidence(tmp_path: Path) -> None:
    db = seed_database(tmp_path / "body-race.db")
    db.conn.execute("UPDATE content_cache SET description='', body_text='Original context'")
    db.conn.commit()
    agent = FakeAgent()
    agent.release = asyncio.Event()
    runtime = make_runtime(db, llm=agent)
    try:
        task = asyncio.create_task(
            runtime.generate(SoulProfile(), stream_ref="s", request_id="first", limit=4)
        )
        await asyncio.wait_for(agent.started.wait(), timeout=3)
        db.conn.execute("UPDATE content_cache SET body_text='Changed after model input'")
        db.conn.commit()
        agent.release.set()
        with pytest.raises(StoreConflict, match="candidate_features_changed"):
            await task
        assert db.count_recommendations() == 0
        assert await runtime.restore("s") is None
    finally:
        await runtime.aclose()
        db.close()
