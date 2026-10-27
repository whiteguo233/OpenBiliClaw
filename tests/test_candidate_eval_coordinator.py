from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.discovery.candidate_pipeline import (
    CandidateEvalClaim,
    CandidateEvalOutcome,
    DiscoveryCandidatePipeline,
)
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.llm.base import LLMFallbackError, LLMRateLimitError
from openbiliclaw.llm.service import LLMProviderExecutionError
from openbiliclaw.runtime.candidate_eval import (
    CandidateEvalCoordinator,
    CandidateEvalSnapshot,
    effective_candidate_eval_workers,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent


@dataclass
class _FakeBatch:
    claim: CandidateEvalClaim
    future: asyncio.Future[dict[str, int]]


class _FakeStagedPipeline:
    def __init__(self, candidate_count: int) -> None:
        self.pending_eval = candidate_count
        self.available = 0
        self.started: list[_FakeBatch] = []
        self.released_tokens: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._started_event = asyncio.Event()
        self.completion_limits: list[int | None] = []
        self.admit_limits: list[int] = []
        self.evaluated_pending_admission = 0
        self.admitted_pending_copy = 0

    def claim_batch(self, *, limit: int) -> CandidateEvalClaim | None:
        count = min(limit, self.pending_eval)
        if count <= 0:
            return None
        offset = sum(len(batch.claim.rows) for batch in self.started)
        token = f"claim-{len(self.started)}"
        rows = tuple({"id": offset + index + 1, "claim_token": token} for index in range(count))
        claim = CandidateEvalClaim(token=token, rows=rows, items=tuple(object() for _ in rows))  # type: ignore[arg-type]
        self.pending_eval -= count
        future: asyncio.Future[dict[str, int]] = asyncio.get_running_loop().create_future()
        self.started.append(_FakeBatch(claim=claim, future=future))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self._started_event.set()
        return claim

    async def evaluate_claim(self, claim: CandidateEvalClaim, profile: Any) -> CandidateEvalOutcome:
        batch = next(batch for batch in self.started if batch.claim.token == claim.token)
        await batch.future
        return CandidateEvalOutcome(
            claim=claim, scores=(0.9,) * len(claim.rows), elapsed_seconds=0.1
        )

    async def complete_claim(
        self,
        outcome: CandidateEvalOutcome,
        *,
        admission_limit: int | None = None,
    ) -> dict[str, int]:
        batch = next(batch for batch in self.started if batch.claim.token == outcome.claim.token)
        raw_result = batch.future.result()
        self.completion_limits.append(admission_limit)
        cached = min(int(raw_result.get("cached", 0)), admission_limit or 0)
        result = {**raw_result, "cached": cached, "rejected": 0}
        self.in_flight -= 1
        self.available += cached
        self.admitted_pending_copy += cached
        self.evaluated_pending_admission += max(0, int(result.get("evaluated", 0)) - cached)
        return result

    def admit_evaluated(self, *, limit: int) -> dict[str, int]:
        self.admit_limits.append(limit)
        cached = min(limit, self.evaluated_pending_admission)
        self.evaluated_pending_admission -= cached
        self.admitted_pending_copy += cached
        return {"cached": cached, "rejected": 0}

    def release_claim(
        self,
        claim: CandidateEvalClaim,
        *,
        reason: str,
        increment_attempts: bool = False,
    ) -> int:
        if claim.token in self.released_tokens:
            return 0
        self.released_tokens.append(claim.token)
        self.pending_eval += len(claim.rows)
        self.in_flight = max(0, self.in_flight - 1)
        return len(claim.rows)

    async def wait_for_started(self, count: int) -> None:
        async with asyncio.timeout(2):
            while len(self.started) < count:
                self._started_event.clear()
                await self._started_event.wait()

    def finish(self, index: int, *, cached: int) -> None:
        batch = self.started[index]
        if not batch.future.done():
            batch.future.set_result(
                {
                    "evaluated": len(batch.claim.rows),
                    "cached": cached,
                    "rejected": len(batch.claim.rows) - cached,
                    "stale": 0,
                }
            )

    def fail(self, index: int, error: BaseException) -> None:
        batch = self.started[index]
        if not batch.future.done():
            batch.future.set_exception(error)


def _coordinator(
    pipeline: _FakeStagedPipeline,
    *,
    worker_count: int = 3,
    target: int = 600,
) -> CandidateEvalCoordinator:
    return CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=target,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=worker_count,
        batch_size=30,
        safety_wake_seconds=0.05,
    )


