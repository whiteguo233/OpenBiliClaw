"""Typed, tentative Agent plans and mode-independent director proposals.

These are values, not database entities: returning a plan/patch confers no write
authority. The store must fence the request and compare every patch base before
atomically committing one screen. Committed screens never enter this contract.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .horizon_coverage import PlanningCompletion  # noqa: TC001 - Pydantic runtime type
from .models import CompositionCard, CompositionRequest, digest
from .policy import PairRole, ScreenBrief, ScreenReview  # noqa: TC001 - Pydantic runtime types

DirectorStrategy = Literal["jev_recurrent", "agent_jev", "agent_only"]
_Ref = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
_Key = Annotated[str, Field(min_length=1, max_length=256)]


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class PlannedPair(_Model):
    """An ordered pair; a singleton is allowed only at a screen's final tail."""

    card_refs: tuple[_Ref, ...] = Field(min_length=1, max_length=2)
    intent: str = Field(default="", max_length=240)
    role: PairRole = "open"

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.card_refs)) != len(self.card_refs):
            raise ValueError("duplicate planned card")
        return self


class ScreenDraft(_Model):
    """One mutable-future screen, never a committed or presented batch."""

    screen_id: _Ref
    revision: int = Field(default=0, ge=0)
    tentative: Literal[True] = True
    direction: str = Field(min_length=1, max_length=500)
    target_count: int = Field(ge=1, le=20)
    # Initial Agent allocation, not the length after hard repair or consumption.
    initial_card_count: int | None = Field(default=None, ge=1, le=20)
    pairs: tuple[PlannedPair, ...] = Field(max_length=10)
    brief: ScreenBrief | None = None

    @property
    def card_refs(self) -> tuple[str, ...]:
        return tuple(ref for pair in self.pairs for ref in pair.card_refs)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        refs = self.card_refs
        if len(refs) > self.target_count or len(refs) != len(set(refs)):
            raise ValueError("invalid planned screen count")
        if self.initial_card_count is not None and self.initial_card_count > self.target_count:
            raise ValueError("initial allocation exceeds screen target")
        if any(len(pair.card_refs) != 2 for pair in self.pairs[:-1]):
            raise ValueError("singleton is only allowed at screen tail")
        return self


class HorizonPlan(_Model):
    """Bounded Agent result, exact planning evidence, and future-only drafts."""

    schema_version: Literal["director-horizon.v1"] = "director-horizon.v1"
    plan_id: _Ref
    revision: int = Field(default=0, ge=0)
    session_ref: _Key
    stream_ref: _Key
    configuration_revision: int = Field(ge=0)
    planning_state_revision: int = Field(ge=0)
    planning_feedback_cursor: _Key
    planning_feedback_event_refs: tuple[_Key, ...] = Field(default=(), max_length=8)
    planning_explicit_intent: str = Field(default="", max_length=500)
    planning_profile_digest: str = Field(default="", max_length=64)
    planning_policy_version: str = Field(default="", max_length=64)
    # Historical planning outcome, not a count of the current unconsumed drafts.
    # Older stored plans remain readable without inventing a completion reason.
    planning_completion: PlanningCompletion | None = None
    planning_input_digest: _Key
    candidate_set_ref: _Key
    feature_schema_version: _Key
    constraints_digest: _Key
    cards: tuple[CompositionCard, ...] = Field(max_length=40)
    screens: tuple[ScreenDraft, ...] = Field(min_length=1, max_length=3)
    created_at: float = Field(ge=0)
    expires_at: float = Field(gt=0)
    planner_model: str = Field(min_length=1, max_length=256)
    planner_provider: str = Field(default="", max_length=256)
    planner_usage_json: str = Field(default="{}", max_length=2000)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        refs = {card.ref for card in self.cards}
        keys = {card.item_key for card in self.cards}
        selected = tuple(ref for screen in self.screens for ref in screen.card_refs)
        if len(refs) != len(self.cards) or len(keys) != len(self.cards):
            raise ValueError("duplicate planning candidate")
        if len({screen.screen_id for screen in self.screens}) != len(self.screens):
            raise ValueError("duplicate planned screen")
        if len(set(selected)) != len(selected) or not set(selected) <= refs:
            raise ValueError("invalid or duplicate future card reference")
        if self.expires_at <= self.created_at:
            raise ValueError("plan expiration must follow creation")
        return self

    @property
    def content_digest(self) -> str:
        return digest(self.model_dump(mode="json"))


class PlanningResult(_Model):
    plan: HorizonPlan | None = None
    reason_code: _Key
    calls: int = Field(ge=0, le=2)


