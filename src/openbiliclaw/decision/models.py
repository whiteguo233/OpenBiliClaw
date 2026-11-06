"""Typed, validated decision requests and evidence; no recommendation policy."""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Iterable

DecisionPurpose = Literal["FILTER", "EVALUATE", "CHOOSE_PAIR", "AUDIT_DRAFT", "REPAIR_PAIR"]
DecisionStatus = Literal["OK", "UNAVAILABLE", "INVALID"]
# Wire rounding tolerance, not a semantic confidence or recommendation threshold.
_WIRE_TOLERANCE = 1e-6
# Transport compatibility version; consumers bind it into reusable evidence.
JEV_WIRE_VERSION = "jev-wire-v2-2dp"
# Real Jev/OpenRouter 1.14 replies (2026-10-27) round each probability and
# score independently to 2 decimal places. Half a last-place unit is a wire
# quantization bound, NOT a learned confidence/quality threshold. Preserve raw
# values; only accept when some unrounded distribution could have produced them.
_ROUNDING_HALF_UNIT = 0.005
_Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
_Probability = Annotated[float, Field(ge=0, le=1)]


class _DecisionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class ChoiceQuestion(_DecisionModel):
    """Select exactly one key from a bounded, caller-owned option map."""

    type: Literal["choice"] = "choice"
    instructions: _Text
    criteria: dict[_Text, _Text] = Field(min_length=1, max_length=255)


class ScoreQuestion(_DecisionModel):
    """Evaluate an ordered rubric whose integer levels start at zero."""

    type: Literal["score"] = "score"
    instructions: _Text
    criteria: tuple[_Text, ...] = Field(min_length=2, max_length=10)


class NoulCriteria(_DecisionModel):
    """Optional meanings of a yes and no answer, using native Jev keys."""

    true: _Text
    false: _Text


class NoulQuestion(_DecisionModel):
    """A yes/no proposition; its answer has no separate confidence field."""

    type: Literal["noul"] = "noul"
    instructions: _Text
    criteria: NoulCriteria | None = None


DecisionQuestion = Annotated[
    ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")
]


