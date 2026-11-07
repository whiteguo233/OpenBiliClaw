"""Durable discovery alert transitions shared by workers, API and event stream."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.source_attention import HEALTHY_STATUSES, workflow_issue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
SCHEMA = """
    CREATE TABLE IF NOT EXISTS custom_source_alert_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        cause TEXT NOT NULL UNIQUE, payload TEXT NOT NULL
    );
"""
HISTORY_LIMIT = 500


def record_transition(
    conn: Any, before: dict[str, Any], after: dict[str, Any], *, cause: str, now: float
) -> None:
    """Append only issue-open/recovery transitions inside the fenced transaction."""
    previous, current = workflow_issue(before), workflow_issue(after)
    if current is not None:
        if previous is not None and (
            previous["action"] == current["action"]
            and (current["action"] != "source_login" or previous["code"] == current["code"])
        ):
            return
        code, message, severity = current["code"], current["message"], "warning"
    elif previous is not None and after["status"] in HEALTHY_STATUSES:
        code, message, severity = (
            "source_recovered",
            "该采集任务已成功试采或完成采集，相关异常已恢复。",
            "info",
        )
    else:
        return
    source_id = str(after["source_id"])
    row = conn.execute("SELECT name FROM custom_sources WHERE source_id=?", (source_id,)).fetchone()
    name = str(row[0] if row else "自定义来源")[:200]
    payload = {
        "category": "discovery",
        "code": code,
        "severity": severity,
        "source": source_id,
        "source_id": source_id,
        "source_name": name,
        "workflow_id": after["workflow_id"],
        "message": f"{name}：{message}",
        "first_seen": now,
        "last_seen": now,
        "count": 1,
        "failure_count": int(after.get("consecutive_failures", 0)),
    }
    conn.execute(
        "INSERT OR IGNORE INTO custom_source_alert_events(cause,payload) VALUES(?,?)",
        (cause, json.dumps(payload, ensure_ascii=False)),
    )
    conn.execute(
        "DELETE FROM custom_source_alert_events WHERE event_id <= "
        "(SELECT event_id FROM custom_source_alert_events ORDER BY event_id DESC LIMIT 1 OFFSET ?)",
        (HISTORY_LIMIT,),
    )


def alert_events(
    database: Any, *, after_id: int = 0, limit: int = HISTORY_LIMIT
) -> list[dict[str, Any]]:
    """Read safe metadata without constructing an executor or starting a browser."""
    if database is None:
        return []
    if not database.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='custom_source_alert_events'"
    ).fetchone():
        return []
    rows = database.conn.execute(
        "SELECT event_id,payload FROM custom_source_alert_events WHERE event_id>? "
        "ORDER BY event_id DESC LIMIT ?",
        (max(0, after_id), min(HISTORY_LIMIT, max(1, limit))),
    ).fetchall()
    return [dict(json.loads(row[1]), id=f"source:{row[0]}", source_event_id=row[0]) for row in rows]


def diagnostics_snapshot(
    database: Any, memory: dict[str, Any], *, source_since_id: int = 0, limit: int = 100
) -> dict[str, Any]:
    """Merge persistent source history into the existing diagnostics feed."""
    durable = alert_events(database, after_id=source_since_id)
    rows = list(memory["alerts"]) + durable
    rows.sort(key=lambda row: row["last_seen"], reverse=True)
    return {
        "alerts": rows[: max(1, min(500, limit))],
        "summary": {
            "total": len(rows),
            "errors": sum(row["severity"] == "error" for row in rows),
            "warnings": sum(row["severity"] == "warning" for row in rows),
            "recovered": sum(row["severity"] == "info" for row in rows),
        },
        "source_cursor": max((row["source_event_id"] for row in durable), default=source_since_id),
        "generated_at": time.time(),
    }


async def relay_alerts(
    database: Callable[[], Any],
    publisher: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    after_id: int,
    interval: float = 2,
) -> None:
    """One API-owned reader relays committed worker events to runtime-stream."""
    while True:
        try:
            rows = await asyncio.to_thread(alert_events, database(), after_id=after_id)
            for row in reversed(rows):
                await publisher({"type": "diagnostics.alert", **row})
                after_id = row["source_event_id"]
        except Exception:
            logger.warning("Discovery alert relay could not read committed events", exc_info=True)
        await asyncio.sleep(interval)
