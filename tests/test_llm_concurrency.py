from __future__ import annotations

import asyncio
import logging

import pytest

from openbiliclaw.llm import concurrency as concurrency_module
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.llm.concurrency import (
    InventoryPriorityState,
    LLMConcurrencyGate,
    LLMTrafficClass,
    background_llm_concurrency,
)
from openbiliclaw.llm.service import LLMService, _background_admission_bypass


async def _wait_until(predicate: object) -> None:
    for _ in range(100):
        if callable(predicate) and predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_background_concurrency_reserves_one_total_slot() -> None:
    assert background_llm_concurrency(4) == 3
    assert background_llm_concurrency(3) == 2
    assert background_llm_concurrency(1) == 1
    assert background_llm_concurrency("invalid") == 2


def test_two_services_share_exact_gate_object() -> None:
    gate = LLMConcurrencyGate(4)
    registry = object()
    memory = object()

    left = LLMService(registry=registry, memory=memory, concurrency_gate=gate)  # type: ignore[arg-type]
    right = LLMService(registry=registry, memory=memory, concurrency_gate=gate)  # type: ignore[arg-type]

    assert left.concurrency_gate is gate
    assert right.concurrency_gate is gate


async def test_resize_up_wakes_queued_waiters() -> None:
    gate = LLMConcurrencyGate(1)
    release = asyncio.Event()
    entered = 0

    async def call() -> None:
        nonlocal entered
        async with gate.slot(caller="soul.dialogue"):
            entered += 1
            await release.wait()

    tasks = [asyncio.create_task(call()) for _ in range(3)]
    await _wait_until(lambda: gate.status_payload()["llm_total_waiting"] == 2)
    gate.reconfigure(3)
    await _wait_until(lambda: entered == 3)
    assert gate.status_payload()["llm_total_active"] == 3
    release.set()
    await asyncio.gather(*tasks)


async def test_resize_down_waits_for_active_calls_before_new_admission() -> None:
    gate = LLMConcurrencyGate(3)
    releases = [asyncio.Event() for _ in range(4)]
    entered = 0

    async def call(index: int) -> None:
        nonlocal entered
        async with gate.slot(caller="soul.dialogue"):
            entered += 1
            await releases[index].wait()

    active = [asyncio.create_task(call(index)) for index in range(3)]
    await _wait_until(lambda: entered == 3)
    gate.reconfigure(1)
    queued = asyncio.create_task(call(3))
    await _wait_until(lambda: gate.status_payload()["llm_total_waiting"] == 1)
    releases[0].set()
    await _wait_until(lambda: gate.status_payload()["llm_total_active"] == 2)
    assert entered == 3
    releases[1].set()
    await _wait_until(lambda: gate.status_payload()["llm_total_active"] == 1)
    assert entered == 3
    releases[2].set()
    await _wait_until(lambda: entered == 4)
    releases[3].set()
    await asyncio.gather(*active, queued)
    assert gate.status_payload()["llm_total_active"] == 0


async def test_old_and_new_services_share_total_during_rebuild_overlap() -> None:
    from openbiliclaw.llm.base import LLMResponse

    gate = LLMConcurrencyGate(1)
    release = asyncio.Event()
    provider_entered = 0

    class Registry:
        default_provider = "fake"

        def is_chat_capable(self, name: str) -> bool:
            return name == "fake"

        async def complete(self, messages: object, **kwargs: object) -> LLMResponse:
            nonlocal provider_entered
            provider_entered += 1
            await release.wait()
            return LLMResponse(content="ok", provider="fake")

        async def complete_provider(
            self, provider_name: str, messages: object, **kwargs: object
        ) -> LLMResponse:
            return await self.complete(messages, **kwargs)

    registry = Registry()
    old_service = LLMService(registry=registry, memory=object(), concurrency_gate=gate)  # type: ignore[arg-type]
    new_service = LLMService(registry=registry, memory=object(), concurrency_gate=gate)  # type: ignore[arg-type]
    old_call = asyncio.create_task(
        old_service.complete_with_core_memory(
            system_instruction="system", user_input="old", caller="soul.dialogue"
        )
    )
    await _wait_until(lambda: provider_entered == 1)
    new_call = asyncio.create_task(
        new_service.complete_with_core_memory(
            system_instruction="system", user_input="new", caller="soul.dialogue"
        )
    )
    await _wait_until(lambda: gate.status_payload()["llm_total_waiting"] == 1)
    assert provider_entered == 1
    release.set()
    await asyncio.gather(old_call, new_call)
    assert gate.status_payload()["llm_total_active"] == 0


