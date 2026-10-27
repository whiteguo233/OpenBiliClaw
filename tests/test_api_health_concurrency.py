"""Health diagnostics must not stall foreground API requests."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import httpx

from openbiliclaw.api import app as app_module

if TYPE_CHECKING:
    import pytest


async def test_lan_probe_yields_http_loop_and_coalesces_health_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = 0

    def detect() -> str:
        nonlocal calls
        calls += 1
        started.set()
        # A bounded escape keeps the regression safe even when the old code
        # blocks this test's event loop and prevents its finally from running.
        release.wait(3)
        finished.set()
        return "192.168.1.7"

    monkeypatch.setattr(app_module, "_detect_lan_ip", detect)
    app = app_module.create_app(memory_manager=object(), database=object(), soul_engine=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        requests = [asyncio.create_task(client.get("/api/health")) for _ in range(3)]
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert not finished.is_set(), "LAN detection blocked the HTTP event loop"
            ping = await asyncio.wait_for(client.get("/api/ping"), timeout=1)
            assert ping.status_code == 200
            assert not finished.is_set(), "ping must finish while LAN detection is blocked"
            assert calls == 1
        finally:
            release.set()
            responses = await asyncio.gather(*requests)
        assert all(response.json()["lan_ip"] == "192.168.1.7" for response in responses)
        assert (await client.get("/api/health")).json()["lan_ip"] == "192.168.1.7"
        assert calls == 1


async def test_fresh_qr_probe_wins_over_inflight_health_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def detect() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            release.wait(3)
            return "192.168.1.7"
        return "192.168.31.98"

    monkeypatch.setattr(app_module, "_detect_lan_ip", detect)
    app = app_module.create_app(memory_manager=object(), database=object(), soul_engine=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        health = asyncio.create_task(client.get("/api/health"))
        qr = None
        try:
            assert await asyncio.to_thread(started.wait, 1)
            qr = asyncio.create_task(client.get("/api/qr-info"))
            await asyncio.sleep(0.01)
            assert calls == 1, "fresh detection must follow the earlier health probe"
            assert (await client.get("/api/ping")).status_code == 200
        finally:
            release.set()
            await health
            if qr is not None:
                qr_response = await qr
        assert qr is not None
        assert qr_response.json()["lan_ip"] == "192.168.31.98"
        assert (await client.get("/api/health")).json()["lan_ip"] == "192.168.31.98"
        assert calls == 2


async def test_lan_probe_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def detect() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("interface enumeration failed")
        return "192.168.1.7"

    monkeypatch.setattr(app_module, "_detect_lan_ip", detect)
    app = app_module.create_app(memory_manager=object(), database=object(), soul_engine=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1",
    ) as client:
        assert (await client.get("/api/health")).status_code == 500
        assert (await client.get("/api/health")).json()["lan_ip"] == "192.168.1.7"
        assert (await client.get("/api/health")).json()["lan_ip"] == "192.168.1.7"
        assert calls == 2
