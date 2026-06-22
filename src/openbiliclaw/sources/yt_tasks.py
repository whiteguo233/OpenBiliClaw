"""YouTube bootstrap event-conversion helpers.

Python-side entry point for YouTube signals captured by the browser
extension (``/feed/history``, ``/feed/channels``, ``/playlist?list=LL``).
Deliberately independent of xhs_tasks.py and dy_tasks.py — no imports
cross between them. The only intentional shared layer is event_format.py.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")

# Map each YouTube bootstrap scope to the canonical event_type.
YT_BOOTSTRAP_SCOPE_EVENT_TYPES: dict[str, str] = {
    "yt_history": "view",
    "yt_subscriptions": "follow",
    "yt_likes": "like",
}

# Per-scope signal strength. Subscriptions are the highest-intent signal
# (deliberate channel follow); likes are explicit approval; history is
# passive exposure.
YT_BOOTSTRAP_SIGNAL_STRENGTH: dict[str, float] = {
    "yt_history": 0.35,
    "yt_subscriptions": 1.0,
    "yt_likes": 0.85,
}

YT_BOOTSTRAP_SCOPE_LABELS: dict[str, str] = {
    "yt_history": "观看历史",
    "yt_subscriptions": "订阅",
    "yt_likes": "点赞",
}


def yt_bootstrap_items_to_events(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert extension-collected YouTube bootstrap items into unified events."""
    from openbiliclaw.sources.event_format import SOURCE_YOUTUBE, build_event

    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope", "")).strip()
        event_type = YT_BOOTSTRAP_SCOPE_EVENT_TYPES.get(scope)
        if event_type is None:
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue

        channel = str(item.get("channel", "")).strip()
        label = YT_BOOTSTRAP_SCOPE_LABELS[scope]
        context = f"YouTube{label}：{title or url}"
        if channel:
            context = f"{context} 频道：{channel}"

        video_id = str(item.get("video_id", "")).strip()
        channel_id = str(item.get("channel_id", "")).strip()
        scope_short = scope.removeprefix("yt_") if scope.startswith("yt_") else scope

        metadata: dict[str, Any] = {
            "cover_url": str(item.get("cover_url", "")).strip(),
            "import_source": f"yt_bootstrap_{scope_short}",
            "signal_strength": YT_BOOTSTRAP_SIGNAL_STRENGTH[scope],
        }
        if video_id:
            metadata["video_id"] = video_id
        if channel_id:
            metadata["channel_id"] = channel_id

        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_YOUTUBE,
                title=title,
                url=url,
                author=channel,
                context=context,
                metadata=metadata,
            )
        )
    return events


def _item_key(item: dict[str, Any]) -> str:
    scope = str(item.get("scope", "")).strip()
    video_id = str(item.get("video_id", "")).strip()
    channel_id = str(item.get("channel_id", "")).strip()
    url = str(item.get("url", "")).strip()
    title = str(item.get("title", "")).strip()
    key = video_id or channel_id or url or title
    return f"{scope}:{key}" if key else ""


def yt_bootstrap_item_key(item: dict[str, Any]) -> str:
    """Return the stable cross-task identity key for one bootstrap item."""
    return _item_key(item)


def _merge_yt_result_payload(
    current: dict[str, Any],
    *,
    items: list[dict[str, Any]] | None = None,
    scope_counts: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    merged_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in current.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_items.append(item)

    added: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = _item_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_items.append(item)
        added.append(item)

    merged: dict[str, Any] = {}
    if merged_items:
        merged["items"] = merged_items

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
    for item in merged_items:
        scope = str(item.get("scope", "")).strip()
        if scope and scope not in merged_counts:
            merged_counts[scope] = sum(
                1 for v in merged_items if str(v.get("scope", "")).strip() == scope
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

    return merged, added


class YtTaskQueue:
    """Manages the yt_tasks SQLite table.

    Independent of XhsTaskQueue and DyTaskQueue. Schema mirrors dy_tasks
    because the state machine is the same (pending → completed/failed).
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS yt_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_yt_tasks_status
                ON yt_tasks (status, created_at);
        """)
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(yt_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE yt_tasks ADD COLUMN claimed_at TIMESTAMP")
            self._db.conn.commit()

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        count_today = self._budgeted_count_today(task_type) if daily_budget > 0 else 0
        if daily_budget > 0 and count_today >= daily_budget:
            logger.info(
                "yt task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None
        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO yt_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def _budgeted_count_today(self, task_type: str) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = self._db.conn.execute(
            """
            SELECT status, result_json
            FROM yt_tasks
            WHERE type = ? AND created_at >= ?
            """,
            (task_type, today),
        ).fetchall()
        count = 0
        for row in rows:
            status = str(row["status"] if hasattr(row, "keys") else row[0])
            result_json = row["result_json"] if hasattr(row, "keys") else row[1]
            if status == "failed" and _is_stale_pending_result(result_json):
                continue
            count += 1
        return count

    def next_pending(self, only_ids: set[str] | None = None) -> dict[str, Any] | None:
        stale_before = (datetime.now(UTC) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        # ``only_ids`` restricts which tasks may be claimed (gui-init: during an
        # active init only init-owned bootstrap tasks are handed out). None = all.
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
                f"SELECT * FROM yt_tasks WHERE {where} ORDER BY created_at ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE yt_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM yt_tasks WHERE id = ?", (task_id,)).fetchone()
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
            FROM yt_tasks
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

    def expire_stale_pending(
        self,
        task_types: Iterable[str],
        *,
        older_than_seconds: float,
        error: str = "stale_pending",
    ) -> int:
        normalized_types = tuple(str(t).strip() for t in task_types if str(t).strip())
        if not normalized_types:
            return 0
        cutoff_ts = datetime.now(UTC).timestamp() - max(0.0, float(older_than_seconds))
        cutoff_text = datetime.fromtimestamp(cutoff_ts, UTC).strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in normalized_types)
        result_payload = json.dumps({"error": error}, ensure_ascii=False)
        cursor = self._db.conn.execute(
            f"""
            UPDATE yt_tasks
            SET status = 'failed',
                result_json = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE status = 'pending'
              AND type IN ({placeholders})
              AND created_at < ?
            """,
            (result_payload, *normalized_types, cutoff_text),
        )
        self._db.conn.commit()
        return int(cursor.rowcount or 0)

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.conn.execute(
            "SELECT * FROM yt_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def merge_result(
        self,
        task_id: str,
        *,
        items: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
        complete: bool = False,
    ) -> list[dict[str, Any]]:
        row = self.get(task_id)
        current: dict[str, Any] = {}
        if row and row.get("result_json"):
            try:
                parsed = json.loads(str(row["result_json"]))
                if isinstance(parsed, dict):
                    current = parsed
            except json.JSONDecodeError:
                current = {}

        merged, added = _merge_yt_result_payload(
            current,
            items=items,
            scope_counts=scope_counts,
            debug=debug,
        )
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE yt_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE yt_tasks SET result_json = ? WHERE id = ?",
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
        result_payload: dict[str, Any] = {"error": error}
        if debug is not None:
            result_payload["debug"] = debug
        result = json.dumps(result_payload, ensure_ascii=False)
        self._db.conn.execute(
            "UPDATE yt_tasks SET status = 'failed', result_json = ?, "
            "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result, task_id),
        )
        self._db.conn.commit()


def _is_stale_pending_result(result_json: Any) -> bool:
    try:
        payload = json.loads(str(result_json or "{}"))
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("error") == "stale_pending"