def test_effective_candidate_eval_workers_reserves_one_llm_slot() -> None:
    assert effective_candidate_eval_workers(3, 4) == 3
    assert effective_candidate_eval_workers(3, 3) == 2
    assert effective_candidate_eval_workers(8, 9) == 3
    assert effective_candidate_eval_workers(8, 1) == 1
    assert effective_candidate_eval_workers(0, 99) == 1


def test_coordinator_uses_pipeline_coalescing_claim() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=3)
    ready_calls: list[int] = []

    def claim_ready_batch(*, limit: int) -> None:
        ready_calls.append(limit)
        return None

    pipeline.claim_ready_batch = claim_ready_batch  # type: ignore[attr-defined]
    pipeline.eval_ready_in_seconds = lambda *, limit: 42.0  # type: ignore[attr-defined]
    coordinator = _coordinator(pipeline, worker_count=1)

    delay = coordinator._fill_open_slots()  # noqa: SLF001

    assert ready_calls == [30]
    assert delay == pytest.approx(42.0)
    assert pipeline.started == []


@pytest.mark.asyncio
async def test_coordinator_caps_worker_claims_at_three_batches_and_ninety_raw() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=240)
    coordinator = _coordinator(pipeline, worker_count=8)

    assert coordinator.worker_count == 3
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("test-cap")
    await pipeline.wait_for_started(3)

    assert len(pipeline.started) == 3
    assert [len(batch.claim.rows) for batch in pipeline.started] == [30, 30, 30]
    assert sum(len(batch.claim.rows) for batch in pipeline.started) == 90
    assert pipeline.max_in_flight == 3

    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_run_forever_can_be_reentered_after_stop() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=90)
    coordinator = _coordinator(pipeline)

    first = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("first-run")
    await pipeline.wait_for_started(3)
    assert len(pipeline.started) == 3

    await coordinator.stop()
    await first
    assert coordinator._stopping is True  # noqa: SLF001

    # Re-entering the same coordinator after a stop must self-heal instead of
    # exiting immediately at the first ``while not self._stopping`` check.
    second = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("second-run")
    await pipeline.wait_for_started(6)

    assert len(pipeline.started) == 6
    assert coordinator._stopping is False  # noqa: SLF001
    assert coordinator.state != "stopping"

    await coordinator.stop()
    await second


def test_projected_inventory_excludes_unscored_raw() -> None:
    snapshot = CandidateEvalSnapshot(
        available=2,
        target=10,
        pending_eval=500,
        evaluating=60,
        evaluated_pending_admission=3,
        admitted_pending_copy=4,
    )

    assert CandidateEvalCoordinator._projected_inventory(snapshot) == 9


def test_projected_inventory_counts_only_pending_copy_that_can_become_available() -> None:
    snapshot = CandidateEvalSnapshot(
        available=2,
        target=10,
        pending_eval=500,
        evaluating=60,
        evaluated_pending_admission=3,
        admitted_pending_copy=40,
        admitted_pending_available=1,
    )

    assert CandidateEvalCoordinator._projected_inventory(snapshot) == 6


def test_admission_headroom_counts_only_pending_copy_that_can_become_available() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    pipeline.evaluated_pending_admission = 10
    coordinator = _coordinator(pipeline, target=10)
    snapshot = CandidateEvalSnapshot(
        available=4,
        target=10,
        pending_eval=0,
        evaluating=0,
        evaluated_pending_admission=10,
        admitted_pending_copy=40,
        admitted_pending_available=2,
    )

    coordinator._admit_evaluated(snapshot)  # noqa: SLF001

    assert pipeline.admit_limits == [4]


