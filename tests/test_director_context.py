"""Profile and actual screen history reach all three director strategies."""

from __future__ import annotations

import json

import pytest

from openbiliclaw.recommendation.director.controller import plan_needs_replanning
from openbiliclaw.recommendation.director.models import canonical_json
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.soul.profile import AwarenessNote, InsightHypothesis, InterestTag, SoulProfile
from openbiliclaw.soul.profile_views import build_director_profile_view
from tests.test_director_horizon_modes import _request as composition_request
from tests.test_director_runtime import FakeAgent, FakeDecision, make_runtime, seed_database
from tests.test_profile_views import legacy_flat_profile, mature_profile, young_profile


@pytest.mark.parametrize("factory", [legacy_flat_profile, mature_profile, young_profile])
@pytest.mark.parametrize("fast", [False, True])
def test_named_profile_view_is_deterministic_portrait_free_and_keeps_negatives(factory, fast):
    profile = factory()
    before = repr(profile)
    view = build_director_profile_view(profile, fast=fast)
    assert canonical_json(view) == canonical_json(build_director_profile_view(profile, fast=fast))
    assert "PORTRAIT_SENTINEL" not in canonical_json(view)
    assert view["stable"]["disliked_topics"] == profile.preferences.disliked_topics
    assert view["stable"]["style"]["depth_preference"] == profile.preferences.style.depth_preference
    assert len(view["stable"]["interests"]) <= (12 if fast else 32)
    assert repr(profile) == before


def test_profile_view_keeps_all_bans_and_latest_concerns_not_archived_interests():
    profile = SoulProfile()
    profile.preferences.disliked_topics = [f"explicit-ban-{i}" for i in range(160)]
    profile.preferences.interests = [
        InterestTag("active-interest", "tech"),
        InterestTag("archived-interest", "tech", state="archived"),
    ]
    profile.recent_awareness = [AwarenessNote(observation=f"concern-{i}") for i in range(20)]
    view = build_director_profile_view(profile, fast=True)
    assert len(view["stable"]["disliked_topics"]) == 160
    assert "archived-interest" not in canonical_json(view)
    assert [x["observation"] for x in view["recent"]["awareness"]] == [
        "concern-17",
        "concern-18",
        "concern-19",
    ]


def test_rejected_insights_do_not_become_director_preferences():
    profile = SoulProfile(
        active_insights=[
            InsightHypothesis(hypothesis="REJECTED THEORY", user_verdict="rejected"),
            InsightHypothesis(hypothesis="tentative concern", confidence=0.3),
        ]
    )
    view = build_director_profile_view(profile)
    assert "REJECTED THEORY" not in canonical_json(view)
    assert view["recent"]["insights"][0]["validated"] is False
    assert view["recent"]["insights"][0]["confidence"] == 0.3


@pytest.mark.parametrize("strategy", ["jev_recurrent", "agent_jev", "agent_only"])
async def test_all_modes_receive_profile_and_semantic_real_history(tmp_path, strategy):
    db = seed_database(tmp_path / "history.db", count=40)
    agent, provider = FakeAgent(), FakeDecision()
    runtime = make_runtime(db, strategy=strategy, llm=agent, provider=provider)
    profile = SoulProfile(personality_portrait="PRIVATE_PORTRAIT")
    profile.preferences.interests = [InterestTag("Distributed systems", "tech")]
    profile.recent_awareness = [AwarenessNote(observation="Learning database internals")]
    try:
        first = await runtime.generate(profile, stream_ref="s", request_id="one", limit=4)
        assert first is not None
        assert runtime.store.session("s").recent_screens == ()
        key = first.items[0]["item_key"]
        runtime.store.present(first.batch_ref, "visible", [key])
        runtime.store.feedback(first.batch_ref, "negative", key, "dislike", note="Too abstract")
        state = runtime.store.session("s")
        assert len(state.recent_screens) == 1
        card = state.recent_screens[0].cards[0]
        assert len(state.recent_screens[0].cards) == 1
        assert card.exposure_confirmed
        assert card.exposure_at > 0
        assert card.title == first.items[0]["title"]
        assert card.summary and card.topic_group
        assert card.actions[0].kind == "dislike" and card.actions[0].note == "Too abstract"
        assert state.real_feedback[-1].content[0].ref == card.ref
        assert state.real_feedback[-1].occurred_at > 0
        agent.calls.clear()
        provider.requests.clear()
        second = await runtime.generate(profile, stream_ref="s", request_id="two", limit=4)
        assert second is not None
        contexts = (
            [
                {
                    **json.loads(call["user_input"])["session_context"],
                    "profile_context": json.loads(call["user_input"])["profile_context"],
                }
                for call in agent.calls
            ]
            if strategy == "agent_only"
            else [decision.state["session_context"] for decision in provider.requests]
        )
        assert contexts
        for context in contexts:
            assert "Distributed systems" in canonical_json(context["profile_context"])
            assert "Learning database internals" in canonical_json(context["profile_context"])
            assert "PRIVATE_PORTRAIT" not in canonical_json(context)
            assert context["recent_screens"][0]["cards"][0]["title"] == card.title
            assert context["real_feedback"][-1]["content"][0]["title"] == card.title
            assert "fast_profile_context" not in context
            if strategy != "agent_only":
                assert "summary" not in context["recent_screens"][0]["cards"][0]
    finally:
        await runtime.aclose()
        db.close()


