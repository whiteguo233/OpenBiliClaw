"""Scripted, network-free decision provider for replay and contract tests."""

from __future__ import annotations

import time
from collections import deque
from copy import deepcopy
from typing import TYPE_CHECKING

from .models import (
    DecisionAnswer,
    DecisionEvidence,
    DecisionRequest,
    DecisionStatus,
    DecisionUsage,
    parse_jev_response,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class ScriptedDecisionProvider:
    """Consume native Jev response fixtures using the production response validator.

    Fixture exhaustion is an explicit outage, never a fabricated answer. Request
    snapshots are available for checking prefix/state propagation without a paid
    API. The fake has no content-selection or filtering policy of its own.
    """

    def __init__(self, payloads: Iterable[object], *, model: str = "jev-fixture") -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("a nonempty fixture model is required")
        self._payloads = deque(deepcopy(list(payloads)))
        self._model = model
        self._requests: list[DecisionRequest] = []
        self._closed = False

    @property
    def requests(self) -> tuple[DecisionRequest, ...]:
        """Return detached snapshots; editing them cannot mutate the recorded calls."""
        return tuple(request.model_copy(deep=True) for request in self._requests)

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        """Return the next validated fixture with the current request's attribution."""
        try:
            snapshot = DecisionRequest.model_validate(
                request.model_dump(mode="python", warnings="error")
            )
        except (ValueError, TypeError, RecursionError):
            raise ValueError("invalid decision request") from None
        self._requests.append(snapshot)

        def evidence(
            status: DecisionStatus,
            reason_code: str,
            attempts: int,
            *,
            actual_model: str | None = None,
            answers: dict[str, DecisionAnswer] | None = None,
            usage: DecisionUsage | None = None,
        ) -> DecisionEvidence:
            return DecisionEvidence(
                provider="fake",
                request_id=snapshot.request_id,
                purpose=snapshot.purpose,
                question_schema_version=snapshot.question_schema_version,
                input_digest=snapshot.input_digest,
                requested_model=self._model,
                latency_ms=0.0,
                status=status,
                reason_code=reason_code,
                actual_model=actual_model,
                answers=answers or {},
                usage=usage,
                attempts=attempts,
            )

        if self._closed:
            return evidence("UNAVAILABLE", "provider_closed", 0)
        if (
            snapshot.deadline_monotonic is not None
            and snapshot.deadline_monotonic <= time.monotonic()
        ):
            return evidence("UNAVAILABLE", "deadline_exceeded", 0)
        if not self._payloads:
            return evidence("UNAVAILABLE", "fake_exhausted", 0)
        try:
            actual_model, answers, usage = parse_jev_response(self._payloads.popleft(), snapshot)
        except ValueError:
            return evidence("INVALID", "invalid_response", 1)
        return evidence(
            "OK",
            "ok",
            1,
            actual_model=actual_model,
            answers=answers,
            usage=usage,
        )

    async def aclose(self) -> None:
        """Close the fake; subsequent calls produce explicit unavailable evidence."""
        self._closed = True