def test_stale_evaluated_waiters_trigger_cleanup_without_inventory_headroom() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    coordinator = _coordinator(pipeline, target=10)
    snapshot = CandidateEvalSnapshot(
        available=10,
        target=10,
        pending_eval=0,
        evaluating=0,
        evaluated_pending_admission=0,
        admitted_pending_copy=0,
        evaluated_waiting_total=1,
    )

    coordinator._admit_evaluated(snapshot)  # noqa: SLF001

    assert pipeline.admit_limits == [0]


@pytest.mark.asyncio
async def test_three_workers_refill_fast_slot_without_waiting_for_slow_slots() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    coordinator = _coordinator(pipeline)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("test")
    await pipeline.wait_for_started(3)
    assert pipeline.max_in_flight == 3

    pipeline.finish(0, cached=10)
    await pipeline.wait_for_started(4)

    assert pipeline.started[1].future.done() is False
    assert pipeline.started[2].future.done() is False
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_fast_worker_refills_under_one_second_with_sixty_second_safety_wake() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    completed_at = 0.0
    claimed_at = 0.0
    original_complete = pipeline.complete_claim
    original_claim = pipeline.claim_batch

    async def complete(
        outcome: CandidateEvalOutcome, *, admission_limit: int | None = None
    ) -> dict[str, int]:
        nonlocal completed_at
        result = await original_complete(outcome, admission_limit=admission_limit)
        completed_at = asyncio.get_running_loop().time()
        return result

    def claim(*, limit: int) -> CandidateEvalClaim | None:
        nonlocal claimed_at
        result = original_claim(limit=limit)
        if result is not None and len(pipeline.started) == 4:
            claimed_at = asyncio.get_running_loop().time()
        return result

    pipeline.complete_claim = complete  # type: ignore[method-assign]
    pipeline.claim_batch = claim  # type: ignore[method-assign]
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=600,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        safety_wake_seconds=60.0,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=1)
    await pipeline.wait_for_started(4)

    assert 0 <= claimed_at - completed_at < 1.0
    assert pipeline.started[1].future.done() is False
    assert pipeline.started[2].future.done() is False
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_post_commit_hook_does_not_block_fast_slot_refill() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    hook_started = asyncio.Event()
    hook_release = asyncio.Event()

    async def post_commit() -> None:
        hook_started.set()
        await hook_release.wait()

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=600,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        post_commit_callback=post_commit,
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=10)

    await asyncio.wait_for(hook_started.wait(), timeout=2)
    await pipeline.wait_for_started(4)
    assert hook_release.is_set() is False

    hook_release.set()
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_admitted_pending_copy_inventory_stops_new_claims_at_target() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=10,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=10)
    async with asyncio.timeout(2):
        while pipeline.available < 10:
            await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    assert len(pipeline.started) == 3
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_existing_pending_copy_at_target_avoids_claim_and_admission() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    pipeline.admitted_pending_copy = 10
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=10,
            pending_eval=pipeline.pending_eval,
            evaluating=0,
            evaluated_pending_admission=0,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await asyncio.sleep(0.05)

    assert pipeline.started == []
    assert pipeline.admit_limits == []
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_admits_evaluated_rows_before_projected_target_stop() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=30)
    pipeline.evaluated_pending_admission = 10
    admitted: list[int] = []
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=10,
            pending_eval=pipeline.pending_eval,
            evaluating=0,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        on_admitted=admitted.append,
        safety_wake_seconds=60.0,
    )
    task = asyncio.create_task(coordinator.run_forever())
    async with asyncio.timeout(2):
        while not pipeline.admit_limits:
            await asyncio.sleep(0)

    assert pipeline.admit_limits == [10]
    assert admitted == [10]
    assert pipeline.started == []
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_coordinator_runs_pre_admit_hook_before_admission_each_tick() -> None:
    # Pool-share fairness (spec 2026-07-20, Phase 3/4 via D7): the share
    # rebalance + deficit summary hooks live on the controller but the
    # production assembly replaces _loop_candidate_eval with the coordinator,
    # so they were dead code. The coordinator must invoke a pre-admit hook each
    # tick BEFORE admitting evaluated rows, so a freed slot is admitted the same
    # tick.
    pipeline = _FakeStagedPipeline(candidate_count=0)
    pipeline.evaluated_pending_admission = 5
    events: list[str] = []
    original_admit = pipeline.admit_evaluated

    def _spy_admit(*, limit: int) -> dict[str, int]:
        events.append("admit")
        return original_admit(limit=limit)

    pipeline.admit_evaluated = _spy_admit  # type: ignore[method-assign]

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=300,
            pending_eval=pipeline.pending_eval,
            evaluating=0,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        pre_admit_hook=lambda: events.append("hook"),
        safety_wake_seconds=60.0,
    )
    task = asyncio.create_task(coordinator.run_forever())
    async with asyncio.timeout(2):
        while "admit" not in events:
            await asyncio.sleep(0)
    await coordinator.stop()
    await task

    assert events[0] == "hook"
    assert events[:2] == ["hook", "admit"]