def _check_json(value: object) -> None:
    """Reject non-JSON types and nonfinite numbers before any coercion."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _check_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _check_json(item)
        return
    raise ValueError("state must contain only finite JSON values")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class DecisionRequest(_DecisionModel):
    """One independent-question evaluation with local attribution and a deadline.

    ``deadline_monotonic`` uses ``time.monotonic()`` and caps the provider's
    total budget. It is not sent to Jev. Nested JSON is snapshotted on evaluate;
    callers should nevertheless treat all request fields as immutable.
    """

    request_id: _Text
    purpose: DecisionPurpose
    question_schema_version: _Text
    state: JsonValue
    questions: dict[_Text, DecisionQuestion] = Field(min_length=1)
    deadline_monotonic: float | None = Field(default=None, ge=0)

    @field_validator("state", mode="before")
    @classmethod
    def _validate_state(cls, value: object) -> object:
        if type(value) not in (str, dict, list):
            raise ValueError("state must be a JSON string, object or array")
        try:
            _check_json(value)
        except RecursionError:
            raise ValueError("state must be acyclic JSON") from None
        return value

    @property
    def input_digest(self) -> str:
        """Stable SHA-256 of semantic inputs; bind model separately for reuse."""
        content = self.model_dump(mode="json", exclude={"request_id", "deadline_monotonic"})
        return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def _validate_distribution(probabilities: dict[str, float]) -> None:
    if probabilities and math.isclose(
        math.fsum(probabilities.values()), 1.0, rel_tol=0, abs_tol=_WIRE_TOLERANCE
    ):
        return
    if probabilities and _two_decimal_wire(probabilities.values()):
        bounds = [_rounding_interval(value) for value in probabilities.values()]
        if math.fsum(low for low, _ in bounds) <= 1 <= math.fsum(high for _, high in bounds):
            return
    raise ValueError("probabilities must sum to one within wire rounding")


def _two_decimal_wire(values: Iterable[float]) -> bool:
    return all(abs(value * 100 - round(value * 100)) <= 1e-9 for value in values)


def _rounding_interval(value: float) -> tuple[float, float]:
    return max(0.0, value - _ROUNDING_HALF_UNIT), min(1.0, value + _ROUNDING_HALF_UNIT)


def _rounded_score_possible(score: float, probabilities: dict[str, float]) -> bool:
    """Exact linear extrema under per-value rounding intervals and sum(p)=1."""
    if not _two_decimal_wire([score, *probabilities.values()]):
        return False
    bounds = [_rounding_interval(probabilities[str(i)]) for i in range(len(probabilities))]
    remaining = 1 - math.fsum(low for low, _ in bounds)
    if (
        remaining < -_WIRE_TOLERANCE
        or remaining > math.fsum(high - low for low, high in bounds) + _WIRE_TOLERANCE
    ):
        return False

    def extremum(reverse: bool) -> float:
        mass = max(0.0, remaining)
        value = math.fsum(i * low for i, (low, _) in enumerate(bounds))
        for i in sorted(range(len(bounds)), reverse=reverse):
            extra = min(mass, bounds[i][1] - bounds[i][0])
            value += i * extra
            mass -= extra
        return value

    return (
        extremum(False) <= score + _ROUNDING_HALF_UNIT + _WIRE_TOLERANCE
        and extremum(True) >= score - _ROUNDING_HALF_UNIT - _WIRE_TOLERANCE
    )


class ChoiceAnswer(_DecisionModel):
    """Raw choice evidence, not a calibrated probability of user satisfaction."""

    type: Literal["choice"] = "choice"
    choice: _Text
    probabilities: dict[_Text, _Probability]
    confidence: _Probability

    @model_validator(mode="after")
    def _validate_choice(self) -> Self:
        _validate_distribution(self.probabilities)
        if (
            self.choice not in self.probabilities
            or self.probabilities[self.choice] < max(self.probabilities.values()) - _WIRE_TOLERANCE
        ):
            raise ValueError("choice must be a highest-probability option")
        return self


class ScoreAnswer(_DecisionModel):
    """Raw probability-weighted level with its original rubric and distribution."""

    type: Literal["score"] = "score"
    score: Annotated[float, Field(ge=0)]
    legend: dict[_Text, _Text] = Field(min_length=2, max_length=10)
    probabilities: dict[_Text, _Probability]
    confidence: _Probability

    @model_validator(mode="after")
    def _validate_score(self) -> Self:
        keys = {str(index) for index in range(len(self.legend))}
        if set(self.legend) != keys or set(self.probabilities) != keys:
            raise ValueError("score levels must be consecutive and complete")
        _validate_distribution(self.probabilities)
        expected = math.fsum(int(key) * value for key, value in self.probabilities.items())
        if self.score > len(self.legend) - 1 or (
            not math.isclose(self.score, expected, rel_tol=0, abs_tol=_WIRE_TOLERANCE)
            and not _rounded_score_possible(self.score, self.probabilities)
        ):
            raise ValueError("score must equal its probability-weighted levels")
        return self


class NoulAnswer(_DecisionModel):
    """Native Noul evidence; deliberately cannot carry synthetic confidence."""

    type: Literal["noul"] = "noul"
    noul: _Probability


DecisionAnswer = Annotated[ChoiceAnswer | ScoreAnswer | NoulAnswer, Field(discriminator="type")]


class DecisionUsage(_DecisionModel):
    """Usage from the returned response; failed attempts may also incur costs."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class DecisionEvidence(_DecisionModel):
    """Technical evidence only; the consumer owns abstention and business policy."""

    status: DecisionStatus
    reason_code: _Text
    provider: _Text = "jev"
    request_id: _Text
    purpose: DecisionPurpose
    question_schema_version: _Text
    input_digest: _Text
    requested_model: _Text
    actual_model: _Text | None = None
    answers: dict[str, DecisionAnswer] = Field(default_factory=dict)
    usage: DecisionUsage | None = None
    latency_ms: float = Field(ge=0)
    attempts: int = Field(ge=0)
    http_status: int | None = Field(default=None, ge=100, le=599)

    @model_validator(mode="after")
    def _validate_status(self) -> Self:
        if self.status == "OK":
            if not self.answers or self.actual_model is None or self.usage is None:
                raise ValueError("successful evidence needs answers, model and usage")
        elif self.answers:
            raise ValueError("failed evidence must not expose partial answers")
        return self


class _JevWireCostDetails(_DecisionModel):
    """Known gateway billing fields; never used to validate recommendation quality."""

    upstream_inference_cost: Annotated[float, Field(ge=0)] | None = None
    upstream_inference_prompt_cost: Annotated[float, Field(ge=0)] | None = None
    upstream_inference_completions_cost: Annotated[float, Field(ge=0)] | None = None


class _JevWireUsage(DecisionUsage):
    cost: Annotated[float, Field(ge=0)] | None = None
    is_byok: bool | None = None
    cost_details: _JevWireCostDetails | None = None


class _JevResponse(_DecisionModel):
    model: _Text
    answers: dict[str, DecisionAnswer]
    usage: _JevWireUsage
    id: _Text | None = None
    provider: _Text | None = None


def parse_jev_response(
    payload: object, request: DecisionRequest
) -> tuple[str, dict[str, DecisionAnswer], DecisionUsage]:
    """Validate complete native evidence against the request, without leaking data.

    Reject partial answers, unknown options, incorrect primitive types and
    changed Score rubrics. Raises only a sanitized ``ValueError`` on bad input.
    """
    try:
        response = _JevResponse.model_validate(payload)
        if set(response.answers) != set(request.questions):
            raise ValueError("question keys differ")
        for key, question in request.questions.items():
            answer = response.answers[key]
            if question.type != answer.type:
                raise ValueError("primitive types differ")
            if (
                isinstance(question, ChoiceQuestion)
                and isinstance(answer, ChoiceAnswer)
                and set(answer.probabilities) != set(question.criteria)
            ):
                raise ValueError("choice keys differ")
            if (
                isinstance(question, ScoreQuestion)
                and isinstance(answer, ScoreAnswer)
                and answer.legend
                != {str(index): value for index, value in enumerate(question.criteria)}
            ):
                raise ValueError("score legend differs")
        usage = DecisionUsage(
            input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens
        )
        return response.model, response.answers, usage
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid decision response") from None