async def test_three_background_calls_leave_default_interactive_slot() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def background() -> None:
        async with gate.slot(caller="soul.preference"):
            await release.wait()

    tasks = [asyncio.create_task(background()) for _ in range(4)]
    await _wait_until(lambda: gate.status_payload()["llm_background_active"] == 3)

    async def interactive() -> None:
        async with gate.slot(caller="soul.dialogue"):
            entered.set()
            await release.wait()

    interactive_task = asyncio.create_task(interactive())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert gate.status_payload()["llm_total_active"] == 4
    release.set()
    await asyncio.gather(*tasks, interactive_task)


async def test_total_one_degrades_without_deadlock() -> None:
    gate = LLMConcurrencyGate(total_concurrency=1)
    async with gate.slot(caller="soul.preference"):
        assert gate.status_payload()["llm_total_active"] == 1
    async with asyncio.timeout(1):
        async with gate.slot(caller="soul.dialogue"):
            pass
    assert gate.status_payload()["llm_total_active"] == 0


@pytest.mark.parametrize("queued_caller", ["soul.dialogue", "soul.preference"])
async def test_cancelled_waiter_does_not_leak_capacity(queued_caller: str) -> None:
    gate = LLMConcurrencyGate(total_concurrency=1)
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.slot(caller="soul.preference"):
            await release.wait()

    holder_task = asyncio.create_task(holder())
    await _wait_until(lambda: gate.status_payload()["llm_total_active"] == 1)
    waiter = asyncio.create_task(_acquire_once(gate, queued_caller))
    waiting_key = (
        "llm_total_waiting" if queued_caller == "soul.dialogue" else "llm_background_waiting"
    )
    await _wait_until(lambda: gate.status_payload()[waiting_key] == 1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await holder_task
    await asyncio.wait_for(_acquire_once(gate, "soul.dialogue"), timeout=1)
    assert gate.status_payload()["llm_total_active"] == 0
    assert gate.status_payload()["llm_background_active"] == 0


async def _acquire_once(gate: LLMConcurrencyGate, caller: str) -> None:
    async with gate.slot(caller=caller):
        pass


async def _hold(gate: LLMConcurrencyGate, caller: str, release: asyncio.Event) -> None:
    async with gate.slot(caller=caller):
        await release.wait()


async def _enter_and_hold(
    gate: LLMConcurrencyGate,
    caller: str,
    entered: asyncio.Event,
    release: asyncio.Event | None = None,
) -> None:
    async with gate.slot(caller=caller):
        entered.set()
        if release is not None:
            await release.wait()


async def test_two_refill_waiters_take_next_two_background_releases() -> None:
    gate = LLMConcurrencyGate(total_concurrency=4)
    gate.update_inventory(available=20, target=20)
    releases = [asyncio.Event() for _ in range(3)]
    maintenance = [
        asyncio.create_task(_hold(gate, "soul.preference", releases[index])) for index in range(3)
    ]
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_active"] == 3)

    gate.update_inventory(available=5, target=20)
    refill_entered = [asyncio.Event(), asyncio.Event()]
    refill_release = asyncio.Event()
    refill = [
        asyncio.create_task(
            _enter_and_hold(
                gate,
                "recommendation.write_expression",
                refill_entered[index],
                refill_release,
            )
        )
        for index in range(2)
    ]
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 2)

    releases[0].set()
    releases[1].set()
    await asyncio.gather(*(event.wait() for event in refill_entered))
    assert gate.status_payload()["llm_refill_active"] == 2
    assert gate.status_payload()["llm_maintenance_active"] == 1

    releases[2].set()
    refill_release.set()
    await asyncio.gather(*maintenance, *refill)


async def test_three_refill_waiters_can_borrow_all_background_slots() -> None:
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=20, target=20)
    maintenance_release = [asyncio.Event() for _ in range(3)]
    maintenance = [
        asyncio.create_task(_hold(gate, "soul.preference", event)) for event in maintenance_release
    ]
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_active"] == 3)
    gate.update_inventory(available=1, target=20)
    refill_release = asyncio.Event()
    entered = [asyncio.Event() for _ in range(3)]
    refill = [
        asyncio.create_task(
            _enter_and_hold(gate, "discovery.evaluate_batch", event, refill_release)
        )
        for event in entered
    ]
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 3)
    for event in maintenance_release:
        event.set()
    await asyncio.gather(*(event.wait() for event in entered))
    assert gate.status_payload()["llm_refill_active"] == 3
    refill_release.set()
    await asyncio.gather(*maintenance, *refill)


