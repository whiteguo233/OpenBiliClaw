"""Cross-process worker heartbeat/status store.

The full background worker writes a small atomically-replaced JSON status so the
API process can report whether the worker is healthy without needing the worker
to be in the same process or on the same event loop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATUS_VERSION = 1
_DEFAULT_MAX_AGE_SECONDS = 45.0


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON file with 0600 permissions on POSIX."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".worker-status-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _timestamp_to_iso(value: float | None) -> str:
    if not value or value <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(value)).astimezone().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


class WorkerStatusStore:
    """Read/write the latest worker status heartbeat."""

    def __init__(
        self,
        path: Path | str | None,
        *,
        max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
        now: float | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path else None
        self.max_age_seconds = max(0.0, float(max_age_seconds))
        self._now = now

    def write(
        self,
        *,
        mode: str,
        pid: int,
        started_at: float,
        heartbeat_at: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Publish one heartbeat (writer process)."""
        if self.path is None:
            return
        payload: dict[str, Any] = {
            "version": _STATUS_VERSION,
            "mode": mode,
            "pid": int(pid),
            "started_at": float(started_at),
            "heartbeat_at": float(heartbeat_at),
        }
        if extra:
            payload.update(extra)
        _atomic_write_json(self.path, payload)

    def read(self) -> dict[str, Any] | None:
        """Return the latest worker status, or None when missing/unreadable."""
        if self.path is None:
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("Unable to read worker status: %s", exc)
            return None
        if not isinstance(raw, dict) or raw.get("version") != _STATUS_VERSION:
            return None
        return raw

    def status_payload(self) -> dict[str, Any]:
        """Return runtime-status friendly worker health fields."""
        data = self.read()
        now = time.time() if self._now is None else self._now()
        if data is None:
            return {
                "worker_running": False,
                "worker_mode": "none",
                "worker_pid": None,
                "worker_started_at": "",
                "worker_last_heartbeat_at": "",
                "worker_heartbeat_age_seconds": -1.0,
            }
        heartbeat = float(data.get("heartbeat_at") or 0.0)
        age = now - heartbeat if heartbeat > 0 else -1.0
        running = age >= 0 and age <= self.max_age_seconds
        return {
            "worker_running": running,
            "worker_mode": str(data.get("mode") or "unknown"),
            "worker_pid": int(data["pid"]) if data.get("pid") is not None else None,
            "worker_started_at": _timestamp_to_iso(float(data.get("started_at") or 0.0)),
            "worker_last_heartbeat_at": _timestamp_to_iso(heartbeat),
            "worker_heartbeat_age_seconds": age,
        }
