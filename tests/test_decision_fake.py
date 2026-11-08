"""Replay fixtures exercise the same response contract without external calls."""

from __future__ import annotations

import time

import pytest

from openbiliclaw.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionRequest,
    ScoreAnswer,
    ScoreQuestion,
    ScriptedDecisionProvider,
)


def _request(**kwargs: object) -> DecisionRequest:
    fields: dict[str, object] = {
        "request_id": "pair-1",
        "purpose": "CHOOSE_PAIR",
        "question_schema_version": "test-pair.v1",
        "state": {"prefix": [], "intent": "local AI tools"},
        "questions": {
            "pair": ChoiceQuestion(
                instructions="Choose the next pair for the stated intent.",
                criteria={"ab": "A and B", "cd": "C and D", "none": "Neither pair is suitable"},
            )
        },
    }
    fields.update(kwargs)
    return DecisionRequest.model_validate(fields)


def _payload() -> dict[str, object]:
    return {
        "model": "jev-fixture-v1",
        "answers": {
            "pair": {
                "type": "choice",
                "choice": "ab",
                "probabilities": {"ab": 0.7, "cd": 0.2, "none": 0.1},
                "confidence": 0.4,
            }
        },
        "usage": {"input_tokens": 40, "output_tokens": 4},
    }


async def test_fake_returns_validated_evidence_bound_to_call() -> None:
    request = _request()
    provider = ScriptedDecisionProvider([_payload()])
    result = await provider.evaluate(request)
    assert result.status == "OK"
    assert result.provider == "fake"
    assert result.actual_model == "jev-fixture-v1"
    assert result.request_id == request.request_id
    assert result.input_digest == request.input_digest
    assert result.purpose == "CHOOSE_PAIR"
    assert isinstance(result.answers["pair"], ChoiceAnswer)
    assert result.answers["pair"].choice == "ab"


async def test_fake_exhaustion_is_not_fabricated_success() -> None:
    result = await ScriptedDecisionProvider([]).evaluate(_request())
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "fake_exhausted"
    assert not result.answers
    assert result.actual_model is None
    assert result.usage is None
    assert result.attempts == 0


@pytest.mark.parametrize("payload", [None, {}, {"model": "jev", "answers": {}}])
async def test_bad_fixture_is_invalid_evidence(payload: object) -> None:
    result = await ScriptedDecisionProvider([payload]).evaluate(_request())
    assert result.status == "INVALID"
    assert result.reason_code == "invalid_response"
    assert not result.answers


async def test_requests_are_copied_and_exposed_as_detached_snapshots() -> None:
    request = _request()
    provider = ScriptedDecisionProvider([_payload()])
    await provider.evaluate(request)
    request.questions.clear()
    assert "pair" in provider.requests[0].questions
    provider.requests[0].questions.clear()
    assert "pair" in provider.requests[0].questions


async def test_fixtures_are_copied_at_construction() -> None:
    payload = _payload()
    provider = ScriptedDecisionProvider([payload])
    payload.clear()
    assert (await provider.evaluate(_request())).status == "OK"


async def test_expired_deadline_does_not_consume_next_fixture() -> None:
    provider = ScriptedDecisionProvider([_payload()])
    expired = await provider.evaluate(_request(deadline_monotonic=time.monotonic() - 1))
    assert expired.reason_code == "deadline_exceeded"
    assert expired.attempts == 0
    assert (await provider.evaluate(_request())).status == "OK"


async def test_closed_fake_never_consumes_fixture() -> None:
    provider = ScriptedDecisionProvider([_payload()])
    await provider.aclose()
    await provider.aclose()
    result = await provider.evaluate(_request())
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "provider_closed"


async def test_score_roundtrip_preserves_ordered_rubric() -> None:
    request = _request(
        purpose="AUDIT_DRAFT",
        questions={
            "fit": ScoreQuestion(instructions="Rate the draft fit.", criteria=("poor", "good"))
        },
    )
    payload = {
        "model": "jev-fixture-v1",
        "answers": {
            "fit": {
                "type": "score",
                "score": 0.8,
                "legend": {"0": "poor", "1": "good"},
                "probabilities": {"0": 0.2, "1": 0.8},
                "confidence": 0.5,
            }
        },
        "usage": {"input_tokens": 25, "output_tokens": 3},
    }
    provider = ScriptedDecisionProvider([payload])
    result = await provider.evaluate(request)
    assert result.status == "OK"
    assert isinstance(result.answers["fit"], ScoreAnswer)
    assert result.answers["fit"].score == 0.8
    assert provider.requests[0].questions["fit"] == request.questions["fit"]


async def test_mutated_invalid_request_does_not_leak_state() -> None:
    request = _request(state="private request content")
    request.questions.clear()
    with pytest.raises(ValueError, match="^invalid decision request$"):
        await ScriptedDecisionProvider([]).evaluate(request)


async def test_corrupt_nested_type_is_sanitized_without_serialization_warnings(
    recwarn: pytest.WarningsRecorder,
) -> None:
    request = _request()
    request.questions.update({"corrupt": "private-value-do-not-log"})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="^invalid decision request$"):
        await ScriptedDecisionProvider([]).evaluate(request)
    assert not recwarn.list


async def test_nonfinite_mutation_is_not_silently_converted_to_null() -> None:
    request = _request(state={"weight": 0.7})
    assert isinstance(request.state, dict)
    request.state["weight"] = float("nan")
    with pytest.raises(ValueError, match="^invalid decision request$"):
        await ScriptedDecisionProvider([]).evaluate(request)
