"""Initial coverage survives execution; intentional short tails are not padded."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.recommendation.director.controller import DirectorController
from openbiliclaw.recommendation.director.horizon_models import HorizonPlan
from openbiliclaw.recommendation.director.models import ScreenConstraints, TopicQuota
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.recommendation.director.repair import DraftRepairer
from openbiliclaw.soul.profile import SoulProfile
from tests.test_director_horizon_modes import _LLM, _planner_output, _Provider, _request
from tests.test_director_runtime import FakeAgent, FakeDecision, make_runtime, seed_database

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.recommendation.director.horizon_models import DirectorStrategy


def _shorten(output: dict[str, Any], all_refs: list[str], count: int = 9) -> str:
    """Synthetic semantic judgment, not a real quality label or preference."""
    remaining = count
    screens = []
    selected = set()
    for screen in output["screens"]:
        refs = [ref for pair in screen["pairs"] for ref in pair["card_refs"]][:remaining]
        if not refs:
            break
        screen["pairs"] = [{"card_refs": refs[i : i + 2]} for i in range(0, len(refs), 2)]
        selected.update(refs)
        screens.append(screen)
        remaining -= len(refs)
    output["screens"] = screens
    output["completion"] = {
        "reason": "supply_exhausted",
        "omissions": [
            {
                "refs": [ref for ref in all_refs if ref not in selected],
                "reason": "off_intent",
                "note": "Synthetic omission fixture, not actual content quality",
            }
        ],
    }
    return json.dumps(output)


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
async def test_controller_preserves_short_tail_without_replanning_every_full_screen(
    strategy: DirectorStrategy,
) -> None:
    request = _request()
    llm = _LLM(_shorten(json.loads(_planner_output(request)), [card.ref for card in request.cards]))
    planning = await HorizonPlanner(llm).plan(request, now_epoch=100.0)
    assert planning.plan is not None
    provider = _Provider()
    controller = DirectorController(provider)
    for index in (0, 1):
        result = await controller.prepare_next(
            request, strategy=strategy, horizon=planning.plan, screen_index=index, now_epoch=101.0
        )
        assert result.horizon is not None
        assert [len(s.card_refs) for s in result.horizon.screens] == [6, 3]
        assert result.horizon.planning_completion == planning.plan.planning_completion
        assert not result.replan_required
        assert not result.patches
        assert len(result.proposal.card_refs) == (6 if index == 0 else 3)
        assert result.proposal.status == ("COMPLETE" if index == 0 else "SHORTAGE")
        assert HorizonPlan.model_validate_json(result.horizon.model_dump_json()) == result.horizon
    assert bool(provider.requests) == (strategy == "agent_jev")


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
@pytest.mark.parametrize("change", ["missing_card", "hard_minimum"])
async def test_semantic_short_tail_still_rechecks_hard_changes_and_requests_replan(
    strategy: DirectorStrategy, change: str
) -> None:
    request = _request()
    planning = await HorizonPlanner(
        _LLM(_shorten(json.loads(_planner_output(request)), [c.ref for c in request.cards]))
    ).plan(request, now_epoch=100.0)
    assert planning.plan is not None
    if change == "missing_card":
        request = request.model_copy(
            update={"cards": tuple(c for c in request.cards if c.ref != "c8")}
        )
    else:
        request = request.model_copy(
            update={
                "constraints": ScreenConstraints(
                    version="changed",
                    topic_quotas=(TopicQuota(topic_group="tools", minimum=4),),
                )
            }
        )
    result = await DirectorController(_Provider()).prepare_next(
        request, strategy=strategy, horizon=planning.plan, screen_index=1, now_epoch=101.0
    )
    assert result.replan_required
    assert result.horizon is not None
    assert result.horizon.planning_completion == planning.plan.planning_completion
    assert "c8" not in result.proposal.card_refs
    if change == "hard_minimum":
        assert result.proposal.status == "UNSATISFIABLE"
        assert result.proposal.card_refs == ()
    else:
        assert len(result.proposal.card_refs) == 3
        expected_origin = "deterministic" if strategy == "agent_only" else "decision"
        assert any(pair.origin == expected_origin for pair in result.proposal.pairs)


async def test_hard_shrink_does_not_turn_an_original_full_screen_into_semantic_shortage() -> None:
    request = _request()
    planning = await HorizonPlanner(
        _LLM(_shorten(json.loads(_planner_output(request)), [c.ref for c in request.cards]))
    ).plan(request, now_epoch=100.0)
    assert planning.plan is not None
    restricted = request.model_copy(
        update={
            "constraints": ScreenConstraints(
                version="temporary_caps",
                topic_quotas=(
                    TopicQuota(topic_group="tools", maximum=2),
                    TopicQuota(topic_group="explore", maximum=2),
                ),
            )
        }
    )
    reduced = await DraftRepairer().repair(restricted, planning.plan)
    assert len(reduced.horizon.screens[0].card_refs) == 4
    assert reduced.horizon.screens[0].initial_card_count == 6
    assert reduced.replan_required
    restored = await DraftRepairer().repair(request, reduced.horizon)
    assert [len(screen.card_refs) for screen in restored.horizon.screens] == [6, 3]
    assert restored.horizon.planning_completion == planning.plan.planning_completion


class _ShortAgent(FakeAgent):
    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        response = await super().complete_structured_task(**kwargs)
        payload = json.loads(kwargs["user_input"])
        response.content = _shorten(
            json.loads(response.content), [card["ref"] for card in payload["untrusted_candidates"]]
        )
        return response


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
async def test_runtime_consumption_restart_and_cached_short_tail_preserve_initial_snapshot(
    tmp_path: Path, strategy: str
) -> None:
    db = seed_database(tmp_path / "coverage.db")
    agent = _ShortAgent()
    runtime = make_runtime(db, llm=agent, provider=FakeDecision(), strategy=strategy)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="a", limit=4)
        assert first is not None
        trace = json.loads(first.trace_json)["planning"]
        initial = trace["completion"]
        assert trace["source"] == "fresh" and trace["calls"] == 1
        assert initial["initial_screen_counts"] == [4, 4, 1]
        assert initial["reason"] == "semantic_shortage"
        saved = runtime.store.load_plan("s")
        assert saved is not None and len(saved.plan["screens"]) == 2
        assert saved.plan["planning_completion"] == initial
        assert len(agent.calls) == 1
        await runtime.aclose()
        runtime = make_runtime(db, llm=agent, provider=FakeDecision(), strategy=strategy)
        assert await runtime.restore("s") == first
        second = await runtime.generate(SoulProfile(), stream_ref="s", request_id="b", limit=4)
        assert second is not None and len(second.items) == 4
        assert json.loads(second.trace_json)["planning"] == {
            "source": "cached",
            "reason_code": "cached_agent_horizon",
            "calls": 0,
            "completion": initial,
        }
        assert len(agent.calls) == 1
        last = await runtime.generate(SoulProfile(), stream_ref="s", request_id="c", limit=4)
        assert last is not None and len(last.items) == 1
        assert json.loads(last.trace_json)["planning"]["completion"] == initial
        assert len({item["item_key"] for b in (first, second, last) for item in b.items}) == 9
    finally:
        await runtime.aclose()
        db.close()


async def test_stay_does_not_replace_future_initial_coverage_with_one_screen_snapshot(
    tmp_path: Path,
) -> None:
    db = seed_database(tmp_path / "stay.db")
    runtime = make_runtime(db)
    try:
        first = await runtime.generate(SoulProfile(), stream_ref="s", request_id="a", limit=4)
        assert first is not None
        saved = runtime.store.load_plan("s")
        assert saved is not None
        initial = saved.plan["planning_completion"]
        stay = await runtime.generate(
            SoulProfile(), stream_ref="s", request_id="stay", intent="STAY", limit=4
        )
        assert stay is not None
        assert json.loads(stay.trace_json)["planning"]["completion"]["requested_screens"] == 1
        remaining = runtime.store.load_plan("s")
        assert remaining is not None and remaining.plan["planning_completion"] == initial
        assert initial["requested_screens"] == 3
    finally:
        await runtime.aclose()
        db.close()


@pytest.mark.parametrize("strategy", ["agent_only", "jev_recurrent"])
async def test_runtime_trace_distinguishes_agent_failure_and_no_planning(
    tmp_path: Path, strategy: str
) -> None:
    db = seed_database(tmp_path / "trace.db")
    agent = FakeAgent()
    agent.fail = True
    runtime = make_runtime(db, llm=agent, strategy=strategy, provider=FakeDecision())
    try:
        batch = await runtime.generate(SoulProfile(), stream_ref="s", request_id="a", limit=4)
        assert batch is not None
        planning = json.loads(batch.trace_json)["planning"]
        assert planning["completion"] is None
        if strategy == "agent_only":
            assert planning["source"] == "fresh"
            assert planning["calls"] == 1 and planning["reason_code"] == "planner_unavailable"
        else:
            assert planning["source"] == "not_applicable"
            assert planning["calls"] == 0 and not agent.calls
        assert "offline fixture unavailable" not in batch.trace_json
    finally:
        await runtime.aclose()
        db.close()
