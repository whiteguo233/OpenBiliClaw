"""Regression tests for issue #250.

Background child processes (``openbiliclaw.worker`` /
``openbiliclaw.discovery_worker``) must survive a config that cannot build any
LLM instance instead of raising into the PyInstaller bootloader (windowed
builds popped two error dialogs) and leaving every background loop dead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest

from openbiliclaw import discovery_worker as discovery_worker_module
from openbiliclaw import recommendation_server as recommendation_server_module
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.llm.registry import RegistryBuildError
from openbiliclaw.runtime.worker_status import WorkerStatusStore
from openbiliclaw.worker import main as worker_main

_NO_LLM = "No LLM instances are available from the current configuration."


def test_wait_for_llm_registry_retries_until_buildable() -> None:
    calls: list[int] = []

    def probe() -> None:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise RegistryBuildError(_NO_LLM)

    asyncio.run(worker_main._wait_for_llm_registry(retry_interval_seconds=0.01, probe=probe))

    assert calls == [1, 2, 3]


def test_run_full_worker_keeps_heartbeat_while_waiting_for_llm(tmp_path, monkeypatch) -> None:
    buildable = False
    probe_calls: list[int] = []

    def fake_build_llm_registry(_config: object) -> object:
        probe_calls.append(len(probe_calls) + 1)
        if not buildable:
            raise RegistryBuildError(_NO_LLM)
        return object()

    monkeypatch.setattr("openbiliclaw.llm.registry.build_llm_registry", fake_build_llm_registry)
    monkeypatch.setattr(worker_main, "load_config", lambda: SimpleNamespace(data_path=tmp_path))
    monkeypatch.setattr(worker_main, "DEFAULT_LLM_PROBE_RETRY_SECONDS", 0.05)

    started = asyncio.Event()

    class FakeContext:
        soul_engine = None
        database = None
        task_registry = None

        async def restart_background_tasks(self, app: object) -> None:
            started.set()

    class FakeFeedbackScheduler:
        def __init__(self, **kwargs: object) -> None: ...

        def start_periodic(self) -> None: ...

        async def close(self) -> None: ...

    monkeypatch.setattr(
        "openbiliclaw.api.runtime_context.build_runtime_context",
        lambda config: FakeContext(),
    )
    monkeypatch.setattr(
        "openbiliclaw.runtime.feedback_scheduler.EventProcessingScheduler",
        FakeFeedbackScheduler,
    )

    async def scenario() -> None:
        task = asyncio.create_task(worker_main.run_full_worker())
        try:
            status_path = tmp_path / "runtime" / "worker_status.json"
            for _ in range(500):
                if status_path.exists():
                    break
                await asyncio.sleep(0.01)
            assert status_path.exists(), (
                "worker heartbeat must exist while the LLM probe is failing"
            )
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            assert payload["mode"] == "full"
            assert probe_calls and not started.is_set()

            nonlocal buildable
            buildable = True
            await asyncio.wait_for(started.wait(), timeout=5.0)
            assert len(probe_calls) >= 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_discovery_worker_retries_instead_of_raising_on_missing_llm() -> None:
    probe_calls: list[int] = []
    sleeps: list[float] = []
    ran: list[str] = []

    def probe() -> None:
        probe_calls.append(len(probe_calls) + 1)
        if len(probe_calls) < 3:
            raise RegistryBuildError(_NO_LLM)

    async def run_forever() -> None:
        ran.append("started")

    app = SimpleNamespace(
        state=SimpleNamespace(
            runtime_context=SimpleNamespace(
                runtime_controller=SimpleNamespace(run_forever=run_forever)
            )
        )
    )

    discovery_worker_module.run_discovery_worker(
        retry_interval_seconds=0.01,
        probe=probe,
        app_factory=lambda: app,
        sleep=sleeps.append,
    )

    assert len(probe_calls) == 3
    assert len(sleeps) == 2
    assert ran == ["started"]


def test_recommendation_server_waits_for_buildable_llm() -> None:
    probe_calls: list[int] = []
    sleeps: list[float] = []

    def probe() -> None:
        probe_calls.append(len(probe_calls) + 1)
        if len(probe_calls) < 3:
            raise RegistryBuildError(_NO_LLM)

    recommendation_server_module.wait_for_buildable_llm(
        retry_interval_seconds=0.01,
        probe=probe,
        sleep=sleeps.append,
    )

    assert len(probe_calls) == 3
    assert len(sleeps) == 2


def test_discovery_worker_retries_when_runtime_controller_is_missing() -> None:
    closed: list[str] = []

    class FakeDatabase:
        def close(self) -> None:
            closed.append("closed")

    async def run_forever() -> None:
        return None

    degraded = SimpleNamespace(
        state=SimpleNamespace(
            runtime_context=SimpleNamespace(
                database=FakeDatabase(),
                degraded=True,
                degraded_reason="llm_registry_unavailable",
                runtime_controller=None,
            )
        )
    )
    healthy = SimpleNamespace(
        state=SimpleNamespace(
            runtime_context=SimpleNamespace(
                runtime_controller=SimpleNamespace(run_forever=run_forever)
            )
        )
    )
    apps = [degraded, healthy]
    sleeps: list[float] = []

    discovery_worker_module.run_discovery_worker(
        retry_interval_seconds=0.01,
        probe=lambda: None,
        app_factory=lambda: apps.pop(0),
        sleep=sleeps.append,
    )

    assert closed == ["closed"]
    assert len(sleeps) == 1


def test_runtime_context_warns_only_for_a_stale_worker_heartbeat(
    tmp_path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("OPENBILICLAW_FULL_WORKER", "1")
    ctx = RuntimeContext(config=SimpleNamespace(data_path=tmp_path))
    logger_name = "openbiliclaw.api.runtime_context"

    # No heartbeat file yet: the worker may still be starting — no warning.
    with caplog.at_level(logging.WARNING, logger=logger_name):
        ctx.warn_if_full_worker_heartbeat_stale()
    assert "stale" not in caplog.text

    store = WorkerStatusStore(
        tmp_path / "runtime" / "worker_status.json",
        max_age_seconds=30.0,
    )
    now = time.time()
    store.write(mode="full", pid=7, started_at=now - 100.0, heartbeat_at=now)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger_name):
        ctx.warn_if_full_worker_heartbeat_stale()
    assert "stale" not in caplog.text

    store.write(mode="full", pid=7, started_at=now - 100.0, heartbeat_at=now - 100.0)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=logger_name):
        ctx.warn_if_full_worker_heartbeat_stale()
    assert "stale" in caplog.text
    assert "worker_running=false" in caplog.text
