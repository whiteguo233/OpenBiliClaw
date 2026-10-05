"""Runtime-wide admission control for all LLM provider traffic."""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_TOTAL_LLM_CONCURRENCY = 3

# How long an empty content pool may park maintenance traffic before it is
# admitted anyway. Calibrated against the observed cost of the failure it
# guards: with refill unable to run at all (all sources disabled), the profile
# pipeline sat blocked indefinitely and produced no log line after startup.
# 5 minutes is long enough that a normal refill burst (seconds to low minutes)
# still gets its reserved slots first, and short enough that a stuck pool does
# not silently freeze portrait updates for a whole session.
MAINTENANCE_STARVATION_GRACE_SECONDS = 300.0


def coerce_total_concurrency(value: object) -> int:
    """Normalize a positive total, preserving every explicit positive value."""
    if isinstance(value, bool):
        return DEFAULT_TOTAL_LLM_CONCURRENCY
    if isinstance(value, int | float):
        normalized = int(value)
    elif isinstance(value, str):
        try:
            normalized = int(value.strip())
        except ValueError:
            return DEFAULT_TOTAL_LLM_CONCURRENCY
    else:
        return DEFAULT_TOTAL_LLM_CONCURRENCY
    return normalized if normalized >= 1 else DEFAULT_TOTAL_LLM_CONCURRENCY


def background_llm_concurrency(total: object) -> int:
    """Reserve one total slot for interactive work, degrading safely at one."""
    return max(1, coerce_total_concurrency(total) - 1)


