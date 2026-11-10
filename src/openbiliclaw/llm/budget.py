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
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

_TABLE = "background_llm_budget"
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
