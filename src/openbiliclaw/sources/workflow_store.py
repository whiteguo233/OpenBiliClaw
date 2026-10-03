"""Durable drafts, immutable workflow versions and fenced discovery runs.

Only a successfully trialled draft may become an active version. Browser
credentials live in private executor profiles; this ledger stores opaque IDs.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping


class WorkflowStore:
    """Persist workflow lifecycle in the application's existing SQLite database."""

    def __init__(self, database: Any) -> None:
        self.database = database
        database.conn.executescript("""
            CREATE TABLE IF NOT EXISTS custom_workflow_drafts (
                draft_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_workflows (
                workflow_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                active_version INTEGER NOT NULL, intent TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS custom_workflow_versions (
                workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(workflow_id, version)
            );
            CREATE TABLE IF NOT EXISTS custom_workflow_state (
                workflow_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'ready',
                next_run_at REAL NOT NULL DEFAULT 0,
                lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', last_success_at REAL NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                cursor_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS custom_workflow_runs (
                run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, version INTEGER NOT NULL,
                lease_token TEXT NOT NULL, executor TEXT NOT NULL, parameters TEXT NOT NULL,
                status TEXT NOT NULL, started_at REAL NOT NULL, completed_at REAL,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                enqueued_count INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS custom_runs_workflow_time
                ON custom_workflow_runs(workflow_id, started_at);
            CREATE TABLE IF NOT EXISTS custom_workflow_seen (
                workflow_id TEXT NOT NULL, item_key TEXT NOT NULL, seen_at REAL NOT NULL,
                PRIMARY KEY(workflow_id, item_key)
            );
        """)

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self.database.conn
        nested = conn.in_transaction
        savepoint = "workflow_" + uuid.uuid4().hex
        conn.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        try:
            yield conn
            if nested:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                conn.commit()
        except BaseException:
            if nested and conn.in_transaction:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                conn.rollback()
            raise

    def save_draft(self, payload: Mapping[str, Any], *, draft_id: str = "") -> dict[str, Any]:
        """Save bounded authoring progress; drafts never reserve pool capacity."""
        value = dict(payload)
        value["draft_id"] = draft_id or str(uuid.uuid4())
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > 200_000:
            raise ValueError("workflow draft exceeds storage limit")
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO custom_workflow_drafts VALUES(?,?,?,?) "
                "ON CONFLICT(draft_id) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (value["draft_id"], encoded, now, now),
            )
        return value

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        row = self.database.conn.execute(
            "SELECT payload FROM custom_workflow_drafts WHERE draft_id=?", (draft_id,)
        ).fetchone()
        if not row:
            raise ValueError("source draft not found")
        return dict(json.loads(row[0]))

    def publish(self, draft_id: str, source_id: str) -> dict[str, Any]:
        """Activate a trialled immutable version, idempotently for one draft."""
        with self.transaction() as conn:
            draft = self.get_draft(draft_id)
            if draft.get("published_workflow_id"):
                return self.get_workflow(str(draft["published_workflow_id"]))
            if draft.get("status") != "ready" or not draft.get("trial_success"):
                raise ValueError("a successful trial is required before activation")
            workflow_id = str(draft.get("workflow_id") or uuid.uuid4())
            current = conn.execute(
                "SELECT source_id, active_version FROM custom_workflows WHERE workflow_id=?",
                (workflow_id,),
            ).fetchone()
            if current and current[0] != source_id:
                raise ValueError("workflow source identity cannot change")
            if current and int(draft.get("base_version", 0)) != current[1]:
                raise ValueError("workflow changed while the draft was being trialled")
            version = int(current[1]) + 1 if current else 1
            now = time.time()
            conn.execute(
                "INSERT INTO custom_workflow_versions VALUES(?,?,?,?)",
                (workflow_id, version, json.dumps(draft, ensure_ascii=False), now),
            )
            conn.execute(
                "INSERT INTO custom_workflows VALUES(?,?,?,?,?) ON CONFLICT(workflow_id) "
                "DO UPDATE SET active_version=excluded.active_version, "
                "intent=excluded.intent, updated_at=excluded.updated_at",
                (workflow_id, source_id, version, draft["intent"], now),
            )
            conn.execute(
                "UPDATE custom_workflow_runs SET status='superseded',completed_at=?, "
                "error='workflow version replaced' WHERE workflow_id=? "
                "AND version<? AND status='running'",
                (now, workflow_id, version),
            )
            conn.execute(
                "INSERT INTO custom_workflow_state(workflow_id) VALUES(?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET status='ready',next_run_at=0, "
                "lease_token='',lease_until=0,last_error='',consecutive_failures=0,cursor_json='{}'",
                (workflow_id,),
            )
            draft.update(published_workflow_id=workflow_id, source_id=source_id)
            conn.execute(
                "UPDATE custom_workflow_drafts SET payload=?,updated_at=? WHERE draft_id=?",
                (json.dumps(draft, ensure_ascii=False), now, draft_id),
            )
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        row = self.database.conn.execute(
            "SELECT w.*, v.payload, s.status,s.next_run_at,s.last_error,s.last_success_at, "
            "s.consecutive_failures,s.cursor_json FROM custom_workflows w "
            "JOIN custom_workflow_versions v ON w.workflow_id=v.workflow_id "
            "AND w.active_version=v.version JOIN custom_workflow_state s "
            "ON s.workflow_id=w.workflow_id WHERE w.workflow_id=?",
            (workflow_id,),
        ).fetchone()
        if not row:
            raise ValueError("workflow not found")
        value = dict(json.loads(row["payload"]))
        value.update({key: row[key] for key in dict(row) if key != "payload"})
        value["version"] = value["active_version"]
        value["cursor"] = json.loads(value.pop("cursor_json"))
        return value

    def list_workflows(self, source_id: str) -> list[dict[str, Any]]:
        rows = self.database.conn.execute(
            "SELECT workflow_id FROM custom_workflows WHERE source_id=? ORDER BY updated_at",
            (source_id,),
        ).fetchall()
        return [self.get_workflow(str(row[0])) for row in rows]

    def claim(
        self,
        workflow_id: str,
        parameters: Mapping[str, str],
        *,
        force: bool = False,
        lease_seconds: int = 180,
    ) -> dict[str, Any] | None:
        """Atomically freeze the version and parameters; recover abandoned runs."""
        with self.transaction() as conn:
            now = time.time()
            workflow = self.get_workflow(workflow_id)
            source = conn.execute(
                "SELECT enabled FROM custom_sources WHERE source_id=?", (workflow["source_id"],)
            ).fetchone()
            if not source or not source[0] or workflow["status"] == "login_required":
                return None
            token = str(uuid.uuid4())
            changed = conn.execute(
                "UPDATE custom_workflow_state SET lease_token=?,lease_until=?,status='running' "
                "WHERE workflow_id=? AND lease_until<=? AND (? OR next_run_at<=?)",
                (token, now + lease_seconds, workflow_id, now, force, now),
            ).rowcount
            if not changed:
                return None
            conn.execute(
                "UPDATE custom_workflow_runs SET status='interrupted', completed_at=?, "
                "error='worker lease expired' WHERE workflow_id=? AND status='running'",
                (now, workflow_id),
            )
            run_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO custom_workflow_runs(run_id,workflow_id,version,lease_token,executor,"
                "parameters,status,started_at) VALUES(?,?,?,?,?,?,'running',?)",
                (
                    run_id,
                    workflow_id,
                    workflow["version"],
                    token,
                    workflow["executor"],
                    json.dumps(dict(parameters)),
                    now,
                ),
            )
            return dict(workflow, run_id=run_id, lease_token=token, parameters=dict(parameters))

    def _current(self, run: Mapping[str, Any]) -> bool:
        row = self.database.conn.execute(
            "SELECT 1 FROM custom_workflows w JOIN custom_workflow_state s USING(workflow_id) "
            "JOIN custom_sources c ON w.source_id=c.source_id WHERE w.workflow_id=? "
            "AND w.active_version=? AND s.lease_token=? AND s.lease_until>? AND c.enabled=1",
            (run["workflow_id"], run["version"], run["lease_token"], time.time()),
        ).fetchone()
        return bool(row)

    def _settle_stale_run(
        self, run: Mapping[str, Any], *, discovered_count: int, enqueued_count: int
    ) -> dict[str, Any]:
        """End this token's history without mutating any workflow frontier."""
        self.database.conn.execute(
            "UPDATE custom_workflow_runs SET status='stale',completed_at=?, "
            "discovered_count=?,enqueued_count=?,error='run lease or source state changed' "
            "WHERE run_id=? AND workflow_id=? AND version=? AND lease_token=? "
            "AND status='running'",
            (
                time.time(),
                discovered_count,
                enqueued_count,
                run["run_id"],
                run["workflow_id"],
                run["version"],
                run["lease_token"],
            ),
        )
        return {
            "status": "stale",
            "discovered_count": discovered_count,
            "enqueued_count": enqueued_count,
        }

    def complete(
        self,
        run: Mapping[str, Any],
        *,
        status: str,
        items: list[Any],
        enqueue: Callable[[list[Any]], int],
        interval_minutes: int,
        error: str = "",
        cursor: Mapping[str, Any] | None = None,
        scope_complete: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Fence handoff and advance seen/cursor only after durable enqueue succeeds."""
        from openbiliclaw.discovery.candidate_pool import (
            candidate_key_for,
            discovered_content_to_candidate_write,
        )

        with self.transaction() as conn:
            if not self._current(run):
                return self._settle_stale_run(run, discovered_count=len(items), enqueued_count=0)
            retained = [
                item
                for item in items
                if not conn.execute(
                    "SELECT 1 FROM custom_workflow_seen WHERE workflow_id=? AND item_key=?",
                    (run["workflow_id"], candidate_key_for(item)),
                ).fetchone()
            ]
            allocated_all = len(retained) <= limit
            if len(retained) > limit:
                scope_complete = False
                retained = retained[:limit]
            count = enqueue(retained) if retained else 0
            accepted = retained
            if count < len(retained):
                # A short count can be a durable duplicate, an intentional
                # date-policy skip, or retryable queue pressure. Policy skips
                # do not enter the candidate pool but have completed this
                # workflow's handoff; only unexplained missing rows must retry.
                accepted = [
                    item
                    for item in retained
                    if conn.execute(
                        "SELECT 1 FROM discovery_candidates WHERE candidate_key=? "
                        "UNION ALL SELECT 1 FROM content_cache WHERE item_key=? LIMIT 1",
                        (candidate_key_for(item), candidate_key_for(item)),
                    ).fetchone()
                    or self.database.discovery_candidate_policy_exclusion_reason(
                        discovered_content_to_candidate_write(item)
                    )
                ]
                if len(accepted) < len(retained):
                    scope_complete = False
                    status = "degraded"
                    error = error or "candidate_queue_full"
            # A candidate sink may commit its own short writes. Re-fence before
            # recording the frontier so a replaced worker cannot advance it.
            if not self._current(run):
                return self._settle_stale_run(
                    run, discovered_count=len(items), enqueued_count=count
                )
            now = time.time()
            for item in accepted:
                conn.execute(
                    "INSERT OR IGNORE INTO custom_workflow_seen VALUES(?,?,?)",
                    (run["workflow_id"], candidate_key_for(item), now),
                )
            success = status in {"ok", "empty", "not_modified"}
            safe_cursor = success and scope_complete
            new_cursor = dict(cursor or {})
            # A bounded, successful scan remains healthy. Partial sitemap
            # recovery may move independently without committing HTTP validators.
            if (
                not safe_cursor
                and allocated_all
                and len(accepted) == len(retained)
                and new_cursor.get("retry_cursor")
            ):
                new_cursor = dict(run.get("cursor") or {}) | {
                    "retry_cursor": new_cursor["retry_cursor"]
                }
                safe_cursor = True
            delay = interval_minutes * 60
            if not success:
                delay = min(86_400, max(delay, 60 * 2 ** min(10, run["consecutive_failures"])))
            conn.execute(
                "UPDATE custom_workflow_state SET status=?,next_run_at=?,lease_token='', "
                "lease_until=0,last_error=?,last_success_at=CASE WHEN ? THEN ? ELSE "
                "last_success_at END,consecutive_failures=CASE WHEN ? THEN 0 ELSE "
                "consecutive_failures+1 END,cursor_json=CASE WHEN ? THEN ? ELSE cursor_json END "
                "WHERE workflow_id=? AND lease_token=?",
                (
                    status,
                    now + delay,
                    error[:500],
                    success,
                    now,
                    success,
                    safe_cursor,
                    json.dumps(new_cursor),
                    run["workflow_id"],
                    run["lease_token"],
                ),
            )
            conn.execute(
                "UPDATE custom_workflow_runs SET status=?,completed_at=?,discovered_count=?,"
                "enqueued_count=?,error=? WHERE run_id=? AND status='running'",
                (status, now, len(items), count, error[:500], run["run_id"]),
            )
            return {"status": status, "discovered_count": len(items), "enqueued_count": count}

    def run_count(self, workflow_id: str) -> int:
        """Count all runs so regular/explore rotation survives history page limits."""
        row = self.database.conn.execute(
            "SELECT COUNT(*) FROM custom_workflow_runs WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        return int(row[0])

    def runs(self, source_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.database.conn.execute(
            "SELECT r.* FROM custom_workflow_runs r JOIN custom_workflows w USING(workflow_id) "
            "WHERE w.source_id=? ORDER BY r.started_at DESC LIMIT ?",
            (source_id, min(100, max(1, limit))),
        ).fetchall()
        return [
            {k: row[k] for k in dict(row) if k not in {"parameters", "lease_token"}} for row in rows
        ]
