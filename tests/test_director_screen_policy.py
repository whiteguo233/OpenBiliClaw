"""Offline policy acceptance: no provider credentials, behavioral guesses or calls."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from openbiliclaw.recommendation.director import runtime as runtime_module
from openbiliclaw.recommendation.director.composer import PairComposer
from openbiliclaw.recommendation.director.controller import (
    DirectorController,
    plan_needs_replanning,
)
from openbiliclaw.recommendation.director.horizon_models import HorizonPlan
from openbiliclaw.recommendation.director.models import PairTrace, RealFeedback
from openbiliclaw.recommendation.director.policy import (
    DIRECTOR_POLICY_INSTRUCTIONS,
    DIRECTOR_POLICY_VERSION,
    build_policy_context,
    build_screen_brief,
    review_screen,
)
from openbiliclaw.recommendation.director.store import StoreConflict
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_horizon_modes import _horizon, _request
from tests.test_director_runtime import FakeAgent, FakeDecision, make_runtime, seed_database
from tests.test_pair_composer import _evidence, _RecordingProvider


async def test_joint_pair_role_is_selected_in_same_call_and_preserved_in_trace():
    request = _request(target=6)
    before = request.model_dump_json()
    roles = iter(("anchor", "complement", "exploration"))
    provider = _RecordingProvider(lambda decision: _evidence(decision, f"{next(roles)}:p0"))
    proposal = await PairComposer(provider).prepare_next(request)
    assert proposal.provider_calls == 3
    assert [pair.role for pair in proposal.pairs] == ["anchor", "complement", "exploration"]
    assert [pair.selected_option_id for pair in proposal.pairs] == [
        "anchor:p0",
        "complement:p0",
        "exploration:p0",
    ]
    for i, call in enumerate(provider.requests):
        policy = call.state["screen_policy"]
        assert policy["selected_count"] == i * 2
        assert policy["remaining_count"] == 6 - i * 2
        assert policy["final_pair_whole_screen_check"] == (i == 2)
        assert DIRECTOR_POLICY_INSTRUCTIONS in call.questions["pair"].instructions
    assert provider.requests[1].state["screen_policy"]["selected_role_counts"] == {"anchor": 1}
    assert request.model_dump_json() == before
    assert proposal.brief.source == "conservative"
    assert proposal.screen_review.semantic_quality == "not_certified"


async def test_role_attribution_cannot_be_changed_while_keeping_the_same_pair():
    request = _request(target=2)
    provider = _RecordingProvider(lambda decision: _evidence(decision, "anchor:p0"))
    proposal = await PairComposer(provider).prepare_next(request)
    data = proposal.pairs[0].model_dump(mode="python")
    with pytest.raises(ValidationError, match="exact offered pair choice"):
        PairTrace.model_validate({**data, "role": "exploration"})


async def test_controller_keeps_joint_role_and_brief_for_pure_jev():
    provider = _RecordingProvider(lambda decision: _evidence(decision, "complement:p0"))
    result = await DirectorController(provider).prepare_next(
        _request(target=2), strategy="jev_recurrent"
    )
    assert result.proposal.pairs[0].role == "complement"
    assert result.proposal.pairs[0].intent
    assert result.proposal.brief.task == "follow_intent"
    assert result.proposal.screen_review.semantic_quality == "not_certified"


def test_no_intent_defaults_to_browsing_not_a_story_inferred_from_click():
    request = _request()
    request = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={
                    "explicit_intent": "",
                    "real_feedback": (
                        RealFeedback(
                            event_ref="one-click", batch_ref="seen", kind="click", description="AI"
                        ),
                    ),
                }
            )
        }
    )
    brief = build_screen_brief(request)
    assert brief.task == "browse"
    assert brief.source == "conservative"
    assert brief.evidence_refs == ()
    context = build_policy_context(request, brief=brief)
    assert context["evidence_ledger"][0]["strength"] == "weak_interest"
    assert context["roles_are_soft"] is True


@pytest.mark.parametrize(
    "kind", ["click", "presented", "like", "dislike", "skip", "explicit_request"]
)
def test_pure_agent_weak_events_do_not_force_global_replan_but_explicit_feedback_does(kind):
    request = _request()
    plan = _horizon(request)
    changed = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={
                    "state_revision": request.session.state_revision + 1,
                    "feedback_cursor": "jump-1000",
                    "real_feedback": (
                        RealFeedback(
                            event_ref="new", batch_ref="seen", kind=kind, description="local reason"
                        ),
                    ),
                }
            )
        }
    )
    assert plan_needs_replanning(changed, plan, strategy="agent_only") == (
        kind not in {"click", "presented"}
    )


def test_truncated_or_unexplained_weak_event_window_replans():
    request = _request()
    plan = _horizon(request)
    events = tuple(
        RealFeedback(event_ref=f"click-{i}", batch_ref="seen", kind="click", description="weak")
        for i in range(4)
    )
    session = request.session.model_copy(
        update={"state_revision": request.session.state_revision + 5, "real_feedback": events}
    )
    assert plan_needs_replanning(
        request.model_copy(update={"session": session}), plan, strategy="agent_only"
    )
    complete = session.model_copy(update={"state_revision": request.session.state_revision + 4})
    assert not plan_needs_replanning(
        request.model_copy(update={"session": complete}), plan, strategy="agent_only"
    )


@pytest.mark.parametrize("strategy", ["agent_jev", "agent_only"])
def test_legacy_persisted_policy_plan_is_readable_but_invalidated(strategy):
    request = _request()
    data = _horizon(request).model_dump(mode="json")
    assert data.pop("planning_policy_version") == DIRECTOR_POLICY_VERSION
    restored = HorizonPlan.model_validate_json(json.dumps(data))
    assert restored.planning_policy_version == ""
    assert plan_needs_replanning(request, restored, strategy=strategy)


@pytest.mark.parametrize("strategy", ["agent_jev", "agent_only"])
def test_v2_future_plan_is_readable_but_replanned_for_live_output_clarifications(strategy):
    request = _request()
    data = _horizon(request).model_dump(mode="json")
    data["planning_policy_version"] = "director-screen-policy.v2"
    restored = HorizonPlan.model_validate_json(json.dumps(data))
    assert restored.planning_policy_version == "director-screen-policy.v2"
    assert plan_needs_replanning(request, restored, strategy=strategy)


def test_title_duplicate_diagnostic_never_certifies_semantics_or_relaxes_constraints():
    request = _request(target=2)
    cards = (
        request.cards[0].model_copy(update={"title": "ＡＩ  教程"}),
        request.cards[1].model_copy(update={"title": "ai 教程"}),
    )
    review = review_screen(cards, (cards[0].ref, cards[1].ref))
    assert review.repeated_title_groups == (("c0", "c1"),)
    assert review.semantic_quality == "not_certified"
    assert review_screen(cards, ("c0",)).repeated_title_groups == ()


async def test_role_choices_do_not_expand_call_budget_and_shortage_remains_legal():
    provider = _RecordingProvider(lambda decision: _evidence(decision, "exploration:p0"))
    result = await PairComposer(provider).prepare_next(
        _request(count=3, target=6, max_provider_calls=1)
    )
    assert result.status == "SHORTAGE"
    assert result.provider_calls == 1
    assert result.pairs[0].role == "exploration"
    assert result.pairs[1].role == "open"
    assert result.pairs[1].reason_code == "call_budget_exhausted"
    assert len(result.pairs[1].selected_refs) == 1


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
async def test_policy_outcomes_persist_in_batch_trace_without_creating_exposure(tmp_path, strategy):
    db = seed_database(tmp_path / "policy.db", count=40)
    runtime = make_runtime(db, strategy=strategy, llm=FakeAgent(), provider=FakeDecision())
    try:
        batch = await runtime.generate(
            SoulProfile(), stream_ref="policy", request_id="one", limit=4
        )
        assert batch is not None
        trace = json.loads(batch.trace_json)
        assert trace["proposal"]["brief"]["source"] == "conservative"
        assert all(pair["role"] == "open" for pair in trace["proposal"]["pairs"])
        assert trace["proposal"]["screen_review"]["semantic_quality"] == "not_certified"
        assert runtime.store.session("policy").recent_screens == ()
        restored = await runtime.restore("policy", batch.batch_ref)
        assert restored.trace_json == batch.trace_json
    finally:
        await runtime.aclose()
        db.close()


async def test_new_policy_version_fences_old_inflight_generation_even_with_same_config(
    tmp_path, monkeypatch
):
    db = seed_database(tmp_path / "policy-version.db")
    provider = FakeDecision()
    provider.release = asyncio.Event()
    old = make_runtime(db, strategy="jev_recurrent", provider=provider)
    new = None
    try:
        task = asyncio.create_task(
            old.generate(SoulProfile(), stream_ref="s", request_id="old", limit=4)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=3)
        monkeypatch.setattr(runtime_module, "DIRECTOR_POLICY_VERSION", "future-policy-test-version")
        new = make_runtime(db, strategy="jev_recurrent", provider=FakeDecision())
        assert old.context_digest != new.context_digest
        provider.release.set()
        with pytest.raises(StoreConflict, match="generation_stale"):
            await task
        assert db.count_recommendations() == 0
    finally:
        provider.release.set()
        await old.aclose()
        if new:
            await new.aclose()
        db.close()


@pytest.mark.parametrize("strategy", ["agent_jev", "agent_only"])
async def test_agent_brief_and_roles_survive_real_sqlite_plan_consumption(tmp_path, strategy):
    class PolicyAgent(FakeAgent):
        async def complete_structured_task(self, **kwargs):
            response = await super().complete_structured_task(**kwargs)
            payload = json.loads(response.content)
            for screen in payload["screens"]:
                screen["brief"] = {
                    "task": "follow_intent",
                    "objective": "提供落地入口与互补案例",
                    "uncertainties": ["不知道用户是否理解前屏"],
                    "avoid": ["同一概念反复介绍"],
                    "revise_when": ["explicit_feedback"],
                }
                for i, pair in enumerate(screen["pairs"]):
                    pair["role"] = "anchor" if i == 0 else "complement"
            response.content = json.dumps(payload)
            return response

    db = seed_database(tmp_path / "planned-policy.db", count=40)
    provider = FakeDecision()
    runtime = make_runtime(db, strategy=strategy, llm=PolicyAgent(), provider=provider)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="one", limit=4)
        second = await runtime.generate(SoulProfile(), stream_ref="s", request_id="two", limit=4)
        assert first is not None and second is not None and first.plan_id == second.plan_id
        for batch in (first, second):
            trace = json.loads(batch.trace_json)
            assert trace["proposal"]["brief"]["source"] == "agent"
            assert trace["proposal"]["brief"]["objective"] == "提供落地入口与互补案例"
            assert [pair["role"] for pair in trace["proposal"]["pairs"]] == ["anchor", "complement"]
        if strategy == "agent_jev":
            assert provider.requests[0].state["screen_policy"]["current_pair"]["role"] == "anchor"
            assert (
                provider.requests[0].state["screen_policy"]["remaining_pairs"][0]["role"]
                == "complement"
            )
        else:
            assert provider.requests == []
    finally:
        await runtime.aclose()
        db.close()
