"""Crash-safe two-phase completion for extension source-task results."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.storage.database import Database

STAGED_TERMINAL_STATUS_FIELD = "_openbiliclaw_terminal_status"
_TASK_TABLES = frozenset(
    {
        "xhs_tasks",
        "dy_tasks",
        "yt_tasks",
        "zhihu_tasks",
        "reddit_tasks",
        "linuxdo_tasks",
        "v2ex_tasks",
        "weibo_tasks",
        "instagram_tasks",
    }
)


def parse_task_result(raw: object) -> dict[str, Any]:
    """Parse one canonical task result without trusting callback payload state."""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def staged_terminal_status(result: object) -> str:
    """Return the immutable staged terminal status, if one was published."""
    payload = parse_task_result(result)
    return str(payload.get(STAGED_TERMINAL_STATUS_FIELD) or "").strip()


def mutate_unstaged_result(
    database: Database,
    *,
    table: str,
    task_id: str,
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
    terminal_status: str | None = None,
    expected_claim_token: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Serialize one legacy mutation and reject it after a final is staged.

    A staged final is logically terminal even though its database ``status``
    remains nonterminal until downstream projections finish.  Every legacy
    partial/final/failure mutation must therefore inspect the marker while it
    holds the same SQLite write lock used for the update.
    """
    if table not in _TASK_TABLES:
        raise ValueError(f"unsupported source task table: {table}")
    normalized_terminal = str(terminal_status or "").strip()
    if normalized_terminal and normalized_terminal not in {"completed", "failed"}:
        raise ValueError(f"unsupported terminal task status: {normalized_terminal}")
    conn = database.open_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        claim_column = ", claim_token" if expected_claim_token is not None else ""
        row = conn.execute(
            f"SELECT status, result_json{claim_column} FROM {table} WHERE id = ?",  # noqa: S608
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        if expected_claim_token is not None and not secrets.compare_digest(
            str(row["claim_token"] or ""),
            str(expected_claim_token or ""),
        ):
            raise PermissionError("task_claim_conflict")
        current = parse_task_result(row["result_json"])
        if str(row["status"] or "").strip() in {"completed", "failed"} or staged_terminal_status(
            current
        ):
            conn.commit()
            return False, current
        canonical = mutate(current)
        if not isinstance(canonical, dict):
            raise TypeError("source task mutation must return an object")
        completed_at_sql = ", completed_at = CURRENT_TIMESTAMP" if normalized_terminal else ""
        status_sql = ", status = ?" if normalized_terminal else ""
        params: list[object] = [json.dumps(canonical, ensure_ascii=False)]
        if normalized_terminal:
            params.append(normalized_terminal)
        params.append(task_id)
        conn.execute(
            f"""
            UPDATE {table}
            SET result_json = ?{status_sql}{completed_at_sql}
            WHERE id = ?
            """,  # noqa: S608
            tuple(params),
        )
        conn.commit()
        return True, canonical
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def stage_terminal_result(
    database: Database,
    *,
    table: str,
    task_id: str,
    terminal_status: str,
    merge: Callable[[dict[str, Any]], dict[str, Any]],
    expected_claim_token: str | None = None,
) -> dict[str, Any]:
    """Publish a canonical final payload while leaving the task nonterminal.

    The first final callback wins. Once the stage marker exists, retries return
    the stored payload without merging any new callback fields. This makes the
    subsequent ingress/projection repair depend only on durable canonical data.
    """
    if table not in _TASK_TABLES:
        raise ValueError(f"unsupported source task table: {table}")
    normalized_status = str(terminal_status or "").strip()
    if not normalized_status:
        raise ValueError("terminal_status is required")
    conn = database.open_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        claim_column = ", claim_token" if expected_claim_token is not None else ""
        row = conn.execute(
            f"SELECT status, result_json{claim_column} FROM {table} WHERE id = ?",  # noqa: S608
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        if expected_claim_token is not None and not secrets.compare_digest(
            str(row["claim_token"] or ""),
            str(expected_claim_token or ""),
        ):
            raise PermissionError("task_claim_conflict")
        current = parse_task_result(row["result_json"])
        if str(row["status"] or "").strip() in {"completed", "failed"}:
            conn.commit()
            return current
        if staged_terminal_status(current):
            conn.commit()
            return current
        canonical = merge(current)
        if not isinstance(canonical, dict):
            raise TypeError("source task merge must return an object")
        canonical = dict(canonical)
        canonical[STAGED_TERMINAL_STATUS_FIELD] = normalized_status
        conn.execute(
            f"UPDATE {table} SET result_json = ? WHERE id = ?",  # noqa: S608
            (json.dumps(canonical, ensure_ascii=False), task_id),
        )
        conn.commit()
        return canonical
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def complete_staged_result(
    database: Database,
    *,
    table: str,
    task_id: str,
    expected_claim_token: str | None = None,
) -> bool:
    """Flip an already-staged task terminal without replacing result_json."""
    if table not in _TASK_TABLES:
        raise ValueError(f"unsupported source task table: {table}")
    conn = database.open_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        claim_column = ", claim_token" if expected_claim_token is not None else ""
        row = conn.execute(
            f"SELECT status, result_json{claim_column} FROM {table} WHERE id = ?",  # noqa: S608
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        if expected_claim_token is not None and not secrets.compare_digest(
            str(row["claim_token"] or ""),
            str(expected_claim_token or ""),
        ):
            raise PermissionError("task_claim_conflict")
        status = str(row["status"] or "").strip()
        if status == "completed":
            conn.commit()
            return False
        if status == "failed":
            raise RuntimeError("failed task cannot be completed")
        terminal_marker = staged_terminal_status(row["result_json"])
        if not terminal_marker:
            raise RuntimeError("task result is not staged for completion")
        target_status = "failed" if terminal_marker == "failed" else "completed"
        cursor = conn.execute(
            f"""
            UPDATE {table}
            SET status = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status NOT IN ('completed', 'failed')
            """,  # noqa: S608
            (target_status, task_id),
        )
        conn.commit()
        return int(cursor.rowcount or 0) == 1
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
