"""Repair packs legal alternatives against both model limits, without I/O."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from openbiliclaw.decision.context_budget import resolve_jev_context
from openbiliclaw.recommendation.director.models import TopicQuota
from openbiliclaw.recommendation.director.proposer import validate_screen
from openbiliclaw.recommendation.director.repair import (
    DraftRepairer,
    _Budget,
    _decision,
    _fit_decision_profile,
    _repair_options,
    _screen_request,
    _with_decision_profile,
)
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.soul.profile_views import (
    build_profile_catalog,
    resize_profile_context,
    select_profile_context,
)
from tests.test_director_horizon_modes import _evidence, _horizon, _Provider, _request


@pytest.mark.parametrize("edit", ["replace_pair", "replace_left", "replace_right"])
@pytest.mark.parametrize("binding_limit", ["single_question", "whole_request"])
async def test_repair_packs_keep_and_audited_edit_without_losing_context(
    edit, binding_limit
) -> None:
    request = _request(count=20, target=4)
    request = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={
                    "fast_profile_context": {
                        "disliked_topics": ["未看完作品不要剧透；明确看完则例外"],
                        "interests": [{"name": "工具", "weight": 0.9}],
                    }
                }
            ),
            "constraints": request.constraints.model_copy(
                update={"topic_quotas": (TopicQuota(topic_group="tools", minimum=2, maximum=3),)}
            ),
        }
    )
    plan = _horizon(request, screens=1)
    screen = plan.screens[0]
    local = _screen_request(request, screen, set())
    original = screen.pairs[0].card_refs
    alternatives = _repair_options(local, (), original, request.target_count)
    if edit in {"replace_left", "replace_right"}:
        alternatives = tuple(
            option
            for option in alternatives
            if option.option_id == "keep" or option.option_id.startswith(edit)
        )
    replacement = next(option for option in alternatives if option.option_id.startswith(edit))
    smallest = tuple(
        option for option in alternatives if option.option_id in {"keep", replacement.option_id}
    )
    timing = _Budget(time.monotonic() + 60, 10)
    full = _decision(local, plan, screen, 0, (), (), original, timing, alternatives)
    lean = _decision(local, plan, screen, 0, (), (), original, timing, smallest)
    audit = _decision(local, plan, screen, 0, (), (), original, timing)
    model = resolve_jev_context("jev-1.13.0")
    limit = (
        max(
            model.measure(lean.state, lean.questions).request_bytes,
            model.measure(audit.state, audit.questions).request_bytes,
        )
        + 50
    )
    budget = replace(
        model,
        max_request_bytes=limit if binding_limit == "whole_request" else 48_000,
        max_state_question_bytes=limit if binding_limit == "single_question" else 24_000,
    )
    assert budget.fits_request(audit)
    assert budget.fits_request(lean)
    assert not budget.fits_request(full)

    def choose(sent):
        if sent.purpose == "AUDIT_DRAFT":
            return _evidence(sent, edit if sent.request_id.endswith(":0") else "keep")
        return _evidence(
            sent,
            next(key for key in sent.questions["decision"].criteria if key.startswith(edit)),
        )

    provider = _Provider(choose)
    repaired = await DraftRepairer(provider, context_budget=budget).repair(request, plan)
    sent = next(item for item in provider.requests if item.purpose == "REPAIR_PAIR")
    assert all(budget.fits_request(item) for item in provider.requests)
    assert sent.state["session_context"] == request.session.prompt_context(fast=True)
    assert sent.state["tentative_prefix"] == []
    assert sent.state["known_pair"] == list(original)
    assert sent.state["screen_policy"] == full.state["screen_policy"]
    kept_options = sent.questions["decision"].criteria
    assert "keep" in kept_options
    assert any(key.startswith(edit + "_") for key in kept_options)
    assert len(kept_options) - 1 < len(alternatives)  # none_suitable is not an alternative
    assert repaired.traces[0][0].origin == "decision"
    assert repaired.traces[0][0].reason_code == "jev_local_repair"
    assert validate_screen(local, repaired.horizon.screens[0].card_refs, request.target_count)


async def test_no_useful_repair_fits_keeps_legal_draft_with_size_reason() -> None:
    request = _request(count=20, target=4)
    plan = _horizon(request, screens=1)
    local = _screen_request(request, plan.screens[0], set())
    original = plan.screens[0].pairs[0].card_refs
    timing = _Budget(time.monotonic() + 60, 10)
    audit = _decision(local, plan, plan.screens[0], 0, (), (), original, timing)
    model = resolve_jev_context("jev-1.13.0")
    audit_bytes = model.measure(audit.state, audit.questions).request_bytes
    budget = replace(model, max_request_bytes=audit_bytes, max_state_question_bytes=audit_bytes)
    provider = _Provider(lambda sent: _evidence(sent, "replace_pair"))
    result = await DraftRepairer(provider, context_budget=budget).repair(request, plan)
    assert all(item.purpose == "AUDIT_DRAFT" for item in provider.requests)
    assert result.horizon.screens[0].card_refs == plan.screens[0].card_refs
    assert result.traces[0][0].reason_code == "decision_input_too_large"
    assert result.replan_required


async def test_small_repair_skips_oversize_first_replacement_but_keeps_audit_kind() -> None:
    request = _request(count=20, target=4)
    # The first REPLACE_RIGHT candidate has a much larger summary than later
    # equally legal replacements. Packing must not stop at that first option.
    request = request.model_copy(
        update={
            "cards": tuple(
                card.model_copy(update={"summary": "中" * 800}) if card.ref == "c2" else card
                for card in request.cards
            )
        }
    )
    plan = _horizon(request, screens=1)
    screen = plan.screens[0]
    local = _screen_request(request, screen, set())
    original = screen.pairs[0].card_refs
    options = tuple(
        option
        for option in _repair_options(local, (), original, 4)
        if option.option_id == "keep" or option.option_id.startswith("replace_right")
    )
    assert len(options) > 2
    # c2 is already in the future draft, so its text remains necessary even
    # after omitting that option. Use a fresh outside-draft heavy alternative.
    heavy_ref = next(ref for ref in options[-1].card_refs if ref not in original)
    changed_cards = tuple(
        card.model_copy(update={"summary": "中" * 800}) if card.ref == heavy_ref else card
        for card in request.cards
    )
    local = local.model_copy(update={"cards": changed_cards})
    # Exercise the pure packer directly to control deterministic option order.
    from openbiliclaw.recommendation.director.repair import _pack_repair_decision

    reordered = (options[0], options[-1], *options[1:-1])
    timing = _Budget(time.monotonic() + 60, 10)

    def build(selected):
        return _decision(local, plan, screen, 0, (), (), original, timing, selected)

    model = resolve_jev_context("jev-1.13.0")
    small = build((reordered[0], reordered[2]))
    cap = model.measure(small.state, small.questions).request_bytes + 30
    budget = replace(model, max_request_bytes=cap, max_state_question_bytes=cap)
    assert not budget.fits_request(build(reordered[:2]))
    packed, decision = _pack_repair_decision(reordered, "replace_right", build, budget)
    assert budget.fits_request(decision)
    assert any(option.option_id == "keep" for option in packed)
    assert any(option.option_id.startswith("replace_right") for option in packed)
    assert reordered[1] not in packed


def _loaded_profile() -> dict[str, object]:
    profile = SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(f"工具体验{i}", "tools", 0.9) for i in range(200)],
            disliked_topics=["未看完不要剧透；明确看完除外"],
        )
    )
    return select_profile_context(
        build_profile_catalog(profile),
        query_texts=["工具体验"],
        max_bytes=8_000,
        purpose="director_pair",
    ).payload


async def test_long_horizon_audit_reloads_optional_profile_under_actual_envelope() -> None:
    loaded = _loaded_profile()
    request = _request(count=24, target=4)
    request = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={"fast_profile_context": loaded, "profile_source_digest": "a" * 64}
            ),
            "cards": tuple(
                card.model_copy(update={"summary": "正文资料" * 50}) for card in request.cards
            ),
        }
    )
    snapshot = request.model_dump_json()
    plan = _horizon(request, screens=3)
    screen = plan.screens[2]
    prior = tuple(ref for previous in plan.screens[:2] for ref in previous.card_refs)
    local = _screen_request(request, screen, set(prior))
    timing = _Budget(time.monotonic() + 60, 10)
    original = _decision(local, plan, screen, 0, (), prior, screen.pairs[0].card_refs, timing)
    floor = resize_profile_context(loaded, max_bytes=0)
    minimal = _with_decision_profile(original, floor.payload)
    model = resolve_jev_context("jev-1.13.0")
    cap = model.measure(minimal.state, minimal.questions).request_bytes + 80
    budget = replace(model, max_request_bytes=cap * 2, max_state_question_bytes=cap)
    assert budget.fits_request(minimal)
    assert not budget.fits_request(original)

    provider = _Provider()  # Always confirms KEEP for each audited pair.
    result = await DraftRepairer(provider, context_budget=budget).repair(request, plan)
    audits = [sent for sent in provider.requests if sent.state["screen_id"] == screen.screen_id]
    assert len(audits) == 2
    assert all(budget.fits_request(sent) for sent in provider.requests)
    first = audits[0]
    selected = first.state["session_context"]["profile_context"]
    assert selected["disliked_topics"] == loaded["disliked_topics"]
    assert len(selected["interests"]) < len(loaded["interests"])
    # All non-profile evidence, horizon/prefix, instructions and source binding
    # are identical; only the actual native request digest follows the new view.
    assert _with_decision_profile(first, loaded).input_digest == original.input_digest
    assert first.input_digest != original.input_digest
    assert first.state["previous_tentative_screens"] == list(prior)
    assert first.state["composition_input_digest"] == original.state["composition_input_digest"]
    assert result.horizon.screens == plan.screens
    assert result.provider_calls == 6
    assert request.model_dump_json() == snapshot


async def test_repair_combines_option_packing_and_optional_profile_resize() -> None:
    loaded = _loaded_profile()
    request = _request(count=20, target=4)
    request = request.model_copy(
        update={"session": request.session.model_copy(update={"fast_profile_context": loaded})}
    )
    plan = _horizon(request, screens=1)
    screen = plan.screens[0]
    local = _screen_request(request, screen, set())
    original = screen.pairs[0].card_refs
    timing = _Budget(time.monotonic() + 60, 10)
    options = _repair_options(local, (), original, 4)
    replacement = next(option for option in options if option.option_id.startswith("replace_pair"))
    required = tuple(
        option for option in options if option.option_id in {"keep", replacement.option_id}
    )
    full = _decision(local, plan, screen, 0, (), (), original, timing, options)
    minimal_options = _decision(local, plan, screen, 0, (), (), original, timing, required)
    floor = resize_profile_context(loaded, max_bytes=0)
    floor_options = _with_decision_profile(minimal_options, floor.payload)
    floor_audit = _with_decision_profile(
        _decision(local, plan, screen, 0, (), (), original, timing), floor.payload
    )
    model = resolve_jev_context("jev-1.13.0")
    cap = (
        max(
            model.measure(floor_options.state, floor_options.questions).request_bytes,
            model.measure(floor_audit.state, floor_audit.questions).request_bytes,
        )
        + 80
    )
    budget = replace(model, max_request_bytes=cap * 2, max_state_question_bytes=cap)
    assert budget.fits_request(floor_options)
    assert not budget.fits_request(minimal_options)
    assert not budget.fits_request(_with_decision_profile(full, floor.payload))

    def choose(sent):
        if sent.purpose == "AUDIT_DRAFT":
            return _evidence(sent, "replace_pair" if sent.request_id.endswith(":0") else "keep")
        return _evidence(
            sent,
            next(
                key for key in sent.questions["decision"].criteria if key.startswith("replace_pair")
            ),
        )

    provider = _Provider(choose)
    result = await DraftRepairer(provider, context_budget=budget).repair(request, plan)
    repair = next(sent for sent in provider.requests if sent.purpose == "REPAIR_PAIR")
    assert all(budget.fits_request(sent) for sent in provider.requests)
    assert "keep" in repair.questions["decision"].criteria
    assert any(key.startswith("replace_pair") for key in repair.questions["decision"].criteria)
    assert (
        repair.state["session_context"]["profile_context"]["disliked_topics"]
        == loaded["disliked_topics"]
    )
    assert result.traces[0][0].reason_code == "jev_local_repair"
    assert validate_screen(local, result.horizon.screens[0].card_refs, 4)


def test_legacy_profile_shape_is_not_automatically_trimmed() -> None:
    request = _request(count=12, target=4)
    request = request.model_copy(
        update={
            "session": request.session.model_copy(
                update={"fast_profile_context": {"stable": {"constraints": "重要条件" * 10_000}}}
            )
        }
    )
    plan = _horizon(request, screens=1)
    original = _decision(
        request,
        plan,
        plan.screens[0],
        0,
        (),
        (),
        plan.screens[0].pairs[0].card_refs,
        _Budget(time.monotonic() + 60, 10),
    )
    assert _fit_decision_profile(original, resolve_jev_context("jev-1.13.0")) is original


async def test_profile_packing_does_not_restart_an_expired_absolute_deadline(monkeypatch) -> None:
    from openbiliclaw.recommendation.director import repair as repair_module

    request = _request(count=12, target=4)
    plan = _horizon(request, screens=1)
    timing = _Budget(10.0, 10, context_budget=resolve_jev_context("jev-1.13.0"))
    decision = _decision(
        request, plan, plan.screens[0], 0, (), (), plan.screens[0].pairs[0].card_refs, timing
    )
    clock = [1.0]
    monkeypatch.setattr(repair_module.time, "monotonic", lambda: clock[0])

    def slow_local_packing(original, _budget):
        clock[0] = 11.0
        return original

    monkeypatch.setattr(repair_module, "_fit_decision_profile", slow_local_packing)
    provider = _Provider()
    choice = await repair_module._evaluate(provider, decision, timing)
    assert choice.reason == "screen_deadline_exhausted"
    assert provider.requests == []
    assert timing.calls == 0
