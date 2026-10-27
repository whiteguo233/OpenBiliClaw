"""Offline replay of recorded live outputs, not new model or quality evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.director.horizon_coverage import validate_horizon_coverage
from openbiliclaw.recommendation.director.models import CompositionRequest
from openbiliclaw.recommendation.director.planner import HorizonPlanner, _PlannerOutput

_ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "director-effect-eval"


def _recorded_case(filename: str) -> tuple[CompositionRequest, dict[str, Any]]:
    report = json.loads((_ARTIFACTS / filename).read_text(encoding="utf-8"))
    case = next(case for case in report["cases"] if case["case_id"] == "practical_agent")
    result = next(
        result
        for result in report["results"]
        if result["case_id"] == "practical_agent" and result["condition"] == "screen_policy"
    )
    assert result["cli_transport"]["completed"] is True
    request = CompositionRequest.model_validate_json(json.dumps(case["input"]))
    return request, json.loads(result["calls"][0]["raw_response"])


class _RecordedResponse:
    def __init__(self, output: dict[str, Any], request: CompositionRequest) -> None:
        # Historical captures use canonical refs. Rebind their exact decisions
        # to today's request-local wire aliases; do not repair/drop any selection.
        aliases = {
            card.ref: f"c{index}"
            for index, card in enumerate(
                card
                for card in request.cards
                if card.eligible and card.item_key not in request.constraints.excluded_item_keys
            )
        }
        self.output = copy.deepcopy(output)
        for screen in self.output["screens"]:
            for pair in screen["pairs"]:
                pair["card_refs"] = [aliases.get(ref, ref) for ref in pair["card_refs"]]
        for group in self.output["completion"]["omissions"]:
            group["refs"] = [aliases.get(ref, ref) for ref in group["refs"]]
        self.calls = 0

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=json.dumps(self.output, ensure_ascii=False),
            model="recorded-response-not-live",
            provider="offline-artifact-replay",
        )


@pytest.mark.parametrize(
    "filename",
    ["luna-stream-recovery-matrix.json", "luna-e2e-1002-baseline.json"],
)
async def test_completed_live_transport_does_not_make_invalid_plan_acceptable(filename: str):
    request, output = _recorded_case(filename)
    before = request.model_dump_json()
    replay = _RecordedResponse(output, request)
    # Historical one-call failure: a replay cannot invent a corrective live reply.
    result = await HorizonPlanner(replay).plan(request, horizon_screens=3, max_validation_retries=0)
    assert result.reason_code == "invalid_planner_output"
    assert result.plan is None
    assert result.calls == replay.calls == 1
    assert request.model_dump_json() == before


def test_recorded_selected_and_omitted_pair_is_rejected_at_coverage_boundary():
    request, raw = _recorded_case("luna-stream-recovery-matrix.json")
    output = _PlannerOutput.model_validate_json(json.dumps(raw))
    screens = tuple(
        tuple(ref for pair in screen.pairs for ref in pair.card_refs) for screen in output.screens
    )
    selected = {ref for screen in screens for ref in screen}
    omitted = {ref for group in output.completion.omissions for ref in group.refs}
    assert tuple(map(len, screens)) == (6, 6)
    assert selected & omitted == {"c02", "c03"}
    with pytest.raises(ValueError, match="omissions must be eligible unselected references"):
        validate_horizon_coverage(request, 3, screens, output.completion)


async def test_counterfactual_without_rejected_pair_can_be_audited_six_plus_four():
    # Minimal offline counterfactual, NOT automatic production repair and NOT a
    # successful live model result. The original captured response stays intact.
    request, recorded = _recorded_case("luna-stream-recovery-matrix.json")
    output = copy.deepcopy(recorded)
    rejected = output["screens"][1]["pairs"].pop()
    assert rejected["card_refs"] == ["c02", "c03"]
    result = await HorizonPlanner(_RecordedResponse(output, request)).plan(request)
    assert result.plan is not None
    assert tuple(len(screen.card_refs) for screen in result.plan.screens) == (6, 4)
    assert result.plan.planning_completion is not None
    assert result.plan.planning_completion.reason == "semantic_shortage"
    assert result.plan.planning_completion.semantic_quality == "not_certified"
    assert recorded["screens"][1]["pairs"][-1] == rejected


def test_recorded_six_six_four_cannot_claim_target_reached_with_omissions():
    _, raw = _recorded_case("luna-e2e-1002-baseline.json")
    assert tuple(
        sum(len(pair["card_refs"]) for pair in screen["pairs"]) for screen in raw["screens"]
    ) == (6, 6, 4)
    assert raw["completion"]["reason"] == "target_reached"
    assert raw["completion"]["omissions"]
    with pytest.raises(ValidationError, match="complete horizon cannot report semantic shortage"):
        _PlannerOutput.model_validate_json(json.dumps(raw))


async def test_changing_stop_enum_alone_does_not_hide_recorded_unexplained_underfill():
    request, raw = _recorded_case("luna-e2e-1002-baseline.json")
    # Changing the enum cannot make the remaining, unaudited c15 disappear.
    raw["completion"]["reason"] = "supply_exhausted"
    output = _PlannerOutput.model_validate_json(json.dumps(raw))
    screens = tuple(
        tuple(ref for pair in screen.pairs for ref in pair.card_refs) for screen in output.screens
    )
    with pytest.raises(ValueError, match="short final screen leaves an admissible extension"):
        validate_horizon_coverage(request, 3, screens, output.completion)
    result = await HorizonPlanner(_RecordedResponse(raw, request)).plan(request)
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
