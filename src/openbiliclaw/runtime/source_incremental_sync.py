"""Extension-online periodic account-bootstrap scheduler.

This module owns only the runtime scheduling decision.  It enqueues through
the database-only helpers in :mod:`openbiliclaw.sources.source_bootstrap`,
then lets the caller wake the connected extension in-process.  It deliberately
does not import the CLI, perform HTTP, or run the blocking collector polls.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from openbiliclaw.config import _normalize_source_incremental_hours
from openbiliclaw.sources.source_bootstrap import (
    SOURCE_BOOTSTRAP_DECISION_LOCK,
    BootstrapEnqueueResult,
    cancel_incremental_bootstrap_tasks,
    enqueue_dy_bootstrap,
    enqueue_linuxdo_bootstrap,
    enqueue_reddit_bootstrap,
    enqueue_v2ex_bootstrap,
    enqueue_xhs_bootstrap,
    enqueue_yt_bootstrap,
    enqueue_zhihu_bootstrap,
)

if TYPE_CHECKING:
    from openbiliclaw.config import SchedulerConfig
    from openbiliclaw.runtime.presence import PresenceTracker

logger = logging.getLogger(__name__)

SOURCE_ORDER = ("xhs", "dy", "yt", "zhihu", "reddit", "linuxdo", "v2ex")
_ACTIVE_STATUSES = frozenset({"pending", "in_progress"})
_TASK_SPECS: dict[str, tuple[str, str, Callable[..., BootstrapEnqueueResult]]] = {
    "xhs": ("xhs_tasks", "bootstrap_profile", enqueue_xhs_bootstrap),
    "dy": ("dy_tasks", "bootstrap_profile", enqueue_dy_bootstrap),
    "yt": ("yt_tasks", "bootstrap_profile", enqueue_yt_bootstrap),
    "zhihu": ("zhihu_tasks", "bootstrap_events", enqueue_zhihu_bootstrap),
    "reddit": ("reddit_tasks", "bootstrap_events", enqueue_reddit_bootstrap),
    "linuxdo": ("linuxdo_tasks", "bootstrap_events", enqueue_linuxdo_bootstrap),
    "v2ex": ("v2ex_tasks", "bootstrap_profile", enqueue_v2ex_bootstrap),
}
_SOURCE_CONFIG_ALIASES: dict[str, tuple[str, ...]] = {
    "xhs": ("xhs", "xiaohongshu"),
    "dy": ("dy", "douyin"),
    "yt": ("yt", "youtube"),
    "zhihu": ("zhihu",),
    "reddit": ("reddit",),
    "linuxdo": ("linuxdo",),
    "v2ex": ("v2ex",),
}
_SOURCE_CONFIG_NAMES: dict[str, str] = {
    "xhs": "xiaohongshu",
    "dy": "douyin",
    "yt": "youtube",
    "zhihu": "zhihu",
    "reddit": "reddit",
    "linuxdo": "linuxdo",
    "v2ex": "v2ex",
}
_SOURCE_INTERVAL_FIELDS = {
    "xhs": "xhs_incremental_hours",
    "dy": "douyin_incremental_hours",
    "yt": "youtube_incremental_hours",
    "zhihu": "zhihu_incremental_hours",
    "reddit": "reddit_incremental_hours",
    "linuxdo": "linuxdo_incremental_hours",
    "v2ex": "v2ex_incremental_hours",
}


@dataclass(frozen=True)
class SourceIncrementalSyncResult:
    """Outcome of one scheduler tick."""

    reason: str
    source: str = ""
    task_id: str | None = None
    created: bool = False


@dataclass(frozen=True)
class _ActiveTask:
    source: str
    task_id: str
    status: str
    incremental: bool
    created_at: datetime | None
    completed_at: datetime | None = None
    result: dict[str, object] = field(default_factory=dict)


def _utc_now(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_task_created_at(value: object) -> datetime | None:
    """Parse SQLite's UTC ``CURRENT_TIMESTAMP`` as task recovery evidence."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_value(row: object, key: str, index: int) -> object:
    if hasattr(row, "keys"):
        try:
            return row[key]  # type: ignore[index]
        except (KeyError, IndexError):
            return None
    try:
        return row[index]  # type: ignore[index]
    except (IndexError, TypeError):
        return None


