"""Coordinator for guided (GUI) initialization.

Owns the init lifecycle on a *live* backend (gui-init spec §5):

* single-flight start (TOCTOU) via the ``init_runs`` reservation,
* the **single writer** to the status store + progress events,
* the per-run ``enqueued_task_ids`` set that writer-gating consults to let
  init's own bootstrap task-results through,
* cooperative cancel of the background task.

It holds the :class:`RuntimeContext` (not direct component references) and
reads ``ctx.database`` / ``ctx.event_hub`` / ``ctx.runtime_controller`` lazily
so it always uses the current instances after a config-driven rebuild swaps
them (review R2 A-1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOTAL_STAGES = 4
_STAGE_LABELS = {1: "拉取数据", 2: "分析偏好", 3: "生成画像", 4: "发现内容池"}
_ACTIVE = ("starting", "running")


def _initial_stages() -> list[dict[str, Any]]:
    return [
        {"n": n, "label": _STAGE_LABELS[n], "status": "pending", "reason": None}
        for n in range(1, _TOTAL_STAGES + 1)
    ]


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class InitCoordinator:
    """Lifecycle owner for one guided-init run at a time."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._current_task: asyncio.Task[Any] | None = None
        self._enqueued_task_ids: set[str] = set()
        # Serializes status writes + event publishes so the parallel stage 3/4
        # progress updates can't interleave / reorder ``sequence`` (spec §5e).
        self._write_lock = asyncio.Lock()
        self._seq = 0

    # ── lazy component access (survives rebuild) ───────────────────────────
    @property
    def _db(self) -> Any:
        return self._ctx.database

    @property
    def _event_hub(self) -> Any:
        return getattr(self._ctx, "event_hub", None)

    # ── boot / liveness ────────────────────────────────────────────────────
    def reconcile_on_boot(self) -> int:
        """Fail stale starting/running runs left by a crash. Returns count."""
        db = self._db
        if db is None:
            return 0
        return int(db.reconcile_init_runs_on_boot())

    def init_active(self) -> bool:
        run = self._db.get_latest_init_run()
        return bool(run and run["status"] in _ACTIVE)

    # ── start / reset (TOCTOU lives in the DB CAS; E2 does cheap pre-checks) ─
    def try_start(self, run_id: str) -> bool:
        """Reserve a new run (single-flight). Seeds the stage list on success."""
        if not self._db.try_reserve_init_starting(run_id):
            return False
        self._enqueued_task_ids = set()
        self._seq = 0
        self._db.update_init_run(
            run_id, stages_json=json.dumps(_initial_stages(), ensure_ascii=False)
        )
        return True

    def reset_to_idle(self, run_id: str, *, reason: str | None = None) -> None:
        """Roll a reserved-but-not-launched run back (E2 pre-flight reject)."""
        self._db.update_init_run(run_id, status="idle", error_reason=reason)

    # ── bootstrap task ownership (consulted by writer-gating, D1) ──────────
    def register_enqueued_task(self, run_id: str, task_id: str) -> None:
        self._enqueued_task_ids.add(str(task_id))

    def is_owned_bootstrap_task(self, task_id: str) -> bool:
        return self.init_active() and str(task_id) in self._enqueued_task_ids

    def owned_task_ids(self) -> set[str]:
        """Bootstrap task ids enqueued by the active run (empty if idle).

        ``next-task`` consults this so the extension is only handed init's own
        bootstrap work while a run is active — never a stale pending task that
        would otherwise starve the run's collectors (gui-init review)."""
        if not self.init_active():
            return set()
        return set(self._enqueued_task_ids)

    # ── background task handle (for cancel) ────────────────────────────────
    def attach_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._current_task = task

    async def cancel_current_run(self, run_id: str) -> bool:
        """Request cancellation of the running task. The wrapper's ``finally``
        persists the ``cancelled`` status (single-writer; spec §5f)."""
        task = self._current_task
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # ── single status writer ───────────────────────────────────────────────
    async def _write(
        self,
        run_id: str,
        *,
        status: str | None = None,
        stage: int | None = None,
        stage_status: str | None = None,
        stage_reason: str | None = None,
        partial_success: bool | None = None,
        error_reason: str | None = None,
        finished: bool = False,
        event_type: str | None = None,
        event_extra: dict[str, Any] | None = None,
    ) -> int:
        async with self._write_lock:
            run = self._db.get_latest_init_run()
            stages = (
                json.loads(run["stages_json"])
                if run and run.get("stages_json")
                else _initial_stages()
            )
            if stage is not None and stage_status is not None:
                for s in stages:
                    if s["n"] == stage:
                        s["status"] = stage_status
                        s["reason"] = stage_reason
            # On a terminal failure/cancel, downgrade any still-"running" or
            # "pending" stage so status consumers (and the extension checklist,
            # which keys off stage status) don't show a non-terminal timeline
            # for a finished run (gui-init review).
            if status in ("failed", "cancelled"):
                for s in stages:
                    if s["status"] in ("running", "pending"):
                        s["status"] = status
                        if s.get("reason") is None:
                            s["reason"] = error_reason
            self._seq += 1
            fields: dict[str, Any] = {
                "sequence": self._seq,
                "stages_json": json.dumps(stages, ensure_ascii=False),
            }
            if status is not None:
                fields["status"] = status
            if stage is not None:
                fields["stage"] = stage
            if partial_success is not None:
                fields["partial_success"] = 1 if partial_success else 0
            if error_reason is not None:
                fields["error_reason"] = error_reason
            if finished:
                fields["finished_at"] = _utcnow_iso()
            self._db.update_init_run(run_id, **fields)

            if event_type and self._event_hub is not None:
                event: dict[str, Any] = {
                    "type": event_type,
                    "run_id": run_id,
                    "sequence": self._seq,
                    "stage": stage if stage is not None else _current_stage(stages),
                    "total": _TOTAL_STAGES,
                }
                if event_extra:
                    event.update(event_extra)
                with contextlib.suppress(Exception):
                    await self._event_hub.publish(event)
            return self._seq

    async def mark_running(self, run_id: str) -> None:
        await self._write(run_id, status="running")

    async def stage_started(self, run_id: str, stage: int) -> None:
        await self._write(
            run_id,
            status="running",
            stage=stage,
            stage_status="running",
            event_type="init_progress",
        )

    async def stage_done(
        self, run_id: str, stage: int, *, status: str = "ok", reason: str | None = None
    ) -> None:
        await self._write(
            run_id,
            stage=stage,
            stage_status=status,
            stage_reason=reason,
            event_type="init_progress",
        )

    async def complete(self, run_id: str, *, partial_success: bool = False) -> None:
        await self._write(
            run_id,
            status="completed",
            partial_success=partial_success,
            finished=True,
            event_type="init_completed",
            event_extra={"partial_success": partial_success},
        )

    async def fail(self, run_id: str, reason: str) -> None:
        await self._write(
            run_id,
            status="failed",
            error_reason=reason,
            finished=True,
            event_type="init_failed",
            event_extra={"reason": reason},
        )

    async def cancel(self, run_id: str, reason: str = "cancelled") -> None:
        await self._write(
            run_id,
            status="cancelled",
            error_reason=reason,
            finished=True,
            event_type="init_failed",
            event_extra={"reason": reason},
        )

    # ── status read (run-derived part; E1 adds prereqs/can_manage) ─────────
    def get_status(self) -> dict[str, Any]:
        run = self._db.get_latest_init_run()
        if run is None:
            return {
                "running": False,
                "run_id": None,
                "sequence": 0,
                "current_stage": 0,
                "total_stages": _TOTAL_STAGES,
                "stages": _initial_stages(),
                "partial_success": False,
                "status": "idle",
                "reason": "none",
            }
        stages = json.loads(run["stages_json"]) if run.get("stages_json") else _initial_stages()
        return {
            "running": run["status"] in _ACTIVE,
            "run_id": run["run_id"],
            "sequence": run["sequence"],
            "current_stage": _current_stage(stages),
            "total_stages": _TOTAL_STAGES,
            "stages": stages,
            "partial_success": bool(run["partial_success"]),
            "status": run["status"],
            "reason": run["error_reason"] or "none",
        }


def _current_stage(stages: Sequence[dict[str, Any]]) -> int:
    """Lowest still-running stage; else the highest completed; else 0 (spec §5e)."""
    running = [int(s["n"]) for s in stages if s["status"] == "running"]
    if running:
        return min(running)
    done = [int(s["n"]) for s in stages if s["status"] in ("ok", "warning", "failed")]
    return max(done) if done else 0