async def test_maintenance_borrows_idle_refill_capacity_work_conservingly() -> None:
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=1, target=20)
    release = asyncio.Event()
    tasks = [
        asyncio.create_task(_hold(gate, "recommendation.write_expression", release)),
        asyncio.create_task(_hold(gate, "soul.preference", release)),
        asyncio.create_task(_hold(gate, "soul.insight", release)),
    ]
    await _wait_until(lambda: gate.status_payload()["llm_background_active"] == 3)
    assert gate.status_payload()["llm_refill_active"] == 1
    assert gate.status_payload()["llm_maintenance_active"] == 2
    release.set()
    await asyncio.gather(*tasks)


async def test_new_refill_precedes_queued_maintenance_on_next_release() -> None:
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=20, target=20)
    releases = [asyncio.Event() for _ in range(3)]
    holders = [asyncio.create_task(_hold(gate, "soul.preference", event)) for event in releases]
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_active"] == 3)
    maintenance_entered = asyncio.Event()
    queued_maintenance = asyncio.create_task(
        _enter_and_hold(gate, "soul.insight", maintenance_entered)
    )
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)
    gate.update_inventory(available=1, target=20)
    refill_entered = asyncio.Event()
    refill_release = asyncio.Event()
    refill = asyncio.create_task(
        _enter_and_hold(
            gate,
            "recommendation.write_expression",
            refill_entered,
            refill_release,
        )
    )
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 1)
    releases[0].set()
    await asyncio.wait_for(refill_entered.wait(), timeout=1)
    assert not maintenance_entered.is_set()
    refill_release.set()
    for event in releases[1:]:
        event.set()
    await asyncio.gather(*holders, queued_maintenance, refill)


async def test_empty_inventory_parks_maintenance_while_refill_uses_all_slots() -> None:
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=0, target=20)
    maintenance_entered = asyncio.Event()
    maintenance = asyncio.create_task(_enter_and_hold(gate, "soul.preference", maintenance_entered))
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)
    refill_release = asyncio.Event()
    refill_entered = [asyncio.Event() for _ in range(3)]
    refill = [
        asyncio.create_task(
            _enter_and_hold(gate, "discovery.evaluate_batch", event, refill_release)
        )
        for event in refill_entered
    ]
    await asyncio.gather(*(event.wait() for event in refill_entered))
    assert gate.status_payload()["llm_refill_active"] == 3
    assert not maintenance_entered.is_set()
    refill_release.set()
    await asyncio.gather(*refill)
    gate.update_inventory(available=20, target=20)
    await asyncio.wait_for(maintenance_entered.wait(), timeout=1)
    await maintenance


async def test_inventory_transition_never_preempts_active_maintenance() -> None:
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=20, target=20)
    release = asyncio.Event()
    tasks = [asyncio.create_task(_hold(gate, "soul.preference", release)) for _ in range(3)]
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_active"] == 3)
    gate.update_inventory(available=0, target=20)
    await asyncio.sleep(0)
    assert gate.status_payload()["llm_maintenance_active"] == 3
    assert all(not task.cancelled() for task in tasks)
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.parametrize("cancel_index", [0, 1, 2])
async def test_refill_queue_cancellation_at_every_position_leaks_nothing(
    cancel_index: int,
) -> None:
    gate = LLMConcurrencyGate(2)
    gate.update_inventory(available=0, target=20)
    holder_release = asyncio.Event()
    holder = asyncio.create_task(_hold(gate, "discovery.evaluate_batch", holder_release))
    await _wait_until(lambda: gate.status_payload()["llm_refill_active"] == 1)
    waiters = [
        asyncio.create_task(_acquire_once(gate, "recommendation.write_expression"))
        for _ in range(3)
    ]
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 3)
    waiters[cancel_index].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiters[cancel_index]
    holder_release.set()
    await holder
    await asyncio.gather(*(task for index, task in enumerate(waiters) if index != cancel_index))
    assert gate.status_payload()["llm_refill_active"] == 0
    assert gate.status_payload()["llm_refill_waiting"] == 0
    assert gate.status_payload()["llm_background_active"] == 0
    assert gate.status_payload()["llm_total_active"] == 0


