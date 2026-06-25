"""Zhihu bootstrap event-conversion helpers.

Python-side entry point for Zhihu signals captured by the browser extension:
recent reads, personal activity likes/favorites, and collection contents.
This path is intentionally fetch-only: the API handler records task results,
and the CLI prints converted events without triggering profile generation.
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

ZHIHU_BOOTSTRAP_SCOPES = (
    "zhihu_read_history",
    "zhihu_activity",
    "zhihu_collection",
)

ZHIHU_BOOTSTRAP_SCOPE_LABELS: dict[str, str] = {
    "zhihu_read_history": "浏览记录",
    "zhihu_activity": "个人动态",
    "zhihu_collection": "收藏夹",
}

ZHIHU_BOOTSTRAP_SIGNAL_STRENGTH: dict[str, float] = {
    "zhihu_bootstrap_read_history": 0.35,
    "zhihu_bootstrap_activity_like": 0.85,
    "zhihu_bootstrap_activity_favorite": 0.9,
    "zhihu_bootstrap_collection": 0.9,
}

ZHIHU_DISCOVERY_SCORE_THRESHOLD = 0.60
ZHIHU_DISCOVERY_SCOPE_STRATEGIES = {
    "zhihu_search": "zhihu-search",
    "zhihu_hot": "zhihu-hot",
    "zhihu_feed": "zhihu-feed",
    "zhihu_creator": "zhihu-creator",
    "zhihu_related": "zhihu-related",
}


def _activity_event_type(action: str) -> str | None:
    normalized = action.strip()
    if normalized.startswith(("赞同了", "喜欢了")):
        return "like"
    if normalized.startswith("收藏了"):
        return "favorite"
    return None


def _event_type_for_item(item: dict[str, Any]) -> tuple[str | None, str]:
    scope = str(item.get("scope", "")).strip()
    if scope == "zhihu_read_history":
        return "view", "zhihu_bootstrap_read_history"
    if scope == "zhihu_collection":
        return "favorite", "zhihu_bootstrap_collection"
    if scope == "zhihu_activity":
        action = str(item.get("interaction_action", "")).strip()
        event_type = _activity_event_type(action)
        if event_type == "like":
            return event_type, "zhihu_bootstrap_activity_like"
        if event_type == "favorite":
            return event_type, "zhihu_bootstrap_activity_favorite"
    return None, ""


def zhihu_bootstrap_items_to_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert extension-collected Zhihu bootstrap items into unified events."""
    from openbiliclaw.sources.event_format import SOURCE_ZHIHU, build_event

    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        event_type, import_source = _event_type_for_item(item)
        if event_type is None:
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue

        author = str(item.get("author", "")).strip()
        scope = str(item.get("scope", "")).strip()
        content_type = str(item.get("content_type", "")).strip()
        content_id = str(item.get("content_id", "")).strip()
        question_id = str(item.get("question_id", "")).strip()
        collection_name = str(item.get("collection_name", "")).strip()
        action = str(item.get("interaction_action", "")).strip()
        label = ZHIHU_BOOTSTRAP_SCOPE_LABELS.get(scope, "事件")

        context = f"知乎{label}：{title or url}"
        if action:
            context = f"知乎{action}：{title or url}"
        if author:
            context = f"{context} 作者：{author}"

        metadata: dict[str, Any] = {
            "content_type": content_type,
            "content_id": content_id,
            "question_id": question_id,
            "collection_name": collection_name,
            "interaction_action": action,
            "interaction_time": str(item.get("interaction_time", "")).strip(),
            "import_source": import_source,
            "signal_strength": ZHIHU_BOOTSTRAP_SIGNAL_STRENGTH.get(import_source, 0.75),
        }
        summary = str(item.get("summary", "")).strip()
        if summary:
            metadata["summary"] = summary
        voteup = item.get("voteup")
        if isinstance(voteup, int | float):
            metadata["voteup_count"] = voteup

        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_ZHIHU,
                title=title,
                url=url,
                author=author,
                context=context,
                metadata=metadata,
            )
        )
    return events


def _item_key(item: dict[str, Any]) -> str:
    scope = str(item.get("scope", "")).strip()
    content_type = str(item.get("content_type", "")).strip()
    content_id = str(item.get("content_id", "")).strip()
    url = str(item.get("url", "")).strip()
    title = str(item.get("title", "")).strip()
    key = content_id or url or title
    prefix = f"{scope}:{content_type}" if content_type else scope
    return f"{prefix}:{key}" if key else ""


def zhihu_bootstrap_item_key(item: dict[str, Any]) -> str:
    """Return the stable cross-task identity key for one Zhihu bootstrap item."""
    return _item_key(item)