@pytest.mark.asyncio
async def test_serial_worker_commits_use_remaining_copy_aware_headroom() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=90)
    admitted: list[int] = []
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=10,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        on_admitted=admitted.append,
        safety_wake_seconds=60.0,
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=30)
    pipeline.finish(1, cached=30)
    pipeline.finish(2, cached=30)
    async with asyncio.timeout(2):
        while len(pipeline.completion_limits) < 3:
            await asyncio.sleep(0)

    assert pipeline.completion_limits == [10, 0, 0]
    assert pipeline.available == 10
    assert pipeline.evaluated_pending_admission == 80
    assert admitted == [10]
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_target_stops_new_claims_and_stop_releases_in_flight() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=90)
    coordinator = _coordinator(pipeline, target=10)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("start")
    await pipeline.wait_for_started(3)
    pipeline.finish(0, cached=10)
    async with asyncio.timeout(2):
        while pipeline.available < 10:
            await asyncio.sleep(0)
    await asyncio.sleep(0.08)

    assert len(pipeline.started) == 3
    await coordinator.stop()
    await task
    assert set(pipeline.released_tokens) == {"claim-1", "claim-2"}
    assert pipeline.pending_eval == 60


@pytest.mark.asyncio
async def test_notify_at_idle_boundary_is_not_lost() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    coordinator = _coordinator(pipeline, worker_count=1)
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("empty")
    await asyncio.sleep(0)
    pipeline.pending_eval = 30
    coordinator.notify("candidate_enqueued:test")

    await pipeline.wait_for_started(1)
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_rate_limit_uses_provider_retry_after() -> None:
    now = [100.0]
    pipeline = _FakeStagedPipeline(candidate_count=30)
    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=600,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=1,
        batch_size=30,
        safety_wake_seconds=0.01,
        time_fn=lambda: now[0],
    )
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(1)
    error = LLMRateLimitError("rate limited")
    error.retry_after = 45  # type: ignore[attr-defined]
    pipeline.fail(0, error)
    async with asyncio.timeout(2):
        while coordinator.state != "backoff":
            await asyncio.sleep(0)

    assert coordinator.status_payload()["candidate_eval_backoff_until"] == 145.0
    assert pipeline.pending_eval == 30
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_no_provider_pauses_until_config_notification() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=30)
    coordinator = _coordinator(pipeline, worker_count=1)
    task = asyncio.create_task(coordinator.run_forever())
    await pipeline.wait_for_started(1)
    pipeline.fail(0, LLMFallbackError("No provider was available to process the request."))
    async with asyncio.timeout(2):
        while coordinator.state != "paused":
            await asyncio.sleep(0)

    coordinator.notify("candidate_enqueued:test")
    await asyncio.sleep(0.08)
    assert len(pipeline.started) == 1
    coordinator.notify("config_reloaded")
    await pipeline.wait_for_started(2)

    await coordinator.stop()
    await task


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("startup", True),
        ("config_reloaded", True),
        ("manual_retry", True),
        ("presence", False),
        ("configurationless", False),
        ("manual", False),
    ],
)
def test_auth_pause_resume_notification_policy_is_exact(reason: str, expected: bool) -> None:
    assert CandidateEvalCoordinator._resume_notification(reason) is expected


