"""Douyin (douyin.com) bootstrap event-conversion helpers.

This module is the Python-side entry point for Douyin signals captured by
the browser extension. It is **deliberately independent** of
``xhs_tasks.py`` — no imports cross between them, the per-platform
constants are defined here, and the ``DyTaskQueue`` class (added in a
later task) will own its own SQLite table. The only intentional shared
layer is ``event_format.py``: Douyin events emit ``event_type`` values
from the canonical vocabulary so soul-engine can analyze cross-source
events uniformly.

See ``docs/plans/2026-05-06-douyin-bootstrap-import-design.md`` for the
architecture rationale and the open-source prior-art notes that
informed the URL / endpoint catalog used elsewhere in the dy_ tree.
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

# Map each Douyin bootstrap scope to its canonical event_type. Scopes
# are the ones the extension's MAIN-world fetch-tap can observe in a
# logged-in user's tab; see design doc §Scope.
DY_BOOTSTRAP_SCOPE_EVENT_TYPES: dict[str, str] = {
    "dy_post": "view",  # user posted it — weak taste signal but is one
    "dy_collect": "favorite",  # 收藏夹: most deliberate
    "dy_like": "like",  # 喜欢过 tab
    "dy_follow": "follow",  # 关注列表 — interest in a creator's catalog
}

# Per-scope signal strength fed into the preference layer. Numbers
# match the design doc; collect ranks highest because it's the most
# deliberate save-for-later action; post ranks lowest because the user
# being the author doesn't strongly indicate consumption preference.
DY_BOOTSTRAP_SIGNAL_STRENGTH: dict[str, float] = {
    "dy_post": 0.4,
    "dy_collect": 1.0,
    "dy_like": 0.85,
    "dy_follow": 0.6,
}

# Human-readable scope labels used in the natural-language context the
# preference / awareness LLM prompts read. Action verbs come from the
# event taxonomy; this label adds the "在抖音上" framing.
DY_BOOTSTRAP_SCOPE_LABELS: dict[str, str] = {
    "dy_post": "发布",
    "dy_collect": "收藏",
    "dy_like": "点赞",
    "dy_follow": "关注",
}

_DISCOVERY_CREATOR_SCOPE_PRIORITY = ("dy_follow", "dy_collect", "dy_like", "dy_post")


def dy_bootstrap_videos_to_events(
    videos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert extension-collected Douyin bootstrap items into events.

    Routes through ``event_format.build_event`` so the resulting dict is
    shape-identical to B站 / 小红书 events. Items missing both ``title``
    and ``url`` are dropped; items with an unknown scope are dropped.

    For ``dy_follow`` scope, ``creator_sec_uid`` (rather than
    ``aweme_id``) is the natural identity key, so we propagate that
    instead under the same metadata field name.
    """
    from openbiliclaw.sources.event_format import SOURCE_DOUYIN, build_event

    events: list[dict[str, Any]] = []
    for item in videos:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope", "")).strip()
        event_type = DY_BOOTSTRAP_SCOPE_EVENT_TYPES.get(scope)
        if event_type is None:
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue

        author = str(item.get("author", "")).strip()
        label = DY_BOOTSTRAP_SCOPE_LABELS[scope]
        # Custom context — scope label is more precise than the generic
        # event_type verb. Mirrors the wording style preference / soul
        # prompts already grew up reading from the XHS path.
        context = f"抖音{label}：{title or url}"
        if author:
            context = f"{context} 作者：{author}"

        # Identity key differs by scope.
        identity_key = "creator_sec_uid" if scope == "dy_follow" else "aweme_id"
        identity_value = str(item.get(identity_key, "")).strip()

        # scope_short strips the "dy_" prefix so import_source reads
        # "dy_bootstrap_collect" rather than "dy_bootstrap_dy_collect".
        scope_short = scope.removeprefix("dy_") if scope.startswith("dy_") else scope

        metadata: dict[str, Any] = {
            identity_key: identity_value,
            "author_sec_uid": str(item.get("author_sec_uid", "")).strip(),
            "cover_url": str(item.get("cover_url", "")).strip(),
            "import_source": f"dy_bootstrap_{scope_short}",
            "signal_strength": DY_BOOTSTRAP_SIGNAL_STRENGTH[scope],
        }

        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_DOUYIN,
                title=title,
                url=url,
                author=author,
                context=context,
                metadata=metadata,
            )
        )
    return events


def _video_key(video: dict[str, Any]) -> str:
    """Identity key for dedup. Includes scope so the same aweme_id can
    legitimately appear in two scopes (e.g. user posted AND collected)."""
    scope = str(video.get("scope", "")).strip()
    aweme_id = str(video.get("aweme_id", "")).strip()
    creator_sec_uid = str(video.get("creator_sec_uid", "")).strip()
    url = str(video.get("url", "")).strip()
    title = str(video.get("title", "")).strip()
    key = aweme_id or creator_sec_uid or url or title
    return f"{scope}:{key}" if key else ""


def dy_bootstrap_video_key(video: dict[str, Any]) -> str:
    """Return the stable cross-task identity key for one bootstrap video."""
    return _video_key(video)


