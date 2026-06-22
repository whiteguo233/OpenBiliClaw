"""xhs task queue and creator subscription storage.

The task queue bridges the backend's Soul-driven scheduler to the
extension's background dispatcher. The backend enqueues search/creator
tasks; the extension polls for pending tasks, opens a tab, collects
URLs, and posts the result back.

Creator subscriptions track xhs creators the user wants to follow —
a nightly scheduler enqueues one creator task per subscription.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

XHS_BOOTSTRAP_SCOPE_EVENT_TYPES = {
    "saved": "favorite",
    "liked": "like",
    "xhs_history": "view",
}

XHS_BOOTSTRAP_SIGNAL_STRENGTH = {
    "saved": 1.0,
    "liked": 0.85,
    "xhs_history": 0.35,
}

XHS_BOOTSTRAP_SCOPE_LABELS = {
    "saved": "收藏",
    "liked": "点赞",
    "xhs_history": "浏览记录",
}

_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")


def _note_key(note: dict[str, Any]) -> str:
    scope = str(note.get("scope", "")).strip()
    note_id = str(note.get("note_id", "")).strip()
    url = str(note.get("url", "")).strip()
    title = str(note.get("title", "")).strip()
    key = note_id or url or title
    return f"{scope}:{key}" if key else ""


def xhs_bootstrap_note_key(note: dict[str, Any]) -> str:
    """Return the stable cross-task identity key for one bootstrap note."""
    return _note_key(note)


def _merge_result_payload(
    current: dict[str, Any],
    *,
    urls: list[str] | None = None,
    notes: list[dict[str, Any]] | None = None,
    scope_counts: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged_urls: list[str] = []
    seen_urls: set[str] = set()
    for url in [*(current.get("urls") or []), *(urls or [])]:
        if not isinstance(url, str) or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged_urls.append(url)

    merged_notes: list[dict[str, Any]] = []
    seen_notes: set[str] = set()
    for note in current.get("notes") or []:
        if not isinstance(note, dict):
            continue
        key = _note_key(note)
        if not key or key in seen_notes:
            continue
        seen_notes.add(key)
        merged_notes.append(note)

    added_notes: list[dict[str, Any]] = []
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        key = _note_key(note)
        if not key or key in seen_notes:
            continue
        seen_notes.add(key)
        merged_notes.append(note)
        added_notes.append(note)

    merged: dict[str, Any] = {"urls": merged_urls}
    if merged_notes:
        merged["notes"] = merged_notes

    merged_counts: dict[str, Any] = {}
    existing_counts = current.get("scope_counts")
    if isinstance(existing_counts, dict):
        merged_counts.update(existing_counts)
    if isinstance(scope_counts, dict):
        for scope, count in scope_counts.items():
            current_count = merged_counts.get(scope, 0)
            if isinstance(current_count, int) and isinstance(count, int):
                merged_counts[scope] = max(current_count, count)
            else:
                merged_counts[scope] = count
    for note in merged_notes:
        scope = str(note.get("scope", "")).strip()
        if scope and scope not in merged_counts:
            merged_counts[scope] = sum(
                1 for item in merged_notes if str(item.get("scope", "")).strip() == scope
            )
    if merged_counts:
        merged["scope_counts"] = merged_counts

    if isinstance(current.get("debug"), dict) or isinstance(debug, dict):
        merged_debug: dict[str, Any] = {}
        if isinstance(current.get("debug"), dict):
            merged_debug.update(current["debug"])
        if isinstance(debug, dict):
            merged_debug.update(debug)
        merged["debug"] = merged_debug

    return merged, added_notes


def xhs_bootstrap_notes_to_events(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert extension-collected Xiaohongshu bootstrap notes into events.

    v0.3.22+ routes through ``event_format.build_event`` so the resulting
    dict is shape-identical to B站 / future-source events. The scope-aware
    natural-language ``context`` (preserving "小红书收藏" / "小红书点赞" /
    "小红书浏览记录" wording) is built explicitly here because the scope
    label carries more nuance than the generic event_type alone.
    """
    from openbiliclaw.sources.event_format import SOURCE_XIAOHONGSHU, build_event

    events: list[dict[str, Any]] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        scope = str(note.get("scope", "")).strip()
        event_type = XHS_BOOTSTRAP_SCOPE_EVENT_TYPES.get(scope)
        if event_type is None:
            continue

        title = str(note.get("title", "")).strip()
        url = str(note.get("url", "")).strip()
        if not title and not url:
            continue

        author = str(note.get("author", "")).strip()
        label = XHS_BOOTSTRAP_SCOPE_LABELS[scope]
        # Custom context — scope label ("收藏" / "点赞" / "浏览记录") is
        # more informative than the generic event_format default
        # ("收藏了" / "点赞了" / "看了"), and the prior wording was
        # already what tests / prompts grew up reading.
        context = f"小红书{label}：{title or url}"
        if author:
            context = f"{context} 作者：{author}"

        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_XIAOHONGSHU,
                title=title,
                url=url,
                author=author,
                context=context,
                metadata={
                    "note_id": str(note.get("note_id", "")).strip(),
                    "xsec_token": str(note.get("xsec_token", "")).strip(),
                    "cover_url": str(note.get("cover_url", "")).strip(),
                    "import_source": f"xhs_bootstrap_{scope}",
                    "signal_strength": XHS_BOOTSTRAP_SIGNAL_STRENGTH[scope],
                },
            )
        )
    return events


