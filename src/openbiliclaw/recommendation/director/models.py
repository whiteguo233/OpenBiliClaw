"""Versioned offline composition contracts, deliberately not a commit protocol."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .policy import PairRole, ScreenBrief, ScreenReview, pair_role_choices

COMPOSITION_SCHEMA_VERSION: Final = "pair-composition.v1"
PAIR_QUESTION_VERSION: Final = "director-choose-pair.v3"
PAIR_POLICY_VERSION: Final = "topic-quota-pairs.v1"

_Ref = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
_Key = Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
_Topic = Annotated[str, Field(min_length=1, max_length=80, pattern=r"\S")]
_Revision = Annotated[int, Field(ge=0, le=2**63 - 1)]


def canonical_json(value: object) -> str:
    """Serialize trace material deterministically; reject nonfinite numbers."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: object) -> str:
    """Hash a JSON-compatible snapshot without depending on insertion order."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class CompositionCard(_FrozenModel):
    """A copied shortlist card; text is untrusted data, never an instruction.

    ``item_key`` is the caller's canonical identity, not a bare platform ID.
    ``eligible`` carries upstream deterministic admission; this module cannot
    independently establish availability, freshness or scope.
    """

    ref: _Ref
    item_key: _Key
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=400)
    topic_group: _Topic
    content_style: str = Field(default="", max_length=80)
    source_platform: str = Field(default="", max_length=32)
    baseline_score: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    eligible: bool = True


class TopicQuota(_FrozenModel):
    """Hard counts for one mutually exclusive, caller-registered topic group."""

    topic_group: _Topic
    minimum: int = Field(default=0, ge=0, le=20)
    maximum: int = Field(default=20, ge=0, le=20)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("topic minimum exceeds maximum")
        return self


class ScreenConstraints(_FrozenModel):
    """Small exactly solvable constraint family, not all production guardrails."""

    version: _Key
    topic_quotas: tuple[TopicQuota, ...] = Field(default=(), max_length=40)
    excluded_item_keys: tuple[_Key, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({quota.topic_group for quota in self.topic_quotas}) != len(self.topic_quotas):
            raise ValueError("duplicate topic quota")
        if len(set(self.excluded_item_keys)) != len(self.excluded_item_keys):
            raise ValueError("duplicate excluded item key")
        return self


class FeedbackContent(_FrozenModel):
    """Immutable semantic identity of a past card; never a new candidate."""

    ref: _Ref
    title: str = Field(max_length=120)
    summary: str = Field(default="", max_length=160)
    topic_group: str = Field(default="", max_length=80)
    content_style: str = Field(default="", max_length=80)
    source_platform: str = Field(default="", max_length=32)


class ScreenAction(_FrozenModel):
    event_ref: _Key
    kind: Literal["click", "like", "dislike", "skip", "explicit_request"]
    note: str = Field(default="", max_length=256)
    occurred_at: float = Field(ge=0)


class ExperiencedCard(FeedbackContent):
    position: int = Field(ge=1, le=20)
    exposure_confirmed: bool = False
    exposure_at: float | None = Field(default=None, ge=0)
    actions: tuple[ScreenAction, ...] = Field(default=(), max_length=4)


class RecentScreen(_FrozenModel):
    batch_ref: _Key
    direction: str = Field(max_length=500)
    committed_at: float = Field(ge=0)
    cards: tuple[ExperiencedCard, ...] = Field(max_length=20)


class RealFeedback(_FrozenModel):
    """Caller-supplied actual event, never synthesized from a tentative pair.

    Offline callers must supply genuine persisted event/batch refs in real
    replays (synthetic fixtures are labelled as such). Production ingestion
    projects canonical events and explicit visible-subset acknowledgements;
    tentative plans and tool delivery never create exposure evidence.
    """

    event_ref: _Key
    batch_ref: _Key
    kind: Literal["presented", "click", "like", "dislike", "skip", "explicit_request"]
    description: str = Field(max_length=256)
    occurred_at: float = Field(default=0, ge=0)
    content: tuple[FeedbackContent, ...] = Field(default=(), max_length=20)


class CompositionSession(_FrozenModel):
    """Frozen authoritative context, separate from the in-screen draft prefix."""

    session_ref: _Key
    stream_ref: _Key
    state_revision: _Revision
    feedback_cursor: _Key
    configuration_revision: _Revision
    explicit_intent: str = Field(default="", max_length=500)
    real_feedback: tuple[RealFeedback, ...] = Field(default=(), max_length=8)
    profile_context: dict[str, JsonValue] = Field(default_factory=dict)
    fast_profile_context: dict[str, JsonValue] = Field(default_factory=dict)
    # Runtime-built views vary with the candidate query and available bytes.
    # Replanning follows underlying profile facts/policy, not selection churn.
    profile_source_digest: str = Field(default="", max_length=64)
    recent_screens: tuple[RecentScreen, ...] = Field(default=(), max_length=3)
    # Authoritative store aggregate, not an inference from the bounded prompt
    # history. None means an offline caller cannot prove full event coverage.
    latest_preference_revision: _Revision | None = None

    @property
    def profile_digest(self) -> str:
        return self.profile_source_digest or (
            digest(self.profile_context) if self.profile_context else ""
        )

    def prompt_context(self, *, fast: bool = False) -> dict[str, JsonValue]:
        """One projection used by all models; fast calls omit historical summaries."""
        data = self.model_dump(
            mode="json",
            exclude={"fast_profile_context", "latest_preference_revision", "profile_source_digest"},
        )
        data["context_schema_version"] = "director-context.v1"
        if fast:
            data["profile_context"] = self.fast_profile_context or self.profile_context
            for screen in data["recent_screens"]:
                for card in screen["cards"]:
                    card.pop("summary", None)
            for feedback in data["real_feedback"]:
                for card in feedback["content"]:
                    card.pop("summary", None)
        return data

    @model_validator(mode="after")
    def _unique_events(self) -> Self:
        if len({event.event_ref for event in self.real_feedback}) != len(self.real_feedback):
            raise ValueError("duplicate feedback event")
        if (
            self.latest_preference_revision is not None
            and self.latest_preference_revision > self.state_revision
        ):
            raise ValueError("preference watermark exceeds observed state")
        return self


class CompositionRequest(_FrozenModel):
    """Bounded snapshot for an offline proposal; confers no commit authority.

    Caps are engineering limits, not calibrated quality thresholds. The caller
    builds a representative shortlist before entering this module; passing a
    full pool is rejected, never silently truncated to its head.
    """

    schema_version: Literal["pair-composition.v1"] = COMPOSITION_SCHEMA_VERSION
    request_id: _Key
    session: CompositionSession
    candidate_set_ref: _Key
    candidate_revision: _Revision
    feature_schema_version: _Key
    direction_ref: _Key
    direction: str = Field(min_length=1, max_length=500)
    cards: tuple[CompositionCard, ...] = Field(max_length=40)
    constraints: ScreenConstraints
    target_count: int = Field(ge=1, le=20)
    # Screen-wide decision budget. No claim that a real provider meets it.
    decision_budget_seconds: float = Field(default=2.0, gt=0, le=60)
    max_provider_calls: int = Field(default=10, ge=0, le=10)
    deadline_monotonic: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _unique_cards(self) -> Self:
        if len({card.ref for card in self.cards}) != len(self.cards):
            raise ValueError("duplicate card ref")
        if len({card.item_key for card in self.cards}) != len(self.cards):
            raise ValueError("duplicate canonical item key")
        return self

    @property
    def input_digest(self) -> str:
        """Bind semantic snapshot and policy budgets, excluding wall-clock identity."""
        payload = self.model_dump(mode="json", exclude={"request_id", "deadline_monotonic"})
        if not self.session.profile_source_digest:
            # Legacy/offline callers did not have a source-identity field; an
            # empty default must not invalidate their exact transport golden.
            payload["session"].pop("profile_source_digest", None)
        return digest(payload)


class PairAlternative(_FrozenModel):
    """Ordered pair, or a singleton exclusively for a final odd tail."""

    option_id: _Ref
    card_refs: tuple[_Ref, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.card_refs)) != len(self.card_refs):
            raise ValueError("pair contains duplicate card")
        return self


class PairTrace(_FrozenModel):
    """Detached, immutable selection attribution; raw evidence is JSON, not mutable dicts."""

    pair_index: int = Field(ge=0)
    prefix_refs: tuple[_Ref, ...]
    alternatives: tuple[PairAlternative, ...] = Field(min_length=1, max_length=16)
    selected_refs: tuple[_Ref, ...] = Field(min_length=1, max_length=2)
    origin: Literal["decision", "deterministic"]
    reason_code: _Key
    role: PairRole = "open"
    intent: str = Field(default="", max_length=240)
    selected_option_id: str | None = Field(default=None, max_length=128)
    decision_request_id: str | None = None
    decision_input_digest: str | None = None
    evidence_json: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if len({option.option_id for option in self.alternatives}) != len(self.alternatives):
            raise ValueError("duplicate alternative ID")
        if any(option.option_id == "none_suitable" for option in self.alternatives):
            raise ValueError("reserved abstention alternative ID")
        if self.selected_refs not in tuple(option.card_refs for option in self.alternatives):
            raise ValueError("selected cards are not an offered alternative")
        if self.selected_option_id is not None:
            choice = pair_role_choices(self.alternatives).get(self.selected_option_id)
            if (
                choice is None
                or choice[0].card_refs != self.selected_refs
                or choice[1] != self.role
            ):
                raise ValueError("selected role must bind to the exact offered pair choice")
        elif self.role != "open":
            raise ValueError("an assigned role needs joint-choice attribution")
        if self.origin == "deterministic" and self.selected_option_id is not None:
            raise ValueError("deterministic fallback cannot claim a model role choice")
        if len(set(self.prefix_refs)) != len(self.prefix_refs):
            raise ValueError("duplicate prefix card")
        if any(set(self.prefix_refs) & set(option.card_refs) for option in self.alternatives):
            raise ValueError("alternative overlaps prefix")
        if (self.decision_request_id is None) != (self.decision_input_digest is None):
            raise ValueError("decision attribution must be complete")
        if self.evidence_json is not None and self.decision_request_id is None:
            raise ValueError("evidence needs request attribution")
        if self.origin == "decision" and self.evidence_json is None:
            raise ValueError("decision selection needs evidence")
        return self


class ScreenProposal(_FrozenModel):
    """Tentative result only: cannot be used as a BatchInstance or presentation ack."""

    schema_version: Literal["pair-composition.v1"] = COMPOSITION_SCHEMA_VERSION
    policy_version: Literal["topic-quota-pairs.v1"] = PAIR_POLICY_VERSION
    request_id: _Key
    input_digest: _Key
    candidate_digest: _Key
    constraints_digest: _Key
    status: Literal["COMPLETE", "SHORTAGE", "UNSATISFIABLE"]
    requested_strategy: Literal["jev_recurrent"] = "jev_recurrent"
    realized_strategy: Literal["jev_recurrent", "deterministic", "mixed", "none"]
    tentative: Literal[True] = True
    target_count: int = Field(ge=1, le=20)
    card_refs: tuple[_Ref, ...]
    pairs: tuple[PairTrace, ...]
    provider_calls: int = Field(ge=0, le=10)
    reason_code: _Key
    brief: ScreenBrief | None = None
    screen_review: ScreenReview | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        refs: tuple[str, ...] = ()
        for index, pair in enumerate(self.pairs):
            if pair.pair_index != index or pair.prefix_refs != refs:
                raise ValueError("pair trace order or prefix mismatch")
            if len(pair.selected_refs) != 2 and index != len(self.pairs) - 1:
                raise ValueError("singleton is only allowed at the tail")
            refs += pair.selected_refs
        if refs != self.card_refs or len(set(refs)) != len(refs):
            raise ValueError("screen refs must equal distinct pair selections")
        if self.status == "COMPLETE" and len(refs) != self.target_count:
            raise ValueError("complete proposal must meet target count")
        if self.status == "SHORTAGE" and len(refs) >= self.target_count:
            raise ValueError("shortage proposal must be below target count")
        if self.status == "UNSATISFIABLE" and (refs or self.provider_calls):
            raise ValueError("unsatisfiable proposal cannot contain cards or provider calls")
        origins = {pair.origin for pair in self.pairs}
        expected = (
            "none"
            if not origins
            else "mixed"
            if len(origins) > 1
            else "jev_recurrent"
            if "decision" in origins
            else "deterministic"
        )
        if self.realized_strategy != expected:
            raise ValueError("realized strategy does not match pair origins")
        if self.provider_calls < sum(pair.origin == "decision" for pair in self.pairs):
            raise ValueError("decision selections exceed provider calls")
        return self
