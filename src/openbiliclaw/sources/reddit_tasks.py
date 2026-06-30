"""Reddit source helpers.

Reddit does not currently have a reliable anonymous API path for this project.
The default steady-state source contract uses rdt-cli with the user's logged-in
session/cookies. Same-origin OpenBiliClaw browser-extension tasks remain
available for bootstrap saved / upvoted / subscribed initialization signals and
as an explicit browser-backed discovery backend.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.event_format import SOURCE_REDDIT, build_event

if TYPE_CHECKING:
    from collections.abc import Iterable

    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

REDDIT_DISCOVERY_SCORE_THRESHOLD = 0.60
REDDIT_SOURCE_ORDER = ("search", "hot", "subreddit", "related")
REDDIT_SOURCE_STRATEGIES = {
    "search": "reddit-search",
    "hot": "reddit-hot",
    "subreddit": "reddit-subreddit",
    "related": "reddit-related",
}
REDDIT_BOOTSTRAP_SCOPES = ("reddit_saved", "reddit_upvoted", "reddit_subscribed")
REDDIT_REQUIRED_COOKIE_NAMES = ("reddit_session",)
_RDT_CREDENTIAL_TTL_SECONDS = 7 * 24 * 60 * 60
_REDDIT_BOOTSTRAP_EVENT_BY_SCOPE: dict[str, tuple[str, float]] = {
    "reddit_saved": ("favorite", 0.90),
    "reddit_upvoted": ("like", 0.75),
    "reddit_subscribed": ("follow", 0.65),
}


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class RedditCommandStatus:
    backend: str
    state: str
    message: str


@dataclass(frozen=True)
class RedditCredentialSyncResult:
    ok: bool
    has_cookie: bool
    cookie_names: tuple[str, ...]
    credential_file: Path
    message: str
    error_code: str = ""


def sync_rdt_credential_from_cookie_header(
    cookie_header: str,
    *,
    source: str = "extension",
) -> RedditCredentialSyncResult:
    """Persist a browser Reddit Cookie header in rdt-cli's credential shape."""

    cookie_pairs = _parse_cookie_header(cookie_header)
    cookie_names = tuple(sorted(cookie_pairs))
    credential_file = _rdt_credential_file()
    missing = [name for name in REDDIT_REQUIRED_COOKIE_NAMES if name not in cookie_pairs]
    if not cookie_pairs:
        return RedditCredentialSyncResult(
            ok=False,
            has_cookie=False,
            cookie_names=cookie_names,
            credential_file=credential_file,
            message="cookie payload is empty",
            error_code="empty_cookie",
        )
    if missing:
        return RedditCredentialSyncResult(
            ok=True,
            has_cookie=False,
            cookie_names=cookie_names,
            credential_file=credential_file,
            message="Reddit Cookie stored? no; missing reddit_session.",
            error_code="missing_reddit_session",
        )

    now = time.time()
    payload = {
        "cookies": cookie_pairs,
        "source": f"openbiliclaw:{source.strip() or 'extension'}",
        "username": None,
        "modhash": cookie_pairs.get("modhash") or cookie_pairs.get("csrf_token"),
        "saved_at": now,
        "last_verified_at": None,
    }
    credential_file.parent.mkdir(parents=True, exist_ok=True)
    credential_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with suppress(OSError):
        credential_file.chmod(0o600)

    return RedditCredentialSyncResult(
        ok=True,
        has_cookie=True,
        cookie_names=cookie_names,
        credential_file=credential_file,
        message="Reddit Cookie synced into rdt credential store.",
    )


