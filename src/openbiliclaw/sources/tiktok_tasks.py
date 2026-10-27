"""TikTok (tiktok.com) bootstrap event-conversion helpers.

This module is the Python-side entry point for TikTok account signals
captured by the browser extension. It is **deliberately independent** of
``dy_tasks.py`` — no imports cross between them, the per-platform
constants are defined here, and ``TiktokTaskQueue`` owns its own SQLite
table. The only intentional shared layer is ``event_format.py``: TikTok
events emit ``event_type`` values from the canonical vocabulary so
soul-engine can analyze cross-source events uniformly.

TikTok is the first source with a watch-history scope (``tt_history``);
its signal strength follows the YouTube ``yt_history`` precedent for
passive exposure rather than the deliberate Douyin scopes.

See ``docs/platform-source-contract.tiktok.toml`` for the frozen
integration contract (profile/bootstrap/incremental all route through
``extension:browser-task:session-read-only``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")

# Map each TikTok bootstrap scope to its canonical event_type. Scopes
# are the account surfaces the extension's task executor can observe in
# a logged-in user's tab; see docs/platform-source-contract.tiktok.toml
# [events].mappings.
TT_BOOTSTRAP_SCOPE_EVENT_TYPES: dict[str, str] = {
    "tt_like": "like",  # liked videos
    "tt_collect": "favorite",  # favorited/collected videos: most deliberate
    "tt_history": "view",  # watch history — passive exposure
    "tt_follow": "follow",  # followed accounts — interest in a creator's catalog
}

# Per-scope signal strength fed into the preference layer. Collect ranks
# highest because it's the most deliberate save-for-later action;
# history ranks lowest because passive viewing only weakly indicates
# preference (mirrors the YouTube yt_history precedent).
TT_BOOTSTRAP_SIGNAL_STRENGTH: dict[str, float] = {
    "tt_like": 0.85,
    "tt_collect": 1.0,
    "tt_history": 0.35,
    "tt_follow": 0.6,
}

# Human-readable scope labels used in the natural-language context the
# preference / awareness LLM prompts read. Action verbs come from the
# event taxonomy; this label adds the "在 TikTok 上" framing.
TT_BOOTSTRAP_SCOPE_LABELS: dict[str, str] = {
    "tt_like": "点赞",
    "tt_collect": "收藏",
    "tt_history": "观看历史",
    "tt_follow": "关注",
}


def _text(value: object) -> str:
    """Accept text fields without stringifying arbitrary external containers."""
    return value.strip() if isinstance(value, str) else ""


def _bounded_result(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Enforce the immutable task's scope and cumulative per-scope row budget."""
    raw_scopes = payload.get("scopes", list(TT_BOOTSTRAP_SCOPE_EVENT_TYPES))
    allowed = (
        {
            scope
            for scope in raw_scopes
            if isinstance(scope, str) and scope in TT_BOOTSTRAP_SCOPE_EVENT_TYPES
        }
        if isinstance(raw_scopes, list)
        else set()
    )
    raw_cap = payload.get("max_items_per_scope", 300)
    cap = min(300, max(0, raw_cap)) if type(raw_cap) is int else 0
    counts = dict.fromkeys(allowed, 0)
    rows: list[dict[str, Any]] = []
    for row in result.get("videos", []):
        scope = _text(row.get("scope"))
        if scope not in allowed or counts[scope] >= cap:
            result["bounds_exceeded"] = True
            continue
        counts[scope] += 1
        rows.append(row)
    result["videos"] = rows
    result["scope_counts"] = counts
    if result.get("bounds_exceeded") is True and result.get("status") in {"ok", "empty"}:
        result["status"] = "degraded"
    return result


