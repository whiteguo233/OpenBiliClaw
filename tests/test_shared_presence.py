"""Cross-process extension presence and browser task delivery (issue #332)."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import (
    RuntimeContext,
    build_degraded_runtime_context,
    build_runtime_context,
)
from openbiliclaw.config import Config
from openbiliclaw.discovery.douyin import DouyinDiscoveryResult
from openbiliclaw.runtime.douyin_producer import DouyinDiscoveryProducer
from openbiliclaw.runtime.presence import PresenceTracker
from openbiliclaw.sources.dy_tasks import DyTaskQueue
from openbiliclaw.storage.database import Database


class Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value


def _worker_produce_task(path: str, send: Any) -> None:
    """Use spawn, matching Windows frozen workers rather than inherited memory."""
    clock = Clock()
    presence = PresenceTracker(shared_path=Path(path), now=clock)
    clock.value += 91
    database = Database(Path(path).parent / "tasks.db")
    database.initialize()
    queue = DyTaskQueue(database)

    async def get_profile() -> dict[str, str]:
        return {"profile": "fixture"}

    async def discover(profile: Any, options: Any) -> DouyinDiscoveryResult:
        assert queue.enqueue("search", {"keyword": "presence regression"}, daily_budget=0)
        return DouyinDiscoveryResult(items=[], cached=False, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=SimpleNamespace(get_profile=get_profile),
        discover=discover,
        presence=presence,
        min_interval_minutes=0,
    )
    try:
        send.send(asyncio.run(producer.produce_if_due(limit=1)))
    finally:
        database.close()
        send.close()


@pytest.mark.parametrize("publish", [False, True])
def test_api_websocket_unblocks_spawned_worker_and_task_claim(
    tmp_path, monkeypatch, publish
) -> None:
    """An API connection must reach a separate worker after its startup grace."""
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: Config(data_dir=str(tmp_path)))
    path = tmp_path / "extension_presence.json"
    database = Database(tmp_path / "tasks.db")
    database.initialize()
    app = create_app(memory_manager=object(), database=database, soul_engine=object())
    ctx = app.state.runtime_context
    ctx.presence = PresenceTracker(shared_path=path if publish else None)
    client = TestClient(app)
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_worker_produce_task, args=(str(path), send))
    try:
        with client.websocket_connect("/api/runtime-stream"):
            deadline = time.monotonic() + 2
            while ctx.presence.snapshot()["active_count"] != 1:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            process.start()
            send.close()
            assert receive.poll(30), "spawned producer did not respond"
            result = receive.recv()
            process.join(5)
            assert process.exitcode == 0
            response = client.get("/api/sources/dy/next-task")
            if publish:
                assert result["reason"] != "extension_absent"
                assert response.status_code == 200
                assert response.json()["keyword"] == "presence regression"
            else:
                assert result == {"discovered": 0, "reason": "extension_absent"}
                assert response.status_code == 204
    finally:
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(5)
        receive.close()
        send.close()
        client.close()
        database.close()


@pytest.mark.parametrize("degraded", [False, True])
def test_worker_construction_does_not_overwrite_api_presence(
    tmp_path, monkeypatch, degraded
) -> None:
    monkeypatch.setattr(RuntimeContext, "_rebuild_components", lambda self, config: None)
    config = Config(data_dir=str(tmp_path))

    def build() -> RuntimeContext:
        factory = build_degraded_runtime_context if degraded else build_runtime_context
        return factory(config, database=object(), memory_manager=object(), event_hub=object())

    api = build()
    api.presence.on_connect()
    path = tmp_path / "runtime" / "extension_presence.json"
    before = path.read_bytes()
    worker = build()
    assert path.read_bytes() == before
    worker.presence._now = lambda: time.monotonic() + 91
    assert worker.presence.is_present(90)
    assert worker.presence.snapshot()["active_count"] == 0


@pytest.mark.parametrize("degraded", [False, True])
def test_idle_api_websocket_renews_shared_presence(tmp_path, monkeypatch, degraded) -> None:
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: Config(data_dir=str(tmp_path)))
    monkeypatch.setattr("openbiliclaw.api.app._RUNTIME_STREAM_HEARTBEAT_SECONDS", 0.02)
    path = tmp_path / "presence.json"
    clock = Clock()
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    ctx = app.state.runtime_context
    ctx.presence = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    ctx.degraded = degraded
    client = TestClient(app)
    with client.websocket_connect("/api/runtime-stream") as websocket:
        if degraded:
            assert websocket.receive_json()["type"] == "degraded"
        deadline = time.monotonic() + 2
        while not path.exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        clock.value += 100
        while json.loads(path.read_text())["updated_at"] != clock.value:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    assert json.loads(path.read_text())["active_count"] == 0
    client.close()


def test_shared_presence_expires_after_api_crash_and_recovers_on_heartbeat(tmp_path) -> None:
    clock = Clock()
    path = tmp_path / "presence.json"
    api = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    worker = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    api.on_connect()
    clock.value += 91
    assert not worker.is_present(90)
    api.heartbeat()
    assert worker.is_present(90)
    # A worker must not keep an API lease alive by reading or heartbeating it.
    clock.value += 61
    worker.heartbeat()
    assert not worker.is_present(90)
    api.heartbeat()
    assert worker.is_present(90)


def test_shared_presence_preserves_multiple_clients_disconnect_grace_and_reconnect(
    tmp_path,
) -> None:
    clock = Clock()
    path = tmp_path / "presence.json"
    api = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    worker = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    api.on_connect()
    api.on_connect()
    api.on_disconnect()
    assert worker.is_present(0)
    assert json.loads(path.read_text())["active_count"] == 1
    api.on_disconnect()
    assert not worker.is_present(0)
    assert worker.is_present(5)
    before = path.read_bytes()
    clock.value += 6
    api.heartbeat()
    api.on_disconnect()
    assert path.read_bytes() == before
    assert not worker.is_present(5)
    api.on_connect()
    assert worker.is_present(0)


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        '{"version":2,"active_count":1,"updated_at":1000}',
        '{"version":1,"active_count":true,"updated_at":1000}',
        '{"version":1,"active_count":-1,"updated_at":1000}',
        '{"version":1,"active_count":1,"updated_at":"1000"}',
        '{"version":1,"active_count":1,"updated_at":NaN}',
        '{"version":1,"active_count":1,"updated_at":Infinity}',
        '{"version":1,"active_count":1,"updated_at":1001}',
    ],
)
def test_invalid_shared_presence_does_not_claim_an_online_extension(tmp_path, payload) -> None:
    clock = Clock()
    path = tmp_path / "presence.json"
    path.write_text(payload)
    worker = PresenceTracker(shared_path=path, now=clock, wall_now=clock)
    assert not worker.is_present(0)


def test_shared_write_failure_keeps_local_presence_and_warns(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "presence.json"

    def fail_replace(*args: Any) -> None:
        raise PermissionError("fixture permission failure")

    monkeypatch.setattr("openbiliclaw.runtime.presence.os.replace", fail_replace)
    api = PresenceTracker(shared_path=path)
    api.on_connect()
    assert api.is_present(0)
    assert "Unable to publish extension presence" in caplog.text
    assert list(tmp_path.iterdir()) == []