class PrioritySemaphore:
    """Cancellation-safe semaphore serving lower priority numbers first."""

    def __init__(self, capacity: int = 1) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._in_flight = 0
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def active(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return sum(not future.done() for _, _, future in self._waiters)

    async def acquire(self, priority: int) -> None:
        if self._in_flight < self._capacity and not self._waiters:
            self._in_flight += 1
            return
        loop = asyncio.get_event_loop()
        future: asyncio.Future[None] = loop.create_future()
        heapq.heappush(self._waiters, (priority, next(self._counter), future))
        self._drain_waiters()
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            self._waiters = [entry for entry in self._waiters if entry[2] is not future]
            heapq.heapify(self._waiters)
            if future.done() and not future.cancelled():
                self.release()
            raise

    def release(self) -> None:
        if self._in_flight <= 0:
            raise RuntimeError("PrioritySemaphore released too many times")
        self._in_flight -= 1
        self._drain_waiters()

    def _drain_waiters(self) -> None:
        while self._in_flight < self._capacity and self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                self._in_flight += 1
                future.set_result(None)

    def resize(self, capacity: int) -> None:
        """Change capacity without revoking active holders."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._drain_waiters()

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        await self.acquire(priority)
        try:
            yield
        finally:
            self.release()


class LLMTrafficClass(StrEnum):
    INTERACTIVE = "interactive"
    REFILL_EXPRESSION = "refill.expression"
    REFILL_EVALUATION = "refill.evaluation"
    REFILL_SUPPLY = "refill.supply"
    MAINTENANCE = "maintenance"


class InventoryPriorityState(StrEnum):
    """Durable recommendation inventory state used for LLM admission."""

    HEALTHY = "healthy"
    REFILL = "refill"
    EMPTY = "empty"


class RefillAdmissionSemaphore:
    """Cancellation-safe, work-conserving admission for background traffic."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._active_total = 0
        self._active_refill = 0
        self._active_maintenance = 0
        self._waiting_refill = 0
        self._waiting_maintenance = 0
        self._inventory_state = InventoryPriorityState.HEALTHY
        self._waiters: list[tuple[int, int, LLMTrafficClass, asyncio.Future[None]]] = []
        self._counter = itertools.count()
        self._starvation_relief = False
        self._starvation_timer: asyncio.TimerHandle | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def active(self) -> int:
        return self._active_total

    @property
    def waiting(self) -> int:
        return self._waiting_refill + self._waiting_maintenance

    @property
    def active_refill(self) -> int:
        return self._active_refill

    @property
    def active_maintenance(self) -> int:
        return self._active_maintenance

    @property
    def waiting_refill(self) -> int:
        return self._waiting_refill

    @property
    def waiting_maintenance(self) -> int:
        return self._waiting_maintenance

    @property
    def inventory_state(self) -> InventoryPriorityState:
        return self._inventory_state

    async def acquire(self, traffic: LLMTrafficClass, priority: int) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        heapq.heappush(
            self._waiters,
            (priority, next(self._counter), traffic, future),
        )
        self._increment_waiting(traffic)
        self._arm_starvation_timer(traffic)
        self._drain_waiters()
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release(traffic)
            else:
                self._remove_waiter(future, traffic)
                self._drain_waiters()
            raise

    def release(self, traffic: LLMTrafficClass) -> None:
        if self._active_total <= 0:
            raise RuntimeError("RefillAdmissionSemaphore released too many times")
        self._active_total -= 1
        if traffic is LLMTrafficClass.MAINTENANCE:
            if self._active_maintenance <= 0:
                raise RuntimeError("maintenance admission released too many times")
            self._active_maintenance -= 1
        else:
            if self._active_refill <= 0:
                raise RuntimeError("refill admission released too many times")
            self._active_refill -= 1
        self._drain_waiters()

    def resize(self, capacity: int) -> None:
        """Change capacity without revoking active holders."""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._drain_waiters()

    def update_inventory(self, state: InventoryPriorityState) -> None:
        """Apply a canonical inventory state and reconsider parked waiters."""
        self._inventory_state = state
        if state is not InventoryPriorityState.EMPTY:
            # The pool recovered: drop the emergency relief and re-arm normal
            # reservation semantics for the next empty spell.
            self._starvation_relief = False
            self._cancel_starvation_timer()
        self._drain_waiters()

    def _arm_starvation_timer(self, traffic: LLMTrafficClass) -> None:
        """Bound how long an empty pool may park maintenance traffic.

        An empty pool reserves slots for refill, which is correct while refill
        is coming. When it never comes — every source disabled, or refill
        failing persistently — maintenance used to stay parked forever. The
        profile pipeline runs as ``soul.*`` (MAINTENANCE), so ordinary browsing
        silently stopped updating the portrait, with nothing in the log beyond
        a single "Background LLM work gate blocked" line. Maintenance cannot
        refill the pool, so an unbounded park buys nothing.
        """
        if traffic is not LLMTrafficClass.MAINTENANCE:
            return
        if self._starvation_relief or self._starvation_timer is not None:
            return
        if self._inventory_state is not InventoryPriorityState.EMPTY:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._starvation_timer = loop.call_later(
            MAINTENANCE_STARVATION_GRACE_SECONDS,
            self._relieve_starvation,
        )

    def _cancel_starvation_timer(self) -> None:
        if self._starvation_timer is not None:
            self._starvation_timer.cancel()
            self._starvation_timer = None

    def _relieve_starvation(self) -> None:
        """Admit parked maintenance after the grace period, loudly."""
        self._starvation_timer = None
        if self._inventory_state is not InventoryPriorityState.EMPTY:
            return
        if self._waiting_maintenance <= 0:
            return
        self._starvation_relief = True
        logger.warning(
            "LLM maintenance traffic parked %.0fs on an empty content pool with "
            "no refill progress; admitting %d waiter(s) so profile updates are "
            "not starved indefinitely. Check whether refill is failing "
            "(sources disabled, credentials expired, or network blocked).",
            MAINTENANCE_STARVATION_GRACE_SECONDS,
            self._waiting_maintenance,
        )
        self._drain_waiters()

    @asynccontextmanager
    async def slot(self, traffic: LLMTrafficClass, priority: int) -> AsyncIterator[None]:
        await self.acquire(traffic, priority)
        try:
            yield
        finally:
            self.release(traffic)

    def _can_admit(self, traffic: LLMTrafficClass) -> bool:
        if self._active_total >= self.capacity:
            return False
        if traffic is not LLMTrafficClass.MAINTENANCE:
            return True
        if self._inventory_state is InventoryPriorityState.EMPTY and not self._starvation_relief:
            return False
        if self._waiting_refill > 0 and self._active_maintenance >= 1:  # noqa: SIM103
            return False
        return True

    def _drain_waiters(self) -> None:
        while self._active_total < self._capacity and self._waiters:
            admissible = [
                (priority, sequence, index)
                for index, (priority, sequence, traffic, future) in enumerate(self._waiters)
                if not future.done() and self._can_admit(traffic)
            ]
            admissible_index = min(admissible)[2] if admissible else None
            if admissible_index is None:
                return
            _, _, traffic, future = self._waiters.pop(admissible_index)
            heapq.heapify(self._waiters)
            self._decrement_waiting(traffic)
            self._active_total += 1
            if traffic is LLMTrafficClass.MAINTENANCE:
                self._active_maintenance += 1
            else:
                self._active_refill += 1
            future.set_result(None)

    def _remove_waiter(self, future: asyncio.Future[None], traffic: LLMTrafficClass) -> None:
        original_length = len(self._waiters)
        self._waiters = [entry for entry in self._waiters if entry[3] is not future]
        if len(self._waiters) != original_length:
            self._decrement_waiting(traffic)
            heapq.heapify(self._waiters)

    def _increment_waiting(self, traffic: LLMTrafficClass) -> None:
        if traffic is LLMTrafficClass.MAINTENANCE:
            self._waiting_maintenance += 1
        else:
            self._waiting_refill += 1

    def _decrement_waiting(self, traffic: LLMTrafficClass) -> None:
        if traffic is LLMTrafficClass.MAINTENANCE:
            self._waiting_maintenance -= 1
        else:
            self._waiting_refill -= 1


_INTERACTIVE_CALLERS = {
    # Configuration probes are control-plane requests initiated by a user.
    # They must stay usable while the canonical recommendation pool is empty;
    # otherwise the recovery action for a broken init waits on the very pool
    # that the broken configuration prevented us from building.
    "api.config_probe",
    "soul.dialogue",
    "soul.dialogue.tools",
    "soul.dialogue.tool_followup",
    "api.sentiment",
    # 「聊一聊」 agent loop lanes are user-initiated: the interactive chat
    # loop ("agent.chat") answers live in the conversation, and durable
    # background tasks ("agent.task") are launched by an explicit user
    # request from the chat. Neither is daemon maintenance, so neither may
    # be parked behind the empty-pool refill reservation.
    "agent.chat",
    "agent.task",
}
_EXPRESSION_CALLERS = {
    "recommendation.expression",
    "recommendation.write_expression",
}
_EVALUATION_CALLERS = {
    "discovery.evaluate_batch",
    "discovery.evaluate_single",
    "recommendation.evaluate_batch",
}
_SUPPLY_PREFIXES = (
    "discovery.douyin.keyword_gen",
    "discovery.explore.queries",
    "discovery.keyword_inspiration",
    "discovery.keyword_planner",
    "discovery.search.queries",
    "discovery.x.keyword_gen",
    "runtime.bilibili_extension_search.queries",
    "sources.xhs.keyword_gen",
    "yt_search.generate_queries",
)
_KNOWN_MAINTENANCE_PREFIXES = (
    "discovery.",
    "eval.",
    "pool_purge.",
    "soul.",
    "sources.",
)
_MAINTENANCE_CALLERS: set[str] = set()


class LLMConcurrencyGate:
    """One true total provider bound plus a reserved background bound."""

    def __init__(self, total_concurrency: int = DEFAULT_TOTAL_LLM_CONCURRENCY) -> None:
        self.total_concurrency = coerce_total_concurrency(total_concurrency)
        self.background_concurrency = background_llm_concurrency(self.total_concurrency)
        self._total = PrioritySemaphore(self.total_concurrency)
        self._background = RefillAdmissionSemaphore(self.background_concurrency)
        self._warned_unknown_callers: set[str] = set()
        self._budget_window_started_at = time.monotonic()
        self._budget_window_calls = 0

    @property
    def inventory_priority_state(self) -> InventoryPriorityState:
        """Return the current durable inventory classification."""
        return self._background.inventory_state

    def update_inventory(self, *, available: int, target: int) -> None:
        """Update refill admission from a canonical durable inventory snapshot."""
        normalized_available = max(0, int(available))
        normalized_target = max(0, int(target))
        if normalized_target <= 0 or normalized_available >= normalized_target:
            state = InventoryPriorityState.HEALTHY
        elif normalized_available == 0:
            state = InventoryPriorityState.EMPTY
        else:
            state = InventoryPriorityState.REFILL
        self._background.update_inventory(state)

    def reconfigure(self, total_concurrency: int) -> None:
        """Resize this runtime-owned gate in place for a hot reload."""
        total = coerce_total_concurrency(total_concurrency)
        background = background_llm_concurrency(total)
        self.total_concurrency = total
        self.background_concurrency = background
        self._total.resize(total)
        self._background.resize(background)

    def background_call_count(self, window_seconds: int) -> int:
        """Return the number of background LLM calls in the current fixed window.

        A non-positive window disables the budget and simply returns the raw
        count since the last explicit rollover.
        """
        now = time.monotonic()
        if window_seconds > 0 and now - self._budget_window_started_at >= window_seconds:
            self._budget_window_started_at = now
            self._budget_window_calls = 0
        return self._budget_window_calls

    def reset_background_budget(self) -> None:
        """Manually start a fresh budget window (e.g. user confirmed continue)."""
        self._budget_window_started_at = time.monotonic()
        self._budget_window_calls = 0

    def classify(self, caller: str) -> LLMTrafficClass:
        tag = caller.strip()
        if tag in _INTERACTIVE_CALLERS:
            return LLMTrafficClass.INTERACTIVE
        if tag in _EXPRESSION_CALLERS:
            return LLMTrafficClass.REFILL_EXPRESSION
        if tag in _EVALUATION_CALLERS:
            return LLMTrafficClass.REFILL_EVALUATION
        if any(tag == prefix or tag.startswith(prefix + ".") for prefix in _SUPPLY_PREFIXES):
            if self.inventory_priority_state is not InventoryPriorityState.HEALTHY:
                return LLMTrafficClass.REFILL_SUPPLY
            return LLMTrafficClass.MAINTENANCE
        if tag.startswith("sources.") and tag.endswith(".extract"):
            # Source extraction is part of the durable supply path.  Parking it
            # as maintenance while the pool is empty creates the same cycle as
            # a parked query generator: admission waits for inventory, while
            # inventory waits for extraction to finish.
            if self.inventory_priority_state is not InventoryPriorityState.HEALTHY:
                return LLMTrafficClass.REFILL_SUPPLY
            return LLMTrafficClass.MAINTENANCE
        if tag in _MAINTENANCE_CALLERS or any(
            tag.startswith(prefix) for prefix in _KNOWN_MAINTENANCE_PREFIXES
        ):
            return LLMTrafficClass.MAINTENANCE
        if tag not in self._warned_unknown_callers:
            self._warned_unknown_callers.add(tag)
            logger.warning("Unknown LLM caller %r; classifying as maintenance", tag)
        return LLMTrafficClass.MAINTENANCE

    @staticmethod
    def _priority(traffic: LLMTrafficClass) -> int:
        return {
            LLMTrafficClass.INTERACTIVE: 0,
            LLMTrafficClass.REFILL_EXPRESSION: 1,
            LLMTrafficClass.REFILL_EVALUATION: 2,
            LLMTrafficClass.REFILL_SUPPLY: 3,
            LLMTrafficClass.MAINTENANCE: 4,
        }[traffic]

    @asynccontextmanager
    async def slot(self, *, caller: str, bypass_background: bool = False) -> AsyncIterator[None]:
        traffic = self.classify(caller)
        priority = self._priority(traffic)
        uses_background = traffic is not LLMTrafficClass.INTERACTIVE and not bypass_background
        if uses_background:
            # Count daemon/background LLM requests for the scheduler's
            # self-imposed per-window quota guard. Interactive user requests
            # are deliberately excluded.
            self._budget_window_calls += 1
            async with self._background.slot(traffic, priority), self._total.slot(priority):
                yield
            return
        async with self._total.slot(priority):
            yield

    def status_payload(self) -> dict[str, int | str | bool]:
        return {
            "llm_total_concurrency": self.total_concurrency,
            "llm_background_concurrency": self.background_concurrency,
            "llm_total_active": self._total.active,
            "llm_total_waiting": self._total.waiting,
            "llm_background_active": self._background.active,
            "llm_background_waiting": self._background.waiting,
            "llm_refill_active": self._background.active_refill,
            "llm_refill_waiting": self._background.waiting_refill,
            "llm_maintenance_active": self._background.active_maintenance,
            "llm_maintenance_waiting": self._background.waiting_maintenance,
            "llm_refill_priority_active": (
                self.inventory_priority_state is not InventoryPriorityState.HEALTHY
            ),
            "inventory_priority_state": self.inventory_priority_state.value,
        }
