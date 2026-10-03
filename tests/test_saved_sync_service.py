from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

import openbiliclaw.saved_sync.service as saved_sync_service_module
from openbiliclaw.saved_sync.adapters.extension import build_extension_native_save_adapters
from openbiliclaw.saved_sync.extension_broker import (
    ExtensionNativeSaveBroker,
    ExtensionNativeSaveResultIn,
)
from openbiliclaw.saved_sync.models import (
    NativeSaveAction,
    NativeSaveCapability,
    NativeSaveResult,
    NativeSaveRoute,
    NativeSaveStatus,
    SavedItemInput,
    SavedSyncBatchResult,
)
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "saved-sync-service.db")
    database.initialize()
    return database


class FakeAdapter:
    def __init__(
        self,
        capability: NativeSaveCapability,
        result_status: str = "synced",
        gate: asyncio.Event | None = None,
    ) -> None:
        self.capability = capability
        self.result_status = result_status
        self.gate = gate
        self.calls: list[str] = []

    def target_label(self, action: NativeSaveAction) -> str:
        if self.capability.platform == "reddit":
            return "Reddit Saved"
        return "B站稍后观看" if action == "watch_later" else "B站 OpenBiliClaw 收藏夹"

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        if self.gate is not None:
            await self.gate.wait()
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class RaisingAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        raise RuntimeError("private platform response body")


class RaisingTargetAdapter(FakeAdapter):
    def target_label(self, action: NativeSaveAction) -> str:
        raise ValueError("private target discovery response")


class MisroutingAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        return NativeSaveResult(
            item_key=item.item_key,
            status=cast("NativeSaveStatus", self.result_status),
            resolved_action="favorite" if route.resolved_action == "watch_later" else "watch_later",
            resolved_target="adapter-controlled target",
        )


class CancellationSuppressingAdapter(FakeAdapter):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__(NativeSaveCapability("bilibili", True, True, True))
        self.release = release
        self.cancel_seen = asyncio.Event()

    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return NativeSaveResult(
            item_key=item.item_key,
            status="synced",
            resolved_action=route.resolved_action,
            resolved_target=route.resolved_target,
        )