class DirectorPair(_Model):
    pair_index: int = Field(ge=0)
    prefix_refs: tuple[_Ref, ...]
    selected_refs: tuple[_Ref, ...] = Field(min_length=1, max_length=2)
    origin: Literal["agent", "decision", "deterministic"]
    reason_code: _Key
    role: PairRole = "open"
    intent: str = Field(default="", max_length=240)
    audit_evidence_json: str | None = None
    evidence_json: str | None = None


class DraftPatch(_Model):
    """Compare-and-swap proposal; the store owns checking and applying it."""

    plan_id: _Ref
    base_plan_revision: int = Field(ge=0)
    base_plan_digest: _Key
    screen_id: _Ref
    base_draft_revision: int = Field(ge=0)
    state_revision: int = Field(ge=0)
    feedback_cursor: _Key
    composition_input_digest: _Key
    constraints_digest: _Key
    old_pairs_digest: _Key
    replacements: tuple[PlannedPair, ...] = Field(max_length=10)
    invalidate_from_pair: int = Field(ge=0)
    affected_future_screens: tuple[_Ref, ...] = Field(max_length=3)
    reason_codes: tuple[_Key, ...] = Field(max_length=20)


class DirectorProposal(_Model):
    """Mode-independent tentative screen; no hidden commit or exposure write."""

    schema_version: Literal["director-proposal.v1"] = "director-proposal.v1"
    request_id: _Key
    input_digest: _Key
    candidate_digest: _Key
    constraints_digest: _Key
    direction_ref: _Key
    direction: str = Field(min_length=1, max_length=500)
    status: Literal["COMPLETE", "SHORTAGE", "UNSATISFIABLE"]
    requested_strategy: DirectorStrategy
    realized_strategy: Literal[
        "jev_recurrent", "agent_jev", "agent_only", "deterministic", "mixed", "none"
    ]
    tentative: Literal[True] = True
    target_count: int = Field(ge=1, le=20)
    card_refs: tuple[_Ref, ...]
    pairs: tuple[DirectorPair, ...]
    provider_calls: int = Field(ge=0, le=10)
    reason_code: _Key
    brief: ScreenBrief | None = None
    screen_review: ScreenReview | None = None
    plan_id: _Ref | None = None
    plan_revision: int | None = Field(default=None, ge=0)
    screen_id: _Ref | None = None
    screen_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        refs: tuple[str, ...] = ()
        for index, pair in enumerate(self.pairs):
            if pair.pair_index != index or pair.prefix_refs != refs:
                raise ValueError("director pair prefix mismatch")
            if len(pair.selected_refs) != 2 and index != len(self.pairs) - 1:
                raise ValueError("singleton is only allowed at screen tail")
            refs += pair.selected_refs
        if refs != self.card_refs or len(set(refs)) != len(refs):
            raise ValueError("director cards must match distinct pair selections")
        if self.status == "COMPLETE" and len(refs) != self.target_count:
            raise ValueError("complete proposal does not meet target")
        if self.status == "SHORTAGE" and len(refs) >= self.target_count:
            raise ValueError("shortage is not below target")
        if self.status == "UNSATISFIABLE" and refs:
            raise ValueError("unsatisfiable proposal contains cards")
        binding = (self.plan_id, self.plan_revision, self.screen_id, self.screen_revision)
        if any(value is None for value in binding) and not all(value is None for value in binding):
            raise ValueError("incomplete plan binding")
        if self.requested_strategy == "agent_only" and self.provider_calls:
            raise ValueError("pure Agent orchestration cannot call decision provider")
        if self.requested_strategy == "jev_recurrent" and self.plan_id is not None:
            raise ValueError("Jev recurrence cannot invent an Agent plan")
        return self


class DirectorResult(_Model):
    proposal: DirectorProposal
    horizon: HorizonPlan | None = None
    patches: tuple[DraftPatch, ...] = Field(default=(), max_length=3)
    horizon_traces: tuple[tuple[DirectorPair, ...], ...] = Field(default=(), max_length=3)
    replan_required: bool = False


def card_content_digest(card: CompositionCard) -> str:
    """Bind exact semantic content while allowing request-local ref/rank rebinding."""
    return digest(card.model_dump(mode="json", exclude={"ref", "baseline_score", "eligible"}))


def freeze_request(request: CompositionRequest) -> CompositionRequest:
    """Defensively detach nested values before crossing any model seam."""
    try:
        return CompositionRequest.model_validate(
            request.model_dump(mode="python", warnings="error")
        )
    except (ValueError, TypeError, OverflowError, AttributeError):
        raise ValueError("invalid composition request") from None
