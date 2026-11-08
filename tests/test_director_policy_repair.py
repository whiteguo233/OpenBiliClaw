"""Repair carries screen duties without treating drafts as user evidence."""

from __future__ import annotations

import time

import pytest

from openbiliclaw.recommendation.director.models import PairAlternative, RealFeedback
from openbiliclaw.recommendation.director.policy import (
    DIRECTOR_POLICY_INSTRUCTIONS,
    ScreenBrief,
)
from openbiliclaw.recommendation.director.repair import DraftRepairer, _Budget, _decision
from tests.test_director_horizon_modes import _evidence, _horizon, _Provider, _request


def _role_plan(request, screens=1):
    plan = _horizon(request, screens)
    roles = ("anchor", "complement", "exploration")
    return plan.model_copy(
        update={
            "screens": tuple(
                screen.model_copy(
                    update={
                        "brief": ScreenBrief(
                            task="change_angle",
                            objective=f"实际屏目标 {index}",
                            uncertainties=("实际阅读深度未知",),
                            avoid=("重复泛泛的发布介绍",),
                            source="agent",
                        ),
                        "pairs": tuple(
                            pair.model_copy(update={"role": roles[i % len(roles)]})
                            for i, pair in enumerate(screen.pairs)
                        ),
                    }
                )
                for index, screen in enumerate(plan.screens)
            )
        }
    )


async def test_audit_transmits_real_brief_current_intent_suffix_tasks_and_semantics():
    request = _request()
    plan = _role_plan(request, screens=2)
    provider = _Provider()
    result = await DraftRepairer(provider).repair(request, plan)
    first = provider.requests[0]
    assert first.question_schema_version == "director-audit.v3"
    assert first.state["schema_version"] == "director-repair.v3"
    policy = first.state["screen_policy"]
    assert policy["brief"] == plan.screens[0].brief.model_dump(mode="json")
    assert policy["current_pair"] == {
        "pair_index": 0,
        "role": "anchor",
        "intent": "屏0对0",
        "card_refs": ["c0", "c1"],
        "unavailable_card_refs": [],
        "tentative": True,
    }
    assert [task["role"] for task in policy["remaining_pairs"]] == [
        "complement",
        "exploration",
    ]
    assert policy["remaining_pairs"][1]["intent"] == "屏0对2"
    assert policy["remaining_role_counts"] == {"complement": 1, "exploration": 1}
    assert {card["ref"] for card in first.state["untrusted_candidates"]} == {
        f"c{i}" for i in range(6)
    }
    second = provider.requests[1].state
    assert second["tentative_prefix"] == ["c0", "c1"]
    assert second["screen_policy"]["selected_role_counts"] == {"anchor": 1}
    assert provider.requests[2].state["screen_policy"]["final_pair_whole_screen_check"]
    assert provider.requests[3].state["previous_tentative_screens"] == [f"c{i}" for i in range(6)]
    assert result.provider_calls == 6
    assert not result.patches and not result.replan_required
    assert result.horizon.screens[0].brief == plan.screens[0].brief


@pytest.mark.parametrize("repair", [False, True])
def test_decision_rebinds_suffix_by_exact_identity_and_does_not_reuse_changed_refs(repair):
    request = _request()
    plan = _role_plan(request)
    changed = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(
                    update={
                        "ref": f"new_{card.ref}",
                        **({"summary": "已变化，不能沿用旧判断"} if card.ref == "c4" else {}),
                    }
                )
                for card in request.cards
                if card.ref != "c5"
            )
        }
    )
    decision = _decision(
        changed,
        plan,
        plan.screens[0],
        0,
        (),
        (),
        ("new_c0", "new_c1"),
        _Budget(time.monotonic() + 20, 10),
        (PairAlternative(option_id="keep", card_refs=("new_c0", "new_c1")),) if repair else None,
    )
    assert decision.question_schema_version == (
        "director-repair.v3" if repair else "director-audit.v3"
    )
    policy = decision.state["screen_policy"]
    assert policy["current_pair"]["card_refs"] == ["new_c0", "new_c1"]
    assert policy["remaining_pairs"][0]["card_refs"] == ["new_c2", "new_c3"]
    assert policy["remaining_pairs"][1]["card_refs"] == []
    assert policy["remaining_pairs"][1]["unavailable_card_refs"] == ["c4", "c5"]
    assert {card["ref"] for card in decision.state["untrusted_candidates"]} == {
        "new_c0",
        "new_c1",
        "new_c2",
        "new_c3",
    }
    assert decision.state["known_pair"] == ["new_c0", "new_c1"]


