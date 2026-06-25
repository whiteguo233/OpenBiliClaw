"""Debounced scheduling for expensive feedback batch learning."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FeedbackBatchScheduler:
    """Coalesce bursts of recommendation feedback into one batch refresh."""

    soul_engine: Any
    debounce_seconds: float = 5.0
    _dirty: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)

    def schedule(self) -> None:
        """Request a feedback batch pass after the debounce window."""
        if self._closed:
            return
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def drain(self) -> None:
        """Wait for the currently scheduled pass, if any.

        Intended for tests and graceful shutdown. It does not create work by
        itself; callers should invoke :meth:`schedule` first.
        """
        task = self._task
        if task is None:
            return
        await task

    async def close(self) -> None:
        """Cancel any pending scheduled work."""
        self._closed = True
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while not self._closed:
            delay = max(0.0, float(self.debounce_seconds))
            if delay > 0:
                await asyncio.sleep(delay)
            self._dirty = False
            process = getattr(self.soul_engine, "process_feedback_batch_if_needed", None)
            if callable(process):
                try:
                    await process()
                except Exception:
                    logger.exception("post-feedback batch processing failed")
            if not self._dirty:
                return