def zhihu_discovery_items_to_contents(
    items: list[dict[str, Any]],
    *,
    source_keyword_ids: dict[str, int] | None = None,
) -> list[Any]:
    """Convert extension-collected Zhihu discovery rows into discovered content.

    The browser extension returns a lightweight, API-shape-independent row. This
    function is the backend boundary that turns those rows into
    :class:`DiscoveredContent` for the shared candidate pool. Bootstrap scopes
    are intentionally ignored here; they only become user behavior events.
    """
    from openbiliclaw.discovery.engine import DiscoveredContent

    keyword_ids = source_keyword_ids or {}
    contents: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope", "")).strip()
        default_strategy = ZHIHU_DISCOVERY_SCOPE_STRATEGIES.get(scope)
        if default_strategy is None:
            continue
        content_type = str(item.get("content_type", "")).strip() or "answer"
        raw_content_id = str(item.get("content_id", "")).strip()
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not raw_content_id and not url:
            continue
        if not title and not url:
            continue

        content_id = f"{content_type}:{raw_content_id}" if raw_content_id else ""
        key = content_id or url
        if key in seen:
            continue
        seen.add(key)

        summary = str(item.get("summary", "")).strip()
        keyword = str(item.get("search_keyword", "")).strip()
        source_keyword_id = _optional_int(item.get("source_keyword_id"))
        if scope == "zhihu_search" and source_keyword_id is None and keyword:
            source_keyword_id = keyword_ids.get(keyword)

        contents.append(
            DiscoveredContent(
                bvid=content_id or url,
                title=title or url,
                up_name=str(item.get("author", "")).strip(),
                author_name=str(item.get("author", "")).strip(),
                description=summary,
                body_text=summary,
                content_id=content_id or url,
                content_url=url,
                content_type=content_type,
                source_platform="zhihu",
                source_strategy=str(item.get("source_strategy", "")).strip() or default_strategy,
                like_count=_safe_int(item.get("voteup")),
                favorite_count=_safe_int(item.get("favorite_count")),
                comment_count=_safe_int(item.get("comment_count")),
                score_threshold=ZHIHU_DISCOVERY_SCORE_THRESHOLD,
                source_keyword_id=source_keyword_id,
            )
        )
    return contents


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(float(str(value or 0).replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def recent_zhihu_creator_urls(db: Database, *, limit: int = 10) -> list[str]:
    """Return recent Zhihu author URLs from completed task results."""
    return _recent_zhihu_item_values(db, key="author_url", limit=limit)


def recent_zhihu_related_urls(db: Database, *, limit: int = 10) -> list[str]:
    """Return recent Zhihu content URLs suitable for related expansion."""
    return _recent_zhihu_item_values(db, key="url", limit=limit)


def _recent_zhihu_item_values(db: Database, *, key: str, limit: int) -> list[str]:
    try:
        rows = db.conn.execute(
            """
            SELECT result_json
            FROM zhihu_tasks
            WHERE status = 'completed' AND result_json IS NOT NULL
            ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC
            LIMIT 50
            """
        ).fetchall()
    except Exception:
        return []

    out: list[str] = []
    seen: set[str] = set()
    max_items = max(1, int(limit))
    for row in rows:
        try:
            payload = json.loads(
                str(row["result_json"] if isinstance(row, dict) else row[0] or "{}")
            )
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            value = str(item.get(key, "") or "").strip()
            if not value or value in seen:
                continue
            if (
                key == "url"
                and "zhihu.com/question/" not in value
                and "zhuanlan.zhihu.com/p/" not in value
            ):
                continue
            seen.add(value)
            out.append(value)
            if len(out) >= max_items:
                return out
    return out


def _merge_zhihu_result_payload(
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


class ZhihuTaskQueue:
    """Manages the zhihu_tasks SQLite table."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS zhihu_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_zhihu_tasks_status
                ON zhihu_tasks (status, created_at);
        """)
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(zhihu_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE zhihu_tasks ADD COLUMN claimed_at TIMESTAMP")
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
                "zhihu task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None
        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO zhihu_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def _budgeted_count_today(self, task_type: str) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = self._db.conn.execute(
            """
            SELECT status, result_json
            FROM zhihu_tasks
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
                f"SELECT * FROM zhihu_tasks WHERE {where} ORDER BY created_at ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE zhihu_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM zhihu_tasks WHERE id = ?", (task_id,)).fetchone()
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
            FROM zhihu_tasks
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
            UPDATE zhihu_tasks
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
            "SELECT * FROM zhihu_tasks WHERE id = ?",
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

        merged, added = _merge_zhihu_result_payload(
            current,
            items=items,
            scope_counts=scope_counts,
            debug=debug,
        )
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE zhihu_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE zhihu_tasks SET result_json = ? WHERE id = ?",
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
            "UPDATE zhihu_tasks SET status = 'failed', result_json = ?, "
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
