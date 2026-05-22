"""Tests for extension presence tracking and background LLM gating."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from openbiliclaw.runtime.presence import PresenceTracker, background_llm_work_allowed


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_presence_tracker_starts_with_startup_grace() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)

    assert tracker.is_present(grace_seconds=90) is True

    clock.advance(91)

    assert tracker.is_present(grace_seconds=90) is False


def test_presence_tracker_connect_and_disconnect_record_final_disconnect() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)

    tracker.on_connect()
    clock.advance(5)

    assert tracker.is_present(grace_seconds=90) is True
    assert tracker.snapshot()["active_count"] == 1

    tracker.on_disconnect()
    snapshot = tracker.snapshot()

    assert snapshot["active_count"] == 0
    assert snapshot["last_disconnect_at"] == clock()
    assert snapshot["seconds_since_disconnect"] == 0


def test_presence_tracker_keeps_present_until_all_clients_disconnect() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)

    tracker.on_connect()
    tracker.on_connect()
    tracker.on_disconnect()

    assert tracker.snapshot()["active_count"] == 1
    assert tracker.is_present(grace_seconds=1) is True


def test_presence_tracker_grace_window_after_final_disconnect() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)

    tracker.on_connect()
    tracker.on_disconnect()

    assert tracker.is_present(grace_seconds=10) is True

    clock.advance(11)

    assert tracker.is_present(grace_seconds=10) is False


@pytest.mark.asyncio
async def test_presence_tracker_is_safe_for_concurrent_connect_disconnect_pairs() -> None:
    tracker = PresenceTracker(now=FakeClock())

    async def connect_then_disconnect() -> None:
        tracker.on_connect()
        await asyncio.sleep(0)
        tracker.on_disconnect()

    await asyncio.gather(*(connect_then_disconnect() for _ in range(200)))

    assert tracker.snapshot()["active_count"] == 0


def test_presence_tracker_extra_disconnect_does_not_go_negative(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = PresenceTracker(now=FakeClock())

    with caplog.at_level(logging.WARNING):
        tracker.on_disconnect()

    assert tracker.snapshot()["active_count"] == 0
    assert "without active clients" in caplog.text


def test_background_llm_work_gate_blocks_when_scheduler_disabled() -> None:
    tracker = PresenceTracker(now=FakeClock())
    scheduler = SimpleNamespace(enabled=False, pause_on_extension_disconnect=False)

    assert background_llm_work_allowed(scheduler, tracker) is False


def test_background_llm_work_gate_ignores_presence_when_disconnect_policy_off() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = SimpleNamespace(enabled=True, pause_on_extension_disconnect=False)
    clock.advance(999)

    assert background_llm_work_allowed(scheduler, tracker) is True


def test_background_llm_work_gate_blocks_on_stale_presence() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = SimpleNamespace(
        enabled=True,
        pause_on_extension_disconnect=True,
        extension_disconnect_grace_seconds=10,
    )
    clock.advance(11)

    assert background_llm_work_allowed(scheduler, tracker) is False


def test_background_llm_work_gate_allows_active_or_grace_window_presence() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = SimpleNamespace(
        enabled=True,
        pause_on_extension_disconnect=True,
        extension_disconnect_grace_seconds=10,
    )

    tracker.on_connect()

    assert background_llm_work_allowed(scheduler, tracker) is True

    tracker.on_disconnect()
    clock.advance(9)

    assert background_llm_work_allowed(scheduler, tracker) is True


def test_background_llm_work_gate_defaults_invalid_grace_to_90_seconds() -> None:
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = SimpleNamespace(
        enabled=True,
        pause_on_extension_disconnect=True,
        extension_disconnect_grace_seconds="bad",
    )
    clock.advance(89)

    assert background_llm_work_allowed(scheduler, tracker) is True

    clock.advance(2)

    assert background_llm_work_allowed(scheduler, tracker) is False
