"""Deployment and explicit preparation must reclaim a host without any sessions."""

import asyncio
import time
from contextlib import suppress
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.custom_sources import create_custom_sources_router
from openbiliclaw.sources import ego_setup
from openbiliclaw.sources.ego_setup import EgoSetupManager
from openbiliclaw.sources.workflow_service import WorkflowService
from openbiliclaw.storage.database import Database


@pytest.mark.parametrize("deployment", [False, True])
@pytest.mark.parametrize("prepared_before", [False, True])
@pytest.mark.parametrize("session_active", [False, True])
def test_preparation_reclaims_host_without_opening_a_session(
    tmp_path, monkeypatch, deployment, prepared_before, session_active
):
    monkeypatch.setattr(ego_setup.sys, "platform", "darwin")
    for key in ("BROWSER", "SERVER_NAME", "PROFILE"):
        monkeypatch.delenv(f"OPENBILICLAW_EGO_{key}", raising=False)
    monkeypatch.setenv("OPENBILICLAW_EGO_IDLE_STOP_SECONDS", "0.02")
    state = {"live": prepared_before, "prepared": prepared_before, "prepare_count": 0}

    def status(_):
        return {
            "managed": True,
            "platform_supported": True,
            "installed": state["prepared"],
            "setup_state": "ready" if state["prepared"] else "idle",
            "available": state["live"],
        }

    async def operate(manager, *, initialize):
        assert initialize is True
        manager.root.mkdir(parents=True, exist_ok=True)
        state["prepare_count"] += 1
        state.update(live=True, prepared=True)
        await asyncio.sleep(0)
        return status(manager)

    async def stop(manager, **kwargs):
        state["live"] = False
        return status(manager)

    monkeypatch.setattr(EgoSetupManager, "status", status)
    monkeypatch.setattr(EgoSetupManager, "_operate", operate)
    monkeypatch.setattr(EgoSetupManager, "stop", stop)
    database = Database(tmp_path / "test.db")
    database.initialize()
    service = WorkflowService(database)
    manager = EgoSetupManager(tmp_path)
    lease = manager.acquire_session_lease() if session_active else None
    app = FastAPI()
    app.include_router(
        create_custom_sources_router(lambda: service, prepare_ego_on_startup=deployment)
    )
    try:
        with TestClient(app) as client:
            if not deployment:
                assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 202
            deadline = time.monotonic() + 1
            while not state["prepared"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert state["prepared"], "The actual background preparation must have run"
            if lease is not None:
                time.sleep(0.1)
                assert state["live"], "Preparation cleanup must preserve another live session"
                manager.release_session_lease(lease)
                lease = None
                assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 202
            while state["live"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not state["live"], "An unused newly prepared host must exit after its idle grace"
            assert state["prepare_count"] == (0 if prepared_before else 1)
    finally:
        if lease is not None:
            manager.release_session_lease(lease)
        database.close()


async def test_preparation_registers_exit_cleanup_without_a_workflow_session(tmp_path, monkeypatch):
    monkeypatch.setattr(ego_setup.sys, "platform", "darwin")
    for key in ("BROWSER", "SERVER_NAME", "PROFILE"):
        monkeypatch.delenv(f"OPENBILICLAW_EGO_{key}", raising=False)
    manager = EgoSetupManager(tmp_path)
    callbacks = []
    monkeypatch.setattr(ego_setup.atexit, "register", callbacks.append)
    monkeypatch.setattr(manager, "_operate", AsyncMock(return_value={"available": True}))
    stop = Mock()
    monkeypatch.setattr(manager, "stop_sync", stop)
    await manager.prepare()
    task = ego_setup._IDLE_TASKS.pop(str(manager.root))
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert len(callbacks) == 1
    lease = manager.acquire_session_lease()
    try:
        callbacks[0]()
        stop.assert_not_called()
    finally:
        manager.release_session_lease(lease)
    callbacks[0]()
    stop.assert_called_once()
