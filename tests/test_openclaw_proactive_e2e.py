"""End-to-end tests for proactive delight and interest probe flows."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import uvicorn

from openbiliclaw.api.app import create_app
from openbiliclaw.integrations.openclaw import cli as openclaw_cli
from openbiliclaw.runtime.events import RuntimeEventHub
from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.soul.speculator import SpeculativeInterest, SpeculativeSpecific


def _unused_tcp_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _run_server(app: Any) -> Any:
    port = _unused_tcp_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while not server.started:
        if time.time() >= deadline:
            raise RuntimeError("Timed out waiting for test server to start.")
        time.sleep(0.01)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def _wait_for(
    predicate: Any,
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for asynchronous condition.")


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.runtime_state: dict[str, object] = {
            "initialized_at": "2026-04-23T10:00:00",
            "probed_domains": {},
            "probed_axes": {},
            "last_delight_notification_at": "",
        }
        self.cognition_updates: list[dict[str, object]] = []

    def load_discovery_runtime_state(self) -> dict[str, object]:
        return dict(self.runtime_state)

    def save_discovery_runtime_state(self, state: dict[str, object]) -> None:
        self.runtime_state = dict(state)

    def get_layer(self, name: str) -> Any:
        return {}

    def load_cognition_updates(self) -> list[dict[str, object]]:
        return list(self.cognition_updates)

    def save_cognition_updates(self, updates: list[dict[str, object]]) -> None:
        self.cognition_updates = list(updates)


class _FakeDatabase:
    def __init__(self) -> None:
        self.delight_candidate: dict[str, object] | None = {
            "bvid": "BV1DELIGHT42",
            "title": "复杂系统里那些意外的秩序",
            "delight_reason": "这条会从你喜欢的结构感切到一个更偏跨域的角度。",
            "delight_score": 0.94,
            "delight_hook": "跨域惊喜",
            "cover_url": "https://example.com/delight.jpg",
            "content_url": "https://www.bilibili.com/video/BV1DELIGHT42",
            "source_platform": "bilibili",
        }
        self.marked_delight_bvids: list[str] = []

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> dict[str, object] | None:
        candidate = self.delight_candidate
        if candidate is None:
            return None
        if float(candidate["delight_score"]) < min_delight_score:
            return None
        return dict(candidate)

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        candidate = self.get_delight_candidate(min_delight_score=min_delight_score)
        return [] if candidate is None else [candidate]

    def mark_delight_notified(self, bvid: str) -> None:
        self.marked_delight_bvids.append(bvid)

    def count_delight_candidates(self, *, min_delight_score: float = 0.85) -> int:
        candidate = self.get_delight_candidate(min_delight_score=min_delight_score)
        return 0 if candidate is None else 1


class _FakeSpeculator:
    def __init__(self) -> None:
        self.specs = [
            SpeculativeInterest(
                domain="建筑美学",
                category="人文",
                reason="你最近会反复停在结构和空间关系上。",
                experience_mode="aesthetic",
                entry_load="light",
                confidence=0.81,
                weight=0.74,
                confirmation_count=0,
                specifics=[
                    SpeculativeSpecific(name="参数化设计"),
                    SpeculativeSpecific(name="混凝土美学"),
                ],
            )
        ]
        self.confirmed_domains: list[str] = []
        self.confirmation_sources: list[str] = []
        self.rejected_domains: list[tuple[str, int]] = []
        self.observed_events: list[list[dict[str, object]]] = []
        self.force_tick_profiles: list[Any] = []

    def get_active_speculations(self) -> list[SpeculativeInterest]:
        return list(self.specs)

    def user_confirm_speculation(
        self,
        domain: str,
        *,
        confirmation_source: str = "probe_confirmed",
    ) -> bool:
        self.confirmed_domains.append(domain)
        self.confirmation_sources.append(confirmation_source)
        return True

    def user_reject_speculation(self, domain: str, cooldown_days: int = 30) -> bool:
        self.rejected_domains.append((domain, cooldown_days))
        return True

    def observe(self, events: list[dict[str, object]]) -> None:
        self.observed_events.append(events)

    def force_tick(self, profile: Any) -> None:
        self.force_tick_profiles.append(profile)


class _FakeSoulEngine:
    def __init__(self, speculator: _FakeSpeculator) -> None:
        self._speculator = speculator

    async def get_profile(self) -> dict[str, object]:
        return {"personality_portrait": "E2E 测试画像"}


def _build_runtime_controller(
    *,
    memory_manager: _FakeMemoryManager,
    database: _FakeDatabase,
    soul_engine: _FakeSoulEngine,
    event_hub: RuntimeEventHub,
) -> ContinuousRefreshController:
    return ContinuousRefreshController(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        discovery_engine=SimpleNamespace(),
        recommendation_engine=SimpleNamespace(),
        event_hub=event_hub,
    )


@pytest.mark.asyncio
async def test_openclaw_listen_streams_delight_and_acknowledges_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openbiliclaw-e2e"),
        bilibili=SimpleNamespace(cookie=""),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    event_hub = RuntimeEventHub()
    memory_manager = _FakeMemoryManager()
    database = _FakeDatabase()
    soul_engine = _FakeSoulEngine(_FakeSpeculator())
    runtime_controller = _build_runtime_controller(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        event_hub=event_hub,
    )
    app = create_app(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        dialogue=object(),
        runtime_controller=runtime_controller,
        runtime_event_hub=event_hub,
    )

    with _run_server(app) as port:
        monkeypatch.setattr(
            openclaw_cli,
            "_DELIGHT_ACK_URL",
            f"http://127.0.0.1:{port}/api/delight/sent",
        )
        payloads: list[dict[str, object]] = []
        monkeypatch.setattr(openclaw_cli, "_print_payload", payloads.append)

        listen_task = asyncio.create_task(
            openclaw_cli._listen_ws(
                f"ws://127.0.0.1:{port}/api/runtime-stream",
                frozenset({"delight.candidate"}),
            )
        )
        try:
            await _wait_for(lambda: len(payloads) >= 1)

            await runtime_controller._publish_delight_if_available()

            await _wait_for(lambda: len(payloads) >= 2)
            await _wait_for(lambda: database.marked_delight_bvids == ["BV1DELIGHT42"])
        finally:
            listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await listen_task

    assert payloads[0]["ok"] is True
    assert payloads[0]["data"]["status"] == "connected"
    event = payloads[1]["data"]
    assert event["type"] == "delight.candidate"
    assert event["bvid"] == "BV1DELIGHT42"
    assert event["delight_hook"] == "跨域惊喜"
    assert event["source_platform"] == "bilibili"
    assert memory_manager.runtime_state["last_delight_notification_at"] != ""


@pytest.mark.asyncio
async def test_interest_probe_trigger_and_confirm_flow_publish_full_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_config = SimpleNamespace(
        data_path=Path("/tmp/openbiliclaw-e2e"),
        bilibili=SimpleNamespace(cookie=""),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    event_hub = RuntimeEventHub()
    memory_manager = _FakeMemoryManager()
    database = _FakeDatabase()
    speculator = _FakeSpeculator()
    soul_engine = _FakeSoulEngine(speculator)
    runtime_controller = _build_runtime_controller(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        event_hub=event_hub,
    )
    app = create_app(
        memory_manager=memory_manager,
        database=database,
        soul_engine=soul_engine,
        dialogue=object(),
        runtime_controller=runtime_controller,
        runtime_event_hub=event_hub,
    )

    with _run_server(app) as port:
        payloads: list[dict[str, object]] = []
        monkeypatch.setattr(openclaw_cli, "_print_payload", payloads.append)

        listen_task = asyncio.create_task(
            openclaw_cli._listen_ws(
                f"ws://127.0.0.1:{port}/api/runtime-stream",
                frozenset({"interest.probe", "interest.confirmed"}),
            )
        )
        try:
            await _wait_for(lambda: len(payloads) >= 1)

            async with httpx.AsyncClient() as client:
                trigger_response = await client.post(
                    f"http://127.0.0.1:{port}/api/interest-probes/trigger"
                )
                assert trigger_response.status_code == 200
                assert trigger_response.json()["ok"] is True

                await _wait_for(
                    lambda: any(
                        payload.get("data", {}).get("type") == "interest.probe"
                        for payload in payloads[1:]
                    )
                )

                confirm_response = await client.post(
                    f"http://127.0.0.1:{port}/api/interest-probes/respond",
                    json={"domain": "建筑美学", "response": "confirm"},
                )
                assert confirm_response.status_code == 200
                assert confirm_response.json() == {
                    "ok": True,
                    "action": "confirmed",
                    "domain": "建筑美学",
                }

            await _wait_for(
                lambda: any(
                    payload.get("data", {}).get("type") == "interest.confirmed"
                    for payload in payloads[1:]
                )
            )
        finally:
            listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await listen_task

    probe_events = [
        payload["data"]
        for payload in payloads[1:]
        if payload.get("data", {}).get("type") == "interest.probe"
    ]
    confirm_events = [
        payload["data"]
        for payload in payloads[1:]
        if payload.get("data", {}).get("type") == "interest.confirmed"
    ]

    assert len(probe_events) == 1
    assert probe_events[0]["domain"] == "建筑美学"
    assert probe_events[0]["experience_mode"] == "aesthetic"
    assert probe_events[0]["entry_load"] == "light"
    assert "参数化设计" in probe_events[0]["question"]
    assert len(confirm_events) == 1
    assert confirm_events[0]["domain"] == "建筑美学"
    assert speculator.confirmed_domains == ["建筑美学"]
    assert speculator.confirmation_sources == ["probe_confirmed"]
    assert speculator.force_tick_profiles == [{"personality_portrait": "E2E 测试画像"}]
    assert "建筑美学" in memory_manager.runtime_state["probed_domains"]
    assert "aesthetic|light" in memory_manager.runtime_state["probed_axes"]
    assert any("已加入画像" in item["summary"] for item in memory_manager.cognition_updates)
