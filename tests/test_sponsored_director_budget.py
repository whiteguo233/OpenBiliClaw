"""The free planner keeps the real deadline and avoids implicit reasoning overhead."""

from __future__ import annotations

import json

import pytest

from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.sponsored_runtime_state import (
    SponsoredRuntimeSettings,
    install_sponsored_settings,
    reset_sponsored_settings,
)

from .test_director_horizon_modes import _LLM, _planner_output, _request


@pytest.mark.parametrize("invalid", [False, True])
async def test_free_planner_uses_compact_validated_output_inside_existing_budget(invalid):
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    try:
        request = _request()
        output = json.loads(_planner_output(request))
        for screen in output["screens"]:
            screen["card_refs"] = [ref for pair in screen.pop("pairs") for ref in pair["card_refs"]]
            screen.pop("brief", None)
        output = json.dumps(output)
        llm = _LLM(output if not invalid else '{"screens":[]}')
        result = await HorizonPlanner(llm).plan(request)
        assert llm.calls[0].get("reasoning_effort") == ""
        assert llm.calls[0]["max_tokens"] == 2048
        assert "brief" in llm.calls[0]["system_instruction"]
        assert len(llm.calls) <= 2
        assert (result.plan is None) == invalid
    finally:
        reset_sponsored_settings()


@pytest.mark.parametrize("refs", [["c0", "c1", "c2"], ["c0", "c0", "c2"], ["c0", "c1", "c99"]])
async def test_free_planner_groups_ordered_refs_without_repairing_bad_selections(refs):
    install_sponsored_settings(SponsoredRuntimeSettings(enabled=True))
    try:
        llm = _LLM(
            json.dumps(
                {
                    "completion": {"reason": "target_reached", "omissions": [], "note": ""},
                    "screens": [{"direction": "体验工具", "card_refs": refs}],
                }
            )
        )
        result = await HorizonPlanner(llm).plan(_request(count=3, target=3), horizon_screens=1)
        if refs == ["c0", "c1", "c2"]:
            assert result.plan is not None
            assert [p.card_refs for p in result.plan.screens[0].pairs] == [("c0", "c1"), ("c2",)]
        else:
            assert result.plan is None
            assert result.calls == 2
            feedback = json.loads(llm.calls[1]["user_input"])["validation_feedback"]
            assert feedback["actual_screen_counts"] == [3]
    finally:
        reset_sponsored_settings()
