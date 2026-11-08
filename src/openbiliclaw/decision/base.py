"""Independent seam for typed, side-effect-free model decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openbiliclaw.decision.models import DecisionEvidence, DecisionRequest


@runtime_checkable
class DecisionProvider(Protocol):
    """A provider returns evidence and never applies recommendation policy.

    Caller cancellation propagates. Expected transport or response failures
    return unavailable/invalid evidence without sensitive input or error text.
    """

    async def evaluate(self, request: DecisionRequest) -> DecisionEvidence:
        """Evaluate within a total deadline, preserving request attribution."""
        ...

    async def aclose(self) -> None:
        """Release only resources owned by this provider."""
        ...
