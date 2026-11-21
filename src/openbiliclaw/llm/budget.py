"""Cross-process admission ledger for the background LLM budget.

The scheduler's self-imposed quota (`scheduler.llm_budget_max_calls` per
`scheduler.llm_budget_window_seconds`) used to be enforced only at controller
loop boundaries and counted in process memory, so one cognition run could issue
many chunk calls past the cap, and sibling processes (desktop worker loops, a
daemon plus a CLI command) each kept their own counter.

:class:`BackgroundBudgetLedger` keeps the window in the shared SQLite database
so every process sees the same remaining budget. `try_acquire()` is one
`BEGIN IMMEDIATE` transaction: reset the window when it rolled over, reject when
the cap is already spent, otherwise increment and admit. Rejection happens
before any provider request is built or sent, and an admitted call is counted
even if the provider later fails (that request was still spent).

The ledger is deliberately small and boring: one row, no history. Per-caller
reporting lives in ``llm_usage``; this table only answers "may this background
call proceed?".

:class:`DailySpendLedger` is the money-side companion (issue #353). It sums
``llm_usage.estimated_cost_cny`` for the local calendar day and writes one
``budget_exceeded`` event per day, so the scheduler can stop on spend instead
of on call count.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.local_day import local_day_window

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_TABLE = "background_llm_budget"
_RESERVATIONS_TABLE = "background_llm_budget_reservations"
_CREATE_RESERVATIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {_RESERVATIONS_TABLE} (
    id TEXT PRIMARY KEY,
    amount_cny REAL NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
)
"""
_CREATE_RESERVATIONS_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS idx_{_RESERVATIONS_TABLE}_expires "
    f"ON {_RESERVATIONS_TABLE}(expires_at)"
)
# A reservation must outlive the longest provider timeout (default 20 min).
_RESERVATION_TTL_SECONDS = 1800.0
_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_started_at REAL NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0
)
"""


class BackgroundBudgetLedger:
    """Durable per-window counter shared by every process on one database."""

    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 10.0) -> None:
        self._db_path = Path(db_path)
        self._timeout_seconds = float(timeout_seconds)

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)}")
        connection.execute(_CREATE_SQL)
        return connection

    def try_acquire(self, *, limit: int, window_seconds: int) -> bool:
        """Reserve one background call, or return False when the window is spent.

        ``limit <= 0`` or ``window_seconds <= 0`` disables the budget and always
        admits; callers that pass those values should skip the ledger entirely,
        but tolerating them here keeps the contract total.
        """

        if limit <= 0 or window_seconds <= 0:
            return True
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    f"SELECT window_started_at, calls FROM {_TABLE} WHERE id = 1"
                ).fetchone()
                window_expired = row is None or now - float(row[0]) >= window_seconds
                if window_expired:
                    calls = 1
                    started_at = now
                else:
                    calls = int(row[1])
                    started_at = float(row[0])
                    if calls >= limit:
                        connection.execute("ROLLBACK")
                        return False
                    calls += 1
                connection.execute(
                    f"INSERT INTO {_TABLE} (id, window_started_at, calls) VALUES (1, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "window_started_at = excluded.window_started_at, "
                    "calls = excluded.calls",
                    (started_at, calls),
                )
                connection.execute("COMMIT")
                return True
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def count(self, *, window_seconds: int) -> int:
        """Return the calls reserved in the current window (0 when it rolled over)."""

        if window_seconds <= 0:
            return 0
        now = time.time()
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT window_started_at, calls FROM {_TABLE} WHERE id = 1"
            ).fetchone()
        if row is None or now - float(row[0]) >= window_seconds:
            return 0
        return int(row[1])

    def reset(self) -> None:
        """Start a fresh window (manual "continue" from a paused daemon)."""

        with closing(self._connect()) as connection:
            connection.execute(f"DELETE FROM {_TABLE} WHERE id = 1")


class DailySpendLedger:
    """Read today's estimated spend and record a once-per-day exceed event.

    The money cap (``scheduler.daily_budget_cny``, issue #353) is evaluated
    against ``llm_usage.estimated_cost_cny`` for the *local* calendar day. The
    query is a plain range scan on ``idx_llm_usage_timestamp`` so it stays
    cheap enough to run before every background provider request.

    Failures fail **open**: a missing ``llm_usage`` table (fresh install,
    pre-initialization) or a locked database returns 0.0 instead of blocking
    all LLM work. The call-count ledger in :class:`BackgroundBudgetLedger` is
    the opposite (it is the budget), but a spend *estimate* must never become
    an unintentional hard outage.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_seconds: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._timeout_seconds = float(timeout_seconds)
        self._now = now

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._db_path),
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)}")
        connection.execute(_CREATE_RESERVATIONS_SQL)
        connection.execute(_CREATE_RESERVATIONS_INDEX_SQL)
        return connection

    def _window(self) -> tuple[str, str, str]:
        return local_day_window(self._now() if self._now is not None else None)

    def spend_cny(self) -> float:
        """Return today's estimated spend in CNY (0.0 when unreadable)."""

        _, start_utc, end_utc = self._window()
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COALESCE(SUM(estimated_cost_cny), 0.0) FROM llm_usage "
                    "WHERE timestamp >= ? AND timestamp < ?",
                    (start_utc, end_utc),
                ).fetchone()
        except sqlite3.Error:
            logger.warning("Daily spend ledger unavailable; treating spend as 0", exc_info=True)
            return 0.0
        return max(0.0, float(row[0] or 0.0)) if row else 0.0

    def try_reserve(
        self,
        *,
        amount_cny: float,
        limit_cny: float,
        ttl_seconds: float = _RESERVATION_TTL_SECONDS,
    ) -> str | None:
        """Atomically reserve ``amount_cny`` against the shared daily ledger.

        Returns a reservation id, ``""`` when ``amount_cny <= 0`` (caller
        disabled reservations), or ``None`` when the cap would be exceeded.
        The spend + active-reservation sum and the insert share one
        ``BEGIN IMMEDIATE`` transaction, so sibling processes cannot both
        admit against the same headroom.
        """

        amount = max(0.0, float(amount_cny))
        if amount <= 0:
            return ""
        day, start_utc, _ = self._window()
        del day
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    f"DELETE FROM {_RESERVATIONS_TABLE} WHERE expires_at <= ?",
                    (now,),
                )
                spend_row = connection.execute(
                    "SELECT COALESCE(SUM(estimated_cost_cny), 0.0) FROM llm_usage "
                    "WHERE timestamp >= ? AND timestamp < ?",
                    (start_utc, self._window()[2]),
                ).fetchone()
                spend = float(spend_row[0] or 0.0) if spend_row else 0.0
                reserved_row = connection.execute(
                    f"SELECT COALESCE(SUM(amount_cny), 0.0) FROM {_RESERVATIONS_TABLE}"
                ).fetchone()
                reserved = float(reserved_row[0] or 0.0) if reserved_row else 0.0
                if spend + reserved + amount > float(limit_cny) + 1e-9:
                    connection.execute("ROLLBACK")
                    return None
                reservation_id = uuid.uuid4().hex
                connection.execute(
                    f"INSERT INTO {_RESERVATIONS_TABLE} "
                    "(id, amount_cny, created_at, expires_at) VALUES (?, ?, ?, ?)",
                    (reservation_id, amount, now, now + float(ttl_seconds)),
                )
                connection.execute("COMMIT")
                return reservation_id
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def release(self, reservation_id: str) -> None:
        """Release one reservation; unknown ids are a no-op."""

        token = str(reservation_id or "")
        if not token:
            return
        try:
            with closing(self._connect()) as connection:
                connection.execute(
                    f"DELETE FROM {_RESERVATIONS_TABLE} WHERE id = ?",
                    (token,),
                )
        except sqlite3.Error:
            logger.warning("Failed to release LLM budget reservation", exc_info=True)

    def active_reserved_cny(self) -> float:
        """Return the sum of unexpired reservations (0.0 when unreadable)."""

        now = time.time()
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        f"DELETE FROM {_RESERVATIONS_TABLE} WHERE expires_at <= ?",
                        (now,),
                    )
                    row = connection.execute(
                        f"SELECT COALESCE(SUM(amount_cny), 0.0) FROM {_RESERVATIONS_TABLE}"
                    ).fetchone()
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        except sqlite3.Error:
            logger.warning("Daily reservation ledger unavailable", exc_info=True)
            return 0.0
        return max(0.0, float(row[0] or 0.0)) if row else 0.0

    def record_exceeded(
        self,
        *,
        spend_cny: float,
        limit_cny: float,
        on_exceed: str,
    ) -> bool:
        """Insert one ``budget_exceeded`` event per local day.

        Returns True only when this call wrote the row. The check and insert
        share one ``BEGIN IMMEDIATE`` transaction so sibling processes cannot
        both write the event.
        """

        day, start_utc, _ = self._window()
        metadata = json.dumps(
            {
                "day": day,
                "spent_cny": round(float(spend_cny), 6),
                "limit_cny": round(float(limit_cny), 6),
                "mode": on_exceed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        created_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    existing = connection.execute(
                        "SELECT id FROM events WHERE event_type = ? AND created_at >= ? LIMIT 1",
                        ("budget_exceeded", start_utc),
                    ).fetchone()
                    if existing is not None:
                        connection.execute("ROLLBACK")
                        return False
                    connection.execute(
                        "INSERT INTO events (event_type, metadata, created_at) VALUES (?, ?, ?)",
                        ("budget_exceeded", metadata, created_at),
                    )
                    connection.execute("COMMIT")
                    return True
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        except sqlite3.Error:
            logger.warning("Failed to record budget_exceeded event", exc_info=True)
            return False
