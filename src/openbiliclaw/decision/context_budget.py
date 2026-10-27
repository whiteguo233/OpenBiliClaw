"""Model-specific Jev input budgets; byte guards are not tokenizer estimates.

The published Jev 1.13.0 limits are 64k tokens across state and questions,
and 32k tokens for state plus one question (docs.typesafe.ai/models). We
interpret k as decimal and reserve 25% engineering headroom while counting
UTF-8 wire bytes, including the envelope. This is intentionally conservative,
not a measured bytes-per-token ratio or a guarantee from a native tokenizer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

from openbiliclaw.config import JevContextLimits as JevContextLimits

if TYPE_CHECKING:
    from openbiliclaw.decision.models import DecisionQuestion, DecisionRequest


class JevContextError(ValueError):
    """Selected model capacity is unknown or explicitly configured incorrectly."""


def parse_jev_context_limits(value: object) -> JevContextLimits:
    """Validate user-supplied limits without coercion or echoing their values."""
    if isinstance(value, JevContextLimits):
        limits = value
    elif isinstance(value, Mapping):
        expected = {"max_request_tokens", "max_state_question_tokens"}
        if set(value) != expected:
            raise JevContextError("context_limits requires exactly both token-limit fields")
        limits = JevContextLimits(**dict(value))
    else:
        raise JevContextError("context_limits must be a table of token limits")
    for amount in (limits.max_request_tokens, limits.max_state_question_tokens):
        # A configuration typo/DoS guard, not a claim about any model's capacity.
        if type(amount) is not int or not 256 <= amount <= 100_000_000:
            raise JevContextError("context token limits must be integers in 256..100000000")
    if limits.max_state_question_tokens > limits.max_request_tokens:
        raise JevContextError("state+question token limit cannot exceed request token limit")
    return limits


def validate_jev_model_id(model: object) -> None:
    """Reject whitespace/control-character model keys rather than normalizing them."""
    if (
        not isinstance(model, str)
        or not model
        or len(model) > 200
        or model != model.strip()
        or any(ord(char) < 32 for char in model)
    ):
        raise JevContextError("context model ID must be nonempty, exact text without whitespace")


@dataclass(frozen=True)
class JevContextMeasurement:
    """Canonical native request size and largest one-question envelope, in bytes."""

    request_bytes: int
    max_state_question_bytes: int


def _wire(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class JevContextBudget:
    """Immutable effective budget bound to the user-selected model, never a router."""

    model: str
    source: str
    max_request_tokens: int
    max_state_question_tokens: int
    max_request_bytes: int
    max_state_question_bytes: int

    @property
    def fingerprint(self) -> str:
        """Include capacity/source/guard policy in request and cache identities."""
        payload = {"policy": "jev-wire-bytes-headroom-v1", **asdict(self)}
        return hashlib.sha256(_wire(payload)).hexdigest()

    def measure(
        self, state: object, questions: Mapping[str, DecisionQuestion]
    ) -> JevContextMeasurement:
        """Count exactly the native provider's canonical JSON wire envelope."""
        encoded_questions = {
            key: question.model_dump(mode="json", exclude_none=True)
            for key, question in questions.items()
        }
        base = len(_wire({"model": self.model, "state": state, "questions": {}}))
        question_sizes = [len(_wire({key: value})) - 2 for key, value in encoded_questions.items()]
        return JevContextMeasurement(
            request_bytes=base + sum(question_sizes) + max(0, len(question_sizes) - 1),
            max_state_question_bytes=base + max(question_sizes, default=0),
        )

    def fits_request(self, request: DecisionRequest) -> bool:
        """Check both limits; consumers still own shrinking/splitting policy."""
        size = self.measure(request.state, request.questions)
        return (
            size.request_bytes <= self.max_request_bytes
            and size.max_state_question_bytes <= self.max_state_question_bytes
        )

    def with_byte_guards(
        self, *, max_request_bytes: int = 0, max_state_question_bytes: int = 0
    ) -> JevContextBudget:
        """Apply optional stricter operator guards; zero means model-adaptive auto."""
        for guard, limit in (
            (max_request_bytes, self.max_request_bytes),
            (max_state_question_bytes, self.max_state_question_bytes),
        ):
            if type(guard) is not int or (guard != 0 and not 256 <= guard <= limit):
                raise JevContextError("byte guard must be zero (auto) or 256..model byte limit")
        request = max_request_bytes or self.max_request_bytes
        single = max_state_question_bytes or min(self.max_state_question_bytes, request)
        if single > request:
            raise JevContextError("state+question byte guard cannot exceed request byte guard")
        return replace(self, max_request_bytes=request, max_state_question_bytes=single)


# Standard windows only (official Models, checked 2026-10-18). The experimental
# long-context header is a separate opt-in protocol and is never enabled here.
_KNOWN_LIMITS = {
    "jev-1.13.0": JevContextLimits(64_000, 32_000),
    "jev-1.14.0": JevContextLimits(64_000, 32_000),
}


def resolve_jev_context(
    model: str,
    overrides: Mapping[str, JevContextLimits | Mapping[str, object]] | None = None,
) -> JevContextBudget:
    """Resolve only this exact model; unknown IDs require operator-confirmed limits."""
    validate_jev_model_id(model)
    if overrides is not None and not isinstance(overrides, Mapping):
        raise JevContextError("context_limits must map exact model IDs to token-limit tables")
    if overrides is not None and model in overrides:
        limits = parse_jev_context_limits(overrides[model])
        published = _KNOWN_LIMITS.get(model)
        if published is not None and (
            limits.max_request_tokens > published.max_request_tokens
            or limits.max_state_question_tokens > published.max_state_question_tokens
        ):
            raise JevContextError("configured limits cannot enlarge the official standard window")
        source = "operator_confirmed"
    elif model in _KNOWN_LIMITS:
        limits = _KNOWN_LIMITS[model]
        source = "published_model_registry"
    else:
        raise JevContextError(
            "unknown Jev model context; configure both token limits under "
            "decision.providers.jev.context_limits for the exact selected model ID"
        )
    return JevContextBudget(
        model=model,
        source=source,
        max_request_tokens=limits.max_request_tokens,
        max_state_question_tokens=limits.max_state_question_tokens,
        max_request_bytes=limits.max_request_tokens * 3 // 4,
        max_state_question_bytes=limits.max_state_question_tokens * 3 // 4,
    )