async def test_local_patch_preserves_actual_brief_and_pair_duty_in_plan_patch_and_trace():
    request = _request()
    plan = _role_plan(request)

    def choose(decision):
        if decision.state["pair_index"] == 1:
            if decision.purpose == "AUDIT_DRAFT":
                return _evidence(decision, "replace_right")
            selected = next(
                option
                for option in decision.state["alternatives"]
                if option["card_refs"] == ["c2", "c6"]
            )
            return _evidence(decision, selected["option_id"])
        return _evidence(decision)

    provider = _Provider(choose)
    result = await DraftRepairer(provider).repair(request, plan)
    replacement = result.horizon.screens[0].pairs[1]
    assert replacement.card_refs == ("c2", "c6")
    assert replacement.role == "complement" and replacement.intent == "屏0对1"
    assert result.horizon.screens[0].brief == plan.screens[0].brief
    assert result.patches[0].replacements[1] == replacement
    trace = result.traces[0][1]
    assert trace.role == replacement.role and trace.intent == replacement.intent
    assert trace.origin == "decision" and trace.reason_code == "jev_local_repair"
    assert result.provider_calls == 4 and not result.replan_required
    repair = next(d for d in provider.requests if d.purpose == "REPAIR_PAIR")
    assert repair.state["screen_policy"]["current_pair"]["role"] == "complement"
    assert repair.state["screen_policy"]["remaining_pairs"][0]["role"] == "exploration"
    for decision in provider.requests:
        assert decision.questions["decision"].instructions.startswith(DIRECTOR_POLICY_INSTRUCTIONS)
        assert decision.state["session_context"]["recent_screens"] == []
        assert decision.state["screen_policy"]["evidence_ledger"] == []


async def test_no_supported_role_replacement_preserves_legal_draft_but_requests_replan():
    request = _request()
    plan = _role_plan(request)

    def choose(decision):
        if decision.state["pair_index"] == 1:
            return _evidence(
                decision,
                "replace_pair" if decision.purpose == "AUDIT_DRAFT" else "none_suitable",
            )
        return _evidence(decision)

    result = await DraftRepairer(_Provider(choose)).repair(request, plan)
    assert result.replan_required
    assert result.horizon.screens == plan.screens
    assert result.traces[0][1].reason_code == "none_suitable"
    assert result.traces[0][1].role == "complement"
    assert not result.patches
    assert result.provider_calls == 4


async def test_hard_fallback_retains_intent_but_does_not_certify_role_fulfillment():
    request = _request()
    plan = _role_plan(request)
    changed = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(update={"eligible": False}) if card.ref == "c3" else card
                for card in request.cards
            )
        }
    )
    result = await DraftRepairer().repair(changed, plan)
    assert result.replan_required
    assert result.horizon.screens[0].brief == plan.screens[0].brief
    assert "c3" not in result.horizon.screens[0].card_refs
    assert result.horizon.screens[0].pairs[1].role == "complement"
    trace = result.traces[0][1]
    assert trace.role == "complement" and trace.intent == "屏0对1"
    assert trace.origin == "deterministic"
    assert trace.reason_code == "hard_invalid_deterministic_repair"
    assert result.provider_calls == 0


async def test_actual_click_and_dislike_keep_scope_without_draft_feedback_invention():
    request = _request()
    feedback = (
        RealFeedback(
            event_ref="clicked", batch_ref="history", kind="click", description="历史内容点击"
        ),
        RealFeedback(
            event_ref="disliked", batch_ref="history", kind="dislike", description="不喜欢这种标题"
        ),
    )
    request = request.model_copy(
        update={"session": request.session.model_copy(update={"real_feedback": feedback})}
    )
    provider = _Provider()
    await DraftRepairer(provider).repair(request, _role_plan(request))
    for decision in provider.requests:
        events = decision.state["screen_policy"]["evidence_ledger"]
        assert events == [
            {
                "event_ref": "clicked",
                "kind": "click",
                "strength": "weak_interest",
                "scope": "item_or_stated_reason",
            },
            {
                "event_ref": "disliked",
                "kind": "dislike",
                "strength": "explicit_negative",
                "scope": "item_or_stated_reason",
            },
        ]
        assert decision.state["session_context"]["real_feedback"] == [
            event.model_dump(mode="json") for event in feedback
        ]


@pytest.mark.parametrize("singleton_already_planned", [False, True])
async def test_shortage_final_singleton_uses_effective_target_for_audit_and_repair(
    singleton_already_planned,
):
    request = _request(target=6)
    plan = _role_plan(request)
    if singleton_already_planned:
        screen = plan.screens[0]
        plan = plan.model_copy(
            update={
                "screens": (
                    screen.model_copy(
                        update={
                            "pairs": (
                                screen.pairs[0],
                                screen.pairs[1].model_copy(update={"card_refs": ("c2",)}),
                            )
                        }
                    ),
                )
            }
        )
    changed = request.model_copy(update={"cards": request.cards[:3]})
    provider = _Provider()
    result = await DraftRepairer(provider).repair(changed, plan)
    assert result.horizon.screens[0].card_refs == ("c0", "c1", "c2")
    assert result.replan_required
    last = provider.requests[-1]
    assert last.purpose == ("AUDIT_DRAFT" if singleton_already_planned else "REPAIR_PAIR")
    assert last.state["tentative_prefix"] == ["c0", "c1"]
    policy = last.state["screen_policy"]
    assert policy["selected_count"] == 2
    assert policy["remaining_count"] == 1
    assert policy["selected_count"] + policy["remaining_count"] == 3
    assert policy["final_pair_whole_screen_check"] is True
    assert provider.requests[0].state["screen_policy"]["remaining_count"] == 3
