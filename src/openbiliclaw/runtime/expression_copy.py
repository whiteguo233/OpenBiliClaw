"""Single-flight microbatch scheduling for recommendation expression copy."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any

from openbiliclaw.llm.base import classify_llm_failure_kind

logger = logging.getLogger(__name__)

_TRANSIENT_BACKOFF_SECONDS = (15.0, 30.0, 60.0, 120.0, 300.0)


class ExpressionCopyCoordinator:
    """Drain durable expression-copy work at an 8-item/3-second cadence."""

    def __init__(
        self,
        *,
        pending_count_provider: Any,
        drain_callback: Any,
        min_items: int = 8,
        max_wait_seconds: float = 3.0,
        drain_limit: int = 60,
        zero_progress_backoff_seconds: float = 15.0,
        safety_wake_seconds: float = 60.0,
        work_allowed: Any = None,
        time_fn: Any = time.monotonic,
        wait_fn: Any = asyncio.sleep,
    ) -> None:
        self.pending_count_provider = pending_count_provider
        self.drain_callback = drain_callback
        self.work_allowed = work_allowed
        self.min_items = max(1, int(min_items))
        self.max_wait_seconds = max(0.0, float(max_wait_seconds))
        self.drain_limit = max(1, min(60, int(drain_limit)))
        self.zero_progress_backoff_seconds = max(0.0, float(zero_progress_backoff_seconds))
        self.safety_wake_seconds = max(0.01, float(safety_wake_seconds))
        self.time_fn = time_fn
        self.wait_fn = wait_fn

        self.state = "idle"
        self.last_error = ""
        self.last_completed = 0
        self.last_wake_reason = ""
        self._deadline = 0.0
        self._first_pending_at: float | None = None
        self._retry_not_before = 0.0
        self._wake_event = asyncio.Event()
        self._generation = 0
        self._copy_task: asyncio.Task[int] | None = None
        self._running = False
        self._stopping = False
        self._transient_streak = 0
        self._paused = False

    def notify(self, reason: str) -> None:
        """Accelerate a durable recheck without running copy inline."""

        if self._stopping:
            return
        if self._paused and (
            str(reason).startswith("config_")
            or str(reason).startswith("manual_")
            or reason == "startup"
        ):
            self._paused = False
            self._retry_not_before = 0.0
            logger.info("expression copy resumed on %s", reason)
        self._generation += 1
        self.last_wake_reason = str(reason)
        if self._first_pending_at is None:
            self._first_pending_at = float(self.time_fn())
        self._wake_event.set()

    async def run_forever(self) -> None:
        """Run one collector/copy task until :meth:`stop` is called."""

        if self._running or self._stopping:
            return
        self._running = True
        self.notify("startup")
        try:
            while not self._stopping:
                if self._copy_task is not None and self._copy_task.done():
                    await self._settle_copy_task()
                    if self._stopping:
                        break
                if self.work_allowed is not None and not bool(self.work_allowed()):
                    self.state = "paused"
                    await self._wait_for_activity(self.safety_wake_seconds)
                    continue

                pending = self._pending_count()
                now = float(self.time_fn())
                if self._copy_task is None:
                    self._schedule(pending=pending, now=now)
                    if self._deadline <= now and pending > 0:
                        self.state = "running"
                        limit = min(self.drain_limit, pending)
                        self._copy_task = asyncio.create_task(
                            self._drain(limit), name="expression_copy"
                        )
                        continue

                await self._wait_for_activity(now)
        finally:
            self._stopping = True
            self.state = "stopping"
            await self._cancel_copy_task()
            self._running = False

    async def stop(self) -> None:
        """Cancel collection and any in-flight copy callback."""

        self._stopping = True
        self.state = "stopping"
        self._wake_event.set()
        await self._cancel_copy_task()

    def status_payload(self) -> dict[str, object]:
        """Return stable public diagnostics for runtime status APIs."""

        return {
            "expression_pending_count": self._pending_count(),
            "expression_batch_state": self.state,
            "expression_batch_deadline": self._deadline,
            "expression_last_completed": self.last_completed,
            "expression_last_error": self.last_error,
        }

    def _pending_count(self) -> int:
        try:
            return max(0, int(self.pending_count_provider()))
        except Exception as exc:
            self.last_error = str(exc)
            return 0

    def _schedule(self, *, pending: int, now: float) -> None:
        if pending <= 0:
            self._first_pending_at = None
            self._deadline = 0.0
            self.state = "idle"
        elif self._paused:
            self._deadline = now + self.safety_wake_seconds
            self.state = "paused"
        elif self._retry_not_before > now:
            self._deadline = self._retry_not_before
            self.state = "backoff"
        elif pending >= self.min_items:
            self._deadline = now
            self.state = "running"
        else:
            if self._first_pending_at is None:
                self._first_pending_at = now
            self._deadline = self._first_pending_at + self.max_wait_seconds
            self.state = "collecting"

    async def _drain(self, limit: int) -> int:
        result = self.drain_callback(limit)
        if inspect.isawaitable(result):
            result = await result
        return max(0, int(result))

    async def _settle_copy_task(self) -> None:
        task = self._copy_task
        self._copy_task = None
        if task is None:
            return
        failure_kind: str | None = None
        backoff_delay = 0.0
        try:
            completed = task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.last_error = str(exc)
            completed = max(0, int(getattr(exc, "completed", 0) or 0))
            kind = getattr(exc, "kind", None) or classify_llm_failure_kind(exc)
            failure_kind = kind
            now = float(self.time_fn())
            if kind in {"no_provider", "auth_failed"}:
                self._paused = True
                logger.warning(
                    "expression copy paused on %s (pending=%d); waiting for a "
                    "startup/config_*/manual_* wake: %s",
                    kind,
                    self._pending_count(),
                    self.last_error,
                )
            elif kind in {"rate_limited", "timeout", "connection", "server_error"}:
                backoff_delay = _TRANSIENT_BACKOFF_SECONDS[
                    min(self._transient_streak, len(_TRANSIENT_BACKOFF_SECONDS) - 1)
                ]
                self._transient_streak += 1
                retry_after = max(0.0, float(getattr(exc, "retry_after", 0.0) or 0.0))
                backoff_delay = max(backoff_delay, retry_after)
                self._retry_not_before = now + backoff_delay
                # One line per state change, matching the candidate-eval worker
                # style: silent 15/30/60/120/300s backoff ladders used to leave
                # "no copy progress for minutes, zero log lines" incidents.
                logger.warning(
                    "expression copy transient failure (%s); backing off %.0fs "
                    "(pending=%d, streak=%d): %s",
                    kind,
                    backoff_delay,
                    self._pending_count(),
                    self._transient_streak,
                    self.last_error,
                )
        else:
            if self._transient_streak > 0 or self._retry_not_before > 0.0:
                logger.info(
                    "expression copy recovered: completed=%d pending=%d",
                    completed,
                    self._pending_count(),
                )
            self.last_error = ""
            self._transient_streak = 0
        self.last_completed = completed
        now = float(self.time_fn())
        pending = self._pending_count()
        if failure_kind in {"rate_limited", "timeout", "connection", "server_error"}:
            self._first_pending_at = None
        elif completed <= 0 and pending > 0:
            if failure_kind not in {
                "rate_limited",
                "timeout",
                "connection",
                "server_error",
            }:
                self._retry_not_before = now + self.zero_progress_backoff_seconds
                if failure_kind is None:
                    logger.warning(
                        "expression copy made no progress; retrying in %.0fs "
                        "(pending=%d, last_error=%s)",
                        self.zero_progress_backoff_seconds,
                        pending,
                        self.last_error or "none",
                    )
            self._first_pending_at = None
        else:
            self._retry_not_before = 0.0
            self._first_pending_at = now if 0 < pending < self.min_items else None

    async def _wait_for_activity(self, now: float) -> None:
        observed_generation = self._generation
        self._wake_event.clear()
        if observed_generation != self._generation:
            return
        delay = self.safety_wake_seconds
        if self._copy_task is None and self._deadline > now:
            delay = min(delay, self._deadline - now)
        wake_task = asyncio.create_task(self._wake_event.wait())
        timer_task = asyncio.create_task(self.wait_fn(max(0.0, delay)))
        waiters: set[asyncio.Task[Any]] = {wake_task, timer_task}
        if self._copy_task is not None:
            waiters.add(self._copy_task)
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (wake_task, timer_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(wake_task, timer_task, return_exceptions=True)

    async def _cancel_copy_task(self) -> None:
        task = self._copy_task
        self._copy_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.last_error = str(exc)