def _merge_dy_result_payload(
    current: dict[str, Any],
    *,
    videos: list[dict[str, Any]] | None = None,
    scope_counts: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge a partial result into the current row.

    Returns the merged payload + the list of videos newly added by this
    merge (caller propagates only those to the soul pipeline so the
    same item never causes two events).

    Independent of xhs_tasks._merge_result_payload — Douyin uses
    aweme_id (not note_id) and the natural scope-counts logic differs
    once dy_history may join later.
    """
    merged_videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for video in current.get("videos") or []:
        if not isinstance(video, dict):
            continue
        key = _video_key(video)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_videos.append(video)

    added: list[dict[str, Any]] = []
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        key = _video_key(video)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_videos.append(video)
        added.append(video)

    merged: dict[str, Any] = {}
    if merged_videos:
        merged["videos"] = merged_videos

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
    # Backfill counts from observed videos if the executor didn't send them.
    for video in merged_videos:
        scope = str(video.get("scope", "")).strip()
        if scope and scope not in merged_counts:
            merged_counts[scope] = sum(
                1 for v in merged_videos if str(v.get("scope", "")).strip() == scope
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


def recent_dy_creator_sec_uids(
    db: Database,
    *,
    limit: int = 20,
    task_limit: int = 5,
) -> tuple[str, ...]:
    """Return creator sec_uid seeds from recent completed Douyin bootstrap tasks.

    Direct search / hot can soft-return HTTP 200 with empty lists. Creator
    timelines are currently the most reliable direct-cookie discovery
    surface, so discovery can use authors seen in recent bootstrap signals
    as a fallback seed list.
    """
    if limit <= 0 or task_limit <= 0:
        return ()

    rows = db.conn.execute(
        """
        SELECT result_json
        FROM dy_tasks
        WHERE status = 'completed' AND result_json IS NOT NULL
        ORDER BY completed_at DESC, created_at DESC
        LIMIT ?
        """,
        (task_limit,),
    ).fetchall()

    buckets: dict[str, list[str]] = {scope: [] for scope in _DISCOVERY_CREATOR_SCOPE_PRIORITY}
    fallback: list[str] = []
    for row in rows:
        try:
            payload = json.loads(str(row[0] or "{}"))
        except json.JSONDecodeError:
            continue
        videos = payload.get("videos")
        if not isinstance(videos, list):
            continue
        for video in videos:
            if not isinstance(video, dict):
                continue
            sec_uid = str(video.get("creator_sec_uid") or video.get("author_sec_uid") or "").strip()
            if not sec_uid:
                continue
            scope = str(video.get("scope", "")).strip()
            if scope in buckets:
                buckets[scope].append(sec_uid)
            else:
                fallback.append(sec_uid)

    seen: set[str] = set()
    result: list[str] = []
    for scope in _DISCOVERY_CREATOR_SCOPE_PRIORITY:
        for sec_uid in buckets[scope]:
            if sec_uid in seen:
                continue
            seen.add(sec_uid)
            result.append(sec_uid)
            if len(result) >= limit:
                return tuple(result)
    for sec_uid in fallback:
        if sec_uid in seen:
            continue
        seen.add(sec_uid)
        result.append(sec_uid)
        if len(result) >= limit:
            break
    return tuple(result)


class DyTaskQueue:
    """Manages the dy_tasks table.

    Independent of XhsTaskQueue. Schema mirrors xhs_tasks because the
    underlying state machine is the same (pending → completed/failed),
    but the table is separate so daily-budget exhaustion on one
    platform never blocks the other, and so future per-platform
    columns can be added without conflict.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS dy_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_dy_tasks_status
                ON dy_tasks (status, created_at);
        """)
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(dy_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE dy_tasks ADD COLUMN claimed_at TIMESTAMP")
            self._db.conn.commit()

    def enqueue(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> bool:
        """Enqueue a task if today's budget for this type allows it.

        Returns True on enqueue, False on budget exhausted.
        """
        return self.enqueue_with_id(task_type, payload, daily_budget=daily_budget) is not None

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, Any],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        """Enqueue a task and return its id, or None when budget exhausted.

        ``daily_budget <= 0`` disables the per-day cap; runtime producers are
        then controlled by source deficits and their per-run throttles.
        """
        count_today = self._budgeted_count_today(task_type) if daily_budget > 0 else 0

        if daily_budget > 0 and count_today >= daily_budget:
            logger.info(
                "dy task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None

        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO dy_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def _budgeted_count_today(self, task_type: str) -> int:
        """Count today's tasks that should consume the per-type daily budget."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = self._db.conn.execute(
            """
            SELECT status, result_json
            FROM dy_tasks
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
                f"SELECT * FROM dy_tasks WHERE {where} ORDER BY created_at ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE dy_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM dy_tasks WHERE id = ?", (task_id,)).fetchone()
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
            FROM dy_tasks
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
        """Fail pending tasks of selected types older than the given age."""
        normalized_types = tuple(str(t).strip() for t in task_types if str(t).strip())
        if not normalized_types:
            return 0
        cutoff_ts = datetime.now(UTC).timestamp() - max(0.0, float(older_than_seconds))
        cutoff_text = datetime.fromtimestamp(cutoff_ts, UTC).strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join("?" for _ in normalized_types)
        result_payload = json.dumps({"error": error}, ensure_ascii=False)
        cursor = self._db.conn.execute(
            f"""
            UPDATE dy_tasks
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
            "SELECT * FROM dy_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def merge_result(
        self,
        task_id: str,
        *,
        videos: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
        complete: bool = False,
    ) -> list[dict[str, Any]]:
        """Merge a partial/final result and optionally mark complete.

        Returns only the videos newly added by this merge so the caller
        can propagate exactly those to the soul pipeline (avoids
        duplicate events when the executor re-sends overlapping batches).
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

        merged, added = _merge_dy_result_payload(
            current,
            videos=videos,
            scope_counts=scope_counts,
            debug=debug,
        )
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE dy_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE dy_tasks SET result_json = ? WHERE id = ?",
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
            "UPDATE dy_tasks SET status = 'failed', result_json = ?, "
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
