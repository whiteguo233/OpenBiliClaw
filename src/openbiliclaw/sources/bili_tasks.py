"""Bilibili extension-search task queue.

This queue is intentionally separate from ``xhs_tasks`` / ``dy_tasks`` even
though the state machine is the same. A Bilibili search task is a fallback for
when the backend WBI search API is cooling down; the browser extension executes
the real rendered search page and posts video metadata back here.
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
_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")


def bili_search_video_key(video: dict[str, Any]) -> str:
    """Return the stable identity key for one Bilibili search result."""

    for key in ("bvid", "content_id", "url", "content_url", "title"):
        value = str(video.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _merge_bili_result_payload(
    current: dict[str, Any],
    *,
    videos: list[dict[str, Any]] | None = None,
    debug: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged_videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for video in current.get("videos") or []:
        if not isinstance(video, dict):
            continue
        key = bili_search_video_key(video)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_videos.append(video)

    added: list[dict[str, Any]] = []
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        key = bili_search_video_key(video)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_videos.append(video)
        added.append(video)

    merged: dict[str, Any] = {}
    if merged_videos:
        merged["videos"] = merged_videos

    if isinstance(current.get("debug"), dict) or isinstance(debug, dict):
        merged_debug: dict[str, Any] = {}
        if isinstance(current.get("debug"), dict):
            merged_debug.update(current["debug"])
        if isinstance(debug, dict):
            merged_debug.update(debug)
        merged["debug"] = merged_debug

    return merged, added


class BiliTaskQueue:
    """Manages the ``bili_tasks`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS bili_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at   TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_bili_tasks_status
                ON bili_tasks (status, created_at);
        """)
        self._db.conn.commit()

    def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> bool:
        """Enqueue a task if today's budget for this type allows it."""

        return self.enqueue_with_id(task_type, payload, daily_budget=daily_budget) is not None

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        """Enqueue a task and return its id, or ``None`` when budget is exhausted."""

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if daily_budget > 0:
            count_today = self._db.conn.execute(
                "SELECT COUNT(*) FROM bili_tasks WHERE type = ? AND created_at >= ?",
                (task_type, today),
            ).fetchone()[0]
        else:
            count_today = 0

        if daily_budget > 0 and count_today >= daily_budget:
            logger.info(
                "bili task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None

        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO bili_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def next_pending(self) -> dict[str, Any] | None:
        """Claim and return the oldest runnable task, or ``None``."""

        stale_before = (datetime.now(UTC) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM bili_tasks
                WHERE status = 'pending'
                   OR (status = 'in_progress' AND claimed_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (stale_before,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE bili_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM bili_tasks WHERE id = ?", (task_id,)).fetchone()
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
            FROM bili_tasks
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
        """Return a task by id, or ``None``."""

        row = self._db.conn.execute(
            "SELECT * FROM bili_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def merge_result(
        self,
        task_id: str,
        *,
        videos: list[dict[str, Any]] | None = None,
        debug: dict[str, Any] | None = None,
        complete: bool = False,
    ) -> list[dict[str, Any]]:
        """Merge a partial/final result and optionally mark the task complete."""

        row = self.get(task_id)
        current: dict[str, Any] = {}
        if row and row.get("result_json"):
            try:
                parsed = json.loads(str(row["result_json"]))
                if isinstance(parsed, dict):
                    current = parsed
            except json.JSONDecodeError:
                current = {}

        merged, added = _merge_bili_result_payload(current, videos=videos, debug=debug)
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE bili_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE bili_tasks SET result_json = ? WHERE id = ?",
                (result, task_id),
            )
        self._db.conn.commit()
        return added

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
        self._db.conn.execute(
            "UPDATE bili_tasks SET status = 'failed', result_json = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(result_payload, ensure_ascii=False), task_id),
        )
        self._db.conn.commit()


def source_keyword_id_from_bili_task(payload_json: str | None) -> int | None:
    """Read ``source_keyword_id`` off a Bili task payload, or ``None``."""

    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("source_keyword_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
