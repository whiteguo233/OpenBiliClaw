"""Bounded horizon coverage and auditable, model-reported semantic omissions.

Hard checks prove only that the selected prefix cannot be extended with the
remaining usable candidates. They do not prove a globally optimal allocation,
semantic correctness, or user satisfaction. Omission reasons are Agent opinions
about this plan, never persistent topic exclusions or new admission rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .models import _Ref  # noqa: TC001 - Pydantic runtime type
from .proposer import eligible_cards, feasible_completion, largest_feasible_count, validate_screen

if TYPE_CHECKING:
    from .models import CompositionRequest

OmissionReason = Literal["off_intent", "redundant", "style_mismatch", "insufficient_evidence"]
CompletionReason = Literal[
    "target_reached", "candidate_shortage", "residual_constraints", "semantic_shortage"
]
_ScreenCount = Annotated[int, Field(ge=1, le=20)]


class _CoverageModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class OmittedGroup(_CoverageModel):
    """An unverified semantic opinion about known unselected candidate refs."""

    refs: tuple[_Ref, ...] = Field(min_length=1, max_length=40)
    reason: OmissionReason
    note: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.refs)) != len(self.refs):
            raise ValueError("duplicate omission reference")
        return self


def _omitted_refs(omissions: tuple[OmittedGroup, ...]) -> tuple[str, ...]:
    refs = tuple(ref for group in omissions for ref in group.refs)
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate omission reference across groups")
    return refs


class HorizonStop(_CoverageModel):
    """Raw Agent stop claim; the caller independently validates hard coverage."""

    reason: Literal["target_reached", "supply_exhausted"]
    omissions: tuple[OmittedGroup, ...] = Field(default=(), max_length=4)
    note: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _omitted_refs(self.omissions)
        if self.reason == "target_reached" and self.omissions:
            raise ValueError("a complete horizon cannot report semantic shortage")
        return self


class PlanningCompletion(_CoverageModel):
    """Immutable initial coverage, independent of subsequently consumed drafts.

    Only this snapshot's own counts and reasons are checked. Current remaining
    screens and candidate refs can change through consumption and ref rebinding.
    """

    requested_screens: int = Field(ge=1, le=3)
    target_count: int = Field(ge=1, le=20)
    initial_screen_counts: tuple[_ScreenCount, ...] = Field(min_length=1, max_length=3)
    reason: CompletionReason
    omissions: tuple[OmittedGroup, ...] = Field(default=(), max_length=4)
    note: str = Field(default="", max_length=240)
    semantic_quality: Literal["not_certified"] = "not_certified"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        counts = self.initial_screen_counts
        if len(counts) > self.requested_screens or any(n > self.target_count for n in counts):
            raise ValueError("initial coverage exceeds its requested horizon")
        if any(n != self.target_count for n in counts[:-1]):
            raise ValueError("only the last initial screen may be short")
        complete = counts == (self.target_count,) * self.requested_screens
        if (self.reason == "target_reached") != complete:
            raise ValueError("completion reason does not match initial coverage")
        _omitted_refs(self.omissions)
        if (self.reason == "semantic_shortage") != bool(self.omissions):
            raise ValueError("semantic shortage must match reported omissions")
        return self


def _validate_horizon(horizon_screens: int) -> None:
    if type(horizon_screens) is not int or not 1 <= horizon_screens <= 3:
        raise ValueError("horizon must contain one to three screens")


def build_horizon_contract(
    request: CompositionRequest, horizon_screens: int
) -> dict[str, JsonValue]:
    """Describe target coverage and a physical bound, not quota feasibility."""
    _validate_horizon(horizon_screens)
    count = len(eligible_cards(request))
    upper = min(count, horizon_screens * request.target_count)
    nominal: list[JsonValue] = [
        min(request.target_count, upper - offset)
        for offset in range(0, upper, request.target_count)
    ]
    return {
        "requested_screens": horizon_screens,
        "cards_per_screen": request.target_count,
        "eligible_count": count,
        "physical_card_upper_bound": upper,
        "nominal_screen_counts": nominal,
        "quota_feasibility_proven": False,
        "bound_basis": "Physical unique-card supply only; not a proof of topic-quota feasibility.",
    }


def validate_horizon_coverage(
    request: CompositionRequest,
    horizon_screens: int,
    screen_refs: tuple[tuple[str, ...], ...],
    stop: HorizonStop,
) -> PlanningCompletion:
    """Reject unexplained underfill without adding model calls or hard bans.

    After removing only explicitly audited semantic omissions, a short final
    screen must not admit one more card. A full final screen before the requested
    horizon must not leave another legal nonempty screen. These are local checks
    against the exact selected prefix, not a global packing optimality proof.
    """
    _validate_horizon(horizon_screens)
    if not 1 <= len(screen_refs) <= horizon_screens:
        raise ValueError("invalid horizon screen count")
    counts = tuple(len(refs) for refs in screen_refs)
    if any(n == 0 or n > request.target_count for n in counts):
        raise ValueError("planned screens must be nonempty and bounded")
    if any(n != request.target_count for n in counts[:-1]):
        raise ValueError("only the final planned screen may be short")
    cards = eligible_cards(request)
    eligible = {card.ref for card in cards}
    selected = tuple(ref for refs in screen_refs for ref in refs)
    selected_set = set(selected)
    if len(selected_set) != len(selected) or not selected_set <= eligible:
        raise ValueError("invalid or repeated planned reference")
    omitted = set(_omitted_refs(stop.omissions))
    if not omitted <= eligible or omitted & selected_set:
        raise ValueError("omissions must be eligible unselected references")
    if any(not validate_screen(request, refs, len(refs)) for refs in screen_refs):
        raise ValueError("planned screen violates hard constraints")

    complete = counts == (request.target_count,) * horizon_screens
    if complete:
        if stop.reason != "target_reached" or stop.omissions:
            raise ValueError("full horizon requires target_reached without omissions")
        reason: CompletionReason = "target_reached"
    else:
        if stop.reason != "supply_exhausted":
            raise ValueError("short horizon must explicitly report exhausted supply")
        usable = tuple(card for card in cards if card.ref not in omitted)
        remaining = tuple(card for card in usable if card.ref not in selected_set)
        tail = screen_refs[-1]
        if len(tail) < request.target_count:
            tail_refs = set(tail)
            tail_cards = tuple(
                card for card in usable if card.ref in tail_refs or card.ref not in selected_set
            )
            if (
                feasible_completion(tail_cards, request.constraints, tail, len(tail) + 1)
                is not None
            ):
                raise ValueError("short final screen leaves an admissible extension")
        elif len(screen_refs) < horizon_screens:
            residual_request = request.model_copy(update={"cards": remaining})
            possible = largest_feasible_count(residual_request)
            if possible is not None and possible > 0:
                raise ValueError("short horizon leaves another admissible screen")
        if not eligible - selected_set:
            reason = "candidate_shortage"
        elif stop.omissions:
            reason = "semantic_shortage"
        else:
            reason = "residual_constraints"

    return PlanningCompletion(
        requested_screens=horizon_screens,
        target_count=request.target_count,
        initial_screen_counts=counts,
        reason=reason,
        omissions=stop.omissions,
        note=stop.note,
    )
