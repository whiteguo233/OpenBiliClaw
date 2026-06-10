"""Tests for InitCoordinator (gui-init spec §5, plan A2)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.init_coordinator import InitCoordinator
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _FakeHub:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


def _coord(tmp_path: Path) -> tuple[InitCoordinator, Database, _FakeHub]:
    db = Database(tmp_path / "init.db")
    db.initialize()
    hub = _FakeHub()
    ctx = SimpleNamespace(database=db, event_hub=hub, runtime_controller=None)
    return InitCoordinator(ctx), db, hub


def test_try_start_single_flight_and_seeds_stages(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    assert coord.try_start("run-1") is True
    assert coord.init_active() is True
    run = db.get_latest_init_run()
    stages = json.loads(run["stages_json"])
    assert [s["n"] for s in stages] == [1, 2, 3, 4]
    assert all(s["status"] == "pending" for s in stages)
    # Second start blocked while active.
    assert coord.try_start("run-2") is False


def test_reconcile_on_boot_delegates(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    db.update_init_run("run-1", status="running")
    assert coord.reconcile_on_boot() == 1
    assert coord.init_active() is False


async def test_lifecycle_emits_progress_then_completed(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    for n in (1, 2, 3, 4):
        await coord.stage_started("run-1", n)
        await coord.stage_done("run-1", n)
    await coord.complete("run-1", partial_success=False)

    run = db.get_latest_init_run()
    assert run["status"] == "completed"
    assert all(s["status"] == "ok" for s in json.loads(run["stages_json"]))
    # sequence strictly increasing across all writes.
    seqs = [e["sequence"] for e in hub.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert hub.events[-1]["type"] == "init_completed"
    assert any(e["type"] == "init_progress" for e in hub.events)


async def test_fail_marks_failed_with_reason(tmp_path: Path) -> None:
    coord, db, hub = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.fail("run-1", "llm_not_ready")
    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "llm_not_ready"
    assert hub.events[-1] == {
        "type": "init_failed",
        "run_id": "run-1",
        "sequence": hub.events[-1]["sequence"],
        "stage": hub.events[-1]["stage"],
        "total": 4,
        "reason": "llm_not_ready",
    }


async def test_parallel_stage_3_4_no_sequence_loss(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    await coord.mark_running("run-1")
    # Stages 3 and 4 run concurrently (P3/P4 parallel) — the write lock must
    # keep both stage statuses and serialize sequence without a lost update.
    await asyncio.gather(
        coord.stage_started("run-1", 3),
        coord.stage_started("run-1", 4),
    )
    status = coord.get_status()
    by_n = {s["n"]: s["status"] for s in status["stages"]}
    assert by_n[3] == "running" and by_n[4] == "running"
    assert status["current_stage"] == 3  # lowest still-running
    run = db.get_latest_init_run()
    assert run["sequence"] == 3  # mark_running + 2 stage_started, no loss


def test_bootstrap_task_ownership(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")
    coord.register_enqueued_task("run-1", "task-abc")
    assert coord.is_owned_bootstrap_task("task-abc") is True
    assert coord.is_owned_bootstrap_task("task-other") is False


def test_unowned_when_not_active(tmp_path: Path) -> None:
    coord, db, _ = _coord(tmp_path)
    coord.try_start("run-1")
    coord.register_enqueued_task("run-1", "task-abc")
    db.update_init_run("run-1", status="completed")
    assert coord.is_owned_bootstrap_task("task-abc") is False


async def test_cancel_current_run_cancels_task(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    coord.try_start("run-1")

    async def _long() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(_long())
    coord.attach_task("run-1", task)
    assert await coord.cancel_current_run("run-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task


def test_get_status_idle_when_empty(tmp_path: Path) -> None:
    coord, _, _ = _coord(tmp_path)
    status = coord.get_status()
    assert status["running"] is False
    assert status["status"] == "idle"
    assert status["current_stage"] == 0


# ── A3: RuntimeContext wiring ──────────────────────────────────────────────


def test_runtime_context_exposes_lazy_init_coordinator(tmp_path: Path) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    db = Database(tmp_path / "ctx.db")
    db.initialize()
    ctx = RuntimeContext(database=db)
    c1 = ctx.init_coordinator
    assert isinstance(c1, InitCoordinator)
    assert ctx.init_coordinator is c1  # memoized singleton
    # Reads ctx.database lazily, so it actually drives the wired DB.
    assert c1.try_start("r1") is True
    assert db.get_latest_init_run()["run_id"] == "r1"


def test_coordinator_reads_ctx_components_lazily_not_cached(tmp_path: Path) -> None:
    from openbiliclaw.api.runtime_context import RuntimeContext

    db1 = Database(tmp_path / "a.db")
    db1.initialize()
    ctx = RuntimeContext(database=db1)
    coord = ctx.init_coordinator
    # Swap a component on the ctx (mirrors hot-reload swapping runtime_controller):
    # the same coordinator must use the new instance, not one cached at build.
    db2 = Database(tmp_path / "b.db")
    db2.initialize()
    ctx.database = db2
    coord.try_start("r2")
    assert db2.get_latest_init_run()["run_id"] == "r2"
    assert db1.get_latest_init_run() is None


def test_startup_reconciles_stale_init_run(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    db = Database(tmp_path / "startup.db")
    db.initialize()
    db.try_reserve_init_starting("stale")
    db.update_init_run("stale", status="running")

    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app):  # entering triggers the startup event
        pass

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"


def test_init_status_endpoint_shape(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app

    db = Database(tmp_path / "e1.db")
    db.initialize()
    app = create_app(memory_manager=object(), database=db, soul_engine=object())
    with TestClient(app) as client:
        resp = client.get("/api/init-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["initialized"] is False
    assert body["total_stages"] == 4
    assert len(body["stages"]) == 4
    # No configured cookie / chat creds in this minimal app → can't start.
    assert body["prerequisites"]["bilibili_check"] == "failed"
    assert body["can_start"] is False
    assert body["reason"] in ("bilibili_not_logged_in", "unsupported_runtime", "llm_not_ready")


def test_background_llm_work_paused_during_init(tmp_path: Path) -> None:
    """D1: all daemon-owned background LLM loops pause while init is active."""
    from openbiliclaw.api.runtime_context import RuntimeContext

    db = Database(tmp_path / "d1.db")
    db.initialize()
    ctx = RuntimeContext(database=db)
    # Idle baseline is whatever presence/scheduler gating decides; the contract
    # under test is the init short-circuit forcing False.
    ctx.init_coordinator.try_start("r1")
    assert ctx.background_llm_work_allowed() is False
