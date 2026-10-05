"""Database-only enqueue helpers for browser-extension bootstrap tasks.

The CLI historically owned the bootstrap enqueue paths.  Keeping that
logic in this module lets runtime code enqueue an already-resolved database
without importing the Typer/Rich CLI surface.  The helpers deliberately stop
at the database boundary: dispatch kicks and user-facing rendering belong to
their caller.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, ParamSpec

INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE = 300
DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_LINUXDO_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_V2EX_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_WEIBO_BOOTSTRAP_DEDUPE_HOURS = 6.0
DEFAULT_INSTAGRAM_BOOTSTRAP_DEDUPE_HOURS = 6.0

_RECENT_TASK_STATUSES = ("pending", "in_progress", "completed", "failed")
Notify = Callable[[str], None]
_P = ParamSpec("_P")

# All in-process bootstrap producers (runtime, guided init, and CLI fetch)
# share this re-entrant fast-path lock. The SQLite admission transaction below
# is the cross-facade/process authority: it holds one write reservation across
# the cross-source active scan and selected insert. The lock additionally closes
# cancelled-worker/hot-reload thread overlap without coupling runtime to CLI.
SOURCE_BOOTSTRAP_DECISION_LOCK = threading.RLock()
_BOOTSTRAP_TASK_TABLES: tuple[tuple[str, str, str], ...] = (
    ("xhs", "xhs_tasks", "bootstrap_profile"),
    ("dy", "dy_tasks", "bootstrap_profile"),
    ("yt", "yt_tasks", "bootstrap_profile"),
    ("zhihu", "zhihu_tasks", "bootstrap_events"),
    ("reddit", "reddit_tasks", "bootstrap_events"),
    ("linuxdo", "linuxdo_tasks", "bootstrap_events"),
    ("v2ex", "v2ex_tasks", "bootstrap_profile"),
    ("weibo", "weibo_tasks", "bootstrap_events"),
    ("instagram", "instagram_tasks", "bootstrap_events"),
)


@contextmanager
def _bootstrap_admission_transaction(database: Any) -> Iterator[None]:
    """Serialize the cross-source active scan and insert in SQLite.

    The process-local decision lock keeps ordinary callers cheap, while
    ``BEGIN IMMEDIATE`` is the authority across separate ``Database`` facades
    and processes. Queue ``enqueue_with_id`` methods detect this surrounding
    transaction and deliberately leave its commit to this context manager.
    Narrow unit-test doubles without a SQLite connection keep the historical
    non-transactional seam.
    """

    conn = getattr(database, "conn", None)
    if not isinstance(conn, sqlite3.Connection):
        yield
        return

    execute = conn.execute
    nested = bool(conn.in_transaction)
    savepoint = "source_bootstrap_admission"
    if nested:
        execute(f"SAVEPOINT {savepoint}")
    else:
        execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            conn.rollback()
        raise
    else:
        if nested:
            execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            conn.commit()


@dataclass(frozen=True)
class BootstrapEnqueueResult:
    """Outcome of one bootstrap enqueue attempt.

    ``created`` is true only for a newly inserted row with a non-empty task
    id.  A task returned by the recent-task dedupe path has an id but
    ``created`` is false, which is important to periodic scheduling: reuse
    must not advance its attempt timestamp or cursor.
    """

    task_id: str | None
    created: bool
    reason: str


def _serialized_enqueue(
    function: Callable[_P, BootstrapEnqueueResult],
) -> Callable[_P, BootstrapEnqueueResult]:
    """Serialize every public bootstrap helper against scheduler decisions."""

    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> BootstrapEnqueueResult:
        with SOURCE_BOOTSTRAP_DECISION_LOCK:
            return function(*args, **kwargs)

    return wrapped


def _find_active_bootstrap_task(database: Any) -> tuple[str, str] | None:
    """Return one pending/in-progress account bootstrap across all task sources."""

    conn = getattr(database, "conn", None)
    execute = getattr(conn, "execute", None)
    if not callable(execute):
        # Narrow unit-test doubles do not expose SQLite. Production Database
        # always does; keep the helpers independently testable at this seam.
        return None
    for source, table, task_type in _BOOTSTRAP_TASK_TABLES:
        try:
            row = execute(
                f"SELECT id FROM {table} "
                "WHERE type = ? AND status IN ('pending', 'in_progress') "
                "ORDER BY created_at ASC LIMIT 1",
                (task_type,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                continue
            raise
        if row is None:
            continue
        try:
            task_id = str(row["id"] or "").strip()
        except (IndexError, KeyError, TypeError):
            task_id = str(row[0] or "").strip()
        if task_id:
            return source, task_id
    return None


def _active_bootstrap_result(
    database: Any,
    *,
    notify: Notify | None,
) -> BootstrapEnqueueResult | None:
    active = _find_active_bootstrap_task(database)
    if active is None:
        return None
    source, _task_id = active
    _notify(
        notify,
        f"  [dim]已有 {source} bootstrap 任务执行中；本次不创建并行账号任务。[/dim]",
    )
    # Do not return the other source's id: CLI wrappers would otherwise kick
    # the wrong dispatcher. The durable row is already recoverable by its own
    # polling/kick path.
    return BootstrapEnqueueResult(task_id=None, created=False, reason="active_task")


def cancel_incremental_bootstrap_tasks(
    database: Any,
    *,
    sources: set[str] | None = None,
    scheduler_task_ids: set[str] | None = None,
) -> tuple[str, ...]:
    """Fail queued periodic bootstrap work after its global opt-out.

    Only tasks marked as scheduler-owned (or recorded by the legacy scheduler
    state) are cancelled. Manual init/fetch and caller-owned incremental tasks
    are left untouched. Cancelling both pending and stale in-progress rows
    prevents an upgrade from reclaiming a pre-existing periodic task after the
    safe default has taken effect.
    """

    selected = frozenset(sources) if sources is not None else None
    recorded_ids = frozenset(scheduler_task_ids or ())
    cancelled: list[str] = []
    conn = getattr(database, "conn", None)
    if not isinstance(conn, sqlite3.Connection):
        return ()
    terminal = json.dumps(
        {
            "status": "failed",
            "error": "source_incremental_disabled",
            "cancelled": True,
        },
        ensure_ascii=False,
    )
    with SOURCE_BOOTSTRAP_DECISION_LOCK, _bootstrap_admission_transaction(database):
        for source, table, task_type in _BOOTSTRAP_TASK_TABLES:
            if selected is not None and source not in selected:
                continue
            try:
                rows = conn.execute(
                    f"SELECT id, payload_json FROM {table} "
                    "WHERE type = ? AND status IN ('pending', 'in_progress')",
                    (task_type,),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    continue
                raise
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"] or "{}"))
                    task_id = str(row["id"] or "").strip()
                except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not task_id or not isinstance(payload, dict):
                    continue
                scheduler_owned = payload.get("incremental_owner") == "scheduler"
                if payload.get("incremental") is not True or not (
                    scheduler_owned or task_id in recorded_ids
                ):
                    continue
                cursor = conn.execute(
                    f"UPDATE {table} "
                    "SET status = 'failed', result_json = ?, "
                    "completed_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status IN ('pending', 'in_progress')",
                    (terminal, task_id),
                )
                if cursor.rowcount:
                    cancelled.append(task_id)
    return tuple(cancelled)


def _notify(notify: Notify | None, message: str) -> None:
    if notify is not None:
        notify(message)


def _dedupe_hours(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, str(default))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _recent_reuse_result(
    recent: dict[str, Any], *, message: str, notify: Notify | None
) -> BootstrapEnqueueResult | None:
    task_id = str(recent.get("id", "")).strip()
    if not task_id:
        return None
    _notify(notify, message.format(status=str(recent.get("status", "unknown"))))
    return BootstrapEnqueueResult(task_id=task_id, created=False, reason="reused_recent")


def _linuxdo_reusable_recent_task(
    queue: Any,
    *,
    recent_hours: float,
) -> dict[str, Any] | None:
    """Return only active or positively completed Linux.do bootstrap work.

    A failed/degraded terminal must never lock a repaired login or transient
    network recovery behind the six-hour dedupe window.  Compare the latest
    success and failure explicitly so an older success cannot mask a newer
    failed attempt.
    """
    find_recent = getattr(queue, "find_recent_task", None)
    if not callable(find_recent):
        return None
    active = find_recent(
        "bootstrap_events",
        recent_hours=recent_hours,
        statuses=("pending", "in_progress"),
    )
    if isinstance(active, dict):
        return active

    terminals = [
        row
        for row in (
            find_recent("bootstrap_events", recent_hours=recent_hours, statuses=("completed",)),
            find_recent("bootstrap_events", recent_hours=recent_hours, statuses=("failed",)),
        )
        if isinstance(row, dict)
    ]
    if not terminals:
        return None
    latest = max(
        terminals,
        # SQLite timestamps are second-granularity here.  When a success and
        # failure share that second, prefer retrying over letting the success
        # mask potentially newer negative evidence.
        key=lambda row: (
            str(row.get("created_at", "")),
            str(row.get("status", "")) == "failed",
            str(row.get("id", "")),
        ),
    )
    if str(latest.get("status", "")) != "completed":
        return None
    try:
        result = json.loads(str(latest.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return None
    terminal_status = (
        str(result.get("_openbiliclaw_terminal_status", "")).strip()
        if isinstance(result, dict)
        else ""
    )
    return latest if terminal_status in {"ok", "empty"} else None


def _instagram_reusable_recent_task(
    queue: Any,
    *,
    recent_hours: float,
    expected_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return active work or the latest positively completed Instagram init.

    Failed and partial browser runs are retry evidence, not successful
    bootstrap snapshots.  Comparing the newest completed and failed attempts
    also prevents an older success from masking a later authentication or
    transport failure inside the dedupe window.
    """

    if not isinstance(expected_payload, dict):
        return None
    expected = expected_payload

    def same_contract(row: dict[str, Any]) -> bool:
        try:
            candidate = json.loads(str(row.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            return False
        if not isinstance(candidate, dict):
            return False
        fields = (
            "scopes",
            "max_items_per_scope",
            "max_pages_per_scope",
            "request_interval_ms",
            "profile_update",
            "smoke_only",
            "profile_rebuild",
            "purpose",
            "expected_account_key",
        )
        return all(candidate.get(field) == expected.get(field) for field in fields)

    active = queue.find_recent_task(
        "bootstrap_events",
        recent_hours=recent_hours,
        statuses=("pending", "in_progress"),
    )
    if isinstance(active, dict) and same_contract(active):
        return active

    terminals = [
        row
        for row in (
            queue.find_recent_task(
                "bootstrap_events",
                recent_hours=recent_hours,
                statuses=("completed",),
            ),
            queue.find_recent_task(
                "bootstrap_events",
                recent_hours=recent_hours,
                statuses=("failed",),
            ),
        )
        if isinstance(row, dict)
    ]
    if not terminals:
        return None
    latest = max(
        terminals,
        # Failed evidence wins a same-second tie: retrying is safer than
        # treating an ambiguously ordered success as the latest attempt.
        key=lambda row: (
            str(row.get("created_at", "")),
            str(row.get("status", "")) == "failed",
            str(row.get("id", "")),
        ),
    )
    if str(latest.get("status", "")) != "completed":
        return None

    from openbiliclaw.sources.task_result_protocol import staged_terminal_status

    terminal = staged_terminal_status(latest.get("result_json"))
    if terminal not in {"ok", "empty"} or not same_contract(latest):
        return None
    try:
        canonical = json.loads(str(latest.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return None
    expected_account_key = str(expected.get("expected_account_key") or "").strip()
    if not expected_account_key or not isinstance(canonical, dict):
        return None
    return latest if canonical.get("account_key") == expected_account_key else None


def _created_or_budget_result(
    task_id: object,
    *,
    budget_message: str,
    notify: Notify | None,
) -> BootstrapEnqueueResult:
    normalized = str(task_id).strip() if task_id is not None else ""
    if not normalized:
        _notify(notify, budget_message)
        return BootstrapEnqueueResult(
            task_id=None,
            created=False,
            reason="enqueue_failed",
        )
    return BootstrapEnqueueResult(task_id=normalized, created=True, reason="created")


def _incremental_payload(
    payload: dict[str, Any],
    incremental: bool,
    incremental_owner: str = "",
) -> dict[str, Any]:
    if incremental:
        payload["incremental"] = True
        owner = str(incremental_owner or "").strip().lower()
        if owner:
            payload["incremental_owner"] = owner
    return payload


def seed_guided_init_attempts(
    memory_manager: Any,
    statuses: dict[str, object],
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Atomically seed scheduler timestamps for successful init collectors.

    XHS, Douyin, YouTube, and Zhihu have ambiguous ``empty`` results: the
    extension can complete the task while the site is logged out or blocked,
    so only ``ok`` is evidence that a usable bootstrap pull completed. Reddit
    and Linux.do are deliberately different: their bootstrap tasks first
    positively resolve the current account and map an unauthenticated response
    to ``login_required``; therefore ``empty`` is a genuine successful-empty
    pull for those sources.
    """
    eligible: list[str] = []
    for source, status in (
        ("xhs", statuses.get("xhs")),
        ("dy", statuses.get("dy")),
        ("yt", statuses.get("yt")),
        ("zhihu", statuses.get("zhihu")),
    ):
        if str(status or "").strip().lower() == "ok":
            eligible.append(source)
    for source in ("reddit", "linuxdo"):
        if str(statuses.get(source) or "").strip().lower() in {"ok", "empty"}:
            # These ``empty`` results are evidence-backed, unlike the four
            # browser pages above; keep this distinction explicit so future
            # status refactors do not turn a logged-out result into a successful
            # schedule stamp.
            eligible.append(source)
    if str(statuses.get("v2ex") or "").strip().lower() in {"ok", "empty"}:
        # V2EX's extension contract proves ``empty`` with a recognized scope
        # layout and an affirmative login state. ``partial`` deliberately stays
        # retryable because at least one requested scope was not authoritative.
        eligible.append("v2ex")
    if not eligible:
        return ()

    current = now or datetime.now(UTC)
    timestamp = (
        current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    ).isoformat()
    update_state = getattr(memory_manager, "update_source_bootstrap_state", None)
    if not callable(update_state):
        raise RuntimeError("memory manager lacks atomic source bootstrap state updates")

    def _mutate(state: dict[str, object]) -> dict[str, object]:
        raw_incremental = state.get("source_incremental")
        incremental = dict(raw_incremental) if isinstance(raw_incremental, dict) else {}
        raw_attempts = incremental.get("last_attempt_at")
        attempts = dict(raw_attempts) if isinstance(raw_attempts, dict) else {}
        raw_successes = incremental.get("last_success_at")
        successes = dict(raw_successes) if isinstance(raw_successes, dict) else {}
        for source in eligible:
            attempts[source] = timestamp
            successes[source] = timestamp
        incremental["last_attempt_at"] = attempts
        incremental["last_success_at"] = successes
        state["source_incremental"] = incremental
        return state

    update_state(_mutate)
    return tuple(eligible)


@_serialized_enqueue
def enqueue_xhs_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the XHS ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.xhs_tasks import XhsTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_XHS_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_XHS_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = XhsTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_XHS_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_profile",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的小红书 bootstrap 任务"
                            "({status})；需要重新拉取可用 "
                            "`openbiliclaw fetch-xhs --force`。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": ["saved", "liked", "xhs_history"],
                    "max_items_per_scope": max(1, max_items),
                    "max_scroll_rounds": max(0, scroll_rounds),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]小红书初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]小红书初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_dy_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Douyin ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_DY_BOOTSTRAP_SCROLL_ROUNDS", "15"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_DY_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = DyTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_profile",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    raw_result = recent.get("result_json")
                    if isinstance(raw_result, dict):
                        parsed_result = raw_result
                    elif isinstance(raw_result, (str, bytes, bytearray)):
                        try:
                            parsed_result = json.loads(raw_result)
                        except (TypeError, ValueError):
                            parsed_result = None
                    else:
                        parsed_result = None
                    recent_is_degraded = (
                        isinstance(parsed_result, dict)
                        and str(parsed_result.get("status", "")).strip().lower() == "degraded"
                    )
                    if recent_is_degraded:
                        _notify(
                            notify,
                            "  [dim]最近的抖音 bootstrap 任务仅部分完成；"
                            "本次重新入队以补齐分页。[/dim]",
                        )
                    else:
                        reused = _recent_reuse_result(
                            recent,
                            message=(
                                "  [dim]复用最近的抖音 bootstrap 任务"
                                "({status})；需要重新拉取可设 "
                                "OPENBILICLAW_DY_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                            ),
                            notify=notify,
                        )
                        if reused is not None:
                            return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": ["dy_post", "dy_collect", "dy_like", "dy_follow"],
                    "max_items_per_scope": max(1, max_items),
                    "max_scroll_rounds": max(0, scroll_rounds),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]抖音初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]抖音初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_yt_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the YouTube ``bootstrap_profile`` task without dispatching it."""
    from openbiliclaw.sources.yt_tasks import YtTaskQueue

    scroll_rounds = int(os.environ.get("OPENBILICLAW_YT_BOOTSTRAP_SCROLL_ROUNDS", "10"))
    max_items = int(
        os.environ.get(
            "OPENBILICLAW_YT_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = YtTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            # Issue #178: a Chrome MV3 service worker can lose its setTimeout
            # timeout while asleep, leaving a claimed YouTube task stuck in
            # in_progress forever. Fail that stale lease before admission so it
            # neither blocks the active-task scan nor gets reused by dedupe.
            expire_stale = getattr(queue, "expire_stale_in_progress", None)
            stale_recovered = (
                int(expire_stale(("bootstrap_profile",)) or 0) if callable(expire_stale) else 0
            )
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent) and not stale_recovered:
                recent = find_recent(
                    "bootstrap_profile",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的 YouTube bootstrap 任务"
                            "({status})；需要重新拉取可设 "
                            "OPENBILICLAW_YT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": ["yt_history", "yt_subscriptions", "yt_likes"],
                    "max_items_per_scope": max(1, max_items),
                    "max_scroll_rounds": max(0, scroll_rounds),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]YouTube 初始化信号未导入: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]YouTube 初始化信号未导入: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_zhihu_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_slug: str = "",
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Zhihu ``bootstrap_events`` task without dispatching it."""
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    max_collections = int(os.environ.get("OPENBILICLAW_ZHIHU_BOOTSTRAP_MAX_COLLECTIONS", "20"))

    try:
        queue = ZhihuTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_events",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的知乎 bootstrap 任务"
                            "({status})；需要重新拉取可设 "
                            "OPENBILICLAW_ZHIHU_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            scopes = ["zhihu_read_history", "zhihu_collection", "zhihu_activity"]
            if not profile_slug.strip():
                _notify(
                    notify,
                    "  [dim]未传 --profile-slug，扩展会尝试从知乎登录态识别当前用户；"
                    "识别失败时只返回浏览记录和收藏夹。[/dim]",
                )
            payload = _incremental_payload(
                {
                    "scopes": scopes,
                    "profile_slug": profile_slug.strip(),
                    "max_items_per_scope": max(1, max_items),
                    "max_collections": max(1, max_collections),
                    "profile_update": bool(profile_update),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]知乎事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]知乎事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_reddit_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Reddit ``bootstrap_events`` task without dispatching it."""
    from openbiliclaw.sources.reddit_tasks import RedditTaskQueue

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_REDDIT_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )

    try:
        queue = RedditTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_REDDIT_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_events",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的 Reddit bootstrap 任务"
                            "({status})；需要重新拉取可设 "
                            "OPENBILICLAW_REDDIT_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
                    "max_items_per_scope": max(1, max_items),
                    "profile_update": bool(profile_update),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]Reddit 初始化事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]Reddit 初始化事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_weibo_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue a read-only, logged-in Weibo bootstrap task."""
    from openbiliclaw.sources.weibo_tasks import WeiboTaskQueue

    max_items = int(
        os.environ.get(
            "OPENBILICLAW_WEIBO_BOOTSTRAP_MAX_ITEMS",
            str(INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE),
        )
    )
    try:
        queue = WeiboTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_WEIBO_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_WEIBO_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_events",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的微博 bootstrap 任务"
                            "；需要重新拉取可设 "
                            "OPENBILICLAW_WEIBO_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": [
                        "weibo_favorites",
                        "weibo_following",
                        "weibo_mentions",
                    ],
                    "max_items_per_scope": max(1, max_items),
                    "profile_update": bool(profile_update),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]微博个人事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]微博个人事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_linuxdo_bootstrap(
    database: Any,
    *,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_update: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue the Linux.do ``bootstrap_events`` task without dispatching it."""
    from openbiliclaw.sources.linuxdo_tasks import LinuxdoTaskQueue

    configured_max_items = INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE
    configured_request_interval = 3.0
    try:
        from openbiliclaw.config import load_config

        linuxdo_cfg = getattr(getattr(load_config(), "sources", None), "linuxdo", None)
        configured_max_items = int(getattr(linuxdo_cfg, "bootstrap_limit", configured_max_items))
        configured_request_interval = float(
            getattr(linuxdo_cfg, "request_interval_seconds", configured_request_interval)
        )
    except Exception:
        pass
    max_items = min(
        300,
        max(
            1,
            int(
                os.environ.get(
                    "OPENBILICLAW_LINUXDO_BOOTSTRAP_MAX_ITEMS",
                    str(configured_max_items),
                )
            ),
        ),
    )
    request_interval_seconds = min(
        30.0,
        max(
            0.0,
            float(
                os.environ.get(
                    "OPENBILICLAW_LINUXDO_REQUEST_INTERVAL_SECONDS",
                    str(configured_request_interval),
                )
            ),
        ),
    )

    try:
        queue = LinuxdoTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_LINUXDO_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_LINUXDO_BOOTSTRAP_DEDUPE_HOURS,
            )
            if not force and dedupe_hours > 0:
                recent = _linuxdo_reusable_recent_task(
                    queue,
                    recent_hours=dedupe_hours,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的 Linux.do bootstrap 任务"
                            "({status})；需要重新拉取可设 "
                            "OPENBILICLAW_LINUXDO_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": [
                        "linuxdo_bookmarks",
                        "linuxdo_likes",
                        "linuxdo_read_history",
                    ],
                    "max_items_per_scope": max(1, max_items),
                    "request_interval_seconds": request_interval_seconds,
                    "profile_update": bool(profile_update),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_events", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]Linux.do 初始化事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]Linux.do 初始化事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_v2ex_bootstrap(
    database: Any,
    *,
    username: str = "",
    config: Any | None = None,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_update: bool = False,
    smoke_only: bool = False,
    profile_rebuild: bool = False,
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue a V2EX browser bootstrap task without reading browser secrets."""

    from openbiliclaw.sources.v2ex_tasks import V2EX_BOOTSTRAP_SCOPES, V2EXTaskQueue

    def configured_limit(name: str, default: int, maximum: int) -> int:
        try:
            value = int(getattr(config, name, default))
        except (TypeError, ValueError):
            value = default
        return min(maximum, max(1, value))

    max_topics = configured_limit("bootstrap_topics_limit", 100, 1000)
    max_replies = configured_limit("bootstrap_replies_limit", 300, 5000)
    max_favorites = configured_limit("bootstrap_favorites_limit", 300, 5000)
    max_pages = configured_limit("bootstrap_max_pages_per_scope", 20, 100)
    normalized_username = str(username or "").strip()
    try:
        queue = V2EXTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_V2EX_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_V2EX_BOOTSTRAP_DEDUPE_HOURS,
            )
            find_recent = getattr(queue, "find_recent_task", None)
            if not force and dedupe_hours > 0 and callable(find_recent):
                recent = find_recent(
                    "bootstrap_profile",
                    recent_hours=dedupe_hours,
                    statuses=_RECENT_TASK_STATUSES,
                )
                if recent is not None:
                    recent_payload: dict[str, Any] = {}
                    try:
                        parsed_recent_payload = json.loads(str(recent.get("payload_json") or "{}"))
                        if isinstance(parsed_recent_payload, dict):
                            recent_payload = parsed_recent_payload
                    except (TypeError, ValueError, json.JSONDecodeError):
                        recent_payload = {}
                    expected_contract = {
                        "username": normalized_username.casefold(),
                        "max_topics": max_topics,
                        "max_replies": max_replies,
                        "max_favorite_topics": max_favorites,
                        "max_pages_per_scope": max_pages,
                        "incremental": bool(incremental),
                        "profile_update": bool(profile_update),
                        "smoke_only": bool(smoke_only),
                        "profile_rebuild": bool(profile_rebuild),
                    }
                    recent_contract = {
                        "username": str(recent_payload.get("username") or "").strip().casefold(),
                        "max_topics": recent_payload.get("max_topics"),
                        "max_replies": recent_payload.get("max_replies"),
                        "max_favorite_topics": recent_payload.get("max_favorite_topics"),
                        "max_pages_per_scope": recent_payload.get("max_pages_per_scope"),
                        "incremental": bool(recent_payload.get("incremental")),
                        "profile_update": bool(recent_payload.get("profile_update")),
                        "smoke_only": bool(recent_payload.get("smoke_only")),
                        "profile_rebuild": bool(recent_payload.get("profile_rebuild")),
                    }
                    if recent_contract != expected_contract:
                        recent = None
                    elif recent is not None:
                        from openbiliclaw.sources.task_result_protocol import (
                            staged_terminal_status,
                        )

                        terminal_status = staged_terminal_status(recent.get("result_json"))
                        if terminal_status and terminal_status not in {"ok", "empty"}:
                            # A partial/degraded snapshot is evidence that at
                            # least one scope was not authoritative. It remains
                            # retryable and must never satisfy a later full init.
                            recent = None
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的 V2EX bootstrap 任务"
                            "({status})；需要重新拉取可设置 "
                            "OPENBILICLAW_V2EX_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active

            payload = _incremental_payload(
                {
                    "scopes": list(V2EX_BOOTSTRAP_SCOPES),
                    "username": normalized_username,
                    "max_topics": max_topics,
                    "max_replies": max_replies,
                    "max_favorite_topics": max_favorites,
                    "max_pages_per_scope": max_pages,
                    "profile_update": bool(profile_update),
                    "smoke_only": bool(smoke_only),
                    "profile_rebuild": bool(profile_rebuild),
                },
                incremental,
                incremental_owner,
            )
            task_id = queue.enqueue_with_id("bootstrap_profile", payload, daily_budget=10)
    except Exception as exc:
        _notify(notify, f"  [yellow]V2EX 初始化事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message="  [yellow]V2EX 初始化事件未拉取: 今日任务预算已用完。[/yellow]",
        notify=notify,
    )


@_serialized_enqueue
def enqueue_instagram_bootstrap(
    database: Any,
    *,
    config: Any | None = None,
    force: bool = False,
    incremental: bool = False,
    incremental_owner: str = "",
    profile_update: bool = False,
    smoke_only: bool = False,
    profile_rebuild: bool = False,
    account_key: str = "",
    purpose: str = "",
    notify: Notify | None = None,
) -> BootstrapEnqueueResult:
    """Enqueue a fixed-scope Instagram init task without browser secrets."""

    from openbiliclaw.sources.instagram_tasks import (
        INSTAGRAM_BOOTSTRAP_PURPOSES,
        INSTAGRAM_BOOTSTRAP_SCOPES,
        InstagramTaskQueue,
        is_instagram_account_key,
    )

    del incremental_owner
    if incremental:
        _notify(
            notify,
            "  [yellow]Instagram 当前仅支持初始化拉取，不支持周期增量。[/yellow]",
        )
        return BootstrapEnqueueResult(task_id=None, created=False, reason="unsupported_incremental")

    def configured_limit(name: str, default: int, maximum: int) -> int:
        try:
            value = int(getattr(config, name, default))
        except (TypeError, ValueError):
            value = default
        return min(maximum, max(1, value))

    max_items = configured_limit("bootstrap_limit", 300, 300)
    max_pages = 20
    request_interval_ms = configured_limit("request_interval_seconds", 3, 30) * 1000
    normalized_account_key = (
        str(account_key or "").strip() if is_instagram_account_key(account_key) else ""
    )
    normalized_purpose = str(purpose or "").strip().casefold()
    if normalized_purpose not in INSTAGRAM_BOOTSTRAP_PURPOSES:
        normalized_purpose = (
            "smoke"
            if smoke_only
            else "profile-rebuild"
            if profile_rebuild
            else "guided-init"
            if profile_update
            else "fetch"
        )
    payload = {
        "scopes": list(INSTAGRAM_BOOTSTRAP_SCOPES),
        "max_items_per_scope": max_items,
        "max_pages_per_scope": max_pages,
        "request_interval_ms": request_interval_ms,
        "profile_update": bool(profile_update),
        "smoke_only": bool(smoke_only),
        "profile_rebuild": bool(profile_rebuild),
        "purpose": normalized_purpose,
        "expected_account_key": normalized_account_key,
    }
    try:
        queue = InstagramTaskQueue(database)
        with _bootstrap_admission_transaction(database):
            dedupe_hours = _dedupe_hours(
                "OPENBILICLAW_INSTAGRAM_BOOTSTRAP_DEDUPE_HOURS",
                DEFAULT_INSTAGRAM_BOOTSTRAP_DEDUPE_HOURS,
            )
            if not force and dedupe_hours > 0:
                recent = _instagram_reusable_recent_task(
                    queue,
                    recent_hours=dedupe_hours,
                    expected_payload=payload,
                )
                if recent is not None:
                    reused = _recent_reuse_result(
                        recent,
                        message=(
                            "  [dim]复用最近的 Instagram bootstrap 任务"
                            "({status})；需要重新拉取可设置 "
                            "OPENBILICLAW_INSTAGRAM_BOOTSTRAP_DEDUPE_HOURS=0。[/dim]"
                        ),
                        notify=notify,
                    )
                    if reused is not None:
                        return reused

            active = _active_bootstrap_result(database, notify=notify)
            if active is not None:
                return active
            task_id = queue.enqueue_with_id(
                "bootstrap_events",
                payload,
                daily_budget=10,
            )
    except Exception as exc:
        _notify(notify, f"  [yellow]Instagram 初始化事件未拉取: {exc}[/yellow]")
        return BootstrapEnqueueResult(task_id=None, created=False, reason="enqueue_error")

    return _created_or_budget_result(
        task_id,
        budget_message=("  [yellow]Instagram 初始化事件未拉取: 今日任务预算已用完。[/yellow]"),
        notify=notify,
    )


__all__ = [
    "BootstrapEnqueueResult",
    "SOURCE_BOOTSTRAP_DECISION_LOCK",
    "enqueue_dy_bootstrap",
    "enqueue_linuxdo_bootstrap",
    "enqueue_instagram_bootstrap",
    "enqueue_reddit_bootstrap",
    "enqueue_v2ex_bootstrap",
    "enqueue_xhs_bootstrap",
    "enqueue_yt_bootstrap",
    "enqueue_zhihu_bootstrap",
    "seed_guided_init_attempts",
]
