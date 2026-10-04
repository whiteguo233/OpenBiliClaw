"""Shared TikTok request pacing and durable source-wide rate-limit state."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TiktokRequestError(RuntimeError):
    """A classified upstream failure, never an affirmative empty listing."""

    def __init__(self, reason: str, *, retry_after: float = 0) -> None:
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(f"TikTok request failed: {reason}")


@dataclass
class TiktokRequestState:
    """Coordinate formal/inspiration clients and processes via a tiny SQLite ledger."""

    path: Path
    interval_seconds: float = 2

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS request_state (id INTEGER PRIMARY KEY, "
            "next_at REAL NOT NULL DEFAULT 0, cooldown REAL NOT NULL DEFAULT 0)"
        )
        conn.execute("INSERT OR IGNORE INTO request_state(id) VALUES (1)")
        conn.commit()
        return conn

    def cooldown_remaining(self) -> float:
        conn = self._connect()
        try:
            row = conn.execute("SELECT cooldown FROM request_state WHERE id=1").fetchone()
            return max(0.0, float(row[0]) - time.time())
        finally:
            conn.close()

    def defer(self, seconds: float) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE request_state SET cooldown=MAX(cooldown, ?) WHERE id=1",
                    (time.time() + max(0, seconds),),
                )
        finally:
            conn.close()

    def before_request(self) -> None:
        # Reserve a slot atomically, then sleep outside the transaction. Recheck
        # cooldown after sleeping: another client may have received a 429.
        conn = self._connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                next_at, cooldown = conn.execute(
                    "SELECT next_at, cooldown FROM request_state WHERE id=1"
                ).fetchone()
                now = time.time()
                if cooldown > now:
                    raise TiktokRequestError("rate_limited", retry_after=cooldown - now)
                slot = max(now, next_at)
                conn.execute(
                    "UPDATE request_state SET next_at=? WHERE id=1",
                    (slot + max(0, self.interval_seconds),),
                )
            time.sleep(max(0, slot - time.time()))
            remaining = self.cooldown_remaining()
            if remaining:
                raise TiktokRequestError("rate_limited", retry_after=remaining)
        finally:
            conn.close()
