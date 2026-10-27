"""Durable screen commits, generation fences, and attributable real feedback.

Providers never enter this module: all callers cross one transaction-owning
Interface. A successful commit freezes a complete ordered screen, not a set of
independently appended recommendations. Presentation is a separate operation.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openbiliclaw.recommendation.director.models import (
    ExperiencedCard,
    FeedbackContent,
    RealFeedback,
    RecentScreen,
    ScreenAction,
    ScreenConstraints,
    canonical_json,
    digest,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from openbiliclaw.storage.database import Database

Strategy = Literal["jev_recurrent", "agent_jev", "agent_only"]
BatchIntent = Literal["NEXT", "STAY"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS director_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS director_streams (
    stream_ref TEXT PRIMARY KEY,
    session_ref TEXT NOT NULL,
    state_revision INTEGER NOT NULL DEFAULT 0,
    feedback_cursor TEXT NOT NULL DEFAULT '0',
    configuration_revision INTEGER NOT NULL DEFAULT 0,
    runtime_epoch INTEGER NOT NULL DEFAULT 0,
    context_digest TEXT NOT NULL DEFAULT '',
    fence INTEGER NOT NULL DEFAULT 0,
    committed_batch_seq INTEGER NOT NULL DEFAULT 0,
    experience_step_seq INTEGER NOT NULL DEFAULT 0,
    latest_batch_ref TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS director_requests (
    stream_ref TEXT NOT NULL REFERENCES director_streams(stream_ref),
    request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    claim_json TEXT NOT NULL,
    batch_ref TEXT,
    abandoned INTEGER NOT NULL DEFAULT 0,
    abandoned_reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (stream_ref, request_id)
);
CREATE TABLE IF NOT EXISTS director_batches (
    batch_ref TEXT PRIMARY KEY,
    stream_ref TEXT NOT NULL REFERENCES director_streams(stream_ref),
    request_id TEXT NOT NULL,
    root_batch_ref TEXT NOT NULL,
    batch_json TEXT NOT NULL,
    commit_digest TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (stream_ref, request_id)
);
CREATE INDEX IF NOT EXISTS idx_director_batches_root
    ON director_batches(root_batch_ref);
CREATE INDEX IF NOT EXISTS idx_director_batches_stream_created
    ON director_batches(stream_ref, created_at);
CREATE TABLE IF NOT EXISTS director_batch_items (
    batch_ref TEXT NOT NULL REFERENCES director_batches(batch_ref),
    item_key TEXT NOT NULL,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    position INTEGER NOT NULL,
    presented INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (batch_ref, item_key),
    UNIQUE (batch_ref, position),
    UNIQUE (recommendation_id)
);
CREATE TABLE IF NOT EXISTS director_feedback (
    stream_ref TEXT NOT NULL REFERENCES director_streams(stream_ref),
    event_ref TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    feedback_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (stream_ref, event_ref)
);
CREATE INDEX IF NOT EXISTS idx_director_feedback_stream_created
    ON director_feedback(stream_ref, created_at);
CREATE INDEX IF NOT EXISTS idx_director_feedback_batch_created
    ON director_feedback(
        stream_ref, json_extract(NULLIF(feedback_json, ''), '$.batch_ref'), created_at);
CREATE INDEX IF NOT EXISTS idx_director_feedback_preference_revision
    ON director_feedback(
        stream_ref, CAST(json_extract(receipt_json, '$.state_revision') AS INTEGER) DESC)
    WHERE feedback_json <> ''
        AND json_extract(NULLIF(feedback_json, ''), '$.kind') NOT IN ('presented', 'click');
CREATE TABLE IF NOT EXISTS director_projected_events (
    event_id INTEGER PRIMARY KEY REFERENCES events(id),
    stream_ref TEXT NOT NULL,
    event_ref TEXT NOT NULL,
    payload_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS director_plans (
    stream_ref TEXT PRIMARY KEY REFERENCES director_streams(stream_ref),
    revision INTEGER NOT NULL,
    runtime_epoch INTEGER NOT NULL,
    state_revision INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    plan_json TEXT NOT NULL
);
"""