class MalformedCancellationSuppressingAdapter(CancellationSuppressingAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return cast("NativeSaveResult", {"private_response": "must not leak"})


class InvalidTargetAdapter(FakeAdapter):
    def __init__(self, target: object) -> None:
        super().__init__(NativeSaveCapability("bilibili", True, True, True))
        self.target = target

    def target_label(self, action: NativeSaveAction) -> str:
        del action
        return cast("str", self.target)


class ImmediateCancellationSuccessAdapter(FakeAdapter):
    async def save(self, item: SavedItemInput, route: NativeSaveRoute) -> NativeSaveResult:
        self.calls.append(item.item_key)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return NativeSaveResult(
                item_key=item.item_key,
                status="synced",
                resolved_action=route.resolved_action,
                resolved_target=route.resolved_target,
            )


def test_local_save_without_auto_sync_never_invokes_adapter(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOCAL")

    result = service.save_local("watch_later", item, note="later", auto_sync=False)

    row = db.get_saved_membership("watch_later", item.item_key)
    assert row is not None
    assert row["note"] == "later"
    assert row["sync_status"] == "pending"
    assert result.saved is True
    assert result.sync_status == "pending"
    assert result.sync_task_id == ""
    assert adapter.calls == []


def test_tiktok_local_save_is_local_only_not_pending_upgrade(db: Database) -> None:
    """TikTok has no native-save adapter by design; a local save must commit
    as ``unsupported`` / ``local_only_source`` instead of a misleading pending
    "upgrade and retry" state."""
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("tiktok", "7340000000000000001")

    result = service.save_local("favorite", item, auto_sync=True)

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "unsupported"
    assert result.saved is True
    assert result.sync_status == "unsupported"
    assert adapter.calls == []


def test_validate_native_save_selection_reads_existing_membership_without_mutation(
    db: Database,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("reddit", True, False, False))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput(
        "reddit",
        "t3_public1",
        "https://www.reddit.com/r/test/comments/public1/title/",
        "post",
    )
    service.save_local("watch_later", item, auto_sync=False)

    selected, route = service.validate_native_save_selection(
        "watch_later",
        "reddit:t3_public1",
    )

    assert selected == item
    assert route == NativeSaveRoute("watch_later", "favorite", "Reddit Saved")
    assert adapter.calls == []
    with pytest.raises(ValueError, match="does not exist"):
        service.validate_native_save_selection("favorite", "reddit:t3_public1")


async def test_auto_sync_returns_after_local_commit_and_runs_in_background(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    started: list[asyncio.Task[Any]] = []

    def start_task(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        assert name.startswith("saved-sync:")
        assert db.get_saved_membership("watch_later", "bilibili:BV1AUTO") is not None
        task = asyncio.create_task(coro, name=name)
        started.append(task)
        return task

    service = SavedSyncService(db, NativeSaveRouter([adapter]), task_starter=start_task)

    result = service.save_local(
        "watch_later",
        SavedItemInput("bilibili", "BV1AUTO"),
        auto_sync=True,
    )

    assert result.sync_status == "pending"
    assert result.sync_task_id
    assert adapter.calls == []
    assert len(started) == 1
    gate.set()
    await started[0]
    assert service.get_sync_task(result.sync_task_id).items[0].status == "synced"


@pytest.mark.parametrize("terminal_status", ["synced", "already_synced"])
def test_duplicate_local_save_preserves_terminal_native_state(
    db: Database,
    terminal_status: NativeSaveStatus,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", f"BV1{terminal_status.upper()}")
    service.save_local("favorite", item)
    db.upsert_native_save_state(
        "favorite",
        item.item_key,
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status=terminal_status,
        task_id="terminal-task",
    )

    result = service.save_local("favorite", item, note="updated", auto_sync=False)
    row = db.get_saved_membership("favorite", item.item_key)

    assert result.sync_status == terminal_status
    assert result.sync_task_id == "terminal-task"
    assert row is not None
    assert row["note"] == "updated"
    assert row["sync_status"] == terminal_status
    assert row["sync_task_id"] == "terminal-task"
    assert row["resolved_target"] == "B站 OpenBiliClaw 收藏夹"


def test_local_save_cannot_erase_task_claimed_between_membership_and_state_insert(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    claiming_service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1INTERLEAVE")
    original_get = db.get_saved_membership
    original_ensure = db.ensure_native_save_state
    claimed_task_ids: list[str] = []

    def read_then_claim(list_kind: str, item_key: str) -> dict[str, Any] | None:
        row = original_get(list_kind, item_key)
        if not claimed_task_ids and row is not None and not str(row["requested_action"]):
            claimed = claiming_service.create_sync_task("favorite", [item_key], "manual_single")
            claimed_task_ids.append(claimed.task_id)
        return row

    def claim_then_ensure(
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        if not claimed_task_ids:
            claimed = claiming_service.create_sync_task("favorite", [item_key], "manual_single")
            claimed_task_ids.append(claimed.task_id)
        return original_ensure(list_kind, item_key, requested_action)

    monkeypatch.setattr(db, "get_saved_membership", read_then_claim)
    monkeypatch.setattr(db, "ensure_native_save_state", claim_then_ensure)

    result = service.save_local("favorite", item, auto_sync=False)
    row = db.get_saved_membership("favorite", item.item_key)

    assert row is not None
    assert row["sync_task_id"] == claimed_task_ids[0]
    assert result.sync_task_id == claimed_task_ids[0]
    assert claiming_service.get_sync_task(claimed_task_ids[0]).items[0].status == "pending"


async def test_platform_failure_keeps_local_membership(db: Database) -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("bilibili", True, True, True),
        result_status="failed",
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1FAIL")
    local = service.save_local("watch_later", item, auto_sync=False)
    created = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(created.task_id)

    assert db.get_saved_membership("watch_later", item.item_key) is not None
    assert local.saved is True
    assert result.items[0].status == "failed"


async def test_blank_task_ids_fail_closed_without_reading_or_executing(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    first = SavedItemInput("bilibili", "BV1BLANKTASK")
    second = SavedItemInput("reddit", "blank-task-post")
    service.save_local("favorite", first)
    service.save_local("favorite", second)

    with pytest.raises(ValueError, match="task_id"):
        await service.run_sync_task("")
    with pytest.raises(ValueError, match="task_id"):
        service.get_sync_task("   ")

    assert adapter.calls == []
    assert db.get_saved_membership("favorite", first.item_key)["sync_task_id"] == ""  # type: ignore[index]
    assert db.get_saved_membership("favorite", second.item_key)["sync_task_id"] == ""  # type: ignore[index]


def test_create_sync_task_uses_one_task_id_for_selected_eligible_items(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    first = SavedItemInput("bilibili", "BV1FIRST")
    second = SavedItemInput("reddit", "post-2")
    excluded = SavedItemInput("bilibili", "BV1EXCLUDED")
    for item in (first, second, excluded):
        db.upsert_saved_membership("favorite", item)
    db.upsert_native_save_state(
        "favorite",
        excluded.item_key,
        requested_action="favorite",
        resolved_action="favorite",
        resolved_target="B站 OpenBiliClaw 收藏夹",
        status="synced",
    )

    created = service.create_sync_task(
        "favorite",
        [first.item_key, second.item_key, excluded.item_key],
        "manual_batch",
    )

    assert created.task_id
    assert {item.item_key for item in created.items} == {
        first.item_key,
        second.item_key,
        excluded.item_key,
    }
    assert {item.status for item in created.items if item.item_key != excluded.item_key} == {
        "pending"
    }
    assert next(item for item in created.items if item.item_key == excluded.item_key).status == (
        "synced"
    )
    rows = db.list_native_save_states_by_task(created.task_id)
    assert {row["item_key"] for row in rows} == {first.item_key, second.item_key}


def test_duplicate_task_creation_does_not_steal_pending_task_rows(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1OWNED")
    service.save_local("favorite", item)

    first = service.create_sync_task("favorite", [item.item_key], "manual_single")
    duplicate = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in first.items] == [item.item_key]
    assert duplicate.items[0].status == "failed"
    assert duplicate.items[0].error_code == "sync_already_in_progress"
    assert service.get_sync_task(first.task_id).items[0].status == "pending"
    assert service.get_sync_task(duplicate.task_id) == duplicate


def test_task_starter_failure_releases_pending_ownership(db: Database) -> None:
    def failing_starter(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        del name, coro
        raise RuntimeError("task registry unavailable")

    item = SavedItemInput("bilibili", "BV1STARTFAIL")
    db.upsert_saved_membership("favorite", item)
    service = SavedSyncService(db, NativeSaveRouter(), task_starter=failing_starter)

    with pytest.raises(RuntimeError, match="task registry unavailable"):
        service.create_sync_task("favorite", [item.item_key], "manual_single")

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "pending"
    assert row["sync_task_id"] == ""
    retry = SavedSyncService(db, NativeSaveRouter()).create_sync_task(
        "favorite", [item.item_key], "manual_single"
    )
    assert [result.item_key for result in retry.items] == [item.item_key]


def test_task_starter_cancellation_releases_pending_ownership(db: Database) -> None:
    def cancelled_starter(name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        del name, coro
        raise asyncio.CancelledError

    item = SavedItemInput("bilibili", "BV1STARTCANCEL")
    db.upsert_saved_membership("favorite", item)
    service = SavedSyncService(db, NativeSaveRouter(), task_starter=cancelled_starter)

    with pytest.raises(asyncio.CancelledError):
        service.create_sync_task("favorite", [item.item_key], "manual_single")

    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "pending"
    assert row["sync_task_id"] == ""


def test_stale_never_started_task_can_be_safely_reclaimed(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1NEVERSTARTED")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_claimed_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    recovered = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in recovered.items] == [item.item_key]
    assert service.get_sync_task(abandoned.task_id).items[0].status == "failed"
    assert service.get_sync_task(abandoned.task_id).items[0].error_code == "interrupted"
    assert service.get_sync_task(recovered.task_id).items[0].status == "pending"


def test_nonempty_blank_item_selection_fails_closed(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    first = SavedItemInput("bilibili", "BV1BLANKFIRST")
    second = SavedItemInput("bilibili", "BV1BLANKSECOND")
    service.save_local("favorite", first)
    service.save_local("favorite", second)

    with pytest.raises(ValueError, match="item_keys"):
        service.create_sync_task("favorite", ["   "], "manual_batch")

    assert db.get_saved_membership("favorite", first.item_key)["sync_task_id"] == ""  # type: ignore[index]
    assert db.get_saved_membership("favorite", second.item_key)["sync_task_id"] == ""  # type: ignore[index]


async def test_concurrent_services_atomically_claim_task_creation(db: Database) -> None:
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(db, NativeSaveRouter())
    second_service = SavedSyncService(second_db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1ATOMICCREATE")
    first_service.save_local("favorite", item)

    first, second = await asyncio.gather(
        asyncio.to_thread(
            first_service.create_sync_task,
            "favorite",
            [item.item_key],
            "manual_single",
        ),
        asyncio.to_thread(
            second_service.create_sync_task,
            "favorite",
            [item.item_key],
            "manual_single",
        ),
    )

    winner, loser = (first, second) if first.items[0].status == "pending" else (second, first)
    assert [result.item_key for result in winner.items] == [item.item_key]
    assert loser.items[0].status == "failed"
    assert loser.items[0].error_code == "sync_already_in_progress"
    assert first_service.get_sync_task(winner.task_id).items[0].status == "pending"
    assert second_service.get_sync_task(loser.task_id) == loser


async def test_adapter_exception_is_sanitized_and_persisted_per_item(db: Database) -> None:
    adapter = RaisingAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1SECRET")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)
    reconstructed = SavedSyncService(db, NativeSaveRouter()).get_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "adapter_exception"
    assert "private" not in result.items[0].error_message
    assert reconstructed == result


async def test_concurrent_task_runners_execute_each_item_once(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1SINGLEFLIGHT")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    first_runner = asyncio.create_task(service.run_sync_task(task.task_id))
    second_runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [item.item_key]

    gate.set()
    first_result, second_result = await asyncio.gather(first_runner, second_runner)
    assert adapter.calls == [item.item_key]
    assert first_result == second_result
    assert first_result.items[0].status == "synced"
    assert task.task_id not in service._task_run_locks


async def test_concurrent_services_execute_claimed_item_once(db: Database) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(db, NativeSaveRouter([adapter]))
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CROSSSERVICE")
    first_service.save_local("favorite", item)
    task = first_service.create_sync_task("favorite", [item.item_key], "manual_single")

    first_runner = asyncio.create_task(first_service.run_sync_task(task.task_id))
    second_runner = asyncio.create_task(second_service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [item.item_key]

    gate.set()
    first_result, second_result = await asyncio.gather(first_runner, second_runner)
    assert adapter.calls == [item.item_key]
    assert first_result.items[0].status == "synced"
    assert second_result.items[0].status in {"pending", "syncing", "synced"}
    assert second_service.get_sync_task(task.task_id).items[0].status == "synced"


def test_polling_releases_stale_started_but_unclaimed_pending_rows(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STARTEDCRASH")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_sync_task_runner(abandoned.task_id, "crashed-poll-runner")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_heartbeat_at = datetime('now', '-10 minutes')
        WHERE task_id = ?
        """,
        (abandoned.task_id,),
    )
    db.conn.commit()

    polled = service.get_sync_task(abandoned.task_id)

    assert polled.items[0].status == "failed"
    assert polled.items[0].error_code == "interrupted"
    row = db.get_saved_membership("favorite", item.item_key)
    assert row is not None
    assert row["sync_status"] == "pending"
    assert row["sync_task_id"] == ""
    retry = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert [result.item_key for result in retry.items] == [item.item_key]


def test_manual_creation_recovers_stale_started_pending_row_without_old_runner(
    db: Database,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STARTEDMANUAL")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_sync_task_runner(abandoned.task_id, "crashed-manual-runner")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_heartbeat_at = datetime('now', '-10 minutes')
        WHERE task_id = ?
        """,
        (abandoned.task_id,),
    )
    db.conn.commit()

    retry = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in retry.items] == [item.item_key]
    assert retry.task_id != abandoned.task_id


async def test_active_task_heartbeat_protects_later_sequential_pending_row(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        task_heartbeat_interval_seconds=0.005,
    )
    first = SavedItemInput("bilibili", "BV1ACTIVEA")
    second = SavedItemInput("bilibili", "BV1ACTIVEB")
    service.save_local("favorite", first)
    service.save_local("favorite", second)
    task = service.create_sync_task("favorite", [first.item_key, second.item_key], "manual_batch")
    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [first.item_key]
    db.conn.execute(
        """
        UPDATE native_save_states
        SET task_heartbeat_at = datetime('now', '-10 minutes')
        WHERE task_id = ?
        """,
        (task.task_id,),
    )
    db.conn.commit()
    for _ in range(100):
        refreshed = db.conn.execute(
            """
            SELECT MIN(task_heartbeat_at > datetime('now', '-1 minute'))
            FROM native_save_states WHERE task_id = ?
            """,
            (task.task_id,),
        ).fetchone()
        if refreshed is not None and int(refreshed[0] or 0) == 1:
            break
        await asyncio.sleep(0.01)

    duplicate = service.create_sync_task("favorite", [second.item_key], "manual_single")
    assert duplicate.items[0].status == "failed"
    assert duplicate.items[0].error_code == "sync_already_in_progress"
    assert adapter.calls == [first.item_key]

    gate.set()
    await runner
    assert adapter.calls == [first.item_key, second.item_key]


async def test_explicit_task_executes_snapshot_order_not_membership_time(
    db: Database,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    first = SavedItemInput("bilibili", "BV1SNAPSHOTA")
    second = SavedItemInput("bilibili", "BV1SNAPSHOTB")
    service.save_local("favorite", first)
    service.save_local("favorite", second)
    db.conn.execute(
        """
        UPDATE saved_memberships
        SET added_at = CASE item_key
            WHEN ? THEN '2026-08-03 00:00:00'
            WHEN ? THEN '2026-08-03 00:00:01'
            ELSE added_at
        END
        WHERE list_kind = 'favorite' AND item_key IN (?, ?)
        """,
        (first.item_key, second.item_key, first.item_key, second.item_key),
    )
    db.conn.commit()
    task = service.create_sync_task(
        "favorite",
        [first.item_key, second.item_key],
        "manual_batch",
    )

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == [first.item_key, second.item_key]
    assert [item.item_key for item in result.items] == [first.item_key, second.item_key]
    assert {item.status for item in result.items} == {"synced"}


async def test_cross_service_second_runner_cannot_execute_or_release_owner_pending(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    second_db = Database(db._db_path)
    second_db.initialize()
    owner_service = SavedSyncService(db, NativeSaveRouter([adapter]))
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    items = [SavedItemInput("bilibili", f"BV1RUNNER{suffix}") for suffix in "ABC"]
    for item in items:
        owner_service.save_local("favorite", item)
    task = owner_service.create_sync_task(
        "favorite", [item.item_key for item in items], "manual_batch"
    )
    owner_runner = asyncio.create_task(owner_service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert adapter.calls == [items[0].item_key]

    non_owner_result = await asyncio.wait_for(
        second_service.run_sync_task(task.task_id), timeout=0.2
    )
    assert {result.status for result in non_owner_result.items} <= {"pending", "syncing"}
    assert adapter.calls == [items[0].item_key]

    def cancel_non_owner_poll(task_id: str) -> SavedSyncBatchResult:
        del task_id
        raise asyncio.CancelledError

    monkeypatch.setattr(second_service, "get_sync_task", cancel_non_owner_poll)
    with pytest.raises(asyncio.CancelledError):
        await second_service.run_sync_task(task.task_id)
    third_state = second_db.get_saved_membership("favorite", items[2].item_key)
    assert third_state is not None
    assert third_state["sync_task_id"] == task.task_id

    owner_runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_runner
    for item in items[1:]:
        state = db.get_saved_membership("favorite", item.item_key)
        assert state is not None
        assert state["sync_status"] == "pending"
        assert state["sync_task_id"] == ""


async def test_task_heartbeat_failure_cancels_work_and_releases_pending(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        task_heartbeat_interval_seconds=0.005,
    )
    first = SavedItemInput("bilibili", "BV1HEARTFAILA")
    second = SavedItemInput("bilibili", "BV1HEARTFAILB")
    service.save_local("favorite", first)
    service.save_local("favorite", second)
    task = service.create_sync_task("favorite", [first.item_key, second.item_key], "manual_batch")

    def fail_heartbeat(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise RuntimeError("private heartbeat storage failure")

    monkeypatch.setattr(db, "heartbeat_native_sync_task", fail_heartbeat)
    runner = asyncio.create_task(service.run_sync_task(task.task_id))

    with pytest.raises(RuntimeError, match="task heartbeat failed") as exc_info:
        await asyncio.wait_for(runner, timeout=0.5)
    assert "private" not in str(exc_info.value)
    assert adapter.calls == [first.item_key]
    first_state = db.get_saved_membership("favorite", first.item_key)
    second_state = db.get_saved_membership("favorite", second.item_key)
    assert first_state is not None and first_state["sync_status"] == "failed"
    assert second_state is not None and second_state["sync_task_id"] == ""
    retry = service.create_sync_task("favorite", [second.item_key], "manual_single")
    assert [result.item_key for result in retry.items] == [second.item_key]


async def test_transient_sqlite_lock_retries_heartbeats_and_terminal_persistence(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        task_heartbeat_interval_seconds=0.005,
    )
    item = SavedItemInput("bilibili", "BV1HEARTBEATLOCKRETRY")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    original_item_heartbeat = db.heartbeat_native_save_claim
    original_task_heartbeat = db.heartbeat_native_sync_task
    original_complete = db.complete_native_save_claim
    item_calls = 0
    task_calls = 0
    complete_calls = 0

    def item_heartbeat(*args: object, **kwargs: object) -> bool:
        nonlocal item_calls
        item_calls += 1
        if item_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_item_heartbeat(*args, **kwargs)  # type: ignore[arg-type]

    def task_heartbeat(*args: object, **kwargs: object) -> int:
        nonlocal task_calls
        task_calls += 1
        if task_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_task_heartbeat(*args, **kwargs)  # type: ignore[arg-type]

    def complete(*args: object, **kwargs: object) -> bool:
        nonlocal complete_calls
        complete_calls += 1
        if complete_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_complete(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "heartbeat_native_save_claim", item_heartbeat)
    monkeypatch.setattr(db, "heartbeat_native_sync_task", task_heartbeat)
    monkeypatch.setattr(db, "complete_native_save_claim", complete)
    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if item_calls >= 2 and task_calls >= 2:
            break
        await asyncio.sleep(0.005)
    gate.set()

    result = await runner

    assert item_calls >= 2
    assert task_calls >= 2
    assert complete_calls >= 2
    assert result.items[0].status == "synced"


async def test_terminal_item_wins_task_heartbeat_completion_race(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1HEARTBEATTERMINALRACE")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    terminal = asyncio.Event()

    async def terminal_but_not_returned(
        rows: list[dict[str, Any]],
        runner_id: str,
    ) -> None:
        row = rows[0]
        execution_id = "terminal-race-owner"
        assert db.claim_native_save_item(
            "favorite",
            item.item_key,
            str(row["task_id"]),
            runner_id,
            execution_id,
        )
        assert db.complete_native_save_claim(
            "favorite",
            item.item_key,
            str(row["task_id"]),
            execution_id,
            requested_action="favorite",
            resolved_action="favorite",
            resolved_target="B站 OpenBiliClaw 收藏夹",
            status="synced",
        )
        terminal.set()
        await asyncio.Event().wait()

    async def lose_after_terminal(*args: object, **kwargs: object) -> None:
        del args, kwargs
        await terminal.wait()
        raise saved_sync_service_module._NativeSaveTaskRunnerOwnershipLostError

    monkeypatch.setattr(service, "_run_platform_group", terminal_but_not_returned)
    monkeypatch.setattr(service, "_heartbeat_sync_task", lose_after_terminal)

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "synced"


async def test_item_heartbeat_exception_detaches_with_retrying_lease_and_late_result(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    adapter = CancellationSuppressingAdapter(release)
    second_db = Database(db._db_path)
    second_db.initialize()
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1ITEMHEARTFAIL")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    original_heartbeat = db.heartbeat_native_save_claim
    heartbeat_calls = 0

    def transient_heartbeat_failure(*args: object, **kwargs: object) -> bool:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            raise RuntimeError("private item heartbeat failure")
        return original_heartbeat(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "heartbeat_native_save_claim", transient_heartbeat_failure)

    with pytest.raises(RuntimeError, match="Native save item heartbeat failed") as exc_info:
        await asyncio.wait_for(service.run_sync_task(task.task_id), timeout=0.5)
    assert "private" not in str(exc_info.value)
    assert adapter.calls == [item.item_key]
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()
    for _ in range(100):
        if heartbeat_calls >= 2:
            break
        await asyncio.sleep(0.01)
    assert heartbeat_calls >= 2

    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        refreshed = db.conn.execute(
            """
            SELECT last_attempt_at > datetime('now', '-1 minute')
            FROM native_save_states
            WHERE list_kind = 'favorite' AND item_key = ?
            """,
            (item.item_key,),
        ).fetchone()
        if refreshed is not None and int(refreshed[0]) == 1:
            break
        await asyncio.sleep(0.01)
    duplicate = second_service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert duplicate.items[0].status == "failed"
    assert duplicate.items[0].error_code == "sync_already_in_progress"
    assert adapter.calls == [item.item_key]

    release.set()
    for _ in range(100):
        result = service.get_sync_task(task.task_id)
        if result.items and result.items[0].status == "synced" and not service._detached_attempts:
            break
        await asyncio.sleep(0.01)
    assert result.items[0].status == "synced"
    assert service._detached_attempts == set()


async def test_partial_batch_cancellation_releases_later_pending_ownership(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    first = SavedItemInput("bilibili", "BV1CANCELA")
    second = SavedItemInput("bilibili", "BV1CANCELB")
    service.save_local("favorite", first)
    service.save_local("favorite", second)
    task = service.create_sync_task("favorite", [first.item_key, second.item_key], "manual_batch")

    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    assert len(adapter.calls) == 1
    active_key = adapter.calls[0]
    pending_key = next(item.item_key for item in (first, second) if item.item_key != active_key)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    active_state = db.get_saved_membership("favorite", active_key)
    pending_state = db.get_saved_membership("favorite", pending_key)
    assert active_state is not None and active_state["sync_status"] == "failed"
    assert pending_state is not None and pending_state["sync_status"] == "pending"
    assert pending_state["sync_task_id"] == ""
    retry = service.create_sync_task("favorite", [pending_key], "manual_single")
    assert [result.item_key for result in retry.items] == [pending_key]


async def test_pending_extension_job_cancellation_does_not_detach(
    db: Database,
) -> None:
    broker = ExtensionNativeSaveBroker(
        db,
        wake_platform=AsyncMock(),
        dispatch_deadline_seconds=1.0,
        execution_deadline_seconds=1.0,
        poll_interval_seconds=0.001,
    )
    service = SavedSyncService(
        db,
        NativeSaveRouter(build_extension_native_save_adapters(broker)),
    )
    item = SavedItemInput("reddit", "t3_pending", "https://www.reddit.com/comments/pending/")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    runner = asyncio.create_task(service.run_sync_task(task.task_id))

    for _ in range(100):
        job_row = db.conn.execute(
            "SELECT * FROM extension_native_save_jobs WHERE item_key = ?",
            (item.item_key,),
        ).fetchone()
        if job_row is not None:
            break
        await asyncio.sleep(0.001)
    assert job_row is not None and job_row["status"] == "pending"

    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    durable_job = db.get_extension_native_save_job(str(job_row["job_id"]))
    assert durable_job is not None and durable_job["status"] == "cancelled"
    assert service._detached_attempts == set()
    assert db.conn.execute("SELECT COUNT(*) FROM extension_native_save_jobs").fetchone()[0] == 1


async def test_claimed_extension_job_survives_short_service_adapter_timeout(
    db: Database,
) -> None:
    broker = ExtensionNativeSaveBroker(
        db,
        wake_platform=AsyncMock(),
        dispatch_deadline_seconds=1.0,
        execution_deadline_seconds=1.0,
        poll_interval_seconds=0.001,
    )
    service = SavedSyncService(
        db,
        NativeSaveRouter(build_extension_native_save_adapters(broker)),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    item = SavedItemInput("reddit", "t3_timeout", "https://www.reddit.com/comments/timeout/")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    runner = asyncio.create_task(service.run_sync_task(task.task_id))

    claimed = None
    for _ in range(100):
        claimed = broker.claim_next("reddit")
        if claimed is not None:
            break
        await asyncio.sleep(0.001)
    assert claimed is not None

    initial = await asyncio.wait_for(runner, timeout=0.5)
    assert initial.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    assert broker.submit_result(
        "reddit",
        ExtensionNativeSaveResultIn(claimed.job_id, item.item_key, "synced"),
    )

    for _ in range(100):
        persisted = service.get_sync_task(task.task_id)
        if persisted.items[0].status == "synced":
            break
        await asyncio.sleep(0.001)
    assert persisted.items[0].status == "synced"
    assert persisted.items[0].error_code == ""
    assert db.conn.execute("SELECT COUNT(*) FROM extension_native_save_jobs").fetchone()[0] == 1
    assert broker.claim_next("reddit") is None


async def test_live_aged_claim_heartbeat_prevents_cross_service_reexecution(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    second_db = Database(db._db_path)
    second_db.initialize()
    first_service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.01,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LIVEAGED")
    first_service.save_local("favorite", item)
    task = first_service.create_sync_task("favorite", [item.item_key], "manual_single")
    first_runner = asyncio.create_task(first_service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        row = db.get_saved_membership("favorite", item.item_key)
        if row is not None and str(row["last_attempt_at"]) > "2000-01-01":
            timestamp = db.conn.execute(
                """
                SELECT last_attempt_at > datetime('now', '-1 minute')
                FROM native_save_states
                WHERE list_kind = 'favorite' AND item_key = ?
                """,
                (item.item_key,),
            ).fetchone()
            if timestamp is not None and int(timestamp[0]) == 1:
                break
        await asyncio.sleep(0.01)

    second_result = await second_service.run_sync_task(task.task_id)
    assert adapter.calls == [item.item_key]
    assert second_result.items[0].status == "syncing"

    gate.set()
    await first_runner
    assert adapter.calls == [item.item_key]


async def test_adapter_deadline_keeps_lease_until_cancellation_suppressing_call_ends(
    db: Database,
) -> None:
    release = asyncio.Event()
    adapter = CancellationSuppressingAdapter(release)
    second_db = Database(db._db_path)
    second_db.initialize()
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1TIMEOUT")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == [item.item_key]
    assert result.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        refreshed = db.conn.execute(
            """
            SELECT last_attempt_at > datetime('now', '-1 minute')
            FROM native_save_states
            WHERE list_kind = 'favorite' AND item_key = ?
            """,
            (item.item_key,),
        ).fetchone()
        if refreshed is not None and int(refreshed[0]) == 1:
            break
        await asyncio.sleep(0.01)

    second = second_service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert second.items[0].status == "failed"
    assert second.items[0].error_code == "sync_already_in_progress"
    assert adapter.calls == [item.item_key]

    release.set()
    for _ in range(100):
        persisted = service.get_sync_task(task.task_id)
        if (
            persisted.items
            and persisted.items[0].status == "synced"
            and not service._detached_attempts
        ):
            break
        await asyncio.sleep(0.01)
    assert persisted.items[0].status == "synced"
    assert persisted.items[0].error_code == ""
    assert adapter.calls == [item.item_key]
    for _ in range(100):
        if not service._detached_attempts:
            break
        await asyncio.sleep(0)
    assert service._detached_attempts == set()


async def test_detached_timeout_restarts_heartbeat_after_later_failure(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    adapter = CancellationSuppressingAdapter(release)
    second_db = Database(db._db_path)
    second_db.initialize()
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    second_service = SavedSyncService(second_db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1DETACHEDHEARTFAIL")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    original_heartbeat = db.heartbeat_native_save_claim
    fail_next_heartbeat = False
    heartbeat_failures = 0

    def fail_after_detach(*args: object, **kwargs: object) -> bool:
        nonlocal fail_next_heartbeat, heartbeat_failures
        if fail_next_heartbeat:
            fail_next_heartbeat = False
            heartbeat_failures += 1
            raise RuntimeError("private detached heartbeat failure")
        return original_heartbeat(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db, "heartbeat_native_save_claim", fail_after_detach)

    initial = await service.run_sync_task(task.task_id)
    assert initial.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()

    fail_next_heartbeat = True
    for _ in range(100):
        if heartbeat_failures:
            break
        await asyncio.sleep(0.01)
    assert heartbeat_failures == 1

    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()
    for _ in range(100):
        refreshed = db.conn.execute(
            """
            SELECT last_attempt_at > datetime('now', '-1 minute')
            FROM native_save_states
            WHERE list_kind = 'favorite' AND item_key = ?
            """,
            (item.item_key,),
        ).fetchone()
        if refreshed is not None and int(refreshed[0]) == 1:
            break
        await asyncio.sleep(0.01)

    duplicate = second_service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert duplicate.items[0].status == "failed"
    assert duplicate.items[0].error_code == "sync_already_in_progress"
    assert adapter.calls == [item.item_key]

    release.set()
    for _ in range(100):
        persisted = service.get_sync_task(task.task_id)
        if (
            persisted.items
            and persisted.items[0].status == "synced"
            and not service._detached_attempts
        ):
            break
        await asyncio.sleep(0.01)
    assert persisted.items[0].status == "synced"
    assert service._detached_attempts == set()


async def test_detached_malformed_adapter_result_is_sanitized_and_completed(
    db: Database,
) -> None:
    release = asyncio.Event()
    adapter = MalformedCancellationSuppressingAdapter(release)
    service = SavedSyncService(
        db,
        NativeSaveRouter([adapter]),
        claim_heartbeat_interval_seconds=0.005,
        adapter_timeout_seconds=0.02,
    )
    item = SavedItemInput("bilibili", "BV1LATEMALFORMED")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    initial = await service.run_sync_task(task.task_id)
    assert initial.items[0].status == "syncing"
    assert len(service._detached_attempts) == 1
    await adapter.cancel_seen.wait()

    release.set()
    for _ in range(100):
        result = service.get_sync_task(task.task_id)
        if result.items and result.items[0].status != "syncing":
            break
        await asyncio.sleep(0.01)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"
    assert "private" not in result.items[0].error_message
    for _ in range(100):
        if not service._detached_attempts:
            break
        await asyncio.sleep(0)
    assert service._detached_attempts == set()


async def test_lost_route_claim_does_not_start_adapter(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOSTROUTE")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    monkeypatch.setattr(db, "update_native_save_claim_route", lambda *args, **kwargs: False)

    await service.run_sync_task(task.task_id)

    assert adapter.calls == []


async def test_completed_task_lock_entry_is_released(db: Database) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1LOCKCLEANUP")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    await service.run_sync_task(task.task_id)

    assert task.task_id not in service._task_run_locks


async def test_cancelled_adapter_attempt_is_persisted_as_retryable_and_reraised(
    db: Database,
) -> None:
    gate = asyncio.Event()
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True), gate=gate)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CANCELLED")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    persisted = service.get_sync_task(task.task_id).items[0]
    assert persisted.status == "failed"
    assert persisted.error_code == "interrupted"
    retry = service.create_sync_task("watch_later", [item.item_key], "manual_single")
    assert [result.item_key for result in retry.items] == [item.item_key]


async def test_stale_syncing_claim_is_reconciled_without_reexecuting_adapter(
    db: Database,
) -> None:
    adapter = FakeAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1STALE")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    db.conn.execute(
        """
        UPDATE native_save_states
        SET status = 'syncing', execution_id = 'dead-worker',
            last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == []
    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "interrupted"


def test_polling_reconciles_stale_syncing_claim(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STALEPOLL")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_sync_task_runner(task.task_id, "dead-poll-runner")
    assert db.claim_native_save_item(
        "favorite", item.item_key, task.task_id, "dead-poll-runner", "dead-poller"
    )
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    result = service.get_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "interrupted"


def test_manual_task_creation_recovers_stale_syncing_claim(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("bilibili", "BV1STALERETRY")
    service.save_local("favorite", item)
    abandoned = service.create_sync_task("favorite", [item.item_key], "manual_single")
    assert db.claim_native_sync_task_runner(abandoned.task_id, "dead-retry-runner")
    assert db.claim_native_save_item(
        "favorite",
        item.item_key,
        abandoned.task_id,
        "dead-retry-runner",
        "dead-retry-owner",
    )
    db.conn.execute(
        """
        UPDATE native_save_states
        SET last_attempt_at = datetime('now', '-10 minutes')
        WHERE list_kind = 'favorite' AND item_key = ?
        """,
        (item.item_key,),
    )
    db.conn.commit()

    retry = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert [result.item_key for result in retry.items] == [item.item_key]
    assert retry.task_id != abandoned.task_id


@pytest.mark.parametrize("invalid_status", ["pending", "syncing"])
async def test_adapter_nonterminal_result_is_normalized_to_failed(
    db: Database,
    invalid_status: NativeSaveStatus,
) -> None:
    adapter = FakeAdapter(
        NativeSaveCapability("bilibili", True, True, True),
        result_status=invalid_status,
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", f"BV1INVALID{invalid_status.upper()}")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"


async def test_router_resolved_route_cannot_be_overridden_by_adapter(db: Database) -> None:
    adapter = MisroutingAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1ROUTE")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "synced"
    assert result.items[0].resolved_action == "watch_later"
    assert result.items[0].resolved_target == "B站稍后观看"


async def test_target_resolution_exception_is_sanitized_per_item(db: Database) -> None:
    adapter = RaisingTargetAdapter(NativeSaveCapability("bilibili", True, True, True))
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1TARGET")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "adapter_exception"
    assert "private" not in result.items[0].error_message


@pytest.mark.parametrize("invalid_target", [None, 123, "", "   ", "x" * 300])
async def test_invalid_target_label_is_sanitized_and_releases_item_owner(
    db: Database,
    invalid_target: object,
) -> None:
    adapter = InvalidTargetAdapter(invalid_target)
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", f"BV1TARGET{len(str(invalid_target))}")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert adapter.calls == []
    assert result.items[0].status == "failed"
    assert result.items[0].error_code == "invalid_adapter_result"
    assert "300" not in result.items[0].error_message
    row = db.list_native_save_states_by_task(task.task_id)[0]
    assert row["status"] == "failed"
    assert row["execution_id"] == ""


async def test_immediate_cancellation_suppression_persists_late_success(db: Database) -> None:
    adapter = ImmediateCancellationSuccessAdapter(
        NativeSaveCapability("bilibili", True, True, True)
    )
    service = SavedSyncService(db, NativeSaveRouter([adapter]))
    item = SavedItemInput("bilibili", "BV1CANCELSUCCESS")
    service.save_local("favorite", item)
    task = service.create_sync_task("favorite", [item.item_key], "manual_single")

    runner = asyncio.create_task(service.run_sync_task(task.task_id))
    for _ in range(100):
        if adapter.calls:
            break
        await asyncio.sleep(0)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner

    persisted = service.get_sync_task(task.task_id)
    assert persisted.items[0].status == "synced"
    assert persisted.items[0].error_code == ""
    assert adapter.calls == [item.item_key]


async def test_unregistered_platform_is_persisted_as_unsupported(db: Database) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("youtube", "video-1")
    service.save_local("watch_later", item)
    task = service.create_sync_task("watch_later", [item.item_key], "manual_single")

    result = await service.run_sync_task(task.task_id)

    assert result.items[0].status == "unsupported"
    assert result.items[0].error_code == "unsupported_adapter_missing"
    assert service.get_sync_task(task.task_id) == result


async def test_missing_adapter_unsupported_row_can_be_retried_explicitly(
    db: Database,
) -> None:
    service = SavedSyncService(db, NativeSaveRouter())
    item = SavedItemInput("youtube", "video-retry")
    service.save_local("favorite", item)
    first = service.create_sync_task("favorite", [item.item_key], "manual_single")
    await service.run_sync_task(first.task_id)

    retry = service.create_sync_task("favorite", [item.item_key], "manual_single")

    assert retry.items[0].status == "pending"
    assert retry.items[0].error_code == ""