@pytest.mark.parametrize(
    ("error", "expected_until"),
    [
        (LLMRateLimitError("429"), 145.0),
        (TimeoutError("timed out"), 145.0),
        (ConnectionError("connection reset"), 145.0),
        (LLMProviderExecutionError("HTTP 503"), 145.0),
    ],
)
def test_all_candidate_transients_honor_retry_after(
    error: BaseException, expected_until: float
) -> None:
    error.retry_after = 45  # type: ignore[attr-defined]
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0), worker_count=1)
    coordinator.time_fn = lambda: 100.0
    coordinator._record_failure(error)
    assert coordinator.status_payload()["candidate_eval_backoff_until"] == expected_until


@pytest.mark.asyncio
async def test_three_zero_cache_batches_trigger_supply_and_backoff() -> None:
    pipeline = _FakeStagedPipeline(candidate_count=120)
    supply_reasons: list[str] = []

    async def request_supply(reason: str) -> None:
        supply_reasons.append(reason)

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,  # type: ignore[arg-type]
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=pipeline.available,
            target=600,
            pending_eval=pipeline.pending_eval,
            evaluating=pipeline.in_flight * 30,
            evaluated_pending_admission=pipeline.evaluated_pending_admission,
            admitted_pending_copy=pipeline.admitted_pending_copy,
        ),
        profile_provider=lambda: object(),
        worker_count=1,
        batch_size=30,
        supply_callback=request_supply,
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    for index in range(3):
        await pipeline.wait_for_started(index + 1)
        pipeline.finish(index, cached=0)
    async with asyncio.timeout(2):
        while coordinator.state != "backoff" or not supply_reasons:
            await asyncio.sleep(0)

    assert supply_reasons == ["candidate_eval_no_progress"]
    await coordinator.stop()
    await task


@pytest.mark.asyncio
async def test_unproductive_supply_backs_off_exponentially() -> None:
    now = [100.0]
    supply_calls: list[float] = []
    pipeline = _FakeStagedPipeline(candidate_count=0)

    async def request_supply(reason: str) -> dict[str, object]:
        assert reason == "candidate_supply"
        supply_calls.append(now[0])
        return {"refreshed": False, "reason": "below_threshold"}

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(
            available=0,
            target=600,
            pending_eval=0,
            evaluating=0,
            evaluated_pending_admission=0,
            admitted_pending_copy=0,
        ),
        profile_provider=lambda: object(),
        supply_callback=request_supply,
        safety_wake_seconds=0.001,
        time_fn=lambda: now[0],
    )
    task = asyncio.create_task(coordinator.run_forever())
    simulated_hour_end = now[0] + 3600.0
    async with asyncio.timeout(2):
        while True:
            while coordinator._supply_cooldown_until <= now[0]:
                await asyncio.sleep(0)
            if coordinator._supply_cooldown_until > simulated_hour_end:
                break
            now[0] = coordinator._supply_cooldown_until
            coordinator.notify("clock_advanced")

    await coordinator.stop()
    await task

    intervals = [
        later - earlier for earlier, later in zip(supply_calls, supply_calls[1:], strict=False)
    ]
    assert len(supply_calls) <= 10
    assert intervals == sorted(intervals)


@pytest.mark.asyncio
async def test_notify_pierces_supply_cooldown_once() -> None:
    now = [100.0]
    supply_calls: list[float] = []
    pipeline = _FakeStagedPipeline(candidate_count=0)

    async def request_supply(reason: str) -> dict[str, object]:
        supply_calls.append(now[0])
        return {"refreshed": False, "reason": "below_threshold"}

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=lambda: CandidateEvalSnapshot(0, 600, 0, 0, 0, 0),
        profile_provider=lambda: object(),
        supply_callback=request_supply,
        safety_wake_seconds=60.0,
        time_fn=lambda: now[0],
    )
    task = asyncio.create_task(coordinator.run_forever())
    async with asyncio.timeout(2):
        while coordinator._supply_streak < 1:
            await asyncio.sleep(0)

    coordinator.notify("candidate_commit")
    async with asyncio.timeout(2):
        while coordinator._supply_streak < 2:
            await asyncio.sleep(0)
    for _ in range(10):
        await asyncio.sleep(0)

    assert supply_calls == [100.0, 100.0]
    assert coordinator._supply_cooldown_until == 160.0
    await coordinator.stop()
    await task


