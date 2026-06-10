"""X (Twitter) account subscription storage.

Account subscriptions track X handles the user wants discovery to follow.
Unlike XHS there is NO extension round-trip: the X producer (server-side)
fetches each subscription via ``XCreatorStrategy`` using harvested cookies.
This module only owns the ``x_creator_subscriptions`` table + CRUD; it
mirrors ``xhs_tasks.XhsCreatorStore`` (table shape, idempotent insert,
``last_fetched_at`` scheduling helpers).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


def normalize_handle(handle: str) -> str:
    """Normalize an X handle: strip whitespace and a single leading ``@``."""
    return handle.strip().lstrip("@").strip()


class XCreatorStore:
    """Manages the ``x_creator_subscriptions`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS x_creator_subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                handle          TEXT NOT NULL UNIQUE,
                added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def add(self, handle: str) -> None:
        """Add a subscription (idempotent on the normalized handle)."""
        normalized = normalize_handle(handle)
        self._db.conn.execute(
            "INSERT OR IGNORE INTO x_creator_subscriptions (handle) VALUES (?)",
            (normalized,),
        )
        self._db.conn.commit()

    def list_all(self) -> list[dict[str, Any]]:
        """Return all subscriptions, oldest first."""
        rows = self._db.conn.execute(
            "SELECT * FROM x_creator_subscriptions ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, sub_id: int) -> bool:
        """Delete a subscription by primary key. Returns True if a row was removed."""
        cursor = self._db.conn.execute(
            "DELETE FROM x_creator_subscriptions WHERE id = ?",
            (sub_id,),
        )
        self._db.conn.commit()
        return cursor.rowcount > 0

    def due_for_fetch(self, *, hours: int = 24) -> list[dict[str, Any]]:
        """Return subscriptions whose ``last_fetched_at`` is older than ``hours`` ago."""
        rows = self._db.conn.execute(
            "SELECT * FROM x_creator_subscriptions "
            "WHERE last_fetched_at IS NULL "
            "   OR last_fetched_at < datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_fetched(self, sub_id: int) -> None:
        """Update ``last_fetched_at`` to now."""
        self._db.conn.execute(
            "UPDATE x_creator_subscriptions SET last_fetched_at = CURRENT_TIMESTAMP WHERE id = ?",
            (sub_id,),
        )
        self._db.conn.commit()
