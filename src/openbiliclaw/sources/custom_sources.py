"""Persistent user-defined discovery sources, separate from transport families."""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from openbiliclaw.sources.web_recipe import _normalize_public_url

if TYPE_CHECKING:
    import sqlite3

_SOURCE_ID = re.compile(r"custom_[0-9a-f]{32}\Z")
_POLICY_FIELDS = frozenset({"name", "target_share", "interval_minutes", "enabled"})
_FIELDS = frozenset(
    {"name", "entry_url", "intent", "target_share", "interval_minutes", "enabled", "session_id"}
)


def is_custom_source_id(value: object) -> bool:
    """Return whether a value has the allocated custom-source ID shape."""
    return isinstance(value, str) and _SOURCE_ID.fullmatch(value) is not None


def ensure_custom_source_schema(connection: sqlite3.Connection) -> None:
    """Create the registry and canonical-content provenance ledger idempotently."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS custom_sources (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            entry_url TEXT NOT NULL,
            origin TEXT NOT NULL,
            intent TEXT NOT NULL,
            target_share INTEGER NOT NULL DEFAULT 1,
            interval_minutes INTEGER NOT NULL DEFAULT 30,
            enabled INTEGER NOT NULL DEFAULT 1,
            archived INTEGER NOT NULL DEFAULT 0,
            session_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(custom_sources)")}
    if "archived" not in columns:
        connection.execute(
            "ALTER TABLE custom_sources ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
        )
    connection.execute("""
        CREATE TABLE IF NOT EXISTS content_source_provenance (
            item_key TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (item_key, source_id)
        )
    """)


class CustomSourceStore:
    """Manage discovery identity and policy without owning credentials or workflows."""

    def __init__(self, database: Any) -> None:
        self.database = database
        ensure_custom_source_schema(database.conn)

    def list_sources(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = " WHERE archived = 0" + (" AND enabled = 1" if enabled_only else "")
        rows = self.database.conn.execute(
            "SELECT * FROM custom_sources" + where + " ORDER BY created_at, source_id"
        ).fetchall()
        return [self._row(row) for row in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        row = self.database.conn.execute(
            "SELECT * FROM custom_sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        return self._row(row) if row is not None else None

    def upsert_source(
        self,
        *,
        source_id: str = "",
        name: str,
        entry_url: str,
        intent: str,
        target_share: int = 1,
        interval_minutes: int = 30,
        enabled: bool = True,
        session_id: str = "",
        commit: bool = True,
        allow_dedicated_feed: bool = False,
    ) -> dict[str, Any]:
        """Create or replace one explicitly identified source, even on the same origin."""
        source_id = source_id or f"custom_{uuid.uuid4().hex}"
        if not is_custom_source_id(source_id):
            raise ValueError("Invalid custom source ID")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise ValueError("Source name must contain 1–200 characters")
        if not isinstance(intent, str) or not intent.strip() or len(intent.strip()) > 4000:
            raise ValueError("Source intent must contain 1–4000 characters")
        if (
            isinstance(target_share, bool)
            or not isinstance(target_share, int)
            or not 1 <= target_share <= 20
        ):
            raise ValueError("target_share must be an integer between 1 and 20")
        if (
            isinstance(interval_minutes, bool)
            or not isinstance(interval_minutes, int)
            or not 5 <= interval_minutes <= 10080
        ):
            raise ValueError("interval_minutes must be an integer between 5 and 10080")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(session_id, str) or len(session_id) > 200:
            raise ValueError("Invalid session reference")
        url = _normalize_public_url(
            entry_url, field="entry_url", allow_dedicated=allow_dedicated_feed
        )
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        current = self.get_source(source_id)
        if current is not None and current["archived"]:
            raise ValueError("Archived custom source cannot be updated")
        execute = self.database._execute_write if commit else self.database.conn.execute
        execute(
            """INSERT INTO custom_sources
               (source_id, name, entry_url, origin, intent, target_share, interval_minutes,
                enabled, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                   name = excluded.name, entry_url = excluded.entry_url, origin = excluded.origin,
                   intent = excluded.intent, target_share = excluded.target_share,
                   interval_minutes = excluded.interval_minutes, enabled = excluded.enabled,
                   session_id = excluded.session_id, updated_at = CURRENT_TIMESTAMP""",
            (
                source_id,
                name.strip(),
                url,
                origin,
                intent.strip(),
                target_share,
                interval_minutes,
                int(enabled),
                session_id.strip(),
            ),
        )
        # Names are display policy, never identity. Keep the two durable
        # content projections current while historical provenance remains valid.
        for table in ("content_cache", "discovery_candidates", "content_source_provenance"):
            execute(
                f"UPDATE {table} SET source_name = ? WHERE source_id = ?",
                (name.strip(), source_id),
            )
        row = self.get_source(source_id)
        assert row is not None
        return row

    def update_source(
        self, source_id: str, *, commit: bool = True, **fields: Any
    ) -> dict[str, Any]:
        """Update only source policy fields; omitted session references are retained."""
        unknown = fields.keys() - _POLICY_FIELDS
        if unknown:
            raise ValueError(f"Unknown source fields: {', '.join(sorted(unknown))}")
        current = self.get_source(source_id)
        if current is None:
            raise ValueError("Unknown custom source")
        values = {key: current[key] for key in _FIELDS}
        values.update(fields)
        # Policy updates reuse the immutable, previously validated source
        # identity; callers cannot introduce a new dedicated-platform URL here.
        return self.upsert_source(
            source_id=source_id, commit=commit, allow_dedicated_feed=True, **values
        )

    def archive_source(self, source_id: str) -> bool:
        """Hide a source and stop scheduling while retaining its durable history."""
        return bool(
            self.database._execute_write(
                "UPDATE custom_sources SET archived = 1, enabled = 0, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE source_id = ? AND archived = 0",
                (source_id,),
            ).rowcount
        )

    def delete_source(self, source_id: str) -> bool:
        """Delete a source without erasing historical content or a shared login."""
        return bool(
            self.database._execute_write(
                "DELETE FROM custom_sources WHERE source_id = ?", (source_id,)
            ).rowcount
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        value["archived"] = bool(value["archived"])
        return value
