"""Offline pair-composition inputs are bounded, immutable semantic snapshots."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.recommendation.director.models import (
    CompositionCard,
    CompositionRequest,
    CompositionSession,
    PairAlternative,
    PairTrace,
    RealFeedback,
    ScreenConstraints,
    ScreenProposal,
    TopicQuota,
    canonical_json,
    digest,
)


def _card(index: int = 1, **updates: Any) -> CompositionCard:
    fields: dict[str, Any] = {
        "ref": f"card_{index}",
        "item_key": f"bilibili:BV{index}",
        "title": f"候选内容 {index}",
        "summary": "仅作数据的候选摘要",
        "topic_group": "technology",
        "content_style": "tutorial",
        "source_platform": "bilibili",
        "baseline_score": 0.5,
    }
    fields.update(updates)
    return CompositionCard(**fields)


def _feedback(index: int = 1, **updates: Any) -> RealFeedback:
    fields: dict[str, Any] = {
        "event_ref": f"fixture-event-{index}",
        "batch_ref": "fixture-presented-batch",
        "kind": "like",
        "description": "Synthetic fixture for an actual-feedback input contract",
    }
    fields.update(updates)
    return RealFeedback(**fields)


def _session(**updates: Any) -> CompositionSession:
    fields: dict[str, Any] = {
        "session_ref": "session-1",
        "stream_ref": "stream-1",
        "state_revision": 3,
        "feedback_cursor": "cursor-1",
        "configuration_revision": 2,
        "explicit_intent": "想看有启发的技术内容",
        "real_feedback": (_feedback(),),
    }
    fields.update(updates)
    return CompositionSession(**fields)


def _constraints(**updates: Any) -> ScreenConstraints:
    fields: dict[str, Any] = {
        "version": "constraints-v1",
        "topic_quotas": (TopicQuota(topic_group="technology", minimum=1, maximum=2),),
        "excluded_item_keys": ("bilibili:excluded",),
    }
    fields.update(updates)
    return ScreenConstraints(**fields)


def _request(**updates: Any) -> CompositionRequest:
    fields: dict[str, Any] = {
        "request_id": "request-1",
        "session": _session(),
        "candidate_set_ref": "shortlist-1",
        "candidate_revision": 4,
        "feature_schema_version": "features-v1",
        "direction_ref": "session-direction-1",
        "direction": "先看技术实用内容，再看跨主题探索",
        "cards": (_card(1), _card(2)),
        "constraints": _constraints(),
        "target_count": 2,
    }
    fields.update(updates)
    return CompositionRequest(**fields)


@pytest.mark.parametrize(
    "field", ["state_revision", "configuration_revision", "candidate_revision"]
)
def test_revisions_are_bounded_before_json_serialization(field: str) -> None:
    fields = _request().model_dump(mode="python")
    target = fields if field == "candidate_revision" else fields["session"]
    target[field] = 10**5000
    with pytest.raises(ValidationError, match="less than or equal"):
        CompositionRequest.model_validate(fields)
    target[field] = 2**63 - 1
    assert CompositionRequest.model_validate(fields).input_digest


def _trace(**updates: Any) -> PairTrace:
    fields: dict[str, Any] = {
        "pair_index": 0,
        "prefix_refs": (),
        "alternatives": (PairAlternative(option_id="pair_1", card_refs=("card_1", "card_2")),),
        "selected_refs": ("card_1", "card_2"),
        "origin": "deterministic",
        "reason_code": "provider_disabled",
    }
    fields.update(updates)
    return PairTrace(**fields)


def _proposal(**updates: Any) -> ScreenProposal:
    fields: dict[str, Any] = {
        "request_id": "request-1",
        "input_digest": _request().input_digest,
        "candidate_digest": digest(["card_1", "card_2"]),
        "constraints_digest": digest({"version": "constraints-v1"}),
        "status": "COMPLETE",
        "realized_strategy": "deterministic",
        "target_count": 2,
        "card_refs": ("card_1", "card_2"),
        "pairs": (_trace(),),
        "provider_calls": 0,
        "reason_code": "complete",
    }
    fields.update(updates)
    return ScreenProposal(**fields)


def test_request_round_trip_preserves_immutable_nested_contracts() -> None:
    request = _request()
    restored = CompositionRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    assert isinstance(restored.cards, tuple)
    assert isinstance(restored.cards[0], CompositionCard)
    assert isinstance(restored.constraints.topic_quotas, tuple)
    assert isinstance(restored.constraints.excluded_item_keys, tuple)
    assert isinstance(restored.session.real_feedback, tuple)
    assert restored.input_digest == request.input_digest


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        ((), "request_id", "changed"),
        (("cards", 0), "title", "changed"),
        (("session",), "state_revision", 100),
        (("session", "real_feedback", 0), "description", "changed"),
        (("constraints",), "version", "changed"),
        (("constraints", "topic_quotas", 0), "minimum", 0),
    ],
)
def test_request_and_nested_models_cannot_be_assigned(
    path: tuple[str | int, ...], field: str, value: object
) -> None:
    request = _request()
    original_digest = request.input_digest
    target: Any = request
    for component in path:
        target = target[component] if isinstance(component, int) else getattr(target, component)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        setattr(target, field, value)
    assert request.input_digest == original_digest


def test_request_detaches_nested_input_dicts() -> None:
    fields = _request().model_dump(mode="python")
    request = CompositionRequest.model_validate(fields)
    original_digest = request.input_digest
    fields["cards"][0]["title"] = "changed outside the snapshot"
    fields["session"]["real_feedback"][0]["description"] = "changed"
    fields["constraints"]["topic_quotas"][0]["minimum"] = 0
    assert request.cards[0].title == "候选内容 1"
    assert request.constraints.topic_quotas[0].minimum == 1
    assert request.input_digest == original_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ref", ""),
        ("ref", "has space"),
        ("ref", "含中文"),
        ("ref", "a" * 65),
        ("item_key", ""),
        ("item_key", " \t\n"),
        ("item_key", "a" * 257),
        ("title", ""),
        ("title", "a" * 201),
        ("title", 5),
        ("summary", "a" * 401),
        ("topic_group", ""),
        ("topic_group", " \n"),
        ("topic_group", "a" * 81),
        ("content_style", "a" * 81),
        ("source_platform", "a" * 33),
        ("baseline_score", "0.5"),
        ("baseline_score", True),
        ("baseline_score", 1_000_001.0),
        ("baseline_score", -1_000_001.0),
        ("eligible", 1),
        ("eligible", "true"),
    ],
)
def test_cards_reject_malformed_or_coerced_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _card(**{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_all_float_inputs_reject_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        _card(baseline_score=value)
    with pytest.raises(ValidationError):
        _request(decision_budget_seconds=value)
    with pytest.raises(ValidationError):
        _request(deadline_monotonic=value)
    with pytest.raises(ValueError):
        canonical_json({"nested": [value]})
    with pytest.raises(ValueError):
        digest({"nested": [value]})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "pair-composition.v2"),
        ("request_id", ""),
        ("candidate_revision", -1),
        ("candidate_revision", True),
        ("candidate_revision", "4"),
        ("direction", ""),
        ("direction", "a" * 501),
        ("target_count", 0),
        ("target_count", 21),
        ("target_count", True),
        ("target_count", 2.0),
        ("target_count", "2"),
        ("decision_budget_seconds", 0.0),
        ("decision_budget_seconds", 60.1),
        ("decision_budget_seconds", True),
        ("max_provider_calls", -1),
        ("max_provider_calls", 11),
        ("max_provider_calls", True),
        ("deadline_monotonic", -0.1),
        ("deadline_monotonic", True),
        ("cards", []),
    ],
)
def test_requests_reject_malformed_bounds_and_implicit_coercion(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _request(**{field: value})


@pytest.mark.parametrize("target_count", [1, 3, 19, 20])
def test_odd_screens_and_shortage_are_valid_input_not_silently_truncated(
    target_count: int,
) -> None:
    request = _request(target_count=target_count, cards=(), max_provider_calls=0)
    assert request.target_count == target_count
    assert request.cards == ()
    assert request.max_provider_calls == 0


def test_request_caps_full_shortlist_instead_of_silently_truncating() -> None:
    assert len(_request(cards=tuple(_card(i) for i in range(40))).cards) == 40
    with pytest.raises(ValidationError):
        _request(cards=tuple(_card(i) for i in range(41)))


def test_request_rejects_duplicate_refs_and_canonical_keys_independently() -> None:
    with pytest.raises(ValidationError, match="duplicate card ref"):
        _request(cards=(_card(1), _card(2, ref="card_1")))
    with pytest.raises(ValidationError, match="duplicate canonical item key"):
        _request(cards=(_card(1), _card(2, item_key="bilibili:BV1")))
    # A bare platform ID cannot globally identify a card. Canonical keys can.
    request = _request(cards=(_card(1, item_key="bilibili:123"), _card(2, item_key="xhs:123")))
    assert len(request.cards) == 2


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(2, 1), (-1, 2), (0, -1), (21, 21), (0, 21), (True, 2), (0, True), ("1", 2)],
)
def test_topic_quotas_reject_invalid_order_bounds_or_coercion(
    minimum: object, maximum: object
) -> None:
    with pytest.raises(ValidationError):
        TopicQuota(topic_group="technology", minimum=minimum, maximum=maximum)  # type: ignore[arg-type]


def test_constraints_reject_duplicate_topics_and_exclusions() -> None:
    with pytest.raises(ValidationError, match="duplicate topic quota"):
        _constraints(
            topic_quotas=(
                TopicQuota(topic_group="technology"),
                TopicQuota(topic_group="technology"),
            )
        )
    with pytest.raises(ValidationError, match="duplicate excluded item key"):
        _constraints(excluded_item_keys=("bilibili:1", "bilibili:1"))


def test_constraints_collections_have_explicit_capacity_limits() -> None:
    quotas = tuple(TopicQuota(topic_group=f"topic-{i}") for i in range(41))
    exclusions = tuple(f"bilibili:{i}" for i in range(201))
    assert len(_constraints(topic_quotas=quotas[:40]).topic_quotas) == 40
    assert len(_constraints(excluded_item_keys=exclusions[:200]).excluded_item_keys) == 200
    with pytest.raises(ValidationError):
        _constraints(topic_quotas=quotas)
    with pytest.raises(ValidationError):
        _constraints(excluded_item_keys=exclusions)
    with pytest.raises(ValidationError):
        _constraints(topic_quotas=[])
    with pytest.raises(ValidationError):
        _constraints(excluded_item_keys=[])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state_revision", -1),
        ("state_revision", True),
        ("configuration_revision", -1),
        ("configuration_revision", "1"),
        ("explicit_intent", "a" * 501),
        ("real_feedback", []),
    ],
)
def test_session_rejects_invalid_state_or_mutable_feedback_sequence(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _session(**{field: value})


def test_feedback_requires_attribution_and_unique_bounded_events() -> None:
    assert len(_session(real_feedback=tuple(_feedback(i) for i in range(8))).real_feedback) == 8
    with pytest.raises(ValidationError):
        _session(real_feedback=tuple(_feedback(i) for i in range(9)))
    with pytest.raises(ValidationError, match="duplicate feedback event"):
        _session(real_feedback=(_feedback(), _feedback(batch_ref="different-batch")))
    with pytest.raises(ValidationError):
        _feedback(batch_ref="")
    with pytest.raises(ValidationError):
        _feedback(event_ref="")
    with pytest.raises(ValidationError):
        _feedback(kind="tentative_pair_selected")
    with pytest.raises(ValidationError):
        _feedback(description="a" * 257)


def test_validation_errors_do_not_echo_untrusted_input_values() -> None:
    secret = "PRIVATE-CANDIDATE-CONTENT-DO-NOT-LOG"
    fields = _request().model_dump(mode="python")
    fields["cards"][0]["title"] = secret * 20
    with pytest.raises(ValidationError) as captured:
        CompositionRequest.model_validate(fields)
    assert secret not in str(captured.value)
    assert "input_value" not in str(captured.value)


def test_semantic_digest_ignores_identity_deadline_and_json_object_order() -> None:
    request = _request()
    fields = request.model_dump(mode="json")
    fields["request_id"] = "retry-request-2"
    fields["deadline_monotonic"] = 0.0
    fields = dict(reversed(list(fields.items())))
    fields["session"] = dict(reversed(list(fields["session"].items())))
    fields["cards"][0] = dict(reversed(list(fields["cards"][0].items())))
    changed = CompositionRequest.model_validate_json(json.dumps(fields))
    assert changed.input_digest == request.input_digest
    assert len(changed.input_digest) == 64
    assert changed.input_digest == changed.input_digest.lower()
    assert int(changed.input_digest, 16) >= 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_set_ref", "shortlist-2"),
        ("candidate_revision", 5),
        ("feature_schema_version", "features-v2"),
        ("direction_ref", "session-direction-2"),
        ("direction", "看轻松的生活内容"),
        ("target_count", 3),
        ("decision_budget_seconds", 3.0),
        ("max_provider_calls", 0),
    ],
)
def test_digest_binds_request_semantics_and_policy_budgets(field: str, value: object) -> None:
    assert _request(**{field: value}).input_digest != _request().input_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_ref", "session-2"),
        ("stream_ref", "stream-2"),
        ("state_revision", 4),
        ("configuration_revision", 3),
        ("feedback_cursor", "cursor-2"),
        ("explicit_intent", "想看电影"),
        ("real_feedback", ()),
    ],
)
def test_digest_binds_every_authoritative_session_field(field: str, value: object) -> None:
    assert _request(session=_session(**{field: value})).input_digest != _request().input_digest


def test_digest_binds_candidate_text_eligibility_order_and_constraints() -> None:
    original = _request()
    changes = (
        _request(cards=tuple(reversed(original.cards))),
        _request(cards=(_card(1, title="Changed title"), _card(2))),
        _request(cards=(_card(1, summary="Changed summary"), _card(2))),
        _request(cards=(_card(1, eligible=False), _card(2))),
        _request(cards=(_card(1, baseline_score=0.6), _card(2))),
        _request(constraints=_constraints(version="constraints-v2")),
        _request(constraints=_constraints(excluded_item_keys=())),
        _request(
            constraints=_constraints(
                topic_quotas=(TopicQuota(topic_group="technology", minimum=0, maximum=2),)
            )
        ),
    )
    for changed in changes:
        assert changed.input_digest != original.input_digest


def test_digest_preserves_feedback_event_order_and_description() -> None:
    request = _request(session=_session(real_feedback=(_feedback(1), _feedback(2))))
    reordered = _request(session=_session(real_feedback=(_feedback(2), _feedback(1))))
    changed_text = _request(
        session=_session(real_feedback=(_feedback(1, description="different"), _feedback(2)))
    )
    assert request.input_digest != reordered.input_digest
    assert request.input_digest != changed_text.input_digest


def test_canonical_json_is_compact_unicode_sorted_and_order_sensitive_for_arrays() -> None:
    assert canonical_json({"z": [2, 1], "a": "中文"}) == '{"a":"中文","z":[2,1]}'
    assert digest({"z": 1, "a": 2}) == digest({"a": 2, "z": 1})
    assert digest([1, 2]) != digest([2, 1])


@pytest.mark.parametrize("refs", [(), ("a", "a"), ("a", "b", "c"), ["a", "b"]])
def test_pair_alternatives_reject_empty_duplicate_oversized_or_mutable_refs(refs: object) -> None:
    with pytest.raises(ValidationError):
        PairAlternative(option_id="pair-1", card_refs=refs)  # type: ignore[arg-type]


def test_pair_alternative_accepts_pair_and_explicit_odd_tail_singleton() -> None:
    assert PairAlternative(option_id="pair-1", card_refs=("a", "b")).card_refs == ("a", "b")
    assert PairAlternative(option_id="tail-1", card_refs=("a",)).card_refs == ("a",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pair_index", -1),
        ("pair_index", True),
        ("prefix_refs", ("previous", "previous")),
        ("prefix_refs", ("card_1",)),
        ("selected_refs", ("other_1", "other_2")),
        ("selected_refs", ("card_2", "card_1")),
        ("selected_refs", ("card_1",)),
        ("origin", "decision"),
        ("decision_request_id", "incomplete-attribution"),
        ("decision_input_digest", "incomplete-attribution"),
        ("evidence_json", '{"status":"INVALID"}'),
        ("alternatives", ()),
        ("alternatives", []),
        (
            "alternatives",
            (PairAlternative(option_id="none_suitable", card_refs=("card_1", "card_2")),),
        ),
        (
            "alternatives",
            (
                PairAlternative(option_id="pair_1", card_refs=("card_1", "card_2")),
                PairAlternative(option_id="pair_1", card_refs=("card_3", "card_4")),
            ),
        ),
    ],
)
def test_pair_trace_rejects_inconsistent_selection_or_attribution(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _trace(**{field: value})


def test_pair_trace_rejects_overlap_in_even_an_unselected_alternative() -> None:
    with pytest.raises(ValidationError, match="alternative overlaps prefix"):
        _trace(
            prefix_refs=("card_3", "card_4"),
            alternatives=(
                PairAlternative(option_id="pair_1", card_refs=("card_1", "card_2")),
                PairAlternative(option_id="pair_2", card_refs=("card_3", "card_5")),
            ),
        )


def test_pair_trace_caps_alternatives() -> None:
    alternatives = tuple(
        PairAlternative(option_id=f"pair_{i}", card_refs=(f"card_{2 * i + 1}", f"card_{2 * i + 2}"))
        for i in range(17)
    )
    assert len(_trace(alternatives=alternatives[:16]).alternatives) == 16
    with pytest.raises(ValidationError):
        _trace(alternatives=alternatives)


def test_decision_trace_requires_complete_attribution_but_fallback_can_retain_failed_evidence() -> (
    None
):
    attribution = {
        "decision_request_id": "request-pair-1",
        "decision_input_digest": digest({"pair": 1}),
        "evidence_json": '{"status":"OK"}',
    }
    trace = _trace(origin="decision", **attribution)
    assert trace.decision_request_id == "request-pair-1"
    fallback = _trace(**{**attribution, "evidence_json": '{"status":"UNAVAILABLE"}'})
    assert fallback.origin == "deterministic"
    assert fallback.evidence_json == '{"status":"UNAVAILABLE"}'


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("card_refs", ()),
        ("card_refs", ("card_2", "card_1")),
        ("pairs", ()),
        ("target_count", 3),
        ("status", "SHORTAGE"),
        ("status", "UNSATISFIABLE"),
        ("realized_strategy", "mixed"),
        ("realized_strategy", "none"),
        ("realized_strategy", "jev_recurrent"),
        ("requested_strategy", "agent_only"),
        ("provider_calls", -1),
        ("provider_calls", 11),
        ("provider_calls", True),
    ],
)
def test_proposal_rejects_inconsistent_status_refs_or_strategy(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _proposal(**{field: value})


@pytest.mark.parametrize(
    "trace",
    [_trace(pair_index=1), _trace(prefix_refs=("previous_1", "previous_2"))],
)
def test_proposal_rejects_discontinuous_trace_index_or_prefix(trace: PairTrace) -> None:
    with pytest.raises(ValidationError, match="pair trace order or prefix mismatch"):
        _proposal(pairs=(trace,))


def test_proposal_permits_singleton_only_as_last_pair() -> None:
    singleton = _trace(
        alternatives=(PairAlternative(option_id="tail", card_refs=("card_1",)),),
        selected_refs=("card_1",),
    )
    after_singleton = _trace(
        pair_index=1,
        prefix_refs=("card_1",),
        alternatives=(PairAlternative(option_id="pair_2", card_refs=("card_2", "card_3")),),
        selected_refs=("card_2", "card_3"),
    )
    with pytest.raises(ValidationError, match="singleton is only allowed at the tail"):
        _proposal(
            target_count=3,
            card_refs=("card_1", "card_2", "card_3"),
            pairs=(singleton, after_singleton),
        )
    tail = _trace(
        pair_index=1,
        prefix_refs=("card_1", "card_2"),
        alternatives=(PairAlternative(option_id="tail", card_refs=("card_3",)),),
        selected_refs=("card_3",),
    )
    proposal = _proposal(
        target_count=3,
        card_refs=("card_1", "card_2", "card_3"),
        pairs=(_trace(), tail),
    )
    assert proposal.card_refs == ("card_1", "card_2", "card_3")


def test_proposal_requires_provider_call_for_decision_origin() -> None:
    trace = _trace(
        origin="decision",
        decision_request_id="request-pair-1",
        decision_input_digest=digest({"pair": 1}),
        evidence_json='{"status":"OK"}',
    )
    with pytest.raises(ValidationError, match="decision selections exceed provider calls"):
        _proposal(pairs=(trace,), realized_strategy="jev_recurrent")
    assert _proposal(pairs=(trace,), realized_strategy="jev_recurrent", provider_calls=1)


def test_empty_and_short_proposals_keep_explicit_status() -> None:
    assert _proposal(status="SHORTAGE", target_count=3).card_refs == ("card_1", "card_2")
    empty = _proposal(status="UNSATISFIABLE", card_refs=(), pairs=(), realized_strategy="none")
    assert empty.card_refs == ()
    assert empty.provider_calls == 0
    with pytest.raises(ValidationError, match="unsatisfiable proposal cannot contain"):
        _proposal(
            status="UNSATISFIABLE",
            card_refs=(),
            pairs=(),
            realized_strategy="none",
            provider_calls=1,
        )


def test_proposal_is_tentative_immutable_serializable_and_not_a_commit_contract() -> None:
    proposal = _proposal()
    assert ScreenProposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert proposal.tentative is True
    assert proposal.requested_strategy == "jev_recurrent"
    assert isinstance(proposal.pairs, tuple)
    assert isinstance(proposal.pairs[0].alternatives, tuple)
    assert not {"plan_id", "batch_id", "committed_at", "presented_at", "lease"}.intersection(
        ScreenProposal.model_fields
    )
    assert not {"plan_id", "batch_id", "lease"}.intersection(CompositionRequest.model_fields)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        proposal.pairs[0].alternatives[0].option_id = "changed"
    with pytest.raises(ValidationError):
        _proposal(tentative=False)


@pytest.mark.parametrize("field", ["plan_id", "batch_id", "committed_at", "presented_at"])
def test_requests_and_proposals_reject_unowned_plan_or_commit_authority(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _request(**{field: "forged"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _proposal(**{field: "forged"})


@pytest.mark.parametrize("factory", [_card, _feedback, _session, _constraints])
def test_nested_models_reject_unknown_fields(factory: Any) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        factory(unexpected="ignored metadata would hide a schema mismatch")
