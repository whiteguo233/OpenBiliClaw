"""Tests for the init_runs store backing guided (GUI) initialization.

See docs/specs/gui-init.md §5a and docs/plans/2026-06-07-gui-init-implementation.md A1.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "init.db")
    db.initialize()
    return db


def test_get_latest_init_run_none_when_empty(tmp_path: Path) -> None:
    assert _db(tmp_path).get_latest_init_run() is None


def test_init_run_reserve_and_roundtrip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True

    run = db.get_latest_init_run()
    assert run is not None
    assert run["run_id"] == "run-1"
    assert run["status"] == "starting"
    assert run["stage"] == 0
    assert run["partial_success"] == 0

    db.update_init_run(
        "run-1",
        status="running",
        stage=2,
        sequence=5,
        stages_json=json.dumps([{"n": 1, "status": "ok"}, {"n": 2, "status": "running"}]),
    )
    run = db.get_latest_init_run()
    assert run["status"] == "running"
    assert run["stage"] == 2
    assert run["sequence"] == 5
    assert json.loads(run["stages_json"])[0]["status"] == "ok"


def test_try_reserve_is_single_flight(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert db.try_reserve_init_starting("run-1") is True
    # A second reservation while one is active must fail (TOCTOU guard).
    assert db.try_reserve_init_starting("run-2") is False

    # Once the active run finishes, a new run can be reserved again.
    db.update_init_run("run-1", status="completed")
    assert db.try_reserve_init_starting("run-3") is True
    assert db.get_latest_init_run()["run_id"] == "run-3"


def test_reconcile_fails_stale_active_runs_on_boot(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run("run-1", status="running", stage=3)

    reconciled = db.reconcile_init_runs_on_boot()
    assert reconciled == 1

    run = db.get_latest_init_run()
    assert run["status"] == "failed"
    assert run["error_reason"] == "interrupted"
    assert run["finished_at"] is not None

    # Idempotent: a completed run is not touched a second time.
    assert db.reconcile_init_runs_on_boot() == 0


def test_reconcile_leaves_terminal_runs_untouched(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    db.update_init_run("run-1", status="completed")
    assert db.reconcile_init_runs_on_boot() == 0
    assert db.get_latest_init_run()["status"] == "completed"


def test_update_init_run_rejects_unknown_column(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.try_reserve_init_starting("run-1")
    with pytest.raises(ValueError, match="unknown columns"):
        db.update_init_run("run-1", bogus="x")
