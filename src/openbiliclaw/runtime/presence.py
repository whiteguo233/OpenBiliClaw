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


def background_llm_work_allowed(scheduler: object, presence: PresenceTracker) -> bool:
    """Return whether daemon-owned background LLM / embedding work may run."""
    if not bool(getattr(scheduler, "enabled", True)):
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


__all__ = ["PresenceTracker", "background_llm_work_allowed"]
