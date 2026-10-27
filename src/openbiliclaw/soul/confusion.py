"""Confusion objects — the "看不懂" cognitive object (Phase 2).

A confusion is raised when the system observes a behaviour it cannot cleanly
read. There are two producing sources:

- **Awareness** (`build_awareness_with_confusions_prompt`): the 12h cognition
  cycle asks the awareness model to flag observations it is genuinely unsure
  how to interpret, alongside the usual notes.
- **Speculation stalemate**: when a speculation expires with a *partial*
  confirmation (``0 < confirmation_count < threshold``) — the user neither
  clearly took nor clearly rejected the probe — the ambiguity itself is a
  confusion (`InterestSpeculator` exposes the stalemate via its tick result).

A confusion NEVER writes the profile directly (spec §invariant 8). It only
drives downstream clarification (ask / probe / wait) and a topic-freeze reflex.
The state machine is::

    open ──claim──▶ clarifying ──▶ resolved | dismissed
      │                              (three exits: promote / direct-settle /
      └──────── expire (TTL) ──▶ expired      dismissed — see resolve())

At most one confusion may be ``clarifying`` at a time — enforced across
connections by a partial unique index (the durable ask budget).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.soul.ledger import ProfileLedger

logger = logging.getLogger(__name__)

# --- Calibration constants ------------------------------------------------
# Confusion candidates per awareness round. Kept tiny so a chatty model cannot
# flood the ask budget; first-round recalibration flagged (pitfall #3).
MAX_CONFUSION_CANDIDATES_PER_ROUND = 2
# Near-duplicate confusion detection at production time.
_CONFUSION_DEDUP_SIMILARITY_THRESHOLD = 0.65
_CONFUSION_DEDUP_MIN_TEXT_LENGTH = 20
# Ask cooldown: once a confusion has been asked, do not re-ask for 72h. Persisted
# in the row (``asked_at``) so it survives restarts. Calibrated to the single-user
# interrupt budget (≤1 ask / 3 days); revisit after provider swap.
ASK_COOLDOWN_HOURS = 72
# Wait-path TTL: a confusion parked on "wait" self-expires after 14 days if no
# behavioural signal resolves it. Matches the probe wait window.
WAIT_TTL_DAYS = 14
# Held-update replay attempt ceiling — a replay that keeps failing is discarded
# rather than retried forever (prefer under- to double-counting, r5/R4-1).
MAX_REPLAY_ATTEMPTS = 2
# Durable dialogue-attribution backlog. Five turns cover the bounded focused
# thread without allowing a permanently failing settlement to grow the row
# forever (r7 Wave A contract; oldest overflow is explicitly audited).
MAX_DIALOGUE_REPLAY_QUEUE = 5

_VALID_STATUSES = frozenset({"open", "clarifying", "resolved", "dismissed", "expired"})
# Resolution outcome whitelist (drives held-update replay vs discard).
_RESOLUTION_REAL_INTEREST = "real_interest"
_RESOLUTION_PROXY = "proxy_behavior"
_RESOLUTION_DISMISSED = "dismissed"
_VALID_RESOLUTIONS = frozenset(
    {_RESOLUTION_REAL_INTEREST, _RESOLUTION_PROXY, _RESOLUTION_DISMISSED}
)

_HELD_STATES = frozenset({"held", "replaying", "applied", "applied_unverified", "discarded"})


@dataclass
class HeldUpdate:
    """A topic weight change deferred while its topic is frozen.

    Freezing prevents *further reinforcement* of a confused topic: new topics
    and weight upgrades are parked here (existing weights are not rolled back).
    On resolution the held update is either replayed (rebased into the next
    preference analysis) or discarded, tracked by ``state``.
    """

    held_id: str
    topic: str
    kind: str = "new"  # "new" | "upgrade"
    value: float = 0.0
    prev_value: float = 0.0
    state: str = "held"  # held | replaying | applied | applied_unverified | discarded
    replay_submitted_at: str = ""
    batch_id: str = ""
    replay_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "held_id": self.held_id,
            "topic": self.topic,
            "kind": self.kind,
            "value": self.value,
            "prev_value": self.prev_value,
            "state": self.state,
            "replay_submitted_at": self.replay_submitted_at,
            "batch_id": self.batch_id,
            "replay_attempts": self.replay_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeldUpdate:
        state = str(data.get("state", "held"))
        if state not in _HELD_STATES:
            state = "held"
        return cls(
            held_id=str(data.get("held_id", "") or uuid.uuid4().hex[:12]),
            topic=str(data.get("topic", "")),
            kind=str(data.get("kind", "new")),
            value=_to_float(data.get("value", 0.0)),
            prev_value=_to_float(data.get("prev_value", 0.0)),
            state=state,
            replay_submitted_at=str(data.get("replay_submitted_at", "")),
            batch_id=str(data.get("batch_id", "")),
            replay_attempts=int(_to_int(data.get("replay_attempts", 0))),
        )


@dataclass
class Confusion:
    """In-memory view of a ``confusions`` row."""

    id: int
    created_at: str = ""
    status: str = "open"
    source: str = ""
    topic: str = ""
    observation: str = ""
    interpretation: str = ""
    interpretation_confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    resolution: str = ""
    resolution_note: str = ""
    asked_at: str = ""
    ask_turn_id: str = ""
    defer_count: int = 0
    expires_at: str = ""
    held_updates: list[HeldUpdate] = field(default_factory=list)
    replay_queue: list[dict[str, Any]] = field(default_factory=list)
    resolved_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Confusion:
        held = [
            HeldUpdate.from_dict(item)
            for item in row.get("held_updates", [])
            if isinstance(item, dict)
        ]
        refs = [str(r) for r in row.get("evidence_refs", []) if str(r)]
        return cls(
            id=int(row.get("id", 0)),
            created_at=str(row.get("created_at", "") or ""),
            status=str(row.get("status", "open") or "open"),
            source=str(row.get("source", "") or ""),
            topic=str(row.get("topic", "") or ""),
            observation=str(row.get("observation", "") or ""),
            interpretation=str(row.get("interpretation", "") or ""),
            interpretation_confidence=_to_float(row.get("interpretation_confidence", 0.0)),
            evidence_refs=refs,
            resolution=str(row.get("resolution", "") or ""),
            resolution_note=str(row.get("resolution_note", "") or ""),
            asked_at=str(row.get("asked_at") or ""),
            ask_turn_id=str(row.get("ask_turn_id", "") or ""),
            defer_count=int(_to_int(row.get("defer_count", 0))),
            expires_at=str(row.get("expires_at") or ""),
            held_updates=held,
            replay_queue=[
                dict(item) for item in row.get("replay_queue", []) if isinstance(item, dict)
            ],
            resolved_at=str(row.get("resolved_at") or ""),
        )


class ConfusionManager:
    """State machine + DAO wrapper for confusion objects.

    Constructed with a ``database`` handle (may be ``None`` — a headless
    component or test without storage — in which case every operation is a
    silent no-op returning empty/false). ``ledger`` (optional) records every
    state transition into ``profile_update_ledger`` (best-effort observer).
    """

    def __init__(
        self,
        database: Any | None,
        ledger: ProfileLedger | None = None,
    ) -> None:
        self._db = database
        self._ledger = ledger
        self._mutation_guard: Callable[[], None] | None = None

    def install_mutation_guard(self, guard: Callable[[], None]) -> None:
        """Require ``guard`` for dialogue-owned runtime mutations."""
        if self._mutation_guard is not None and self._mutation_guard != guard:
            raise RuntimeError("Confusion mutation guard is already installed")
        self._mutation_guard = guard

    def _require_dialogue_settlement_worker(self) -> None:
        guard = self._mutation_guard
        if guard is not None:
            guard()

    # -- Producing sources ----------------------------------------------------

    @staticmethod
    def _dedupe_norm_text(value: str) -> str:
        return re.sub(r"[\W_]+", "", value).lower()

    @classmethod
    def _same_confusion(cls, left: dict[str, Any], right: dict[str, Any]) -> bool:
        """Whether two confusion candidates refer to the same ambiguity.

        Observation is the primary signal; matching on topic alone is too
        coarse because a short topic like "解压视频" can cover several
        genuinely different ambiguous behaviours.
        """
        left_observation = str(left.get("observation", "")).strip()
        right_observation = str(right.get("observation", "")).strip()
        left_topic = str(left.get("topic", "")).strip()
        right_topic = str(right.get("topic", "")).strip()
        if not left_observation or not right_observation:
            return False
        na = cls._dedupe_norm_text(left_observation)
        nb = cls._dedupe_norm_text(right_observation)
        if na == nb:
            return True
        if (
            len(na) >= _CONFUSION_DEDUP_MIN_TEXT_LENGTH
            and len(nb) >= _CONFUSION_DEDUP_MIN_TEXT_LENGTH
            and SequenceMatcher(None, na, nb).ratio() >= _CONFUSION_DEDUP_SIMILARITY_THRESHOLD
        ):
            return True
        # A long, specific topic plus similar observation is stronger evidence;
        # short topics alone never decide by themselves.
        if left_topic and right_topic:
            ta = cls._dedupe_norm_text(left_topic)
            tb = cls._dedupe_norm_text(right_topic)
            if (
                len(ta) >= _CONFUSION_DEDUP_MIN_TEXT_LENGTH
                and len(tb) >= _CONFUSION_DEDUP_MIN_TEXT_LENGTH
                and SequenceMatcher(None, ta, tb).ratio() >= _CONFUSION_DEDUP_SIMILARITY_THRESHOLD
                and cls._dedupe_norm_text(left_observation)
                == cls._dedupe_norm_text(right_observation)
            ):
                return True
        return False

    @classmethod
    def _confusion_as_dict(cls, item: Confusion) -> dict[str, Any]:
        return {
            "topic": str(getattr(item, "topic", "") or ""),
            "observation": str(getattr(item, "observation", "") or ""),
        }

    def create_from_awareness_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[int]:
        """Validate + persist awareness-sourced confusion candidates.

        Whitelist/clamp (pitfall #4): drop candidates without an ``observation``;
        cap the batch at ``MAX_CONFUSION_CANDIDATES_PER_ROUND`` (excess logged).
        Duplicate open/clarifying confusions are skipped at production time so a
        recurring ambiguous observation does not create a new row each cycle.
        """
        if self._db is None or not candidates:
            return []
        valid = [
            c for c in candidates if isinstance(c, dict) and str(c.get("observation", "")).strip()
        ]
        if len(valid) > MAX_CONFUSION_CANDIDATES_PER_ROUND:
            logger.warning(
                "Awareness produced %d confusion candidates; capping at %d",
                len(valid),
                MAX_CONFUSION_CANDIDATES_PER_ROUND,
            )
            valid = valid[:MAX_CONFUSION_CANDIDATES_PER_ROUND]
        active = [self._confusion_as_dict(item) for item in self.list_active()]
        seen: list[dict[str, Any]] = list(active)
        created: list[int] = []
        for cand in valid:
            if any(self._same_confusion(cand, existing) for existing in seen):
                continue
            cid = self._db.insert_confusion(
                source="awareness",
                topic=str(cand.get("topic", "")).strip(),
                observation=str(cand.get("observation", "")).strip(),
                interpretation=str(cand.get("interpretation", "")).strip(),
                interpretation_confidence=_clamp01(cand.get("interpretation_confidence", 0.0)),
                evidence_refs=[str(r) for r in _as_list(cand.get("evidence_refs")) if str(r)],
            )
            if cid:
                created.append(cid)
                seen.append(
                    {
                        "topic": str(cand.get("topic", "")).strip(),
                        "observation": str(cand.get("observation", "")).strip(),
                    }
                )
                self._record("confusion_open", topic=str(cand.get("topic", "")), after={"id": cid})
        return created

    def create_from_speculation_stalemate(
        self,
        *,
        domain: str,
        confirmation_count: int,
        confirmation_threshold: int,
        evidence_refs: list[str] | None = None,
    ) -> int | None:
        """Raise a confusion for a speculation that expired partially confirmed.

        The caller has already filtered ``0 < confirmation_count < threshold``
        (spec §Phase 2 decidable-stalemate); this method persists the object.
        """
        if self._db is None or not domain.strip():
            return None
        observation = (
            f"对「{domain}」的猜测得到过 {confirmation_count} 次部分确认，"
            f"但未达到 {confirmation_threshold} 次门槛就到期了——你对它到底有没有兴趣不清楚。"
        )
        cid = self._db.insert_confusion(
            source="speculation_stalemate",
            topic=domain.strip(),
            observation=observation,
            interpretation="部分确认但未达门槛，可能是弱兴趣或误判。",
            interpretation_confidence=round(confirmation_count / max(1, confirmation_threshold), 4),
            evidence_refs=[str(r) for r in (evidence_refs or []) if str(r)],
        )
        if cid:
            self._record("confusion_open", topic=domain, after={"id": cid, "source": "stalemate"})
        return cid or None

    # -- Reads ----------------------------------------------------------------

    def get(self, confusion_id: int) -> Confusion | None:
        if self._db is None:
            return None
        row = self._db.get_confusion(confusion_id)
        return Confusion.from_row(row) if row is not None else None

    def list_open(self) -> list[Confusion]:
        return self._list(["open"])

    def list_active(self) -> list[Confusion]:
        """Open + clarifying confusions (injected into the dialogue active list)."""
        return self._list(["open", "clarifying"])

    def list_for_generation_context(self, limit: int = 30) -> list[dict[str, Any]]:
        """Recent confusion history for LLM generation prompts.

        Returns a compact, deduplicated-by-row view of active plus already
        resolved/dismissed confusions so the awareness model can avoid
        recreating a similar objection/ambiguity from scratch.
        """
        if self._db is None:
            return []
        rows = self._db.list_confusions(
            statuses=["open", "clarifying", "resolved", "dismissed"],
            limit=max(1, int(limit)),
        )
        return [
            {
                "id": int(row.get("id", 0) or 0),
                "status": str(row.get("status", "") or "").strip().lower(),
                "topic": str(row.get("topic", "") or "").strip(),
                "observation": str(row.get("observation", "") or "").strip(),
                "interpretation": str(row.get("interpretation", "") or "").strip(),
            }
            for row in rows
            if str(row.get("observation", "") or "").strip()
        ]

    def _list(self, statuses: list[str]) -> list[Confusion]:
        if self._db is None:
            return []
        return [Confusion.from_row(r) for r in self._db.list_confusions(statuses=statuses)]

    def frozen_topics(self) -> set[str]:
        """Topics with an unresolved confusion — frozen against reinforcement."""
        return {c.topic.strip() for c in self.list_active() if c.topic.strip()}

    # -- TTL maintenance (folded into the 12h cognition cycle) ----------------

    def expire_due(self, *, now: datetime | None = None) -> list[int]:
        """Expire open/clarifying confusions past their wait TTL.

        Returns the ids expired. Held updates on an expired confusion are
        discarded (dismissed/expired → discard, spec §Phase 2 unfreeze).
        """
        if self._db is None:
            return []
        current = now or datetime.now()
        expired: list[int] = []
        for confusion in self._list(["open", "clarifying"]):
            if not confusion.expires_at:
                continue
            due = _parse_iso(confusion.expires_at)
            if due is None or current < due:
                continue
            self._discard_all_held(confusion)
            self._db.update_confusion(
                confusion.id,
                status="expired",
                resolved_at=current.isoformat(),
                held_updates=[h.to_dict() for h in confusion.held_updates],
            )
            expired.append(confusion.id)
            self._record("confusion_expired", topic=confusion.topic, before={"id": confusion.id})
        return expired

    # -- Clarification path 1: ask (durable chat, scope="confusion") ----------

    def can_ask(self, confusion: Confusion, *, now: datetime | None = None) -> bool:
        """Whether the 72h ask cooldown has elapsed since the last ask."""
        if not confusion.asked_at:
            return True
        last = _parse_iso(confusion.asked_at)
        if last is None:
            return True
        current = now or datetime.now()
        return current - last >= timedelta(hours=ASK_COOLDOWN_HOURS)

    def schedule_ask(
        self,
        confusion_id: int,
        *,
        ask_turn_id: str,
        now: datetime | None = None,
        ignore_cooldown: bool = False,
    ) -> bool:
        """Claim the confusion into ``clarifying`` and stamp the ask time.

        Returns ``False`` if the 72h cooldown has not elapsed, if another
        confusion already holds the single clarifying slot (partial unique
        index), or if the row is not ``open``. The cooldown is persisted in
        ``asked_at`` so it survives restarts. ``ignore_cooldown`` is reserved
        for the explicit pending-list open action; the database's global
        ``clarifying <= 1`` constraint still applies.
        """
        self._require_dialogue_settlement_worker()
        if self._db is None:
            return False
        confusion = self.get(confusion_id)
        if confusion is None or (not ignore_cooldown and not self.can_ask(confusion, now=now)):
            return False
        current = now or datetime.now()
        claimed = bool(
            self._db.claim_confusion_clarifying(
                confusion_id,
                ask_turn_id=ask_turn_id,
                asked_at=current.isoformat(),
            )
        )
        if claimed:
            self._record("confusion_ask", topic=confusion.topic, after={"id": confusion_id})
        return claimed

    def defer(self, confusion_id: int, *, now: datetime | None = None) -> None:
        """User "暂时忽略" — release the clarifying slot, keep the cooldown.

        Reuses the probe ignore semantics: back to ``open`` (freeing the slot),
        ``defer_count`` incremented, ``asked_at`` retained so the 72h cooldown
        still blocks an immediate re-ask.
        """
        if self._db is None:
            return
        confusion = self.get(confusion_id)
        if confusion is None or confusion.status in {"resolved", "dismissed", "expired"}:
            return
        self._db.update_confusion(
            confusion_id,
            status="open",
            defer_count=confusion.defer_count + 1,
        )
        self._record("confusion_defer", topic=confusion.topic, before={"id": confusion_id})

    def start_wait(
        self,
        confusion_id: int,
        *,
        now: datetime | None = None,
    ) -> None:
        """Clarification path 3: park on wait with a 14d TTL (open + expires_at)."""
        if self._db is None:
            return
        current = now or datetime.now()
        expires = (current + timedelta(days=WAIT_TTL_DAYS)).isoformat()
        self._db.update_confusion(confusion_id, status="open", expires_at=expires)

    # -- Resolution: three exits ---------------------------------------------

    def resolve(
        self,
        confusion_id: int,
        *,
        resolution: str,
        note: str = "",
        now: datetime | None = None,
    ) -> str | None:
        """Resolve a confusion via one of the three exits.

        - ``real_interest``  → transient interest confirmed; held updates begin
          replay (rebased into the next preference analysis). status=resolved.
        - ``proxy_behavior`` → a misread / proxy behaviour; held updates
          discarded, related evidence flagged for discount. status=resolved.
        - ``dismissed``      → user dismisses; held updates discarded.
          status=dismissed.

        Returns the terminal status, or ``None`` if the row is missing / the
        resolution is not whitelisted.
        """
        if self._db is None:
            return None
        if resolution not in _VALID_RESOLUTIONS:
            logger.warning("confusion resolve dropped: bad resolution=%r", resolution)
            return None
        confusion = self.get(confusion_id)
        if confusion is None or confusion.status in {"resolved", "dismissed", "expired"}:
            # Idempotent: an already-terminal confusion is not re-resolved.
            return confusion.status if confusion is not None else None
        current = now or datetime.now()
        if resolution == _RESOLUTION_REAL_INTEREST:
            self._begin_replay(confusion, now=current)
            terminal = "resolved"
        else:
            self._discard_all_held(confusion)
            terminal = "resolved" if resolution == _RESOLUTION_PROXY else "dismissed"
            if resolution == _RESOLUTION_PROXY:
                # The behaviour was a proxy / misread — discount its evidence so
                # it stops driving preference weight (retraction-style patch).
                self._discount_proxy_evidence(confusion)
        self._db.update_confusion(
            confusion_id,
            status=terminal,
            resolution=resolution,
            resolution_note=note,
            resolved_at=current.isoformat(),
            held_updates=[h.to_dict() for h in confusion.held_updates],
        )
        self._record(
            "confusion_resolve",
            topic=confusion.topic,
            before={"id": confusion_id},
            after={"resolution": resolution, "status": terminal},
        )
        return terminal

    # -- Dialogue-anchor settlement ownership + durable FIFO replay ---------

    def process_anchor_settlement(
        self,
        confusion_id: int,
        *,
        action: str,
        interpretation: str = "",
        note: str = "",
        turn_id: str = "",
        anchor_generation: int = 0,
    ) -> str | None:
        """Persist classifier output, then drain settlements strictly FIFO.

        Every item is durably enqueued before the side effect. A failure leaves
        the head in place; later turns append behind it and can never overtake.
        ``None`` therefore means "retained for replay", not "ignored".
        """
        self._require_dialogue_settlement_worker()
        if self._db is None:
            return None
        normalized_action = action.strip().lower()
        normalized_interpretation = interpretation.strip().lower()
        if normalized_action not in {"resolve", "defer"}:
            logger.warning("confusion anchor settlement dropped: bad action=%r", action)
            return None
        if normalized_action == "resolve" and normalized_interpretation not in _VALID_RESOLUTIONS:
            logger.warning(
                "confusion anchor settlement dropped: bad interpretation=%r",
                interpretation,
            )
            return None
        replay_id = turn_id.strip() or f"replay-{uuid.uuid4().hex}"
        item: dict[str, object] = {
            "replay_id": replay_id,
            "turn_id": turn_id.strip(),
            "action": normalized_action,
            "interpretation": normalized_interpretation,
            "note": note,
            "anchor_generation": max(0, int(anchor_generation)),
        }
        enqueue = getattr(self._db, "enqueue_confusion_replay", None)
        if not callable(enqueue):
            logger.warning("confusion replay queue unavailable; settlement retained in memory only")
            return None
        dropped = enqueue(
            confusion_id,
            item,
            max_items=MAX_DIALOGUE_REPLAY_QUEUE,
        )
        for dropped_item in dropped:
            dropped_turn_id = str(dropped_item.get("turn_id", ""))
            self._record(
                "confusion_replay_dropped",
                before=dropped_item,
                after={"reason": "overflow", "limit": MAX_DIALOGUE_REPLAY_QUEUE},
                turn_id=dropped_turn_id,
            )
        return self.retry_anchor_settlements(
            confusion_id,
            expected_generation=max(0, int(anchor_generation)),
        )

    def retry_anchor_settlements(
        self,
        confusion_id: int,
        *,
        expected_generation: int | None = None,
    ) -> str | None:
        """Drain an existing durable settlement queue from its head."""
        self._require_dialogue_settlement_worker()
        if self._db is None:
            return None
        pop_head = getattr(self._db, "pop_confusion_replay_head", None)
        if not callable(pop_head):
            return None
        last_status: str | None = None
        while True:
            confusion = self.get(confusion_id)
            if confusion is None or not confusion.replay_queue:
                return last_status
            head = confusion.replay_queue[0]
            replay_id = str(head.get("replay_id") or head.get("turn_id") or "")
            turn_id = str(head.get("turn_id", ""))
            action = str(head.get("action", "")).strip().lower()
            interpretation = str(head.get("interpretation", "")).strip().lower()
            note = str(head.get("note", ""))
            terminal: str | None
            try:
                generation = max(0, int(head.get("anchor_generation", 0)))
            except (TypeError, ValueError):
                generation = -1
            malformed = (
                not replay_id
                or action not in {"resolve", "defer"}
                or (action == "resolve" and interpretation not in _VALID_RESOLUTIONS)
            )
            stale = expected_generation is not None and generation != expected_generation
            if malformed or stale:
                reason = "malformed" if malformed else "stale_generation"
                if not pop_head(confusion_id, expected_id=replay_id):
                    logger.warning(
                        "confusion replay drop fenced: id=%s reason=%s",
                        replay_id,
                        reason,
                    )
                    return last_status
                self._record(
                    "confusion_replay_dropped",
                    before=head,
                    after={"reason": reason},
                    turn_id=turn_id,
                )
                continue
            try:
                if action == "defer":
                    # A crash after defer's status update but before the FIFO
                    # pop leaves an open row with the same head. Treat that as
                    # the already-applied object segment so recovery does not
                    # increment defer_count twice.
                    if confusion.status != "open":
                        self.defer(confusion_id)
                    terminal = "deferred"
                else:
                    terminal = self.resolve(
                        confusion_id,
                        resolution=interpretation,
                        note=note,
                    )
                    if terminal is None:
                        raise RuntimeError("confusion resolution returned no terminal state")
            except Exception:
                logger.warning(
                    "confusion anchor settlement failed; FIFO head retained: turn_id=%s",
                    turn_id,
                    exc_info=True,
                )
                return None
            if not pop_head(confusion_id, expected_id=replay_id):
                # A concurrent release may have cleared the queue after the
                # idempotent side effect. Empty means the release owns cleanup;
                # a non-empty mismatch is a real fencing loss and must stop.
                refreshed = self.get(confusion_id)
                if refreshed is not None and refreshed.replay_queue:
                    logger.warning(
                        "confusion replay pop fenced after settlement: turn_id=%s",
                        turn_id,
                    )
                    return None
            self._record_anchor_processed(
                turn_id=turn_id,
                after={
                    "action": action,
                    "interpretation": interpretation,
                    "status": terminal,
                },
            )
            last_status = terminal

    def record_anchor_relation_processed(
        self,
        confusion_id: int,
        *,
        relation: str,
        turn_id: str,
    ) -> None:
        """Write the idempotency receipt for non-settlement anchor relations."""
        self._record_anchor_processed(
            turn_id=turn_id,
            after={"confusion_id": confusion_id, "relation": relation},
        )

    def pending_dialogue_replays(self) -> list[dict[str, Any]]:
        """Completed clarifying replies without a successful processing receipt."""
        if self._db is None:
            return []
        list_pending = getattr(self._db, "list_pending_confusion_dialogue_replays", None)
        if not callable(list_pending):
            return []
        return [dict(item) for item in list_pending() if isinstance(item, dict)]

    # -- Topic freeze + held-update replay -----------------------------------

    def record_held_updates(self, held: list[HeldUpdate]) -> None:
        """Attach freeze-deferred updates to their topic's active confusion.

        Each held update is appended to the ``held_updates`` of the newest
        active confusion whose ``topic`` matches. Updates with no matching
        confusion are dropped (the topic is not actually frozen).
        """
        if self._db is None or not held:
            return
        active = self.list_active()
        by_topic: dict[str, Confusion] = {}
        for confusion in active:  # list is newest-first; keep the newest per topic
            by_topic.setdefault(confusion.topic.strip(), confusion)
        touched: dict[int, Confusion] = {}
        for update in held:
            target = by_topic.get(update.topic.strip())
            if target is None:
                continue
            target.held_updates.append(update)
            touched[target.id] = target
            self._record(
                "confusion_hold_update",
                topic=update.topic,
                after={"kind": update.kind, "value": update.value},
                held_id=update.held_id,
            )
        for confusion in touched.values():
            self._db.update_confusion(
                confusion.id,
                held_updates=[h.to_dict() for h in confusion.held_updates],
            )

    def _begin_replay(self, confusion: Confusion, *, now: datetime) -> None:
        """Move ``held`` updates → ``replaying`` and persist the receipt.

        The receipt (``replay_submitted_at`` + ``batch_id``) is written in the
        SAME ``update_confusion`` call as the status flip (single SQLite txn,
        r5/R4-1) so a crash can never leave a replaying item without a receipt.
        """
        batch_id = uuid.uuid4().hex[:16]
        for held in confusion.held_updates:
            if held.state != "held":
                continue
            held.state = "replaying"
            held.replay_submitted_at = now.isoformat()
            held.batch_id = batch_id
            held.replay_attempts += 1

    def pending_replays(self) -> list[Confusion]:
        """Resolved confusions with held updates still in ``replaying`` state.

        These were flipped to ``replaying`` (with a receipt) by
        :meth:`resolve` on a real-interest resolution but have not yet been
        rebased into a preference analysis. The 12h held-replay consumer picks
        them up, feeds the held topics as evidence, then calls
        :meth:`mark_replay_applied`.
        """
        return [
            confusion
            for confusion in self._list(["resolved"])
            if any(held.state == "replaying" for held in confusion.held_updates)
        ]

    def mark_replay_applied(self, confusion_id: int) -> None:
        """Downstream preference analysis consumed the held updates → applied."""
        if self._db is None:
            return
        confusion = self.get(confusion_id)
        if confusion is None:
            return
        changed = False
        for held in confusion.held_updates:
            if held.state == "replaying":
                held.state = "applied"
                changed = True
        if changed:
            self._db.update_confusion(
                confusion_id,
                held_updates=[h.to_dict() for h in confusion.held_updates],
            )

    def recover_replaying(self) -> list[int]:
        """Crash recovery for held updates stuck in ``replaying`` (r5/R4-1).

        Scans terminal (resolved) confusions with ``replaying`` held updates:

        - **has receipt** (``replay_submitted_at``) → ``applied_unverified`` +
          WARNING and DO NOT resubmit (it may already have been absorbed —
          prefer under- to double-counting).
        - **no receipt** (defensive; the receipt is written in the same txn as
          the status flip, so this should not happen) → retry: leave ``held``
          for resubmission until ``replay_attempts`` reaches
          ``MAX_REPLAY_ATTEMPTS``, then ``discarded`` + WARNING.

        Returns the confusion ids whose held updates were touched.
        """
        if self._db is None:
            return []
        touched: list[int] = []
        for confusion in self._list(["resolved"]):
            changed = False
            for held in confusion.held_updates:
                if held.state != "replaying":
                    continue
                if held.replay_submitted_at:
                    held.state = "applied_unverified"
                    logger.warning(
                        "held update %s was replaying with a receipt on recovery; "
                        "marking applied_unverified (no resubmit)",
                        held.held_id,
                    )
                elif held.replay_attempts >= MAX_REPLAY_ATTEMPTS:
                    held.state = "discarded"
                    logger.warning(
                        "held update %s exhausted replay attempts; discarding",
                        held.held_id,
                    )
                else:
                    held.state = "held"
                changed = True
            if changed:
                self._db.update_confusion(
                    confusion.id,
                    held_updates=[h.to_dict() for h in confusion.held_updates],
                )
                touched.append(confusion.id)
        return touched

    # -- Internal helpers -----------------------------------------------------

    def _record_anchor_processed(self, *, turn_id: str, after: object) -> None:
        if turn_id and self._db is not None:
            mark_processed = getattr(self._db, "mark_confusion_dialogue_replay_processed", None)
            if callable(mark_processed) and not mark_processed(turn_id):
                logger.debug(
                    "confusion attribution receipt turn was not found/eligible: %s",
                    turn_id,
                )
        self._record(
            "confusion_anchor_processed",
            after=after,
            turn_id=turn_id,
        )

    def _discount_proxy_evidence(self, confusion: Confusion) -> None:
        """Discount events behind a proxy-resolved confusion (best-effort)."""
        if self._db is None or not confusion.evidence_refs:
            return
        discount = getattr(self._db, "discount_events_by_confusion", None)
        if not callable(discount):
            return
        try:
            marked = int(discount(confusion.evidence_refs))
        except Exception:
            logger.debug("confusion proxy evidence discount failed", exc_info=True)
            return
        if marked:
            self._record(
                "confusion_proxy_discount",
                topic=confusion.topic,
                before={"id": confusion.id},
                after={"discounted_events": marked},
            )

    def _discard_all_held(self, confusion: Confusion) -> None:
        for held in confusion.held_updates:
            if held.state not in {"applied", "applied_unverified"}:
                held.state = "discarded"

    def _record(
        self,
        write_point: str,
        *,
        topic: str = "",
        before: object = None,
        after: object = None,
        held_id: str = "",
        turn_id: str = "",
    ) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.record(
                write_point=write_point,
                source="confusion",
                before=before,
                after=after,
                source_refs=[topic] if topic else [],
                held_id=held_id,
                turn_id=turn_id,
            )
        except Exception:  # pragma: no cover - ledger is best-effort
            logger.debug("confusion ledger record failed", exc_info=True)


def apply_confusion_freeze(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    frozen_topics: set[str],
) -> tuple[dict[str, Any], list[HeldUpdate]]:
    """Filter a preference write so frozen topics are not further reinforced.

    Freeze semantics (spec §Phase 2): for a topic with an unresolved confusion,
    a *new* interest or a *weight upgrade* is held back (returned as a
    ``HeldUpdate``); an already-present weight is left untouched (no rollback —
    only future reinforcement is blocked). Interests of non-frozen topics and
    all other preference fields pass through unchanged.

    **No-op when ``frozen_topics`` is empty** — the common case — so a database
    with zero confusions produces a byte-identical write (regression guard).
    """
    if not frozen_topics:
        return after, []
    before_weights: dict[str, float] = {}
    for item in _as_list(before.get("interests")):
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                before_weights[name] = _to_float(item.get("weight", 0.0))

    filtered_interests: list[Any] = []
    held: list[HeldUpdate] = []
    for item in _as_list(after.get("interests")):
        if not isinstance(item, dict):
            filtered_interests.append(item)
            continue
        name = str(item.get("name", "")).strip()
        if not name or name not in frozen_topics:
            filtered_interests.append(item)
            continue
        new_weight = _to_float(item.get("weight", 0.0))
        prev = before_weights.get(name)
        if prev is None:
            # New frozen topic: hold the whole thing, drop from this write.
            held.append(
                HeldUpdate(
                    held_id=uuid.uuid4().hex[:12],
                    topic=name,
                    kind="new",
                    value=new_weight,
                    prev_value=0.0,
                )
            )
            continue
        if new_weight > prev:
            # Upgrade: hold the delta, keep the existing weight.
            held.append(
                HeldUpdate(
                    held_id=uuid.uuid4().hex[:12],
                    topic=name,
                    kind="upgrade",
                    value=new_weight,
                    prev_value=prev,
                )
            )
            frozen_item = dict(item)
            frozen_item["weight"] = prev
            filtered_interests.append(frozen_item)
            continue
        filtered_interests.append(item)

    if not held:
        return after, []
    result = dict(after)
    result["interests"] = filtered_interests
    return result, held


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _to_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return 0


def _clamp01(value: object) -> float:
    return max(0.0, min(1.0, round(_to_float(value), 4)))


def _parse_iso(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
