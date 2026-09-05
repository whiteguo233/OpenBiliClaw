"""Cross-process recommendation snapshot store.

Phase 1 of the API/worker isolation work: the background worker builds a
``PoolServeSnapshot`` and publishes it as an atomically replaced JSON file.
The API process can then serve recommendations from that file instead of
opening a fresh SQLite read transaction on every refresh.

The file is deliberately plain JSON + a small metadata envelope so a separate
Python process (or a future non-Python worker) can publish it without needing
to pickle the API process's in-memory objects.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import PoolServeSnapshot

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_SECONDS = 30.0
_SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class ServeSnapshotEnvelope:
    """Persisted envelope around one full recommend-serve snapshot."""

    version: int
    built_at: float
    snapshot: PoolServeSnapshot


def _rows_to_json(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def serialize_snapshot(snapshot: PoolServeSnapshot) -> dict[str, Any]:
    """Convert a :class:`PoolServeSnapshot` to JSON-safe data."""
    return {
        "readiness": dict(snapshot.readiness),
        "candidate_rows": _rows_to_json(snapshot.candidate_rows),
        "loaded_count": int(snapshot.loaded_count),
        "platform_topups": [list(pair) for pair in snapshot.platform_topups],
        "seen_bvids": list(snapshot.seen_bvids),
        "curator_signals": _rows_to_json(snapshot.curator_signals),
        "feedback_signals": _rows_to_json(snapshot.feedback_signals),
    }


def deserialize_snapshot(data: dict[str, Any]) -> PoolServeSnapshot:
    """Rebuild a :class:`PoolServeSnapshot` from JSON-safe data."""
    return PoolServeSnapshot(
        readiness={str(k): int(v) for k, v in dict(data.get("readiness") or {}).items()},
        candidate_rows=tuple(dict(row) for row in (data.get("candidate_rows") or [])),
        loaded_count=int(data.get("loaded_count", 0) or 0),
        platform_topups=tuple(
            (str(pair[0]), int(pair[1]))
            for pair in (data.get("platform_topups") or [])
            if len(pair) >= 2
        ),
        seen_bvids=frozenset(str(v) for v in (data.get("seen_bvids") or [])),
        curator_signals=tuple(dict(row) for row in (data.get("curator_signals") or [])),
        feedback_signals=tuple(dict(row) for row in (data.get("feedback_signals") or [])),
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON file with 0600 permissions on POSIX."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".serve-snapshot-",
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


class ServeSnapshotStore:
    """Read/write the latest atomically published serve snapshot."""

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

    def save(self, snapshot: PoolServeSnapshot) -> None:
        """Publish one snapshot atomically (writer process)."""
        if self.path is None:
            return
        payload = {
            "version": _SNAPSHOT_VERSION,
            "built_at": self._current_time(),
            "snapshot": serialize_snapshot(snapshot),
        }
        _atomic_write_json(self.path, payload)

    def load(self) -> PoolServeSnapshot | None:
        """Return the latest snapshot if it exists and is fresh enough."""
        if self.path is None:
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("Unable to read serve snapshot: %s", exc)
            return None
        if not isinstance(raw, dict) or raw.get("version") != _SNAPSHOT_VERSION:
            return None
        try:
            built_at = float(raw.get("built_at", 0.0))
            if self.max_age_seconds > 0 and self._current_time() - built_at > self.max_age_seconds:
                return None
            snapshot = raw.get("snapshot")
            if not isinstance(snapshot, dict):
                return None
            return deserialize_snapshot(snapshot)
        except (TypeError, ValueError) as exc:
            logger.warning("Unable to decode serve snapshot: %s", exc)
            return None

    def _current_time(self) -> float:
        return time.time() if self._now is None else self._now()
