"""L2 approval gate for hard_write agent tools (「聊一聊」 M7).

Hard-write tool calls (config changes, source create/toggle) are never
executed inside the agent loop. The loop submits an :class:`ApprovalRecord`
to the :class:`ApprovalStore`, streams an ``approval_request`` event, and
feeds an "awaiting approval" result back to the model so the turn can end
normally. The user then approves or rejects through
``/api/chat/approvals/{id}/...``; only the approve endpoint re-dispatches
the original tool call with the recorded arguments.

Storage is a single JSON document (atomic tmp-file + rename) so no
``storage/database.py`` migration is needed; approvals survive restarts.
The store is thread-safe (one lock around every mutation) and every state
transition is idempotent or conflict-checked:

    pending ──approve──▶ approved ──mark_executing──▶ executing ──mark_executed──▶ executed
                                                          │                         (ok)
                                                          └──mark_executed──▶ failed
    pending ──reject───▶ rejected                            (not ok)
    pending ──(ttl)────▶ expired          (lazy, on read/write)

``approve`` on an already ``approved``/``executing``/``executed``/``failed``
record returns the record unchanged (idempotent); ``approved`` is the only
state from which execution may start, and ``mark_executed`` only accepts
``executing``, so a repeated approve never re-executes the side effect.
Execution runs in a background task (the approve endpoint returns as soon
as the record reaches ``executing``); a crash that interrupts execution is
recovered on load by demoting ``executing`` back to ``approved`` so the
user can retry the approve.

Two durability rules keep the single-file document consistent when a hot
reload briefly overlaps two store instances:

- reads (``get``/``list``) only persist when lazy expiry actually changed a
  record, so a stale instance can never clobber newer states by reading;
- production wiring reuses one store instance across runtime rebuilds
  (``api/runtime_context.py``), keeping a single in-memory authority.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

ApprovalStatus = Literal[
    "pending", "approved", "executing", "rejected", "executed", "failed", "expired"
]

APPROVAL_STATUSES: frozenset[str] = frozenset(
    {"pending", "approved", "executing", "rejected", "executed", "failed", "expired"}
)

DEFAULT_APPROVAL_TTL_HOURS = 24


class ApprovalConflictError(RuntimeError):
    """Raised when a state transition is not allowed from the current status."""

    def __init__(self, approval_id: str, status: str, action: str) -> None:
        super().__init__(f"审批 {approval_id} 当前状态为 {status}，无法执行 {action}。")
        self.approval_id = approval_id
        self.status = status
        self.action = action


@dataclass
class ApprovalRecord:
    """One pending/decided hard-write action awaiting user approval."""

    approval_id: str
    tool_name: str
    arguments: dict[str, Any]
    summary: str
    reason: str = ""
    impact: str = ""
    session: str = ""
    session_id: str = ""
    turn_id: str = ""
    status: ApprovalStatus = "pending"
    created_at: str = ""
    updated_at: str = ""
    decided_at: str = ""
    executed_at: str = ""
    expires_at: str = ""
    result: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON transport / persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRecord:
        """Rebuild a record from its serialized form, tolerating extras."""
        known = {field_name for field_name in cls.__dataclass_fields__}
        payload = {key: value for key, value in data.items() if key in known}
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            payload["arguments"] = {}
        record = cls(**payload)
        if record.status not in APPROVAL_STATUSES:
            record.status = "pending"
        return record


class ApprovalStore:
    """Durable, idempotent store for hard-write approval records.

    ``path`` points at the backing JSON file; ``None`` keeps everything in
    memory (tests, headless components). ``now`` is injectable for expiry
    tests. All public methods are synchronous and lock-guarded; the API
    layer executes approved actions in a background task *between*
    ``mark_executing`` and ``mark_executed``.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl_hours: float = DEFAULT_APPROVAL_TTL_HOURS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path
        self._ttl = timedelta(hours=max(0.0, float(ttl_hours)))
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._records: dict[str, ApprovalRecord] = {}
        if self._path is not None:
            self._load()

    @property
    def path(self) -> Path | None:
        """Backing JSON file (``None`` for in-memory stores)."""
        return self._path

    def submit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        summary: str,
        reason: str = "",
        impact: str = "",
        session: str = "",
        session_id: str = "",
        turn_id: str = "",
    ) -> ApprovalRecord:
        """Register one pending approval for a hard-write tool call."""
        name = str(tool_name).strip()
        if not name:
            raise ValueError("Approval tool name must not be empty.")
        now = self._now()
        record = ApprovalRecord(
            approval_id=f"ap_{uuid.uuid4().hex[:16]}",
            tool_name=name,
            arguments=dict(arguments),
            summary=str(summary or name),
            reason=str(reason or ""),
            impact=str(impact or ""),
            session=str(session or ""),
            session_id=str(session_id or ""),
            turn_id=str(turn_id or ""),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + self._ttl).isoformat(),
        )
        with self._lock:
            self._expire_stale_locked(now)
            self._records[record.approval_id] = record
            self._save_locked()
        logger.info("Approval submitted: %s (%s)", record.approval_id, record.summary)
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return one record by id (lazy expiry applied)."""
        with self._lock:
            dirty = self._expire_stale_locked(self._now())
            record = self._records.get(approval_id.strip())
            if record is None:
                if dirty:
                    self._save_locked()
                return None
            if dirty:
                self._save_locked()
            return record

    def list(
        self,
        *,
        status: str = "",
        limit: int = 100,
    ) -> list[ApprovalRecord]:
        """Return records newest-first, optionally filtered by status."""
        normalized = status.strip()
        with self._lock:
            dirty = self._expire_stale_locked(self._now())
            records = [
                record
                for record in self._records.values()
                if not normalized or record.status == normalized
            ]
            if dirty:
                self._save_locked()
        records.sort(key=lambda record: (record.created_at, record.approval_id), reverse=True)
        return records[: max(1, int(limit))]

    def approve(self, approval_id: str) -> ApprovalRecord:
        """Move pending → approved. Idempotent once past pending."""
        with self._lock:
            record = self._require_locked(approval_id)
            if record.status in ("approved", "executing", "executed", "failed"):
                return record
            if record.status != "pending":
                raise ApprovalConflictError(record.approval_id, record.status, "approve")
            record.status = "approved"
            record.decided_at = self._now().isoformat()
            record.updated_at = record.decided_at
            self._save_locked()
            return record

    def reject(self, approval_id: str, *, reason: str = "") -> ApprovalRecord:
        """Move pending → rejected. Idempotent for rejected."""
        with self._lock:
            record = self._require_locked(approval_id)
            if record.status == "rejected":
                return record
            if record.status != "pending":
                raise ApprovalConflictError(record.approval_id, record.status, "reject")
            record.status = "rejected"
            if reason.strip():
                suffix = f"拒绝原因: {reason.strip()}"
                record.reason = f"{record.reason}（{suffix}）" if record.reason else suffix
            record.decided_at = self._now().isoformat()
            record.updated_at = record.decided_at
            self._save_locked()
            return record

    def mark_executing(self, approval_id: str) -> ApprovalRecord:
        """Move approved → executing as the background execution is queued.

        Idempotent for an already ``executing`` record so a duplicate
        approve can observe the in-flight execution instead of queueing a
        second one; every other state conflicts.
        """
        with self._lock:
            record = self._require_locked(approval_id)
            if record.status == "executing":
                return record
            if record.status != "approved":
                raise ApprovalConflictError(record.approval_id, record.status, "mark_executing")
            record.status = "executing"
            record.updated_at = self._now().isoformat()
            self._save_locked()
            return record

    def mark_executed(
        self,
        approval_id: str,
        *,
        ok: bool,
        result: str = "",
        error: str = "",
    ) -> ApprovalRecord:
        """Move executing → executed (ok) or failed (not ok) with the outcome.

        Raises :class:`ApprovalConflictError` from any other state, so the
        executor can never double-record an already-settled approval.
        """
        with self._lock:
            record = self._require_locked(approval_id)
            if record.status != "executing":
                raise ApprovalConflictError(record.approval_id, record.status, "mark_executed")
            record.status = "executed" if ok else "failed"
            record.result = str(result or "")
            record.error = "" if ok else str(error or "执行失败")
            record.executed_at = self._now().isoformat()
            record.updated_at = record.executed_at
            self._save_locked()
            return record

    def _require_locked(self, approval_id: str) -> ApprovalRecord:
        self._expire_stale_locked(self._now())
        record = self._records.get(approval_id.strip())
        if record is None:
            raise KeyError(approval_id)
        return record

    def _expire_stale_locked(self, now: datetime) -> bool:
        """Expire stale pending records; return True when anything changed."""
        dirty = False
        for record in self._records.values():
            if record.status != "pending" or not record.expires_at:
                continue
            try:
                expires_at = datetime.fromisoformat(record.expires_at)
            except ValueError:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if now >= expires_at:
                record.status = "expired"
                record.updated_at = now.isoformat()
                dirty = True
        return dirty

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("Approval store unreadable: %s", self._path, exc_info=True)
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Approval store corrupted, starting empty: %s", self._path)
            return
        items = data.get("approvals") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return
        recovered = False
        for item in items:
            if not isinstance(item, dict) or not item.get("approval_id"):
                continue
            record = ApprovalRecord.from_dict(item)
            if record.status == "executing":
                # Crash recovery: the background execution was interrupted
                # mid-flight, so its side effect is not guaranteed to have
                # completed. Demote back to approved; the user retries the
                # approve to re-dispatch (retry semantics, never auto-rerun).
                record.status = "approved"
                record.updated_at = self._now().isoformat()
                recovered = True
            self._records[record.approval_id] = record
        if recovered:
            self._save_locked()

    def _save_locked(self) -> None:
        if self._path is None:
            return
        payload = {
            "version": 1,
            "approvals": [record.to_dict() for record in self._records.values()],
        }
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._path)
        except OSError:
            logger.warning("Approval store persist failed: %s", self._path, exc_info=True)
