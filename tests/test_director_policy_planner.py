"""The Agent emits bounded auditable screen decisions, not reasoning transcripts."""

from __future__ import annotations

import json

import pytest

from openbiliclaw.recommendation.director.horizon_models import HorizonPlan, PlannedPair
from openbiliclaw.recommendation.director.models import (
    CompositionRequest,
    RealFeedback,
    canonical_json,
)
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.recommendation.director.policy import (
    DIRECTOR_POLICY_INSTRUCTIONS,
    DIRECTOR_POLICY_VERSION,
    build_screen_brief,
)
from tests.test_director_horizon_modes import _LLM, _horizon, _planner_output, _request


def _output(request: CompositionRequest, *, brief: bool = True) -> dict[str, object]:
    plan = _horizon(request, screens=1)
    screen = plan.screens[0]
    output: dict[str, object] = {
        "direction": "从实践入口补齐落地判断，给用户独立选择",
        "pairs": [
            {
                "card_refs": list(pair.card_refs),
                "role": role,
                "intent": intent,
            }
            for pair, role, intent in zip(
                screen.pairs,
                ("anchor", "complement", "exploration"),
                ("落地案例与实施入口", "补充局限与评估角度", "提供相关但独立的新入口"),
                strict=True,
            )
        ],
    }
    if brief:
        value = build_screen_brief(request).model_dump(mode="json")
        value.update(
            {
                "task": "follow_intent",
                "objective": "帮助用户判断怎样落地，减少泛泛介绍",
                "uncertainties": ["不知道用户是否已经消费或理解前屏内容"],
                "avoid": ["相同概念重复介绍"],
                "revise_when": ["explicit_intent_changed", "explicit_feedback"],
                # Provenance is assigned by the caller, not trusted from LLM text.
                "source": "conservative",
            }
        )
        output["brief"] = value
    return {"screens": [output], "completion": {"reason": "target_reached"}}


async def test_planner_normalizes_structured_brief_roles_and_policy_version() -> None:
    request = _request()
    request = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={
                    "real_feedback": (
                        RealFeedback(
                            event_ref="feedback-1",
                            batch_ref="previous-screen",
                            kind="explicit_request",
                            description="给我点真正能落地的",
                        ),
                    )
                }
            )
        }
    )
    output = _output(request)
    output["screens"][0]["brief"]["evidence_refs"] = ["feedback-1"]
    llm = _LLM(canonical_json(output))
    result = await HorizonPlanner(llm).plan(request, horizon_screens=1, now_epoch=100.0)
    assert result.plan is not None
    assert result.calls == len(llm.calls) == 1
    assert result.plan.planning_policy_version == DIRECTOR_POLICY_VERSION
    screen = result.plan.screens[0]
    assert screen.brief is not None and screen.brief.source == "agent"
    assert screen.brief.task == "follow_intent"
    assert screen.brief.evidence_refs == ("feedback-1",)
    assert screen.brief.objective == "帮助用户判断怎样落地，减少泛泛介绍"
    assert [pair.role for pair in screen.pairs] == ["anchor", "complement", "exploration"]
    assert screen.pairs[1].intent == "补充局限与评估角度"
    assert HorizonPlan.model_validate_json(result.plan.model_dump_json()) == result.plan
    ledger = json.loads(llm.calls[0]["user_input"])["screen_policy"]["evidence_ledger"]
    assert ledger[0]["event_ref"] == "feedback-1"
    assert ledger[0]["strength"] == "explicit_request"


async def test_each_future_screen_keeps_its_own_goal_and_roles() -> None:
    request = _request()
    first = _output(request)["screens"][0]
    second = json.loads(canonical_json(first))
    second["brief"].update({"task": "change_angle", "objective": "提供相关领域的独立入口"})
    for pair, refs in zip(
        second["pairs"], (("c6", "c7"), ("c8", "c9"), ("c10", "c11")), strict=True
    ):
        pair["card_refs"] = refs
        pair["role"] = "open"
    llm = _LLM(
        canonical_json({"screens": [first, second], "completion": {"reason": "target_reached"}})
    )
    result = await HorizonPlanner(llm).plan(request, horizon_screens=2)
    assert result.plan is not None
    assert [screen.brief.task for screen in result.plan.screens] == [
        "follow_intent",
        "change_angle",
    ]
    assert all(pair.role == "open" for pair in result.plan.screens[1].pairs)
    assert result.calls == len(llm.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("task", "force_learning_path"),
        ("evidence_refs", ["invented_exposure_event"]),
        ("revise_when", ["no_click_means_dislike"]),
        ("uncertainties", ["a", "b", "c", "d"]),
        ("source", "verified_user_intent"),
    ],
)
async def test_invalid_or_unsupported_brief_never_becomes_plan(field, value) -> None:
    request = _request()
    output = _output(request)
    output["screens"][0]["brief"][field] = value
    llm = _LLM(canonical_json(output))
    result = await HorizonPlanner(llm).plan(request, horizon_screens=1)
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(llm.calls) == 2


