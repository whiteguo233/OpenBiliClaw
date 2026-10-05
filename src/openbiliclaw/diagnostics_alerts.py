"""Diagnostics alert buffer for LLM / embedding request failures.

Collects abnormal runtime events — LLM provider failures (HTTP 429 rate
limits, auth errors, timeouts, malformed responses, fallback exhaustion)
and embedding provider failures (including circuit-breaker trips) — into
a bounded in-memory ring so the Web console and the browser-extension
settings "日志" tab can render an 异常报警 list.

Design constraints:
- Hot-path safety: ``record()`` never raises and never blocks on I/O.
- Bounded memory: identical alerts (same category/source/code) arriving
  within a short coalesce window are merged into one row whose ``count``
  increments, and the ring itself has a hard cap.
- Zero wiring for producers: they call :func:`record_diagnostics_alert`
  against a process-wide singleton. The API layer reads the same buffer
  and optionally fans live events out through the runtime event hub.

This module deliberately lives at the top level of the package: both
``llm.base`` and the runtime/API layers import it, and routing it via
``openbiliclaw.runtime`` would create a circular import through that
package's ``__init__``.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# Identical alerts inside this window merge into one row (count bumps)
# instead of appending a new entry. Long enough to collapse a burst of
# per-call failures, short enough that a recurring problem stays visible.
_COALESCE_WINDOW_SECONDS = 60.0

_MAX_ENTRIES = 100

Publisher = Callable[[dict[str, Any]], Awaitable[bool]]


@dataclass
class DiagnosticsAlert:
    """One coalesced anomaly report shown in the 异常报警 list."""

    id: int
    category: str  # "llm" | "embedding"
    code: str  # e.g. "rate_limited", "auth_failed", "timeout"
    severity: str  # "warning" | "error"
    source: str  # provider instance id / endpoint label
    message: str
    first_seen: float  # epoch seconds
    last_seen: float  # epoch seconds
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "message": self.message,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "count": self.count,
        }


class DiagnosticsAlertBuffer:
    """Thread-safe bounded ring of recent diagnostics alerts."""

    def __init__(
        self,
        *,
        max_entries: int = _MAX_ENTRIES,
        coalesce_window_seconds: float = _COALESCE_WINDOW_SECONDS,
    ) -> None:
        self._entries: deque[DiagnosticsAlert] = deque(maxlen=max(1, int(max_entries)))
        self._coalesce_window = max(0.0, float(coalesce_window_seconds))
        # RLock: composite operations (e.g. the reset seam) hold the lock
        # while calling methods that re-acquire it.
        self._lock = threading.RLock()
        self._next_id = 1
        self._publisher: Publisher | None = None

    def set_publisher(self, publisher: Publisher | None) -> None:
        """Attach an async event publisher (the runtime event hub).

        Passing ``None`` detaches. Used by the API layer so freshly
        recorded alerts also appear on ``/api/runtime-stream`` without a
        poll round-trip.
        """
        with self._lock:
            self._publisher = publisher

    def record(
        self,
        *,
        category: str,
        code: str,
        message: str,
        source: str = "",
        severity: str = "warning",
    ) -> dict[str, Any] | None:
        """Record one anomaly; return the alert payload or ``None``.

        Never raises: producers sit in LLM/embedding hot paths where a
        diagnostics hiccup must be invisible.
        """
        try:
            return self._record_inner(
                category=category,
                code=code,
                message=message,
                source=source,
                severity=severity,
            )
        except Exception:
            return None

    def _record_inner(
        self,
        *,
        category: str,
        code: str,
        message: str,
        source: str,
        severity: str,
    ) -> dict[str, Any] | None:
        now = time.time()
        payload: dict[str, Any] | None = None
        with self._lock:
            # Merge into the most recent row sharing this exact identity
            # (category/code/source), not merely entries[-1]: a single LLM
            # request writes an alternating pair (per-provider failure then
            # the chain-terminal error), so tail-only matching would append
            # a new row for both kinds on every retry instead of bumping
            # their counts.
            target: DiagnosticsAlert | None = None
            for alert in reversed(self._entries):
                if alert.category == category and alert.code == code and alert.source == source:
                    target = alert
                    break
            if target is not None and now - target.last_seen <= self._coalesce_window:
                target.count += 1
                target.last_seen = now
                if severity == "error":
                    target.severity = "error"
                    target.message = message
                payload = target.to_dict()
            else:
                alert = DiagnosticsAlert(
                    id=self._next_id,
                    category=str(category or "unknown"),
                    code=str(code or "unknown"),
                    severity="error" if severity == "error" else "warning",
                    source=str(source or ""),
                    message=str(message or ""),
                    first_seen=now,
                    last_seen=now,
                )
                self._next_id += 1
                self._entries.append(alert)
                payload = alert.to_dict()
            publisher = self._publisher
        if publisher is not None:
            self._publish_async(publisher, payload)
        return payload

    @staticmethod
    def _publish_async(publisher: Publisher, payload: dict[str, Any] | None) -> None:
        """Fire-and-forget the live event; safe outside a running loop."""
        if payload is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        event = {"type": "diagnostics.alert", **payload}
        with contextlib.suppress(Exception):
            loop.create_task(_swallow(publisher(event)))

    def snapshot(
        self,
        *,
        since_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return recent alerts (newest first) plus severity summary."""
        bounded_limit = max(1, min(int(limit), 500))
        min_id = max(0, int(since_id))
        with self._lock:
            rows = [alert.to_dict() for alert in reversed(self._entries) if alert.id > min_id]
            error_count = sum(1 for alert in self._entries if alert.severity == "error")
            total = len(self._entries)
        return {
            "alerts": rows[:bounded_limit],
            "summary": {
                "total": total,
                "errors": error_count,
                "warnings": total - error_count,
            },
            "generated_at": time.time(),
        }

    def clear(self) -> None:
        """Drop all buffered alerts (used by tests and manual reset)."""
        with self._lock:
            self._entries.clear()


async def _swallow(awaitable: Awaitable[object]) -> None:
    """Await without propagating — live fan-out must never break producers."""
    with contextlib.suppress(Exception):
        await awaitable


_buffer = DiagnosticsAlertBuffer()


def get_diagnostics_alert_buffer() -> DiagnosticsAlertBuffer:
    """Return the process-wide singleton buffer."""
    return _buffer


def record_diagnostics_alert(
    *,
    category: str,
    code: str,
    message: str,
    source: str = "",
    severity: str = "warning",
) -> dict[str, Any] | None:
    """Record one anomaly into the process-wide buffer (never raises)."""
    return _buffer.record(
        category=category,
        code=code,
        message=message,
        source=source,
        severity=severity,
    )


def reset_diagnostics_alert_buffer(buffer: DiagnosticsAlertBuffer | None = None) -> None:
    """Replace/reset the singleton (test seam)."""
    global _buffer
    with _buffer._lock:  # noqa: SLF001 - test seam owns the swap
        _buffer.clear()
        _buffer.set_publisher(None)
    if buffer is not None:
        _buffer = buffer