def test_manual_notify_resets_supply_ladder() -> None:
    now = [100.0]
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator.time_fn = lambda: now[0]
    for _ in range(5):
        coordinator._record_supply_result(productive=False)

    coordinator.notify("manual_refresh")
    coordinator._record_supply_result(productive=False)

    assert coordinator._supply_streak == 1
    assert coordinator._supply_cooldown_until == 130.0


@pytest.mark.asyncio
async def test_productive_supply_resets_ladder() -> None:
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator._record_supply_result(productive=False)
    coordinator._record_supply_result(productive=False)
    coordinator.supply_callback = lambda reason: {"refreshed": True}

    coordinator._request_supply("test")
    assert coordinator._supply_task is not None
    await coordinator._supply_task
    await coordinator._settle_supply_task()

    assert coordinator._supply_streak == 0
    assert coordinator._supply_cooldown_until == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"refreshed": True, "supply_inserted_count": 0},
        {"refreshed": True, "supply_progress_count": 0},
        {"refreshed": True, "supply_productive": False},
    ],
)
async def test_explicit_zero_supply_progress_backs_off(
    result: dict[str, object],
) -> None:
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator.time_fn = lambda: 100.0
    coordinator.supply_callback = lambda reason: result

    coordinator._request_supply("test")
    assert coordinator._supply_task is not None
    await coordinator._supply_task
    await coordinator._settle_supply_task()

    assert coordinator._supply_streak == 1
    assert coordinator._supply_cooldown_until == 130.0


def test_candidate_enqueue_notification_resets_supply_ladder() -> None:
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator._record_supply_result(productive=False)
    coordinator._record_supply_result(productive=False)

    coordinator.notify("candidate_enqueued:pipeline")

    assert coordinator._supply_streak == 0
    assert coordinator._supply_cooldown_until == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, "legacy"])
async def test_legacy_supply_result_shape_is_not_throttled(result: object) -> None:
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator.supply_callback = lambda reason: result

    coordinator._request_supply("test")
    assert coordinator._supply_task is not None
    await coordinator._supply_task
    await coordinator._settle_supply_task()

    assert coordinator._supply_streak == 0
    assert coordinator._supply_cooldown_until == 0.0


def test_supply_starvation_warns_once_per_episode(caplog: pytest.LogCaptureFixture) -> None:
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    caplog.set_level(logging.WARNING, logger="openbiliclaw.runtime.candidate_eval")

    for _ in range(6):
        coordinator._record_supply_result(productive=False)
    warnings = [
        record
        for record in caplog.records
        if record.message.startswith("candidate supply starved:")
    ]
    assert len(warnings) == 1

    coordinator._record_supply_result(productive=True)
    for _ in range(5):
        coordinator._record_supply_result(productive=False)
    warnings = [
        record
        for record in caplog.records
        if record.message.startswith("candidate supply starved:")
    ]
    assert len(warnings) == 2


def test_supply_starvation_status_payload_tracks_cooldown() -> None:
    now = [100.0]
    coordinator = _coordinator(_FakeStagedPipeline(candidate_count=0))
    coordinator.time_fn = lambda: now[0]

    initial = coordinator.status_payload()
    assert initial["candidate_eval_supply_streak"] == 0
    assert initial["candidate_eval_supply_cooldown_until"] == 0.0

    coordinator._record_supply_result(productive=False)
    cooling = coordinator.status_payload()
    assert cooling["candidate_eval_supply_streak"] == 1
    assert cooling["candidate_eval_supply_cooldown_until"] == 130.0

    coordinator._record_supply_result(productive=True)
    recovered = coordinator.status_payload()
    assert recovered["candidate_eval_supply_streak"] == 0
    assert recovered["candidate_eval_supply_cooldown_until"] == 0.0