def test_inventory_state_drives_dynamic_supply_classification() -> None:
    gate = LLMConcurrencyGate(4)
    supply_callers = [
        "discovery.explore.queries",
        "discovery.keyword_planner",
        "discovery.search.queries",
        "sources.zhihu.extract",
        "sources.xhs.keyword_gen",
        "runtime.bilibili_extension_search.queries",
    ]
    gate.update_inventory(available=20, target=20)
    assert gate.inventory_priority_state is InventoryPriorityState.HEALTHY
    assert all(gate.classify(caller) is LLMTrafficClass.MAINTENANCE for caller in supply_callers)
    gate.update_inventory(available=1, target=20)
    assert gate.inventory_priority_state is InventoryPriorityState.REFILL
    assert all(gate.classify(caller) is LLMTrafficClass.REFILL_SUPPLY for caller in supply_callers)
    assert gate.classify("recommendation.write_expression") is LLMTrafficClass.REFILL_EXPRESSION
    assert gate.classify("discovery.evaluate_batch") is LLMTrafficClass.REFILL_EVALUATION
    assert gate.classify("soul.preference") is LLMTrafficClass.MAINTENANCE


async def test_refill_class_order_is_expression_then_evaluation_then_supply() -> None:
    gate = LLMConcurrencyGate(2)
    gate.update_inventory(available=20, target=20)
    holder_release = asyncio.Event()
    holder = asyncio.create_task(_hold(gate, "soul.preference", holder_release))
    await _wait_until(lambda: gate.status_payload()["llm_background_active"] == 1)
    gate.update_inventory(available=1, target=20)
    entry_order: list[str] = []

    async def record(caller: str) -> None:
        async with gate.slot(caller=caller):
            entry_order.append(caller)

    waiters = [
        asyncio.create_task(record("discovery.keyword_planner")),
        asyncio.create_task(record("discovery.evaluate_batch")),
        asyncio.create_task(record("recommendation.write_expression")),
    ]
    await _wait_until(lambda: gate.status_payload()["llm_refill_waiting"] == 3)
    holder_release.set()
    await asyncio.gather(holder, *waiters)
    assert entry_order == [
        "recommendation.write_expression",
        "discovery.evaluate_batch",
        "discovery.keyword_planner",
    ]


@pytest.mark.parametrize(
    "caller",
    [
        "discovery.douyin.keyword_gen",
        "discovery.evaluate_batch",
        "discovery.evaluate_single",
        "discovery.explore.queries",
        "discovery.keyword_inspiration",
        "discovery.keyword_planner",
        "discovery.search.queries",
        "discovery.x.keyword_gen",
        "eval.query_quality",
        "eval.relevance",
        "eval.scenario_gen",
        "eval.specificity",
        "pool_purge.llm_agent",
        "recommendation.evaluate_batch",
        "recommendation.expression",
        "recommendation.write_expression",
        "runtime.bilibili_extension_search.queries",
        "soul.avoidance_speculate",
        "soul.awareness",
        "soul.category_migration",
        "soul.consolidation",
        "soul.core_update",
        "soul.dialogue_insight",
        "soul.insight",
        "soul.preference",
        "soul.preference.chunk",
        "soul.profile_build",
        "soul.role_update",
        "soul.speculate",
        "soul.values_update",
        "sources.xhs.keyword_gen",
        "sources.zhihu.extract",
        "yt_search.generate_queries",
    ],
)
def test_current_background_callers_are_classified(caller: str) -> None:
    assert LLMConcurrencyGate(4).classify(caller) is not LLMTrafficClass.INTERACTIVE


@pytest.mark.parametrize(
    "caller",
    [
        "api.config_probe",
        "soul.dialogue",
        "soul.dialogue.tools",
        "soul.dialogue.tool_followup",
        "api.sentiment",
        "agent.chat",
        "agent.task",
    ],
)
def test_confirmed_interactive_callers(caller: str) -> None:
    assert LLMConcurrencyGate(4).classify(caller) is LLMTrafficClass.INTERACTIVE


@pytest.mark.parametrize("caller", ["agent.chat", "agent.task"])
async def test_agent_loop_callers_are_not_parked_by_empty_inventory(caller: str) -> None:
    """M10: user-initiated agent lanes must not park behind an empty pool."""
    gate = LLMConcurrencyGate(1)
    gate.update_inventory(available=0, target=20)

    async with asyncio.timeout(1):
        await _acquire_once(gate, caller)

    assert gate.status_payload()["llm_background_active"] == 0


@pytest.mark.parametrize(
    "caller",
    ["discovery.explore.queries", "sources.zhihu.extract"],
)
async def test_empty_inventory_admits_load_bearing_supply_callers(caller: str) -> None:
    gate = LLMConcurrencyGate(1)
    gate.update_inventory(available=0, target=20)

    async with asyncio.timeout(1):
        await _acquire_once(gate, caller)

    assert gate.classify(caller) is LLMTrafficClass.REFILL_SUPPLY
    assert gate.status_payload()["llm_background_active"] == 0


