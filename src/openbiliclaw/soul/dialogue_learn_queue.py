"""Single-consumer in-memory queue for dialogue settlement jobs.

The module keeps the historical filename while Wave 1 widens the learn-only
queue into a typed queue. Admission is synchronous: sequence allocation,
anchor reservation/snapshot capture, and ``put_nowait`` happen in one
event-loop turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast
from uuid import uuid4

from openbiliclaw.soul.dialogue_settlement_guard import (
    DialogueSettlementGuard,
    DialogueSettlementWorkerPermit,
    default_dialogue_settlement_guard,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_QUEUE_DEPTH_WARN = 10
_QUEUE_MAX_DEPTH = 1000


class DialogueJobKind(StrEnum):
    """Closed dispatcher kind set from the finalized Wave 1 contract."""

    LEARN = "learn"
    SETTLE_HYPOTHESIS = "settle.hypothesis"
    SETTLE_CONFUSION = "settle.confusion"
    CARD_DEFER = "card.defer"
    CARD_DISCUSS = "card.discuss"
    CARD_RECONCILE = "card.reconcile"
    ANCHOR_ESTABLISH = "anchor.establish"
    PROBE_REPLY_APPLY = "probe.reply.apply"
    CONFUSION_REPLY_APPLY = "confusion.reply.apply"
    CONFUSION_ATTRIBUTION_REPLAY = "confusion.attribution.replay"
    CONFUSION_OPEN_SYNC = "confusion.open.sync"


class AnchorTransitionPolicy(StrEnum):
    """Whether a job kind establishes an anchor at admission."""

    NEVER = "never"
    ALWAYS = "always"
    WHEN_NEEDS_ANCHOR = "when_needs_anchor"


ANCHOR_TRANSITION_POLICY: dict[DialogueJobKind, AnchorTransitionPolicy] = {
    DialogueJobKind.LEARN: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.SETTLE_HYPOTHESIS: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.SETTLE_CONFUSION: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.CARD_DEFER: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.CARD_DISCUSS: AnchorTransitionPolicy.ALWAYS,
    DialogueJobKind.CARD_RECONCILE: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.ANCHOR_ESTABLISH: AnchorTransitionPolicy.ALWAYS,
    DialogueJobKind.PROBE_REPLY_APPLY: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.CONFUSION_REPLY_APPLY: AnchorTransitionPolicy.NEVER,
    DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY: (AnchorTransitionPolicy.WHEN_NEEDS_ANCHOR),
    DialogueJobKind.CONFUSION_OPEN_SYNC: AnchorTransitionPolicy.NEVER,
}

ANCHOR_ESTABLISH_PRODUCER_SOURCES = frozenset(
    {
        "pending_probe_throw",
        "pending_confusion_throw",
        "durable_confusion_ensure",
    }
)


class DialogueSettlementQueueError(RuntimeError):
    """Base error for typed dialogue settlement queue contract violations."""


class DialogueSettlementQueueClosedError(DialogueSettlementQueueError):
    """Raised when a completion job cannot be admitted."""


class DialogueSettlementReentryError(DialogueSettlementQueueError):
    """Raised when the worker tries to wait for a job behind itself."""


class AnchorAdmissionError(DialogueSettlementQueueError):
    """Raised when a job cannot be classified into a safe anchor transition."""


class AnchorReservationResolutionError(DialogueSettlementQueueError):
    """Raised on a non-owner, duplicate, or otherwise invalid resolution."""


class AnchorReservationPendingError(DialogueSettlementQueueError):
    """Raised if FIFO dispatch observes an unresolved dependency reservation."""


@dataclass(frozen=True, slots=True)
class AnchorPersisted:
    """Exact durable anchor generation visible at admission or resolution."""

    kind: str
    ref: str
    generation: int
    resolved_by_reservation_id: str | None = None
    terminal_disposition: str | None = None
    state: Literal["persisted"] = field(default="persisted", init=False)

    def __post_init__(self) -> None:
        if not self.kind or not self.ref or self.generation <= 0:
            raise ValueError("Persisted anchor requires kind/ref and a positive generation")


@dataclass(frozen=True, slots=True)
class AnchorAbsent:
    """Admission tombstone proving a target had no anchor at acceptance."""

    target_kind: str
    target_ref: str
    tombstone_epoch: int
    resolved_by_reservation_id: str | None = None
    terminal_disposition: str | None = None
    state: Literal["absent"] = field(default="absent", init=False)

    def __post_init__(self) -> None:
        if not self.target_kind or not self.target_ref or self.tombstone_epoch <= 0:
            raise ValueError("Absent anchor requires target kind/ref and a positive epoch")


@dataclass(frozen=True, slots=True)
class AnchorReserved:
    """Owner-bound reservation made visible before its builder is enqueued."""

    kind: str
    ref: str
    reservation_id: str
    owner_job_id: str
    owner_sequence: int
    producer_kind: str
    origin: str
    state: Literal["reserved"] = field(default="reserved", init=False)


@dataclass(frozen=True, slots=True)
class AnchorFailed:
    """Resolved failure visible only to jobs admitted before that failure."""

    kind: str
    ref: str
    reservation_id: str
    cause: str
    actual_state: AnchorPersisted | AnchorAbsent
    state: Literal["failed"] = field(default="failed", init=False)


@dataclass(frozen=True, slots=True)
class AnchorNotApplicable:
    """Explicitly marks a job that has no anchor relationship."""

    state: Literal["not_applicable"] = field(default="not_applicable", init=False)


AnchorActualState: TypeAlias = AnchorPersisted | AnchorAbsent
AnchorAdmissionSnapshot: TypeAlias = (
    AnchorPersisted | AnchorAbsent | AnchorReserved | AnchorFailed | AnchorNotApplicable
)
_AnchorHead: TypeAlias = AnchorPersisted | AnchorAbsent | AnchorReserved
ANCHOR_NOT_APPLICABLE = AnchorNotApplicable()


class AnchorMutationDisposition(StrEnum):
    """Every terminal outcome of an anchor-building mutation."""

    PERSISTED = "persisted"
    ABSENT = "absent"
    ALREADY_TERMINAL = "already_terminal"
    NO_OP = "no_op"
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AnchorMutationTerminal:
    """Typed authoritative post-state returned by an anchor mutator."""

    disposition: AnchorMutationDisposition
    actual_state: AnchorActualState
    cause: str = ""

    def __post_init__(self) -> None:
        if self.disposition is AnchorMutationDisposition.PERSISTED and not isinstance(
            self.actual_state, AnchorPersisted
        ):
            raise ValueError("persisted terminal requires a persisted actual state")
        if self.disposition is AnchorMutationDisposition.ABSENT and not isinstance(
            self.actual_state, AnchorAbsent
        ):
            raise ValueError("absent terminal requires an absent actual state")
        if self.disposition is AnchorMutationDisposition.FAILED and not self.cause:
            raise ValueError("failed terminal requires a cause")

    @classmethod
    def persisted(
        cls,
        *,
        kind: str,
        ref: str,
        generation: int,
    ) -> AnchorMutationTerminal:
        return cls(
            AnchorMutationDisposition.PERSISTED,
            AnchorPersisted(kind=kind, ref=ref, generation=generation),
        )

    @classmethod
    def absent(
        cls,
        *,
        target_kind: str,
        target_ref: str,
        tombstone_epoch: int = 1,
    ) -> AnchorMutationTerminal:
        return cls(
            AnchorMutationDisposition.ABSENT,
            AnchorAbsent(
                target_kind=target_kind,
                target_ref=target_ref,
                tombstone_epoch=tombstone_epoch,
            ),
        )

    @classmethod
    def already_terminal(
        cls,
        actual_state: AnchorActualState,
    ) -> AnchorMutationTerminal:
        return cls(AnchorMutationDisposition.ALREADY_TERMINAL, actual_state)

    @classmethod
    def no_op(cls, actual_state: AnchorActualState) -> AnchorMutationTerminal:
        return cls(AnchorMutationDisposition.NO_OP, actual_state)

    @classmethod
    def superseded(cls, actual_state: AnchorActualState) -> AnchorMutationTerminal:
        return cls(AnchorMutationDisposition.SUPERSEDED, actual_state)

    @classmethod
    def failed(
        cls,
        actual_state: AnchorActualState,
        *,
        cause: str,
    ) -> AnchorMutationTerminal:
        return cls(AnchorMutationDisposition.FAILED, actual_state, cause=cause)


@dataclass(frozen=True, slots=True)
class AnchorTransition:
    """Admission-time transition derived exhaustively from kind and payload."""

    action: Literal["none", "establish"]
    target_kind: str = ""
    target_ref: str = ""
    origin: str = ""

    @property
    def establishes_anchor(self) -> bool:
        return self.action == "establish"


@dataclass(frozen=True, slots=True)
class ExplorationIntent:
    """Immutable handoff data reserved for the Wave 3 probe dispatcher."""

    domain: str
    source_event: str
    specifics: tuple[str, ...]
    evidence_id: str


@dataclass(frozen=True, slots=True)
class DialogueJobResult:
    """Typed completion returned to request/response submitters."""

    outcome: str
    settlement: Mapping[str, object] | None = None
    classification: str | None = None
    classifier: str | None = None
    resulting_action: str | None = None
    exploration_intent: ExplorationIntent | None = None


_Followup: TypeAlias = "Callable[[], Awaitable[DialogueJobResult | None]]"


@dataclass(frozen=True, slots=True)
class DialogueDispatchResult:
    """Dispatcher return with an optional post-resolution follow-up.

    For builders, ``anchor_terminal`` is resolved synchronously as soon as the
    dispatcher await returns.  Only then may ``followup`` be awaited.
    """

    result: DialogueJobResult
    anchor_terminal: AnchorMutationTerminal | None = None
    followup: _Followup | None = None


@dataclass(frozen=True, slots=True)
class DialogueJob:
    """Immutable in-memory envelope admitted to the single queue."""

    job_id: str
    kind: DialogueJobKind
    payload: Mapping[str, object]
    anchor_snapshot: AnchorAdmissionSnapshot
    anchor_transition: AnchorTransition
    owned_anchor_reservation_id: str | None
    accepted_at: float
    sequence: int
    completion: asyncio.Future[DialogueJobResult] | None
    effective_anchor_snapshot: AnchorAdmissionSnapshot | None = None


DialogueDispatchReturn: TypeAlias = (
    DialogueJobResult | DialogueDispatchResult | AnchorMutationTerminal | None
)


class DialogueDispatcher(Protocol):
    """Typed callable installed once by the runtime."""

    def __call__(self, job: DialogueJob, /) -> Awaitable[DialogueDispatchReturn]: ...


@dataclass(slots=True)
class _ReservationEntry:
    reservation: AnchorReserved
    reference_count: int = 0
    terminal: AnchorMutationTerminal | None = None
    effective_state: AnchorActualState | None = None
    resolution_count: int = 0


@dataclass(slots=True)
class _WorkerExecutionScope:
    """Fail-closed lineage marker shared with children of one worker job."""

    active: bool = True


def anchor_snapshot_as_mapping(snapshot: AnchorAdmissionSnapshot) -> dict[str, object]:
    """Return a stable mapping used by compatibility handlers and diagnostics."""
    if isinstance(snapshot, AnchorPersisted):
        return {
            "state": snapshot.state,
            "kind": snapshot.kind,
            "ref": snapshot.ref,
            "generation": snapshot.generation,
            "resolved_by_reservation_id": snapshot.resolved_by_reservation_id,
            "terminal_disposition": snapshot.terminal_disposition,
        }
    if isinstance(snapshot, AnchorAbsent):
        return {
            "state": snapshot.state,
            "target_kind": snapshot.target_kind,
            "target_ref": snapshot.target_ref,
            "tombstone_epoch": snapshot.tombstone_epoch,
            "resolved_by_reservation_id": snapshot.resolved_by_reservation_id,
            "terminal_disposition": snapshot.terminal_disposition,
        }
    if isinstance(snapshot, AnchorReserved):
        return {
            "state": snapshot.state,
            "kind": snapshot.kind,
            "ref": snapshot.ref,
            "reservation_id": snapshot.reservation_id,
            "owner_job_id": snapshot.owner_job_id,
            "owner_sequence": snapshot.owner_sequence,
            "producer_kind": snapshot.producer_kind,
            "origin": snapshot.origin,
        }
    if isinstance(snapshot, AnchorFailed):
        return {
            "state": snapshot.state,
            "kind": snapshot.kind,
            "ref": snapshot.ref,
            "reservation_id": snapshot.reservation_id,
            "cause": snapshot.cause,
            "actual_state": anchor_snapshot_as_mapping(snapshot.actual_state),
        }
    return {"state": snapshot.state}


def anchor_transition_as_mapping(transition: AnchorTransition) -> dict[str, object]:
    """Return transition diagnostics without exposing mutable registry state."""
    return {
        "action": transition.action,
        "target_kind": transition.target_kind,
        "target_ref": transition.target_ref,
        "origin": transition.origin,
    }


class AnchorAdmissionRegistry:
    """Queue-global logical anchor timeline and reservation ownership registry."""

    def __init__(
        self,
        anchor_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._anchor_provider = anchor_provider
        self._heads: dict[tuple[str, str], _AnchorHead] = {}
        self._entries: dict[str, _ReservationEntry] = {}
        self._latest_head_key: tuple[str, str] | None = None
        self._tombstone_epoch = 0

    def reserve(
        self,
        *,
        kind: str,
        ref: str,
        owner_job_id: str,
        owner_sequence: int,
        producer_kind: str,
        origin: str,
    ) -> AnchorReserved:
        """Create a fresh owner entry and make it the latest same-ref head."""
        if not kind or not ref:
            raise AnchorAdmissionError("Anchor builders require target_kind and target_ref")
        reservation = AnchorReserved(
            kind=kind,
            ref=ref,
            reservation_id=uuid4().hex,
            owner_job_id=owner_job_id,
            owner_sequence=owner_sequence,
            producer_kind=producer_kind,
            origin=origin,
        )
        self._entries[reservation.reservation_id] = _ReservationEntry(reservation)
        key = (kind, ref)
        self._heads[key] = reservation
        self._latest_head_key = key
        return reservation

    def snapshot(
        self,
        *,
        target_kind: str = "",
        target_ref: str = "",
    ) -> AnchorAdmissionSnapshot:
        """Freeze and retain the current logical state for one admitted job."""
        if bool(target_kind) != bool(target_ref):
            raise AnchorAdmissionError(
                "target_kind and target_ref must either both be present or both be absent"
            )
        if target_kind:
            key = (target_kind, target_ref)
            state = self._heads.get(key)
            if state is None:
                state = self.actual_state(target_kind=target_kind, target_ref=target_ref)
                self._heads[key] = state
            self._latest_head_key = key
            self._retain(state)
            return state
        if self._latest_head_key is not None:
            state = self._heads[self._latest_head_key]
            self._retain(state)
            return state
        active = self._read_provider_active()
        if active is None:
            return ANCHOR_NOT_APPLICABLE
        key = (active.kind, active.ref)
        self._heads[key] = active
        self._latest_head_key = key
        return active

    def peek(
        self,
        *,
        target_kind: str = "",
        target_ref: str = "",
    ) -> AnchorAdmissionSnapshot:
        """Read the logical admission head without retaining or mutating it.

        Context validation and refresh recovery use this method.  In
        particular, a GET must not create a queue reference, tombstone, or
        reservation merely by looking at the current target.
        """
        if bool(target_kind) != bool(target_ref):
            raise AnchorAdmissionError(
                "target_kind and target_ref must either both be present or both be absent"
            )
        if target_kind:
            state = self._heads.get((target_kind, target_ref))
            return (
                state
                if state is not None
                else self.actual_state(
                    target_kind=target_kind,
                    target_ref=target_ref,
                )
            )
        if self._latest_head_key is not None:
            state = self._heads.get(self._latest_head_key)
            if state is not None:
                return state
        active = self._read_provider_active()
        return active if active is not None else ANCHOR_NOT_APPLICABLE

    def resolve_owned(
        self,
        *,
        ref: str,
        reservation_id: str,
        owner_job_id: str,
        owner_sequence: int,
        terminal: AnchorMutationTerminal,
    ) -> AnchorActualState:
        """CAS one exact owner entry from reserved to its typed terminal state."""
        entry = self._entries.get(reservation_id)
        if entry is None:
            raise AnchorReservationResolutionError("Unknown anchor reservation")
        reservation = entry.reservation
        if (
            reservation.ref != ref
            or reservation.owner_job_id != owner_job_id
            or reservation.owner_sequence != owner_sequence
        ):
            raise AnchorReservationResolutionError(
                "Anchor reservation can only be resolved by its exact owner"
            )
        if entry.terminal is not None:
            raise AnchorReservationResolutionError("Anchor reservation has already been resolved")
        self._validate_actual_state(reservation, terminal.actual_state)
        effective = self._resolved_effective_state(reservation, terminal)
        entry.terminal = terminal
        entry.effective_state = effective
        entry.resolution_count += 1

        key = (reservation.kind, reservation.ref)
        head = self._heads.get(key)
        if isinstance(head, AnchorReserved) and head.reservation_id == reservation_id:
            self._heads[key] = effective
        self._gc_if_unreferenced(entry)
        return effective

    def resolve_for_dispatch(
        self,
        snapshot: AnchorAdmissionSnapshot,
        *,
        owner_reservation_id: str | None,
    ) -> AnchorAdmissionSnapshot:
        """Resolve an admitted reservation without changing its frozen snapshot."""
        if not isinstance(snapshot, AnchorReserved):
            return snapshot
        if owner_reservation_id == snapshot.reservation_id:
            return snapshot
        entry = self._entries.get(snapshot.reservation_id)
        if entry is None or entry.terminal is None or entry.effective_state is None:
            raise AnchorReservationPendingError(
                "Anchor dependency reached dispatch before its builder resolved"
            )
        if entry.terminal.disposition is AnchorMutationDisposition.FAILED:
            return AnchorFailed(
                kind=snapshot.kind,
                ref=snapshot.ref,
                reservation_id=snapshot.reservation_id,
                cause=entry.terminal.cause,
                actual_state=entry.effective_state,
            )
        return entry.effective_state

    def release(self, snapshot: AnchorAdmissionSnapshot) -> None:
        """Release one admitted envelope's reservation reference."""
        if not isinstance(snapshot, AnchorReserved):
            return
        entry = self._entries.get(snapshot.reservation_id)
        if entry is None:
            raise AnchorReservationResolutionError(
                "Reservation reference outlived its registry entry"
            )
        if entry.reference_count <= 0:
            raise AnchorReservationResolutionError("Reservation reference underflow")
        entry.reference_count -= 1
        self._gc_if_unreferenced(entry)

    def actual_state(self, *, target_kind: str, target_ref: str) -> AnchorActualState:
        """Read authoritative persisted/absent state for failure head advancement."""
        active = self._read_provider_active()
        if active is not None and active.kind == target_kind and active.ref == target_ref:
            return active
        return self._new_absent(target_kind=target_kind, target_ref=target_ref)

    def head(self, *, target_kind: str, target_ref: str) -> AnchorAdmissionSnapshot:
        """Return the current logical head for deterministic tests/diagnostics."""
        state = self._heads.get((target_kind, target_ref))
        if state is None:
            state = self.actual_state(target_kind=target_kind, target_ref=target_ref)
        return state

    def refresh_after_dispatch(
        self,
        *,
        target_kind: str,
        target_ref: str,
        completed_sequence: int,
    ) -> None:
        """Publish a completed dispatch mutation without erasing later admission.

        A settlement, relation, or builder follow-up may release/change the
        active anchor. Refreshing after its handler returns keeps future
        admission aligned with persistence. A reservation admitted later in
        the same event-loop timeline remains the logical head.
        """
        if not target_kind or not target_ref:
            return
        key = (target_kind, target_ref)
        head = self._heads.get(key)
        if isinstance(head, AnchorReserved) and head.owner_sequence > completed_sequence:
            return
        latest = (
            self._heads.get(self._latest_head_key) if self._latest_head_key is not None else None
        )
        preserve_later_global_head = (
            self._latest_head_key != key
            and isinstance(latest, AnchorReserved)
            and latest.owner_sequence > completed_sequence
        )
        actual = self.actual_state(
            target_kind=target_kind,
            target_ref=target_ref,
        )
        if (
            isinstance(head, AnchorPersisted)
            and isinstance(actual, AnchorPersisted)
            and (head.kind, head.ref, head.generation)
            == (actual.kind, actual.ref, actual.generation)
        ):
            return
        if isinstance(head, AnchorAbsent) and isinstance(actual, AnchorAbsent):
            return
        self._heads[key] = actual
        if not preserve_later_global_head:
            self._latest_head_key = key

    def has_reservation(self, reservation_id: str) -> bool:
        return reservation_id in self._entries

    def reservation_reference_count(self, reservation_id: str) -> int:
        entry = self._entry(reservation_id)
        return entry.reference_count

    def reservation_resolution_count(self, reservation_id: str) -> int:
        entry = self._entry(reservation_id)
        return entry.resolution_count

    def reservation_terminal(
        self,
        reservation_id: str,
    ) -> AnchorMutationTerminal | None:
        return self._entry(reservation_id).terminal

    def clear(self) -> None:
        """Discard all process-local state on queue shutdown."""
        self._heads.clear()
        self._entries.clear()
        self._latest_head_key = None

    def _entry(self, reservation_id: str) -> _ReservationEntry:
        try:
            return self._entries[reservation_id]
        except KeyError as exc:
            raise AnchorReservationResolutionError(
                f"Unknown anchor reservation {reservation_id}"
            ) from exc

    def _retain(self, state: _AnchorHead) -> None:
        if isinstance(state, AnchorReserved):
            self._entry(state.reservation_id).reference_count += 1

    def _gc_if_unreferenced(self, entry: _ReservationEntry) -> None:
        if entry.terminal is None or entry.reference_count != 0:
            return
        reservation_id = entry.reservation.reservation_id
        self._entries.pop(reservation_id, None)

    def _resolved_effective_state(
        self,
        reservation: AnchorReserved,
        terminal: AnchorMutationTerminal,
    ) -> AnchorActualState:
        disposition = terminal.disposition.value
        actual = terminal.actual_state
        if isinstance(actual, AnchorPersisted):
            return AnchorPersisted(
                kind=actual.kind,
                ref=actual.ref,
                generation=actual.generation,
                resolved_by_reservation_id=reservation.reservation_id,
                terminal_disposition=disposition,
            )
        return AnchorAbsent(
            target_kind=actual.target_kind,
            target_ref=actual.target_ref,
            tombstone_epoch=self._next_tombstone_epoch(),
            resolved_by_reservation_id=reservation.reservation_id,
            terminal_disposition=disposition,
        )

    @staticmethod
    def _validate_actual_state(
        reservation: AnchorReserved,
        actual: AnchorActualState,
    ) -> None:
        if isinstance(actual, AnchorPersisted):
            matches = actual.kind == reservation.kind and actual.ref == reservation.ref
        else:
            matches = (
                actual.target_kind == reservation.kind and actual.target_ref == reservation.ref
            )
        if not matches:
            raise AnchorReservationResolutionError(
                "Anchor terminal post-state does not match its reservation target"
            )

    def _read_provider_active(self) -> AnchorPersisted | None:
        if self._anchor_provider is None:
            return None
        raw = self._anchor_provider()
        kind = str(
            raw.get("anchor_kind") or raw.get("target_kind") or raw.get("kind") or "hypothesis"
        ).strip()
        ref = str(raw.get("anchor_ref") or raw.get("ref") or "").strip()
        generation_raw = raw.get("anchor_generation", raw.get("generation", 0))
        if isinstance(generation_raw, bool):
            return None
        try:
            generation = int(cast("int | str", generation_raw))
        except (TypeError, ValueError):
            logger.warning("Invalid dialogue anchor generation in snapshot; using absent")
            return None
        if not kind or not ref or generation <= 0:
            return None
        return AnchorPersisted(kind=kind, ref=ref, generation=generation)

    def _new_absent(self, *, target_kind: str, target_ref: str) -> AnchorAbsent:
        return AnchorAbsent(
            target_kind=target_kind,
            target_ref=target_ref,
            tombstone_epoch=self._next_tombstone_epoch(),
        )

    def _next_tombstone_epoch(self) -> int:
        self._tombstone_epoch += 1
        return self._tombstone_epoch


