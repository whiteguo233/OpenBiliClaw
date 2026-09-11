"""Concurrency and lifecycle tests for the shared cover-fetch coordinator."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.runtime.image_cache import CoverFetchError, image_cache_key, save_image_bytes
from openbiliclaw.runtime.image_fetch import ImageFetchCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _url(name: str) -> str:
    return f"https://i1.hdslb.com/bfs/archive/{name}.jpg"


async def _wait_until(predicate: Callable[[], bool], *, attempts: int = 500) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("condition was not reached")


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = tmp_path / "image-cache"
    cache_dir.mkdir()
    monkeypatch.setattr("openbiliclaw.runtime.image_cache._CACHE_DIR", cache_dir)


class _GlobalBlockingFetcher:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: list[str] = []
        self.calls: Counter[str] = Counter()
        self.active = 0
        self.peak_active = 0

    async def __call__(self, url: str) -> tuple[bytes, str]:
        self.started.append(url)
        self.calls[url] += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return f"bytes:{url}".encode(), "image/jpeg"


class _PerUrlBlockingFetcher:
    def __init__(self, urls: list[str]) -> None:
        self.gates = {url: asyncio.Event() for url in urls}
        self.started: list[str] = []
        self.calls: Counter[str] = Counter()

    async def __call__(self, url: str) -> tuple[bytes, str]:
        self.started.append(url)
        self.calls[url] += 1
        await self.gates[url].wait()
        return f"bytes:{url}".encode(), "image/jpeg"

    def release_all(self) -> None:
        for gate in self.gates.values():
            gate.set()


async def test_total_and_background_caps_are_enforced() -> None:
    fetcher = _GlobalBlockingFetcher()
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher, max_active=4, max_background=3)
    background = [
        asyncio.create_task(coordinator.fetch(_url(f"bg-{index}"), priority="background"))
        for index in range(8)
    ]
    await _wait_until(lambda: len(fetcher.started) == 3)

    foreground = [asyncio.create_task(coordinator.fetch(_url(f"fg-{index}"))) for index in range(4)]
    await _wait_until(lambda: len(fetcher.started) == 4)

    status = coordinator.status_payload()
    assert status["image_fetch_active"] == 4
    assert status["image_fetch_peak_active"] == 4
    assert status["image_fetch_peak_background"] == 3
    assert fetcher.peak_active == 4

    fetcher.release.set()
    await asyncio.gather(*background, *foreground)
    assert coordinator.status_payload()["image_fetch_active"] == 0


async def test_released_slot_goes_to_waiting_foreground_before_background() -> None:
    active_bg = [_url(f"active-bg-{index}") for index in range(3)]
    active_fg = _url("active-fg")
    queued_bg = _url("queued-bg")
    queued_fg = _url("queued-fg")
    all_urls = [*active_bg, active_fg, queued_bg, queued_fg]
    fetcher = _PerUrlBlockingFetcher(all_urls)
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher, max_active=4, max_background=3)

    tasks = [
        asyncio.create_task(coordinator.fetch(url, priority="background")) for url in active_bg
    ]
    await _wait_until(lambda: len(fetcher.started) == 3)
    tasks.append(asyncio.create_task(coordinator.fetch(active_fg)))
    await _wait_until(lambda: len(fetcher.started) == 4)
    tasks.append(asyncio.create_task(coordinator.fetch(queued_bg, priority="background")))
    tasks.append(asyncio.create_task(coordinator.fetch(queued_fg)))
    await _wait_until(lambda: coordinator.status_payload()["image_fetch_waiting"] == 2)

    fetcher.gates[active_bg[0]].set()
    await _wait_until(lambda: queued_fg in fetcher.started)
    assert queued_bg not in fetcher.started

    fetcher.release_all()
    await asyncio.gather(*tasks)


async def test_same_key_concurrent_callers_use_one_upstream() -> None:
    url = _url("singleflight")
    fetcher = _GlobalBlockingFetcher()
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher)

    tasks = [asyncio.create_task(coordinator.fetch(url)) for _ in range(6)]
    await _wait_until(lambda: fetcher.calls[url] == 1)
    await _wait_until(lambda: coordinator.status_payload()["image_fetch_singleflight_joins"] == 5)
    assert coordinator.status_payload()["image_fetch_singleflight_joins"] == 5

    fetcher.release.set()
    results = await asyncio.gather(*tasks)
    assert {result.data for result in results} == {f"bytes:{url}".encode()}
    assert fetcher.calls[url] == 1


async def test_foreground_join_promotes_queued_background_same_key() -> None:
    blockers = [_url(f"promote-blocker-{index}") for index in range(3)]
    stale_target = (
        "https://sns-webpic-qc.xhscdn.com/202608010101/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/spectrum/shared-cover!webp"
    )
    fresh_target = (
        "https://sns-webpic-qc.xhscdn.com/202608011212/"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/spectrum/shared-cover!webp"
    )
    assert image_cache_key(stale_target) == image_cache_key(fresh_target)
    fetcher = _PerUrlBlockingFetcher([*blockers, stale_target, fresh_target])
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher, max_active=4, max_background=3)

    blocker_tasks = [
        asyncio.create_task(coordinator.fetch(url, priority="background")) for url in blockers
    ]
    await _wait_until(lambda: len(fetcher.started) == 3)
    background_waiter = asyncio.create_task(coordinator.fetch(stale_target, priority="background"))
    await _wait_until(lambda: coordinator.status_payload()["image_fetch_waiting"] == 1)

    foreground_waiter = asyncio.create_task(coordinator.fetch(fresh_target))
    await _wait_until(lambda: fresh_target in fetcher.started)
    assert stale_target not in fetcher.started
    assert fetcher.calls[fresh_target] == 1
    assert coordinator.status_payload()["image_fetch_singleflight_joins"] == 1

    fetcher.release_all()
    background_result, foreground_result = await asyncio.gather(
        background_waiter,
        foreground_waiter,
    )
    await asyncio.gather(*blocker_tasks)
    assert background_result.data == foreground_result.data


async def test_cancelling_one_waiter_does_not_cancel_shared_upstream() -> None:
    url = _url("cancel-one")
    fetcher = _GlobalBlockingFetcher()
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher)
    cancelled_waiter = asyncio.create_task(coordinator.fetch(url))
    surviving_waiter = asyncio.create_task(coordinator.fetch(url))
    await _wait_until(lambda: fetcher.calls[url] == 1)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    assert coordinator.status_payload()["image_fetch_active"] == 1

    fetcher.release.set()
    result = await surviving_waiter
    assert result.data == f"bytes:{url}".encode()
    assert fetcher.calls[url] == 1


async def test_orphaned_upstream_failure_is_retrieved_after_only_waiter_cancels() -> None:
    url = _url("orphan-failure?token=do-not-log")
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_release(_url: str) -> tuple[bytes, str]:
        started.set()
        await release.wait()
        raise CoverFetchError(502, "Upstream request failed")

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop_errors: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
    coordinator = ImageFetchCoordinator(upstream_fetcher=fail_after_release)
    try:
        waiter = asyncio.create_task(coordinator.fetch(url))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await _wait_until(lambda: coordinator.status_payload()["image_fetch_inflight_keys"] == 0)
        await asyncio.sleep(0)
        assert loop_errors == []
    finally:
        await coordinator.close()
        loop.set_exception_handler(previous_handler)


async def test_failure_cleans_singleflight_entry_and_retry_starts_fresh() -> None:
    url = _url("retry")
    calls = 0

    async def flaky(_url: str) -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CoverFetchError(502, "Upstream request failed")
        return b"recovered", "image/jpeg"

    coordinator = ImageFetchCoordinator(upstream_fetcher=flaky)
    with pytest.raises(CoverFetchError):
        await coordinator.fetch(url)
    assert coordinator.status_payload()["image_fetch_inflight_keys"] == 0

    result = await coordinator.fetch(url)
    assert result.data == b"recovered"
    assert calls == 2
    assert coordinator.status_payload()["image_fetch_upstream_started"] == 2


async def test_cache_hit_uses_no_gate_or_upstream_slot() -> None:
    url = _url("cache-hit")
    assert save_image_bytes(url, b"cached", "image/jpeg") is True

    async def unexpected(_url: str) -> tuple[bytes, str]:
        raise AssertionError("cache hit must not call upstream")

    coordinator = ImageFetchCoordinator(upstream_fetcher=unexpected)
    result = await coordinator.fetch(url)
    assert result.cache_hit is True
    assert result.data == b"cached"
    assert coordinator.status_payload() == {
        "image_fetch_active": 0,
        "image_fetch_waiting": 0,
        "image_fetch_inflight_keys": 0,
        "image_fetch_upstream_started": 0,
        "image_fetch_singleflight_joins": 0,
        "image_fetch_peak_active": 0,
        "image_fetch_peak_background": 0,
    }


async def test_shutdown_cancels_active_and_queued_work_and_rejects_new_fetches() -> None:
    fetcher = _GlobalBlockingFetcher()
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher, max_active=4, max_background=3)
    tasks = [
        asyncio.create_task(coordinator.fetch(_url(f"shutdown-bg-{index}"), priority="background"))
        for index in range(5)
    ]
    tasks.extend(
        asyncio.create_task(coordinator.fetch(_url(f"shutdown-fg-{index}"))) for index in range(2)
    )
    await _wait_until(
        lambda: (
            coordinator.status_payload()["image_fetch_active"] == 4
            and coordinator.status_payload()["image_fetch_waiting"] == 3
        )
    )

    await coordinator.close()
    await asyncio.gather(*tasks, return_exceptions=True)
    status = coordinator.status_payload()
    assert status["image_fetch_active"] == 0
    assert status["image_fetch_waiting"] == 0
    assert status["image_fetch_inflight_keys"] == 0

    cached_url = _url("closed-cache-hit")
    assert save_image_bytes(cached_url, b"cached", "image/jpeg") is True
    with pytest.raises(CoverFetchError) as exc:
        await coordinator.fetch(cached_url)
    assert exc.value.status_code == 503


async def test_status_counters_are_correct_and_contain_no_url_data() -> None:
    signed_url = _url("signed?token=secret-value")
    fetcher = _GlobalBlockingFetcher()
    coordinator = ImageFetchCoordinator(upstream_fetcher=fetcher)
    first = asyncio.create_task(coordinator.fetch(signed_url))
    second = asyncio.create_task(coordinator.fetch(signed_url))
    await _wait_until(lambda: fetcher.calls[signed_url] == 1)

    live_status = coordinator.status_payload()
    assert live_status["image_fetch_active"] == 1
    assert live_status["image_fetch_inflight_keys"] == 1
    assert live_status["image_fetch_singleflight_joins"] == 1
    assert "secret-value" not in json.dumps(live_status)

    fetcher.release.set()
    await asyncio.gather(first, second)
    final_status = coordinator.status_payload()
    assert final_status["image_fetch_upstream_started"] == 1
    assert final_status["image_fetch_peak_active"] == 1
