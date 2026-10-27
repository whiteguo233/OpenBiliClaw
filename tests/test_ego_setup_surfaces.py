"""Explicit Ego setup API/CLI actions; status reads never start a browser."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openbiliclaw.api import custom_sources


def _fixture(monkeypatch, tmp_path, *, managed=True, supported=True, deployment=False):
    state = {
        "managed": managed,
        "setup_state": "idle" if supported else "unsupported",
        "phase": "",
        "installed": False,
        "available": False,
        "platform_supported": supported,
        "detail": "Managed environment has not been prepared",
    }
    started, release = threading.Event(), threading.Event()
    calls = []

    async def prepare():
        calls.append("prepare")
        state.update(setup_state="preparing", phase="onboarding", detail="Preparing private host")
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        state.update(setup_state="ready", phase="ready", installed=True, available=True)
        return dict(state)

    setup = SimpleNamespace(
        status=lambda: dict(state), prepare=prepare, schedule_idle_stop=lambda: None
    )
    monkeypatch.setattr(custom_sources, "_ego_setup_manager", lambda _: setup, raising=False)
    service = SimpleNamespace(
        database=SimpleNamespace(_db_path=tmp_path / "test.db"),
        list_sources=lambda: [],
        executor=lambda name: SimpleNamespace(
            availability=lambda: {
                "executor": name,
                "installed": True,
                "available": True,
                "platform_supported": supported,
                "detail": "Actual external/native connection",
            }
        ),
    )
    app = FastAPI()
    app.include_router(
        custom_sources.create_custom_sources_router(
            lambda: service, prepare_ego_on_startup=deployment
        )
    )
    return app, calls, started, release


def test_get_reports_setup_without_starting_it_or_overwriting_executor_facts(monkeypatch, tmp_path):
    app, calls, _, _ = _fixture(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ego = next(
            row
            for row in client.get("/api/custom-sources").json()["executors"]
            if row["id"] == "ego"
        )
    assert calls == []
    assert ego["setup_state"] == "idle" and ego["managed"] is True
    assert ego["installed"] and ego["available"]
    assert ego["detail"] == "Actual external/native connection"


def test_prepare_is_explicit_and_deduplicates_active_requests(monkeypatch, tmp_path):
    app, calls, started, release = _fixture(monkeypatch, tmp_path)
    with TestClient(app) as client:
        first = client.post("/api/custom-sources/executors/ego/prepare")
        assert first.status_code == 202
        assert first.json()["setup_state"] == "preparing"
        assert started.wait(1)
        second = client.post("/api/custom-sources/executors/ego/prepare")
        assert second.status_code == 202 and calls == ["prepare"]
        ego = next(
            row
            for row in client.get("/api/custom-sources").json()["executors"]
            if row["id"] == "ego"
        )
        assert ego["setup_state"] == "preparing" and ego["phase"] == "onboarding"
        assert ego["detail"] == "Preparing private host"
        release.set()


def test_deployment_prepares_before_any_chat_without_blocking_http(monkeypatch, tmp_path):
    app, calls, started, release = _fixture(monkeypatch, tmp_path, deployment=True)
    assert calls == []  # Building the app alone still has no install side effects.
    with TestClient(app) as client:
        assert started.wait(1)
        assert not release.is_set()
        response = client.get("/api/custom-sources")
        assert response.status_code == 200
        ego = next(row for row in response.json()["executors"] if row["id"] == "ego")
        assert ego["setup_state"] == "preparing"
        assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 202
        assert calls == ["prepare"]
        release.set()


def test_prepared_offline_deployment_does_not_reinstall_or_start_host(monkeypatch, tmp_path):
    app, calls, _, _ = _fixture(monkeypatch, tmp_path, deployment=True)
    setup = custom_sources._ego_setup_manager(None)
    state = setup.status()
    state.update(setup_state="ready", installed=True, available=False, connection_state="offline")
    setup.status = lambda: dict(state)
    with TestClient(app) as client:
        assert client.get("/api/custom-sources").status_code == 200
    assert calls == []


def test_deployment_does_not_touch_external_or_unsupported_hosts(monkeypatch, tmp_path):
    for managed, supported in [(False, True), (True, False)]:
        app, calls, _, _ = _fixture(
            monkeypatch, tmp_path, deployment=True, managed=managed, supported=supported
        )
        with TestClient(app) as client:
            assert client.get("/api/custom-sources").status_code == 200
        assert calls == []


def test_failed_deployment_retries_once_at_next_start_and_remains_readable(monkeypatch, tmp_path):
    app, calls, started, _ = _fixture(monkeypatch, tmp_path, deployment=True)
    setup = custom_sources._ego_setup_manager(None)
    state = setup.status()
    state.update(setup_state="failed", detail="ego_setup_interrupted")
    setup.status = lambda: dict(state)

    async def fail():
        calls.append("prepare")
        started.set()
        raise RuntimeError("private-local-path-and-credentials")

    setup.prepare = fail
    with TestClient(app) as client:
        assert started.wait(1)
        for _ in range(3):
            ego = next(
                row
                for row in client.get("/api/custom-sources").json()["executors"]
                if row["id"] == "ego"
            )
            assert ego["setup_state"] == "failed"
            assert "private-local" not in ego["detail"]
        assert calls == ["prepare"]


def test_deployment_shutdown_cancels_its_pending_preparation(monkeypatch, tmp_path):
    app, _, started, _ = _fixture(monkeypatch, tmp_path, deployment=True)
    setup = custom_sources._ego_setup_manager(None)
    cancelled = threading.Event()

    async def prepare():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    setup.prepare = prepare
    with TestClient(app):
        assert started.wait(1)
    assert cancelled.wait(1)


def test_external_host_cannot_be_prepared_or_have_its_status_overwritten(monkeypatch, tmp_path):
    app, calls, _, _ = _fixture(monkeypatch, tmp_path, managed=False)
    with TestClient(app) as client:
        ego = next(
            row
            for row in client.get("/api/custom-sources").json()["executors"]
            if row["id"] == "ego"
        )
        assert ego["managed"] is False and ego["available"] is True
        assert ego["detail"] == "Actual external/native connection"
        assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 409
    assert calls == []


def test_unsupported_host_does_not_start_prepare(monkeypatch, tmp_path):
    app, calls, _, _ = _fixture(monkeypatch, tmp_path, supported=False)
    with TestClient(app) as client:
        assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 409
    assert calls == []


def test_unexpected_prepare_failure_is_safe_and_retryable(monkeypatch, tmp_path):
    app, calls, _, _ = _fixture(monkeypatch, tmp_path)
    setup = custom_sources._ego_setup_manager(None)
    failed = threading.Event()

    async def fail():
        calls.append("prepare")
        failed.set()
        raise RuntimeError("private-local-path-and-credentials")

    setup.prepare = fail
    with TestClient(app) as client:
        endpoint = "/api/custom-sources/executors/ego/prepare"
        assert client.post(endpoint).status_code == 202
        assert failed.wait(1)
        ego = next(
            row
            for row in client.get("/api/custom-sources").json()["executors"]
            if row["id"] == "ego"
        )
        assert ego["setup_state"] == "failed"
        assert "RuntimeError" in ego["detail"]
        assert "private-local" not in ego["detail"]
        failed.clear()
        assert client.post(endpoint).status_code == 202
        assert failed.wait(1) and calls == ["prepare", "prepare"]


def test_server_shutdown_cancels_its_prepare_job(monkeypatch, tmp_path):
    app, _, started, _ = _fixture(monkeypatch, tmp_path)
    setup = custom_sources._ego_setup_manager(None)
    cancelled = threading.Event()

    async def prepare():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    setup.prepare = prepare
    with TestClient(app) as client:
        assert client.post("/api/custom-sources/executors/ego/prepare").status_code == 202
        assert started.wait(1)
    assert cancelled.wait(1)


def test_cli_ego_status_is_readonly_and_prepare_is_explicit(monkeypatch):
    from openbiliclaw import cli_custom_sources

    calls = []

    async def prepare():
        calls.append("prepare")
        return {"setup_state": "ready", "available": True}

    setup = SimpleNamespace(status=Mock(return_value={"setup_state": "idle"}), prepare=prepare)
    monkeypatch.setattr(cli_custom_sources, "_ego_setup", lambda: setup, raising=False)
    runner = CliRunner()
    status = runner.invoke(cli_custom_sources.sources_app, ["ego-status"])
    assert status.exit_code == 0 and calls == []
    result = runner.invoke(cli_custom_sources.sources_app, ["prepare-ego"])
    assert result.exit_code == 0 and calls == ["prepare"]
    assert '"ready"' in result.stdout


def test_cli_ego_prepare_failure_has_a_nonzero_exit(monkeypatch):
    from openbiliclaw import cli_custom_sources

    async def prepare():
        return {"setup_state": "failed", "available": False, "detail": "safe reason"}

    monkeypatch.setattr(cli_custom_sources, "_ego_setup", lambda: SimpleNamespace(prepare=prepare))
    result = CliRunner().invoke(cli_custom_sources.sources_app, ["prepare-ego"])
    assert result.exit_code == 1 and "safe reason" in result.stdout