def _classify_anchor_transition(
    kind: DialogueJobKind,
    payload: Mapping[str, object],
) -> AnchorTransition:
    policy = ANCHOR_TRANSITION_POLICY[kind]
    establishes = policy is AnchorTransitionPolicy.ALWAYS or (
        policy is AnchorTransitionPolicy.WHEN_NEEDS_ANCHOR and payload.get("needs_anchor") is True
    )
    if not establishes:
        return AnchorTransition(action="none")
    target_kind = str(payload.get("target_kind", "")).strip()
    target_ref = str(payload.get("target_ref", "")).strip()
    if not target_kind or not target_ref:
        raise AnchorAdmissionError(
            f"{kind.value} requires target_kind and target_ref before enqueue"
        )
    origin = str(payload.get("producer_source", "")).strip()
    if not origin:
        if kind is DialogueJobKind.CARD_DISCUSS:
            origin = "card_action"
        elif kind is DialogueJobKind.CONFUSION_ATTRIBUTION_REPLAY:
            origin = "cognition_cycle"
        else:
            raise AnchorAdmissionError("anchor.establish requires an explicit producer_source")
    if kind is DialogueJobKind.ANCHOR_ESTABLISH and origin not in ANCHOR_ESTABLISH_PRODUCER_SOURCES:
        allowed = ", ".join(sorted(ANCHOR_ESTABLISH_PRODUCER_SOURCES))
        raise AnchorAdmissionError(f"anchor.establish producer_source must be one of: {allowed}")
    return AnchorTransition(
        action="establish",
        target_kind=target_kind,
        target_ref=target_ref,
        origin=origin,
    )