@dataclass
class SourceIncrementalSync:
    """Schedule at most one due account bootstrap per tick.

    ``clock`` is injected so tests can advance time without sleeping.  The
    synchronous part of each decision runs in ``asyncio.to_thread``; only a
    newly-created task is followed by the asynchronous in-process ``kick``.
    """

    database: Any
    memory_manager: Any
    presence: PresenceTracker
    source_enabled: Mapping[str, bool] | Callable[[], Mapping[str, bool]]
    scheduler_config: SchedulerConfig
    profile_ready: Callable[[], bool | Awaitable[bool]]
    init_active: Callable[[], bool | Awaitable[bool]]
    runtime_config: Any | None = None
    kick: Callable[[str], Awaitable[None]] | None = None
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))
    _tick_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def tick(self) -> SourceIncrementalSyncResult:
        """Run one non-blocking scheduling tick."""
        async with self._tick_lock:
            scheduler_enabled = bool(getattr(self.scheduler_config, "enabled", True))
            incremental_enabled = bool(
                getattr(self.scheduler_config, "source_incremental_enabled", False)
            )
            if not scheduler_enabled or not incremental_enabled:
                state = self._load_state()
                scheduler_task_ids = self._scheduler_task_ids(state or {})
                try:
                    await asyncio.to_thread(
                        cancel_incremental_bootstrap_tasks,
                        self.database,
                        scheduler_task_ids=scheduler_task_ids,
                    )
                except Exception:
                    logger.warning(
                        "disabled source incremental task cleanup failed",
                        exc_info=True,
                    )
                reason = (
                    "scheduler_disabled" if not scheduler_enabled else "source_incremental_disabled"
                )
                return SourceIncrementalSyncResult(reason=reason)

            disabled_sources = {
                source for source in SOURCE_ORDER if not self._source_incremental_enabled(source)
            }
            if disabled_sources:
                try:
                    await asyncio.to_thread(
                        cancel_incremental_bootstrap_tasks,
                        self.database,
                        sources=disabled_sources,
                    )
                except Exception:
                    logger.warning(
                        "disabled per-source incremental task cleanup failed",
                        exc_info=True,
                    )

            try:
                present = self.presence.is_present(
                    int(getattr(self.scheduler_config, "extension_disconnect_grace_seconds", 90))
                )
            except Exception:
                logger.warning("source incremental presence check failed", exc_info=True)
                present = False
            if not present:
                logger.debug("source incremental sync skipped: extension absent")
                return SourceIncrementalSyncResult(reason="extension_absent")

            if not await self._gate_value(self.profile_ready, default=False):
                return SourceIncrementalSyncResult(reason="profile_not_ready")
            if await self._gate_value(self.init_active, default=True):
                return SourceIncrementalSyncResult(reason="init_active")

            now = _utc_now(self.clock())
            try:
                outcome = await asyncio.to_thread(self._locked_tick_sync, now)
            except Exception:
                logger.exception("source incremental sync database decision failed")
                return SourceIncrementalSyncResult(reason="database_error")

            if outcome.created and outcome.task_id and self.kick is not None:
                try:
                    await self.kick(outcome.source)
                except Exception:
                    # The row and schedule timestamp are intentionally kept;
                    # the extension's normal polling path can recover it.
                    logger.warning(
                        "source incremental task kick failed source=%s task_id=%s",
                        outcome.source,
                        outcome.task_id,
                        exc_info=True,
                    )
                    return SourceIncrementalSyncResult(
                        reason="created_kick_failed",
                        source=outcome.source,
                        task_id=outcome.task_id,
                        created=True,
                    )
            return outcome

    def _locked_tick_sync(self, now: datetime) -> SourceIncrementalSyncResult:
        # Shared with CLI/guided-init enqueue helpers, so a manual task cannot
        # slip into the active-scan -> periodic-enqueue window (or vice versa).
        with SOURCE_BOOTSTRAP_DECISION_LOCK:
            return self._tick_sync(now)

    async def _gate_value(
        self,
        callback: Callable[[], bool | Awaitable[bool]],
        *,
        default: bool,
    ) -> bool:
        try:
            value = callback()
            if inspect.isawaitable(value):
                value = await value
            return bool(value)
        except Exception:
            logger.warning("source incremental gate callback failed", exc_info=True)
            return default

    def _tick_sync(self, now: datetime) -> SourceIncrementalSyncResult:
        state = self._load_state()
        if state is None:
            return SourceIncrementalSyncResult(reason="state_error")

        reconciled = self._reconcile_active_state(state, now=now)
        if reconciled is not None:
            return reconciled
        # Reconciliation may atomically settle a terminal task in the memory
        # manager. Reload before the due decision so it observes the newly
        # written last_success_at rather than the pre-reconcile snapshot.
        state = self._load_state()
        if state is None:
            return SourceIncrementalSyncResult(reason="state_error")

        skipped_budget_sources: set[str] = set()
        skipped_capability_sources: set[str] = set()
        first_budget_result: SourceIncrementalSyncResult | None = None
        first_capability_result: SourceIncrementalSyncResult | None = None
        while True:
            source = self._select_due_source(
                state,
                now,
                excluded=skipped_budget_sources | skipped_capability_sources,
            )
            if source is None:
                return (
                    first_budget_result
                    or first_capability_result
                    or SourceIncrementalSyncResult(reason="not_due")
                )

            _table, _task_type, enqueue = _TASK_SPECS[source]
            enqueue_kwargs: dict[str, Any] = {"incremental_owner": "scheduler"}
            if source == "v2ex" and self.runtime_config is not None:
                from openbiliclaw.api.source_auth.probe_cache import LIVE_PROBES
                from openbiliclaw.sources.v2ex_identity import resolve_v2ex_identity_state

                identity = resolve_v2ex_identity_state(
                    cfg=self.runtime_config,
                    database=self.database,
                    probes=LIVE_PROBES,
                )
                if not identity.private_bootstrap_available:
                    first_capability_result = (
                        first_capability_result
                        or SourceIncrementalSyncResult(
                            reason="capability_not_ready",
                            source=source,
                        )
                    )
                    skipped_capability_sources.add(source)
                    continue
                enqueue_kwargs.update(
                    {
                        "username": identity.username,
                        "config": getattr(
                            getattr(self.runtime_config, "sources", None),
                            "v2ex",
                            None,
                        ),
                    }
                )
            try:
                outcome = enqueue(
                    self.database,
                    force=True,
                    incremental=True,
                    **enqueue_kwargs,
                )
            except Exception:
                logger.exception("source incremental enqueue failed source=%s", source)
                return SourceIncrementalSyncResult(reason="enqueue_error", source=source)

            task_id = str(outcome.task_id or "").strip()
            if not outcome.created or not task_id:
                # The enqueue core uses this exact empty result for a source's
                # exhausted daily budget. It proves no row was inserted, so
                # the same tick may safely consider the next due source and
                # avoid starving it. Reuse, exceptions, and inconsistent
                # created-without-id outcomes stop immediately because they do
                # not provide that no-row guarantee.
                result = SourceIncrementalSyncResult(
                    reason=outcome.reason,
                    source=source,
                    task_id=task_id or None,
                    created=False,
                )
                if outcome.reason == "enqueue_failed" and not outcome.created and not task_id:
                    first_budget_result = first_budget_result or result
                    skipped_budget_sources.add(source)
                    continue
                return result
            break

        timestamp = _utc_now(now).isoformat()
        try:
            self._update_state(
                lambda current: self._stamp_created_task(
                    current,
                    source=source,
                    task_id=task_id,
                    timestamp=timestamp,
                )
            )
        except Exception:
            # The task row is the recovery authority.  If the process dies or
            # state persistence fails in this window, the next tick adopts its
            # incremental payload before considering another enqueue.
            logger.warning(
                "source incremental state stamp failed; task will be adopted source=%s id=%s",
                source,
                task_id,
                exc_info=True,
            )
        return SourceIncrementalSyncResult(
            reason="created",
            source=source,
            task_id=task_id,
            created=True,
        )

    def _load_state(self) -> dict[str, object] | None:
        load = getattr(self.memory_manager, "load_source_bootstrap_state", None)
        if not callable(load):
            return {}
        try:
            loaded = load()
        except Exception:
            logger.warning("source incremental state load failed", exc_info=True)
            return None
        return loaded if isinstance(loaded, dict) else {}

    def _update_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]:
        update = getattr(self.memory_manager, "update_source_bootstrap_state", None)
        if callable(update):
            updated = update(mutator)
            if not isinstance(updated, dict):
                raise RuntimeError("source bootstrap state updater returned a non-object")
            return updated
        state = self._load_state() or {}
        result = mutator(state)
        next_state = state if result is None else result
        save = getattr(self.memory_manager, "save_source_bootstrap_state", None)
        if not callable(save):
            raise RuntimeError("memory manager lacks source bootstrap state persistence")
        save(next_state)
        return next_state

    @staticmethod
    def _incremental_state(state: dict[str, object]) -> dict[str, object]:
        raw = state.get("source_incremental")
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _scheduler_task_ids(state: dict[str, object]) -> set[str]:
        raw_active = SourceIncrementalSync._incremental_state(state).get("active_task")
        if not isinstance(raw_active, dict):
            return set()
        task_id = str(raw_active.get("task_id", "") or "").strip()
        return {task_id} if task_id else set()

    def _reconcile_active_state(
        self,
        state: dict[str, object],
        *,
        now: datetime,
    ) -> SourceIncrementalSyncResult | None:
        incremental_state = self._incremental_state(state)
        raw_active = incremental_state.get("active_task")
        recorded: _ActiveTask | None = None
        if isinstance(raw_active, dict):
            recorded_source = str(raw_active.get("source", "")).strip().lower()
            recorded_id = str(raw_active.get("task_id", "")).strip()
            if recorded_source in _TASK_SPECS and recorded_id:
                row = self._get_task(recorded_source, recorded_id)
                if row is not None:
                    recorded = row

        if (
            recorded is not None
            and recorded.status in _ACTIVE_STATUSES
            and self._source_is_enabled(recorded.source)
        ):
            return SourceIncrementalSyncResult(
                reason="active_task",
                source=recorded.source,
                task_id=recorded.task_id,
            )

        # A terminal state advances cadence only after a complete success.
        # Failed and degraded runs clear ownership but stay immediately due.
        if recorded is not None and recorded.status in {"completed", "failed"}:
            marker = str(recorded.result.get("_openbiliclaw_terminal_status", "") or "").strip()
            succeeded = (
                recorded.incremental and recorded.status == "completed" and marker != "degraded"
            )
            current_time = _utc_now(now)
            completed_at = recorded.completed_at or recorded.created_at or current_time
            if completed_at > current_time:
                completed_at = current_time
            try:
                self._update_state(
                    lambda current: self._settle_terminal_task(
                        current,
                        source=recorded.source,
                        completed_at=completed_at.isoformat() if succeeded else "",
                    )
                )
            except Exception:
                logger.warning("source incremental terminal reconciliation failed", exc_info=True)
                return SourceIncrementalSyncResult(reason="state_error")
            state = self._load_state() or state
            raw_active = None

        # A missing state record is stale. Clear it before scanning the
        # authoritative task tables so a later active row can be adopted.
        if isinstance(raw_active, dict) and raw_active:
            try:
                self._update_state(
                    lambda current: self._replace_active_task(current, active_task=None)
                )
            except Exception:
                logger.warning(
                    "source incremental active-state reconciliation failed", exc_info=True
                )
                return SourceIncrementalSyncResult(reason="state_error")

        active_rows = [
            row for row in self._find_active_tasks() if self._source_is_enabled(row.source)
        ]
        if not active_rows:
            return None

        first = active_rows[0]
        if first.incremental:
            adopted_at = first.created_at or _utc_now(now)
            try:
                self._update_state(
                    lambda current: self._stamp_created_task(
                        current,
                        source=first.source,
                        task_id=first.task_id,
                        timestamp=adopted_at.isoformat(),
                    )
                )
            except Exception:
                logger.warning("source incremental crash-window adoption failed", exc_info=True)
                return SourceIncrementalSyncResult(reason="state_error")
        return SourceIncrementalSyncResult(
            reason="active_task",
            source=first.source,
            task_id=first.task_id,
        )

    @staticmethod
    def _replace_active_task(
        state: dict[str, object],
        *,
        active_task: dict[str, object] | None,
    ) -> dict[str, object]:
        incremental = SourceIncrementalSync._incremental_state(state)
        incremental["active_task"] = active_task
        state["source_incremental"] = incremental
        return state

    @staticmethod
    def _settle_terminal_task(
        state: dict[str, object],
        *,
        source: str,
        completed_at: str,
    ) -> dict[str, object]:
        incremental = SourceIncrementalSync._incremental_state(state)
        if completed_at:
            raw_successes = incremental.get("last_success_at")
            successes = dict(raw_successes) if isinstance(raw_successes, dict) else {}
            successes[source] = completed_at
            incremental["last_success_at"] = successes
        incremental["active_task"] = None
        state["source_incremental"] = incremental
        return state

    @staticmethod
    def _stamp_created_task(
        state: dict[str, object],
        *,
        source: str,
        task_id: str,
        timestamp: str,
    ) -> dict[str, object]:
        incremental = SourceIncrementalSync._incremental_state(state)
        attempts_raw = incremental.get("last_attempt_at")
        attempts = dict(attempts_raw) if isinstance(attempts_raw, dict) else {}
        attempts[source] = timestamp
        incremental["last_attempt_at"] = attempts
        incremental["cursor"] = source
        incremental["active_task"] = {"source": source, "task_id": task_id}
        state["source_incremental"] = incremental
        return state

    def _find_active_tasks(self) -> list[_ActiveTask]:
        result: list[_ActiveTask] = []
        for source in SOURCE_ORDER:
            table, _task_type, _enqueue = _TASK_SPECS[source]
            result.extend(self._query_tasks(source, table=table, task_type=_task_type))
        return result

    def _get_task(self, source: str, task_id: str) -> _ActiveTask | None:
        table, task_type, _enqueue = _TASK_SPECS[source]
        rows = self._query_tasks(source, table=table, task_type=task_type, task_id=task_id)
        return rows[0] if rows else None

    def _query_tasks(
        self,
        source: str,
        *,
        table: str,
        task_type: str,
        task_id: str | None = None,
    ) -> list[_ActiveTask]:
        conn = getattr(self.database, "conn", None)
        if conn is None:
            return []
        where = "type = ?"
        params: list[object] = [task_type]
        if task_id is not None:
            where += " AND id = ?"
            params.append(task_id)
        else:
            where += " AND status IN ('pending', 'in_progress')"
        try:
            columns = {
                str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            completed_expr = "completed_at" if "completed_at" in columns else "NULL"
            result_expr = "result_json" if "result_json" in columns else "NULL"
            rows = conn.execute(
                f"SELECT id, status, payload_json, created_at, "
                f"{completed_expr} AS completed_at, {result_expr} AS result_json "
                f"FROM {table} WHERE {where} "
                "ORDER BY created_at ASC",
                params,
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        result: list[_ActiveTask] = []
        for row in rows:
            normalized_id = str(_row_value(row, "id", 0) or "").strip()
            if not normalized_id:
                continue
            status = str(_row_value(row, "status", 1) or "").strip().lower()
            payload = self._parse_payload(_row_value(row, "payload_json", 2))
            result.append(
                _ActiveTask(
                    source=source,
                    task_id=normalized_id,
                    status=status,
                    incremental=self._payload_flag(payload.get("incremental")),
                    created_at=_parse_task_created_at(_row_value(row, "created_at", 3)),
                    completed_at=_parse_task_created_at(_row_value(row, "completed_at", 4)),
                    result=self._parse_payload(_row_value(row, "result_json", 5)),
                )
            )
        return result

    @staticmethod
    def _parse_payload(raw: object) -> dict[str, object]:
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _payload_flag(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _source_incremental_enabled(self, source: str) -> bool:
        # Production always passes ``runtime_config``; tests that construct the
        # scheduler without it keep the legacy all-sources-on behaviour.
        if self.runtime_config is None:
            return True
        sources = getattr(self.runtime_config, "sources", None)
        source_config = getattr(sources, _SOURCE_CONFIG_NAMES[source], None)
        return bool(getattr(source_config, "incremental_enabled", False))

    def _source_is_enabled(self, source: str) -> bool:
        policy = self.source_enabled() if callable(self.source_enabled) else self.source_enabled
        if not isinstance(policy, Mapping):
            return False
        for key in _SOURCE_CONFIG_ALIASES[source]:
            if key in policy:
                return bool(policy[key])
        return False

    def _effective_interval_hours(self, source: str) -> int:
        global_hours = _normalize_source_incremental_hours(
            getattr(self.scheduler_config, "source_incremental_hours", 24),
            default=24,
            allow_none=False,
        )
        if global_hours is None:
            global_hours = 24
        if global_hours == 0:
            return 0
        override_hours = _normalize_source_incremental_hours(
            getattr(self.scheduler_config, _SOURCE_INTERVAL_FIELDS[source], None),
            default=None,
            allow_none=True,
        )
        if override_hours is None:
            return global_hours
        return override_hours

    def _select_due_source(
        self,
        state: dict[str, object],
        now: datetime,
        *,
        excluded: set[str] | None = None,
    ) -> str | None:
        incremental = self._incremental_state(state)
        cursor = str(incremental.get("cursor", "")).strip().lower()
        try:
            start = (SOURCE_ORDER.index(cursor) + 1) % len(SOURCE_ORDER)
        except ValueError:
            start = 0
        raw_successes = incremental.get("last_success_at")
        successes = raw_successes if isinstance(raw_successes, dict) else {}
        current = _utc_now(now)
        for offset in range(len(SOURCE_ORDER)):
            source = SOURCE_ORDER[(start + offset) % len(SOURCE_ORDER)]
            if excluded and source in excluded:
                continue
            if not self._source_is_enabled(source):
                continue
            if not self._source_incremental_enabled(source):
                continue
            interval_hours = self._effective_interval_hours(source)
            if interval_hours == 0:
                continue
            timestamp = _parse_timestamp(successes.get(source))
            if (
                timestamp is None
                or timestamp > current
                or current - timestamp >= timedelta(hours=interval_hours)
            ):
                return source
        return None


__all__ = ["SOURCE_ORDER", "SourceIncrementalSync", "SourceIncrementalSyncResult"]
