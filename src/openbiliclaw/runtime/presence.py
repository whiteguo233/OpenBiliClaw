"""Extension presence tracking for background LLM work gating."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSION_DISCONNECT_GRACE_SECONDS = 90
# Runtime-stream checks its live socket every 20s. Three missed checks expire
# a crashed API's lease, independently of the configurable disconnect grace.
# Recalibrate together with the stream heartbeat interval in api/app.py.
PRESENCE_HEARTBEAT_INTERVAL_SECONDS = 20.0
_SHARED_HEARTBEAT_TTL_SECONDS = 3 * PRESENCE_HEARTBEAT_INTERVAL_SECONDS

# Peak-hour deferral defaults. ``peak_hours`` is a manual override for
# providers the rate catalog cannot price; it is interpreted in the user's
# provider timezone offset (default Beijing, UTC+8). The catalog path uses UTC
# windows directly and does not need this.
_DEFAULT_PEAK_REFILL_FLOOR = 30
_DEFAULT_PEAK_TIMEZONE_OFFSET_MINUTES = 480


class PresenceTracker:
    """Track local stream clients and optionally read API presence across workers.

    Only the API owns connections and publishes ``shared_path``. Headless
    workers open the same path read-only; constructing a tracker never writes.
    Local deadlines use monotonic time; the shared lease uses wall-clock time.
    """

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        shared_path: Path | None = None,
        wall_now: Callable[[], float] = time.time,
    ) -> None:
        self._now = now
        self._wall_now = wall_now
        self._shared_path = shared_path
        self._lock = threading.Lock()
        self._active_count = 0
        self._last_disconnect_at: float | None = now()
        self._last_shared_publish_at: float | None = None

    def on_connect(self) -> None:
        """Record a runtime-stream client connection."""
        with self._lock:
            self._active_count += 1
            if self._active_count == 1:
                self._last_disconnect_at = None
            self._publish_shared_locked(force=True)

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
            self._publish_shared_locked(force=True)

    def heartbeat(self) -> None:
        """Renew the API's shared lease while a local stream remains connected."""
        with self._lock:
            if self._active_count > 0:
                self._publish_shared_locked(force=False)

    def _publish_shared_locked(self, *, force: bool) -> None:
        if self._shared_path is None:
            return
        now = self._now()
        if (
            not force
            and self._last_shared_publish_at is not None
            and now - self._last_shared_publish_at < PRESENCE_HEARTBEAT_INTERVAL_SECONDS
        ):
            return
        # Serialize with connect/disconnect so a delayed heartbeat cannot
        # overwrite a newer disconnect. Do not involve SQLite's write lane.
        self._last_shared_publish_at = now
        path = self._shared_path
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".presence-", dir=path.parent)
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 1,
                        "active_count": self._active_count,
                        "updated_at": self._wall_now(),
                    },
                    handle,
                    separators=(",", ":"),
                )
            os.replace(temporary_path, path)
        except OSError:
            logger.warning("Unable to publish extension presence", exc_info=True)
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink()

    def _shared_is_present(self, grace_seconds: int) -> bool:
        if self._shared_path is None:
            return False
        try:
            data = json.loads(self._shared_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                return False
            count = data.get("active_count")
            updated_at = data.get("updated_at")
            if type(count) is not int or count < 0:
                return False
            if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
                return False
            age = self._wall_now() - updated_at
            if not math.isfinite(age) or age < 0:
                return False
            if count > 0:
                return age <= _SHARED_HEARTBEAT_TTL_SECONDS
            return grace_seconds > 0 and age <= grace_seconds
        except (OSError, ValueError, OverflowError):
            return False

    def is_present(self, grace_seconds: int) -> bool:
        """Return whether a client is active or inside the disconnect grace window."""
        with self._lock:
            active_count = self._active_count
            last_disconnect_at = self._last_disconnect_at
        if active_count > 0:
            return True
        if self._shared_is_present(grace_seconds):
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


def parse_peak_windows(
    spec: str,
    *,
    offset_minutes: int = _DEFAULT_PEAK_TIMEZONE_OFFSET_MINUTES,
) -> list[tuple[int, int]]:
    """Parse ``"HH:MM-HH:MM,HH:MM-HH:MM"`` into UTC minute windows.

    The spec is written in the provider's local time (``offset_minutes``,
    default Beijing +480). Returns ``(start, end)`` pairs in UTC minutes,
    start inclusive / end exclusive; ``start > end`` means the window wraps
    midnight. Malformed or empty specs return ``[]`` — callers treat that as
    "no manual windows, never defer".
    """

    windows: list[tuple[int, int]] = []
    offset = int(offset_minutes)
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
        local_start = start_h * 60 + start_m
        local_end = end_h * 60 + end_m
        if not (0 <= local_start < 1440 and 0 <= local_end <= 1440):
            continue
        if local_start == local_end:
            continue
        start = (local_start - offset) % 1440
        end = (local_end - offset) % 1440
        if start == end:
            # A full-day window; treat it as invalid rather than blocking all
            # background work forever.
            continue
        windows.append((start, end))
    return windows


def now_in_peak_windows(
    now_utc: datetime,
    windows: list[tuple[int, int]],
) -> bool:
    """Return whether ``now_utc`` is inside any UTC minute window."""

    if not windows:
        return False
    minute = now_utc.hour * 60 + now_utc.minute
    for start, end in windows:
        if start < end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:
            return True
    return False


def manual_peak_active(
    scheduler: object,
    *,
    now_utc: datetime | None = None,
) -> bool:
    """Evaluate the manual ``scheduler.peak_hours`` override, if configured."""

    spec = str(getattr(scheduler, "peak_hours", "") or "").strip()
    if not spec:
        return False
    try:
        offset = int(
            getattr(
                scheduler,
                "peak_timezone_offset_minutes",
                _DEFAULT_PEAK_TIMEZONE_OFFSET_MINUTES,
            )
        )
    except (TypeError, ValueError):
        offset = _DEFAULT_PEAK_TIMEZONE_OFFSET_MINUTES
    windows = parse_peak_windows(spec, offset_minutes=offset)
    if not windows:
        return False
    moment = now_utc or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return now_in_peak_windows(moment.astimezone(UTC), windows)


def peak_deferral_active(
    scheduler: object,
    *,
    pool_available: int | None = None,
    peak_active: bool | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Return whether peak-hour policy blocks this background call.

    ``peak_active`` is supplied by the controller from the rate catalog (the
    precise, per-provider source). ``None`` falls back to the manual
    ``peak_hours`` spec. ``pool_available`` distinguishes refill traffic (the
    servable pool count) from maintenance (``None``):

    - ``defer``: maintenance is fully deferred; refill may pass only below
      ``peak_refill_floor`` so the feed never starves during a long peak.
    - ``prefer``: maintenance is deferred; refill always runs (freshness wins
      over savings).
    """

    policy = str(getattr(scheduler, "peak_policy", "off") or "off").strip().lower()
    if policy not in {"defer", "prefer"}:
        return False
    is_peak = manual_peak_active(scheduler, now_utc=now_utc) if peak_active is None else peak_active
    if not is_peak:
        return False
    if pool_available is None:
        return True
    if policy == "prefer":
        return False
    try:
        floor = int(getattr(scheduler, "peak_refill_floor", _DEFAULT_PEAK_REFILL_FLOOR) or 0)
    except (TypeError, ValueError):
        floor = _DEFAULT_PEAK_REFILL_FLOOR
    if floor <= 0:
        return True
    return int(pool_available) >= floor


def background_llm_work_allowed(
    scheduler: object,
    presence: PresenceTracker,
    *,
    pool_available: int | None = None,
    peak_active: bool | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """Return whether daemon-owned background LLM / embedding work may run.

    ``pool_available`` marks refill traffic and carries the servable-pool
    count; ``None`` means maintenance / push traffic. ``peak_active`` is the
    rate catalog's verdict for the current provider; ``None`` falls back to
    the manual ``peak_hours`` override.
    """

    if not bool(getattr(scheduler, "enabled", True)):
        return False
    if peak_deferral_active(
        scheduler,
        pool_available=pool_available,
        peak_active=peak_active,
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
    "manual_peak_active",
    "now_in_peak_windows",
    "parse_peak_windows",
    "peak_deferral_active",
]
