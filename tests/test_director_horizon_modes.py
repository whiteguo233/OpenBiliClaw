"""Synthetic, offline tests for concrete Agent plans and three director modes."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from openbiliclaw.decision.models import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionEvidence,
    DecisionRequest,
    DecisionUsage,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.director.controller import (
    DirectorController,
    plan_needs_replanning,
)
from openbiliclaw.recommendation.director.horizon_models import (
    DirectorProposal,
    DirectorStrategy,
    HorizonPlan,
    PlannedPair,
    ScreenDraft,
)
from openbiliclaw.recommendation.director.models import (
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    RealFeedback,
    ScreenConstraints,
    TopicQuota,
    digest,
)
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.recommendation.director.policy import DIRECTOR_POLICY_VERSION

if TYPE_CHECKING:
    from collections.abc import Callable


def _request(count: int = 24, target: int = 6, **changes: object) -> CompositionRequest:
    value = CompositionRequest(
        request_id="fixture-request",
        session=CompositionSession(
            session_ref="fixture-session",
            stream_ref="fixture-stream",
            state_revision=4,
            feedback_cursor="event_4",
            configuration_revision=2,
            explicit_intent="今天先看工具，不看长篇理论",
        ),
        candidate_set_ref="fixture-candidates",
        candidate_revision=2,
        feature_schema_version="fixture.v1",
        direction_ref="fixture-direction",
        direction="先体验，再实践，最后扩展",
        cards=tuple(
            CompositionCard(
                ref=f"c{i}",
                item_key=f"fixture:{i}",
                title=f"合成卡片 {i}",
                summary="合成内容摘要",
                topic_group="tools" if i % 2 == 0 else "explore",
                baseline_score=float(count - i),
            )
            for i in range(count)
        ),
        constraints=ScreenConstraints(version="constraints_1"),
        target_count=target,
    )
    return CompositionRequest.model_validate({**value.model_dump(mode="python"), **changes})


def _horizon(request: CompositionRequest, screens: int = 3) -> HorizonPlan:
    drafts = tuple(
        ScreenDraft(
            screen_id=f"screen_{i}",
            direction=f"未来屏 {i} 的方向",
            target_count=request.target_count,
            pairs=tuple(
                PlannedPair(
                    card_refs=tuple(
                        card.ref
                        for card in request.cards[
                            i * request.target_count + j : min(
                                i * request.target_count + j + 2,
                                (i + 1) * request.target_count,
                            )
                        ]
                    ),
                    intent=f"屏{i}对{j // 2}",
                )
                for j in range(
                    0,
                    min(
                        request.target_count, max(0, len(request.cards) - i * request.target_count)
                    ),
                    2,
                )
            ),
        )
        for i in range(screens)
    )
    return HorizonPlan(
        plan_id="fixture_plan",
        session_ref=request.session.session_ref,
        stream_ref=request.session.stream_ref,
        configuration_revision=request.session.configuration_revision,
        planning_state_revision=request.session.state_revision,
        planning_policy_version=DIRECTOR_POLICY_VERSION,
        planning_feedback_cursor=request.session.feedback_cursor,
        planning_explicit_intent=request.session.explicit_intent,
        planning_input_digest=request.input_digest,
        candidate_set_ref=request.candidate_set_ref,
        feature_schema_version=request.feature_schema_version,
        constraints_digest=digest(request.constraints.model_dump(mode="json")),
        cards=request.cards,
        screens=drafts,
        created_at=100.0,
        expires_at=400.0,
        planner_model="fixture-agent.v1",
    )


def _evidence(request: DecisionRequest, choice: str | None = None) -> DecisionEvidence:
    key = next(iter(request.questions))
    question = request.questions[key]
    assert isinstance(question, ChoiceQuestion)
    choice = choice or ("keep" if "keep" in question.criteria else next(iter(question.criteria)))
    return DecisionEvidence(
        status="OK",
        reason_code="fixture",
        request_id=request.request_id,
        purpose=request.purpose,
        question_schema_version=request.question_schema_version,
        input_digest=request.input_digest,
        requested_model="fixture-jev.v1",
        actual_model="fixture-jev.v1",
        answers={
            key: ChoiceAnswer(
                choice=choice,
                probabilities={option: float(option == choice) for option in question.criteria},
                confidence=1.0,
            )
        },
        usage=DecisionUsage(input_tokens=10, output_tokens=2),
        latency_ms=1.0,
        attempts=1,
    )


class _Provider:
    def __init__(self, choose: Callable[[DecisionRequest], DecisionEvidence] = _evidence) -> None:
        self.choose = choose
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        self.requests.append(request)
        return self.choose(request)

    async def aclose(self) -> None:
        pass


class _LLM:
    def __init__(self, output: str, *, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResponse(
            content=self.output,
            model="fixture-agent.v1",
            provider="fixture",
            usage={"input_tokens": 20},
        )


def _planner_output(request: CompositionRequest, screens: int = 3) -> str:
    return json.dumps(
        {
            "screens": [
                {
                    "direction": screen.direction,
                    "pairs": [pair.model_dump(mode="json") for pair in screen.pairs],
                }
                for screen in _horizon(request, screens).screens
            ],
            "completion": {"reason": "target_reached"},
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_three_modes_with_decision_provider_enabled_or_disabled(
    strategy: DirectorStrategy, enabled: bool
) -> None:
    request = _request()
    provider = _Provider()
    result = await DirectorController(provider if enabled else None).prepare_next(
        request, strategy=strategy, horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.status == "COMPLETE"
    assert result.proposal.requested_strategy == strategy
    assert result.proposal.card_refs == tuple(f"c{i}" for i in range(6))
    if strategy == "jev_recurrent":
        assert result.horizon is None
        assert result.proposal.plan_id is None
        assert len(provider.requests) == (3 if enabled else 0)
    else:
        assert result.horizon is not None
        assert result.proposal.plan_id == "fixture_plan"
        assert result.proposal.direction == "未来屏 0 的方向"
        assert len(provider.requests) == (9 if enabled and strategy == "agent_jev" else 0)
    assert not result.replan_required


@pytest.mark.parametrize("strategy", ["agent_jev", "agent_only"])
async def test_missing_plan_falls_back_without_hidden_agent_or_pure_agent_jev(
    strategy: DirectorStrategy,
) -> None:
    provider = _Provider()
    result = await DirectorController(provider).prepare_next(_request(), strategy=strategy)
    assert result.replan_required
    assert result.horizon is None
    assert result.proposal.plan_id is None
    assert result.proposal.reason_code == "agent_plan_missing"
    assert len(provider.requests) == (3 if strategy == "agent_jev" else 0)
    assert result.proposal.realized_strategy == (
        "jev_recurrent" if strategy == "agent_jev" else "deterministic"
    )


async def test_agent_executes_actual_planned_order_not_baseline() -> None:
    request = _request()
    plan = _horizon(request, 1)
    screen = plan.screens[0].model_copy(update={"pairs": tuple(reversed(plan.screens[0].pairs))})
    plan = plan.model_copy(update={"screens": (screen,)})
    provider = _Provider()
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_only", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.card_refs == ("c4", "c5", "c2", "c3", "c0", "c1")
    assert result.proposal.realized_strategy == "agent_only"
    assert not provider.requests


async def test_one_card_middle_patch_reaudits_suffix_and_repairs_future_duplicate() -> None:
    request = _request()

    def choose(decision: DecisionRequest) -> DecisionEvidence:
        state = cast("dict[str, object]", decision.state)
        if state["screen_id"] == "screen_0" and state["pair_index"] == 1:
            if decision.purpose == "AUDIT_DRAFT":
                return _evidence(decision, "replace_right")
            for option in cast("list[dict[str, object]]", state["alternatives"]):
                if option["card_refs"] == ["c2", "c6"]:
                    return _evidence(decision, cast("str", option["option_id"]))
            raise AssertionError("expected one-card alternative using future screen card")
        return _evidence(decision)

    provider = _Provider(choose)
    original = _horizon(request)
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=original, now_epoch=200.0
    )
    assert result.proposal.card_refs == ("c0", "c1", "c2", "c6", "c4", "c5")
    assert result.proposal.pairs[1].origin == "decision"
    assert result.horizon is not None
    future_refs = tuple(ref for screen in result.horizon.screens for ref in screen.card_refs)
    assert len(set(future_refs)) == len(future_refs)
    assert original.screens[0].card_refs == tuple(f"c{i}" for i in range(6))
    suffix_audit = next(
        decision
        for decision in provider.requests
        if isinstance(decision.state, dict)
        and decision.state["screen_id"] == "screen_0"
        and decision.state["pair_index"] == 2
    )
    assert suffix_audit.state["tentative_prefix"] == ["c0", "c1", "c2", "c6"]
    patch = result.patches[0]
    assert patch.invalidate_from_pair == 1
    assert patch.affected_future_screens == ("screen_1", "screen_2")
    assert patch.base_plan_digest == original.content_digest
    assert patch.old_pairs_digest == digest(
        [pair.model_dump(mode="json") for pair in original.screens[0].pairs]
    )
    assert patch.state_revision == request.session.state_revision
    assert result.replan_required  # The consumed future card causes a hard conflict.


async def test_can_patch_middle_pair_of_second_future_screen() -> None:
    request = _request(max_provider_calls=10)

    def choose(decision: DecisionRequest) -> DecisionEvidence:
        state = cast("dict[str, object]", decision.state)
        if state["screen_id"] == "screen_1" and state["pair_index"] == 1:
            if decision.purpose == "AUDIT_DRAFT":
                return _evidence(decision, "replace_right")
            option = next(
                option
                for option in cast("list[dict[str, object]]", state["alternatives"])
                if cast("str", option["option_id"]).startswith("replace_right")
            )
            return _evidence(decision, cast("str", option["option_id"]))
        return _evidence(decision)

    provider = _Provider(choose)
    plan = _horizon(request)
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.card_refs == plan.screens[0].card_refs
    assert result.horizon is not None
    assert result.horizon.screens[1].pairs[0] == plan.screens[1].pairs[0]
    assert result.horizon.screens[1].pairs[1].card_refs != plan.screens[1].pairs[1].card_refs
    assert any(
        patch.screen_id == "screen_1" and patch.invalidate_from_pair == 1
        for patch in result.patches
    )


async def test_hard_blocks_override_zero_edit_budget_and_never_offer_blocked_keep() -> None:
    request = _request()
    plan = _horizon(request)
    request = request.model_copy(
        update={
            "constraints": ScreenConstraints(
                version="constraints_2", excluded_item_keys=("fixture:2",)
            )
        }
    )
    provider = _Provider()
    result = await DirectorController(provider, max_patch_pairs=0).prepare_next(
        request, strategy="agent_jev", horizon=plan, now_epoch=200.0
    )
    assert "c2" not in result.proposal.card_refs
    assert result.replan_required
    repair = next(decision for decision in provider.requests if decision.purpose == "REPAIR_PAIR")
    assert isinstance(repair.state, dict)
    assert all("c2" not in option["card_refs"] for option in repair.state["alternatives"])


async def test_soft_edit_budget_preserves_legal_pair_and_requests_agent_replan() -> None:
    request = _request()
    provider = _Provider(lambda decision: _evidence(decision, "replace_pair"))
    result = await DirectorController(provider, max_patch_pairs=0).prepare_next(
        request, strategy="agent_jev", horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.card_refs == tuple(f"c{i}" for i in range(6))
    assert result.replan_required
    assert not result.patches
    assert all(decision.purpose == "AUDIT_DRAFT" for decision in provider.requests)


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
async def test_latest_hard_constraints_repair_plan_even_without_provider(
    strategy: DirectorStrategy,
) -> None:
    request = _request()
    plan = _horizon(request)
    updated = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(update={"eligible": card.ref != "c3"}) for card in request.cards
            )
        }
    )
    result = await DirectorController().prepare_next(
        updated, strategy=strategy, horizon=plan, now_epoch=200.0
    )
    assert "c3" not in result.proposal.card_refs
    assert result.replan_required
    assert result.proposal.pairs[1].origin == "deterministic"
    assert result.proposal.provider_calls == 0


async def test_plan_survives_consumed_prefix_disappearance_and_new_candidate_snapshot() -> None:
    request = _request()
    original = _horizon(request)
    plan = original.model_copy(update={"screens": original.screens[1:]})
    changed = request.model_copy(
        update={
            "candidate_set_ref": "new_snapshot",
            "candidate_revision": 50,
            "cards": request.cards[6:],
        }
    )
    result = await DirectorController().prepare_next(
        changed, strategy="agent_only", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.card_refs == tuple(f"c{i}" for i in range(6, 12))
    assert result.proposal.realized_strategy == "agent_only"
    assert not result.replan_required


async def test_exact_card_content_change_invalidates_old_judgment_not_just_ref() -> None:
    request = _request()
    changed = request.model_copy(
        update={
            "cards": (
                request.cards[0].model_copy(update={"summary": "完全不同的内容"}),
                *request.cards[1:],
            )
        }
    )
    result = await DirectorController().prepare_next(
        changed, strategy="agent_only", horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.pairs[0].origin == "deterministic"
    assert result.replan_required


async def test_canonical_identity_rebinds_new_opaque_refs() -> None:
    request = _request()
    changed = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(update={"ref": f"new_{card.ref}"}) for card in request.cards
            )
        }
    )
    result = await DirectorController().prepare_next(
        changed, strategy="agent_only", horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.card_refs == tuple(f"new_c{i}" for i in range(6))
    assert result.proposal.realized_strategy == "agent_only"
    assert result.horizon is not None
    assert all(
        ref.startswith("new_") for screen in result.horizon.screens for ref in screen.card_refs
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"expires_at": 199.0}, "agent_plan_expired"),
        ({"configuration_revision": 4}, "agent_plan_binding_mismatch"),
        ({"stream_ref": "other_stream"}, "agent_plan_binding_mismatch"),
        ({"feature_schema_version": "v2"}, "agent_plan_binding_mismatch"),
    ],
)
async def test_expired_or_foreign_plan_not_reused(change: dict[str, object], reason: str) -> None:
    request = _request()
    provider = _Provider()
    result = await DirectorController(provider).prepare_next(
        request,
        strategy="agent_only",
        horizon=_horizon(request).model_copy(update=change),
        now_epoch=200.0,
    )
    assert result.horizon is None
    assert result.proposal.reason_code == reason
    assert result.replan_required
    assert not provider.requests


async def test_call_budget_caps_entire_window_and_keeps_valid_unaudited_suffix() -> None:
    request = _request(max_provider_calls=2)
    provider = _Provider()
    plan = _horizon(request)
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.provider_calls == len(provider.requests) == 2
    assert result.proposal.card_refs == plan.screens[0].card_refs
    assert result.proposal.pairs[-1].reason_code == "call_budget_exhausted"
    assert result.horizon.screens == plan.screens


async def test_expired_deadline_makes_no_decision_call() -> None:
    request = _request(deadline_monotonic=0.0)
    provider = _Provider()
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.provider_calls == 0
    assert result.proposal.realized_strategy == "agent_only"


async def test_provider_exceptions_cannot_edit_legal_drafts() -> None:
    def broken(request: DecisionRequest) -> DecisionEvidence:
        raise RuntimeError("private provider error")

    request = _request()
    result = await DirectorController(_Provider(broken)).prepare_next(
        request, strategy="agent_jev", horizon=_horizon(request), now_epoch=200.0
    )
    assert result.proposal.card_refs == tuple(f"c{i}" for i in range(6))
    assert "private provider error" not in result.model_dump_json()
    assert not result.patches


async def test_misbound_evidence_cannot_edit_legal_drafts() -> None:
    provider = _Provider(
        lambda decision: _evidence(decision, "replace_pair").model_copy(
            update={"input_digest": "wrong"}
        )
    )
    request = _request()
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=_horizon(request), now_epoch=200.0
    )
    assert not result.patches
    assert result.proposal.pairs[0].reason_code == "evidence_binding_mismatch"


async def test_hybrid_cancellation_propagates() -> None:
    class CancelProvider(_Provider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            raise asyncio.CancelledError

    request = _request()
    with pytest.raises(asyncio.CancelledError):
        await DirectorController(CancelProvider()).prepare_next(
            request, strategy="agent_jev", horizon=_horizon(request), now_epoch=200.0
        )


async def test_plan_expiring_during_audit_falls_back_without_second_decision_budget() -> None:
    class SlowProvider(_Provider):
        async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
            self.requests.append(request)
            await asyncio.sleep(0.02)
            return _evidence(request)

    request = _request(max_provider_calls=1)
    plan = _horizon(request).model_copy(update={"expires_at": 200.005})
    provider = SlowProvider()
    result = await DirectorController(provider).prepare_next(
        request, strategy="agent_jev", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.reason_code == "agent_plan_expired_during_audit"
    assert result.proposal.realized_strategy == "deterministic"
    assert result.proposal.provider_calls == len(provider.requests) == 1
    assert result.proposal.plan_id is None
    assert result.replan_required
    assert len(result.horizon_traces) == 3


@pytest.mark.parametrize("target,count", [(5, 18), (6, 4), (1, 3)])
async def test_odd_tail_and_shortages_never_duplicate(target: int, count: int) -> None:
    request = _request(count=count, target=target)
    result = await DirectorController().prepare_next(
        request, strategy="agent_only", horizon=_horizon(request, 1), now_epoch=200.0
    )
    assert len(result.proposal.card_refs) == min(target, count)
    assert len(set(result.proposal.card_refs)) == min(target, count)
    assert result.proposal.status == ("SHORTAGE" if count < target else "COMPLETE")


async def test_new_hard_minimum_unsatisfiable_returns_no_cards() -> None:
    request = _request()
    plan = _horizon(request)
    request = request.model_copy(
        update={
            "constraints": ScreenConstraints(
                version="new", topic_quotas=(TopicQuota(topic_group="absent", minimum=1),)
            )
        }
    )
    result = await DirectorController().prepare_next(
        request, strategy="agent_only", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.status == "UNSATISFIABLE"
    assert not result.proposal.card_refs
    assert result.replan_required


async def test_agent_new_feedback_requests_replan_without_inventing_exposure() -> None:
    request = _request()
    plan = _horizon(request)
    updated = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={"state_revision": 5, "feedback_cursor": "event_5"}
            )
        }
    )
    result = await DirectorController().prepare_next(
        updated, strategy="agent_only", horizon=plan, now_epoch=200.0
    )
    assert result.replan_required
    assert request.session.real_feedback == ()
    assert result.proposal.realized_strategy == "deterministic"
    assert result.proposal.reason_code == "agent_plan_state_changed"


async def test_expected_presentation_does_not_discard_remaining_agent_screens() -> None:
    request = _request()
    plan = _horizon(request)
    plan = plan.model_copy(update={"screens": plan.screens[1:]})
    session = request.session.model_copy(
        update={
            "state_revision": 5,
            "feedback_cursor": "event_5",
            "real_feedback": (
                RealFeedback(
                    event_ref="presented_5",
                    batch_ref="batch_1",
                    kind="presented",
                    description="actual screen1 presentation",
                ),
            ),
        }
    )
    updated = request.model_copy(update={"session": session, "cards": request.cards[6:]})
    result = await DirectorController().prepare_next(
        updated, strategy="agent_only", horizon=plan, now_epoch=200.0
    )
    assert result.proposal.card_refs == tuple(f"c{i}" for i in range(6, 12))
    assert result.proposal.realized_strategy == "agent_only"
    assert not result.replan_required


async def test_actual_negative_feedback_replans_pure_agent_without_calling_jev() -> None:
    request = _request()
    session = request.session.model_copy(
        update={
            "state_revision": 5,
            "feedback_cursor": "event_5",
            "real_feedback": (
                RealFeedback(
                    event_ref="dislike_5",
                    batch_ref="batch_1",
                    kind="dislike",
                    description="不要这个方向",
                ),
            ),
        }
    )
    provider = _Provider()
    result = await DirectorController(provider).prepare_next(
        request.model_copy(update={"session": session}),
        strategy="agent_only",
        horizon=_horizon(request),
        now_epoch=200.0,
    )
    assert result.proposal.realized_strategy == "deterministic"
    assert result.replan_required
    assert not provider.requests


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
async def test_explicit_intent_change_rejects_old_direction_even_without_state_bump(
    strategy: DirectorStrategy,
) -> None:
    request = _request()
    plan = _horizon(request)
    changed = request.model_copy(
        update={"session": request.session.model_copy(update={"explicit_intent": "现在只想看音乐"})}
    )
    assert plan_needs_replanning(changed, plan, strategy=strategy)
    result = await DirectorController().prepare_next(
        changed, strategy=strategy, horizon=plan, now_epoch=200.0
    )
    assert result.horizon is None
    assert result.proposal.plan_id is None
    assert result.proposal.reason_code == "agent_plan_state_changed"
    assert result.replan_required


async def test_planner_makes_one_real_structured_call_with_static_prompt_and_concrete_cards() -> (
    None
):
    request = _request()
    llm = _LLM(_planner_output(request))
    planner = HorizonPlanner(llm)
    result = await planner.plan(request, now_epoch=100.0)
    assert result.reason_code == "agent_horizon_prepared"
    assert result.plan is not None
    assert result.plan.screens[1].card_refs == tuple(f"c{i}" for i in range(6, 12))
    assert result.plan.expires_at == 400.0
    assert result.calls == len(llm.calls) == 1
    call = llm.calls[0]
    assert call["inject_core_memory"] is False
    assert call["caller"] == "recommendation.director.plan"
    assert "fixture:" not in cast("str", call["user_input"])
    assert "untrusted_candidates" in cast("str", call["user_input"])
    changed = request.model_copy(update={"direction": "完全不同的方向"})
    await planner.plan(changed, now_epoch=110.0)
    assert llm.calls[0]["system_instruction"] == llm.calls[1]["system_instruction"]
    assert llm.calls[0]["user_input"] != llm.calls[1]["user_input"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"screens":[],"screens":[]}',
        '{"screens":[]}',
        '{"screens":[{"direction":"bad","pairs":[{"card_refs":["unknown","c1"]}]}]}',
        '{"screens":[{"direction":"bad","pairs":[{"card_refs":["c0","c0"]}]}]}',
        '{"screens":[{"direction":"first","pairs":[{"card_refs":["c0","c1"]}]},{"direction":"second","pairs":[{"card_refs":["c0","c2"]}]}]}',
        '{"screens":[{"direction":"empty","pairs":[]}]}',
        '{"screens":[{"direction":"bad","pairs":[],"extra":NaN}]}',
    ],
)
async def test_invalid_planner_output_never_becomes_cached_plan(content: str) -> None:
    llm = _LLM(content)
    result = await HorizonPlanner(llm).plan(_request(), now_epoch=100.0)
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(llm.calls) == 2


async def test_planner_does_not_relax_hard_minimum_on_shortage() -> None:
    request = _request(
        constraints=ScreenConstraints(
            version="q", topic_quotas=(TopicQuota(topic_group="explore", minimum=2),)
        )
    )
    llm = _LLM(
        '{"screens":[{"direction":"bad","pairs":[{"card_refs":["c0","c1"]}]}],'
        '"completion":{"reason":"supply_exhausted"}}'
    )
    result = await HorizonPlanner(llm).plan(request)
    assert result.plan is None


async def test_planner_deadline_and_no_candidates_skip_call() -> None:
    llm = _LLM("{}")
    planner = HorizonPlanner(llm)
    result = await planner.plan(_request(), deadline_monotonic=0.0)
    assert result.calls == 0
    assert result.reason_code == "planning_deadline_exhausted"
    result = await planner.plan(_request(count=0))
    assert result.calls == 0
    assert result.reason_code == "no_planning_candidates"
    assert not llm.calls


async def test_planner_failure_is_explicit_and_sanitized() -> None:
    result = await HorizonPlanner(_LLM("{}", error=RuntimeError("SECRET"))).plan(_request())
    assert result.reason_code == "planner_unavailable"
    assert "SECRET" not in result.model_dump_json()


async def test_background_planner_uses_separate_maintenance_caller() -> None:
    request = _request()
    llm = _LLM(_planner_output(request))
    result = await HorizonPlanner(llm).plan(request, background=True)
    assert result.plan is not None
    assert llm.calls[0]["caller"] == "recommendation.director.preplan"


async def test_planner_cancellation_propagates() -> None:
    class CancelLLM(_LLM):
        async def complete_structured_task(self, **kwargs: object) -> LLMResponse:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await HorizonPlanner(CancelLLM("{}")).plan(_request())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon_screens": 4},
        {"horizon_screens": True},
        {"ttl_seconds": 0.0},
        {"ttl_seconds": float("nan")},
        {"budget_seconds": float("inf")},
        {"deadline_monotonic": -1.0},
        {"now_epoch": -1.0},
    ],
)
async def test_invalid_planner_budgets_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        await HorizonPlanner(_LLM("{}")).plan(_request(), **kwargs)


def test_committed_draft_or_duplicate_future_refs_rejected() -> None:
    plan = _horizon(_request())
    data = plan.model_dump(mode="python")
    first = data["screens"][0]
    first["tentative"] = False
    with pytest.raises(ValidationError):
        HorizonPlan.model_validate(data)
    data = plan.model_dump(mode="python")
    data["screens"][1]["pairs"] = data["screens"][0]["pairs"]
    with pytest.raises(ValidationError):
        HorizonPlan.model_validate(data)


def test_pure_agent_proposal_cannot_claim_jev_calls_or_partial_plan_binding() -> None:
    base = {
        "request_id": "r",
        "input_digest": "d",
        "candidate_digest": "c",
        "constraints_digest": "c",
        "direction_ref": "dir",
        "direction": "test",
        "status": "SHORTAGE",
        "requested_strategy": "agent_only",
        "realized_strategy": "none",
        "target_count": 2,
        "card_refs": (),
        "pairs": (),
        "provider_calls": 1,
        "reason_code": "fixture",
    }
    with pytest.raises(ValidationError):
        DirectorProposal.model_validate(base)
    with pytest.raises(ValidationError):
        DirectorProposal.model_validate({**base, "provider_calls": 0, "plan_id": "only_one_field"})