async def test_history_window_is_stream_scoped_exposure_only_and_chronological(tmp_path):
    db = seed_database(tmp_path / "window.db", count=40)
    runtime = make_runtime(db, strategy="jev_recurrent", provider=FakeDecision())
    try:
        batches = []
        for index in range(5):
            batch = await runtime.generate(
                SoulProfile(), stream_ref="s", request_id=str(index), limit=2
            )
            batches.append(batch)
            if index != 4:
                runtime.store.present(batch.batch_ref, f"ack-{index}", [batch.items[0]["item_key"]])
        history = runtime.store.session("s").recent_screens
        assert [screen.batch_ref for screen in history] == [b.batch_ref for b in batches[1:4]]
        assert all(len(screen.cards) == 1 and not screen.cards[0].actions for screen in history)
        assert runtime.store.session("unrelated").recent_screens == ()
        frozen = runtime.store.restore("s", batches[1].batch_ref)
        # Context uses committed semantic snapshots even if live metadata changes.
        db.cache_content(str(batches[1].items[0]["bvid"]), title="MUTATED CACHE TITLE")
        assert runtime.store.session("s").recent_screens[0].cards[0].title != "MUTATED CACHE TITLE"
        assert runtime.store.restore("s", batches[1].batch_ref) == frozen
    finally:
        await runtime.aclose()
        db.close()


async def test_interaction_without_ack_does_not_invent_exposure_confirmation(tmp_path):
    db = seed_database(tmp_path / "click.db")
    runtime = make_runtime(db)
    try:
        batch = await runtime.generate(SoulProfile(), stream_ref="s", request_id="one", limit=2)
        runtime.store.feedback(batch.batch_ref, "click", batch.items[1]["item_key"], "click")
        card = runtime.store.session("s").recent_screens[0].cards[0]
        assert card.position == 2 and not card.exposure_confirmed
        assert [action.kind for action in card.actions] == ["click"]
    finally:
        await runtime.aclose()
        db.close()


@pytest.mark.parametrize("strategy", ["agent_only", "agent_jev"])
async def test_profile_edit_invalidates_agent_plan_even_without_new_feedback(strategy):
    request = composition_request()
    session = request.session.model_copy(
        update={"profile_context": {"stable": {"interests": ["old"]}}}
    )
    request = request.model_copy(update={"session": session})
    result = await HorizonPlanner(FakeAgent()).plan(request)
    assert result.plan is not None
    assert not plan_needs_replanning(request, result.plan, strategy=strategy)
    changed = request.model_copy(
        update={
            "session": session.model_copy(
                update={"profile_context": {"stable": {"interests": ["new"]}}}
            )
        }
    )
    assert plan_needs_replanning(changed, result.plan, strategy=strategy)


async def test_planner_keeps_stable_profile_before_volatile_context_in_user_prompt():
    request = composition_request()
    profile = {"recent": {"awareness": ["new concern"]}, "stable": {"interests": ["系统设计"]}}
    request = request.model_copy(
        update={"session": request.session.model_copy(update={"profile_context": profile})}
    )
    agent = FakeAgent()
    planner = HorizonPlanner(agent)
    assert (await planner.plan(request)).plan is not None
    changed = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={
                    "explicit_intent": "接下来轻松一点",
                    "profile_context": {
                        "stable": profile["stable"],
                        "recent": {"awareness": ["another concern"]},
                    },
                }
            )
        }
    )
    assert (await planner.plan(changed)).plan is not None
    prefix = '{"profile_context":{"stable":' + canonical_json(profile["stable"]) + ","
    assert all(call["user_input"].startswith(prefix) for call in agent.calls)
    assert agent.calls[0]["system_instruction"] == agent.calls[1]["system_instruction"]
    assert agent.calls[0]["inject_core_memory"] is False
    assert "profile_context" not in json.loads(agent.calls[0]["user_input"])["session_context"]
