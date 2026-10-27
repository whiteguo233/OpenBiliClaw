"""Decision contracts reject malformed evidence before policy can consume it."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from openbiliclaw.decision.models import (
    ChoiceQuestion,
    DecisionRequest,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    ScoreQuestion,
    parse_jev_response,
)


def _request(**updates: Any) -> DecisionRequest:
    fields: dict[str, Any] = {
        "request_id": "request-1",
        "purpose": "CHOOSE_PAIR",
        "question_schema_version": "pair-v1",
        "state": {"prefix": [], "intent": "实用内容"},
        "questions": {
            "pair": ChoiceQuestion(
                instructions="Choose a pair", criteria={"ab": "A B", "cd": "C D"}
            ),
            "fit": ScoreQuestion(instructions="Rate fit", criteria=("low", "medium", "high")),
            "safe": NoulQuestion(instructions="Is this allowed?"),
        },
    }
    fields.update(updates)
    return DecisionRequest(**fields)


def _payload() -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "pair": {
                "type": "choice",
                "choice": "ab",
                "probabilities": {"ab": 0.8, "cd": 0.2},
                "confidence": 0.5,
            },
            "fit": {
                "type": "score",
                "score": 1.5,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
                "confidence": 0.2,
            },
            "safe": {"type": "noul", "noul": 0.9},
        },
        "usage": {"input_tokens": 100, "output_tokens": 12},
    }


def test_native_typed_evidence_round_trip() -> None:
    request = _request()
    model, answers, usage = parse_jev_response(_payload(), request)
    assert model == "jev-1.13.0"
    assert usage.input_tokens == 100
    assert answers["pair"].model_dump()["choice"] == "ab"
    assert answers["fit"].model_dump()["score"] == 1.5
    assert isinstance(answers["safe"], NoulAnswer)
    assert "confidence" not in answers["safe"].model_dump()
    assert DecisionRequest.model_validate_json(request.model_dump_json()) == request


def test_digest_binds_semantics_not_map_order_request_id_or_deadline() -> None:
    request = _request()
    fields = request.model_dump(mode="json")
    fields["request_id"] = "request-2"
    fields["deadline_monotonic"] = 100.0
    fields["state"] = {"intent": "实用内容", "prefix": []}
    fields["questions"] = dict(reversed(list(fields["questions"].items())))
    fields["questions"]["pair"]["criteria"] = {"cd": "C D", "ab": "A B"}
    reordered = DecisionRequest.model_validate_json(json.dumps(fields))
    assert reordered.input_digest == request.input_digest
    for field, value in (
        ("purpose", "REPAIR_PAIR"),
        ("question_schema_version", "pair-v2"),
        ("state", {"intent": "理论"}),
    ):
        assert _request(**{field: value}).input_digest != request.input_digest
    assert len(request.input_digest) == 64


@pytest.mark.parametrize(
    "state",
    [None, 2, True, 0.3, {1: "bad key"}, {"bad": float("nan")}, [float("inf")], [set()]],
)
def test_state_rejects_non_json_and_nonfinite_values(state: object) -> None:
    with pytest.raises(ValidationError):
        _request(state=state)


@pytest.mark.parametrize("state", ["", [], {}, {"a": [None, True, 1, 2.0, "中文"]}])
def test_state_accepts_native_json_containers_and_text(state: object) -> None:
    assert _request(state=state).state == state


@pytest.mark.parametrize("size", [0, 256])
def test_choice_option_count_is_bounded(size: int) -> None:
    with pytest.raises(ValidationError):
        ChoiceQuestion(instructions="Choose", criteria={str(i): "option" for i in range(size)})


@pytest.mark.parametrize("size", [0, 1, 11])
def test_score_rubric_count_is_bounded(size: int) -> None:
    with pytest.raises(ValidationError):
        ScoreQuestion(instructions="Rate", criteria=tuple(str(i) for i in range(size)))


def test_noul_optional_criteria_use_native_boolean_labels() -> None:
    question = NoulQuestion(
        instructions="Is relevant?", criteria=NoulCriteria(true="related", false="unrelated")
    )
    assert question.model_dump()["criteria"] == {"true": "related", "false": "unrelated"}


@pytest.mark.parametrize(
    ("answer_key", "field", "value"),
    [
        ("pair", "choice", "unknown"),
        ("pair", "choice", "cd"),
        ("pair", "probabilities", {"ab": 1.0}),
        ("pair", "probabilities", {"ab": 0.8, "cd": 0.1, "extra": 0.1}),
        ("pair", "probabilities", {"ab": 0.4, "cd": 0.4}),
        ("pair", "probabilities", {"ab": 1.1, "cd": -0.1}),
        ("pair", "probabilities", {"ab": float("nan"), "cd": 0.2}),
        ("pair", "confidence", "0.8"),
        ("pair", "confidence", True),
        ("pair", "confidence", float("inf")),
        ("pair", "confidence", 1.01),
        ("fit", "score", 1.0),
        ("fit", "score", float("nan")),
        ("fit", "legend", {"0": "different", "1": "medium", "2": "high"}),
        ("fit", "probabilities", {"0": 0.0, "1": 0.5, "3": 0.5}),
        ("safe", "noul", -0.1),
        ("safe", "noul", True),
        ("safe", "noul", "0.9"),
        ("safe", "confidence", 1.0),
        ("safe", "type", "choice"),
    ],
)
def test_invalid_answers_are_rejected_without_payload_leak(
    answer_key: str, field: str, value: object
) -> None:
    payload = _payload()
    payload["answers"][answer_key][field] = value
    with pytest.raises(ValueError, match="^invalid decision response$"):
        parse_jev_response(payload, _request())


@pytest.mark.parametrize("change", ["missing", "extra", "missing_type", "usage", "model"])
def test_response_requires_exact_keys_types_model_and_usage(change: str) -> None:
    payload = _payload()
    if change == "missing":
        del payload["answers"]["safe"]
    elif change == "extra":
        payload["answers"]["extra"] = deepcopy(payload["answers"]["safe"])
    elif change == "missing_type":
        del payload["answers"]["safe"]["type"]
    elif change == "usage":
        payload["usage"]["input_tokens"] = True
    else:
        payload["model"] = ""
    with pytest.raises(ValueError, match="^invalid decision response$"):
        parse_jev_response(payload, _request())