def tiktok_bootstrap_videos_to_events(
    videos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert extension-collected TikTok bootstrap items into events.

    Routes through ``event_format.build_event`` so the resulting dict is
    shape-identical to B站 / 小红书 / 抖音 events. Items missing both
    ``title`` and ``url`` are dropped; items with an unknown scope are
    dropped.

    For ``tt_follow`` scope, ``creator_sec_uid`` (rather than
    ``video_id``) is the natural identity key, so we propagate that
    instead under the same metadata field name.
    """
    from openbiliclaw.sources.event_format import SOURCE_TIKTOK, build_event

    events: list[dict[str, Any]] = []
    for item in videos:
        if not isinstance(item, dict):
            continue
        scope = _text(item.get("scope", ""))
        event_type = TT_BOOTSTRAP_SCOPE_EVENT_TYPES.get(scope)
        if event_type is None:
            continue

        title = _text(item.get("title", ""))
        url = _text(item.get("url", ""))
        if not title and not url:
            continue

        author = _text(item.get("author", ""))
        label = TT_BOOTSTRAP_SCOPE_LABELS[scope]
        # Custom context — scope label is more precise than the generic
        # event_type verb. Mirrors the wording style preference / soul
        # prompts already grew up reading from the Douyin path.
        context = f"TikTok{label}：{title or url}"
        if author:
            context = f"{context} 作者：{author}"

        # Identity key differs by scope.
        identity_key = "creator_sec_uid" if scope == "tt_follow" else "video_id"
        identity_value = _text(item.get(identity_key, ""))

        # scope_short strips the "tt_" prefix so import_source reads
        # "tt_bootstrap_collect" rather than "tt_bootstrap_tt_collect".
        scope_short = scope.removeprefix("tt_") if scope.startswith("tt_") else scope

        metadata: dict[str, Any] = {
            identity_key: identity_value,
            "author_sec_uid": _text(item.get("author_sec_uid", "")),
            "cover_url": _text(item.get("cover_url", "")),
            "import_source": f"tt_bootstrap_{scope_short}",
            "signal_strength": TT_BOOTSTRAP_SIGNAL_STRENGTH[scope],
        }

        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_TIKTOK,
                title=title,
                url=url,
                author=author,
                context=context,
                metadata=metadata,
            )
        )
    return events


def _video_key(video: dict[str, Any]) -> str:
    """Identity key for dedup. Includes scope so the same video_id can
    legitimately appear in two scopes (e.g. user liked AND collected)."""
    scope = _text(video.get("scope", ""))
    video_id = _text(video.get("video_id", ""))
    creator_sec_uid = _text(video.get("creator_sec_uid", ""))
    if scope not in TT_BOOTSTRAP_SCOPE_EVENT_TYPES:
        return ""
    key = creator_sec_uid if scope == "tt_follow" else video_id
    return f"{scope}:{key}" if key else ""


def tiktok_bootstrap_video_key(video: dict[str, Any]) -> str:
    """Return the stable cross-task identity key for one bootstrap video."""
    return _video_key(video)


def _merge_tiktok_result_payload(
    current: dict[str, Any],
    *,
    videos: list[dict[str, Any]] | None = None,
    scope_counts: dict[str, Any] | None = None,
    debug: dict[str, Any] | None = None,
    completion_status: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Merge a partial result into the current row.

    Returns the merged payload + the list of videos newly added by this
    merge (caller propagates only those to the soul pipeline so the
    same item never causes two events).

    Independent of dy_tasks._merge_dy_result_payload — TikTok uses
    video_id (not aweme_id) and adds the tt_history scope.
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
    for key in ("account_key", "sec_uid_key", "bounds_exceeded"):
        if current.get(key):
            merged[key] = current[key]
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

    # A partial update must never erase a terminal semantic status that was
    # already persisted in result_json.  The queue rejects late terminal-row
    # updates below, but preserving the field here keeps the pure merge helper
    # safe for callers that merge an in-flight snapshot carrying status
    # metadata.
    preserved_status = completion_status or current.get("status")
    if isinstance(preserved_status, str) and preserved_status.strip():
        merged["status"] = preserved_status.strip()

    return merged, added


class TiktokTaskQueue:
    """Manages the tiktok_tasks table.

    Independent of DyTaskQueue. Schema mirrors dy_tasks because the
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
            CREATE TABLE IF NOT EXISTS tiktok_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tiktok_tasks_status
                ON tiktok_tasks (status, created_at);
        """)
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(tiktok_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE tiktok_tasks ADD COLUMN claimed_at TIMESTAMP")
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
        conn = self._db.conn
        participating_in_transaction = bool(conn.in_transaction)
        count_today = self._budgeted_count_today(task_type) if daily_budget > 0 else 0

        if daily_budget > 0 and count_today >= daily_budget:
            logger.info(
                "tiktok task budget exhausted: type=%s used_today=%d budget=%d "
                "(per-day UTC cap; 0 = unlimited)",
                task_type,
                count_today,
                daily_budget,
            )
            return None

        task_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO tiktok_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        if not participating_in_transaction:
            conn.commit()
        return task_id

    def _budgeted_count_today(self, task_type: str) -> int:
        """Count today's tasks that should consume the per-type daily budget."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = self._db.conn.execute(
            """
            SELECT status, result_json
            FROM tiktok_tasks
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

    def bind_account(
        self,
        task_id: str,
        *,
        viewer_handle: object,
        viewer_sec_uid: object,
        profile_update: bool,
    ) -> str:
        """Bind observed viewer claims once; never label DOM identity as verified.

        Hash the normalized claims locally. Profile imports reject account switches;
        ordinary smoke tasks only bind their own result and cannot bind the profile.
        """
        handle = _text(viewer_handle).lower()
        sec_uid = _text(viewer_sec_uid)
        if not re.fullmatch(r"[a-z0-9_.]{2,24}", handle):
            raise ValueError("tiktok_viewer_identity_required")
        if sec_uid and not re.fullmatch(r"[A-Za-z0-9_=-]{6,256}", sec_uid):
            raise ValueError("tiktok_viewer_identity_invalid")
        # The DOM can identify the same viewer with or without SSR secUid.
        # Keep the handle namespace stable and record secUid only as an optional
        # conflict guard. A handle change remains an explicit account switch.
        account_key = "sha256:" + hashlib.sha256(("handle:" + handle).encode()).hexdigest()
        sec_uid_key = (
            "sha256:" + hashlib.sha256(("sec:" + sec_uid).encode()).hexdigest() if sec_uid else ""
        )
        conn = self._db.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, result_json FROM tiktok_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            current = json.loads(row["result_json"] or "{}")
            if row["status"] in {"completed", "failed"} or current.get(
                "_openbiliclaw_terminal_status"
            ):
                conn.commit()
                return _text(current.get("account_key"))
            previous = current.get("account_key")
            previous_uid_key = _text(current.get("sec_uid_key"))
            if (previous and previous != account_key) or (
                previous_uid_key and sec_uid_key and previous_uid_key != sec_uid_key
            ):
                raise ValueError("tiktok_task_account_mismatch")
            if profile_update:
                bound = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'tiktok_profile_account_key'"
                ).fetchone()
                bound_uid = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'tiktok_profile_sec_uid_key'"
                ).fetchone()
                if (bound and bound["value"] != account_key) or (
                    bound_uid and sec_uid_key and bound_uid["value"] != sec_uid_key
                ):
                    raise ValueError("tiktok_account_switch_requires_reset")
                conn.execute(
                    "INSERT OR IGNORE INTO auth_state (key, value) VALUES (?, ?)",
                    ("tiktok_profile_account_key", account_key),
                )
                if sec_uid_key:
                    conn.execute(
                        "INSERT OR IGNORE INTO auth_state (key, value) VALUES (?, ?)",
                        ("tiktok_profile_sec_uid_key", sec_uid_key),
                    )
                elif bound_uid:
                    sec_uid_key = str(bound_uid["value"])
            current["account_key"] = account_key
            if sec_uid_key:
                current["sec_uid_key"] = sec_uid_key
            conn.execute(
                "UPDATE tiktok_tasks SET result_json = ? WHERE id = ?",
                (json.dumps(current), task_id),
            )
            conn.commit()
            return account_key
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def next_pending(self, only_ids: set[str] | None = None) -> dict[str, Any] | None:
        stale_before = (datetime.now(UTC) - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
        # ``only_ids`` restricts which tasks may be claimed (gui-init: during an
        # active init only init-owned bootstrap tasks are handed out). None = all.
        # Staged results remain stale-reclaimable so a lost result response
        # gets a fresh dispatcher callback that repairs canonical projections.
        where = (
            "(status = 'pending' OR "
            "(status = 'in_progress' AND (claimed_at IS NULL OR claimed_at <= ?)))"
        )
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
            # The extension mutex is scoped to one service-worker global. Two
            # unpacked extension IDs or browser profiles do not share it and
            # could otherwise claim different TikTok tasks concurrently. Keep
            # the source queue single-flight at the durable claim boundary;
            # stale leases remain eligible for recovery below.
            active = conn.execute(
                """
                SELECT 1
                FROM tiktok_tasks
                WHERE status = 'in_progress' AND claimed_at > ?
                LIMIT 1
                """,
                (stale_before,),
            ).fetchone()
            if active is not None:
                conn.commit()
                return None
            row = conn.execute(
                f"SELECT * FROM tiktok_tasks WHERE {where} ORDER BY created_at ASC, rowid ASC "
                "LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE tiktok_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM tiktok_tasks WHERE id = ?", (task_id,)).fetchone()
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
            FROM tiktok_tasks
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
            UPDATE tiktok_tasks
            SET status = 'failed',
                result_json = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE status = 'pending'
              AND type IN ({placeholders})
              AND created_at < ?
              AND COALESCE(result_json, '') NOT LIKE
                  '%"_openbiliclaw_terminal_status"%'
            """,
            (result_payload, *normalized_types, cutoff_text),
        )
        self._db.conn.commit()
        return int(cursor.rowcount or 0)

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self._db.conn.execute(
            "SELECT * FROM tiktok_tasks WHERE id = ?",
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
        completion_status: str | None = None,
        complete: bool = False,
    ) -> list[dict[str, Any]]:
        """Merge a partial/final result and optionally mark complete.

        Returns only the videos newly added by this merge so the caller
        can propagate exactly those to the soul pipeline (avoids
        duplicate events when the executor re-sends overlapping batches).

        Completed/failed rows are immutable.  Extension callbacks can arrive
        out of order (for example a final ``degraded`` followed by an older
        ``partial``); those callbacks are idempotently ignored so they cannot
        erase the final result status or propagate late events.
        """
        conn = self._db.open_connection()
        try:
            # Serialize the read/merge/write transition across API workers.  A
            # status check before this transaction is only an optimization:
            # without the lock, a final callback could commit between that
            # check and a late partial's write.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, result_json, payload_json FROM tiktok_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None or str(row["status"]) in {"completed", "failed"}:
                conn.commit()
                return []

            current: dict[str, Any] = {}
            if row["result_json"]:
                try:
                    parsed = json.loads(str(row["result_json"]))
                    if isinstance(parsed, dict):
                        current = parsed
                except json.JSONDecodeError:
                    current = {}

            from openbiliclaw.sources.task_result_protocol import staged_terminal_status

            if staged_terminal_status(current):
                conn.commit()
                return []

            merged, added = _merge_tiktok_result_payload(
                current,
                videos=videos,
                scope_counts=scope_counts,
                debug=debug,
                completion_status=completion_status,
            )
            merged = _bounded_result(json.loads(row["payload_json"] or "{}"), merged)
            accepted_keys = {_video_key(item) for item in merged["videos"]}
            added = [item for item in added if _video_key(item) in accepted_keys]
            result = json.dumps(merged, ensure_ascii=False)
            if complete:
                conn.execute(
                    "UPDATE tiktok_tasks SET status = 'completed', result_json = ?, "
                    "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (result, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tiktok_tasks SET result_json = ? WHERE id = ?",
                    (result, task_id),
                )
            conn.commit()
            return added
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def stage_final_result(
        self,
        task_id: str,
        *,
        terminal_status: str,
        videos: list[dict[str, Any]] | None = None,
        scope_counts: dict[str, Any] | None = None,
        debug: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stage the immutable canonical result before downstream projection."""
        from openbiliclaw.sources.task_result_protocol import (
            STAGED_TERMINAL_STATUS_FIELD,
            mutate_unstaged_result,
        )

        if not terminal_status.strip():
            raise ValueError("terminal_status is required")
        task = self.get(task_id) or {}
        task_payload = json.loads(task.get("payload_json") or "{}")

        def merge(current: dict[str, Any]) -> dict[str, Any]:
            merged, _added = _merge_tiktok_result_payload(
                current,
                videos=videos,
                scope_counts=scope_counts,
                debug=debug,
                completion_status=terminal_status,
            )
            canonical = _bounded_result(task_payload, merged)
            canonical[STAGED_TERMINAL_STATUS_FIELD] = canonical["status"]
            return canonical

        # The common mutation primitive holds the same BEGIN IMMEDIATE lock,
        # checks first-final-wins, and publishes payload + derived marker in
        # one write. Derive the status inside that lock so a concurrently
        # accepted partial cannot hide that its rows were clipped.
        _mutated, canonical = mutate_unstaged_result(
            self._db,
            table="tiktok_tasks",
            task_id=task_id,
            mutate=merge,
        )
        return canonical

    def complete_staged_result(self, task_id: str) -> bool:
        """Mark a staged canonical result complete without replacing it."""
        from openbiliclaw.sources.task_result_protocol import complete_staged_result

        return complete_staged_result(self._db, table="tiktok_tasks", task_id=task_id)

    def fail(
        self,
        task_id: str,
        *,
        error: str = "",
        debug: dict[str, Any] | None = None,
    ) -> bool:
        """Mark an active task failed without overwriting a terminal result."""
        result_payload: dict[str, Any] = {"error": error}
        if debug is not None:
            result_payload["debug"] = debug
        from openbiliclaw.sources.task_result_protocol import mutate_unstaged_result

        mutated, _canonical = mutate_unstaged_result(
            self._db,
            table="tiktok_tasks",
            task_id=task_id,
            mutate=lambda current: {**current, **result_payload},
            terminal_status="failed",
        )
        return mutated


def _is_stale_pending_result(result_json: Any) -> bool:
    try:
        payload = json.loads(str(result_json or "{}"))
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("error") == "stale_pending"
