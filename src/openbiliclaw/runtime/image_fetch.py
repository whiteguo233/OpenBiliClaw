"""App-owned cover fetch coordination for foreground and background callers.

The image proxy and refresh prefetch loop share one coordinator.  It reserves
one of four upstream slots for foreground requests, prioritises queued
foreground work, and coalesces concurrent requests for the same cache key.
Disk cache access remains outside the network gate and runs in worker threads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from openbiliclaw.runtime import image_cache

FetchPriority = Literal["foreground", "background"]
FetchPhase = Literal["pending", "waiting", "active"]
CoverFetcher = Callable[[str], Awaitable[tuple[bytes, str]]]


@dataclass(frozen=True)
class ImageFetchResult:
    """Bytes returned to a caller plus their cache provenance."""

    data: bytes
    content_type: str
    cache_hit: bool
    stored: bool = False


@dataclass
class _InflightFetch:
    key: str
    url: str
    priority: FetchPriority
    phase: FetchPhase = "pending"
    task: asyncio.Task[ImageFetchResult] | None = None
    active_priority: FetchPriority | None = None


@dataclass
class ImageFetchCoordinator:
    """Coordinate cache-first cover fetches across all API runtime owners.

    The coordinator owns upstream tasks rather than attaching their lifetime to
    any individual waiter.  Every waiter observes the shared task through
    ``asyncio.wait``, so cancelling one HTTP request cannot cancel a fetch still
    needed by another waiter or leave a Python 3.14 shield logger behind.
    """

    upstream_fetcher: CoverFetcher | None = None
    max_active: int = 10
    max_background: int = 8
    _condition: asyncio.Condition = field(init=False, repr=False)
    _inflight: dict[str, _InflightFetch] = field(init=False, default_factory=dict, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _active: int = field(init=False, default=0, repr=False)
    _active_background: int = field(init=False, default=0, repr=False)
    _upstream_started: int = field(init=False, default=0, repr=False)
    _singleflight_joins: int = field(init=False, default=0, repr=False)
    _peak_active: int = field(init=False, default=0, repr=False)
    _peak_background: int = field(init=False, default=0, repr=False)

    def __post_init__(self) -> None:
        if self.max_active < 1:
            raise ValueError("max_active must be at least 1")
        if self.max_background < 0 or self.max_background >= self.max_active:
            raise ValueError("max_background must reserve at least one foreground slot")
        self._condition = asyncio.Condition()

    async def fetch(
        self,
        url: str,
        *,
        priority: FetchPriority = "foreground",
    ) -> ImageFetchResult:
        """Return a cached cover or join/start one priority-gated upstream fetch."""
        image_cache.validate_image_url(url)
        async with self._condition:
            if self._closed:
                raise image_cache.CoverFetchError(503, "Image fetch coordinator is closed")
        cached = await asyncio.to_thread(image_cache.cached_cover_bytes, url)
        if cached is not None:
            return ImageFetchResult(*cached, cache_hit=True)

        key = image_cache.image_cache_key(url)
        async with self._condition:
            if self._closed:
                raise image_cache.CoverFetchError(503, "Image fetch coordinator is closed")
            entry = self._inflight.get(key)
            if entry is not None:
                self._singleflight_joins += 1
                if priority == "foreground" and entry.phase != "active":
                    # A browser request waiting on queued prefetch work promotes
                    # the shared entry and supplies its freshest signed URL.
                    entry.priority = "foreground"
                    entry.url = url
                    self._condition.notify_all()
            else:
                entry = _InflightFetch(key=key, url=url, priority=priority)
                created_task = asyncio.create_task(
                    self._run_entry(entry),
                    name=f"image-fetch-{key[:12]}",
                )
                created_task.add_done_callback(self._consume_owned_task_result)
                entry.task = created_task
                self._inflight[key] = entry
            task: asyncio.Task[ImageFetchResult] | None = entry.task

        if task is None:  # pragma: no cover - construction invariant
            raise RuntimeError("image fetch task was not created")
        done, _pending = await asyncio.wait((task,))
        return next(iter(done)).result()

    async def prefetch(self, url: str) -> bool:
        """Best-effort background fetch; True only for a newly stored cover."""
        if not image_cache.is_allowed_image_url(url):
            return False
        try:
            result = await self.fetch(url, priority="background")
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return result.stored

    async def close(self) -> None:
        """Cancel every coordinator-owned upstream task and clear queued work."""
        async with self._condition:
            if self._closed and not self._inflight:
                return
            self._closed = True
            tasks = [entry.task for entry in self._inflight.values() if entry.task is not None]
            for task in tasks:
                task.cancel()
            self._condition.notify_all()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._condition:
            self._inflight.clear()
            self._condition.notify_all()

    def status_payload(self) -> dict[str, int]:
        """Return URL-free live and lifetime counters for runtime diagnostics."""
        return {
            "image_fetch_active": self._active,
            "image_fetch_waiting": sum(
                entry.phase == "waiting" for entry in self._inflight.values()
            ),
            "image_fetch_inflight_keys": len(self._inflight),
            "image_fetch_upstream_started": self._upstream_started,
            "image_fetch_singleflight_joins": self._singleflight_joins,
            "image_fetch_peak_active": self._peak_active,
            "image_fetch_peak_background": self._peak_background,
        }

    async def _run_entry(self, entry: _InflightFetch) -> ImageFetchResult:
        acquired = False
        try:
            # The first caller checked before creating the entry; repeat inside
            # the owned task to close the extension-write / prior-fetch race.
            cached = await asyncio.to_thread(image_cache.cached_cover_bytes, entry.url)
            if cached is not None:
                return ImageFetchResult(*cached, cache_hit=True)

            await self._acquire(entry)
            acquired = True
            fetcher = self.upstream_fetcher or image_cache.fetch_cover_bytes
            try:
                data, content_type = await fetcher(entry.url)
            except image_cache.CoverFetchError as exc:
                # A different process or extension upload may have filled the
                # cache while this upstream failed. Preserve the existing >=500
                # proxy race fallback without masking validation failures.
                if exc.status_code >= 500:
                    cached = await asyncio.to_thread(
                        image_cache.cached_cover_bytes,
                        entry.url,
                    )
                    if cached is not None:
                        return ImageFetchResult(*cached, cache_hit=True)
                raise
            stored = await asyncio.to_thread(
                image_cache.save_image_bytes,
                entry.url,
                data,
                content_type,
            )
            return ImageFetchResult(
                data=data,
                content_type=content_type,
                cache_hit=False,
                stored=stored,
            )
        finally:
            async with self._condition:
                if acquired:
                    self._active -= 1
                    if entry.active_priority == "background":
                        self._active_background -= 1
                if self._inflight.get(entry.key) is entry:
                    self._inflight.pop(entry.key, None)
                self._condition.notify_all()

    async def _acquire(self, entry: _InflightFetch) -> None:
        async with self._condition:
            entry.phase = "waiting"
            while True:
                if self._closed:
                    raise asyncio.CancelledError
                if self._slot_available(entry):
                    active_priority = entry.priority
                    entry.phase = "active"
                    entry.active_priority = active_priority
                    self._active += 1
                    if active_priority == "background":
                        self._active_background += 1
                    self._upstream_started += 1
                    self._peak_active = max(self._peak_active, self._active)
                    self._peak_background = max(
                        self._peak_background,
                        self._active_background,
                    )
                    return
                await self._condition.wait()

    def _slot_available(self, entry: _InflightFetch) -> bool:
        if self._active >= self.max_active:
            return False
        if entry.priority == "foreground":
            return True
        if self._active_background >= self.max_background:
            return False
        # Wakeups are not ordered. Keep background work parked while any
        # distinct foreground entry is waiting so scheduling order cannot steal
        # the newly released slot from an interactive request.
        return not any(
            other is not entry and other.phase == "waiting" and other.priority == "foreground"
            for other in self._inflight.values()
        )

    @staticmethod
    def _consume_owned_task_result(task: asyncio.Task[ImageFetchResult]) -> None:
        """Retrieve orphaned failures after all shielded waiters disappear."""
        with suppress(asyncio.CancelledError):
            task.exception()