def rdt_credential_cookie_names() -> tuple[str, ...]:
    """Return cookie names currently present in rdt-cli's credential file."""

    try:
        data = json.loads(_rdt_credential_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(data, dict):
        return ()
    cookies = data.get("cookies")
    if not isinstance(cookies, dict):
        return ()
    return tuple(sorted(str(name) for name, value in cookies.items() if str(name) and value))


def _parse_cookie_header(cookie: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        pairs[name] = value
    return pairs


def reddit_item_key(item: dict[str, Any]) -> str:
    """Return the stable identity key for one Reddit item."""

    content_id = _content_id(item)
    url = _content_url(item)
    title = _text(item, "title", "body", "selftext", "text")
    return content_id or url or title


def parse_reddit_command_output(output: str) -> list[dict[str, Any]]:
    """Parse OpenCLI / rdt structured output into a list of item dicts."""

    parsed = _parse_json_or_yaml(output)
    return _extract_item_dicts(parsed)


def reddit_items_to_contents(
    items: list[dict[str, Any]],
    *,
    strategy: str,
    source_keyword_ids: dict[str, int] | None = None,
) -> list[DiscoveredContent]:
    """Convert Reddit post/comment rows into discovery candidates."""

    keyword_ids = source_keyword_ids or {}
    fallback_keyword_id = next(iter(keyword_ids.values()), None) if len(keyword_ids) == 1 else None
    contents: list[DiscoveredContent] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        content_id = _content_id(item)
        url = _content_url(item)
        title = _text(item, "title", "name")
        body_text = _text(item, "selftext", "body", "text", "content")
        if not content_id and not url:
            continue
        if not title and not body_text and not url:
            continue
        key = content_id or url
        if key in seen:
            continue
        seen.add(key)

        subreddit = _subreddit(item)
        tags = [f"r/{subreddit}"] if subreddit else []
        author = _author(item)
        keyword = _text(item, "search_keyword", "query")
        source_keyword_id = _optional_int(item.get("source_keyword_id"))
        if source_keyword_id is None and keyword:
            source_keyword_id = keyword_ids.get(keyword)
        if source_keyword_id is None:
            source_keyword_id = fallback_keyword_id

        contents.append(
            DiscoveredContent(
                bvid=content_id or url,
                title=title or body_text[:80] or url,
                up_name=author,
                author_name=author,
                description=body_text,
                body_text=body_text,
                content_id=content_id or url,
                content_url=url,
                content_type=_content_type(content_id, item),
                source_platform=SOURCE_REDDIT,
                source_strategy=strategy,
                like_count=_safe_int(item.get("score") or item.get("ups") or item.get("upvotes")),
                comment_count=_safe_int(
                    item.get("num_comments") or item.get("comment_count") or item.get("comments")
                ),
                tags=tags,
                score_threshold=REDDIT_DISCOVERY_SCORE_THRESHOLD,
                source_keyword_id=source_keyword_id,
            )
        )
    return contents


def reddit_items_to_events(
    items: list[dict[str, Any]],
    *,
    import_source: str,
) -> list[dict[str, Any]]:
    """Convert Reddit rows into unified events.

    Discovery / fetch smoke rows stay as low-strength ``view`` events unless
    the caller explicitly writes them to memory. Browser bootstrap rows from
    the logged-in user's saved / upvoted / subscribed feeds become real profile
    signals so Reddit can participate in first-run initialization.
    """

    events: list[dict[str, Any]] = []
    for item in items:
        content_id = _content_id(item)
        url = _content_url(item)
        title = _text(item, "title", "name") or _text(item, "selftext", "body", "text")[:80]
        if not title and not url:
            continue
        subreddit = _subreddit(item)
        scope = _text(item, "scope")
        event_type, signal_strength = _REDDIT_BOOTSTRAP_EVENT_BY_SCOPE.get(
            scope,
            ("view", 0.25),
        )
        metadata: dict[str, Any] = {
            "content_id": content_id,
            "subreddit": subreddit,
            "import_source": import_source,
            "signal_strength": signal_strength,
        }
        if scope:
            metadata["scope"] = scope
        content_type = _text(item, "content_type", "type")
        if content_type:
            metadata["content_type"] = content_type
        if score := _safe_int(item.get("score") or item.get("ups") or item.get("upvotes")):
            metadata["score"] = score
        if comments := _safe_int(
            item.get("num_comments") or item.get("comment_count") or item.get("comments")
        ):
            metadata["num_comments"] = comments
        if body := _text(item, "selftext", "body", "text", "content"):
            metadata["summary"] = body
        if public_description := _text(item, "public_description", "description"):
            metadata["summary"] = public_description
        events.append(
            build_event(
                event_type=event_type,
                source_platform=SOURCE_REDDIT,
                title=title or url,
                url=url,
                author=_author(item),
                metadata=metadata,
            )
        )
    return events


def recent_reddit_subreddits(db: Database, *, limit: int = 10) -> list[str]:
    """Return recent subreddit names from completed extension task results."""

    return _recent_reddit_item_values(db, key="subreddit", limit=limit)


def recent_reddit_related_urls(db: Database, *, limit: int = 10) -> list[str]:
    """Return recent Reddit post/comment URLs suitable for related expansion."""

    return _recent_reddit_item_values(db, key="url", limit=limit)


def _recent_reddit_item_values(db: Database, *, key: str, limit: int) -> list[str]:
    try:
        rows = db.conn.execute(
            """
            SELECT result_json
            FROM reddit_tasks
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
            raw = row["result_json"] if hasattr(row, "keys") else row[0]
            payload = json.loads(str(raw or "{}"))
        except (json.JSONDecodeError, TypeError, KeyError, IndexError):
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            value = _content_url(item) if key == "url" else str(item.get(key, "") or "").strip()
            if key == "subreddit":
                value = value.removeprefix("r/")
            if not value or value in seen:
                continue
            if key == "url" and "reddit.com" not in value and "redd.it" not in value:
                continue
            seen.add(value)
            out.append(value)
            if len(out) >= max_items:
                return out
    return out


def _merge_reddit_result_payload(
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
        key = reddit_item_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged_items.append(item)

    added: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = reddit_item_key(item)
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


class RedditTaskQueue:
    """Manages the reddit_tasks SQLite table for extension-backed tasks."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        self._db.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reddit_tasks (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status       TEXT NOT NULL DEFAULT 'pending',
                result_json  TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                claimed_at   TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_reddit_tasks_status
                ON reddit_tasks (status, created_at);
            CREATE INDEX IF NOT EXISTS idx_reddit_tasks_type_created
                ON reddit_tasks (type, created_at);
            """
        )
        columns = {
            str(row["name"])
            for row in self._db.conn.execute("PRAGMA table_info(reddit_tasks)").fetchall()
        }
        if "claimed_at" not in columns:
            self._db.conn.execute("ALTER TABLE reddit_tasks ADD COLUMN claimed_at TIMESTAMP")
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
                "reddit task budget exhausted: type=%s, count=%d, budget=%d",
                task_type,
                count_today,
                daily_budget,
            )
            return None
        task_id = str(uuid.uuid4())
        self._db.conn.execute(
            "INSERT INTO reddit_tasks (id, type, payload_json) VALUES (?, ?, ?)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._db.conn.commit()
        return task_id

    def _budgeted_count_today(self, task_type: str) -> int:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = self._db.conn.execute(
            """
            SELECT status, result_json
            FROM reddit_tasks
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
                f"SELECT * FROM reddit_tasks WHERE {where} ORDER BY created_at ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            task_id = str(row["id"])
            conn.execute(
                "UPDATE reddit_tasks SET status = 'in_progress', claimed_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (task_id,),
            )
            claimed = conn.execute("SELECT * FROM reddit_tasks WHERE id = ?", (task_id,)).fetchone()
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
        selected_statuses = statuses or ("pending", "in_progress", "completed", "failed")
        if not selected_statuses:
            return None
        placeholders = ",".join("?" for _ in selected_statuses)
        cutoff = (datetime.now(UTC) - timedelta(hours=recent_hours)).strftime("%Y-%m-%d %H:%M:%S")
        row = self._db.conn.execute(
            f"""
            SELECT *
            FROM reddit_tasks
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
            UPDATE reddit_tasks
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
            "SELECT * FROM reddit_tasks WHERE id = ?",
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

        merged, added = _merge_reddit_result_payload(
            current,
            items=items,
            scope_counts=scope_counts,
            debug=debug,
        )
        result = json.dumps(merged, ensure_ascii=False)
        if complete:
            self._db.conn.execute(
                "UPDATE reddit_tasks SET status = 'completed', result_json = ?, "
                "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (result, task_id),
            )
        else:
            self._db.conn.execute(
                "UPDATE reddit_tasks SET result_json = ? WHERE id = ?",
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
            "UPDATE reddit_tasks SET status = 'failed', result_json = ?, "
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


def probe_reddit_command_backend(
    backend: str,
    *,
    which: Any = None,
    runner: CommandRunner | None = None,
    timeout: float = 10.0,
) -> RedditCommandStatus:
    """Probe Reddit command backend without starting browser automation."""

    allow_in_process = not callable(which)
    which_fn = which if callable(which) else _default_which
    normalized = str(backend or "auto").strip().lower()
    backends = ("opencli", "rdt") if normalized == "auto" else (normalized,)
    findings: list[RedditCommandStatus] = []
    for candidate in backends:
        if candidate == "opencli":
            status = _probe_opencli(which=which_fn, runner=runner, timeout=timeout)
        elif candidate == "rdt":
            status = _probe_rdt(
                which=which_fn,
                runner=runner,
                timeout=timeout,
                allow_in_process=allow_in_process,
            )
        else:
            status = RedditCommandStatus(
                backend=candidate,
                state="missing",
                message=f"未知 Reddit 后端 `{candidate}`，当前支持 opencli / rdt。",
            )
        if status.state == "ready":
            return status
        findings.append(status)

    if normalized == "auto" and all(item.state == "missing" for item in findings):
        return RedditCommandStatus(
            backend="",
            state="missing",
            message="未安装 opencli 或 rdt，无法使用 Reddit 登录态命令后端。",
        )
    return findings[0] if findings else RedditCommandStatus("", "missing", "Reddit 后端不可用。")


def build_reddit_command(
    backend: str,
    *,
    mode: str,
    query: str = "",
    subreddit: str = "",
    limit: int = 10,
) -> list[str]:
    """Return the shell command for one Reddit discovery mode."""

    selected = str(backend or "opencli").strip().lower()
    max_items = max(1, int(limit))
    if selected == "rdt":
        if mode == "search":
            return ["rdt", "search", query, "-n", str(max_items), "--json"]
        if mode == "hot":
            target = (subreddit or "all").removeprefix("r/")
            if target.lower() == "all":
                return ["rdt", "all", "-n", str(max_items), "--json"]
            if target.lower() == "popular":
                return ["rdt", "popular", "-n", str(max_items), "--json"]
            return ["rdt", "sub", target, "--sort", "hot", "-n", str(max_items), "--json"]
        if mode == "subreddit":
            target = (subreddit or query).removeprefix("r/")
            return ["rdt", "sub", target, "-n", str(max_items), "--json"]
        if mode == "related":
            target = _reddit_read_post_id(query)
            if not target:
                raise ValueError("reddit related mode requires a Reddit post id or URL")
            return ["rdt", "read", target, "-n", str(max_items), "--json"]
    else:
        if mode == "search":
            return ["opencli", "reddit", "search", query, "-n", str(max_items), "-f", "yaml"]
        if mode == "hot":
            return [
                "opencli",
                "reddit",
                "hot",
                subreddit or "all",
                "-n",
                str(max_items),
                "-f",
                "yaml",
            ]
        if mode == "subreddit":
            return [
                "opencli",
                "reddit",
                "subreddit",
                subreddit or query,
                "-n",
                str(max_items),
                "-f",
                "yaml",
            ]
        if mode == "related":
            return ["opencli", "reddit", "read", query, "-f", "yaml"]
    raise ValueError(f"unsupported reddit discovery mode: {mode}")


def run_reddit_command(
    args: list[str],
    *,
    runner: CommandRunner | None = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Execute one Reddit command and parse its structured output."""

    completed = (runner or _subprocess_run)(args, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"Reddit command failed: exit {completed.returncode}")
    return parse_reddit_command_output(completed.stdout or "")


def _parse_json_or_yaml(output: str) -> Any:
    text = str(output or "").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(text)
    except Exception:
        return []


def _extract_item_dicts(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        out: list[dict[str, Any]] = []
        for item in parsed:
            out.extend(_extract_item_dicts(item))
        return out
    if not isinstance(parsed, dict):
        return []
    children = parsed.get("children")
    if isinstance(children, list):
        rows: list[dict[str, Any]] = []
        for child in children:
            if isinstance(child, dict) and isinstance(child.get("data"), dict):
                rows.append(dict(child["data"]))
        return rows
    for key in ("items", "results", "posts", "comments", "data"):
        value = parsed.get(key)
        nested = _extract_item_dicts(value)
        if nested:
            return nested
    return [dict(parsed)] if _looks_like_reddit_item(parsed) else []


def _looks_like_reddit_item(item: dict[str, Any]) -> bool:
    return any(key in item for key in ("id", "title", "permalink", "url", "selftext", "body"))


def _probe_opencli(
    *,
    which: Any,
    runner: CommandRunner | None,
    timeout: float,
) -> RedditCommandStatus:
    if not which("opencli"):
        return RedditCommandStatus("opencli", "missing", "未安装 opencli。")
    try:
        completed = (runner or _subprocess_run)(
            ["opencli", "daemon", "status"],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RedditCommandStatus("opencli", "error", f"opencli 状态检查失败: {exc}")
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    if "extension:" in output and "connected" in output and "disconnected" not in output:
        return RedditCommandStatus("opencli", "ready", "OpenCLI 可用，已连接浏览器扩展。")
    if completed.returncode == 0:
        return RedditCommandStatus(
            "opencli",
            "login_required",
            "OpenCLI 已安装，但未确认浏览器扩展连接；请打开已登录 Reddit 的 Chrome。",
        )
    return RedditCommandStatus("opencli", "error", "opencli daemon status 异常退出。")


def _probe_rdt(
    *,
    which: Any,
    runner: CommandRunner | None,
    timeout: float,
    allow_in_process: bool = False,
) -> RedditCommandStatus:
    if not which("rdt") and not (allow_in_process and _rdt_cli_module_available()):
        return RedditCommandStatus("rdt", "missing", "未安装 rdt。")
    credential_state, credential_message = _rdt_saved_credential_state()
    if credential_state != "present":
        return RedditCommandStatus("rdt", "login_required", credential_message)
    try:
        completed = (runner or _subprocess_run)(["rdt", "status", "--json"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return RedditCommandStatus(
            "rdt",
            "login_required",
            "rdt 状态检查超时；"
            "请在已连接插件的浏览器登录 Reddit 等待自动同步，或运行 `rdt login`。",
        )
    except OSError as exc:
        return RedditCommandStatus("rdt", "error", f"rdt 状态检查失败: {exc}")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return RedditCommandStatus(
            "rdt",
            "error",
            f"rdt 异常退出: {detail[-1] if detail else completed.returncode}",
        )
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    info = data.get("data", data) if isinstance(data, dict) else {}
    if isinstance(info, dict) and bool(info.get("authenticated")):
        username = str(info.get("username") or "").strip()
        suffix = f" ({username})" if username else ""
        return RedditCommandStatus("rdt", "ready", f"rdt 已登录{suffix}。")
    return RedditCommandStatus(
        "rdt",
        "login_required",
        "rdt 已安装但未登录；请在已连接插件的浏览器登录 Reddit 等待自动同步，或运行 `rdt login`。",
    )


def _rdt_saved_credential_state() -> tuple[str, str]:
    credential_file = _rdt_credential_file()
    if not credential_file.exists():
        return (
            "missing",
            "rdt 已安装但未同步 Reddit Cookie。"
            "请在已连接插件的浏览器登录 Reddit；插件会自动同步，也可运行 `rdt login`。",
        )
    try:
        data = json.loads(credential_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid", "rdt credential 文件不可读，请等待插件重新同步或运行 `rdt login`。"
    if not isinstance(data, dict):
        return "invalid", "rdt credential 文件格式异常，请等待插件重新同步或运行 `rdt login`。"
    cookies = data.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        return (
            "invalid",
            "rdt credential 文件没有可用 Cookie，请等待插件重新同步或运行 `rdt login`。",
        )
    missing = [name for name in REDDIT_REQUIRED_COOKIE_NAMES if not cookies.get(name)]
    if missing:
        return (
            "invalid",
            "rdt credential 缺少 reddit_session，请在已连接插件的浏览器重新登录 Reddit。",
        )
    saved_at = _optional_float(data.get("saved_at"))
    if saved_at is not None and time.time() - saved_at > _RDT_CREDENTIAL_TTL_SECONDS:
        return "expired", "rdt credential 已过期，请等待插件重新同步或运行 `rdt login`。"
    return "present", "rdt credential 就绪。"


def _rdt_credential_file() -> Path:
    try:
        constants = importlib.import_module("rdt_cli.constants")
        return Path(cast("Any", constants).CREDENTIAL_FILE)
    except Exception:
        return Path.home() / ".config" / "rdt-cli" / "credential.json"


def _subprocess_run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    resolved_args = list(args)
    if resolved_args:
        resolved = _default_which(resolved_args[0])
        if resolved:
            resolved_args[0] = resolved
        elif resolved_args[0] == "rdt" and _rdt_cli_module_available():
            return _run_rdt_cli_in_process(resolved_args, timeout=timeout)
    return subprocess.run(
        resolved_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _run_rdt_cli_in_process(
    args: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run bundled rdt-cli without requiring a console-script executable.

    PyInstaller desktop builds can bundle the ``rdt_cli`` Python package but do
    not automatically expose the package's ``rdt`` console script on PATH. This
    fallback keeps the command backend usable in frozen installers while normal
    source / venv installs still prefer the real subprocess.
    """

    del timeout  # click commands are synchronous; caller-level timeouts apply to subprocess only.
    from click.testing import CliRunner

    try:
        rdt_cli = importlib.import_module("rdt_cli.cli")
        cli = cast("Any", rdt_cli).cli
    except Exception as exc:
        raise FileNotFoundError("rdt") from exc
    result = CliRunner().invoke(cli, args[1:])
    return subprocess.CompletedProcess(
        args=args,
        returncode=int(result.exit_code),
        stdout=str(result.output or ""),
        stderr=str(result.exception or "") if result.exception else "",
    )


def _rdt_cli_module_available() -> bool:
    return importlib.util.find_spec("rdt_cli.cli") is not None


def _default_which(name: str) -> str | None:
    """Find command on PATH or next to the active Python executable.

    Project installs put dependency console scripts such as ``rdt`` in the same
    virtualenv ``bin`` directory as ``openbiliclaw``. Users often invoke
    ``.venv/bin/openbiliclaw`` without activating the venv, so PATH alone is not
    enough.
    """

    found = shutil.which(name)
    if found:
        return found
    raw = str(name or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or path.parent != Path("."):
        return raw if path.exists() else None
    script_dirs = [
        Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin"),
        Path(sys.executable).parent,
        Path(sys.executable).resolve().parent,
    ]
    candidates = [script_dir / raw for script_dir in script_dirs]
    if os.name == "nt" and not Path(raw).suffix:
        pathext = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";")
        for script_dir in script_dirs:
            candidates.extend(script_dir / f"{raw}{ext.lower()}" for ext in pathext if ext)
            candidates.extend(script_dir / f"{raw}{ext.upper()}" for ext in pathext if ext)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


def _reddit_read_post_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("t3_"):
        return text.removeprefix("t3_")
    match = re.search(r"/comments/([^/?#]+)/?", text)
    if match:
        return match.group(1)
    match = re.search(r"redd\.it/([^/?#]+)/?", text)
    if match:
        return match.group(1)
    return text


def _content_id(item: dict[str, Any]) -> str:
    raw = _text(item, "content_id", "name", "fullname", "id", "post_id")
    if not raw:
        return ""
    if raw.startswith(("t1_", "t3_")):
        return raw
    kind = str(item.get("kind") or item.get("type") or "").strip().lower()
    prefix = "t1_" if kind in {"comment", "t1"} else "t3_"
    return f"{prefix}{raw}"


def _content_url(item: dict[str, Any]) -> str:
    url = _text(item, "url", "permalink", "link")
    if url.startswith("/"):
        return f"https://www.reddit.com{url}"
    if url:
        return url
    content_id = _content_id(item).removeprefix("t3_").removeprefix("t1_")
    return f"https://www.reddit.com/comments/{content_id}/" if content_id else ""


def _subreddit(item: dict[str, Any]) -> str:
    value = _text(item, "subreddit", "subreddit_name_prefixed")
    return value.removeprefix("r/")


def _author(item: dict[str, Any]) -> str:
    value = _text(item, "author", "username", "user")
    if not value or value in {"[deleted]", "deleted"}:
        return ""
    return value if value.startswith("u/") else f"u/{value}"


def _content_type(content_id: str, item: dict[str, Any]) -> str:
    explicit = str(item.get("content_type") or item.get("type") or "").strip().lower()
    if explicit in {"comment", "post"}:
        return explicit
    return "comment" if content_id.startswith("t1_") else "post"


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


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


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