class _SqliteSoakEngine:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.delays = iter((0.03, 0.005, 0.015, 0.001, 0.02))

    async def evaluate_content_batch(
        self,
        items: list[DiscoveredContent],
        profile: object,
        **kwargs: object,
    ) -> list[float]:
        await asyncio.sleep(next(self.delays, 0.001))
        for item in items:
            item.relevance_score = 0.9
            item.relevance_reason = "fit"
            item.topic_group = f"tech-{item.content_id}"
            item.style_key = "deep_dive"
            item.pool_expression = "推荐文案"
            item.pool_topic_label = "推荐主题"
        return [0.9] * len(items)

    def cache_evaluated_results(self, items: list[DiscoveredContent]) -> int:
        for item in items:
            self.database.cache_content(
                item.bvid,
                content_id=item.content_id,
                title=item.title,
                source=item.source_strategy,
                source_platform=item.source_platform,
                relevance_score=item.relevance_score,
                relevance_reason=item.relevance_reason,
                topic_group=item.topic_group,
                style_key=item.style_key,
                pool_expression=item.pool_expression,
                pool_topic_label=item.pool_topic_label,
            )
        return len(items)


@pytest.mark.asyncio
async def test_sqlite_random_completion_soak(tmp_path: Any) -> None:
    db = Database(tmp_path / "soak.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:BVSOAK{i:03d}",
                source_platform="bilibili",
                source_strategy="search",
                bvid=f"BVSOAK{i:03d}",
                content_id=f"BVSOAK{i:03d}",
                title=f"Soak {i}",
            )
            for i in range(150)
        ]
    )
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_SqliteSoakEngine(db),  # type: ignore[arg-type]
        pool_target_count=60,
    )

    def snapshot() -> CandidateEvalSnapshot:
        readiness = db.count_pool_readiness()
        counts = db.count_discovery_candidates_by_status()
        return CandidateEvalSnapshot(
            available=readiness["available"],
            target=60,
            pending_eval=readiness["pending_eval"],
            evaluating=counts.get("evaluating", 0),
            evaluated_pending_admission=readiness["evaluated_pending"],
            admitted_pending_copy=readiness["admitted_pending_copy"],
        )

    coordinator = CandidateEvalCoordinator(
        pipeline=pipeline,
        snapshot_provider=snapshot,
        profile_provider=lambda: object(),
        worker_count=3,
        batch_size=30,
        safety_wake_seconds=0.01,
    )
    task = asyncio.create_task(coordinator.run_forever())
    coordinator.notify("soak")
    async with asyncio.timeout(5):
        while snapshot().available < 60:
            await asyncio.sleep(0.005)
    await coordinator.stop()
    await task

    counts = db.count_discovery_candidates_by_status()
    assert snapshot().available == 60
    assert counts.get("evaluating", 0) == 0
    assert (
        db.conn.execute("SELECT COUNT(DISTINCT content_id) FROM content_cache").fetchone()[0] == 60
    )
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM discovery_candidates WHERE claim_token IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_rate_limit_backoff_and_pause_transitions_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = _FakeStagedPipeline(candidate_count=0)
    coordinator = _coordinator(pipeline)

    with caplog.at_level(logging.WARNING, logger="openbiliclaw.runtime.candidate_eval"):
        coordinator._record_failure(LLMRateLimitError("429 slow down"))
        coordinator._record_failure(
            LLMFallbackError("No provider was available to process the request.")
        )

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "candidate evaluation rate limited; backing off 15s (streak=1)" in message
        for message in warnings
    )
    assert any("candidate evaluation paused on no_provider" in message for message in warnings)
    assert coordinator._paused is True

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.candidate_eval"):
        coordinator.notify("config_reloaded")

    assert coordinator._paused is False
    assert any(
        "candidate evaluation resumed on config_reloaded" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO
    )
