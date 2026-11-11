"""Request-level background LLM budget admission (issue #344).

The scheduler quota (`scheduler.llm_budget_max_calls` per
`llm_budget_window_seconds`) used to be enforced only at controller loop
boundaries, so one cognition pass could issue many chunk calls past the cap,
and each process counted its own traffic. These tests lock in the three
behaviours that fix it:

1. the gate refuses a background request *before* it is sent, at the cap;
2. the counter is shared through SQLite when a ledger is wired;
3. a multi-call cognition pass stops between chunks, keeps its watermark and
   reports the stop, and the early trigger is debounced.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta
from typing import Any

import pytest

from openbiliclaw.llm.base import LLMBudgetExhaustedError, LLMResponse
from openbiliclaw.llm.budget import BackgroundBudgetLedger
from openbiliclaw.llm.concurrency import LLMConcurrencyGate, apply_background_budget_config
from openbiliclaw.llm.service import LLMService
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.cognition_cycle import CognitionCycle
from openbiliclaw.soul.profile import awareness_note_from_dict

_NOTE_PAYLOAD = {
    "date": "2026-11-10",
    "observation": "最近连续浏览深度内容。",
    "trend": "更偏向深度解释。",
}


def _seed_memory(tmp_path: Any, *, event_type: str = "view", count: int = 3) -> MemoryManager:
    memory = MemoryManager(tmp_path)
    memory.initialize()
    memory.get_layer("preference").update("interests", ["深度内容"])
    memory.get_layer("soul").update("personality_portrait", "理性又敏感")
    for index in range(count):
        memory._database.insert_event(  # noqa: SLF001 — test seeding
            event_type,
            title=f"事件{index}",
            context=f"在 B 站看了《事件{index}》",
            metadata={"source_platform": "bilibili"},
        )
    return memory


class _NoopInsightAnalyzer:
    async def analyze(self, **_kwargs: Any) -> list[Any]:
        return []

    def merge_insights(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


class _BudgetStoppingAwareness:
    """Returns one note, then refuses every later chunk with a budget error."""

    def __init__(self, *, stop_after: int = 1) -> None:
        self.stop_after = stop_after
        self.calls = 0

    async def analyze_with_confusions(self, **_kwargs: Any) -> tuple[list[Any], list[Any]]:
        self.calls += 1
        if self.calls > self.stop_after:
            raise LLMBudgetExhaustedError(
                "Background LLM budget exhausted for caller 'soul.awareness_confusions': 1/1 calls"
            )
        return [awareness_note_from_dict(_NOTE_PAYLOAD)], []

    def merge_notes(self, existing: list[Any], incoming: list[Any]) -> list[Any]:
        return list(existing) + list(incoming)


def _gate(tmp_path: Any, *, calls: int = 2, window: int = 3600) -> LLMConcurrencyGate:
    return LLMConcurrencyGate(
        4,
        background_budget_calls=calls,
        background_budget_window_seconds=window,
        background_budget_ledger=BackgroundBudgetLedger(tmp_path / "budget.db"),
    )


# -- gate admission -------------------------------------------------------


async def test_gate_refuses_background_request_at_the_cap(tmp_path: Any) -> None:
    gate = _gate(tmp_path, calls=2)

    for _ in range(2):
        async with gate.slot(caller="soul.insight"):
            pass

    with pytest.raises(LLMBudgetExhaustedError) as excinfo:
        async with gate.slot(caller="soul.insight"):
            pass
    message = str(excinfo.value)
    assert "2/2" in message
    assert "scheduler.llm_budget_max_calls" in message

    # A refused request never reserved a slot.
    assert gate.background_call_count(3600) == 2


async def test_gate_admission_excludes_interactive_callers(tmp_path: Any) -> None:
    gate = _gate(tmp_path, calls=1)
    async with gate.slot(caller="soul.insight"):
        pass

    async with gate.slot(caller="soul.dialogue"):
        pass  # interactive traffic is never budgeted

    with pytest.raises(LLMBudgetExhaustedError):
        async with gate.slot(caller="soul.insight"):
            pass
    assert gate.background_call_count(3600) == 1


async def test_gate_window_rollover_readmits(tmp_path: Any, monkeypatch: Any) -> None:
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(
        "openbiliclaw.llm.budget.time",
        types.SimpleNamespace(time=lambda: clock["now"]),
    )
    gate = _gate(tmp_path, calls=1, window=60)

    async with gate.slot(caller="soul.insight"):
        pass
    with pytest.raises(LLMBudgetExhaustedError):
        async with gate.slot(caller="soul.insight"):
            pass

    clock["now"] += 61
    assert gate.background_call_count(60) == 0
    async with gate.slot(caller="soul.insight"):
        pass
    assert gate.background_call_count(60) == 1


async def test_shared_ledger_caps_two_gates(tmp_path: Any) -> None:
    ledger = BackgroundBudgetLedger(tmp_path / "shared.db")
    first = LLMConcurrencyGate(4, background_budget_calls=3, background_budget_ledger=ledger)
    second = LLMConcurrencyGate(4, background_budget_calls=3, background_budget_ledger=ledger)

    async with first.slot(caller="soul.awareness_confusions"):
        pass
    async with second.slot(caller="soul.insight"):
        pass
    async with first.slot(caller="soul.insight"):
        pass

    with pytest.raises(LLMBudgetExhaustedError):
        async with second.slot(caller="soul.insight"):
            pass

    assert first.background_call_count(3600) == 3
    assert second.background_call_count(3600) == 3

    first.reset_background_budget()
    assert second.background_call_count(3600) == 0


def test_apply_background_budget_config_wires_scheduler_and_ledger(tmp_path: Any) -> None:
    gate = LLMConcurrencyGate(4)
    config = types.SimpleNamespace(
        scheduler=types.SimpleNamespace(
            llm_budget_max_calls=7,
            llm_budget_window_seconds=1800,
        ),
        data_path=tmp_path,
    )

    apply_background_budget_config(gate, config)  # type: ignore[arg-type]

    assert gate.background_budget_max_calls == 7
    assert gate.background_budget_window_seconds == 1800
    assert gate.status_payload()["llm_background_budget_shared"] is True


async def test_disabled_budget_admits_everything(tmp_path: Any) -> None:
    gate = LLMConcurrencyGate(4, background_budget_calls=0)
    for _ in range(5):
        async with gate.slot(caller="soul.insight"):
            pass


# -- propagation ----------------------------------------------------------


class _StubRegistry:
    default_provider = "stub"

    def is_chat_capable(self, _name: str) -> bool:
        return True

    def provider_supports_tool_calling(self, _name: str) -> bool:
        return False

    async def complete(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        return LLMResponse(content="{}", usage={"prompt_tokens": 1, "completion_tokens": 1})

    async def complete_chain(self, *_args: Any, **_kwargs: Any) -> LLMResponse:
        return await self.complete()


async def test_llm_service_propagates_budget_error_unwrapped(tmp_path: Any) -> None:
    memory = _seed_memory(tmp_path, count=0)
    gate = _gate(tmp_path, calls=1)
    service = LLMService(registry=_StubRegistry(), memory=memory, concurrency_gate=gate)

    first = await service.complete_with_core_memory(
        system_instruction="sys",
        user_input="user",
        caller="soul.insight",
        inject_core_memory=False,
    )
    assert first.content == "{}"

    with pytest.raises(LLMBudgetExhaustedError):
        await service.complete_with_core_memory(
            system_instruction="sys",
            user_input="user",
            caller="soul.insight",
            inject_core_memory=False,
        )


# -- cognition stops between chunks --------------------------------------


def _cognition_cycle(
    memory: MemoryManager,
    awareness: Any,
    *,
    batch_size: int = 1,
) -> CognitionCycle:
    return CognitionCycle(
        memory=memory,
        awareness_analyzer=awareness,
        insight_analyzer=_NoopInsightAnalyzer(),  # type: ignore[arg-type]
        min_interval_seconds=0,
        awareness_event_batch_size=batch_size,
    )


async def test_cognition_stops_between_awareness_batches_and_keeps_watermark(
    tmp_path: Any,
) -> None:
    memory = _seed_memory(tmp_path, count=3)
    awareness = _BudgetStoppingAwareness(stop_after=1)
    cycle = _cognition_cycle(memory, awareness, batch_size=1)

    result = await cycle.run_if_due(now=datetime(2026, 11, 10, 12, 0, 0))

    assert result.ran is True
    assert result.awareness_generated == 1
    assert result.budget_exhausted is True
    assert "awareness stopped before batch 2/3" in result.budget_detail
    # Exactly one chunk was attempted past the first (the refused one), with no
    # AwarenessGenerationError-style retry burning a second reservation.
    assert awareness.calls == 2

    state = cycle._load_state()  # noqa: SLF001 — persisted contract is the surface
    watermark = int(state.get("last_awareness_event_id", 0))
    newest_id = max(
        int(row["id"])
        for row in memory.query_events(limit=10)  # type: ignore[union-attr]
    )
    assert 0 < watermark < newest_id
    # The throttle timestamp is NOT advanced, so the next window resumes from
    # the watermark instead of waiting a full 12h.
    assert "last_awareness_at" not in state


async def test_cognition_insight_stops_between_batches(tmp_path: Any) -> None:
    memory = _seed_memory(tmp_path, count=0)

    class _StoppingInsight(_NoopInsightAnalyzer):
        def __init__(self) -> None:
            self.calls = 0

        async def analyze(self, **_kwargs: Any) -> list[Any]:
            self.calls += 1
            raise LLMBudgetExhaustedError(
                "Background LLM budget exhausted for caller 'soul.insight'"
            )

    insight = _StoppingInsight()
    cycle = CognitionCycle(
        memory=memory,
        awareness_analyzer=_BudgetStoppingAwareness(),  # type: ignore[arg-type]
        insight_analyzer=insight,  # type: ignore[arg-type]
        min_interval_seconds=0,
        insight_note_batch_size=1,
    )
    # Seed two awareness notes so the insight pass has at least one chunk.
    cycle._save_awareness_notes(
        [  # noqa: SLF001 — persisted contract is the surface
            awareness_note_from_dict({**_NOTE_PAYLOAD, "date": "2026-11-09"}),
            awareness_note_from_dict({**_NOTE_PAYLOAD, "date": "2026-11-10"}),
        ]
    )

    result = await cycle.run_if_due(now=datetime(2026, 11, 10, 12, 0, 0))

    assert result.budget_exhausted is True
    assert "insight stopped" in result.budget_detail
    assert insight.calls == 1
    assert "last_insight_at" not in cycle._load_state()  # noqa: SLF001


# -- early-trigger debounce ----------------------------------------------


def test_early_trigger_is_debounced_and_counted(tmp_path: Any) -> None:
    memory = _seed_memory(tmp_path, event_type="comment", count=3)
    cycle = _cognition_cycle(memory, _BudgetStoppingAwareness(stop_after=99))
    state: dict[str, Any] = {}

    assert cycle._should_early_trigger(state) is True  # noqa: SLF001
    assert state["early_trigger_count"] == 1
    assert "last_early_trigger_at" in state

    # Second probe within the 30-minute floor is suppressed and does not count.
    assert cycle._should_early_trigger(state) is False  # noqa: SLF001
    assert state["early_trigger_count"] == 1


def test_early_trigger_caps_per_window_and_recovers_after_rollover(tmp_path: Any) -> None:
    memory = _seed_memory(tmp_path, event_type="comment", count=3)
    cycle = _cognition_cycle(memory, _BudgetStoppingAwareness(stop_after=99))
    now = datetime.now()

    capped = {
        "last_early_trigger_at": (now - timedelta(minutes=31)).isoformat(),
        "early_trigger_window_started_at": (now - timedelta(hours=1)).isoformat(),
        "early_trigger_count": 4,
    }
    assert cycle._should_early_trigger(capped) is False  # noqa: SLF001

    rolled = {
        "last_early_trigger_at": (now - timedelta(hours=13)).isoformat(),
        "early_trigger_window_started_at": (now - timedelta(hours=13)).isoformat(),
        "early_trigger_count": 4,
    }
    assert cycle._should_early_trigger(rolled) is True  # noqa: SLF001
    assert rolled["early_trigger_count"] == 1


def test_database_schema_creates_budget_table(tmp_path: Any) -> None:
    from openbiliclaw.storage.database import Database

    database = Database(tmp_path / "app.db")
    database.initialize()
    rows = database._conn.execute(  # noqa: SLF001 — schema assertion
        "SELECT name FROM sqlite_master WHERE type='table' AND name='background_llm_budget'"
    ).fetchall()
    assert rows


def test_gate_constructor_keeps_legacy_signature_defaults() -> None:
    gate = LLMConcurrencyGate(2)
    assert gate.background_budget_max_calls == 0
    assert gate.background_budget_window_seconds == 3600
    assert gate.status_payload()["llm_background_budget_shared"] is False
