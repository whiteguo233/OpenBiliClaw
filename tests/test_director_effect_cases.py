"""Synthetic rubric integrity, not a claim about real recommendation quality."""

from __future__ import annotations

from scripts.director_effect_cases import build_cases, score_selection


def test_fixtures_are_deterministic_and_share_pool_and_profile():
    first, second = build_cases(), build_cases()
    assert first == second
    assert [case.case_id for case in first] == [
        "practical_agent",
        "local_style_feedback",
        "weak_click",
    ]
    assert all(len(case.request.cards) == 24 for case in first)
    assert all(case.request.target_count == 6 for case in first)
    assert all(case.request.cards == first[0].request.cards for case in first)
    assert all(
        case.request.session.profile_context == first[0].request.session.profile_context
        for case in first
    )
    assert all(not case.request.constraints.topic_quotas for case in first)


def test_labels_are_known_evaluator_only_and_no_portrait_enters_request():
    for case in build_cases():
        available = {card.ref for card in case.request.cards}
        assert all(set(members) <= available for members in case.labels.values())
        assert all(len(members) == len(set(members)) for members in case.labels.values())
        payload = case.request.model_dump_json()
        assert "task_support" not in payload
        assert "forbidden_style" not in payload
        assert "personality_portrait" not in payload
        assert all(card.source_platform == "synthetic" for card in case.request.cards)


def test_practical_rubric_rejects_definition_repetition_without_banning_ai():
    case = build_cases()[0]
    decoys = score_selection(case, ("c01", "c02", "c03", "c04", "c05", "c06"))
    assert decoys["valid_selection"]
    assert decoys["task_support_count"] == 0
    assert decoys["semantic_duplicate_excess"] == 3
    assert decoys["semantic_duplicate_pairs"] == 4
    practical = score_selection(case, ("c07", "c08", "c09", "c10", "c11", "c12"))
    assert practical["task_support_count"] == 6
    assert practical["semantic_duplicate_excess"] == 0
    assert practical["topical_coverage"] == 1


def test_style_rubric_is_local_to_hype_not_whole_ai_topic():
    case = build_cases()[1]
    assert case.request.session.explicit_intent == ""
    feedback = case.request.session.real_feedback[-1]
    assert feedback.kind == "dislike" and feedback.description == "这个标题太夸张"
    assert feedback.content[0].topic_group == "ai_news"
    assert case.request.session.profile_context["stable"]["disliked_topics"] == []
    assert score_selection(case, ("c06", "c21", "c22"))["forbidden_style_count"] == 3
    assert score_selection(case, ("c07", "c08", "c23"))["forbidden_style_count"] == 0
    assert score_selection(case, ("c07", "c08", "c23"))["task_support_count"] == 3
    assert score_selection(case, ("c15", "c16", "c17"))["task_support_count"] == 0


def test_weak_click_has_no_negative_or_mastery_evidence():
    case = build_cases()[2]
    assert [event.kind for event in case.request.session.real_feedback] == [
        "presented",
        "click",
    ]
    screen = case.request.session.recent_screens[0]
    assert [action.kind for action in screen.cards[0].actions] == ["click"]
    assert screen.cards[1].actions == ()
    assert case.request.session.latest_preference_revision == 0
    assert case.labels["forbidden_style"] == ()
    independent = score_selection(case, ("c07", "c08", "c09", "c15", "c16", "c17"))
    assert independent["independent_interest_coverage"] == 2
    bridge_only = score_selection(case, ("c20",))
    assert bridge_only["topical_coverage"] == 2
    assert bridge_only["independent_interest_coverage"] == 0
    assert bridge_only["supported_exploration_count"] == 1


def test_invalid_refs_cannot_inflate_metric_counts():
    case = build_cases()[0]
    score = score_selection(case, ("c07", "c07", "c08", "c09", "unknown", "unknown"))
    assert not score["valid_selection"]
    assert score["unknown_ref_count"] == 2
    assert score["repeated_ref_count"] == 2
    assert score["task_support_count"] == 3
    assert score["unique_known_count"] == 3


def test_scoring_does_not_mutate_preregistered_cases():
    case = build_cases()[0]
    before = repr(case)
    score_selection(case, ("c07", "c08", "c09", "c10", "c11", "c12"))
    assert repr(case) == before


def test_aggregate_reference_audit_accepts_expected_length_but_not_unknowns_or_repeats():
    case = build_cases()[0]
    refs = tuple(card.ref for card in case.request.cards[:18])
    assert not score_selection(case, refs)["valid_selection"]
    assert score_selection(case, refs, expected_count=18)["valid_selection"]
    repeated = refs[:-1] + (refs[0],)
    unknown = refs[:-1] + ("unknown",)
    assert not score_selection(case, repeated, expected_count=18)["valid_selection"]
    assert not score_selection(case, unknown, expected_count=18)["valid_selection"]
