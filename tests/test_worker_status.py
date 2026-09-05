"""Tests for the cross-process worker heartbeat/status store."""

from __future__ import annotations

from openbiliclaw.runtime.worker_status import WorkerStatusStore


def test_worker_status_store_writes_and_reads(tmp_path) -> None:
    store = WorkerStatusStore(tmp_path / "worker_status.json", now=lambda: 1000.0)
    store.write(
        mode="full",
        pid=1234,
        started_at=900.0,
        heartbeat_at=999.0,
    )

    data = store.read()
    assert data is not None
    assert data["version"] == 1
    assert data["mode"] == "full"
    assert data["pid"] == 1234
    assert data["started_at"] == 900.0
    assert data["heartbeat_at"] == 999.0


def test_worker_status_store_reports_healthy_and_stale(tmp_path) -> None:
    now = [1000.0]
    store = WorkerStatusStore(
        tmp_path / "worker_status.json",
        max_age_seconds=30.0,
        now=lambda: now[0],
    )
    assert store.status_payload() == {
        "worker_running": False,
        "worker_mode": "none",
        "worker_pid": None,
        "worker_started_at": "",
        "worker_last_heartbeat_at": "",
        "worker_heartbeat_age_seconds": -1.0,
    }

    store.write(mode="full", pid=7, started_at=900.0, heartbeat_at=990.0)
    payload = store.status_payload()
    assert payload["worker_running"] is True
    assert payload["worker_mode"] == "full"
    assert payload["worker_pid"] == 7
    assert payload["worker_heartbeat_age_seconds"] == 10.0

    now[0] = 1040.0
    payload = store.status_payload()
    assert payload["worker_running"] is False
    assert payload["worker_heartbeat_age_seconds"] == 50.0