class StoreConflict(ValueError):  # noqa: N818 - domain conflict, shared runtime Interface
    """A stale, ambiguous, or ineligible operation was rejected without writes."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class SessionSnapshot(_Record):
    """Only explicit presentation/actions contribute to real feedback."""

    session_ref: str
    stream_ref: str
    state_revision: int = 0
    feedback_cursor: str = "0"
    configuration_revision: int = 0
    runtime_epoch: int = 0
    committed_batch_seq: int = 0
    experience_step_seq: int = 0
    latest_batch_ref: str | None = None
    real_feedback: tuple[RealFeedback, ...] = ()
    recent_screens: tuple[RecentScreen, ...] = ()
    # A durable aggregate, not additional model-facing event history. None
    # preserves compatibility for snapshots constructed without a store read.
    latest_preference_revision: int | None = Field(default=None, ge=0)


class GenerationClaim(_Record):
    """Persisted lease: final commit checks every version while holding the writer."""

    session_ref: str
    stream_ref: str
    request_id: str
    intent: BatchIntent
    context_digest: str
    state_revision: int
    feedback_cursor: str
    configuration_revision: int
    runtime_epoch: int
    fence: int
    expires_at: float
    direction_ref: str
    direction: str
    target_count: int = Field(ge=1, le=20)
    source_platform: str = ""
    root_batch_ref: str | None = None
    parent_batch_ref: str | None = None
    patch_seq: int = 0
    excluded_item_keys: tuple[str, ...] = ()


class BatchInstance(_Record):
    """Immutable committed sequence; mutable exposure state lives outside this row."""

    batch_ref: str
    session_ref: str
    stream_ref: str
    request_id: str
    intent: BatchIntent
    execution_binding: Literal["SESSION_STEP", "SESSION_STAY", "PLAN_SLOT", "STAY_PATCH"]
    requested_strategy: Strategy
    realized_strategy: str
    direction_ref: str
    direction: str
    root_batch_ref: str
    parent_batch_ref: str | None
    patch_seq: int
    committed_batch_seq: int
    experience_step_seq: int
    state_revision: int
    configuration_revision: int
    runtime_epoch: int
    target_count: int
    items: tuple[dict[str, JsonValue], ...]
    trace_json: str
    committed_at: float
    plan_id: str | None = None
    plan_revision: int | None = None
    slot_seq: int | None = None


class FeedbackReceipt(_Record):
    """Stable replay result; no second state increment on a duplicate receipt."""

    event_ref: str
    batch_ref: str
    state_revision: int
    feedback_cursor: str
    item_keys: tuple[str, ...]
    event_ids: tuple[int, ...]


class StoredPlan(_Record):
    """Uncommitted planning JSON and its compare-and-swap revision."""

    stream_ref: str
    revision: int
    runtime_epoch: int
    state_revision: int
    expires_at: float
    plan: dict[str, JsonValue]


def candidate_fingerprint(row: Mapping[str, Any]) -> str:
    """Bind content/feature evidence, not volatile delivery counters or score drift."""
    fields = (
        "bvid",
        "item_key",
        "title",
        "description",
        "body_text",
        "content_type",
        "source_metadata",
        "summary",
        "content_summary",
        "topic_group",
        "style_key",
        "source_platform",
        "content_url",
        "published_at",
        "temporal_class",
        "temporal_policy_version",
        "temporal_validity_mode",
        "temporal_valid_until",
        "temporal_state",
        "temporal_evidence",
        "temporal_next_review_at",
        "temporal_evaluated_at",
        "temporal_evidence_complete",
    )
    return digest({field: row.get(field) for field in fields})


def _key(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"invalid {label}")
    return value


class DirectorStore:
    """One durable owner for screens, sessions, events, and uncommitted horizons.

    Uses short-lived SQLite connections; safe across thread/process callers.
    Engineering lease/JSON caps prevent unbounded input, not quality thresholds.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        conn = db.open_connection()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ensure_stream(conn: sqlite3.Connection, stream_ref: str) -> sqlite3.Row:
        _key(stream_ref, "stream_ref")
        conn.execute(
            "INSERT OR IGNORE INTO director_streams (stream_ref, session_ref, created_at) "
            "VALUES (?, ?, ?)",
            (stream_ref, uuid.uuid4().hex, time.time()),
        )
        row = conn.execute(
            "SELECT * FROM director_streams WHERE stream_ref = ?", (stream_ref,)
        ).fetchone()
        assert row is not None
        return cast("sqlite3.Row", row)

    @staticmethod
    def _content(item: dict[str, JsonValue]) -> FeedbackContent:
        return FeedbackContent(
            ref="c_" + digest(str(item["item_key"]))[:24],
            title=str(item.get("title") or "")[:120],
            summary=str(item.get("description") or item.get("body_text") or "")[:160],
            topic_group=str(item.get("topic_group") or item.get("topic") or "")[:80],
            content_style=str(item.get("style_key") or "")[:80],
            source_platform=str(item.get("source_platform") or "")[:32],
        )

    @classmethod
    def _recent_screens(cls, conn: sqlite3.Connection, stream: str) -> tuple[RecentScreen, ...]:
        # Three latest *experienced* screens, not three generated drafts. Caps
        # are engineering budgets, not fatigue/engagement thresholds.
        batches = conn.execute(
            "SELECT b.batch_json FROM director_batches b WHERE b.stream_ref = ? "
            "AND EXISTS (SELECT 1 FROM director_feedback f WHERE f.stream_ref = b.stream_ref "
            "AND json_extract(NULLIF(f.feedback_json, ''), '$.batch_ref') = b.batch_ref) "
            "ORDER BY b.created_at DESC, b.rowid DESC LIMIT 3",
            (stream,),
        ).fetchall()
        screens = []
        for record in reversed(batches):
            batch = BatchInstance.model_validate_json(record["batch_json"])
            events = []
            # ACKs are bounded by screen size; keep them independently so many
            # later actions never erase exposure confirmation from the view.
            for presented in (True, False):
                events.extend(
                    conn.execute(
                        "SELECT feedback_json, receipt_json, created_at, rowid AS event_rowid "
                        "FROM director_feedback WHERE stream_ref = ? "
                        "AND json_extract(NULLIF(feedback_json, ''), '$.batch_ref') = ? "
                        "AND (json_extract(NULLIF(feedback_json, ''), '$.kind') = 'presented') = ? "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 80",
                        (stream, batch.batch_ref, presented),
                    ).fetchall()
                )
            events.sort(key=lambda entry: (entry["created_at"], entry["event_rowid"]))
            exposures: dict[str, float] = {}
            actions_by_key: dict[str, list[ScreenAction]] = {}
            for event in events:
                receipt = FeedbackReceipt.model_validate_json(event["receipt_json"])
                feedback = RealFeedback.model_validate_json(event["feedback_json"])
                for key in receipt.item_keys:
                    if feedback.kind == "presented":
                        exposures[key] = float(event["created_at"])
                    else:
                        actions_by_key.setdefault(key, []).append(
                            ScreenAction(
                                event_ref=feedback.event_ref,
                                kind=feedback.kind,
                                note=feedback.description,
                                occurred_at=float(event["created_at"]),
                            )
                        )
            cards = []
            for position, item in enumerate(batch.items, 1):
                key = str(item["item_key"])
                exposed = key in exposures
                actions = actions_by_key.get(key, [])
                # Do not send never-exposed, never-interacted cards as history.
                if exposed or actions:
                    cards.append(
                        ExperiencedCard(
                            **cls._content(item).model_dump(),
                            position=position,
                            exposure_confirmed=exposed,
                            exposure_at=exposures.get(key),
                            actions=tuple(actions[-4:]),
                        )
                    )
            screens.append(
                RecentScreen(
                    batch_ref=batch.batch_ref,
                    direction=batch.direction,
                    committed_at=batch.committed_at,
                    cards=tuple(cards),
                )
            )
        return tuple(screens)

    @classmethod
    def _snapshot(cls, conn: sqlite3.Connection, row: sqlite3.Row) -> SessionSnapshot:
        # ACK/click bursts can push a preference out of the bounded model
        # context. This indexed durable watermark establishes whether a plan
        # has already incorporated every stronger receipt, without loading or
        # transmitting the complete event history. Read it in the same writer
        # transaction as the session revision and summarized feedback.
        preference = conn.execute(
            "SELECT CAST(json_extract(receipt_json, '$.state_revision') AS INTEGER) "
            "AS preference_revision, feedback_json, receipt_json, created_at, "
            "rowid AS event_rowid FROM director_feedback "
            "WHERE stream_ref = ? AND feedback_json <> '' "
            "AND json_extract(NULLIF(feedback_json, ''), '$.kind') "
            "NOT IN ('presented', 'click') "
            "ORDER BY CAST(json_extract(receipt_json, '$.state_revision') AS INTEGER) DESC "
            "LIMIT 1",
            (row["stream_ref"],),
        ).fetchone()
        # Engineering summary cap: four actual actions plus four exposures.
        # Protect the latest non-click preference within the four action slots:
        # otherwise a click burst could trigger replan via the watermark while
        # hiding its actual reason from the model. Historical rows are unchanged.
        records: list[sqlite3.Row] = []
        for presented in (False, True):
            group = conn.execute(
                "SELECT feedback_json, receipt_json, created_at, rowid AS event_rowid "
                "FROM director_feedback "
                "WHERE stream_ref = ? AND feedback_json <> '' AND "
                "(json_extract(NULLIF(feedback_json, ''), '$.kind') = 'presented') = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 4",
                (row["stream_ref"], int(presented)),
            ).fetchall()
            if (
                not presented
                and preference is not None
                and all(event["event_rowid"] != preference["event_rowid"] for event in group)
            ):
                group = [*group[:3], preference]
            records.extend(group)
        records.sort(key=lambda event: (event["created_at"], event["event_rowid"]))
        enriched = []
        for event in records:
            feedback = RealFeedback.model_validate_json(event["feedback_json"])
            receipt = FeedbackReceipt.model_validate_json(event["receipt_json"])
            batch = cls._batch(conn, feedback.batch_ref)
            enriched.append(
                feedback.model_copy(
                    update={
                        "occurred_at": float(event["created_at"]),
                        "content": tuple(
                            cls._content(item)
                            for item in batch.items
                            if item["item_key"] in receipt.item_keys
                        ),
                    }
                )
            )
        return SessionSnapshot(
            **{
                name: row[name]
                for name in SessionSnapshot.model_fields
                if name not in {"real_feedback", "recent_screens", "latest_preference_revision"}
            },
            real_feedback=tuple(enriched),
            recent_screens=cls._recent_screens(conn, row["stream_ref"]),
            latest_preference_revision=(
                int(preference["preference_revision"]) if preference is not None else 0
            ),
        )

    def session(self, stream_ref: str) -> SessionSnapshot:
        """Read/create a durable stream; does not invent feedback or advance steps."""
        with self._transaction() as conn:
            return self._snapshot(conn, self._ensure_stream(conn, stream_ref))

    def begin(
        self,
        stream_ref: str,
        request_id: str,
        *,
        intent: BatchIntent = "NEXT",
        context_digest: str,
        direction_ref: str,
        direction: str,
        target_count: int = 10,
        configuration_revision: int = 0,
        source_platform: str = "",
        lease_seconds: float = 30.0,
        request_digest: str = "",
    ) -> GenerationClaim | BatchInstance:
        """Claim generation, or replay the exact already-committed request.

        A new request fences any older in-flight request on the same stream.
        An identical pending retry returns its lease. A crashed worker's expired
        lease can be reclaimed only if its context/state/fence are still current;
        the new fence revokes the old worker. STAY preserves root direction.
        """
        _key(request_id, "request_id")
        _key(context_digest, "context_digest")
        _key(direction_ref, "direction_ref")
        if intent not in ("NEXT", "STAY") or not 1 <= target_count <= 20:
            raise ValueError("invalid generation intent or size")
        if not direction.strip() or len(direction) > 500 or len(source_platform) > 32:
            raise ValueError("invalid direction or scope")
        if (
            configuration_revision < 0
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 300
        ):
            raise ValueError("invalid revision or lease")
        if request_digest:
            _key(request_digest, "request_digest")
        semantic_digest = digest(
            {
                "intent": intent,
                "direction_ref": direction_ref,
                "direction": direction,
                "target_count": target_count,
                "source_platform": source_platform,
                "caller_request_digest": request_digest,
            }
        )
        with self._transaction() as conn:
            row = self._ensure_stream(conn, stream_ref)
            existing = conn.execute(
                "SELECT * FROM director_requests WHERE stream_ref = ? AND request_id = ?",
                (stream_ref, request_id),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != semantic_digest:
                    raise StoreConflict("request_id_reused")
                if existing["batch_ref"]:
                    return self._batch(conn, str(existing["batch_ref"]))
            active = conn.execute(
                "SELECT value FROM director_meta WHERE key='active_context_digest'"
            ).fetchone()
            if active is not None and active["value"] != context_digest:
                raise StoreConflict("runtime_context_stale")
            if configuration_revision < int(row["configuration_revision"]):
                raise StoreConflict("configuration_revision_stale")
            if row["context_digest"] and row["context_digest"] != context_digest:
                raise StoreConflict("runtime_context_stale")
            if existing is not None:
                if not existing["abandoned"]:
                    claim = GenerationClaim.model_validate_json(existing["claim_json"])
                    if time.time() < claim.expires_at:
                        self._assert_claim(row, claim)
                        return claim
                    self._assert_claim(row, claim, require_live=False)
                conn.execute(
                    "DELETE FROM director_requests WHERE stream_ref = ? AND request_id = ?",
                    (stream_ref, request_id),
                )
            epoch = int(row["runtime_epoch"])
            if (
                row["context_digest"] != context_digest
                or int(row["configuration_revision"]) != configuration_revision
            ):
                epoch += 1
            root: str | None = None
            parent: str | None = None
            patch_seq = 0
            excluded: tuple[str, ...] = ()
            if intent == "STAY":
                if row["latest_batch_ref"] is None:
                    raise StoreConflict("stay_without_batch")
                prior = self._batch(conn, str(row["latest_batch_ref"]))
                root, parent, patch_seq = prior.root_batch_ref, prior.batch_ref, prior.patch_seq + 1
                direction_ref, direction = prior.direction_ref, prior.direction
                excluded = tuple(
                    str(value["item_key"])
                    for value in conn.execute(
                        "SELECT i.item_key FROM director_batch_items i JOIN director_batches b "
                        "ON b.batch_ref = i.batch_ref WHERE b.root_batch_ref = ? "
                        "ORDER BY b.created_at, i.position",
                        (root,),
                    )
                )
            claim = GenerationClaim(
                session_ref=row["session_ref"],
                stream_ref=stream_ref,
                request_id=request_id,
                intent=intent,
                context_digest=context_digest,
                state_revision=int(row["state_revision"]),
                feedback_cursor=str(row["feedback_cursor"]),
                configuration_revision=configuration_revision,
                runtime_epoch=epoch,
                fence=int(row["fence"]) + 1,
                expires_at=time.time() + lease_seconds,
                direction_ref=direction_ref,
                direction=direction,
                target_count=target_count,
                source_platform=source_platform,
                root_batch_ref=root,
                parent_batch_ref=parent,
                patch_seq=patch_seq,
                excluded_item_keys=excluded,
            )
            conn.execute(
                "UPDATE director_streams SET context_digest = ?, configuration_revision = ?, "
                "runtime_epoch = ?, fence = ? WHERE stream_ref = ?",
                (context_digest, configuration_revision, epoch, claim.fence, stream_ref),
            )
            conn.execute(
                "INSERT INTO director_requests "
                "(stream_ref, request_id, request_digest, claim_json) VALUES (?, ?, ?, ?)",
                (stream_ref, request_id, semantic_digest, claim.model_dump_json()),
            )
            return claim

    @staticmethod
    def _assert_claim(
        row: sqlite3.Row, claim: GenerationClaim, *, require_live: bool = True
    ) -> None:
        if require_live and time.time() >= claim.expires_at:
            raise StoreConflict("lease_expired")
        for name in (
            "session_ref",
            "state_revision",
            "feedback_cursor",
            "configuration_revision",
            "runtime_epoch",
            "context_digest",
            "fence",
        ):
            if row[name] != getattr(claim, name):
                raise StoreConflict("generation_stale")
        if claim.intent == "STAY" and row["latest_batch_ref"] != claim.parent_batch_ref:
            raise StoreConflict("replacement_chain_stale")

    @staticmethod
    def _batch(conn: sqlite3.Connection, batch_ref: str) -> BatchInstance:
        row = conn.execute(
            "SELECT batch_json FROM director_batches WHERE batch_ref = ?", (batch_ref,)
        ).fetchone()
        if row is None:
            raise StoreConflict("batch_not_found")
        return BatchInstance.model_validate_json(row["batch_json"])

    def restore(self, stream_ref: str, batch_ref: str | None = None) -> BatchInstance | None:
        """Restore exact frozen items, never select, top up, or advance counters."""
        conn = self._db.open_connection()
        try:
            if batch_ref is None:
                row = conn.execute(
                    "SELECT latest_batch_ref FROM director_streams WHERE stream_ref = ?",
                    (stream_ref,),
                ).fetchone()
                if row is None or row["latest_batch_ref"] is None:
                    return None
                batch_ref = str(row["latest_batch_ref"])
            batch = self._batch(conn, batch_ref)
            if batch.stream_ref != stream_ref:
                raise StoreConflict("batch_stream_mismatch")
            return batch
        finally:
            conn.close()

    def commit(
        self,
        claim: GenerationClaim,
        items: Sequence[Mapping[str, Any]],
        *,
        requested_strategy: Strategy,
        realized_strategy: str,
        trace: Mapping[str, Any] | None = None,
        constraints: ScreenConstraints | None = None,
        candidate_fingerprints: Mapping[str, str] | None = None,
        direction_ref: str | None = None,
        direction: str | None = None,
        plan_id: str | None = None,
        plan_revision: int | None = None,
        slot_seq: int | None = None,
        xhs_self_nickname: str = "",
        expected_plan_revision: int | None = None,
        plan_update: Mapping[str, Any] | None = None,
        plan_ttl_seconds: float = 300.0,
        latest_disliked_topics: Callable[[], Sequence[str]] | None = None,
    ) -> BatchInstance:
        """Commit all items or none after canonical fresh eligibility recheck.

        A shortage can commit fewer than target_count; the *supplied* sequence
        is indivisible. Optional fingerprints additionally reject content/feature
        drift during provider calls. Plan fields are all present or all absent.
        """
        if not items or len(items) > claim.target_count:
            raise ValueError("invalid committed screen size")
        if requested_strategy not in ("jev_recurrent", "agent_jev", "agent_only"):
            raise ValueError("invalid strategy")
        _key(realized_strategy, "realized_strategy")
        if (direction_ref is None) != (direction is None):
            raise ValueError("partial direction override")
        actual_direction_ref = direction_ref or claim.direction_ref
        actual_direction = direction if direction is not None else claim.direction
        _key(actual_direction_ref, "direction_ref")
        if not actual_direction.strip() or len(actual_direction) > 500:
            raise ValueError("invalid direction override")
        if claim.intent == "STAY" and (
            actual_direction_ref != claim.direction_ref or actual_direction != claim.direction
        ):
            raise StoreConflict("stay_direction_immutable")
        if (expected_plan_revision is None) != (plan_update is None):
            raise ValueError("partial plan update")
        plan_update_json = canonical_json(dict(plan_update)) if plan_update is not None else None
        if not math.isfinite(plan_ttl_seconds) or not 0 < plan_ttl_seconds <= 86400:
            raise ValueError("invalid plan TTL")
        bindings = (plan_id, plan_revision, slot_seq)
        if any(value is None for value in bindings) != all(value is None for value in bindings):
            raise ValueError("partial plan binding")
        if requested_strategy == "jev_recurrent" and plan_id is not None:
            raise ValueError("Jev recurrent cannot bind a fictitious plan")
        if plan_id is not None:
            _key(plan_id, "plan_id")
            if plan_revision is None or plan_revision < 0 or slot_seq is None or slot_seq < 0:
                raise ValueError("invalid plan binding")
        # Roundtrip detaches caller-owned mutable objects before transaction.
        frozen_items: list[dict[str, Any]] = json.loads(canonical_json(list(items)))
        trace_json = canonical_json(dict(trace or {}))
        if len(trace_json.encode()) > 1_000_000:
            raise ValueError("screen trace too large")
        commit_digest = digest(
            {
                "claim": claim.model_dump(mode="json"),
                "items": frozen_items,
                "requested_strategy": requested_strategy,
                "realized_strategy": realized_strategy,
                "trace": trace_json,
                "plan": bindings,
                "constraints": constraints.model_dump(mode="json") if constraints else None,
                "fingerprints": dict(candidate_fingerprints or {}),
                "direction": [actual_direction_ref, actual_direction],
                "plan_update": plan_update_json,
                "expected_plan_revision": expected_plan_revision,
                "plan_ttl_seconds": plan_ttl_seconds,
            }
        )
        with self._transaction() as conn:
            request = conn.execute(
                "SELECT * FROM director_requests WHERE stream_ref = ? AND request_id = ?",
                (claim.stream_ref, claim.request_id),
            ).fetchone()
            if request is None or request["claim_json"] != claim.model_dump_json():
                raise StoreConflict("claim_not_owned")
            if request["abandoned"]:
                raise StoreConflict("claim_abandoned")
            if request["batch_ref"]:
                record = conn.execute(
                    "SELECT commit_digest FROM director_batches WHERE batch_ref = ?",
                    (request["batch_ref"],),
                ).fetchone()
                if record is None or record["commit_digest"] != commit_digest:
                    raise StoreConflict("committed_screen_immutable")
                return self._batch(conn, str(request["batch_ref"]))
            row = self._ensure_stream(conn, claim.stream_ref)
            self._assert_claim(row, claim)
            if expected_plan_revision is not None:
                plan = conn.execute(
                    "SELECT * FROM director_plans WHERE stream_ref = ?", (claim.stream_ref,)
                ).fetchone()
                if (int(plan["revision"]) if plan else 0) != expected_plan_revision:
                    raise StoreConflict("plan_revision_stale")
            # No topic-window truncation at commit: the selected sequence must
            # satisfy its own registered constraints, not a moving top-3 window.
            available = self._db._load_available_pool_candidate_rows_on(
                conn,
                max_per_topic_group=2**31 - 1,
                xhs_self_nickname=xhs_self_nickname,
                full_rows=True,
                # Bypass the facade's event-ID cache. Account snapshots written
                # by another process can add seen rows without changing MAX ID.
                _viewed_content_keys={
                    str(value["item_key"])
                    for value in conn.execute("SELECT item_key FROM seen_items")
                },
            )
            by_bvid = {str(value["bvid"]): value for value in available}
            chosen: list[dict[str, Any]] = []
            keys: set[str] = set()
            for item in frozen_items:
                bvid = str(item.get("bvid", ""))
                latest = by_bvid.get(bvid)
                if latest is None:
                    raise StoreConflict("candidate_ineligible")
                item_key = str(
                    latest.get("item_key") or self._db._fallback_recommendation_item_key(bvid)
                )
                if str(item.get("item_key", item_key)) != item_key:
                    raise StoreConflict("candidate_identity_changed")
                if item_key in keys or item_key in claim.excluded_item_keys:
                    raise StoreConflict("duplicate_or_replacement_item")
                if claim.source_platform and latest.get("source_platform") != claim.source_platform:
                    raise StoreConflict("candidate_scope_changed")
                if candidate_fingerprints is not None and candidate_fingerprints.get(
                    item_key
                ) != candidate_fingerprint(latest):
                    raise StoreConflict("candidate_features_changed")
                keys.add(item_key)
                # Freeze display fields from latest canonical cache plus validated
                # caller display copy. Provider output never supplies identity.
                merged = {**latest, **item, "bvid": bvid, "item_key": item_key, "presented": 0}
                merged["topic_group"] = str(latest.get("topic_group", ""))
                chosen.append(merged)
            if latest_disliked_topics is not None:
                from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

                try:
                    latest_dislikes = latest_disliked_topics()
                    permitted = filter_recommendation_rows(
                        chosen, latest_dislikes, restore_on_total_fuzzy_match=False
                    )
                except Exception as exc:
                    # The callback is a pure local read; unavailable current
                    # policy must never publish cards using an obsolete policy.
                    raise StoreConflict("latest_dislike_unavailable") from exc
                if len(permitted) != len(chosen):
                    raise StoreConflict("latest_dislike_changed")
            self._verify_constraints(chosen, constraints)
            self._assert_claim(row, claim)
            batch_ref = uuid.uuid4().hex
            for item in chosen:
                confidence = float(item.get("confidence", item.get("relevance_score", 0)) or 0)
                if not math.isfinite(confidence):
                    raise ValueError("nonfinite recommendation confidence")
                cursor = conn.execute(
                    "INSERT INTO recommendations "
                    "(bvid, item_key, expression, topic, confidence, presented) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        item["bvid"],
                        item["item_key"],
                        str(item.get("expression", item.get("pool_expression", ""))),
                        str(item.get("topic", item.get("pool_topic_label", ""))),
                        confidence,
                    ),
                )
                item["recommendation_id"] = int(cursor.lastrowid or 0)
                item["id"] = int(cursor.lastrowid or 0)
                conn.execute(
                    "UPDATE content_cache SET pool_status = 'shown', "
                    "recommended_at = CURRENT_TIMESTAMP "
                    "WHERE bvid = ?",
                    (item["bvid"],),
                )
            sequence = int(row["committed_batch_seq"]) + 1
            step = int(row["experience_step_seq"]) + int(claim.intent == "NEXT")
            batch = BatchInstance(
                batch_ref=batch_ref,
                session_ref=claim.session_ref,
                stream_ref=claim.stream_ref,
                request_id=claim.request_id,
                intent=claim.intent,
                execution_binding=("STAY_PATCH" if claim.intent == "STAY" else "PLAN_SLOT")
                if plan_id
                else ("SESSION_STAY" if claim.intent == "STAY" else "SESSION_STEP"),
                requested_strategy=requested_strategy,
                realized_strategy=realized_strategy,
                direction_ref=actual_direction_ref,
                direction=actual_direction,
                root_batch_ref=claim.root_batch_ref or batch_ref,
                parent_batch_ref=claim.parent_batch_ref,
                patch_seq=claim.patch_seq,
                committed_batch_seq=sequence,
                experience_step_seq=step,
                state_revision=claim.state_revision,
                configuration_revision=claim.configuration_revision,
                runtime_epoch=claim.runtime_epoch,
                target_count=claim.target_count,
                items=tuple(chosen),
                trace_json=trace_json,
                committed_at=time.time(),
                plan_id=plan_id,
                plan_revision=plan_revision,
                slot_seq=slot_seq,
            )
            conn.execute(
                "INSERT INTO director_batches VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_ref,
                    claim.stream_ref,
                    claim.request_id,
                    batch.root_batch_ref,
                    batch.model_dump_json(),
                    commit_digest,
                    batch.committed_at,
                ),
            )
            for index, item in enumerate(batch.items):
                conn.execute(
                    "INSERT INTO director_batch_items "
                    "(batch_ref, item_key, recommendation_id, position) VALUES (?, ?, ?, ?)",
                    (batch_ref, item["item_key"], item["recommendation_id"], index),
                )
            conn.execute(
                "UPDATE director_requests SET batch_ref = ? "
                "WHERE stream_ref = ? AND request_id = ?",
                (batch_ref, claim.stream_ref, claim.request_id),
            )
            conn.execute(
                "UPDATE director_streams SET latest_batch_ref = ?, committed_batch_seq = ?, "
                "experience_step_seq = ? WHERE stream_ref = ?",
                (batch_ref, sequence, step, claim.stream_ref),
            )
            if plan_update_json is not None and (plan_update or expected_plan_revision):
                expires_at = 0.0
                if plan_update:
                    raw_expiry = plan_update.get("expires_at", time.time() + plan_ttl_seconds)
                    if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (float, int)):
                        raise ValueError("invalid plan expiry")
                    expires_at = float(raw_expiry)
                    if not math.isfinite(expires_at) or expires_at <= time.time():
                        raise StoreConflict("plan_expired")
                    expires_at = min(expires_at, time.time() + plan_ttl_seconds)
                conn.execute(
                    "INSERT INTO director_plans VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(stream_ref) DO UPDATE SET revision=excluded.revision, "
                    "runtime_epoch=excluded.runtime_epoch, state_revision=excluded.state_revision, "
                    "expires_at=excluded.expires_at, plan_json=excluded.plan_json",
                    (
                        claim.stream_ref,
                        int(expected_plan_revision or 0) + 1,
                        claim.runtime_epoch,
                        claim.state_revision,
                        expires_at,
                        plan_update_json,
                    ),
                )
            return batch

    @staticmethod
    def _verify_constraints(
        items: Sequence[Mapping[str, Any]], constraints: ScreenConstraints | None
    ) -> None:
        if constraints is None:
            return
        if any(str(item["item_key"]) in constraints.excluded_item_keys for item in items):
            raise StoreConflict("screen_exclusion_failed")
        for quota in constraints.topic_quotas:
            count = sum(item.get("topic_group") == quota.topic_group for item in items)
            if not quota.minimum <= count <= quota.maximum:
                raise StoreConflict("screen_quota_failed")

    def present(self, batch_ref: str, ack_id: str, item_keys: Sequence[str]) -> FeedbackReceipt:
        """Acknowledge only actually rendered cards, idempotently, in one transaction."""
        return self._record_feedback(batch_ref, ack_id, tuple(item_keys), "presented", "")

    def feedback(
        self,
        batch_ref: str,
        event_ref: str,
        item_key: str,
        kind: Literal["click", "like", "dislike", "skip", "explicit_request"],
        note: str = "",
        *,
        existing_event_id: int | None = None,
    ) -> FeedbackReceipt:
        """Record an action, or project one canonical ingress event without duplicating it.

        When existing_event_id is supplied, ingress retains ownership of the
        recommendation feedback fields and learning routing. This module checks
        persisted identity/kind/note and only updates exposure/session state.
        """
        if kind not in ("click", "like", "dislike", "skip", "explicit_request"):
            raise ValueError("invalid feedback kind")
        return self._record_feedback(
            batch_ref, event_ref, (item_key,), kind, note, existing_event_id=existing_event_id
        )

    def _record_feedback(
        self,
        batch_ref: str,
        event_ref: str,
        item_keys: tuple[str, ...],
        kind: Literal["presented", "click", "like", "dislike", "skip", "explicit_request"],
        note: str,
        *,
        existing_event_id: int | None = None,
    ) -> FeedbackReceipt:
        _key(batch_ref, "batch_ref")
        _key(event_ref, "event_ref")
        if not item_keys or len(item_keys) > 20 or len(set(item_keys)) != len(item_keys):
            raise ValueError("invalid feedback items")
        if len(note) > 2000:
            raise ValueError("feedback note too long")
        if existing_event_id is not None and (existing_event_id <= 0 or kind == "presented"):
            raise ValueError("invalid projected feedback event")
        payload_digest = digest(
            {
                "batch": batch_ref,
                "items": sorted(item_keys),
                "kind": kind,
                "note": note,
                "existing_event_id": existing_event_id,
            }
        )
        with self._transaction() as conn:
            batch = self._batch(conn, batch_ref)
            details = {str(item["item_key"]): item for item in batch.items}
            if not set(item_keys) <= details.keys():
                raise StoreConflict("feedback_item_not_in_batch")
            if existing_event_id is not None:
                stored = conn.execute(
                    "SELECT event_type, metadata FROM events WHERE id = ?", (existing_event_id,)
                ).fetchone()
                expected_event_type = "click" if kind == "click" else "feedback"
                if stored is None or stored["event_type"] != expected_event_type:
                    raise StoreConflict("feedback_event_not_found")
                try:
                    metadata = json.loads(str(stored["metadata"] or "{}"))
                except (TypeError, ValueError) as exc:
                    raise StoreConflict("feedback_event_invalid") from exc
                item = details[item_keys[0]]
                accepted_kinds: set[str] = {kind}
                if kind == "skip":
                    accepted_kinds.add("dismiss")
                if kind == "explicit_request":
                    accepted_kinds.add("comment")
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("recommendation_id") != item["recommendation_id"]
                    or (kind != "click" and metadata.get("feedback_type") not in accepted_kinds)
                    or str(metadata.get("feedback_note") or "") != note
                    or (metadata.get("item_key") and metadata["item_key"] != item["item_key"])
                    or (metadata.get("bvid") and metadata["bvid"] != item["bvid"])
                ):
                    raise StoreConflict("feedback_event_mismatch")
                projected = conn.execute(
                    "SELECT * FROM director_projected_events WHERE event_id = ?",
                    (existing_event_id,),
                ).fetchone()
                if projected is not None:
                    if projected["payload_digest"] != payload_digest:
                        raise StoreConflict("feedback_event_already_projected")
                    previous = conn.execute(
                        "SELECT receipt_json FROM director_feedback "
                        "WHERE stream_ref = ? AND event_ref = ?",
                        (projected["stream_ref"], projected["event_ref"]),
                    ).fetchone()
                    assert previous is not None
                    return FeedbackReceipt.model_validate_json(previous["receipt_json"])
            prior = conn.execute(
                "SELECT * FROM director_feedback WHERE stream_ref = ? AND event_ref = ?",
                (batch.stream_ref, event_ref),
            ).fetchone()
            if prior is not None:
                if prior["payload_digest"] != payload_digest:
                    raise StoreConflict("feedback_id_reused")
                return FeedbackReceipt.model_validate_json(prior["receipt_json"])
            event_ids: list[int] = []
            affected: list[str] = []
            for item_key in item_keys:
                item = details[item_key]
                ledger = conn.execute(
                    "SELECT presented FROM director_batch_items "
                    "WHERE batch_ref = ? AND item_key = ?",
                    (batch_ref, item_key),
                ).fetchone()
                assert ledger is not None
                if kind == "presented" and ledger["presented"]:
                    continue
                affected.append(item_key)
                conn.execute(
                    "UPDATE director_batch_items SET presented = 1 "
                    "WHERE batch_ref = ? AND item_key = ?",
                    (batch_ref, item_key),
                )
                conn.execute(
                    "UPDATE recommendations SET presented = 1, "
                    "presented_at = COALESCE(presented_at, CURRENT_TIMESTAMP) WHERE id = ?",
                    (item["recommendation_id"],),
                )
                if kind not in ("presented", "click") and existing_event_id is None:
                    conn.execute(
                        "UPDATE recommendations SET feedback = ?, feedback_type = ?, "
                        "feedback_note = ?, feedback_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (kind, kind, note, item["recommendation_id"]),
                    )
                    conn.execute(
                        "UPDATE content_cache SET pool_status = 'feedbacked', feedback_type = ?, "
                        "feedback_at = CURRENT_TIMESTAMP WHERE bvid = ?",
                        (kind, item["bvid"]),
                    )
                if existing_event_id is not None:
                    event_ids.append(existing_event_id)
                    continue
                params = self._db._event_insert_params(
                    "recommendation_presented"
                    if kind == "presented"
                    else "click"
                    if kind == "click"
                    else "feedback",
                    {
                        "url": item.get("content_url", ""),
                        "title": item.get("title", ""),
                        "source_platform": item.get("source_platform", ""),
                        "content_id": item.get("content_id", ""),
                        "context": note,
                        "metadata": {
                            "batch_ref": batch_ref,
                            "item_key": item_key,
                            "recommendation_id": item["recommendation_id"],
                            "stream_ref": batch.stream_ref,
                            "feedback_type": kind,
                            "feedback_note": note,
                        },
                        "ingest_key": "director:" + digest([batch.stream_ref, event_ref, item_key]),
                    },
                )
                cursor = conn.execute(
                    "INSERT INTO events (event_type, url, title, source_platform, content_id, "
                    "source_confidence, context, metadata, inferred_satisfaction, "
                    "satisfaction_reason, ingest_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params,
                )
                event_ids.append(int(cursor.lastrowid or 0))
            stream = self._ensure_stream(conn, batch.stream_ref)
            revision = int(stream["state_revision"]) + int(bool(affected))
            cursor_value = str(max([int(stream["feedback_cursor"]), *event_ids]))
            receipt = FeedbackReceipt(
                event_ref=event_ref,
                batch_ref=batch_ref,
                state_revision=revision,
                feedback_cursor=cursor_value,
                item_keys=tuple(affected),
                event_ids=tuple(event_ids),
            )
            # Keep duplicate ACK receipts, but do not place no-op acknowledgements
            # in the real-feedback history or falsely advance the state.
            if affected:
                feedback = RealFeedback(
                    event_ref=event_ref,
                    batch_ref=batch_ref,
                    kind=kind,
                    description=(note or f"{kind}: {', '.join(affected)}")[:256],
                )
                feedback_json = feedback.model_dump_json()
            else:
                feedback_json = ""
            conn.execute(
                "INSERT INTO director_feedback VALUES (?, ?, ?, ?, ?, ?)",
                (
                    batch.stream_ref,
                    event_ref,
                    payload_digest,
                    receipt.model_dump_json(),
                    feedback_json,
                    time.time(),
                ),
            )
            if existing_event_id is not None:
                conn.execute(
                    "INSERT INTO director_projected_events VALUES (?, ?, ?, ?)",
                    (existing_event_id, batch.stream_ref, event_ref, payload_digest),
                )
            conn.execute(
                "UPDATE director_streams SET state_revision = ?, feedback_cursor = ? "
                "WHERE stream_ref = ?",
                (revision, cursor_value, batch.stream_ref),
            )
            return receipt

    def invalidate(
        self, stream_ref: str, context_digest: str, *, configuration_revision: int | None = None
    ) -> SessionSnapshot:
        """Fence in-flight work immediately on mode/model/policy changes."""
        _key(context_digest, "context_digest")
        with self._transaction() as conn:
            row = self._ensure_stream(conn, stream_ref)
            revision = (
                int(row["configuration_revision"])
                if configuration_revision is None
                else configuration_revision
            )
            if revision < int(row["configuration_revision"]):
                raise StoreConflict("configuration_revision_stale")
            conn.execute(
                "UPDATE director_streams SET context_digest = ?, configuration_revision = ?, "
                "runtime_epoch = runtime_epoch + 1, fence = fence + 1 WHERE stream_ref = ?",
                (context_digest, revision, stream_ref),
            )
            return self._snapshot(conn, self._ensure_stream(conn, stream_ref))

    def activate_context(self, context_digest: str) -> int:
        """Activate configured ownership without discarding same-context horizons.

        The global context also rejects obsolete runtimes attempting to create a
        new stream. Explicit reload/shutdown uses ``invalidate_all`` to force a
        generation fence even when configuration values have not changed.
        """
        _key(context_digest, "context_digest")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO director_meta (key, value) VALUES ('active_context_digest', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (context_digest,),
            )
            cursor = conn.execute(
                "UPDATE director_streams SET context_digest = ?, "
                "runtime_epoch = runtime_epoch + 1, fence = fence + 1 "
                "WHERE context_digest <> ?",
                (context_digest, context_digest),
            )
            return int(cursor.rowcount)

    def invalidate_all(
        self, context_digest: str, *, configuration_revision: int | None = None
    ) -> int:
        """Fence all pre-existing workers, including when the route is switched off."""
        _key(context_digest, "context_digest")
        if configuration_revision is not None and configuration_revision < 0:
            raise ValueError("invalid configuration revision")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO director_meta (key, value) VALUES ('active_context_digest', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (context_digest,),
            )
            if configuration_revision is None:
                cursor = conn.execute(
                    "UPDATE director_streams SET context_digest = ?, "
                    "runtime_epoch = runtime_epoch + 1, fence = fence + 1",
                    (context_digest,),
                )
            else:
                newest = conn.execute(
                    "SELECT MAX(configuration_revision) FROM director_streams"
                ).fetchone()[0]
                if newest is not None and configuration_revision < int(newest):
                    raise StoreConflict("configuration_revision_stale")
                cursor = conn.execute(
                    "UPDATE director_streams SET context_digest = ?, configuration_revision = ?, "
                    "runtime_epoch = runtime_epoch + 1, fence = fence + 1",
                    (context_digest, configuration_revision),
                )
            return int(cursor.rowcount)

    def abandon(self, claim: GenerationClaim, reason_code: str) -> bool:
        """Close an uncommitted attempt; same logical request may retry with a new fence."""
        _key(reason_code, "reason_code")
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE director_requests SET abandoned = 1, abandoned_reason = ? "
                "WHERE stream_ref = ? AND request_id = ? AND claim_json = ? "
                "AND batch_ref IS NULL AND abandoned = 0",
                (reason_code, claim.stream_ref, claim.request_id, claim.model_dump_json()),
            )
            return bool(cursor.rowcount)

    def lookup_recommendation(self, recommendation_id: int) -> tuple[str, str] | None:
        """Resolve only the exact committed recommendation row, never newest-by-identity."""
        conn = self._db.open_connection()
        try:
            row = conn.execute(
                "SELECT batch_ref, item_key FROM director_batch_items WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
            if row is None:
                return None
            return str(row["batch_ref"]), str(row["item_key"])
        finally:
            conn.close()

    def clear_plan(self, stream_ref: str, *, expected_revision: int) -> None:
        """CAS a tombstone, preserving revision monotonicity and committed history."""
        with self._transaction() as conn:
            result = conn.execute(
                "UPDATE director_plans SET revision = revision + 1, expires_at = 0, "
                "plan_json = '{}' WHERE stream_ref = ? AND revision = ?",
                (stream_ref, expected_revision),
            )
            if result.rowcount != 1:
                raise StoreConflict("plan_revision_stale")

    def save_plan(
        self,
        stream_ref: str,
        plan: Mapping[str, Any],
        *,
        expected_revision: int = 0,
        ttl_seconds: float = 300.0,
        expected_state_revision: int | None = None,
        expected_runtime_epoch: int | None = None,
    ) -> StoredPlan:
        """Compare-and-swap one tentative horizon, without reserving any card."""
        if expected_revision < 0 or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 86400:
            raise ValueError("invalid plan revision or TTL")
        plan_json = canonical_json(dict(plan))
        if len(plan_json.encode()) > 1_000_000:
            raise ValueError("plan too large")
        with self._transaction() as conn:
            row = self._ensure_stream(conn, stream_ref)
            prior = conn.execute(
                "SELECT revision FROM director_plans WHERE stream_ref = ?", (stream_ref,)
            ).fetchone()
            revision = int(prior["revision"]) if prior is not None else 0
            if revision != expected_revision:
                raise StoreConflict("plan_revision_stale")
            if (
                expected_state_revision is not None
                and expected_state_revision != row["state_revision"]
            ):
                raise StoreConflict("plan_state_stale")
            if (
                expected_runtime_epoch is not None
                and expected_runtime_epoch != row["runtime_epoch"]
            ):
                raise StoreConflict("plan_epoch_stale")
            result = StoredPlan(
                stream_ref=stream_ref,
                revision=revision + 1,
                runtime_epoch=int(row["runtime_epoch"]),
                state_revision=int(row["state_revision"]),
                expires_at=time.time() + ttl_seconds,
                plan=json.loads(plan_json),
            )
            conn.execute(
                "INSERT INTO director_plans VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(stream_ref) DO UPDATE SET revision=excluded.revision, "
                "runtime_epoch=excluded.runtime_epoch, state_revision=excluded.state_revision, "
                "expires_at=excluded.expires_at, plan_json=excluded.plan_json",
                (
                    stream_ref,
                    result.revision,
                    result.runtime_epoch,
                    result.state_revision,
                    result.expires_at,
                    plan_json,
                ),
            )
            return result

    def load_plan(self, stream_ref: str, *, include_stale: bool = False) -> StoredPlan | None:
        """Read tentative JSON; feedback drift is visible for hybrid revalidation."""
        conn = self._db.open_connection()
        try:
            row = conn.execute(
                "SELECT p.*, s.runtime_epoch AS current_epoch FROM director_plans p "
                "JOIN director_streams s ON s.stream_ref = p.stream_ref WHERE p.stream_ref = ?",
                (stream_ref,),
            ).fetchone()
            if row is None or (
                not include_stale
                and (
                    row["expires_at"] <= time.time() or row["runtime_epoch"] != row["current_epoch"]
                )
            ):
                return None
            return StoredPlan(
                stream_ref=stream_ref,
                revision=row["revision"],
                runtime_epoch=row["runtime_epoch"],
                state_revision=row["state_revision"],
                expires_at=row["expires_at"],
                plan=json.loads(row["plan_json"]),
            )
        finally:
            conn.close()
