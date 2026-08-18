"""Extension presence tracking for background LLM work gating."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS = 90

# Peak-hour deferral defaults (DeepSeek bills off-peak at half price).
# Peak window spec format: "HH:MM-HH:MM,HH:MM-HH:MM" in Beijing time
# (UTC+8, no DST — a fixed offset needs no tzdata, which is absent from
# the python:3.11-slim runtime image).
_DEFAULT_PEAK_HOURS = "09:00-12:00,14:00-18:00"
_DEFAULT_PEAK_REFILL_FLOOR = 30

_BEIJING_TZ = timezone(timedelta(hours=8))


class PresenceTracker:
    """Track shared extension presence across runtime-stream clients."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._lock = threading.Lock()
        self._active_count = 0
        self._last_disconnect_at: float | None = now()

    def on_connect(self) -> None:
        """Record a runtime-stream client connection."""
        with self._lock:
            self._active_count += 1
            if self._active_count == 1:
                self._last_disconnect_at = None

    def on_disconnect(self) -> None:
        """Record a runtime-stream client disconnect."""
        with self._lock:
            if self._active_count <= 0:
                logger.warning("Presence disconnect received without active clients")
                self._active_count = 0
                return
            self._active_count -= 1
            if self._active_count == 0:
                self._last_disconnect_at = self._now()

    def is_present(self, grace_seconds: int) -> bool:
        """Return whether a client is active or inside the disconnect grace window."""
        with self._lock:
            active_count = self._active_count
            last_disconnect_at = self._last_disconnect_at
        if active_count > 0:
            return True
        if last_disconnect_at is None or grace_seconds <= 0:
            return False
        return self._now() - last_disconnect_at <= grace_seconds

    def snapshot(self) -> dict[str, int | float | None]:
        """Return current presence state for diagnostics."""
        with self._lock:
            active_count = self._active_count
            last_disconnect_at = self._last_disconnect_at
        seconds_since_disconnect = (
            None if last_disconnect_at is None else max(0.0, self._now() - last_disconnect_at)
        )
        return {
            "active_count": active_count,
            "last_disconnect_at": last_disconnect_at,
            "seconds_since_disconnect": seconds_since_disconnect,
        }


def parse_peak_windows(spec: str) -> list[tuple[int, int]]:
    """Parse a peak-window spec like ``"09:00-12:00,14:00-18:00"``.

    Returns a list of ``(start_minutes, end_minutes)`` pairs within
    ``[0, 1440)``, where the start is inclusive and the end is exclusive
    (09:00 counts as peak, 12:00 does not). Malformed or empty specs
    return ``[]`` — callers treat that as "no peak windows, never defer".
    """
    windows: list[tuple[int, int]] = []
    for chunk in str(spec or "").split(","):
        chunk = chunk.strip()
        if not chunk or "-" not in chunk:
            continue
        start_s, _, end_s = chunk.partition("-")
        try:
            start_h, start_m = (int(part) for part in start_s.strip().split(":", 1))
            end_h, end_m = (int(part) for part in end_s.strip().split(":", 1))
        except (TypeError, ValueError):
            continue
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if not (0 <= start < 1440 and 0 <= end <= 1440) or start >= end:
            continue
        windows.append((start, end))
    return windows


def _now_in_peak_windows(
    now_utc: datetime,
    windows: list[tuple[int, int]],
) -> bool:
    """Return whether ``now_utc`` falls inside any Beijing-time peak window."""
    if not windows:
        return False
    beijing = now_utc.astimezone(_BEIJING_TZ)
    minutes = beijing.hour * 60 + beijing.minute
    return any(start <= minutes < end for start, end in windows)


def _peak_hour_deferral_active(
    scheduler: object,
    *,
    pool_available: int | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Return whether peak-hour deferral currently blocks background LLM work.

    Only engages when the scheduler opts in via ``pause_during_peak_hours``.
    Non-refill traffic (``pool_available is None``, e.g. profile maintenance
    or proactive pushes) is fully blocked inside a peak window. Refill
    traffic passes only when the servable pool is below
    ``peak_refill_floor`` — an emergency top-up so the feed never starves
    during peak hours.
    """
    if not bool(getattr(scheduler, "pause_during_peak_hours", False)):
        return False
    spec = str(getattr(scheduler, "peak_hours", "") or _DEFAULT_PEAK_HOURS)
    windows = parse_peak_windows(spec)
    if not windows:
        return False
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if not _now_in_peak_windows(now_utc, windows):
        return False
    if pool_available is None:
        return True
    floor = int(getattr(scheduler, "peak_refill_floor", _DEFAULT_PEAK_REFILL_FLOOR) or 0)
    if floor <= 0:
        return True
    return int(pool_available) >= floor


def background_llm_work_allowed(
    scheduler: object,
    presence: PresenceTracker,
    *,
    pool_available: int | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Return whether daemon-owned background LLM / embedding work may run.

    ``pool_available`` marks the caller as refill traffic (the servable pool
    count) so peak-hour deferral can keep an emergency floor; ``None`` means
    non-refill (maintenance / push) traffic. ``now_utc`` is injectable for
    tests.
    """
    if not bool(getattr(scheduler, "enabled", True)):
        return False
    if _peak_hour_deferral_active(
        scheduler,
        pool_available=pool_available,
        now_utc=now_utc,
    ):
        return False
    if not bool(getattr(scheduler, "pause_on_extension_disconnect", False)):
        return True
    try:
        grace = int(
            getattr(
                scheduler,
                "extension_disconnect_grace_seconds",
                _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS,
            )
            or _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
        )
    except (TypeError, ValueError):
        grace = _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    if grace <= 0:
        grace = _DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS
    return presence.is_present(grace_seconds=grace)


__all__ = [
    "PresenceTracker",
    "background_llm_work_allowed",
    "parse_peak_windows",
]