async def test_unknown_caller_warns_once_and_remains_background_limited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = LLMConcurrencyGate(2)
    with caplog.at_level(logging.WARNING):
        await _acquire_once(gate, "new.unclassified")
        await _acquire_once(gate, "new.unclassified")
    assert sum("new.unclassified" in record.message for record in caplog.records) == 1


async def test_bypass_background_still_obeys_total_gate() -> None:
    gate = LLMConcurrencyGate(1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.slot(caller="soul.preference", bypass_background=True):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    waiter = asyncio.create_task(
        gate.slot(caller="soul.dialogue", bypass_background=True).__aenter__()
    )
    await asyncio.sleep(0)
    assert not waiter.done()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await task


async def test_scoped_bypass_unparks_maintenance_and_resets_after_exit() -> None:
    gate = LLMConcurrencyGate(1)
    gate.update_inventory(available=0, target=20)

    class Registry:
        default_provider = "fake"

        async def complete(self, messages: object, **kwargs: object) -> LLMResponse:
            return LLMResponse(content='{"ok": true}', provider="fake")

    service = LLMService(registry=Registry(), memory=None, concurrency_gate=gate)  # type: ignore[arg-type]
    with _background_admission_bypass():
        async with asyncio.timeout(1):
            await service.complete_structured_task(
                system_instruction="Return json.",
                user_input="init",
                caller="soul.preference",
            )

    parked = asyncio.create_task(
        service.complete_structured_task(
            system_instruction="Return json.",
            user_input="background",
            caller="soul.preference",
        )
    )
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)
    assert not parked.done()
    parked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parked


async def test_empty_pool_does_not_park_maintenance_forever_when_refill_never_comes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty pool with no refill in sight must not freeze profile updates.

    Reserving slots for refill is right while refill is coming. When every
    source is disabled (or refill keeps failing) the pool stays EMPTY forever,
    and `soul.*` profile work — classified MAINTENANCE — used to wait with no
    timeout and no log line: ordinary browsing silently stopped updating the
    portrait. Maintenance cannot refill the pool, so the park is bounded.
    """
    monkeypatch.setattr(concurrency_module, "MAINTENANCE_STARVATION_GRACE_SECONDS", 0.05)
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=0, target=20)

    entered = asyncio.Event()
    task = asyncio.create_task(_enter_and_hold(gate, "soul.preference", entered))
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)
    assert not entered.is_set()  # parked at first, exactly as before

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(entered.wait(), timeout=2)
    await task

    assert "parked" in caplog.text
    assert "starved" in caplog.text


async def test_starvation_relief_does_not_fire_when_refill_makes_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grace timer must not hand slots to maintenance while refill runs."""
    monkeypatch.setattr(concurrency_module, "MAINTENANCE_STARVATION_GRACE_SECONDS", 0.05)
    gate = LLMConcurrencyGate(4)
    gate.update_inventory(available=0, target=20)

    maintenance_entered = asyncio.Event()
    maintenance = asyncio.create_task(_enter_and_hold(gate, "soul.preference", maintenance_entered))
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)

    # Pool recovers before the grace elapses — reservation semantics stay intact
    # and the relief flag is dropped rather than left armed for next time.
    gate.update_inventory(available=20, target=20)
    await asyncio.wait_for(maintenance_entered.wait(), timeout=1)
    await maintenance

    gate.update_inventory(available=0, target=20)
    parked = asyncio.Event()
    parked_task = asyncio.create_task(_enter_and_hold(gate, "soul.preference", parked))
    await _wait_until(lambda: gate.status_payload()["llm_maintenance_waiting"] == 1)
    assert not parked.is_set(), "relief must be re-armed, not sticky"

    gate.update_inventory(available=20, target=20)
    await asyncio.wait_for(parked.wait(), timeout=1)
    await parked_task


async def test_background_call_budget_counts_only_background_llm_requests() -> None:
    gate = LLMConcurrencyGate(4)

    async with gate.slot(caller="soul.dialogue"):
        pass  # interactive; not counted
    async with gate.slot(caller="soul.preference"):
        pass  # background; counted
    async with gate.slot(caller="discovery.keyword_planner"):
        pass  # background; counted

    assert gate.background_call_count(3600) == 2
    assert gate.background_call_count(0) == 2


async def test_background_call_budget_reset_starts_fresh_window() -> None:
    gate = LLMConcurrencyGate(4)

    async with gate.slot(caller="soul.preference"):
        pass
    assert gate.background_call_count(3600) == 1

    gate.reset_background_budget()
    assert gate.background_call_count(3600) == 0

    async with gate.slot(caller="soul.preference"):
        pass
    assert gate.background_call_count(3600) == 1
