"""Durable dialogue reply ordering, recovery, and hot-reload lane tests."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from openbiliclaw.runtime.dialogue_reply_scheduler import (
    DialogueExecutionCoordinator,
    DurableChatReplyScheduler,
    TerminalChatReplyError,
)
from openbiliclaw.storage.database import Database


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    return database


def _pending_turn(database: Database, turn_id: str) -> None:
    database.create_chat_turn(
        turn_id=turn_id,
        message=f"message for {turn_id}",
    )


@pytest.mark.asyncio
async def test_pending_turns_retry_in_rowid_order_with_peak_one(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _pending_turn(database, "turn-1")
    _pending_turn(database, "turn-2")
    calls: list[str] = []
    active = 0
    peak_active = 0
    turn_one_attempts = 0

    async def process(turn_id: str) -> None:
        nonlocal active, peak_active, turn_one_attempts
        active += 1
        peak_active = max(peak_active, active)
        calls.append(turn_id)
        try:
            await asyncio.sleep(0)
            if turn_id == "turn-1":
                turn_one_attempts += 1
                if turn_one_attempts == 1:
                    assert database.get_chat_turn(turn_id)["status"] == "pending"  # type: ignore[index]
                    raise TimeoutError("temporary provider timeout")
            assert database.complete_chat_turn(turn_id, reply=f"reply for {turn_id}")
        finally:
            active -= 1

    scheduler = DurableChatReplyScheduler(
        processor=process,
        database_resolver=lambda: database,
        retry_base_seconds=0.01,
        retry_max_seconds=0.01,
        recovery_batch_size=1,
    )
    await scheduler.start()
    await scheduler.wait_idle(timeout=2)

    assert calls == ["turn-1", "turn-1", "turn-2"]
    assert peak_active == 1
    assert database.count_pending_chat_turns() == 0
    assert scheduler.status_payload()["chat_reply_processed"] == 2
    await scheduler.close()


@pytest.mark.asyncio
async def test_startup_pages_through_every_pending_turn(tmp_path: Path) -> None:
    database = _database(tmp_path)
    expected = [f"turn-{index:04d}" for index in range(7)]
    for turn_id in expected:
        _pending_turn(database, turn_id)
    calls: list[str] = []

    async def process(turn_id: str) -> None:
        calls.append(turn_id)
        assert database.complete_chat_turn(turn_id, reply="done")

    scheduler = DurableChatReplyScheduler(
        processor=process,
        database_resolver=lambda: database,
        recovery_batch_size=2,
    )
    await scheduler.start()
    await scheduler.wait_idle(timeout=2)

    assert calls == expected
    assert database.count_pending_chat_turns() == 0
    await scheduler.close()


@pytest.mark.asyncio
async def test_shutdown_cancellation_leaves_turn_pending_for_restart(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _pending_turn(database, "restart-me")
    started = asyncio.Event()

    async def interrupted(_turn_id: str) -> None:
        started.set()
        await asyncio.Event().wait()

    first = DurableChatReplyScheduler(
        processor=interrupted,
        database_resolver=lambda: database,
    )
    await first.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await first.close()

    interrupted_row = database.get_chat_turn("restart-me")
    assert interrupted_row is not None
    assert interrupted_row["status"] == "pending"
    assert interrupted_row["error"] == ""

    recovered: list[str] = []

    async def complete(turn_id: str) -> None:
        recovered.append(turn_id)
        assert database.complete_chat_turn(turn_id, reply="recovered")

    second = DurableChatReplyScheduler(
        processor=complete,
        database_resolver=lambda: database,
    )
    await second.start()
    await second.wait_idle(timeout=1)

    assert recovered == ["restart-me"]
    recovered_row = database.get_chat_turn("restart-me")
    assert recovered_row is not None
    assert recovered_row["status"] == "completed"
    assert recovered_row["reply"] == "recovered"
    await second.close()


@pytest.mark.asyncio
async def test_explicit_terminal_failure_is_the_only_failed_transition(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _pending_turn(database, "terminal")

    async def process(_turn_id: str) -> None:
        raise TerminalChatReplyError("安全错误", code="invalid_response")

    scheduler = DurableChatReplyScheduler(
        processor=process,
        database_resolver=lambda: database,
    )
    await scheduler.start()
    await scheduler.wait_idle(timeout=1)

    row = database.get_chat_turn("terminal")
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "安全错误"
    assert scheduler.status_payload()["chat_reply_last_error"] == "invalid_response"
    await scheduler.close()


def test_chat_turn_completion_and_failure_are_compare_and_swap(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _pending_turn(database, "complete-once")
    _pending_turn(database, "fail-once")

    assert database.complete_chat_turn("complete-once", reply="visible") is True
    assert database.complete_chat_turn("complete-once", reply="duplicate") is False
    assert database.fail_chat_turn("complete-once", error="too late") is False
    assert database.get_chat_turn("complete-once")["reply"] == "visible"  # type: ignore[index]

    assert database.fail_chat_turn("fail-once", error="terminal") is True
    assert database.fail_chat_turn("fail-once", error="duplicate") is False
    assert database.complete_chat_turn("fail-once", reply="too late") is False
    assert database.get_chat_turn("fail-once")["error"] == "terminal"  # type: ignore[index]


def test_retry_delay_caps_huge_attempt_without_exponent_overflow() -> None:
    async def unused(_turn_id: str) -> None:
        return None

    scheduler = DurableChatReplyScheduler(
        processor=unused,
        database_resolver=lambda: object(),
        retry_base_seconds=0.25,
        retry_max_seconds=12.0,
    )

    assert scheduler._retry_delay(1) == 0.25
    assert scheduler._retry_delay(10_000) == 12.0


@pytest.mark.asyncio
async def test_pause_drain_publishes_new_owner_before_queued_execution() -> None:
    coordinator = DialogueExecutionCoordinator()
    owner = {"value": "old"}
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    observed: list[str] = []

    async def active_old_execution() -> None:
        async with coordinator.lease():
            observed.append(owner["value"])
            old_started.set()
            await release_old.wait()

    async def queued_execution() -> None:
        async with coordinator.lease():
            # Resolution deliberately happens after admission.
            observed.append(owner["value"])

    active = asyncio.create_task(active_old_execution())
    await asyncio.wait_for(old_started.wait(), timeout=1)
    draining = asyncio.create_task(coordinator.pause_and_drain(timeout=1))
    await asyncio.sleep(0)
    queued = asyncio.create_task(queued_execution())
    await asyncio.sleep(0)
    assert queued.done() is False

    release_old.set()
    await draining
    owner["value"] = "new"
    await coordinator.resume()
    await asyncio.gather(active, queued)

    assert observed == ["old", "new"]


@pytest.mark.asyncio
async def test_pause_timeout_does_not_publish_and_resumes_old_owner() -> None:
    coordinator = DialogueExecutionCoordinator()
    owner_started = asyncio.Event()
    release_owner = asyncio.Event()
    published: list[str] = []
    queued_observed: list[str] = []

    async def active_owner() -> None:
        async with coordinator.lease():
            owner_started.set()
            await release_owner.wait()

    async def rebuild() -> None:
        await coordinator.pause_and_drain(timeout=0.01)
        published.append("new")

    active = asyncio.create_task(active_owner())
    await asyncio.wait_for(owner_started.wait(), timeout=1)
    with pytest.raises(TimeoutError):
        await rebuild()

    assert published == []
    assert coordinator.paused is False

    async def queued_old_execution() -> None:
        async with coordinator.lease():
            queued_observed.append("old")

    queued = asyncio.create_task(queued_old_execution())
    release_owner.set()
    await asyncio.gather(active, queued)
    assert queued_observed == ["old"]


def test_every_production_dialogue_respond_call_is_behind_stable_lease() -> None:
    source_path = Path(__file__).parents[1] / "src/openbiliclaw/api/app.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    enclosing_functions: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "respond"
        ):
            continue
        current: ast.AST | None = node
        while current is not None and not isinstance(current, ast.AsyncFunctionDef):
            current = parent.get(current)
        assert isinstance(current, ast.AsyncFunctionDef)
        enclosing_functions.append(current.name)

    assert sorted(enclosing_functions) == sorted(
        [
            "_generate_durable_chat_reply",
            "_respond",
            "_run_avoidance_chat",
            "_run_delight_chat",
            "_run_legacy_chat",
            "_run_probe_chat",
        ]
    )
    assert "ctx.dialogue.respond" not in source
    assert "async with _dialogue_execution_lease() as current_dialogue:" in source
    assert source.count("await _run_with_dialogue_execution(") == 5
    assert 'current_speculator = getattr(ctx.soul_engine, "_speculator", None)' in source
    assert 'current_speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)' in source