class XhsTaskQueue:
    """Manages the xhs_tasks table."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS xhs_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at   TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_xhs_tasks_status
                ON xhs_tasks (status, created_at);
        """)
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(xhs_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE xhs_tasks ADD COLUMN claimed_at TIMESTAMP")
            self._db.conn.commit()

    def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> bool:
        """Enqueue a task if the daily budget for this type allows it.

        Returns True if enqueued, False if budget exhausted.
        """
        return (
            self.enqueue_with_id(
                task_type,
                payload,
                daily_budget=daily_budget,
            )
            is not None
        )

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        """Enqueue a task and return its id, or None when budget is exhausted.

        ``daily_budget <= 0`` disables the per-day cap; runtime producers are
        then controlled by source deficits and their per-run throttles.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if daily_budget > 0:
            count_today = self._db.conn.execute(
                "SELECT COUNT(*) FROM xhs_tasks WHERE type = ? AND created_at >= ?",
                (task_type, today),
            ).fetchone()[0]
        else:
            count_today = 0

        if daily_budget > 0 and count_today >= daily_budget:
            logger.info(
                "xhs task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None

        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO xhs_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def next_pending(self, only_ids: set[str] | None = None) -> dict[str, Any] | None:
        """Claim and return the oldest runnable task, or None.

        The extension can be installed in multiple browser profiles, and
        MV3 service workers can restart mid-task. Marking the task
        ``in_progress`` as it is handed out prevents a foreground
        bootstrap task from being opened repeatedly while one extension
        instance is already working on it. Stale in-progress tasks are
        eligible again after 15 minutes so a crashed extension does not
        permanently wedge the queue.
        """
        stale_before = (datetime.now(UTC) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        # ``only_ids`` restricts which tasks may be claimed (gui-init: during an
        # active init the dispatcher is only handed init-owned bootstrap tasks,
        # so a stale pending task can't starve the run). None = no restriction.
        where = "(status = 'pending' OR (status = 'in_progress' AND claimed_at <= ?))"
        params: list[Any] = [stale_before]
        if only_ids is not None:
            ids = [str(i) for i in only_ids]
            if not ids:
                return None
            where += f" AND id IN ({','.join('?' * len(ids))})"
            params.extend(ids)
        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT * FROM xhs_tasks WHERE {where} ORDER BY created_at ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE xhs_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM xhs_tasks WHERE id = ?", (task_id,)).fetchone()
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return dict(claimed) if claimed is not None else None

    def find_recent_task(
        self,
        task_type: str,
        *,
        recent_hours: float,
        statuses: tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Return a recent task of this type for idempotent enqueue paths."""
        if recent_hours <= 0:
            return None
        selected_statuses = statuses or _RECENT_TASK_STATUSES
        if not selected_statuses:
            return None
        placeholders = ",".join("?" for _ in selected_statuses)
        cutoff = (datetime.now(UTC) - timedelta(hours=recent_hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = self._db.conn.execute(
            f"""
            SELECT *
            FROM xhs_tasks
            WHERE type = ?
              AND created_at >= ?
              AND status IN ({placeholders})
            ORDER BY
              CASE
                WHEN status IN ('pending', 'in_progress') THEN 0
                WHEN status = 'completed' THEN 1
                ELSE 2
              END,
              created_at DESC
            LIMIT 1
            """,
            (task_type, cutoff, *selected_statuses),
        ).fetchone()
        return dict(row) if row is not None else None

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return a task by id, or None."""
        row = self._db.conn.execute(
            "SELECT * FROM xhs_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def complete(
        self,
        task_id: str,
        *,
        urls: list[str] | None = None,
        notes: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> None:
        """Mark a task as completed with optional result payload details."""
        result_payload: dict[str, Any] = {"urls": urls or []}
        if notes is not None:
            result_payload["notes"] = notes
        if scope_counts is not None:
            result_payload["scope_counts"] = scope_counts
        if debug is not None:
            result_payload["debug"] = debug
        result = json.dumps(result_payload, ensure_ascii=False)
        self._db.conn.execute(
            "UPDATE xhs_tasks SET status = 'completed', result_json = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result, task_id),
        )
        self._db.conn.commit()

    def merge_result(
        self,
        task_id: str,
        *,
        urls: list[str] | None = None,
        notes: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
        complete: bool = False,
    ) -> list[dict[str, Any]]:
        """Merge a partial/final result payload and optionally mark complete.

        Returns only notes that were newly added by this merge.
        """
        row = self.get(task_id)
        current: dict[str, Any] = {}
        if row and row.get("result_json"):
            try:
                parsed = json.loads(str(row["result_json"]))
                if isinstance(parsed, dict):
                    current = parsed
            except json.JSONDecodeError:
                current = {}

        merged, added_notes = _merge_result_payload(
            current,
            urls=urls,
            notes=notes,
            scope_counts=scope_counts,
            debug=debug,
        )
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE xhs_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE xhs_tasks SET result_json = ? WHERE id = ?",
                (result, task_id),
            )
        self._db.conn.commit()
        return added_notes

    def fail(
        self,
        task_id: str,
        *,
        error: str = "",
        debug: dict[str, Any] | None = None,
    ) -> None:
        """Mark a task as failed."""
        result_payload: dict[str, Any] = {"error": error}
        if debug is not None:
            result_payload["debug"] = debug
        result = json.dumps(result_payload, ensure_ascii=False)
        self._db.conn.execute(
            "UPDATE xhs_tasks SET status = 'failed', result_json = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result, task_id),
        )
        self._db.conn.commit()


class XhsCreatorStore:
    """Manages xhs_creator_subscriptions table."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS xhs_creator_subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id      TEXT NOT NULL UNIQUE,
                creator_url     TEXT NOT NULL,
                display_name    TEXT NOT NULL DEFAULT '',
                added_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def add(
        self,
        creator_id: str,
        creator_url: str,
        display_name: str,
    ) -> None:
        """Add a subscription (ignore if duplicate creator_id)."""
        self._db.conn.execute(
            "INSERT OR IGNORE INTO xhs_creator_subscriptions "
            "(creator_id, creator_url, display_name) VALUES (?, ?, ?)",
            (creator_id, creator_url, display_name),
        )
        self._db.conn.commit()

    def list_all(self) -> list[dict[str, Any]]:
        """Return all subscriptions."""
        rows = self._db.conn.execute(
            "SELECT * FROM xhs_creator_subscriptions ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, sub_id: int) -> bool:
        """Delete a subscription by primary key. Returns True if deleted."""
        cursor = self._db.conn.execute(
            "DELETE FROM xhs_creator_subscriptions WHERE id = ?",
            (sub_id,),
        )
        self._db.conn.commit()
        return cursor.rowcount > 0

    def due_for_fetch(self, *, hours: int = 24) -> list[dict[str, Any]]:
        """Return subscriptions whose last_fetched_at is older than ``hours`` ago."""
        rows = self._db.conn.execute(
            "SELECT * FROM xhs_creator_subscriptions "
            "WHERE last_fetched_at IS NULL "
            "   OR last_fetched_at < datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_fetched(self, sub_id: int) -> None:
        """Update last_fetched_at to now."""
        self._db.conn.execute(
            "UPDATE xhs_creator_subscriptions SET last_fetched_at = CURRENT_TIMESTAMP WHERE id = ?",
            (sub_id,),
        )
        self._db.conn.commit()
