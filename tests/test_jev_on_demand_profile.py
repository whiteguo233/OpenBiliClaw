"""On-demand profile integration at the native evaluator and cache boundary."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any

import pytest

from openbiliclaw.config import JevEvaluationConfig
from openbiliclaw.decision.context_budget import JevContextLimits, resolve_jev_context
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.soul.profile_views import PROFILE_CONTEXT_VERSION, build_profile_catalog

from .test_jev_evaluator_wiring import RecordingEvaluator, item, make_engine
from .test_jev_full_evaluator import ITEM, MODEL, NativeProvider, native_evidence
from .test_on_demand_profile_views import _mature_profile
from .test_search_strategy import _build_profile


async def test_evaluation_loads_relevant_tail_not_entire_source_profile() -> None:
    profile = _mature_profile()
    original = copy.deepcopy(profile.to_dict())
    catalog = build_profile_catalog(profile)
    provider = NativeProvider()
    adapter = JevContentEvaluator(provider, model=MODEL, config=JevEvaluationConfig())
    result = await adapter.evaluate(
        profile_summary=catalog,
        content_items=[{**ITEM, "title": "古琴漆艺教程"}],
    )
    assert result[0] is not None
    state = provider.requests[0].state
    assert state["profile"]["disliked_topics"] == profile.preferences.disliked_topics
    loaded = state["candidate_profiles"]["c0"]
    assert loaded["schema_version"] == PROFILE_CONTEXT_VERSION
    names = [row["name"] for row in loaded["interests"]]
    assert "古琴漆艺" in names
    assert len(names) < 300
    assert "disliked_topics" not in loaded
    assert "on-demand-profile-catalog" not in json.dumps(state)
    assert profile.to_dict() == original
    assert all(adapter._fits(request) for request in provider.requests)


async def test_same_candidate_projection_is_independent_of_batch_peers() -> None:
    provider = NativeProvider()
    adapter = JevContentEvaluator(provider, model=MODEL, config=JevEvaluationConfig())
    catalog = build_profile_catalog(_mature_profile())
    target = {**ITEM, "title": "古琴漆艺教程"}
    await adapter.evaluate(profile_summary=catalog, content_items=[target])
    alone = provider.requests[0].state["candidate_profiles"]["c0"]
    provider.requests.clear()
    await adapter.evaluate(
        profile_summary=catalog,
        content_items=[{**ITEM, "title": "通用兴趣10"}, target],
    )
    together = next(
        request.state["candidate_profiles"]["c1"]
        for request in provider.requests
        if "c1" in request.state["candidate_profiles"]
    )
    assert together == alone


async def test_batched_candidates_share_explicit_exclusions_once() -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag("古琴", "音乐", 0.8)],
            disliked_topics=["明确不看带货，除非只是批判分析"],
        )
    )
    provider = NativeProvider()
    adapter = JevContentEvaluator(provider, model=MODEL, config=JevEvaluationConfig())
    rows = await adapter.evaluate(
        profile_summary=build_profile_catalog(profile), content_items=[ITEM, ITEM]
    )
    assert all(row is not None for row in rows)
    assert len(provider.requests) == 1
    state = provider.requests[0].state
    assert state["profile"]["disliked_topics"] == profile.preferences.disliked_topics
    assert json.dumps(state, ensure_ascii=False).count("明确不看带货，除非只是批判分析") == 1


async def test_selected_model_capacity_changes_projection_without_switching_model() -> None:
    loaded: list[dict[str, Any]] = []
    namespaces: list[str] = []
    for model, single in ((MODEL, 32000), ("test-smaller-model", 16000)):
        budget = resolve_jev_context(model, {model: JevContextLimits(64000, single)})
        provider = NativeProvider(
            lambda request, model=model: native_evidence(request).model_copy(
                update={"requested_model": model, "actual_model": model}
            )
        )
        adapter = JevContentEvaluator(
            provider, model=model, config=JevEvaluationConfig(), context_budget=budget
        )
        result = await adapter.evaluate(
            profile_summary=build_profile_catalog(_mature_profile()),
            content_items=[{**ITEM, "title": "古琴漆艺教程"}],
        )
        assert result[0] is not None
        assert all(budget.fits_request(request) for request in provider.requests)
        loaded.append(provider.requests[0].state["candidate_profiles"]["c0"])
        namespaces.append(adapter.cache_namespace)
    assert len(loaded[0]["interests"]) > len(loaded[1]["interests"])
    assert namespaces[0] != namespaces[1]
    assert "disliked_topics" not in loaded[0] and "disliked_topics" not in loaded[1]


@pytest.mark.parametrize("body_length", [10600, 10700, 10800, 10900])
def test_boundary_projection_is_independent_of_candidate_reference_width(body_length: int) -> None:
    profile = SoulProfile(
        preferences=PreferenceLayer(interests=[InterestTag("jazz", "music", 0.1)])
    )
    catalog = build_profile_catalog(profile)
    adapter = JevContentEvaluator(NativeProvider(), model=MODEL, config=JevEvaluationConfig())
    candidate = {"title": "jazz", "body_text": "x" * body_length}
    loaded = []
    for index in (0, 10, 100, 1000):
        row = adapter._candidate(index, candidate)
        assert row is not None
        selected = adapter._load_profile(catalog, {"profile": {}}, row, 1_000_000_000.0)
        assert selected is not None
        loaded.append(selected.profile_context)
    assert all(value == loaded[0] for value in loaded)


async def test_mandatory_profile_overflow_never_calls_provider(caplog: Any) -> None:
    profile = _mature_profile()
    profile.preferences.disliked_topics = ["不要删掉这个带有例外条件的限制。" * 3000]
    provider = NativeProvider()
    adapter = JevContentEvaluator(provider, model=MODEL, config=JevEvaluationConfig())
    result = await adapter.evaluate(
        profile_summary=build_profile_catalog(profile), content_items=[ITEM]
    )
    assert result == [None]
    assert not provider.requests
    assert "mandatory_profile_too_large" in caplog.text


async def test_jev_catalog_bypasses_legacy_tail_embedding_recall() -> None:
    class BombEmbedding:
        async def embed(self, text: str) -> list[float]:
            raise AssertionError("On-demand Jev loading must not call embedding or another LLM")

    adapter = RecordingEvaluator()
    engine = make_engine(adapter, embedding_service=BombEmbedding())
    assert await engine.evaluate_content_batch([item()], _mature_profile()) == [0.9]
    assert len(adapter.calls[0]["profile_summary"]["interests"]) == 300


async def test_profile_change_during_provider_call_cannot_poison_new_profile_cache() -> None:
    profile = _build_profile()

    class MutatingEvaluator(RecordingEvaluator):
        async def evaluate(self, **kwargs: Any) -> list[dict[str, Any] | None]:
            result = await super().evaluate(**kwargs)
            if len(self.calls) == 1:
                profile.preferences.disliked_topics.append("用户在请求途中新增的不喜欢")
            return result

    adapter = MutatingEvaluator()
    engine = make_engine(adapter)
    assert await engine.evaluate_content_batch([item()], profile) == [0.9]
    assert await engine.evaluate_content_batch([item()], profile) == [0.9]
    assert len(adapter.calls) == 2
    assert await engine.evaluate_content_batch([item()], profile) == [0.9]
    assert len(adapter.calls) == 2


async def test_far_tail_source_edit_invalidates_cache() -> None:
    profile = _mature_profile()
    adapter = RecordingEvaluator()
    engine = make_engine(adapter)
    await engine.evaluate_content_batch([item()], profile)
    profile.preferences.interests.append(InterestTag("新兴趣", "手工", 0.001))
    await engine.evaluate_content_batch([item()], profile)
    assert len(adapter.calls) == 2


def test_budget_identity_prevents_stale_cache_even_when_model_unchanged() -> None:
    provider = NativeProvider()
    default = JevContentEvaluator(provider, model=MODEL, config=JevEvaluationConfig())
    tightened = JevContentEvaluator(
        provider,
        model=MODEL,
        config=replace(JevEvaluationConfig(), max_state_question_bytes=12000),
    )
    assert default.cache_namespace != tightened.cache_namespace
    with pytest.raises(ValueError, match="selected model"):
        JevContentEvaluator(
            provider,
            model=MODEL,
            config=JevEvaluationConfig(),
            context_budget=resolve_jev_context(
                "different-model", {"different-model": JevContextLimits(64000, 32000)}
            ),
        )