async def test_unknown_pair_role_still_fails_after_one_bounded_model_correction() -> None:
    request = _request()
    output = _output(request)
    output["screens"][0]["pairs"][0]["role"] = "guaranteed_click"
    llm = _LLM(canonical_json(output))
    result = await HorizonPlanner(llm).plan(request, horizon_screens=1)
    assert result.plan is None and result.reason_code == "invalid_planner_output"
    assert result.calls == len(llm.calls) == 2


async def test_missing_legacy_brief_and_role_get_honest_conservative_defaults() -> None:
    request = _request()
    output = _output(request, brief=False)
    for pair in output["screens"][0]["pairs"]:
        del pair["role"]
    result = await HorizonPlanner(_LLM(canonical_json(output))).plan(request, horizon_screens=1)
    assert result.plan is not None
    screen = result.plan.screens[0]
    assert screen.brief == build_screen_brief(request)
    assert screen.brief.source == "conservative"
    assert all(pair.role == "open" for pair in screen.pairs)
    assert all(pair.intent for pair in screen.pairs)
    assert PlannedPair(card_refs=("c0", "c1")).role == "open"


async def test_policy_prompt_is_static_and_keeps_stable_profile_cache_prefix() -> None:
    request = _request()
    profile = {"recent": {"awareness": ["正在研究落地"]}, "stable": {"interests": ["AI"]}}
    request = request.model_copy(
        update={"session": request.session.model_copy(update={"profile_context": profile})}
    )
    llm = _LLM(canonical_json(_output(request, brief=False)))
    planner = HorizonPlanner(llm)
    assert (await planner.plan(request, horizon_screens=1)).plan is not None
    changed = request.model_copy(
        update={
            "direction": "补一些轻松的入口",
            "session": request.session.model_copy(update={"explicit_intent": "换个方向"}),
        }
    )
    assert (await planner.plan(changed, horizon_screens=1)).plan is not None
    assert llm.calls[0]["system_instruction"] == llm.calls[1]["system_instruction"]
    assert DIRECTOR_POLICY_INSTRUCTIONS in llm.calls[0]["system_instruction"]
    prefix = '{"profile_context":{"stable":' + canonical_json(profile["stable"]) + ","
    for call in llm.calls:
        assert call["user_input"].startswith(prefix)
        assert call["inject_core_memory"] is False
        payload = json.loads(call["user_input"])
        assert DIRECTOR_POLICY_VERSION in canonical_json(payload["screen_policy"])
        assert "profile_context" not in payload["session_context"]


async def test_prompt_requires_auditable_outcomes_and_no_invented_consumption() -> None:
    request = _request()
    llm = _LLM(canonical_json(_output(request)))
    assert (await HorizonPlanner(llm).plan(request, horizon_screens=1)).plan is not None
    prompt = llm.calls[0]["system_instruction"]
    assert "concise decision outcomes, never a" in prompt
    assert "private reasoning transcript" in prompt
    assert "only supplied evidence refs" in prompt
    assert "descriptive soft roles, not hard quotas" in prompt
    assert "Lack of action is not\ndislike; a click is not a like" in prompt
    assert "future drafts are not exposures" in prompt


async def test_multi_screen_target_is_separate_from_single_screen_policy() -> None:
    request = _request()
    llm = _LLM(_planner_output(request))
    result = await HorizonPlanner(llm).plan(request)
    assert result.plan is not None
    completion = result.plan.planning_completion
    assert completion is not None
    assert completion.initial_screen_counts == (6, 6, 6)
    assert completion.reason == "target_reached"
    assert completion.semantic_quality == "not_certified"
    payload = json.loads(llm.calls[0]["user_input"])
    assert payload["planning_contract"]["nominal_screen_counts"] == [6, 6, 6]
    assert payload["planning_contract"]["requested_screens"] == 3
    assert payload["planning_contract"]["physical_card_upper_bound"] == 18
    prompt = llm.calls[0]["system_instruction"]
    assert "ONE-screen template" in prompt
    assert "Current explicit intent governs ALL future screens" in prompt
    assert len(llm.calls) == 1


