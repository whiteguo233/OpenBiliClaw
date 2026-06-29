"""Reddit source helpers.

Reddit does not currently have a reliable anonymous API path for this project.
The primary source contract therefore runs same-origin browser-extension tasks
inside the user's logged-in Reddit session. OpenCLI / rdt command helpers remain
available as explicit fallback backends.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

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
    which: Any = shutil.which,
    runner: CommandRunner | None = None,
    timeout: float = 10.0,
) -> RedditCommandStatus:
    """Probe Reddit command backend without starting browser automation."""

    which_fn = which or shutil.which
    normalized = str(backend or "auto").strip().lower()
    backends = ("opencli", "rdt") if normalized == "auto" else (normalized,)
    findings: list[RedditCommandStatus] = []
    for candidate in backends:
        if candidate == "opencli":
            status = _probe_opencli(which=which_fn, runner=runner, timeout=timeout)
        elif candidate == "rdt":
            status = _probe_rdt(which=which_fn, runner=runner, timeout=timeout)
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
            return ["rdt", "search", query, "-n", str(max_items)]
        if mode == "hot":
            target = subreddit or "all"
            return ["rdt", "subreddit", target, "--sort", "hot", "-n", str(max_items)]
        if mode == "subreddit":
            return ["rdt", "subreddit", subreddit or query, "-n", str(max_items)]
        if mode == "related":
            return ["rdt", "read", query]
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
        return [dict(item) for item in parsed if isinstance(item, dict)]
    if not isinstance(parsed, dict):
        return []
    for key in ("items", "results", "posts", "comments", "data"):
        value = parsed.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_item_dicts(value)
            if nested:
                return nested
    children = parsed.get("children")
    if isinstance(children, list):
        rows: list[dict[str, Any]] = []
        for child in children:
            if isinstance(child, dict) and isinstance(child.get("data"), dict):
                rows.append(dict(child["data"]))
        return rows
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
) -> RedditCommandStatus:
    if not which("rdt"):
        return RedditCommandStatus("rdt", "missing", "未安装 rdt。")
    try:
        completed = (runner or _subprocess_run)(["rdt", "status", "--json"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
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
    info = data.get("data") if isinstance(data, dict) else {}
    if isinstance(info, dict) and bool(info.get("authenticated")):
        username = str(info.get("username") or "").strip()
        suffix = f" ({username})" if username else ""
        return RedditCommandStatus("rdt", "ready", f"rdt 已登录{suffix}。")
    return RedditCommandStatus("rdt", "login_required", "rdt 已安装但未登录，请运行 `rdt login`。")


def _subprocess_run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


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