class DialogueSettlementQueue:
    """Own one typed asyncio queue, one consumer, and one worker permit."""

    def __init__(
        self,
        dispatcher: DialogueDispatcher,
        *,
        name: str = "dialogue_settlement_worker",
        anchor_provider: Callable[[], Mapping[str, object]] | None = None,
        guard: DialogueSettlementGuard | None = None,
        max_depth: int = _QUEUE_MAX_DEPTH,
    ) -> None:
        self._dispatcher = dispatcher
        self._name = name
        self._guard = guard or default_dialogue_settlement_guard()
        self._registry = AnchorAdmissionRegistry(anchor_provider)
        self._queue: asyncio.Queue[DialogueJob] = asyncio.Queue()
        self._max_depth = max(1, int(max_depth))
        self._dropped_jobs = 0
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task[None] | None = None
        self._active_job: DialogueJob | None = None
        self._worker_permit: DialogueSettlementWorkerPermit | None = None
        self._accepting = True
        self._resume = asyncio.Event()
        self._resume.set()
        self._closed = False
        self._next_sequence = 1
        self._started = asyncio.Event()
        self._startup_error: BaseException | None = None
        self._execution_scope: ContextVar[_WorkerExecutionScope | None] = ContextVar(
            f"dialogue_settlement_execution_scope_{id(self)}",
            default=None,
        )

    @property
    def registry(self) -> AnchorAdmissionRegistry:
        return self._registry

    def peek_anchor(
        self,
        *,
        target_kind: str = "",
        target_ref: str = "",
    ) -> AnchorAdmissionSnapshot:
        """Expose the registry's read-only context-validation peek."""
        return self._registry.peek(target_kind=target_kind, target_ref=target_ref)

    @property
    def worker_alive(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def worker_task(self) -> asyncio.Task[None] | None:
        return self._worker

    @property
    def worker_permit(self) -> DialogueSettlementWorkerPermit | None:
        return self._worker_permit

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_jobs(self) -> int:
        """Number of low-priority background jobs dropped by the bound."""
        return self._dropped_jobs

    @property
    def max_depth(self) -> int:
        return self._max_depth

    @property
    def accepting(self) -> bool:
        """Whether external producers may submit new settlement work."""
        return self._accepting and not self._closed

    @property
    def ready_for_interactive_submission(self) -> bool:
        """Whether a short user command can run without waiting behind LLM work."""
        return self.accepting and self._active_job is None and self._queue.empty()

    def require_dialogue_settlement_worker(self) -> None:
        """Require the permit owned by this queue's actual worker Task."""
        self._guard.require_dialogue_settlement_worker()

    def start(self) -> None:
        """Create and synchronously authorize the actual worker Task."""
        if self._closed:
            raise DialogueSettlementQueueClosedError("DialogueSettlementQueue is closed")
        if self.worker_alive:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._worker = None
            return
        if self._event_loop is not None and self._event_loop is not loop:
            if not self._queue.empty():
                raise RuntimeError("Cannot move a non-empty dialogue queue across event loops")
            self._queue = asyncio.Queue()
            self._resume = asyncio.Event()
            self._resume.set()
        self._event_loop = loop
        self._started = asyncio.Event()
        self._startup_error = None
        worker = asyncio.create_task(self._run(), name=self._name)
        worker.add_done_callback(self._consume_worker_exception)
        self._worker = worker
        try:
            permit = self._guard.register_worker(cast("asyncio.Task[object]", worker))
        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            worker.cancel()
            self._worker = None
            raise
        self._worker_permit = permit
        self._started.set()

    def submit(
        self,
        kind: DialogueJobKind | str,
        payload: Mapping[str, object],
        *,
        completion: bool = False,
        _server_frozen_anchor_snapshot: AnchorAdmissionSnapshot | None = None,
    ) -> DialogueJob | None:
        """Atomically classify, snapshot, and enqueue one immutable envelope."""
        self._reject_worker_lineage_reentry()
        parsed_kind = DialogueJobKind(kind)
        if self._closed or not self._accepting:
            logger.warning(
                "DialogueSettlementQueue not accepting; dropped job (kind=%s, closed=%s)",
                parsed_kind.value,
                self._closed,
            )
            return None
        loop = asyncio.get_running_loop()
        if not self.worker_alive:
            self.start()

        sequence = self._next_sequence
        self._next_sequence += 1
        job_id = uuid4().hex
        copied_payload = MappingProxyType(copy.deepcopy(dict(payload)))
        transition = _classify_anchor_transition(parsed_kind, copied_payload)
        owned_reservation_id: str | None = None
        anchor_snapshot: AnchorAdmissionSnapshot
        if _server_frozen_anchor_snapshot is not None:
            if parsed_kind is not DialogueJobKind.LEARN:
                raise AnchorAdmissionError(
                    "server frozen anchor snapshots are only valid for LEARN"
                )
            if not isinstance(
                _server_frozen_anchor_snapshot,
                (AnchorPersisted, AnchorNotApplicable),
            ):
                raise AnchorAdmissionError(
                    "server frozen LEARN snapshot must be persisted or not applicable"
                )
            raw_binding = copied_payload.get("dialogue_binding")
            if not isinstance(raw_binding, Mapping):
                raise AnchorAdmissionError("server frozen LEARN requires a parsed dialogue binding")
            from openbiliclaw.soul.dialogue_turn_context import DialogueTurnBinding

            binding = DialogueTurnBinding.from_mapping(raw_binding)
            if binding.mode.value == "bound":
                context = binding.context
                if context is None or not isinstance(
                    _server_frozen_anchor_snapshot,
                    AnchorPersisted,
                ):
                    raise AnchorAdmissionError(
                        "bound LEARN binding requires a persisted anchor snapshot"
                    )
                if (
                    _server_frozen_anchor_snapshot.kind != context.kind
                    or _server_frozen_anchor_snapshot.ref != context.ref
                    or _server_frozen_anchor_snapshot.generation != context.generation
                ):
                    raise AnchorAdmissionError(
                        "frozen LEARN snapshot conflicts with dialogue binding"
                    )
            elif not isinstance(_server_frozen_anchor_snapshot, AnchorNotApplicable):
                raise AnchorAdmissionError(
                    "ordinary/detached LEARN bindings require not-applicable snapshot"
                )
            anchor_snapshot = _server_frozen_anchor_snapshot
        elif transition.establishes_anchor:
            reservation = self._registry.reserve(
                kind=transition.target_kind,
                ref=transition.target_ref,
                owner_job_id=job_id,
                owner_sequence=sequence,
                producer_kind=parsed_kind.value,
                origin=transition.origin,
            )
            owned_reservation_id = reservation.reservation_id
            anchor_snapshot = self._registry.snapshot(
                target_kind=transition.target_kind,
                target_ref=transition.target_ref,
            )
        else:
            target_kind = str(copied_payload.get("target_kind", "")).strip()
            target_ref = str(copied_payload.get("target_ref", "")).strip()
            anchor_snapshot = self._registry.snapshot(
                target_kind=target_kind,
                target_ref=target_ref,
            )
        completion_future = loop.create_future() if completion else None
        job = DialogueJob(
            job_id=job_id,
            kind=parsed_kind,
            payload=copied_payload,
            anchor_snapshot=anchor_snapshot,
            anchor_transition=transition,
            owned_anchor_reservation_id=owned_reservation_id,
            accepted_at=loop.time(),
            sequence=sequence,
            completion=completion_future,
        )
        if self._queue.qsize() >= self._max_depth and not completion:
            self._dropped_jobs += 1
            logger.warning(
                "DialogueSettlementQueue reached max depth=%d; dropping "
                "non-completion background job (kind=%s)",
                self._max_depth,
                parsed_kind.value,
            )
            return None
        self._queue.put_nowait(job)
        depth = self._queue.qsize()
        if depth >= _QUEUE_DEPTH_WARN:
            logger.warning(
                "DialogueSettlementQueue depth=%d — settlement is falling behind.",
                depth,
            )
        return job

    async def submit_and_wait(
        self,
        kind: DialogueJobKind | str,
        payload: Mapping[str, object],
    ) -> DialogueJobResult:
        """Submit externally and fail fast on worker-lineage self-reentry."""
        job = self.submit(kind, payload, completion=True)
        if job is None or job.completion is None:
            raise DialogueSettlementQueueClosedError(
                "Dialogue settlement queue is not accepting completion jobs"
            )
        return await asyncio.shield(job.completion)

    def _reject_worker_lineage_reentry(self) -> None:
        """Reject active and stale children instead of self-queuing worker work."""
        scope = self._execution_scope.get()
        if scope is None and asyncio.current_task() is not self._worker:
            return
        lifecycle = "active" if scope is None or scope.active else "completed"
        raise DialogueSettlementReentryError(
            "Dialogue settlement worker lineage cannot submit to its own queue "
            f"({lifecycle} job); call the worker-only _apply_* function directly"
        )

    def pause(self) -> None:
        self._accepting = False
        self._resume.clear()

    def resume(self) -> None:
        if self._closed:
            return
        self._accepting = True
        self._resume.set()

    async def pause_and_drain(self, *, timeout: float | None = None) -> None:
        # Keep accepting while an existing long-running LLM settlement drains.
        # Rejecting first used to make every chat/pending-open request fail for
        # the whole drain window.  Once join() returns there is no ``await``
        # before the flag flips, so no producer can interleave between the
        # observed idle state and the atomic pause.
        self._resume.set()
        await self._join(timeout=timeout)
        self._accepting = False

    async def wait_until_started(self) -> None:
        """Wait until the actual worker Task owns a permit or startup fails."""
        if not self.worker_alive:
            self.start()
        await self._started.wait()
        if self._startup_error is not None:
            raise RuntimeError(
                "Dialogue settlement worker failed to start"
            ) from self._startup_error
        if self._worker_permit is None:
            raise RuntimeError("Dialogue settlement worker started without a permit")

    def revoke_worker_permit(self) -> bool:
        """Revoke this queue's exact lifecycle tuple before a reload handoff."""
        permit = self._worker_permit
        return permit is not None and self._guard.revoke_worker(permit)

    def reauthorize_worker(self) -> DialogueSettlementWorkerPermit:
        """Register a fresh nonce for a drained old worker during rollback."""
        worker = self._worker
        if worker is None or worker.done():
            raise RuntimeError("Cannot reauthorize a stopped dialogue settlement worker")
        permit = self._guard.register_worker(cast("asyncio.Task[object]", worker))
        self._worker_permit = permit
        return permit

    async def shutdown(self, *, timeout: float | None = None) -> None:
        self._closed = True
        self._accepting = False
        self._resume.set()
        try:
            await self._join(timeout=timeout)
        finally:
            worker = self._worker
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await worker
                self._worker = None
            self._registry.clear()

    async def _join(self, *, timeout: float | None) -> None:
        if timeout is None:
            await self._queue.join()
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except TimeoutError:
            logger.warning(
                "DialogueSettlementQueue drain timed out after %.1fs (depth=%d)",
                timeout,
                self._queue.qsize(),
            )
            raise

    async def _run(self) -> None:
        worker = asyncio.current_task()
        if worker is None:
            raise RuntimeError("Dialogue settlement worker requires an asyncio Task")
        permit = self._worker_permit
        if permit is None or permit.worker_task is not worker:
            raise RuntimeError("Dialogue settlement worker started without its exact permit")
        try:
            while True:
                job = await self._queue.get()
                self._active_job = job
                try:
                    await self._resume.wait()
                    current_permit = self._worker_permit
                    if current_permit is None:
                        raise RuntimeError("Dialogue settlement worker has no permit")
                    execution_scope = _WorkerExecutionScope()
                    scope_token = self._execution_scope.set(execution_scope)
                    try:
                        with self._guard.activate_worker(current_permit):
                            await self._execute(job)
                    finally:
                        execution_scope.active = False
                        self._execution_scope.reset(scope_token)
                except asyncio.CancelledError:
                    self._complete_cancelled(job)
                    raise
                except Exception as exc:
                    self._complete_exception(job, exc)
                    logger.exception(
                        "Dialogue settlement job failed (kind=%s sequence=%d)",
                        job.kind.value,
                        job.sequence,
                    )
                finally:
                    self._active_job = None
                    self._registry.release(job.anchor_snapshot)
                    self._queue.task_done()
        finally:
            latest_permit = self._worker_permit
            if latest_permit is not None:
                self._guard.clear_if_current(latest_permit)

    async def _execute(self, job: DialogueJob) -> None:
        started_at = asyncio.get_running_loop().time()
        effective = self._registry.resolve_for_dispatch(
            job.anchor_snapshot,
            owner_reservation_id=job.owned_anchor_reservation_id,
        )
        if isinstance(effective, AnchorFailed):
            self._complete_result(
                job,
                DialogueJobResult(outcome="anchor_dependency_failed"),
            )
            return
        dispatch_job = replace(job, effective_anchor_snapshot=effective)
        builder_resolved = False
        refresh_kind, refresh_ref = self._anchor_refresh_target(job, effective)
        try:
            raw_result = await self._dispatcher(dispatch_job)
            if job.owned_anchor_reservation_id is not None:
                dispatch_result = self._normalize_builder_result(raw_result)
                terminal = dispatch_result.anchor_terminal
                if terminal is None:
                    raise AnchorReservationResolutionError(
                        "Anchor builder returned without a typed terminal"
                    )
                self._registry.resolve_owned(
                    ref=cast("AnchorReserved", job.anchor_snapshot).ref,
                    reservation_id=job.owned_anchor_reservation_id,
                    owner_job_id=job.job_id,
                    owner_sequence=job.sequence,
                    terminal=terminal,
                )
                builder_resolved = True
                result = dispatch_result.result
                if dispatch_result.followup is not None:
                    followup_result = await dispatch_result.followup()
                    if followup_result is not None:
                        result = followup_result
            else:
                result = self._normalize_non_builder_result(raw_result)
            self._complete_result(job, result)
            outcome = result.outcome
        except BaseException as exc:
            if job.owned_anchor_reservation_id is not None and not builder_resolved:
                reservation = cast("AnchorReserved", job.anchor_snapshot)
                actual = self._registry.actual_state(
                    target_kind=reservation.kind,
                    target_ref=reservation.ref,
                )
                self._registry.resolve_owned(
                    ref=reservation.ref,
                    reservation_id=job.owned_anchor_reservation_id,
                    owner_job_id=job.job_id,
                    owner_sequence=job.sequence,
                    terminal=AnchorMutationTerminal.failed(
                        actual,
                        cause=f"{type(exc).__name__}: {exc}",
                    ),
                )
            raise
        finally:
            self._registry.refresh_after_dispatch(
                target_kind=refresh_kind,
                target_ref=refresh_ref,
                completed_sequence=job.sequence,
            )
            finished_at = asyncio.get_running_loop().time()
            logger.debug(
                "dialogue settlement job",
                extra={
                    "kind": job.kind.value,
                    "sequence": job.sequence,
                    "depth": self._queue.qsize(),
                    "queue_wait_ms": max(0.0, (started_at - job.accepted_at) * 1000),
                    "run_ms": max(0.0, (finished_at - started_at) * 1000),
                    "outcome": locals().get("outcome", "error"),
                },
            )

    @staticmethod
    def _anchor_refresh_target(
        job: DialogueJob,
        effective: AnchorAdmissionSnapshot,
    ) -> tuple[str, str]:
        """Resolve the durable target even when a LEARN payload omits it."""
        if job.anchor_transition.establishes_anchor:
            return (
                job.anchor_transition.target_kind,
                job.anchor_transition.target_ref,
            )
        target_kind = str(job.payload.get("target_kind", "")).strip()
        target_ref = str(job.payload.get("target_ref", "")).strip()
        if target_kind and target_ref:
            return target_kind, target_ref
        if isinstance(effective, AnchorPersisted):
            return effective.kind, effective.ref
        if isinstance(effective, AnchorAbsent):
            return effective.target_kind, effective.target_ref
        return "", ""

    @staticmethod
    def _normalize_builder_result(
        raw_result: DialogueDispatchReturn,
    ) -> DialogueDispatchResult:
        if isinstance(raw_result, AnchorMutationTerminal):
            return DialogueDispatchResult(
                result=DialogueJobResult(outcome=raw_result.disposition.value),
                anchor_terminal=raw_result,
            )
        if isinstance(raw_result, DialogueDispatchResult):
            return raw_result
        raise AnchorReservationResolutionError(
            "Anchor builder dispatcher must return an AnchorMutationTerminal"
        )

    @staticmethod
    def _normalize_non_builder_result(
        raw_result: DialogueDispatchReturn,
    ) -> DialogueJobResult:
        if raw_result is None:
            return DialogueJobResult(outcome="completed")
        if isinstance(raw_result, DialogueJobResult):
            return raw_result
        if isinstance(raw_result, DialogueDispatchResult):
            if raw_result.anchor_terminal is not None or raw_result.followup is not None:
                raise AnchorReservationResolutionError(
                    "Non-builder job returned an anchor mutation result"
                )
            return raw_result.result
        raise AnchorReservationResolutionError(
            "Non-builder job returned an unexpected anchor terminal"
        )

    @staticmethod
    def _complete_result(job: DialogueJob, result: DialogueJobResult) -> None:
        completion = job.completion
        if completion is not None and not completion.done():
            completion.set_result(result)

    @staticmethod
    def _complete_exception(job: DialogueJob, exc: BaseException) -> None:
        completion = job.completion
        if completion is not None and not completion.done():
            completion.set_exception(exc)

    @staticmethod
    def _complete_cancelled(job: DialogueJob) -> None:
        completion = job.completion
        if completion is not None and not completion.done():
            completion.cancel()

    @staticmethod
    def _consume_worker_exception(worker: asyncio.Task[None]) -> None:
        """Retrieve loop-shutdown failures so embedded test loops stay quiet."""
        with contextlib.suppress(asyncio.CancelledError, Exception):
            worker.result()
