"""One bounded model correction at the real planner Interface; no network calls."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.recommendation.director import planner as planner_module
from openbiliclaw.recommendation.director.planner import HorizonPlanner
from tests.test_director_horizon_modes import _request as _base_request

if TYPE_CHECKING:
    from openbiliclaw.recommendation.director.models import CompositionRequest


def _request(count: int = 9) -> CompositionRequest:
    request = _base_request(count=count)
    return request.model_copy(
        update={"cards": (*request.cards[:-1], request.cards[-1].model_copy(update={"ref": "c13"}))}
    )


def _short_plan(*, corrected: bool) -> str:
    # Nine usable candidates, but the first draft leaves c13 unaudited and unused.
    return json.dumps(
        {
            "completion": {"reason": "supply_exhausted", "omissions": []},
            "screens": [
                {
                    "direction": "Useful full screen",
                    "pairs": [{"card_refs": [f"c{i}", f"c{i + 1}"]} for i in (0, 2, 4)],
                },
                {
                    "direction": "Useful short tail",
                    "pairs": [{"card_refs": ["c6", "c7"]}]
                    + ([{"card_refs": ["c8"]}] if corrected else []),
                },
            ],
        }
    )


class _SequenceLLM:
    def __init__(self, *outputs: str | BaseException) -> None:
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        value = self.outputs[len(self.calls) - 1]
        if isinstance(value, BaseException):
            raise value
        return LLMResponse(
            content=value,
            model="offline-correction",
            provider="fixture",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 2,
                "reasoning_output_tokens": 3,
            },
        )


async def test_invalid_six_plus_two_gets_one_model_correction_to_six_plus_three():
    request = _request(count=9)
    before = request.model_dump_json()
    llm = _SequenceLLM(_short_plan(corrected=False), _short_plan(corrected=True))
    result = await HorizonPlanner(llm).plan(request)
    assert result.plan is not None
    assert result.calls == len(llm.calls) == 2
    assert tuple(len(screen.card_refs) for screen in result.plan.screens) == (6, 3)
    assert result.plan.screens[-1].card_refs[-1] == "c13"
    assert json.loads(result.plan.planner_usage_json) == {
        "input_tokens": 20,
        "output_tokens": 10,
        "cached_input_tokens": 4,
        "reasoning_output_tokens": 6,
    }
    first, second = llm.calls
    assert first["system_instruction"] == second["system_instruction"]
    assert all(call["inject_core_memory"] is False and "history" not in call for call in llm.calls)
    original = json.loads(first["user_input"])
    correction = json.loads(second["user_input"])
    assert correction.pop("previous_invalid_plan") == _short_plan(corrected=False)
    feedback = correction.pop("validation_feedback")
    assert feedback["code"] == "unreported_usable_candidates"
    assert feedback["actual_screen_counts"] == [6, 2]
    assert feedback["unexplained_eligible_refs"] == ["c8"]  # c13's request-local alias.
    assert correction["untrusted_candidates"][-1]["ref"] == "c8"
    assert correction == original
    assert request.model_dump_json() == before


async def test_repeated_invalid_output_stops_after_two_calls_without_local_card_repair():
    llm = _SequenceLLM(_short_plan(corrected=False), _short_plan(corrected=False))
    result = await HorizonPlanner(llm).plan(_request(count=9))
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(llm.calls) == 2


async def test_disabled_correction_keeps_exact_one_call_failure_baseline():
    llm = _SequenceLLM(_short_plan(corrected=False))
    result = await HorizonPlanner(llm).plan(_request(count=9), max_validation_retries=0)
    assert result.reason_code == "invalid_planner_output"
    assert result.plan is None
    assert result.calls == len(llm.calls) == 1


@pytest.mark.parametrize("value", [-1, 2, True, 1.0])
async def test_validation_retry_bound_is_strict(value):
    llm = _SequenceLLM(_short_plan(corrected=True))
    with pytest.raises(ValueError):
        await HorizonPlanner(llm).plan(_request(count=9), max_validation_retries=value)
    assert not llm.calls


@pytest.mark.parametrize("attempt", [1, 2])
@pytest.mark.parametrize(
    "error,reason",
    [(TimeoutError("SECRET"), "planner_timeout"), (RuntimeError("SECRET"), "planner_unavailable")],
)
async def test_transport_failure_is_not_retried_and_preserves_real_failure(attempt, error, reason):
    outputs = (error,) if attempt == 1 else (_short_plan(corrected=False), error)
    llm = _SequenceLLM(*outputs)
    result = await HorizonPlanner(llm).plan(_request(count=9))
    assert result.reason_code == reason
    assert result.plan is None
    assert result.calls == len(llm.calls) == attempt
    assert "SECRET" not in result.model_dump_json()


async def test_too_large_first_output_is_not_retried():
    llm = _SequenceLLM("x" * (planner_module._MAX_RESPONSE_BYTES + 1))
    result = await HorizonPlanner(llm).plan(_request(count=9))
    assert result.plan is None
    assert result.reason_code == "invalid_planner_output"
    assert result.calls == len(llm.calls) == 1


async def test_unencodable_output_is_rejected_without_constructing_correction_prompt():
    llm = _SequenceLLM("\ud800")
    result = await HorizonPlanner(llm).plan(_request())
    assert result.reason_code == "invalid_planner_output"
    assert result.plan is None
    assert result.calls == len(llm.calls) == 1


async def test_correction_prompt_size_is_bounded_without_truncating_original_context(monkeypatch):
    llm = _SequenceLLM(_short_plan(corrected=False))
    # Derive the original input bound through the actual planner, not a parallel
    # test serializer. One extra byte cannot fit the invalid-output feedback.
    await HorizonPlanner(llm).plan(_request(count=9), max_validation_retries=0)
    monkeypatch.setattr(
        planner_module, "_MAX_PROMPT_BYTES", len(llm.calls[0]["user_input"].encode()) + 1
    )
    llm = _SequenceLLM(_short_plan(corrected=False))
    result = await HorizonPlanner(llm).plan(_request(count=9))
    assert result.plan is None
    assert result.calls == len(llm.calls) == 1


@pytest.mark.parametrize("attempt", [1, 2])
async def test_cancellation_is_never_converted_into_retry_or_failure_result(attempt):
    outputs = (
        (asyncio.CancelledError(),)
        if attempt == 1
        else (_short_plan(corrected=False), asyncio.CancelledError())
    )
    llm = _SequenceLLM(*outputs)
    with pytest.raises(asyncio.CancelledError):
        await HorizonPlanner(llm).plan(_request(count=9))
    assert len(llm.calls) == attempt


async def test_correction_uses_frozen_original_request_even_if_caller_mutates_nested_profile():
    request = _request(count=9)
    request.session.profile_context["stable"] = {"interests": ["original"]}

    class MutatingLLM(_SequenceLLM):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            response = await super().complete_structured_task(**kwargs)
            request.session.profile_context["stable"] = {"interests": ["mutation"]}
            return response

    llm = MutatingLLM(_short_plan(corrected=False), _short_plan(corrected=True))
    assert (await HorizonPlanner(llm).plan(request)).plan is not None
    assert all(
        json.loads(call["user_input"])["profile_context"]["stable"]["interests"] == ["original"]
        for call in llm.calls
    )


async def test_validation_feedback_never_echoes_exception_or_candidate_text():
    llm = _SequenceLLM('{"screens":"PRIVATE_INVALID_SHAPE"}', _short_plan(corrected=True))
    assert (await HorizonPlanner(llm).plan(_request(count=9))).plan is not None
    correction = json.loads(llm.calls[1]["user_input"])
    assert "PRIVATE" not in json.dumps(correction["validation_feedback"])
    assert "PRIVATE" in correction["previous_invalid_plan"]


async def test_feedback_reports_known_ref_conflicts_without_echoing_unknown_ref_text():
    invalid = json.loads(_short_plan(corrected=True))
    invalid["screens"][1]["pairs"][0]["card_refs"] = ["c0", "PRIVATE_UNKNOWN_REF"]
    invalid["completion"]["omissions"] = [{"refs": ["c0"], "reason": "off_intent"}]
    llm = _SequenceLLM(json.dumps(invalid), _short_plan(corrected=True))
    result = await HorizonPlanner(llm).plan(_request(count=9))
    assert result.plan is not None
    feedback = json.loads(llm.calls[1]["user_input"])["validation_feedback"]
    assert feedback["repeated_known_refs"] == ["c0"]
    assert feedback["selected_omitted_overlap_known_refs"] == ["c0"]
    assert feedback["unexplained_eligible_refs"] == ["c6", "c7"]
    assert "PRIVATE" not in json.dumps(feedback)


@pytest.mark.parametrize("attempt", [1, 2])
async def test_swallowed_external_cancellation_still_propagates(attempt):
    class SwallowingLLM(_SequenceLLM):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            response = await super().complete_structured_task(**kwargs)
            if len(self.calls) == attempt:
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(0)
            return response

    llm = SwallowingLLM(_short_plan(corrected=False), _short_plan(corrected=True))
    with pytest.raises(asyncio.CancelledError):
        await HorizonPlanner(llm).plan(_request(count=9))
    assert len(llm.calls) == attempt


async def test_budget_expiring_while_validating_invalid_output_does_not_start_second_call(
    monkeypatch,
):
    clock = [100.0]
    original_feedback = planner_module._validation_feedback

    def slow_feedback(*args):
        feedback = original_feedback(*args)
        clock[0] = 111.0
        return feedback

    monkeypatch.setattr(planner_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(planner_module, "_validation_feedback", slow_feedback)
    llm = _SequenceLLM(_short_plan(corrected=False))
    result = await HorizonPlanner(llm).plan(_request(count=9), budget_seconds=10)
    assert result.reason_code == "planner_timeout"
    assert result.calls == len(llm.calls) == 1


async def test_correction_shares_total_deadline_instead_of_getting_a_new_budget(monkeypatch):
    waits: list[float] = []
    clock = [100.0]
    real_timeout = asyncio.timeout

    def recording_timeout(delay):
        waits.append(delay)
        return real_timeout(delay)

    monkeypatch.setattr(planner_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(planner_module.asyncio, "timeout", recording_timeout)

    class SlowFirst(_SequenceLLM):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            response = await super().complete_structured_task(**kwargs)
            clock[0] += 6 if len(self.calls) == 1 else 1
            return response

    llm = SlowFirst(_short_plan(corrected=False), _short_plan(corrected=True))
    result = await HorizonPlanner(llm).plan(_request(count=9), budget_seconds=10)
    assert result.plan is not None
    assert waits == [10, 4]


async def test_late_first_result_does_not_launch_correction(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(planner_module.time, "monotonic", lambda: clock[0])

    class LateFirst(_SequenceLLM):
        async def complete_structured_task(self, **kwargs: Any) -> LLMResponse:
            response = await super().complete_structured_task(**kwargs)
            clock[0] += 11
            return response

    llm = LateFirst(_short_plan(corrected=False))
    result = await HorizonPlanner(llm).plan(_request(count=9), budget_seconds=10)
    assert result.plan is None
    assert result.reason_code == "late_planner_result"
    assert result.calls == len(llm.calls) == 1
