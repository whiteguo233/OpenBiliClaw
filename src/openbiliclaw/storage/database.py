"""SQLite database management.

Provides async-compatible SQLite operations for event logs,
content cache, and recommendation history.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)
# v0.3.62+: retry budget tightened from 5×100ms (worst-case 500ms
# blocking the asyncio event loop on lock contention) to 8×20ms
# (worst-case 160ms). Same total absolute timeout floor (~160-500ms)
# is preserved by raising attempt count; per-attempt sleep is short
# enough that even if it fires inside an async context the event-loop
# stutter is below human-perception thresholds. Most writes succeed
# on the first try anyway — this only matters under heavy concurrent
# write load (refresh tick + ingest + classify all hammering pool
# rows simultaneously). A future rewrite can move to asyncio.to_thread
# for true non-blocking DB I/O, but that's a larger refactor (every
# caller must become async) — for now this constant tweak is the
# pragmatic middle ground.
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
_BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)")
_VIEW_CONTENT_ID_METADATA_KEYS = (
    "content_id",
    "bvid",
    "note_id",
    "aweme_id",
    "video_id",
    "yt_video_id",
)
_XHS_SOURCE_FAMILY = "xiaohongshu"
_XHS_SOURCE_PREFIXES = ("xhs-", "xhs_", "xiaohongshu")
_DOUYIN_SOURCE_FAMILY = "douyin"
_DOUYIN_SOURCE_PREFIXES = ("dy-", "dy_", "douyin")
_BILIBILI_SOURCE_FAMILY = "bilibili"
_BILIBILI_SOURCE_KEYS = ("search", "related_chain", "trending", "explore")
_YOUTUBE_SOURCE_FAMILY = "youtube"
_YOUTUBE_SOURCE_PREFIXES = ("yt-", "yt_", "youtube")
_EXPLORE_HIGH_RISK_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "manufacturing",
        ("制造", "工艺", "工厂", "工业", "材料", "金属", "芯片", "显微", "纳米", "疲劳"),
    ),
    (
        "game_theory",
        ("博弈", "桌游", "纳什", "机制", "策略模型", "平衡性"),
    ),
)

# Schema version for migrations
_SCHEMA_VERSION = 2

_SCHEMA_SQL = """
-- Event log (behavioral data from browser extension)
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type            TEXT NOT NULL,        -- click, search, scroll, comment, etc.
    url                   TEXT,
    title                 TEXT,
    context               TEXT,                 -- JSON: DOM snapshot reference, viewport, etc.
    metadata              TEXT,                 -- JSON: additional event-specific data
    -- v0.3.x event-satisfaction signal: deterministic classification
    -- written at insert time by ``classify_event_satisfaction``. NULL on
    -- pre-migration rows; consumers treat NULL as ``unknown``.
    inferred_satisfaction TEXT,                 -- "positive" | "neutral" | "negative" | "unknown"
    satisfaction_reason   TEXT,                 -- short snake_case reason; see event_format.py
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Content cache (discovered/evaluated content)
CREATE TABLE IF NOT EXISTS content_cache (
    bvid        TEXT PRIMARY KEY,
    title       TEXT,
    up_name     TEXT,
    up_mid      INTEGER,
    duration    INTEGER,
    tags        TEXT,                 -- JSON array
    topic_key   TEXT DEFAULT '',
    style_key   TEXT DEFAULT '',
    franchise_key TEXT DEFAULT '',  -- LLM IP/series; see _ensure_content_cache_topic_columns
    description TEXT,
    cover_url   TEXT,
    view_count  INTEGER DEFAULT 0,
    like_count  INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0.0,
    relevance_reason TEXT DEFAULT '',
    pool_expression TEXT DEFAULT '',
    pool_topic_label TEXT DEFAULT '',
    candidate_tier TEXT DEFAULT 'primary',
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notification_sent INTEGER DEFAULT 0,
    notified_at TIMESTAMP,
    pool_status TEXT DEFAULT 'fresh',
    recommended_at TIMESTAMP,
    feedback_type TEXT,
    feedback_at TIMESTAMP,
    source      TEXT                 -- Which discovery strategy found it
);

-- Recommendation history
CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT NOT NULL,
    expression  TEXT,                -- Friend-style recommendation text
    topic       TEXT,                -- Personal topic label
    confidence  REAL DEFAULT 0.0,
    presented   INTEGER DEFAULT 0,   -- Boolean
    feedback    TEXT,                -- User feedback (like/dislike/comment)
    feedback_type TEXT,
    feedback_note TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    presented_at TIMESTAMP,
    feedback_at TIMESTAMP,
    FOREIGN KEY (bvid) REFERENCES content_cache(bvid)
);

