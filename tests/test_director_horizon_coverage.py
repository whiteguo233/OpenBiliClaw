"""Offline checks of target coverage, never semantic recommendation claims."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openbiliclaw.recommendation.director.horizon_coverage import (
    HorizonStop,
    OmittedGroup,
    PlanningCompletion,
    build_horizon_contract,
    validate_horizon_coverage,
)
from openbiliclaw.recommendation.director.horizon_models import HorizonPlan
from openbiliclaw.recommendation.director.models import ScreenConstraints, TopicQuota
from tests.test_director_horizon_modes import _horizon, _request


def _refs(start: int, end: int) -> tuple[str, ...]:
    return tuple(f"c{i}" for i in range(start, end))


def test_three_full_screens_reach_target_without_auditing_unneeded_candidates() -> None:
    request = _request()
    before = request.model_dump_json()
    result = validate_horizon_coverage(
        request,
        3,
        (_refs(0, 6), _refs(6, 12), _refs(12, 18)),
        HorizonStop(reason="target_reached"),
    )
    assert result.initial_screen_counts == (6, 6, 6)
    assert result.reason == "target_reached"
    assert result.semantic_quality == "not_certified"
    assert request.model_dump_json() == before


def test_six_plus_three_with_audited_off_intent_supply_is_semantic_shortage() -> None:
    request = _request()
    group = OmittedGroup(refs=_refs(9, 24), reason="off_intent", note="本轮任务之外的候选。")
    result = validate_horizon_coverage(
        request,
        3,
        (_refs(0, 6), _refs(6, 9)),
        HorizonStop(reason="supply_exhausted", omissions=(group,), note="保留当前任务主线。"),
    )
    assert result.initial_screen_counts == (6, 3)
    assert result.reason == "semantic_shortage"
    assert result.omissions == (group,)
    assert result.semantic_quality == "not_certified"
    # The opinion does not mutate admission: these candidates remain eligible
    # and are selectable in a later request with a different objective.
    assert all(card.eligible for card in request.cards)
    later = validate_horizon_coverage(
        request, 1, (_refs(9, 15),), HorizonStop(reason="target_reached")
    )
    assert later.reason == "target_reached"


@pytest.mark.parametrize("screens", [(_refs(0, 6),), (_refs(0, 6), _refs(6, 9))])
def test_unexplained_shortage_with_remaining_usable_candidates_is_rejected(
    screens: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises(ValueError, match="admissible"):
        validate_horizon_coverage(_request(), 3, screens, HorizonStop(reason="supply_exhausted"))


def test_one_unreported_usable_candidate_still_requires_extending_short_tail() -> None:
    stop = HorizonStop(
        reason="supply_exhausted", omissions=(OmittedGroup(refs=_refs(9, 23), reason="off_intent"),)
    )
    with pytest.raises(ValueError, match="admissible extension"):
        validate_horizon_coverage(_request(), 3, (_refs(0, 6), _refs(6, 9)), stop)


@pytest.mark.parametrize(
    "screens",
    [
        (),
        ((),),
        (_refs(0, 6), ()),
        (_refs(0, 2), _refs(2, 4), _refs(4, 6)),
        (_refs(0, 3), _refs(3, 9)),
        (_refs(0, 7),),
        (_refs(0, 6), _refs(5, 11)),
        (("unknown",),),
        (_refs(0, 6), _refs(6, 12), _refs(12, 18), _refs(18, 24)),
    ],
)
def test_empty_split_oversized_repeated_and_unknown_screens_are_rejected(
    screens: tuple[tuple[str, ...], ...],
) -> None:
    with pytest.raises(ValueError):
        validate_horizon_coverage(_request(), 3, screens, HorizonStop(reason="supply_exhausted"))


@pytest.mark.parametrize("omitted_refs", [("unknown",), ("c0",)])
def test_unknown_or_selected_omission_refs_are_rejected(omitted_refs: tuple[str, ...]) -> None:
    stop = HorizonStop(
        reason="supply_exhausted",
        omissions=(OmittedGroup(refs=omitted_refs, reason="insufficient_evidence"),),
    )
    with pytest.raises(ValueError, match="eligible unselected"):
        validate_horizon_coverage(_request(), 3, (_refs(0, 6),), stop)


@pytest.mark.parametrize("excluded", [False, True])
def test_ineligible_and_excluded_refs_cannot_be_selected_or_omitted(excluded: bool) -> None:
    request = _request(count=7)
    if excluded:
        request = request.model_copy(
            update={
                "constraints": ScreenConstraints(
                    version="excluded", excluded_item_keys=(request.cards[6].item_key,)
                )
            }
        )
    else:
        request = request.model_copy(
            update={
                "cards": (
                    *request.cards[:6],
                    request.cards[6].model_copy(update={"eligible": False}),
                )
            }
        )
    with pytest.raises(ValueError, match="planned reference"):
        validate_horizon_coverage(
            request, 3, (_refs(1, 7),), HorizonStop(reason="supply_exhausted")
        )
    with pytest.raises(ValueError, match="eligible unselected"):
        validate_horizon_coverage(
            request,
            3,
            (_refs(0, 6),),
            HorizonStop(
                reason="supply_exhausted",
                omissions=(OmittedGroup(refs=("c6",), reason="off_intent"),),
            ),
        )
    result = validate_horizon_coverage(
        request, 3, (_refs(0, 6),), HorizonStop(reason="supply_exhausted")
    )
    assert result.reason == "candidate_shortage"


def test_raw_stop_rejects_duplicate_omissions_within_and_across_groups() -> None:
    with pytest.raises(ValueError, match="duplicate omission"):
        OmittedGroup(refs=("c1", "c1"), reason="redundant")
    group = OmittedGroup(refs=("c1",), reason="redundant")
    with pytest.raises(ValueError, match="across groups"):
        HorizonStop(reason="supply_exhausted", omissions=(group, group))


def test_false_target_and_false_supply_exhaustion_are_rejected() -> None:
    with pytest.raises(ValueError, match="explicitly report"):
        validate_horizon_coverage(
            _request(count=6), 3, (_refs(0, 6),), HorizonStop(reason="target_reached")
        )
    with pytest.raises(ValueError, match="full horizon"):
        validate_horizon_coverage(
            _request(), 1, (_refs(0, 6),), HorizonStop(reason="supply_exhausted")
        )
    with pytest.raises(ValueError, match="complete horizon"):
        HorizonStop(
            reason="target_reached", omissions=(OmittedGroup(refs=("c8",), reason="off_intent"),)
        )


@pytest.mark.parametrize("count", [3, 6, 9, 12, 14])
def test_physical_candidate_shortage_preserves_full_prefix_and_one_tail(count: int) -> None:
    request = _request(count=count)
    screens = tuple(_refs(i, min(count, i + 6)) for i in range(0, count, 6))
    result = validate_horizon_coverage(request, 3, screens, HorizonStop(reason="supply_exhausted"))
    assert result.reason == "candidate_shortage"
    assert result.initial_screen_counts == tuple(map(len, screens))


def test_quota_minimum_can_exhaust_residual_supply_after_a_full_screen() -> None:
    request = _request(
        count=8,
        constraints=ScreenConstraints(
            version="min", topic_quotas=(TopicQuota(topic_group="tools", minimum=2),)
        ),
    )
    # All four tools are used, leaving two explore cards that cannot constitute
    # a legal next screen. A different allocation might permit more screens.
    selected = ("c0", "c2", "c4", "c6", "c1", "c3")
    result = validate_horizon_coverage(
        request, 3, (selected,), HorizonStop(reason="supply_exhausted")
    )
    assert result.reason == "residual_constraints"
    assert result.initial_screen_counts == (6,)


def test_remaining_quota_legal_short_screen_must_be_appended() -> None:
    request = _request(
        count=8,
        constraints=ScreenConstraints(
            version="min", topic_quotas=(TopicQuota(topic_group="tools", minimum=1),)
        ),
    )
    with pytest.raises(ValueError, match="another admissible screen"):
        validate_horizon_coverage(
            request, 3, (_refs(0, 6),), HorizonStop(reason="supply_exhausted")
        )
    result = validate_horizon_coverage(
        request, 3, (_refs(0, 6), _refs(6, 8)), HorizonStop(reason="supply_exhausted")
    )
    assert result.reason == "candidate_shortage"


def test_maximum_limited_short_tail_may_stop_without_fabricating_multiple_short_screens() -> None:
    request = _request(
        count=8,
        constraints=ScreenConstraints(
            version="max",
            topic_quotas=(
                TopicQuota(topic_group="tools", maximum=2),
                TopicQuota(topic_group="explore", maximum=2),
            ),
        ),
    )
    result = validate_horizon_coverage(
        request, 3, (_refs(0, 4),), HorizonStop(reason="supply_exhausted")
    )
    assert result.reason == "residual_constraints"
    with pytest.raises(ValueError, match="only the final"):
        validate_horizon_coverage(
            request, 3, (_refs(0, 4), _refs(4, 8)), HorizonStop(reason="supply_exhausted")
        )


def test_semantic_omissions_do_not_relax_a_hard_topic_minimum() -> None:
    request = _request(
        count=4,
        constraints=ScreenConstraints(
            version="hard", topic_quotas=(TopicQuota(topic_group="explore", minimum=2),)
        ),
    )
    with pytest.raises(ValueError, match="hard constraints"):
        validate_horizon_coverage(
            request,
            3,
            (("c0", "c2"),),
            HorizonStop(
                reason="supply_exhausted",
                omissions=(OmittedGroup(refs=("c1", "c3"), reason="off_intent"),),
            ),
        )


def test_contract_is_an_eligible_physical_bound_not_a_quota_proof() -> None:
    request = _request(
        count=9,
        constraints=ScreenConstraints(
            version="impossible", topic_quotas=(TopicQuota(topic_group="missing", minimum=1),)
        ),
    )
    contract = build_horizon_contract(request, 3)
    assert contract["requested_screens"] == 3
    assert contract["cards_per_screen"] == 6
    assert contract["eligible_count"] == contract["physical_card_upper_bound"] == 9
    assert contract["nominal_screen_counts"] == [6, 3]
    assert contract["quota_feasibility_proven"] is False
    assert build_horizon_contract(_request(), 2)["physical_card_upper_bound"] == 12
    assert build_horizon_contract(_request(count=0), 3)["nominal_screen_counts"] == []


@pytest.mark.parametrize("horizon", [0, 4, True, 1.0])
def test_contract_rejects_noninteger_or_unbounded_horizons(horizon: int) -> None:
    with pytest.raises(ValueError):
        build_horizon_contract(_request(), horizon)
    with pytest.raises(ValueError):
        validate_horizon_coverage(
            _request(), horizon, (_refs(0, 6),), HorizonStop(reason="target_reached")
        )


@pytest.mark.parametrize(
    "update",
    [
        {"initial_screen_counts": ()},
        {"initial_screen_counts": (6, 0)},
        {"initial_screen_counts": (3, 6)},
        {"initial_screen_counts": (6, 7)},
        {"initial_screen_counts": (6, 6, 6)},
        {"requested_screens": 1},
        {"reason": "target_reached"},
        {"reason": "semantic_shortage"},
        {"semantic_quality": "certified"},
        {"target_count": True},
        {"unexpected": "field"},
    ],
)
def test_persisted_completion_checks_its_own_snapshot(update: dict[str, object]) -> None:
    snapshot = PlanningCompletion(
        requested_screens=3,
        target_count=6,
        initial_screen_counts=(6, 3),
        reason="candidate_shortage",
    )
    with pytest.raises(ValueError):
        PlanningCompletion.model_validate({**snapshot.model_dump(mode="python"), **update})


def test_completion_is_frozen_bounded_and_round_trips_without_current_drafts() -> None:
    snapshot = PlanningCompletion(
        requested_screens=3,
        target_count=6,
        initial_screen_counts=(6, 3),
        reason="semantic_shortage",
        omissions=(OmittedGroup(refs=("original_ref",), reason="style_mismatch"),),
    )
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.reason = "candidate_shortage"  # type: ignore[misc]
    assert PlanningCompletion.model_validate_json(snapshot.model_dump_json()) == snapshot
    for field, value in (("note", "x" * 241), ("requested_screens", 4)):
        with pytest.raises(ValidationError):
            PlanningCompletion.model_validate({**snapshot.model_dump(mode="python"), field: value})
    with pytest.raises(ValidationError):
        OmittedGroup(refs=("c1",), reason="off_intent", note="x" * 161)
    with pytest.raises(ValidationError):
        HorizonStop(reason="supply_exhausted", note="x" * 241)


def test_initial_completion_survives_consuming_a_screen_and_rebinding_candidates() -> None:
    request = _request(count=12)
    completion = validate_horizon_coverage(
        request,
        3,
        (_refs(0, 6), _refs(6, 12)),
        HorizonStop(reason="supply_exhausted"),
    )
    plan = _horizon(request, screens=2)
    # This test begins with a historical snapshot, then consumes one draft and
    # rebinds the remaining request-local refs. Completion still describes the
    # original 6+6 plan, not its currently remaining six-card screen.
    data = plan.model_dump(mode="python")
    data["planning_completion"] = completion
    data["screens"] = (plan.screens[1],)
    restored = HorizonPlan.model_validate(data)
    assert restored.planning_completion == completion
    rebound = restored.model_dump(mode="python")
    rebound["cards"] = tuple(
        card.model_copy(update={"ref": f"new_{card.ref}"}) for card in request.cards
    )
    rebound["screens"] = (
        restored.screens[0].model_copy(
            update={
                "pairs": tuple(
                    pair.model_copy(
                        update={"card_refs": tuple(f"new_{ref}" for ref in pair.card_refs)}
                    )
                    for pair in restored.screens[0].pairs
                )
            }
        ),
    )
    assert HorizonPlan.model_validate(rebound).planning_completion.initial_screen_counts == (6, 6)
