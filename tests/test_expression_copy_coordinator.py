from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.base import LLMFallbackError
from openbiliclaw.recommendation.engine import ExpressionCopyTransientError
from openbiliclaw.runtime.expression_copy import ExpressionCopyCoordinator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _Pending:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def __call__(self) -> float:
        return self.now

    async def wait(self, delay: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now + delay, future))
        await future

    async def until_waiting(self) -> None:
        await _wait_until(lambda: bool(self._waiters))

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        ready = [item for item in self._waiters if item[0] <= self.now]
        self._waiters = [item for item in self._waiters if item[0] > self.now]
        for _, future in ready:
            if not future.done():
                future.set_result(None)
        await asyncio.sleep(0)


async def _wait_until(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def _coordinator(
    pending: _Pending,
    drain: Callable[[int], int | Awaitable[int]],
    **kwargs: object,
) -> ExpressionCopyCoordinator:
    return ExpressionCopyCoordinator(
        pending_count_provider=pending,
        drain_callback=drain,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_eight_pending_starts_immediately() -> None:
    pending = _Pending(8)
    started = asyncio.Event()
    coordinator = _coordinator(pending, lambda limit: started.set() or min(limit, pending.value))
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("candidate_admitted")
    await asyncio.wait_for(started.wait(), timeout=0.2)
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_tail_batch_uses_one_three_second_deadline() -> None:
    clock = _FakeClock()
    pending = _Pending(1)
    calls: list[tuple[float, int]] = []
    coordinator = _coordinator(
        pending,
        lambda limit: calls.append((clock.now, limit)) or pending.value,
        time_fn=clock,
        wait_fn=clock.wait,
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("one")
    await clock.until_waiting()
    first_deadline = coordinator.status_payload()["expression_batch_deadline"]
    pending.value = 7
    coordinator.notify("seven")
    await asyncio.sleep(0)
    assert coordinator.status_payload()["expression_batch_deadline"] == first_deadline
    await clock.advance(3.0)
    await _wait_until(lambda: len(calls) == 1)
    assert calls[0][0] == 3.0
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_threshold_accelerates_existing_tail_window() -> None:
    clock = _FakeClock()
    pending = _Pending(1)
    calls: list[int] = []
    coordinator = _coordinator(
        pending,
        lambda limit: calls.append(limit) or pending.value,
        time_fn=clock,
        wait_fn=clock.wait,
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("one")
    await clock.until_waiting()
    pending.value = 8
    coordinator.notify("eight")
    await _wait_until(lambda: calls == [8])
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_running_notifications_coalesce_into_durable_recheck() -> None:
    pending = _Pending(8)
    release = asyncio.Event()
    calls: list[int] = []

    async def drain(limit: int) -> int:
        calls.append(limit)
        if len(calls) == 1:
            await release.wait()
        completed = min(limit, pending.value)
        pending.value -= completed
        return completed

    coordinator = _coordinator(pending, drain)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await _wait_until(lambda: len(calls) == 1)
    pending.value = 17
    for index in range(20):
        coordinator.notify(f"during:{index}")
    release.set()
    await _wait_until(lambda: len(calls) == 2)
    await asyncio.sleep(0)
    assert calls == [8, 9]
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_seventy_five_drains_as_sixty_then_fifteen_serially() -> None:
    pending = _Pending(75)
    calls: list[int] = []
    active = 0
    max_active = 0

    async def drain(limit: int) -> int:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        calls.append(limit)
        pending.value -= limit
        active -= 1
        return limit

    coordinator = _coordinator(pending, drain)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await _wait_until(lambda: pending.value == 0)
    assert calls == [60, 15]
    assert max_active == 1
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_zero_progress_backs_off_fifteen_seconds() -> None:
    clock = _FakeClock()
    pending = _Pending(8)
    calls: list[int] = []
    coordinator = _coordinator(
        pending, lambda limit: calls.append(limit) or 0, time_fn=clock, wait_fn=clock.wait
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await _wait_until(lambda: calls == [8])
    await _wait_until(lambda: coordinator.status_payload()["expression_batch_state"] == "backoff")
    assert coordinator.status_payload()["expression_batch_deadline"] == 15.0
    await asyncio.sleep(0)
    assert calls == [8]
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_transient_error_uses_retry_after_and_preserves_completed_progress() -> None:
    clock = _FakeClock()
    pending = _Pending(8)

    def drain(_limit: int) -> int:
        raise ExpressionCopyTransientError(kind="connection", completed=3, retry_after=45.0)

    coordinator = _coordinator(pending, drain, time_fn=clock, wait_fn=clock.wait)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await _wait_until(lambda: coordinator.status_payload()["expression_batch_state"] == "backoff")
    assert coordinator.status_payload()["expression_batch_deadline"] == 45.0
    assert coordinator.status_payload()["expression_last_completed"] == 3
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_no_provider_pauses_until_exact_config_notification() -> None:
    pending = _Pending(8)
    calls = 0

    def drain(_limit: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            error = LLMFallbackError("No provider was available to process the request.")
            error.completed = 2  # type: ignore[attr-defined]
            raise error
        pending.value = 0
        return 8

    coordinator = _coordinator(pending, drain, safety_wake_seconds=0.01)
    task = asyncio.create_task(coordinator.run_forever())
    await _wait_until(lambda: coordinator.status_payload()["expression_batch_state"] == "paused")
    assert coordinator.status_payload()["expression_last_completed"] == 2
    coordinator.notify("configurationless")
    await asyncio.sleep(0.03)
    assert calls == 1
    coordinator.notify("config_reloaded")
    await _wait_until(lambda: calls == 2)
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_progress_with_tail_starts_new_fixed_window() -> None:
    clock = _FakeClock()
    pending = _Pending(9)

    def drain(limit: int) -> int:
        pending.value = 1
        return 8

    coordinator = _coordinator(pending, drain, time_fn=clock, wait_fn=clock.wait)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await _wait_until(lambda: coordinator.status_payload()["expression_last_completed"] == 8)
    await _wait_until(
        lambda: coordinator.status_payload()["expression_batch_state"] == "collecting"
    )
    assert coordinator.status_payload()["expression_batch_deadline"] == 3.0
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_stop_cancels_running_callback() -> None:
    pending = _Pending(8)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def drain(_limit: int) -> int:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    coordinator = _coordinator(pending, drain)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await entered.wait()
    await coordinator.stop()
    await task
    assert cancelled.is_set()
    assert coordinator.status_payload()["expression_batch_state"] == "stopping"


@pytest.mark.asyncio
async def test_stop_consumes_already_failed_callback_without_raising() -> None:
    pending = _Pending(8)
    failed = asyncio.Event()

    async def drain(_limit: int) -> int:
        failed.set()
        raise RuntimeError("provider exploded")

    coordinator = _coordinator(pending, drain)
    collector = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await failed.wait()

    await coordinator.stop()
    await collector

    status = coordinator.status_payload()
    assert status["expression_batch_state"] == "stopping"
    assert status["expression_last_error"] == "provider exploded"


@pytest.mark.asyncio
async def test_stale_generation_notification_cannot_wake_replacement() -> None:
    old_pending = _Pending(0)
    new_pending = _Pending(0)
    old = _coordinator(old_pending, lambda _limit: 0)
    new_calls: list[int] = []
    new = _coordinator(new_pending, lambda limit: new_calls.append(limit) or limit)
    old_task = asyncio.create_task(old.run_forever())
    await old.stop()
    await old_task
    new_task = asyncio.create_task(new.run_forever())
    old_pending.value = 8
    old.notify("stale")
    await asyncio.sleep(0)
    assert new_calls == []
    await new.stop()
    await new_task


@pytest.mark.asyncio
async def test_work_allowed_false_parks_loop_without_drain() -> None:
    pending = _Pending(8)
    calls: list[int] = []
    coordinator = _coordinator(
        pending,
        lambda limit: calls.append(limit) or limit,
        work_allowed=lambda: False,
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("candidate_admitted")
    await asyncio.sleep(0.05)

    assert calls == []
    assert coordinator.status_payload()["expression_batch_state"] == "paused"

    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_transient_backoff_logs_delay_pending_and_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = _FakeClock()
    pending = _Pending(19)
    calls = 0

    def drain(_limit: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExpressionCopyTransientError(kind="rate_limited", completed=0, retry_after=45.0)
        pending.value = 0
        return 8

    coordinator = _coordinator(pending, drain, time_fn=clock, wait_fn=clock.wait)
    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.expression_copy"):
        task = asyncio.create_task(coordinator.run_forever())
        coordinator.notify("start")
        await _wait_until(
            lambda: coordinator.status_payload()["expression_batch_state"] == "backoff"
        )
        # The clock may hold a stale cancelled safety-wake waiter, so wait for
        # the actual backoff waiter before advancing.
        await _wait_until(
            lambda: any(
                deadline == 45.0 and not future.done() for deadline, future in clock._waiters
            )
        )
        await clock.advance(45.0)
        await _wait_until(lambda: coordinator.status_payload()["expression_last_completed"] == 8)
        await coordinator.stop()
        await task

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "transient failure (rate_limited)" in message
        and "backing off 45s" in message
        and "pending=19" in message
        and "streak=1" in message
        for message in warnings
    )
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("expression copy recovered: completed=8 pending=0" in message for message in infos)


@pytest.mark.asyncio
async def test_zero_progress_backoff_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    clock = _FakeClock()
    pending = _Pending(8)
    calls: list[int] = []
    coordinator = _coordinator(
        pending, lambda limit: calls.append(limit) or 0, time_fn=clock, wait_fn=clock.wait
    )
    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.expression_copy"):
        task = asyncio.create_task(coordinator.run_forever())
        coordinator.notify("start")
        await _wait_until(lambda: calls == [8])
        await _wait_until(
            lambda: coordinator.status_payload()["expression_batch_state"] == "backoff"
        )
        await coordinator.stop()
        await task

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "made no progress" in message and "15s" in message and "pending=8" in message
        for message in warnings
    )


@pytest.mark.asyncio
async def test_pause_and_resume_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    pending = _Pending(8)
    calls = 0

    def drain(_limit: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMFallbackError("No provider was available to process the request.")
        pending.value = 0
        return 8

    coordinator = _coordinator(pending, drain, safety_wake_seconds=0.01)
    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.expression_copy"):
        task = asyncio.create_task(coordinator.run_forever())
        await _wait_until(
            lambda: coordinator.status_payload()["expression_batch_state"] == "paused"
        )
        coordinator.notify("config_reloaded")
        await _wait_until(lambda: calls == 2)
        await coordinator.stop()
        await task

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "expression copy paused on no_provider" in message and "pending=8" in message
        for message in messages
    )
    assert any("expression copy resumed on config_reloaded" in message for message in messages)


@pytest.mark.asyncio
async def test_clean_drain_does_not_spam_warnings(caplog: pytest.LogCaptureFixture) -> None:
    pending = _Pending(8)

    def drain(limit: int) -> int:
        pending.value = 0
        return min(limit, 8)

    coordinator = _coordinator(pending, drain)
    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.expression_copy"):
        task = asyncio.create_task(coordinator.run_forever())
        coordinator.notify("start")
        await _wait_until(lambda: coordinator.status_payload()["expression_last_completed"] == 8)
        await coordinator.stop()
        await task

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