-- Durable popup chat turns.  These let the side panel recover in-flight
-- and completed replies after Chrome reloads or discards the panel page.
CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id       TEXT PRIMARY KEY,
    session       TEXT NOT NULL DEFAULT 'popup',
    scope         TEXT NOT NULL DEFAULT 'chat',
    subject_id    TEXT NOT NULL DEFAULT '',
    subject_title TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reply         TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
    ON chat_turns(session, created_at, turn_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
    ON chat_turns(scope, subject_id, created_at);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Per-call LLM usage ledger. Populated by ``UsageRecorder`` after every
-- successful provider response. Used by ``openbiliclaw cost`` to print
-- daily spend summaries and by future per-module attribution work.
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    -- v0.3.28+: portion of prompt_tokens served from provider-side
    -- prompt cache. Always <= prompt_tokens. 0 means cache miss / no
    -- caching. Used to compute cache hit rate per caller.
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_cny REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider, model);
"""


def _pool_source_family(source: object, source_platform: object = "") -> str:
    """Return the source family key used by pool share accounting."""
    platform = str(source_platform or "").strip().lower()
    raw_source = str(source or "").strip()
    source_key = raw_source.lower()
    if platform in {_XHS_SOURCE_FAMILY, "xhs"} or source_key.startswith(_XHS_SOURCE_PREFIXES):
        return _XHS_SOURCE_FAMILY
    if platform in {_DOUYIN_SOURCE_FAMILY, "dy"} or source_key.startswith(_DOUYIN_SOURCE_PREFIXES):
        return _DOUYIN_SOURCE_FAMILY
    if platform in {_YOUTUBE_SOURCE_FAMILY, "yt"} or source_key.startswith(
        _YOUTUBE_SOURCE_PREFIXES
    ):
        return _YOUTUBE_SOURCE_FAMILY
    if platform in {_BILIBILI_SOURCE_FAMILY, "bili"} or source_key in _BILIBILI_SOURCE_KEYS:
        return _BILIBILI_SOURCE_FAMILY
    return raw_source or "unknown"


def _normalize_source_platform_key(source_platform: object) -> str:
    """Return the canonical source key used in cross-source content IDs."""
    raw = str(source_platform or "").strip().lower()
    if raw in {_XHS_SOURCE_FAMILY, "xhs"}:
        return _XHS_SOURCE_FAMILY
    if raw in {_DOUYIN_SOURCE_FAMILY, "dy"}:
        return _DOUYIN_SOURCE_FAMILY
    if raw in {_YOUTUBE_SOURCE_FAMILY, "yt"}:
        return _YOUTUBE_SOURCE_FAMILY
    if raw in {_BILIBILI_SOURCE_FAMILY, "bili"}:
        return _BILIBILI_SOURCE_FAMILY
    return raw


def _is_linkable_pool_source(
    source: object,
    source_platform: object,
    content_url: object,
) -> bool:
    """Return False for xhs rows that cannot be opened from recommendations."""
    if _pool_source_family(source, source_platform) != _XHS_SOURCE_FAMILY:
        return True
    return "xsec_token=" in str(content_url or "")


class Database:
    """Lightweight SQLite wrapper for OpenBiliClaw.

    Manages the event log, content cache, and recommendation history.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Initialize the database and run migrations if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_event_satisfaction_columns()
        self._ensure_recommendation_feedback_columns()
        self._ensure_content_cache_runtime_columns()
        self._ensure_content_cache_relevance_columns()
        self._ensure_content_cache_topic_columns()
        self._ensure_content_cache_pool_copy_columns()
        self._ensure_content_cache_delight_columns()
        self._ensure_content_cache_multisource_columns()
        self._ensure_source_recipes_table()
        self._ensure_xhs_observed_urls_table()
        self._ensure_llm_usage_cache_columns()
        self._ensure_chat_turns_table()

        # Set schema version
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        self._conn.commit()
        logger.info("Database initialized at %s", self._db_path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._conn

    def open_connection(self) -> sqlite3.Connection:
        """Open a short-lived connection to the initialized database.

        Use this for explicit transactions that may run from FastAPI's
        threadpool. A separate connection lets SQLite serialize writers
        with ``busy_timeout`` instead of nesting transactions on the
        process-wide connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _ensure_fresh_read(self) -> None:
        """Close any implicit transaction so the next SELECT sees the latest WAL state.

        When a CLI command (a separate process) writes to the same database,
        this server process may still hold a stale read snapshot inside an
        implicit transaction.  Committing closes that transaction so the next
        query starts a new one against the current WAL head.
        """
        if self.conn.in_transaction:
            self.conn.commit()

    def _execute_write(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Cursor:
        """Execute a write with short retry on transient SQLite locks."""
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.execute(sql, params)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def insert_event(self, event_type: str, **kwargs: Any) -> int:
        """Insert a behavioral event.

        v0.3.23+: ``context`` is now a natural-language string (from
        ``event_format.build_event()``). It's stored as raw text — no
        outer JSON wrapping — so consumers reading via SELECT get back
        the same string they put in. Pre-v0.3.22 callers that passed
        dict-shaped context still work: dicts / lists / other non-string
        values are JSON-encoded for storage so older code paths don't
        suddenly lose data.

        Args:
            event_type: Type of event.
            **kwargs: Additional event fields. ``context`` may be str,
                dict, list, or None.

        Returns:
            Inserted row ID.
        """
        import json

        from openbiliclaw.sources.event_format import classify_event_satisfaction

        raw_context = kwargs.get("context", "")
        if isinstance(raw_context, str):
            context_text = raw_context
        elif raw_context is None:
            context_text = ""
        else:
            # Legacy dict / list payload — JSON-encode for storage.
            context_text = json.dumps(raw_context, ensure_ascii=False)

        metadata_payload = kwargs.get("metadata", {})

        # Single classification owner. Reconstruct the event dict shape
        # the classifier expects (event_type + url + title + metadata).
        # API ingest may set dwell fields at the top level as well; pass
        # those through so the click rules read either location.
        classifier_event: dict[str, Any] = {
            "event_type": event_type,
            "url": kwargs.get("url", ""),
            "title": kwargs.get("title", ""),
            "metadata": metadata_payload if isinstance(metadata_payload, dict) else {},
        }
        for top_level_key in ("watch_seconds", "video_duration_seconds"):
            if top_level_key in kwargs and kwargs[top_level_key] is not None:
                classifier_event[top_level_key] = kwargs[top_level_key]
        inferred_satisfaction, satisfaction_reason = classify_event_satisfaction(
            classifier_event
        )

        cursor = self._execute_write(
            "INSERT INTO events "
            "(event_type, url, title, context, metadata, "
            " inferred_satisfaction, satisfaction_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                kwargs.get("url", ""),
                kwargs.get("title", ""),
                context_text,
                json.dumps(metadata_payload, ensure_ascii=False),
                inferred_satisfaction,
                satisfaction_reason,
            ),
        )
        return cursor.lastrowid or 0

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent events.

        Args:
            limit: Maximum number of events.

        Returns:
            List of event dicts.
        """
        cursor = self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Durable popup chat turns
    # ------------------------------------------------------------------

    def create_chat_turn(
        self,
        *,
        turn_id: str,
        message: str,
        session: str = "popup",
        scope: str = "chat",
        subject_id: str = "",
        subject_title: str = "",
    ) -> dict[str, Any]:
        """Create a pending popup chat turn if it does not already exist."""
        self._execute_write(
            """
            INSERT OR IGNORE INTO chat_turns (
                turn_id, session, scope, subject_id, subject_title, message, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                turn_id,
                session or "popup",
                scope or "chat",
                subject_id or "",
                subject_title or "",
                message,
            ),
        )
        row = self.get_chat_turn(turn_id)
        if row is None:
            raise RuntimeError(f"Failed to create chat turn {turn_id!r}")
        return row

    def complete_chat_turn(self, turn_id: str, *, reply: str) -> None:
        """Mark a pending popup chat turn as completed."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'completed',
                reply = ?,
                error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, turn_id),
        )

    def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> None:
        """Mark a popup chat turn as failed while preserving visible copy."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'failed',
                reply = ?,
                error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, error, turn_id),
        )

    def get_chat_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Return one durable popup chat turn by id."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM chat_turns
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_chat_turns(
        self,
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent popup chat turns in display order."""
        self._ensure_fresh_read()
        clauses = ["session = ?"]
        params: list[Any] = [session or "popup"]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"""
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM (
                SELECT turn_id, session, scope, subject_id, subject_title, message,
                       status, reply, error, created_at, updated_at
                FROM chat_turns
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, turn_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, turn_id ASC
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # LLM usage ledger
    # ------------------------------------------------------------------

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int:
        """Append one LLM-call usage record.

        ``cached_input_tokens`` (v0.3.28+) is the portion of
        ``prompt_tokens`` served from provider-side prompt cache —
        always ``<= prompt_tokens``. 0 means no cache use. Used by
        ``cost --by caller`` to compute hit rates and by
        ``estimate_cost`` to discount cached tokens correctly.
        """
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        cursor = self._execute_write(
            """INSERT INTO llm_usage
               (provider, model, caller, prompt_tokens, completion_tokens,
                total_tokens, cached_input_tokens, estimated_cost_cny,
                success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider or "",
                model or "",
                caller or "",
                int(max(0, prompt_tokens)),
                int(max(0, completion_tokens)),
                int(total),
                int(max(0, cached_input_tokens)),
                float(estimated_cost_cny),
                1 if success else 0,
            ),
        )
        return cursor.lastrowid or 0

    def query_llm_usage_by_day(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-day aggregates for the last ``days`` days.

        Each row: {day, calls, prompt_tokens, completion_tokens,
        total_tokens, cost_cny}. Days with zero usage are omitted —
        the CLI fills gaps for display.
        """
        cursor = self.conn.execute(
            """
            SELECT date(timestamp, 'localtime') AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY day
            ORDER BY day DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_provider(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-(provider, model) totals over the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY provider, model
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_caller(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-caller totals over the last ``days`` days.

        ``caller`` is a free-form string the LLM service tags into each
        row (e.g. ``discovery.evaluate`` / ``recommendation.write`` /
        ``soul.profile``). Untagged calls land under ``""`` which the
        CLI renders as ``(untagged)``. Result is sorted by cost so the
        first row is the most expensive caller.

        v0.3.28+ also returns ``cached_input_tokens`` so the CLI can
        compute and surface per-caller cache hit rates — a low rate
        (< 30%) signals prompt-prefix instability worth investigating.
        """
        cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_total(self, *, days: int = 7) -> dict[str, Any]:
        """Return a single-row total for the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            """,
            (max(1, int(days)),),
        )
        row = cursor.fetchone()
        return (
            dict(row)
            if row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

    def max_llm_usage_id(self) -> int:
        """Return the highest currently-stored ``llm_usage.id`` (0 if empty).

        Used as a checkpoint for "what's been billed since this point"
        queries — the init / discovery cycle wrappers snapshot it on
        entry and pass it to ``query_llm_usage_since_id`` on exit to
        scope the cost summary to that single phase.
        """
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM llm_usage")
        row = cursor.fetchone()
        return int(row["m"]) if row else 0

    def query_llm_usage_since_id(self, *, since_id: int) -> dict[str, Any]:
        """Return per-caller breakdown + totals for rows ``id > since_id``.

        Output: ``{"total": {calls, prompt_tokens, completion_tokens,
        cost_cny}, "by_caller": [{caller, calls, ...}, ...]}``. Bound
        to a single phase by passing ``max_llm_usage_id()`` taken at
        the phase entry.
        """
        total_cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            """,
            (int(since_id),),
        )
        total_row = total_cursor.fetchone()
        total = (
            dict(total_row)
            if total_row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

        caller_cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (int(since_id),),
        )
        return {
            "total": total,
            "by_caller": [dict(row) for row in caller_cursor.fetchall()],
        }

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
        satisfaction_modes: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters.

        ``satisfaction_modes`` filters by ``inferred_satisfaction``. When
        the set includes ``"unknown"``, rows with a NULL classification
        (pre-migration legacy rows) are also returned.
        """
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if keyword:
            like = f"%{keyword}%"
            clauses.append("(url LIKE ? OR title LIKE ? OR metadata LIKE ?)")
            params.extend([like, like, like])

        if satisfaction_modes is not None:
            modes = list(satisfaction_modes)
            mode_clauses: list[str] = []
            if modes:
                placeholders = ", ".join("?" for _ in modes)
                mode_clauses.append(f"inferred_satisfaction IN ({placeholders})")
                params.extend(modes)
            if "unknown" in satisfaction_modes:
                mode_clauses.append("inferred_satisfaction IS NULL")
            if mode_clauses:
                clauses.append("(" + " OR ".join(mode_clauses) + ")")
            else:
                # Empty modes set explicitly requested → match nothing.
                clauses.append("1 = 0")

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_events_by_type(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event type."""
        sql = "SELECT event_type, COUNT(*) AS count FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} GROUP BY event_type ORDER BY event_type ASC"
        cursor = self.conn.execute(sql, params)
        return {str(row["event_type"]): int(row["count"]) for row in cursor.fetchall()}

    def cache_content(self, bvid: str, **kwargs: Any) -> None:
        """Cache discovered content.

        Args:
            bvid: Video BV ID.
            **kwargs: Content fields.
        """
        import json

        self._execute_write(
            """
            INSERT INTO content_cache (
                bvid,
                title,
                up_name,
                up_mid,
                duration,
                tags,
                topic_key,
                topic_group,
                style_key,
                franchise_key,
                description,
                cover_url,
                view_count,
                like_count,
                relevance_score,
                relevance_reason,
                pool_expression,
                pool_topic_label,
                candidate_tier,
                last_scored_at,
                source,
                content_id,
                content_url,
                source_platform,
                author_name
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, ?, ?, ?, ?, ?
            )
            ON CONFLICT(bvid) DO UPDATE SET
                title = excluded.title,
                up_name = excluded.up_name,
                up_mid = excluded.up_mid,
                duration = excluded.duration,
                tags = excluded.tags,
                -- Preserve LLM-classified fields: when the incoming value
                -- is empty/zero, keep the existing DB value.  This prevents
                -- re-ingest from raw sources (e.g. xhs extension re-sending
                -- the same notes on every page load) from wiping out
                -- classifications that classify_pool_backlog has written.
                topic_key = COALESCE(
                    NULLIF(excluded.topic_key, ''),
                    content_cache.topic_key,
                    ''
                ),
                topic_group = COALESCE(
                    NULLIF(excluded.topic_group, ''),
                    content_cache.topic_group,
                    ''
                ),
                style_key = COALESCE(
                    NULLIF(excluded.style_key, ''),
                    content_cache.style_key,
                    ''
                ),
                franchise_key = COALESCE(
                    NULLIF(excluded.franchise_key, ''),
                    content_cache.franchise_key,
                    ''
                ),
                description = excluded.description,
                cover_url = excluded.cover_url,
                view_count = excluded.view_count,
                like_count = excluded.like_count,
                relevance_score = CASE
                    WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                    ELSE COALESCE(content_cache.relevance_score, 0)
                END,
                relevance_reason = COALESCE(
                    NULLIF(excluded.relevance_reason, ''),
                    content_cache.relevance_reason,
                    ''
                ),
                pool_expression = COALESCE(
                    NULLIF(excluded.pool_expression, ''),
                    content_cache.pool_expression,
                    ''
                ),
                pool_topic_label = COALESCE(
                    NULLIF(excluded.pool_topic_label, ''),
                    content_cache.pool_topic_label,
                    ''
                ),
                candidate_tier = excluded.candidate_tier,
                last_scored_at = CURRENT_TIMESTAMP,
                -- Re-fresh items previously trim-suppressed: 'suppressed' is
                -- an internal diversity decision (over-quota cuts, topic cap),
                -- not a user signal. When a discovery strategy re-finds the
                -- item it deserves another shot. Without this, B站 trending
                -- (which churns slowly) stays bottlenecked because most hot
                -- BVIDs are already cached as 'suppressed' from earlier
                -- trim cycles. User-driven states ('shown', 'feedbacked',
                -- 'purged_by_dislike') are preserved.
                pool_status = CASE
                    WHEN content_cache.pool_status = 'suppressed' THEN 'fresh'
                    ELSE content_cache.pool_status
                END,
                source = excluded.source,
                content_id = excluded.content_id,
                content_url = excluded.content_url,
                source_platform = excluded.source_platform,
                author_name = COALESCE(
                    NULLIF(excluded.author_name, ''),
                    content_cache.author_name,
                    ''
                )
            """,
            (
                bvid,
                kwargs.get("title", ""),
                kwargs.get("up_name", ""),
                kwargs.get("up_mid", 0),
                kwargs.get("duration", 0),
                json.dumps(kwargs.get("tags", []), ensure_ascii=False),
                kwargs.get("topic_key", ""),
                kwargs.get("topic_group", ""),
                kwargs.get("style_key", ""),
                kwargs.get("franchise_key", ""),
                kwargs.get("description", ""),
                kwargs.get("cover_url", ""),
                kwargs.get("view_count", 0),
                kwargs.get("like_count", 0),
                kwargs.get("relevance_score", 0.0),
                kwargs.get("relevance_reason", ""),
                kwargs.get("pool_expression", ""),
                kwargs.get("pool_topic_label", ""),
                kwargs.get("candidate_tier", "primary"),
                kwargs.get("source", ""),
                kwargs.get("content_id", bvid),
                kwargs.get("content_url", ""),
                kwargs.get("source_platform", "bilibili"),
                kwargs.get("author_name", ""),
            ),
        )

    def get_cached_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached discovered content ordered by basic quality signals."""
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_unrecommended_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached content that has not been recommended yet."""
        cursor = self.conn.execute(
            """
            SELECT c.*
            FROM content_cache AS c
            WHERE NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = c.bvid
            )
            ORDER BY
                CASE c.candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                c.relevance_score DESC,
                c.last_scored_at DESC,
                c.view_count DESC,
                c.bvid ASC
            LIMIT ?
            """,
            (max(limit * 5, 50),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def get_pool_candidates(
        self,
        limit: int = 20,
        *,
        max_per_topic_group: int = 3,
    ) -> list[dict[str, Any]]:
        """Get fresh recommendation candidates directly from the discovery pool.

        ``max_per_topic_group`` caps how many items from any single
        ``topic_group`` enter the relevance-ordered head. Without this
        cap, a 600-item pool that contains 270 distinct topic_groups still
        produces a top-50 shortlist concentrated in ~10 head groups,
        because high-relevance candidates cluster around the user's
        primary interests; long-tail groups (197 with a single item each
        in the typical pool) never reach the candidate window. Cap of 3
        lets obvious favourites keep a strong presence while opening
        room for ~40+ different groups in the candidate window. Pass
        ``max_per_topic_group=0`` to restore the legacy unrestricted
        ordering for callers that need it (e.g. health checks).

        Notes:
            xhs rows without ``xsec_token`` in their ``content_url`` are
            excluded. Bare xhs URLs get rejected by xhs with error 300031
            when shared outbound, so surfacing them in recommendations
            would just mint dead links. Tokens get backfilled by the
            MAIN-world sniffer as the user browses xhs; bare rows become
            eligible again once ``_backfill_xhs_tokens`` upgrades them.
        """
        self._ensure_fresh_read()
        # Over-fetch widely so the per-group filter still leaves headroom
        # for the downstream balance pass.
        fetch_limit = max(limit * 8, 80)
        if max_per_topic_group <= 0:
            sql = """
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR content_url LIKE '%xsec_token=%'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (fetch_limit,)
        else:
            # Per-group rank via window function: keep the top-N items of
            # each topic_group (and all items with empty topic_group, which
            # are untracked). Then order the remainder by relevance.
            sql = """
                WITH ranked AS (
                    SELECT *,
                           CASE
                               WHEN COALESCE(topic_group, '') = '' THEN 1
                               ELSE ROW_NUMBER() OVER (
                                   PARTITION BY topic_group
                                   ORDER BY
                                       relevance_score DESC,
                                       last_scored_at DESC,
                                       view_count DESC,
                                       bvid ASC
                               )
                           END AS group_rank
                    FROM content_cache
                    WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR content_url LIKE '%xsec_token=%'
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM recommendations AS r
                        WHERE r.bvid = content_cache.bvid
                      )
                )
                SELECT * FROM ranked
                WHERE
                    COALESCE(topic_group, '') = ''
                    OR group_rank <= ?
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params = (max_per_topic_group, fetch_limit)
        cursor = self.conn.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def count_pool_candidates(self) -> int:
        """Return how many fresh candidates are immediately available for reshuffle.

        v0.3.57+: matches ``get_pool_candidates`` precompute gate — rows
        without ``pool_expression`` / ``pool_topic_label`` are excluded so
        the popup's "还有 N 条" never overstates what serve() can actually
        return.
        """
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        return sum(
            1
            for row in cursor.fetchall()
            if str(row["bvid"]).strip()
            and not self._is_viewed_row(dict(row), viewed_content_keys)
            and _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            )
        )

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        """Return fresh pool counts grouped by discovery source family."""
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        """Return fresh pool counts grouped by topic, style, and franchise."""
        cursor = self.conn.execute(
            """
            SELECT bvid, topic_group, style_key, franchise_key, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, dict[str, int]] = {
            "topic_group": defaultdict(int),
            "style_key": defaultdict(int),
            "franchise_key": defaultdict(int),
        }
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            for axis in ("topic_group", "style_key", "franchise_key"):
                value = str(row[axis] or "").strip()
                if value:
                    counts[axis][value] += 1
        return {axis: dict(axis_counts) for axis, axis_counts in counts.items()}

    def canonicalize_topic_groups(self, canonical_map: dict[str, str]) -> int:
        """Rewrite ``content_cache.topic_group`` to canonical form per map.

        v0.3.56+: ``canonical_map`` is built by
        ``RecommendationEngine.prewarm_supergroup_embeddings`` and maps
        normalized (lowered + stripped) topic_group → canonical form.
        Without applying it to the database rows, the merge only fires
        at serve time and downstream analytics (``get_topic_group_samples``,
        per-topic counts in popup status) see the un-merged labels.

        Returns the number of rows actually updated. Empty input or all-
        identity mappings short-circuit to 0.
        """
        if not canonical_map:
            return 0
        # Bulk update: one statement per (src → dst) pair. Pure SQL,
        # no row-level fetch. WAL-friendly because we batch in a single
        # transaction. Only rewrites rows whose lowercased+trimmed
        # topic_group exactly matches the source key — case-preserving
        # storage stays intact for non-matching rows.
        total = 0
        for src, dst in canonical_map.items():
            if src == dst or not src or not dst:
                continue
            cursor = self._execute_write(
                """
                UPDATE content_cache
                SET topic_group = ?
                WHERE LOWER(TRIM(COALESCE(topic_group, ''))) = ?
                  AND COALESCE(topic_group, '') != ?
                """,
                (dst, src, dst),
            )
            total += cursor.rowcount or 0
        return total

    def count_pool_by_franchise(self) -> dict[str, int]:
        """Return ``{franchise_key_lower: count}`` for fresh pool items.

        Used by discovery's pool-wide franchise quota check (v0.3.50+)
        so a franchise that already has many items in the pool can't
        keep accumulating across discovery rounds. Empty franchise_key
        is excluded — most generic content has no IP signal and the
        quota is only meaningful for series / IP / UP-driven groups.
        """
        cursor = self.conn.execute(
            """
            SELECT LOWER(TRIM(franchise_key)) AS fk, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND franchise_key IS NOT NULL
              AND TRIM(franchise_key) != ''
            GROUP BY LOWER(TRIM(franchise_key))
            """
        )
        return {str(row["fk"]): int(row["n"]) for row in cursor.fetchall() if row["fk"]}

    def get_distinct_topic_groups(self) -> list[str]:
        """Return distinct non-empty ``topic_group`` values in the fresh pool.

        Used by recommendation pre-warming so the embedding cache is hot
        before the popup hits ``serve()``. Cheap GROUP BY on a small
        column with no JOIN.
        """
        cursor = self.conn.execute(
            """
            SELECT DISTINCT topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(topic_group, '') != ''
            """
        )
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    def get_active_pool_topic_groups(
        self,
        *,
        limit: int = 30,
        min_count: int = 2,
    ) -> list[str]:
        """Return the top ``limit`` topic_group names currently in active pool.

        Used by ExploreStrategy to know which topics the pool already
        covers, so the LLM that generates explore domains can avoid
        re-proposing those (the v0.3.31 explore-blind-spot pattern).
        Filters to groups with at least ``min_count`` members so a
        single one-off item doesn't block exploration of an actually-
        empty area. Result is sorted by group size DESC.
        """
        cursor = self.conn.execute(
            """
            SELECT topic_group, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            GROUP BY topic_group
            HAVING COUNT(*) >= ?
            ORDER BY n DESC, topic_group ASC
            LIMIT ?
            """,
            (max(1, int(min_count)), max(1, int(limit))),
        )
        return [str(row["topic_group"]) for row in cursor.fetchall()]

    def get_topic_group_samples(
        self,
        *,
        samples_per_group: int = 5,
        top_n_groups: int = 60,
    ) -> list[tuple[str, list[str]]]:
        """For each fresh-pool ``topic_group``, return up to N sample titles.

        Returns the top ``top_n_groups`` groups by member count (tie-break
        on highest in-group ``relevance_score``). Long-tail micro-topics
        (1-2 items) almost never show up together in a single 40-candidate
        recommendation batch, so investing API budget to merge-map them
        adds latency without affecting visible diversity.

        Used by the recommendation prewarmer to build an accurate
        supergroup-merge map: short Chinese labels (``赛博朋克``,
        ``动漫`` …) are catastrophically ambiguous in embedding space
        when embedded standalone — they need title-context disambiguation.
        Sample titles are picked top-by-``relevance_score`` within each
        group, so the input is reasonably stable while the pool is steady.
        """
        cursor = self.conn.execute(
            """
            SELECT topic_group, title, relevance_score
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(topic_group, '') != ''
              AND COALESCE(title, '') != ''
            ORDER BY topic_group, relevance_score DESC, bvid
            """
        )
        by_group: dict[str, list[str]] = defaultdict(list)
        group_max_score: dict[str, float] = {}
        group_count: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            group = str(row["topic_group"]).strip()
            title = str(row["title"]).strip()
            if not group or not title:
                continue
            group_count[group] += 1
            score = float(row["relevance_score"] or 0.0)
            if score > group_max_score.get(group, -1.0):
                group_max_score[group] = score
            if len(by_group[group]) < samples_per_group:
                by_group[group].append(title)

        # Rank groups by member count desc, score desc, label asc (stable).
        ranked = sorted(
            by_group.keys(),
            key=lambda g: (-group_count[g], -group_max_score.get(g, 0.0), g),
        )
        return [(group, by_group[group]) for group in ranked[:top_n_groups]]

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        """Suppress excess fresh explore items from high-risk topic clusters."""
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(source, '') = 'explore'
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            cluster = self._explore_risk_cluster(row)
            if not cluster:
                continue
            grouped[cluster].append(row)

        overflow_bvids: list[str] = []
        for items in grouped.values():
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            overflow_bvids.extend(
                str(row.get("bvid", "")).strip() for row in ranked[max(0, max_per_cluster) :]
            )

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        return len(clean_bvids)

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        """Suppress fresh items where any single ``topic_group`` exceeds *max_per_group*.

        Generalises the source-and-keyword-specific
        :meth:`trim_explore_cluster_overflow` to a cross-source, dynamic cap on
        every populated ``topic_group`` value. Without this, a single topic
        (e.g. ``人工智能``) can accumulate hundreds of fresh candidates as
        related_chain/search/explore each keep returning the same coarse group
        across rounds — m118's per-call ``_compress_topic_repeats`` doesn't
        compose across rounds, and the explore-only cluster cap doesn't see
        related_chain or search.

        Items with empty ``topic_group`` are ignored. Within an over-cap
        group, the highest-scored / most-recently-scored items are kept;
        the rest get ``pool_status='suppressed'``.

        v0.3.31+: emits an INFO log when something gets dropped, naming
        the over-flowing groups + how many items each lost. Without this,
        the function ran silently — operators couldn't tell whether the
        diversity machinery was actually cutting anything or sleeping.
        """
        if max_per_group <= 0:
            return 0

        cursor = self.conn.execute(
            """
            SELECT bvid, topic_group, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            group = str(row.get("topic_group", "") or "").strip().lower()
            if not group:
                continue
            grouped[group].append(row)

        overflow_bvids: list[str] = []
        # v0.3.31+: track per-group drop counts for the INFO log
        drops_per_group: dict[str, int] = {}
        for group_name, items in grouped.items():
            if len(items) <= max_per_group:
                continue
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            losers = ranked[max_per_group:]
            drops_per_group[group_name] = len(losers)
            overflow_bvids.extend(str(row.get("bvid", "")).strip() for row in losers)

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

        # Top 10 most-trimmed groups so the log line stays readable.
        # Demoted to DEBUG: this runs once per minute from the refresh
        # tick. When the pool is steady-state and a single group
        # consistently sits ~8 items over the cap, the same line gets
        # logged 1440x per day at INFO. Caller can lift to INFO when
        # the trim shape actually changes (see refresh.enforce_pool_cap).
        top = sorted(drops_per_group.items(), key=lambda kv: -kv[1])[:10]
        logger.debug(
            "[diversity] trim_topic_group_overflow: cap=%d, dropped=%d items "
            "across %d over-cap groups, top: %s",
            max_per_group,
            len(clean_bvids),
            len(drops_per_group),
            ", ".join(f"{g}:{c}" for g, c in top),
        )
        return len(clean_bvids)

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Suppress overflow fresh items so the pool does not exceed *target*.

        Ranking (what we keep): higher ``relevance_score`` > newer
        ``last_scored_at`` > non-``explore`` source > stable ``bvid``. Items
        already surfaced as recommendations are excluded from the count — the
        recommendation side treats the pool as a queue, so consumed rows are
        never trimmed here.

        When ``source_share_quotas`` is provided, the trim respects per-source-family
        share targets: items from source families already at or above their quota
        get suppressed *before* lower-scored items from under-quota sources.
        Without this, score-only trim systematically axes low-relevance
        sources (trending, explore) when high-relevance sources (search,
        related_chain) overflow — defeating the per-source diversity goal.
        Xiaohongshu extension channels (task/search/explore/profile) are
        collapsed under the single ``xiaohongshu`` family.
        """
        if target <= 0:
            return 0

        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows = [
            dict(row)
            for row in cursor.fetchall()
            if not self._is_viewed_row(dict(row), viewed_content_keys)
            and _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            )
        ]
        if len(rows) <= target:
            return 0

        ranked = sorted(
            rows,
            key=lambda row: (
                -float(row.get("relevance_score", 0.0) or 0.0),
                -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                1 if str(row.get("source", "") or "") == "explore" else 0,
                str(row.get("bvid", "")),
            ),
        )

        if source_share_quotas:
            # Three-tier protection so under-quota sources stay fully intact:
            #   protected: items from sources whose total ≤ quota, OR top-N
            #              items from sources whose total > quota (where N=quota)
            #   negotiable_tracked: bottom (total-quota) items from over-quota
            #              tracked sources
            #   negotiable_untracked: items from sources without a declared
            #              share — eligible to be cut before touching protected.
            # Order for the final keep walk: protected → negotiable_untracked
            # → negotiable_tracked.  This ensures trending (under quota) stays
            # 100% protected even when sum of in_quota > target due to
            # untracked sources eating slots.
            counts_per_source: dict[str, int] = defaultdict(int)
            for row in rows:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                counts_per_source[source_family] += 1

            protected: list[dict[str, Any]] = []
            negotiable_tracked: list[dict[str, Any]] = []
            negotiable_untracked: list[dict[str, Any]] = []
            seen: dict[str, int] = defaultdict(int)
            for row in ranked:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                quota = source_share_quotas.get(source_family)
                if quota is None:
                    negotiable_untracked.append(row)
                    continue
                if counts_per_source[source_family] <= quota:
                    # entire source under quota — every item protected
                    protected.append(row)
                else:
                    # over quota: top `quota` items protected, rest negotiable
                    if seen[source_family] < quota:
                        protected.append(row)
                        seen[source_family] += 1
                    else:
                        negotiable_tracked.append(row)
            ranked = protected + negotiable_untracked + negotiable_tracked

        overflow_rows = ranked[target:]
        overflow_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        # v0.3.31+: log per-source breakdown so operators see whether the
        # quota guard is biting (e.g. "explore overflowing 80%" → fix the
        # discovery cycle, not the recommender).
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_to_target_count: target=%d, before=%d, "
            "suppressed=%d, by-source: %s",
            target,
            len(rows),
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        """Suppress fresh rows that exceed platform-family pool quotas.

        ``trim_pool_to_target_count`` caps the total pool size. This pass caps
        each tracked platform family independently, so an over-filled family
        cannot occupy capacity reserved for another source while the total pool
        is still below target.
        """
        clean_quotas: dict[str, int] = {}
        for source_family, quota in source_share_quotas.items():
            try:
                clean_quotas[str(source_family)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        if not clean_quotas:
            return 0

        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            if source_family in clean_quotas:
                grouped[source_family].append(dict(row))

        overflow_rows: list[dict[str, Any]] = []
        for source_family, rows in grouped.items():
            quota = clean_quotas[source_family]
            if len(rows) <= quota:
                continue
            ranked = sorted(
                rows,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    1 if str(row.get("source", "") or "") == "explore" else 0,
                    str(row.get("bvid", "")),
                ),
            )
            overflow_rows.extend(ranked[quota:])

        clean_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in clean_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_source_overflow: suppressed=%d, by-source: %s",
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
    ) -> int:
        """Move suppressed candidates back to fresh for under-quota source families.

        This is a source-balance repair pass for pools that are already full but
        uneven. It only reactivates rows that are otherwise eligible for the
        recommendation pool; the caller should run ``trim_pool_to_target_count``
        afterwards if the fresh pool rises above ``target``.
        """
        if target <= 0 or not source_share_quotas:
            return 0

        current_counts = self.count_pool_candidates_by_source()
        deficits = {
            source_family: min(target, max(0, int(quota)))
            - int(current_counts.get(source_family, 0))
            for source_family, quota in source_share_quotas.items()
            if int(quota) > 0
        }
        deficits = {source: deficit for source, deficit in deficits.items() if deficit > 0}
        if not deficits:
            return 0

        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                bvid ASC
            """
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        selected_bvids: list[str] = []
        selected_counts: dict[str, int] = defaultdict(int)
        target_selection_count = sum(deficits.values())

        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            deficit = deficits.get(source_family, 0)
            if deficit <= 0 or selected_counts[source_family] >= deficit:
                continue
            selected_bvids.append(bvid)
            selected_counts[source_family] += 1
            if len(selected_bvids) >= target_selection_count:
                break

        if not selected_bvids:
            return 0

        placeholders = ", ".join("?" for _ in selected_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE bvid IN ({placeholders})
            """,
            selected_bvids,
        )
        return len(selected_bvids)

    @staticmethod
    def _balance_pool_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        """Round-robin sample from a relevance-ordered pool, balanced by content topic.

        Buckets by ``topic_group`` (with fallback to ``topic_key`` then a
        sentinel) so that one dominant topic in the relevance head can't
        crowd out the candidate window. Source/platform are intentionally
        ignored — content-side features drive richness, not provenance.

        The round-robin always runs (even when ``len(rows) <= limit``) so
        that the returned ordering is balanced for downstream callers
        that may sub-select; otherwise the SQL ordering can place several
        items of the same topic back-to-back at the top.
        """
        if limit <= 0 or len(rows) <= 1:
            return rows[:limit]

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topic_order: list[str] = []
        for row in rows:
            key = str(row.get("topic_group", "") or "").strip().lower()
            if not key:
                key = str(row.get("topic_key", "") or "").strip().lower()
            if not key:
                key = "unknown"
            if key not in buckets:
                topic_order.append(key)
            buckets[key].append(row)

        balanced: list[dict[str, Any]] = []
        while len(balanced) < limit:
            progressed = False
            for key in topic_order:
                bucket = buckets[key]
                if not bucket:
                    continue
                balanced.append(bucket.pop(0))
                progressed = True
                if len(balanced) >= limit:
                    break
            if not progressed:
                break
        return balanced[:limit]

    def get_recent_viewed_bvids(self, limit: int = 2000) -> set[str]:
        """Return recently viewed BVIDs from view events."""
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_bvids: set[str] = set()
        for row in cursor.fetchall():
            bvid = self._extract_bvid_from_view_event(dict(row))
            if bvid:
                viewed_bvids.add(bvid)
        return viewed_bvids

    def get_recent_viewed_content_keys(self, limit: int = 2000) -> set[str]:
        """Return recently viewed content identities across supported sources.

        Keys are source-aware (``source_platform:content_id``) and include
        raw BVIDs for legacy Bilibili callers.
        """
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_keys: set[str] = set()
        for row in cursor.fetchall():
            viewed_keys.update(self._extract_content_keys_from_view_event(dict(row)))
        return viewed_keys

    @staticmethod
    def _explore_risk_cluster(row: dict[str, Any]) -> str:
        haystack = " ".join(
            [
                str(row.get("topic_key", "") or ""),
                str(row.get("title", "") or ""),
            ]
        ).lower()
        if not haystack.strip():
            return ""
        compact = re.sub(r"\s+", "", haystack)
        for cluster, keywords in _EXPLORE_HIGH_RISK_CLUSTERS:
            if any(keyword in compact for keyword in keywords):
                return cluster
        return ""

    @staticmethod
    def _sort_timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        normalized = value.replace(" ", "T")
        try:
            from datetime import datetime

            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    def mark_pool_items_shown(self, bvids: list[str]) -> None:
        """Mark discovery-pool items as already shown in recommendations."""
        clean_bvids = [item for item in bvids if item]
        if not clean_bvids:
            return
        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'shown',
                recommended_at = CURRENT_TIMESTAMP
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int:
        """Mark pool items older than *max_age_days* as stale."""
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'stale'
            WHERE pool_status = 'fresh'
              AND discovered_at < datetime('now', '-' || ? || ' days')
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (max_age_days,),
        )
        return cursor.rowcount

    def purge_pool_by_disliked_topics(self, topics: list[str]) -> int:
        """Mark fresh pool candidates matching new dislikes as purged.

        Matching strategy (all case-sensitive at the SQLite layer — Chinese
        text makes case folding moot and ASCII matching still works):
          1. Exact match on ``topic_key``, ``topic_group``, or ``pool_topic_label``
          2. Substring match on ``title`` or ``pool_topic_label``
             (catches "鬼畜合集" when the dislike is "鬼畜")

        Only candidates in ``pool_status = 'fresh'`` are affected — historical
        rows (``shown``, ``feedbacked``, ``stale``) are preserved for audit.
        Already-recommended items are skipped so the recommendation history
        remains intact.

        Args:
            topics: Newly added disliked topics (stripped, non-empty strings).

        Returns:
            Number of rows transitioned to ``pool_status = 'purged_by_dislike'``.
        """
        clean = [t.strip() for t in topics if t and t.strip()]
        if not clean:
            return 0

        # Build the match clause dynamically. Use parameterized queries
        # throughout — topic values may contain SQL metacharacters that must
        # not be interpolated into the query string.
        exact_placeholders = ", ".join("?" for _ in clean)
        like_conditions = " OR ".join("title LIKE ? OR pool_topic_label LIKE ?" for _ in clean)

        params: list[Any] = []
        params.extend(clean)  # topic_key IN (...)
        params.extend(clean)  # topic_group IN (...)
        params.extend(clean)  # pool_topic_label IN (...)
        for topic in clean:
            like = f"%{topic}%"
            params.append(like)  # title LIKE ?
            params.append(like)  # pool_topic_label LIKE ?

        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
              AND (
                topic_key IN ({exact_placeholders})
                OR topic_group IN ({exact_placeholders})
                OR pool_topic_label IN ({exact_placeholders})
                OR {like_conditions}
              )
            """,
            params,
        )
        return cursor.rowcount

    def get_fresh_pool_candidates_for_purge_scan(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return fresh, not-yet-recommended pool candidates for a semantic scan.

        Returns only the fields needed for embedding-based matching:
        bvid, title, topic_key, topic_group, pool_topic_label.
        """
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, topic_group, pool_topic_label
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_pool_items_purged_by_dislike(self, bvids: list[str]) -> int:
        """Mark specified bvids as purged_by_dislike (only if currently fresh)."""
        clean = [b.strip() for b in bvids if b and b.strip()]
        if not clean:
            return 0
        placeholders = ", ".join("?" for _ in clean)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            clean,
        )
        return cursor.rowcount

    def get_pool_candidates_needing_evaluation(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return fresh pool candidates that lack LLM content classification.

        Targets items with empty ``style_key`` AND empty ``topic_group`` —
        typically content from non-bilibili sources (e.g. xiaohongshu) that
        was inserted directly into ``content_cache`` without passing through
        the discovery engine's ``evaluate_content`` pipeline.

        These items need LLM evaluation to receive ``style_key``,
        ``topic_group``, and ``relevance_score`` so the diversity mechanism
        in ``_select_diversified_batch`` can treat them equally alongside
        bilibili content.
        """
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(style_key, '') = ''
              AND COALESCE(topic_group, '') = ''
              AND COALESCE(relevance_score, 0) = 0
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                last_scored_at DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def get_pool_candidates_needing_copy(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return fresh pool candidates missing precomputed popup copy."""
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND (
                COALESCE(pool_expression, '') = ''
                OR COALESCE(pool_topic_label, '') = ''
              )
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def update_pool_copy(
        self,
        bvid: str,
        *,
        expression: str,
        topic_label: str,
    ) -> None:
        """Persist precomputed popup copy for one pooled candidate."""
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_expression = ?,
                pool_topic_label = ?
            WHERE bvid = ?
            """,
            (expression, topic_label, bvid),
        )

    def get_latest_event_id(self) -> int:
        """Return the latest event primary key."""
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events")
        row = cursor.fetchone()
        return int(row["latest_id"]) if row is not None else 0

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]:
        """Query events newer than a given id for selected event types."""
        if not event_types:
            return []
        placeholders = ", ".join("?" for _ in event_types)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE id > ? AND event_type IN ({placeholders})
            ORDER BY id ASC
            """,
            [after_event_id, *event_types],
        )
        return [dict(row) for row in cursor.fetchall()]

    def insert_recommendation(
        self,
        bvid: str,
        *,
        confidence: float,
        expression: str = "",
        topic: str = "",
        presented: int = 0,
    ) -> int:
        """Insert a recommendation history record."""
        cursor = self._execute_write(
            """
            INSERT INTO recommendations (bvid, expression, topic, confidence, presented)
            VALUES (?, ?, ?, ?, ?)
            """,
            (bvid, expression, topic, confidence, presented),
        )
        return cursor.lastrowid or 0

    def batch_insert_recommendations(
        self,
        items: list[dict[str, Any]],
    ) -> list[int]:
        """Insert N recommendation rows in one transaction; return row IDs in order.

        Single fsync replaces N (was 200-300ms each under discovery write
        contention → ~3s for the popup's 10-item batch). Returns
        ``lastrowid`` per item, computed from the auto-increment delta
        since this connection's last id.
        """
        return self.batch_insert_recommendations_and_mark_shown(items, [])

    def batch_insert_recommendations_and_mark_shown(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> list[int]:
        """Insert recommendations + mark pool items shown in **one transaction**.

        v0.3.45+: serve() used to fire two separate writes (insert recs,
        then UPDATE content_cache.pool_status='shown') and pay two
        fsyncs. Under refresh-tick write contention this stretched the
        tail to ~1s. One BEGIN IMMEDIATE / COMMIT pair gives the same
        atomic semantics with a single fsync, and the rare lost-write
        case (insert succeeds, mark fails) is now structurally
        impossible — both succeed or both rollback together.

        Returns ``lastrowid`` per item, in the same order as ``items``.
        """
        if not items and not shown_bvids:
            return []
        clean_bvids = [b for b in shown_bvids if b]
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    ids: list[int] = []
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO recommendations
                                (bvid, expression, topic, confidence, presented)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                str(item.get("bvid", "")),
                                str(item.get("expression", "")),
                                str(item.get("topic", "")),
                                float(item.get("confidence", 0.0) or 0.0),
                                int(item.get("presented", 0) or 0),
                            ),
                        )
                        ids.append(cursor.lastrowid or 0)
                    if clean_bvids:
                        placeholders = ", ".join("?" for _ in clean_bvids)
                        cursor.execute(
                            f"""
                            UPDATE content_cache
                            SET pool_status = 'shown',
                                recommended_at = CURRENT_TIMESTAMP
                            WHERE bvid IN ({placeholders})
                            """,
                            clean_bvids,
                        )
                    self.conn.commit()
                    return ids
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def get_recent_recommendation_signals(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent recommendations with topic/source for scoring context.

        Includes both ``topic_key`` (fine, e.g. ``"洛克王国"``) and
        ``topic_group`` (coarse, e.g. ``"游戏"``) so the curator can fatigue
        on both axes. Without ``topic_group``, sibling fine-grained keys
        like ``动漫杂谈`` / ``动漫补番`` / ``动漫解说`` are independent and
        per-key fatigue never fires across them.
        """
        cursor = self.conn.execute(
            """
            SELECT r.bvid, c.topic_key, c.topic_group, c.source, r.created_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = r.bvid
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_feedback_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feedback with UP/topic/franchise info for score
        adjustment.

        ``franchise_key`` is the LLM-tagged IP / series column (added in
        v0.3.18). Disliking one 原神 video used to only block its exact
        bvid; now the curator collects ``franchise_key`` across recent
        dislikes and down-ranks any candidate whose own ``franchise_key``
        matches — without relying on title-string heuristics.
        """
        cursor = self.conn.execute(
            """
            SELECT r.feedback_type, c.up_mid, c.up_name, c.topic_key,
                   c.source, c.title, c.franchise_key
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = r.bvid
            WHERE r.feedback_type IS NOT NULL
            ORDER BY r.feedback_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recommendations(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recommendation history ordered by newest first.

        xhs rows whose cached ``content_url`` is missing ``xsec_token``
        are filtered out — clicking them hits xhs's 300031 login wall.

        ``franchise_key`` (v0.3.18) is exposed so /api/recommendations
        can apply a final per-IP cap before returning to the client —
        otherwise five 原神 / 提瓦特 items can land in one popup view.
        """
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT
                r.*,
                c.title AS title,
                c.up_name AS up_name,
                c.cover_url AS cover_url,
                c.content_id AS content_id,
                c.content_url AS content_url,
                c.source_platform AS source_platform,
                c.franchise_key AS franchise_key
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = r.bvid
            WHERE (
                COALESCE(c.source_platform, '') != 'xiaohongshu'
                OR COALESCE(c.content_url, '') LIKE '%xsec_token=%'
            )
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_recommendations(self) -> int:
        """Return the total number of stored recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute("SELECT COUNT(*) AS count FROM recommendations")
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def count_unread_recommendations(self) -> int:
        """Return the number of unpresented recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            "SELECT COUNT(*) AS count FROM recommendations WHERE presented = 0"
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None:
        """Return one recommendation worth notifying the user about."""
        cursor = self.conn.execute(
            """
            SELECT
                r.id,
                r.bvid,
                r.expression,
                r.confidence,
                c.title,
                c.notification_sent,
                c.notified_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = r.bvid
            WHERE r.presented = 0
              AND c.notification_sent = 0
              AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (min_confidence,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def mark_notification_sent(self, bvid: str) -> None:
        """Mark one cached item as already notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET notification_sent = 1,
                notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_recommendation_content(
        self,
        recommendation_id: int,
        *,
        expression: str,
        topic: str,
    ) -> None:
        """Update the generated expression fields of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET expression = ?, topic = ?
            WHERE id = ?
            """,
            (expression, topic, recommendation_id),
        )

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, Any] | None:
        """Return a single recommendation row by primary key."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT r.*, c.title AS title, c.up_name AS up_name
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = r.bvid
            WHERE r.id = ?
            """,
            (recommendation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def update_recommendation_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        feedback_note: str = "",
    ) -> None:
        """Update the current feedback state of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET feedback = ?,
                feedback_type = ?,
                feedback_note = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (feedback_type, feedback_type, feedback_note, recommendation_id),
        )
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'feedbacked',
                feedback_type = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE bvid = (
                SELECT bvid
                FROM recommendations
                WHERE id = ?
            )
            """,
            (feedback_type, recommendation_id),
        )

    def mark_recommendations_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendations as presented and set their presented timestamp."""
        if not recommendation_ids:
            return
        placeholders = ", ".join("?" for _ in recommendation_ids)
        self._execute_write(
            f"""
            UPDATE recommendations
            SET presented = 1,
                presented_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            recommendation_ids,
        )

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_llm_usage_cache_columns(self) -> None:
        """Backfill v0.3.28+ prompt-cache columns on existing llm_usage tables."""
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(llm_usage)").fetchall()
        }
        required_columns = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {column_name} {column_type}")

    def _ensure_event_satisfaction_columns(self) -> None:
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs.

        Existing rows keep ``NULL`` in both columns; consumers treat NULL
        as ``unknown`` so the upgrade is non-blocking.
        """
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        required_columns = {
            "inferred_satisfaction": "TEXT",
            "satisfaction_reason": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE events ADD COLUMN {column_name} {column_type}"
            )

    def _ensure_recommendation_feedback_columns(self) -> None:
        """Backfill recommendation feedback columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        required_columns = {
            "feedback_type": "TEXT",
            "feedback_note": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE recommendations ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_runtime_columns(self) -> None:
        """Backfill content-cache runtime columns for continuous refresh."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "last_scored_at": "TIMESTAMP",
            "notification_sent": "INTEGER DEFAULT 0",
            "notified_at": "TIMESTAMP",
            "pool_status": "TEXT DEFAULT 'fresh'",
            "recommended_at": "TIMESTAMP",
            "feedback_type": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_relevance_columns(self) -> None:
        """Backfill relevance fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "relevance_score": "REAL DEFAULT 0.0",
            "relevance_reason": "TEXT DEFAULT ''",
            "candidate_tier": "TEXT DEFAULT 'primary'",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_topic_columns(self) -> None:
        """Backfill topic bucketing fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "topic_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_key TEXT DEFAULT ''")
        if "topic_group" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_group TEXT DEFAULT ''")
        if "style_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN style_key TEXT DEFAULT ''")
        if "franchise_key" not in existing_columns:
            # v0.3.18: LLM-tagged IP / franchise / series. Empty string for
            # general-interest content; non-empty rows let the curator
            # propagate dislikes within an IP and let
            # /api/recommendations cap how many same-franchise items
            # appear in a single response window — without relying on
            # any title-string heuristic or hardcoded alias list.
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN franchise_key TEXT DEFAULT ''")

    def _ensure_content_cache_pool_copy_columns(self) -> None:
        """Backfill precomputed pool-copy fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "pool_expression": "TEXT DEFAULT ''",
            "pool_topic_label": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_delight_columns(self) -> None:
        """Backfill proactive delight scoring fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "delight_score": "REAL DEFAULT 0.0",
            "delight_reason": "TEXT DEFAULT ''",
            "delight_hook": "TEXT DEFAULT ''",
            "delight_notified": "INTEGER DEFAULT 0",
            "delight_notified_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_multisource_columns(self) -> None:
        """Add multi-source content identity fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "content_id": "TEXT DEFAULT ''",
            "content_url": "TEXT DEFAULT ''",
            "source_platform": "TEXT DEFAULT 'bilibili'",
            "author_name": "TEXT DEFAULT ''",
        }
        added = False
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")
            added = True
        if added:
            self.conn.execute("UPDATE content_cache SET content_id = bvid WHERE content_id = ''")

    def _ensure_source_recipes_table(self) -> None:
        """Create the source_recipes table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_recipes (
                id            TEXT PRIMARY KEY,
                source_type   TEXT NOT NULL,
                name          TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                config        TEXT DEFAULT '{}',
                target_share  INTEGER DEFAULT 4,
                enabled       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'system',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def _ensure_xhs_observed_urls_table(self) -> None:
        """Create the xhs_observed_urls table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS xhs_observed_urls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                page_type   TEXT NOT NULL DEFAULT 'other',
                observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                enriched    INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_xhs_observed_urls_url
                ON xhs_observed_urls (url);
        """)

    def _ensure_chat_turns_table(self) -> None:
        """Create durable popup chat-turn storage for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_turns (
                turn_id       TEXT PRIMARY KEY,
                session       TEXT NOT NULL DEFAULT 'popup',
                scope         TEXT NOT NULL DEFAULT 'chat',
                subject_id    TEXT NOT NULL DEFAULT '',
                subject_title TEXT NOT NULL DEFAULT '',
                message       TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'pending',
                reply         TEXT NOT NULL DEFAULT '',
                error         TEXT NOT NULL DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
                ON chat_turns(session, created_at, turn_id);
            CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
                ON chat_turns(scope, subject_id, created_at);
        """)

    # ── XHS observed URL ingest ───────────────────────────────────

    def save_xhs_observed_urls(self, urls: list[str], page_type: str) -> int:
        """Insert observed xhs URLs, skipping duplicates. Returns count inserted."""
        inserted = 0
        for url in urls:
            # Skip if we've already seen this URL
            existing = self.conn.execute(
                "SELECT 1 FROM xhs_observed_urls WHERE url = ?", (url,)
            ).fetchone()
            if existing:
                continue
            self._execute_write(
                "INSERT INTO xhs_observed_urls (url, page_type) VALUES (?, ?)",
                (url, page_type),
            )
            inserted += 1
        return inserted

    # ── Source recipe CRUD ──────────────────────────────────────────

    def save_source_recipe(self, recipe: dict[str, Any]) -> None:
        """Insert or update a source recipe."""
        import json as _json

        self._execute_write(
            """
            INSERT INTO source_recipes (id, source_type, name, strategy, config,
                                        target_share, enabled, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                strategy = excluded.strategy,
                config = excluded.config,
                target_share = excluded.target_share,
                enabled = excluded.enabled
            """,
            (
                str(recipe["id"]),
                str(recipe["source_type"]),
                str(recipe["name"]),
                str(recipe["strategy"]),
                _json.dumps(recipe.get("config", {}), ensure_ascii=False),
                int(recipe.get("target_share", 4)),
                int(recipe.get("enabled", True)),
                str(recipe.get("created_by", "system")),
                recipe.get("created_at") or None,
            ),
        )

    def get_all_recipes(self) -> list[dict[str, Any]]:
        """Return all source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute("SELECT * FROM source_recipes ORDER BY created_at").fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def get_enabled_recipes(self) -> list[dict[str, Any]]:
        """Return only enabled source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT * FROM source_recipes WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def update_recipe(self, recipe_id: str, **fields: Any) -> bool:
        """Update specific fields of a recipe. Returns True if a row was updated."""
        import json as _json

        allowed = {"name", "strategy", "config", "target_share", "enabled", "last_fetched_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        if "config" in updates and not isinstance(updates["config"], str):
            updates["config"] = _json.dumps(updates["config"], ensure_ascii=False)
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [recipe_id]
        cursor = self._execute_write(
            f"UPDATE source_recipes SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        return cursor.rowcount > 0

    def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a recipe by id. Returns True if a row was deleted."""
        cursor = self._execute_write(
            "DELETE FROM source_recipes WHERE id = ?",
            (recipe_id,),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_recipe(row: Any) -> dict[str, Any]:
        import json as _json

        config_raw = row["config"] if row["config"] else "{}"
        try:
            config = _json.loads(config_raw)
        except (ValueError, TypeError):
            config = {}
        return {
            "id": str(row["id"]),
            "source_type": str(row["source_type"]),
            "name": str(row["name"]),
            "strategy": str(row["strategy"]),
            "config": config,
            "target_share": int(row["target_share"]),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"] or ""),
            "last_fetched_at": str(row["last_fetched_at"] or ""),
        }

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Return one un-notified pool item with the highest delight_score.

        Backwards-compatible: ``limit=1`` returns a single dict (or None);
        callers that want multiple candidates (for example to filter
        disliked topics in Python) should call
        ``get_delight_candidates`` instead.
        """
        rows = self.get_delight_candidates(
            min_delight_score=min_delight_score,
            limit=max(1, int(limit)),
        )
        return rows[0] if rows else None

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` un-notified delight candidates ordered by score.

        Restricts to ``pool_status IN ('fresh', 'shown')`` —  ``suppressed``
        items have been trimmed out of the active pool by topic-group cap
        or source-share quota and shouldn't reappear as delights. Without
        this guard, popup re-hydration would pull historical delight
        scores baked under earlier (looser) calibrations from the
        suppressed graveyard and surface 20 stale "surprises" on every
        extension reload (observed 2026-05-04: 562 suppressed items
        carried delight metadata vs 2 in fresh).
        """
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
            ORDER BY delight_score DESC, relevance_score DESC, discovered_at DESC
            LIMIT ?
            """,
            (min_delight_score, max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_delight_notified(self, bvid: str) -> None:
        """Mark one content item as delight-notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_notified = 1,
                delight_notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_delight_score(
        self,
        bvid: str,
        *,
        delight_score: float,
        delight_reason: str,
        delight_hook: str = "",
    ) -> None:
        """Persist the computed delight score and explanation for a pool item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_score = ?,
                delight_reason = ?,
                delight_hook = ?
            WHERE bvid = ?
            """,
            (delight_score, delight_reason, delight_hook, bvid),
        )

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        """Return the number of un-notified delight candidates."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            (min_delight_score,),
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_pool_candidates_needing_delight_score(
        self,
        limit: int = 30,
        *,
        min_delight_score_for_reason: float | None = None,
        min_relevance_score: float = 0.55,
    ) -> list[dict[str, Any]]:
        """Return pool candidates that still need delight evaluation or copy.

        Two-stage retrieval: ``relevance_score >= min_relevance_score``
        is the cheap pre-filter (the discovery LLM already judged user-
        content fit during ``evaluate_batch``), then the caller runs the
        expensive LLM delight scorer only on this shortlist.

        Default 0.55 is calibrated to the discovery rubric:
          0.6+ strong fit, 0.5-0.6 moderate, <0.5 weak fit.
        Items below ``min_relevance_score`` skip delight scoring
        entirely — they're not going to delight anyone they don't
        already half-fit, and burning LLM calls on weak-fit items just
        wastes budget.
        """
        if min_delight_score_for_reason is None:
            cursor = self.conn.execute(
                """
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(delight_score, 0.0) = 0.0
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY relevance_score DESC, discovered_at DESC
                LIMIT ?
                """,
                (min_relevance_score, limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND (
                    COALESCE(delight_score, 0.0) = 0.0
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND (
                        COALESCE(delight_reason, '') = ''
                        OR COALESCE(delight_hook, '') = ''
                      )
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY
                    CASE WHEN COALESCE(delight_score, 0.0) > 0.0 THEN 0 ELSE 1 END ASC,
                    delight_score DESC,
                    relevance_score DESC,
                    discovered_at DESC
                LIMIT ?
                """,
                (min_relevance_score, min_delight_score_for_reason, limit),
            )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _decode_event_metadata(row: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = row.get("metadata", "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                return metadata
        if isinstance(metadata_raw, dict):
            return metadata_raw
        return {}

    @classmethod
    def _extract_content_keys_from_view_event(cls, row: dict[str, Any]) -> set[str]:
        metadata = cls._decode_event_metadata(row)
        url = str(row.get("url", "")).strip()

        platform = _normalize_source_platform_key(metadata.get("source_platform", ""))
        if not platform:
            platform = cls._infer_source_platform_from_url(url)

        content_ids: set[str] = set()
        for key in _VIEW_CONTENT_ID_METADATA_KEYS:
            raw_value = metadata.get(key, "")
            if isinstance(raw_value, (str, int)):
                value = str(raw_value).strip()
                if value:
                    content_ids.add(value)

        url_content_id = cls._extract_content_id_from_url(platform, url)
        if url_content_id:
            content_ids.add(url_content_id)

        bvid = cls._extract_bvid_from_view_event(row)
        if bvid:
            content_ids.add(bvid)
            platform = platform or _BILIBILI_SOURCE_FAMILY

        keys: set[str] = set()
        for content_id in content_ids:
            if content_id.startswith("BV"):
                keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    @staticmethod
    def _infer_source_platform_from_url(url: str) -> str:
        if not url:
            return ""
        host = urlparse(url).netloc.lower()
        if "bilibili.com" in host or host == "b23.tv":
            return _BILIBILI_SOURCE_FAMILY
        if "xiaohongshu.com" in host or "xhslink.com" in host:
            return _XHS_SOURCE_FAMILY
        if "douyin.com" in host:
            return _DOUYIN_SOURCE_FAMILY
        if "youtube.com" in host or host == "youtu.be":
            return _YOUTUBE_SOURCE_FAMILY
        return ""

    @staticmethod
    def _extract_content_id_from_url(platform: str, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if platform == _XHS_SOURCE_FAMILY:
            if len(path_parts) >= 2 and path_parts[0] == "explore":
                return path_parts[1]
            if len(path_parts) >= 3 and path_parts[:2] == ["discovery", "item"]:
                return path_parts[2]
        if platform == _DOUYIN_SOURCE_FAMILY and "video" in path_parts:
            video_index = path_parts.index("video")
            if len(path_parts) > video_index + 1:
                return path_parts[video_index + 1]
        if platform == _YOUTUBE_SOURCE_FAMILY:
            query_video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if query_video_id:
                return query_video_id
            if parsed.netloc.lower() == "youtu.be" and path_parts:
                return path_parts[0]
            for prefix in ("shorts", "embed", "live"):
                if prefix in path_parts:
                    prefix_index = path_parts.index(prefix)
                    if len(path_parts) > prefix_index + 1:
                        return path_parts[prefix_index + 1]
        if platform == _BILIBILI_SOURCE_FAMILY:
            match = _BVID_PATTERN.search(url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_bvid_from_view_event(row: dict[str, Any]) -> str:
        metadata = Database._decode_event_metadata(row)
        bvid = str(metadata.get("bvid", "")).strip()
        if bvid:
            return bvid

        url = str(row.get("url", "")).strip()
        match = _BVID_PATTERN.search(url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _content_row_view_keys(row: dict[str, Any]) -> set[str]:
        platform = _normalize_source_platform_key(row.get("source_platform", ""))
        if not platform:
            platform = _pool_source_family(row.get("source", ""), row.get("source_platform", ""))
            if platform == "unknown":
                platform = ""

        keys: set[str] = set()
        raw_bvid = str(row.get("bvid", "") or "").strip()
        content_id = str(row.get("content_id", "") or "").strip() or raw_bvid
        for value in {raw_bvid, content_id}:
            if not value:
                continue
            if value.startswith("BV"):
                keys.add(value)
            if platform:
                keys.add(f"{platform}:{value}")
        return keys

    @staticmethod
    def _is_viewed_row(row: dict[str, Any], viewed_content_keys: set[str]) -> bool:
        if not viewed_content_keys:
            return False
        return bool(Database._content_row_view_keys(row) & viewed_content_keys)

    @staticmethod
    def _exclude_viewed_rows(
        rows: list[dict[str, Any]],
        viewed_content_keys: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not viewed_content_keys:
            return rows[:limit]
        filtered = [
            row for row in rows if not Database._is_viewed_row(row, viewed_content_keys)
        ]
        return filtered[:limit]
