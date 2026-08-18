"""Tests for extension presence tracking and background LLM gating."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from openbiliclaw.runtime.presence import (
    PresenceTracker,
    background_llm_work_allowed,
    parse_peak_windows,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime whose Beijing wall time is hour:minute (UTC+8)."""
    return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc)


def _scheduler(**overrides: object) -> SimpleNamespace:
    base = {
        "enabled": True,
        "pause_on_extension_disconnect": False,
        "pause_during_peak_hours": True,
        "peak_hours": "09:00-12:00,14:00-18:00",
        "peak_refill_floor": 30,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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


# ── Peak-hour deferral (DeepSeek off-peak half-price) ──────────────────


def test_parse_peak_windows_default_spec() -> None:
    windows = parse_peak_windows("09:00-12:00,14:00-18:00")
    assert windows == [(9 * 60, 12 * 60), (14 * 60, 18 * 60)]


def test_parse_peak_windows_malformed_specs() -> None:
    assert parse_peak_windows("") == []
    assert parse_peak_windows("garbage") == []
    assert parse_peak_windows("09:00-12:00,not-a-window") == [(9 * 60, 12 * 60)]
    assert parse_peak_windows("25:00-26:00") == []
    assert parse_peak_windows("12:00-09:00") == []  # start >= end rejected
    assert parse_peak_windows("09:00-09:00") == []


def test_peak_deferral_disabled_by_default() -> None:
    """Opt-in only: without pause_during_peak_hours, nothing changes."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = SimpleNamespace(
        enabled=True,
        pause_on_extension_disconnect=False,
        pause_during_peak_hours=False,
        peak_hours="09:00-12:00,14:00-18:00",
        peak_refill_floor=30,
    )
    # Beijing 10:00 (inside the morning peak) — still allowed when opt-out.
    assert (
        background_llm_work_allowed(
            scheduler, tracker, pool_available=100, now_utc=_utc(2)
        )
        is True
    )


def test_peak_deferral_blocks_non_refill_inside_peak() -> None:
    """Maintenance traffic (pool_available=None) is fully blocked in peak."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    # Beijing 10:00 = UTC 02:00.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(2)
        )
        is False
    )
    # Beijing 15:00 = UTC 07:00 (afternoon peak).
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(7)
        )
        is False
    )


def test_peak_deferral_allows_outside_peak() -> None:
    """Non-peak windows (incl. the 12:00-14:00 lunch gap) run normally."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    # Beijing 08:59 = UTC 00:59.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(0, 59)
        )
        is True
    )
    # Beijing 12:00 = UTC 04:00 — lunch gap, not peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(4)
        )
        is True
    )
    # Beijing 18:00 = UTC 10:00 — evening, not peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(10)
        )
        is True
    )
    # Beijing 23:00 = UTC 15:00 — night, not peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(15)
        )
        is True
    )


def test_peak_deferral_boundary_minutes() -> None:
    """Peak starts are inclusive, ends exclusive (09:00 in, 12:00 out)."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    # Beijing 09:00 = UTC 01:00 — start of morning peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(1)
        )
        is False
    )
    # Beijing 08:59 = UTC 00:59 — just before.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(0, 59)
        )
        is True
    )
    # Beijing 11:59 = UTC 03:59 — last minute of morning peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(3, 59)
        )
        is False
    )
    # Beijing 12:00 = UTC 04:00 — exactly the end, no longer peak.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, now_utc=_utc(4)
        )
        is True
    )


def test_peak_deferral_refill_keeps_emergency_floor() -> None:
    """Refill passes inside peak only when the pool is at/below the floor."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    peak_now = _utc(2)  # Beijing 10:00, inside morning peak.

    # Pool above the floor → refill deferred.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, pool_available=100, now_utc=peak_now
        )
        is False
    )
    # Pool exactly at the floor → still deferred (defer while >= floor).
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, pool_available=30, now_utc=peak_now
        )
        is False
    )
    # Pool below the floor → emergency refill allowed.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, pool_available=29, now_utc=peak_now
        )
        is True
    )
    # Pool empty → emergency refill allowed.
    assert (
        background_llm_work_allowed(
            _scheduler(), tracker, pool_available=0, now_utc=peak_now
        )
        is True
    )


def test_peak_deferral_floor_zero_blocks_all_refill_in_peak() -> None:
    """peak_refill_floor = 0 means peak fully blocks refill too."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = _scheduler(peak_refill_floor=0)
    peak_now = _utc(2)

    assert (
        background_llm_work_allowed(
            scheduler, tracker, pool_available=0, now_utc=peak_now
        )
        is False
    )
    assert (
        background_llm_work_allowed(
            scheduler, tracker, pool_available=5, now_utc=peak_now
        )
        is False
    )


def test_peak_deferral_custom_windows() -> None:
    """A custom peak spec is honoured exactly."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = _scheduler(peak_hours="20:00-23:00")
    # Beijing 22:00 = UTC 14:00 — inside custom peak.
    assert (
        background_llm_work_allowed(
            scheduler, tracker, now_utc=_utc(14)
        )
        is False
    )
    # Beijing 19:59 = UTC 11:59 — outside custom peak.
    assert (
        background_llm_work_allowed(
            scheduler, tracker, now_utc=_utc(11, 59)
        )
        is True
    )


def test_peak_deferral_ignores_presence_when_disconnect_policy_off() -> None:
    """Outside peak, the original presence semantics are untouched."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = _scheduler(pause_on_extension_disconnect=False)
    clock.advance(999)

    assert (
        background_llm_work_allowed(
            scheduler, tracker, now_utc=_utc(15)
        )
        is True
    )


def test_peak_deferral_composes_with_presence_gate() -> None:
    """Inside peak, presence cannot override the deferral (blocked either way)."""
    clock = FakeClock()
    tracker = PresenceTracker(now=clock)
    scheduler = _scheduler(pause_on_extension_disconnect=True)
    tracker.on_connect()

    # Even with an active client, peak still blocks non-refill traffic.
    assert (
        background_llm_work_allowed(
            scheduler, tracker, now_utc=_utc(2)
        )
        is False
    )
