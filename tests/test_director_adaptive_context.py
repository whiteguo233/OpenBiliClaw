"""Model-aware director packing and stable profile-source identity (offline)."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from openbiliclaw.config import Config, JevContextLimits
from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.director.composer import (
    PairComposer,
    _decision_request,
    profile_context_headroom,
)
from openbiliclaw.recommendation.director.controller import (
    DirectorController,
    plan_needs_replanning,
)
from openbiliclaw.recommendation.director.models import (
    ExperiencedCard,
    RealFeedback,
    RecentScreen,
    digest,
)
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from openbiliclaw.recommendation.director.proposer import propose_pairs, validate_screen
from openbiliclaw.recommendation.director.repair import DraftRepairer
from openbiliclaw.recommendation.director.runtime import DirectorRuntime
from openbiliclaw.recommendation.engine import DirectorCandidateSnapshot, RecommendationEngine
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.soul.profile_views import build_profile_catalog, select_profile_context
from tests.test_director_horizon_modes import _horizon
from tests.test_director_runtime import FakeAgent, FakeDecision, seed_database
from tests.test_pair_composer import _evidence, _RecordingProvider, _request


def _budget(single: int, total: int | None = None):
    return replace(
        resolve_jev_context("jev-1.13.0"),
        max_state_question_bytes=single,
        max_request_bytes=total or single * 2,
    )


async def test_pair_adapts_alternatives_to_selected_model_without_removing_prefix() -> None:
    request = _request(count=20, target=6)
    alternatives = propose_pairs(request, (), request.target_count)
    full = _decision_request(request, (), alternatives, request.target_count, time.monotonic() + 60)
    one = _decision_request(
        request, (), alternatives[:1], request.target_count, time.monotonic() + 60
    )
    measuring = resolve_jev_context("jev-1.13.0")
    full_bytes = measuring.measure(full.state, full.questions).max_state_question_bytes
    minimum_bytes = measuring.measure(one.state, one.questions).max_state_question_bytes
    budget = _budget(minimum_bytes + 1000)
    assert full_bytes > budget.max_state_question_bytes
    provider = _RecordingProvider()
    result = await PairComposer(provider, context_budget=budget).prepare_next(request)
    assert result.provider_calls >= 1
    assert len(result.pairs[0].alternatives) < len(alternatives)
    assert validate_screen(request, result.card_refs, request.target_count)
    for sent in provider.requests:
        assert budget.fits_request(sent)
        index = int(sent.request_id.rsplit(":", 1)[1])
        assert sent.state["tentative_prefix"] == list(result.card_refs[: index * 2])
        assert sent.state["session_context"] == request.session.prompt_context(fast=True)
        assert sent.state["constraints_digest"] == digest(
            request.constraints.model_dump(mode="json")
        )


async def test_profile_headroom_reserves_future_prefix_not_entire_candidate_pool() -> None:
    request = _request(count=40, target=6)
    request = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(
                    update={
                        "ref": f"card-{index:03}",
                        "title": "合成标题" * (2 + index),
                        "summary": "长篇中文卡片摘要" * (5 + index),
                    }
                )
                for index, card in enumerate(request.cards)
            )
        }
    )
    catalog = build_profile_catalog(
        SoulProfile(
            preferences=PreferenceLayer(
                interests=[InterestTag(f"手工兴趣{i}", "广泛", 0.8) for i in range(300)],
                disliked_topics=["未看完作品不剧透，已看完除外"],
            )
        )
    )
    floor = select_profile_context(catalog, query_texts=(), max_bytes=0, purpose="director_pair")
    minimum = request.model_copy(
        update={
            "session": request.session.model_copy(update={"fast_profile_context": floor.payload})
        }
    )
    budget = _budget(20000)
    remaining = profile_context_headroom(minimum, budget)
    assert remaining > 0
    assert sum(len(card.model_dump_json().encode()) for card in request.cards) > 20000
    projection = select_profile_context(
        catalog,
        query_texts=("手工兴趣299",),
        max_bytes=floor.used_bytes + remaining,
        purpose="director_pair",
    )
    assert projection.included_interest_names
    packed = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={"fast_profile_context": projection.payload}
            )
        }
    )

    def choose_other_prefix(decision):
        options = decision.questions["pair"].criteria
        role = ("complement", "exploration", "anchor")[int(decision.request_id.rsplit(":", 1)[1])]
        choice = next(option for option in reversed(options) if option.startswith(role + ":"))
        return _evidence(decision, choice)

    provider = _RecordingProvider(choose_other_prefix)
    result = await PairComposer(provider, context_budget=budget).prepare_next(packed)
    assert result.provider_calls == 3
    assert all(pair.origin == "decision" for pair in result.pairs)
    assert all(budget.fits_request(sent) for sent in provider.requests)
    assert provider.requests[-1].state["tentative_prefix"] == list(result.card_refs[:4])
    assert validate_screen(packed, result.card_refs, 6)


@pytest.mark.parametrize("single,total", [(500, 100000), (100000, 500)])
async def test_either_budget_rejects_without_provider_call_or_model_success(single, total) -> None:
    provider = _RecordingProvider()
    result = await DirectorController(provider, context_budget=_budget(single, total)).prepare_next(
        _request(), strategy="jev_recurrent"
    )
    assert provider.requests == []
    assert result.proposal.realized_strategy == "deterministic"
    assert all(pair.reason_code == "decision_input_too_large" for pair in result.proposal.pairs)


async def test_small_repair_budget_keeps_known_draft_and_records_guard_without_call() -> None:
    request = _request(count=12, target=4)
    provider = FakeDecision()
    plan = _horizon(request, screens=1)
    result = await DraftRepairer(provider, context_budget=_budget(500)).repair(request, plan)
    assert provider.requests == []
    assert result.provider_calls == 0
    assert result.horizon.screens[0].card_refs == plan.screens[0].card_refs
    assert all(trace.reason_code == "decision_input_too_large" for trace in result.traces[0])


async def test_projection_churn_does_not_replan_but_profile_source_changes_do() -> None:
    request = _request()
    first_session = request.session.model_copy(
        update={
            "profile_context": {"interests": ["古琴"]},
            "fast_profile_context": {"interests": ["古琴"]},
            "profile_source_digest": "a" * 64,
        }
    )
    first = request.model_copy(update={"session": first_session})
    planned = await HorizonPlanner(FakeAgent()).plan(first, horizon_screens=1)
    assert planned.plan is not None
    second_session = first_session.model_copy(
        update={
            "profile_context": {"interests": ["洞箫"]},
            "fast_profile_context": {"interests": ["洞箫"]},
        }
    )
    second = first.model_copy(update={"session": second_session})
    assert second.input_digest != first.input_digest
    assert second.session.profile_digest == first.session.profile_digest
    assert "profile_source_digest" not in second.session.prompt_context()
    assert "profile_source_digest" not in second.session.prompt_context(fast=True)
    for strategy in ("agent_only", "agent_jev"):
        assert not plan_needs_replanning(second, planned.plan, strategy=strategy)
        changed = second.model_copy(
            update={
                "session": second_session.model_copy(update={"profile_source_digest": "b" * 64})
            }
        )
        assert plan_needs_replanning(changed, planned.plan, strategy=strategy)


def _snapshot(title: str) -> DirectorCandidateSnapshot:
    return DirectorCandidateSnapshot(
        items=(
            DiscoveredContent(
                bvid="BVfixture", title=title, topic_group="音乐", relevance_score=0.9
            ),
        ),
        source_rows=(),
        scores={},
        pool_counts={},
    )


async def test_runtime_uses_full_catalog_and_binds_capacity_not_candidate_projection(
    tmp_path,
) -> None:
    database = seed_database(tmp_path / "adaptive.db")
    config = Config()
    config.director.mode = "enforce"
    config.director.strategy = "jev_recurrent"
    config.decision.providers.jev.model = "private-g5"
    config.decision.providers.jev.context_limits = {"private-g5": JevContextLimits(24000, 12000)}
    agent, provider = FakeAgent(), FakeDecision()
    runtime = DirectorRuntime(
        config, database, RecommendationEngine(agent, database), agent, provider=provider
    )
    tags = [InterestTag(f"通用兴趣{i}", "广泛", 0.9) for i in range(300)]
    tags.extend([InterestTag("古琴漆艺", "音乐", 0.01), InterestTag("洞箫修复", "音乐", 0.01)])
    profile = SoulProfile(
        preferences=PreferenceLayer(interests=tags, disliked_topics=["未看完作品不要剧透"])
    )
    try:

        def build(title):
            return runtime._build_request(
                runtime.store.session("s"),
                _snapshot(title),
                request_id="r",
                target_count=1,
                direction_ref="d",
                direction="浏览",
                explicit_intent="",
                profile=profile,
            )

        first, second = build("古琴漆艺入门"), build("洞箫修复教程")
        assert first.session.profile_digest == second.session.profile_digest
        assert first.session.fast_profile_context != second.session.fast_profile_context
        assert "古琴漆艺" in json.dumps(first.session.fast_profile_context, ensure_ascii=False)
        assert "洞箫修复" in json.dumps(second.session.fast_profile_context, ensure_ascii=False)
        assert len(first.session.fast_profile_context["interests"]) < len(tags)
        assert first.session.fast_profile_context["disliked_topics"] == ["未看完作品不要剧透"]
        assert runtime.context_budget.model == "private-g5"
        assert runtime.context_budget.max_state_question_bytes == 9000
        assert "on-demand-profile-catalog" not in first.model_dump_json()
    finally:
        await runtime.aclose()
        database.close()


async def test_runtime_large_negative_floor_yields_explicit_no_call_fallback(tmp_path) -> None:
    database = seed_database(tmp_path / "oversize.db")
    config = Config()
    config.director.mode = "enforce"
    config.director.strategy = "jev_recurrent"
    config.decision.providers.jev.context_limits = {"jev-1.13.0": JevContextLimits(4000, 2000)}
    agent, provider = FakeAgent(), FakeDecision()
    runtime = DirectorRuntime(
        config, database, RecommendationEngine(agent, database), agent, provider=provider
    )
    negative = "未看完的作品不要剧透，但已经明确看完的除外。" * 200
    profile = SoulProfile(preferences=PreferenceLayer(disliked_topics=[negative]))
    try:
        request = runtime._build_request(
            runtime.store.session("s"),
            _snapshot("古琴"),
            request_id="r",
            target_count=1,
            direction_ref="d",
            direction="浏览",
            explicit_intent="",
            profile=profile,
        )
        assert request.session.fast_profile_context["disliked_topics"] == [negative]
        result = await runtime.controller.prepare_next(request, strategy="jev_recurrent")
        assert result.proposal.realized_strategy == "deterministic"
        assert result.proposal.pairs[0].reason_code == "decision_input_too_large"
        assert provider.requests == []
    finally:
        await runtime.aclose()
        database.close()


async def test_selected_model_capacity_changes_runtime_identity_and_fast_projection(
    tmp_path,
) -> None:
    database = seed_database(tmp_path / "capacity-identity.db")
    agent = FakeAgent()
    runtimes = []
    try:
        for limit in (6000, 32000):
            config = Config()
            config.director.mode = "enforce"
            config.director.strategy = "jev_recurrent"
            config.decision.providers.jev.model = "selected-model"
            config.decision.providers.jev.context_limits = {
                "selected-model": JevContextLimits(limit * 2, limit)
            }
            runtimes.append(
                DirectorRuntime(
                    config,
                    database,
                    RecommendationEngine(agent, database),
                    agent,
                    provider=FakeDecision(),
                    activate=False,
                )
            )
        assert runtimes[0].context_digest != runtimes[1].context_digest
        assert all(runtime.context_budget.model == "selected-model" for runtime in runtimes)
        profile = SoulProfile(
            preferences=PreferenceLayer(
                interests=[InterestTag(f"乐器工艺{i}", "音乐", 0.9) for i in range(200)]
            )
        )
        requests = [
            runtime._build_request(
                runtime.store.session("s"),
                _snapshot("乐器工艺"),
                request_id="r",
                target_count=1,
                direction_ref="d",
                direction="浏览",
                explicit_intent="",
                profile=profile,
            )
            for runtime in runtimes
        ]
        assert len(requests[0].session.fast_profile_context["interests"]) < len(
            requests[1].session.fast_profile_context["interests"]
        )
        assert requests[0].session.profile_digest == requests[1].session.profile_digest
        assert requests[0].input_digest != requests[1].input_digest
    finally:
        for runtime in runtimes:
            await runtime.aclose()
        database.close()


async def test_agent_only_does_not_resolve_disabled_unknown_jev_capacity(tmp_path) -> None:
    database = seed_database(tmp_path / "agent-only.db")
    config = Config()
    config.director.mode = "enforce"
    config.director.strategy = "agent_only"
    config.decision.providers.jev.model = "unknown-disabled-model"
    agent = FakeAgent()
    runtime = DirectorRuntime(config, database, RecommendationEngine(agent, database), agent)
    try:
        assert runtime.context_budget is None
        request = runtime._build_request(
            runtime.store.session("s"),
            _snapshot("古琴"),
            request_id="r",
            target_count=1,
            direction_ref="d",
            direction="浏览",
            explicit_intent="",
            profile=SoulProfile(),
        )
        result = await runtime.controller.prepare_next(request, strategy="agent_only")
        assert result.proposal.provider_calls == 0
    finally:
        await runtime.aclose()
        database.close()


@pytest.mark.parametrize("rich_history,spare_bytes", [(False, 20), (True, 300)])
async def test_runtime_accounts_for_complete_envelope_before_loading_optional_profile(
    tmp_path, rich_history, spare_bytes
) -> None:
    database = seed_database(tmp_path / "exact-envelope.db")
    config = Config()
    config.director.mode = "enforce"
    config.director.strategy = "jev_recurrent"
    agent, provider = FakeAgent(), FakeDecision()
    runtime = DirectorRuntime(
        config, database, RecommendationEngine(agent, database), agent, provider=provider
    )
    runtimes = [runtime]
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(f"乐器工艺{i}", "音乐", 0.9) for i in range(200)],
            disliked_topics=["未看完的作品不要剧透，但已明确看完的除外"],
        )
    )
    session = runtime.store.session("s")
    if rich_history:
        session = session.model_copy(
            update={
                "real_feedback": tuple(
                    RealFeedback(
                        event_ref=f"event-{index}",
                        batch_ref="past-screen",
                        kind="explicit_request",
                        description="合成测试的近期真实反馈，不要夸张结论。" * 8,
                    )
                    for index in range(8)
                ),
                "recent_screens": tuple(
                    RecentScreen(
                        batch_ref=f"past-{index}",
                        direction="合成历史，仅作为实际曝光记录",
                        committed_at=1,
                        cards=(
                            ExperiencedCard(
                                ref=f"past-card-{index}",
                                title="合成历史标题" * 10,
                                summary="过去的摘要不会在快速请求重复",
                                position=1,
                                exposure_confirmed=True,
                            ),
                        ),
                    )
                    for index in range(3)
                ),
            }
        )

    def build(active):
        return active._build_request(
            session,
            _snapshot("乐器工艺"),
            request_id="r",
            target_count=1,
            direction_ref="d",
            direction="浏览",
            explicit_intent="",
            profile=profile,
        )

    try:
        floor = select_profile_context(
            build_profile_catalog(profile), query_texts=(), max_bytes=0, purpose="director_pair"
        )
        sample = build(runtime)
        minimum = sample.model_copy(
            update={
                "session": sample.session.model_copy(update={"fast_profile_context": floor.payload})
            }
        )
        probe = _decision_request(
            minimum, (), propose_pairs(minimum, (), 1)[:1], 1, time.monotonic() + 60
        )
        minimal_bytes = runtime.context_budget.measure(
            probe.state, probe.questions
        ).max_state_question_bytes
        tokens = ((minimal_bytes + spare_bytes) * 4 + 2) // 3
        config.decision.providers.jev.context_limits = {
            "jev-1.13.0": JevContextLimits(tokens * 2, tokens)
        }
        constrained = DirectorRuntime(
            config,
            database,
            RecommendationEngine(agent, database),
            agent,
            provider=provider,
            activate=False,
        )
        runtimes.append(constrained)
        request = build(constrained)
        assert len(request.session.fast_profile_context["interests"]) < 10
        if rich_history:
            assert request.session.fast_profile_context["interests"]
        result = await constrained.controller.prepare_next(request, strategy="jev_recurrent")
        assert result.proposal.provider_calls == 1
        assert result.proposal.pairs[0].origin == "decision"
        assert constrained.context_budget.fits_request(provider.requests[0])
        sent = provider.requests[0].state["session_context"]
        assert sent == request.session.prompt_context(fast=True)
        assert sent["profile_context"]["disliked_topics"] == profile.preferences.disliked_topics
        assert sent["profile_context"]["view_is_partial"] is True
        assert request.session.real_feedback == session.real_feedback
        assert request.session.recent_screens == session.recent_screens
    finally:
        for active in runtimes:
            await active.aclose()
        database.close()