async def test_live_failure_prompt_contract_scopes_open_roles_and_checks_selected_counts() -> None:
    """Prompt contract only; live selection quality needs separate model evidence."""
    request = _request()
    llm = _LLM(_planner_output(request))
    assert (await HorizonPlanner(llm).plan(request)).plan is not None
    prompt = " ".join(llm.calls[0]["system_instruction"].split())
    shared = " ".join(DIRECTOR_POLICY_INSTRUCTIONS.split())
    assert "Independent means useful without other cards, not unrelated to the request" in shared
    assert "Without explicit intent, stable interests may supply cross-interest variety" in shared
    assert "Every pair in pairs is selected for display" in prompt
    assert "Rejected or review-only pairs must never appear in screens" in prompt
    assert "count the actual card_refs in every screen" in prompt
    assert "target_reached if and only if" in prompt
    assert "otherwise use supply_exhausted" in prompt
    assert DIRECTOR_POLICY_VERSION == "director-screen-policy.v3"
    assert len(llm.calls) == 1


async def test_planner_prompt_decides_useful_supply_before_layout_and_serializes_it_first() -> None:
    """Guard planning order, without claiming a model obeys the prompt."""
    request = _request()
    llm = _LLM(_planner_output(request))
    assert (await HorizonPlanner(llm).plan(request)).plan is not None
    original = llm.calls[0]["system_instruction"]
    shape, _ = json.JSONDecoder().raw_decode(
        original.split("Return only JSON with this shape:\n", 1)[1]
    )
    assert list(shape) == ["completion", "screens"]
    prompt = " ".join(original.split())
    assert "Decide useful supply before designing screen layouts" in prompt
    assert "Output completion first and screens second" in prompt
    assert "ten useful unique cards support 6 + 4, not three screens" in prompt
    assert "No placeholder or 'not selected' pair" in prompt
    assert "useful unique supply takes priority over the requested screen count" in prompt
    assert "desired full-horizon target" in prompt
    assert "satisfy it whenever useful hard-feasible supply permits" in prompt
    assert (
        "If that supply fills the requested horizon, use target_reached and empty omissions"
        in prompt
    )
    assert len(llm.calls) == 1


async def test_completion_first_is_a_generation_order_not_a_new_validation_requirement() -> None:
    request = _request()
    payload = json.loads(_planner_output(request))
    completion_first = {"completion": payload["completion"], "screens": payload["screens"]}
    outputs = (_planner_output(request), json.dumps(completion_first))
    plans = []
    for text in outputs:
        llm = _LLM(text)
        result = await HorizonPlanner(llm).plan(request, now_epoch=100.0)
        assert result.plan is not None
        assert len(llm.calls) == 1
        plans.append(result.plan)
    assert plans[0] == plans[1]


@pytest.mark.parametrize("audit_omissions", [True, False])
async def test_semantic_short_tail_needs_audited_unused_supply(audit_omissions: bool) -> None:
    request = _request()
    output = json.loads(_planner_output(request))
    output["screens"] = output["screens"][:2]
    output["screens"][1]["pairs"] = [
        {"card_refs": ["c6", "c7"]},
        {"card_refs": ["c8"]},
    ]
    output["completion"] = {
        "reason": "supply_exhausted",
        "omissions": [
            {
                "refs": [f"c{i}" for i in range(9, 24)],
                "reason": "off_intent",
                "note": "Synthetic semantic judgment for testing, not a content-quality claim",
            }
        ]
        if audit_omissions
        else [],
    }
    llm = _LLM(canonical_json(output))
    result = await HorizonPlanner(llm).plan(request)
    assert result.calls == len(llm.calls) == (1 if audit_omissions else 2)
    if not audit_omissions:
        assert result.plan is None and result.reason_code == "invalid_planner_output"
        return
    assert result.plan is not None
    completion = result.plan.planning_completion
    assert completion is not None
    assert completion.initial_screen_counts == (6, 3)
    assert completion.reason == "semantic_shortage"
    assert HorizonPlan.model_validate_json(result.plan.model_dump_json()) == result.plan


async def test_live_completion_is_required_but_old_persisted_plans_remain_readable() -> None:
    request = _request()
    output = json.loads(_planner_output(request))
    del output["completion"]
    result = await HorizonPlanner(_LLM(canonical_json(output))).plan(request)
    assert result.plan is None and result.reason_code == "invalid_planner_output"
    saved = _horizon(request).model_dump(mode="json")
    saved.pop("planning_completion", None)
    assert HorizonPlan.model_validate_json(canonical_json(saved)).planning_completion is None
