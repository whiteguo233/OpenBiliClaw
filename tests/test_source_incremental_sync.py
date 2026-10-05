"""Regression tests for extension-online periodic bootstrap scheduling."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.runtime import source_incremental_sync as scheduler_module
from openbiliclaw.runtime.source_incremental_sync import (
    SOURCE_ORDER,
    SourceIncrementalSync,
    SourceIncrementalSyncResult,
)
from openbiliclaw.sources.bootstrap_state import default_source_bootstrap_state
from openbiliclaw.sources.source_bootstrap import BootstrapEnqueueResult

if TYPE_CHECKING:
    from pathlib import Path


class _FakePresence:
    def __init__(self, present: bool = True) -> None:
        self.present = present
        self.calls: list[int] = []

    def is_present(self, grace_seconds: int) -> bool:
        self.calls.append(grace_seconds)
        return self.present


class _FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


class _FakeMemory:
    def __init__(self, state: dict[str, object] | None = None) -> None:
        self.state = deepcopy(state or default_source_bootstrap_state())
        self.update_calls = 0

    def load_source_bootstrap_state(self) -> dict[str, object]:
        return deepcopy(self.state)

    def update_source_bootstrap_state(self, mutator: Any) -> dict[str, object]:
        self.update_calls += 1
        current = deepcopy(self.state)
        result = mutator(current)
        self.state = deepcopy(current if result is None else result)
        return deepcopy(self.state)


class _FakeDatabase:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for table in (
            "xhs_tasks",
            "dy_tasks",
            "yt_tasks",
            "zhihu_tasks",
            "reddit_tasks",
            "linuxdo_tasks",
            "v2ex_tasks",
        ):
            self.conn.execute(
                f"CREATE TABLE {table} ("
                "id TEXT PRIMARY KEY, type TEXT, payload_json TEXT, "
                "status TEXT, created_at TEXT, completed_at TEXT, result_json TEXT)"
            )
        self.conn.commit()


def _harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: SchedulerConfig | None = None,
    enabled: dict[str, bool] | None = None,
    state: dict[str, object] | None = None,
    runtime_config: Any | None = None,
) -> tuple[SourceIncrementalSync, _FakeDatabase, _FakeMemory, _FakePresence, _FakeClock, list[str]]:
    database = _FakeDatabase()
    memory = _FakeMemory(state)
    presence = _FakePresence()
    clock = _FakeClock()
    kicks: list[str] = []
    enqueue_calls: list[str] = []
    counters = {source: 0 for source in SOURCE_ORDER}
    table_types = {
        "xhs": ("xhs_tasks", "bootstrap_profile"),
        "dy": ("dy_tasks", "bootstrap_profile"),
        "yt": ("yt_tasks", "bootstrap_profile"),
        "zhihu": ("zhihu_tasks", "bootstrap_events"),
        "reddit": ("reddit_tasks", "bootstrap_events"),
        "linuxdo": ("linuxdo_tasks", "bootstrap_events"),
        "v2ex": ("v2ex_tasks", "bootstrap_profile"),
    }

    def make_enqueue(source: str) -> Any:
        def enqueue(
            db: _FakeDatabase,
            *,
            force: bool,
            incremental: bool,
            **kwargs: Any,
        ) -> BootstrapEnqueueResult:
            assert force is True
            assert incremental is True
            enqueue_calls.append(source)
            if kwargs:
                enqueue_kwargs[source] = kwargs
            counters[source] += 1
            task_id = f"{source}-{counters[source]}"
            table, task_type = table_types[source]
            db.conn.execute(
                f"INSERT INTO {table} (id, type, payload_json, status, created_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (
                    task_id,
                    task_type,
                    '{"incremental": true}',
                    clock.value.isoformat(),
                ),
            )
            db.conn.commit()
            return BootstrapEnqueueResult(task_id=task_id, created=True, reason="created")

        return enqueue

    enqueue_kwargs: dict[str, dict[str, Any]] = {}
    specs = {
        source: (table_types[source][0], table_types[source][1], make_enqueue(source))
        for source in SOURCE_ORDER
    }
    monkeypatch.setattr(scheduler_module, "_TASK_SPECS", specs)

    async def kick(source: str) -> None:
        kicks.append(source)

    scheduler = SourceIncrementalSync(
        database=database,
        memory_manager=memory,
        presence=presence,
        source_enabled=enabled or {source: True for source in SOURCE_ORDER},
        scheduler_config=config or SchedulerConfig(source_incremental_enabled=True),
        profile_ready=lambda: True,
        init_active=lambda: False,
        runtime_config=runtime_config,
        kick=kick,
        clock=clock,
    )
    scheduler._test_enqueue_calls = enqueue_calls  # type: ignore[attr-defined]
    scheduler._test_enqueue_kwargs = enqueue_kwargs  # type: ignore[attr-defined]
    return scheduler, database, memory, presence, clock, kicks


def _complete(database: _FakeDatabase, source: str, task_id: str) -> None:
    table = {
        "xhs": "xhs_tasks",
        "dy": "dy_tasks",
        "yt": "yt_tasks",
        "zhihu": "zhihu_tasks",
        "reddit": "reddit_tasks",
        "linuxdo": "linuxdo_tasks",
        "v2ex": "v2ex_tasks",
    }[source]
    database.conn.execute(f"UPDATE {table} SET status = 'completed' WHERE id = ?", (task_id,))
    database.conn.commit()


def test_v2ex_incremental_four_table_registry_is_complete() -> None:
    assert "v2ex" in SOURCE_ORDER
    assert scheduler_module._TASK_SPECS["v2ex"][:2] == (
        "v2ex_tasks",
        "bootstrap_profile",
    )


@pytest.mark.asyncio
async def test_v2ex_incremental_requires_private_capability_and_passes_resolved_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v2ex_config = SimpleNamespace(bootstrap_topics_limit=17, incremental_enabled=True)
    runtime_config = SimpleNamespace(sources=SimpleNamespace(v2ex=v2ex_config))
    scheduler, _database, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        enabled={source: source == "v2ex" for source in SOURCE_ORDER},
        runtime_config=runtime_config,
    )
    monkeypatch.setattr(
        "openbiliclaw.sources.v2ex_identity.resolve_v2ex_identity_state",
        lambda **_kwargs: SimpleNamespace(
            private_bootstrap_available=False,
            username="alice",
        ),
    )

    blocked = await scheduler.tick()

    assert blocked.reason == "capability_not_ready"
    assert blocked.source == "v2ex"
    assert scheduler._test_enqueue_calls == []  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "openbiliclaw.sources.v2ex_identity.resolve_v2ex_identity_state",
        lambda **_kwargs: SimpleNamespace(
            private_bootstrap_available=True,
            username="alice",
        ),
    )

    created = await scheduler.tick()

    assert created.reason == "created"
    assert created.source == "v2ex"
    assert scheduler._test_enqueue_kwargs["v2ex"] == {  # type: ignore[attr-defined]
        "incremental_owner": "scheduler",
        "username": "alice",
        "config": v2ex_config,
    }
    assert scheduler_module._SOURCE_CONFIG_ALIASES["v2ex"] == ("v2ex",)
    assert scheduler_module._SOURCE_INTERVAL_FIELDS["v2ex"] == "v2ex_incremental_hours"


@pytest.mark.asyncio
async def test_due_tick_creates_one_task_and_not_due_reconciles_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, memory, _presence, clock, kicks = _harness(monkeypatch)

    first = await scheduler.tick()
    assert (first.reason, first.source, first.created) == ("created", "xhs", True)
    assert kicks == ["xhs"]
    assert memory.state["source_incremental"]["last_attempt_at"]["xhs"]  # type: ignore[index]

    _complete(database, "xhs", "xhs-1")
    scheduler.source_enabled = {source: source == "xhs" for source in SOURCE_ORDER}
    second = await scheduler.tick()
    assert second.reason == "not_due"

    clock.advance(hours=24)
    third = await scheduler.tick()
    assert third.source == "xhs"


@pytest.mark.asyncio
async def test_per_source_incremental_enabled_gates_due_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config = SimpleNamespace(
        sources=SimpleNamespace(
            xiaohongshu=SimpleNamespace(incremental_enabled=True),
            douyin=SimpleNamespace(incremental_enabled=False),
            youtube=SimpleNamespace(incremental_enabled=False),
            zhihu=SimpleNamespace(incremental_enabled=False),
            reddit=SimpleNamespace(incremental_enabled=False),
            linuxdo=SimpleNamespace(incremental_enabled=False),
            v2ex=SimpleNamespace(incremental_enabled=False),
        )
    )
    scheduler, _database, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        runtime_config=runtime_config,
    )

    result = await scheduler.tick()

    assert result.reason == "created"
    assert result.source == "xhs"
    assert scheduler._test_enqueue_calls == ["xhs"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_all_per_source_incremental_disabled_returns_not_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config = SimpleNamespace(
        sources=SimpleNamespace(
            xiaohongshu=SimpleNamespace(incremental_enabled=False),
            douyin=SimpleNamespace(incremental_enabled=False),
            youtube=SimpleNamespace(incremental_enabled=False),
            zhihu=SimpleNamespace(incremental_enabled=False),
            reddit=SimpleNamespace(incremental_enabled=False),
            linuxdo=SimpleNamespace(incremental_enabled=False),
            v2ex=SimpleNamespace(incremental_enabled=False),
        )
    )
    scheduler, _database, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        runtime_config=runtime_config,
    )

    result = await scheduler.tick()

    assert result.reason == "not_due"
    assert scheduler._test_enqueue_calls == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_disabled_per_source_cancels_pending_scheduler_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_config = SimpleNamespace(
        sources=SimpleNamespace(
            xiaohongshu=SimpleNamespace(incremental_enabled=False),
            douyin=SimpleNamespace(incremental_enabled=False),
            youtube=SimpleNamespace(incremental_enabled=False),
            zhihu=SimpleNamespace(incremental_enabled=False),
            reddit=SimpleNamespace(incremental_enabled=False),
            linuxdo=SimpleNamespace(incremental_enabled=False),
            v2ex=SimpleNamespace(incremental_enabled=False),
        )
    )
    scheduler, database, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        runtime_config=runtime_config,
    )
    database.conn.execute(
        "INSERT INTO xhs_tasks "
        "(id, type, payload_json, status, created_at) "
        "VALUES ('scheduled-xhs', 'bootstrap_profile', "
        '\'{"incremental": true, "incremental_owner": "scheduler"}\', '
        "'pending', '2026-08-10T00:00:00+00:00')"
    )
    database.conn.commit()

    result = await scheduler.tick()

    assert result.reason == "not_due"
    row = database.conn.execute(
        "SELECT status, result_json FROM xhs_tasks WHERE id = 'scheduled-xhs'"
    ).fetchone()
    assert row["status"] == "failed"
    assert "source_incremental_disabled" in row["result_json"]


@pytest.mark.asyncio
async def test_source_incremental_sync_is_globally_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, memory, presence, _clock, _kicks = _harness(
        monkeypatch,
        config=SchedulerConfig(),
    )
    database.conn.execute(
        "INSERT INTO xhs_tasks "
        "(id, type, payload_json, status, created_at) "
        "VALUES ('scheduled-xhs', 'bootstrap_profile', "
        '\'{"incremental": true, "incremental_owner": "scheduler"}\', '
        "'pending', '2026-08-10T00:00:00+00:00')"
    )
    database.conn.execute(
        "INSERT INTO dy_tasks "
        "(id, type, payload_json, status, created_at) "
        "VALUES ('legacy-scheduled-dy', 'bootstrap_profile', "
        "'{\"incremental\": true}', 'pending', '2026-08-10T00:00:00+00:00')"
    )
    memory.state["source_incremental"]["active_task"] = {  # type: ignore[index]
        "source": "dy",
        "task_id": "legacy-scheduled-dy",
    }
    database.conn.execute(
        "INSERT INTO reddit_tasks "
        "(id, type, payload_json, status, created_at) "
        "VALUES ('manual-reddit', 'bootstrap_events', "
        "'{\"incremental\": true}', "
        "'pending', '2026-08-10T00:00:01+00:00')"
    )
    database.conn.commit()

    result = await scheduler.tick()

    assert result.reason == "source_incremental_disabled"
    assert presence.calls == []
    assert scheduler._test_enqueue_calls == []  # type: ignore[attr-defined]
    assert (
        database.conn.execute("SELECT status FROM xhs_tasks WHERE id = 'scheduled-xhs'").fetchone()[
            "status"
        ]
        == "failed"
    )
    assert (
        database.conn.execute(
            "SELECT status FROM dy_tasks WHERE id = 'legacy-scheduled-dy'"
        ).fetchone()["status"]
        == "failed"
    )
    assert (
        database.conn.execute(
            "SELECT status FROM reddit_tasks WHERE id = 'manual-reddit'"
        ).fetchone()["status"]
        == "pending"
    )


@pytest.mark.asyncio
async def test_global_zero_and_per_source_zero_disable_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_off = SchedulerConfig(source_incremental_enabled=True, source_incremental_hours=0)
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(monkeypatch, config=global_off)
    assert (await scheduler.tick()).reason == "not_due"

    # Explicit None still exercises shared inheritance; Douyin's production
    # default is 0 so it never foregrounds an account task by surprise.
    per_source_off = SchedulerConfig(
        source_incremental_enabled=True,
        xhs_incremental_hours=0,
        douyin_incremental_hours=None,
    )
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch, config=per_source_off
    )
    result = await scheduler.tick()
    assert result.source == "dy"


@pytest.mark.asyncio
async def test_douyin_incremental_sync_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        config=SchedulerConfig(source_incremental_enabled=True),
        enabled={source: source == "dy" for source in SOURCE_ORDER},
    )

    assert (await scheduler.tick()).reason == "not_due"


@pytest.mark.asyncio
async def test_douyin_incremental_sync_can_be_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        config=SchedulerConfig(
            source_incremental_enabled=True,
            douyin_incremental_hours=24,
        ),
        enabled={source: source == "dy" for source in SOURCE_ORDER},
    )

    result = await scheduler.tick()
    assert (result.reason, result.source, result.created) == ("created", "dy", True)


@pytest.mark.asyncio
async def test_per_source_override_controls_actual_due_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SchedulerConfig(
        source_incremental_enabled=True,
        source_incremental_hours=24,
        xhs_incremental_hours=2,
    )
    scheduler, database, _memory, _presence, clock, _kicks = _harness(
        monkeypatch,
        config=config,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )

    first = await scheduler.tick()
    _complete(database, "xhs", str(first.task_id))
    clock.advance(hours=1)
    assert (await scheduler.tick()).reason == "not_due"

    clock.advance(hours=1)
    assert (await scheduler.tick()).source == "xhs"


@pytest.mark.asyncio
async def test_linuxdo_interval_override_controls_actual_due_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table, task_type, enqueue = scheduler_module._TASK_SPECS["linuxdo"]
    assert (table, task_type) == ("linuxdo_tasks", "bootstrap_events")
    assert callable(enqueue)
    config = SchedulerConfig(
        source_incremental_enabled=True,
        source_incremental_hours=24,
        linuxdo_incremental_hours=2,
    )
    scheduler, database, _memory, _presence, clock, _kicks = _harness(
        monkeypatch,
        config=config,
        enabled={source: source == "linuxdo" for source in SOURCE_ORDER},
    )

    first = await scheduler.tick()
    assert first.source == "linuxdo"
    _complete(database, "linuxdo", str(first.task_id))
    clock.advance(hours=1)
    assert (await scheduler.tick()).reason == "not_due"

    clock.advance(hours=1)
    assert (await scheduler.tick()).source == "linuxdo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row_status", "terminal_status"),
    [("failed", "failed"), ("completed", "degraded")],
)
async def test_linuxdo_failed_or_degraded_incremental_stays_immediately_due(
    monkeypatch: pytest.MonkeyPatch,
    row_status: str,
    terminal_status: str,
) -> None:
    scheduler, database, memory, _presence, clock, _kicks = _harness(
        monkeypatch,
        enabled={source: source == "linuxdo" for source in SOURCE_ORDER},
    )

    first = await scheduler.tick()
    assert (first.source, first.task_id) == ("linuxdo", "linuxdo-1")
    database.conn.execute(
        "UPDATE linuxdo_tasks SET status = ?, completed_at = ?, result_json = ? WHERE id = ?",
        (
            row_status,
            clock.value.isoformat(),
            json.dumps({"_openbiliclaw_terminal_status": terminal_status}),
            first.task_id,
        ),
    )
    database.conn.commit()

    retried = await scheduler.tick()

    assert (retried.source, retried.task_id, retried.created) == (
        "linuxdo",
        "linuxdo-2",
        True,
    )
    incremental = memory.state["source_incremental"]
    assert "linuxdo" not in incremental.get("last_success_at", {})  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_linuxdo_incremental_four_table_flow_is_durable_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task -> event -> seen -> schedule is one executable source contract."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from openbiliclaw.api.app import create_app
    from openbiliclaw.config import Config
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.storage.database import Database

    table, task_type, enqueue = scheduler_module._TASK_SPECS["linuxdo"]
    assert (table, task_type) == ("linuxdo_tasks", "bootstrap_events")
    assert callable(enqueue)

    config = Config(data_dir=str(tmp_path / "runtime"))
    config.sources.linuxdo.enabled = True
    config.scheduler.source_incremental_enabled = True
    config.scheduler.source_incremental_hours = 24
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda *_a, **_kw: config)

    database = Database(tmp_path / "incremental.db")
    database.initialize()
    memory = MemoryManager(tmp_path / "runtime", database=database)
    memory.initialize()
    clock = _FakeClock()

    async def kick(_source: str) -> None:
        return None

    scheduler = SourceIncrementalSync(
        database=database,
        memory_manager=memory,
        presence=_FakePresence(),
        source_enabled={source: source == "linuxdo" for source in SOURCE_ORDER},
        scheduler_config=config.scheduler,
        profile_ready=lambda: True,
        init_active=lambda: False,
        kick=kick,
        clock=clock,
    )

    class _ReadySoul:
        def is_profile_ready(self) -> bool:
            return True

    app = create_app(
        database=database,
        memory_manager=memory,
        soul_engine=_ReadySoul(),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    app.state.runtime_context.config = config

    async def complete_one_cycle(client: TestClient, expected_task_id: str) -> None:
        claimed = client.get("/api/sources/linuxdo/next-task")
        assert claimed.status_code == 200
        claim = claimed.json()
        assert claim["id"] == expected_task_id
        response = client.post(
            "/api/sources/linuxdo/task-result",
            json={
                "task_id": expected_task_id,
                "claim_token": claim["claim_token"],
                "status": "ok",
                "account_key": "sha256:" + "c" * 64,
                "response_observed": True,
                "complete_scopes": [
                    "linuxdo_bookmarks",
                    "linuxdo_likes",
                    "linuxdo_read_history",
                ],
                "items": [
                    {
                        "scope": "linuxdo_bookmarks",
                        "content_type": "post",
                        "topic_id": 4242,
                        "content_id": "topic:4242",
                        "title": "durable incremental topic",
                    }
                ],
                "scope_counts": {"linuxdo_bookmarks": 1},
            },
        )
        assert response.status_code == 200, response.text

    with TestClient(app) as client:
        first = await scheduler.tick()
        assert (first.source, first.created) == ("linuxdo", True)
        await complete_one_cycle(client, str(first.task_id))

        assert (
            database.conn.execute(
                "SELECT status FROM linuxdo_tasks WHERE id = ?", (first.task_id,)
            ).fetchone()["status"]
            == "completed"
        )
        assert database.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert (
            database.conn.execute(
                "SELECT COUNT(*) FROM seen_items WHERE source_platform = 'linuxdo'"
            ).fetchone()[0]
            == 1
        )

        settled = await scheduler.tick()
        assert settled.reason == "not_due"
        state = memory.load_source_bootstrap_state()["source_incremental"]
        assert state["last_success_at"]["linuxdo"]
        assert state["active_task"] is None

        clock.advance(hours=24)
        second = await scheduler.tick()
        assert (second.source, second.created) == ("linuxdo", True)
        await complete_one_cycle(client, str(second.task_id))
        assert (await scheduler.tick()).reason == "not_due"

    assert database.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert (
        database.conn.execute(
            "SELECT COUNT(*) FROM seen_items WHERE source_platform = 'linuxdo'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_enabled_presence_profile_and_init_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = SchedulerConfig(enabled=False, source_incremental_enabled=True)
    scheduler, _db, _memory, presence, _clock, _kicks = _harness(monkeypatch, config=disabled)
    assert (await scheduler.tick()).reason == "scheduler_disabled"
    assert presence.calls == []

    scheduler, _db, _memory, presence, _clock, _kicks = _harness(
        monkeypatch, enabled={source: False for source in SOURCE_ORDER}
    )
    assert (await scheduler.tick()).reason == "not_due"

    presence.present = False
    scheduler.source_enabled = {source: True for source in SOURCE_ORDER}
    assert (await scheduler.tick()).reason == "extension_absent"
    assert presence.calls[-1] == 90

    presence.present = True
    scheduler.profile_ready = lambda: False
    assert (await scheduler.tick()).reason == "profile_not_ready"

    scheduler.profile_ready = lambda: True
    scheduler.init_active = lambda: True
    assert (await scheduler.tick()).reason == "init_active"


@pytest.mark.asyncio
async def test_round_robin_fairness_uses_persisted_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler, database, _memory, _presence, clock, _kicks = _harness(
        monkeypatch,
        config=SchedulerConfig(
            source_incremental_enabled=True,
            douyin_incremental_hours=24,
        ),
    )
    seen: list[str] = []
    for _ in SOURCE_ORDER:
        result = await scheduler.tick()
        assert result.created is True
        seen.append(result.source)
        _complete(database, result.source, str(result.task_id))
        clock.advance(hours=24)
    assert seen == list(SOURCE_ORDER)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created", "task_id", "reason"),
    [
        (False, "reused-id", "reused_recent"),
        (False, None, "enqueue_failed"),
        (True, "", "created"),
    ],
)
async def test_reused_or_none_outcome_does_not_stamp_schedule_state(
    monkeypatch: pytest.MonkeyPatch,
    created: bool,
    task_id: str | None,
    reason: str,
) -> None:
    scheduler, _db, memory, _presence, _clock, kicks = _harness(
        monkeypatch,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )
    original = deepcopy(memory.state)

    def outcome(
        _db: Any,
        *,
        force: bool,
        incremental: bool,
        incremental_owner: str,
    ) -> BootstrapEnqueueResult:
        assert force and incremental
        assert incremental_owner == "scheduler"
        return BootstrapEnqueueResult(task_id=task_id, created=created, reason=reason)

    table, task_type, _ = scheduler_module._TASK_SPECS["xhs"]
    monkeypatch.setattr(
        scheduler_module,
        "_TASK_SPECS",
        {**scheduler_module._TASK_SPECS, "xhs": (table, task_type, outcome)},
    )
    result = await scheduler.tick()
    assert result.reason == reason
    assert memory.state == original
    assert kicks == []


@pytest.mark.asyncio
async def test_exhausted_source_budget_does_not_starve_next_due_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, memory, _presence, clock, kicks = _harness(
        monkeypatch,
        config=SchedulerConfig(
            source_incremental_enabled=True,
            douyin_incremental_hours=24,
        ),
    )
    table, task_type, _ = scheduler_module._TASK_SPECS["xhs"]

    def exhausted(
        _db: Any,
        *,
        force: bool,
        incremental: bool,
        incremental_owner: str,
    ) -> BootstrapEnqueueResult:
        assert force and incremental
        assert incremental_owner == "scheduler"
        return BootstrapEnqueueResult(None, False, "enqueue_failed")

    monkeypatch.setattr(
        scheduler_module,
        "_TASK_SPECS",
        {**scheduler_module._TASK_SPECS, "xhs": (table, task_type, exhausted)},
    )

    result = await scheduler.tick()

    assert (result.reason, result.source, result.created) == ("created", "dy", True)
    assert memory.state["source_incremental"]["last_attempt_at"].keys() == {"dy"}  # type: ignore[index,union-attr]
    assert database.conn.execute("SELECT COUNT(*) FROM xhs_tasks").fetchone()[0] == 0
    assert database.conn.execute("SELECT COUNT(*) FROM dy_tasks").fetchone()[0] == 1
    assert kicks == ["dy"]


@pytest.mark.asyncio
async def test_enqueue_exception_does_not_stamp_schedule_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, _db, memory, _presence, _clock, kicks = _harness(monkeypatch)
    original = deepcopy(memory.state)

    def enqueue(
        _db: Any,
        *,
        force: bool,
        incremental: bool,
        incremental_owner: str,
    ) -> BootstrapEnqueueResult:
        assert force and incremental
        assert incremental_owner == "scheduler"
        raise RuntimeError("database unavailable")

    table, task_type, _ = scheduler_module._TASK_SPECS["xhs"]
    monkeypatch.setattr(
        scheduler_module,
        "_TASK_SPECS",
        {**scheduler_module._TASK_SPECS, "xhs": (table, task_type, enqueue)},
    )

    result = await scheduler.tick()

    assert result.reason == "enqueue_error"
    assert memory.state == original
    assert kicks == []


@pytest.mark.asyncio
async def test_one_pending_bootstrap_in_any_source_table_blocks_new_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for source in SOURCE_ORDER:
        scheduler, database, memory, _presence, _clock, kicks = _harness(monkeypatch)
        table, task_type, _ = scheduler_module._TASK_SPECS[source]
        database.conn.execute(
            f"INSERT INTO {table} (id, type, payload_json, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (f"manual-{source}", task_type, "{}", "2026-08-03T00:00:00+00:00"),
        )
        database.conn.commit()
        result = await scheduler.tick()
        assert result.reason == "active_task"
        assert result.source == source
        assert memory.state["source_incremental"]["active_task"] is None  # type: ignore[index]
        assert kicks == []


@pytest.mark.asyncio
async def test_disabled_source_pending_task_does_not_block_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, memory, _presence, _clock, kicks = _harness(
        monkeypatch,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )
    database.conn.execute(
        "INSERT INTO zhihu_tasks (id, type, payload_json, status, created_at) "
        "VALUES ('disabled-zhihu', 'bootstrap_events', '{}', 'pending', '2026-08-03')"
    )
    database.conn.commit()

    result = await scheduler.tick()

    assert (result.reason, result.source) == ("created", "xhs")
    assert memory.state["source_incremental"]["active_task"]["source"] == "xhs"  # type: ignore[index]
    assert kicks == ["xhs"]


@pytest.mark.asyncio
async def test_disabled_source_recorded_active_task_is_cleared_before_next_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = default_source_bootstrap_state()
    state["source_incremental"] = {
        "cursor": "",
        "last_attempt_at": {},
        "active_task": {"source": "zhihu", "task_id": "stale-zhihu"},
    }
    scheduler, database, memory, _presence, _clock, kicks = _harness(
        monkeypatch,
        state=state,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )
    database.conn.execute(
        "INSERT INTO zhihu_tasks (id, type, payload_json, status, created_at) "
        "VALUES ('stale-zhihu', 'bootstrap_events', "
        "'{\"incremental\": true}', 'in_progress', '2026-08-03')"
    )
    database.conn.commit()

    result = await scheduler.tick()

    assert (result.reason, result.source) == ("created", "xhs")
    assert memory.state["source_incremental"]["active_task"]["source"] == "xhs"  # type: ignore[index]
    assert kicks == ["xhs"]


@pytest.mark.asyncio
async def test_concurrent_ticks_on_one_scheduler_create_only_one_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, _memory, _presence, _clock, _kicks = _harness(monkeypatch)
    results = await asyncio.gather(scheduler.tick(), scheduler.tick())

    assert sum(result.created for result in results) == 1
    assert database.conn.execute("SELECT COUNT(*) FROM xhs_tasks").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_hot_reload_scheduler_instances_share_one_decision_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, database, memory, presence, clock, kicks = _harness(monkeypatch)
    second = SourceIncrementalSync(
        database=database,
        memory_manager=memory,
        presence=presence,
        source_enabled=first.source_enabled,
        scheduler_config=first.scheduler_config,
        profile_ready=lambda: True,
        init_active=lambda: False,
        kick=first.kick,
        clock=clock,
    )
    first_scanned = threading.Event()
    release_first = threading.Event()
    original_reconcile = first._reconcile_active_state

    def pause_after_empty_scan(
        state: dict[str, object], *, now: datetime
    ) -> SourceIncrementalSyncResult | None:
        result = original_reconcile(state, now=now)
        assert result is None
        first_scanned.set()
        assert release_first.wait(timeout=2)
        return result

    monkeypatch.setattr(first, "_reconcile_active_state", pause_after_empty_scan)
    first_tick = asyncio.create_task(first.tick())
    assert await asyncio.to_thread(first_scanned.wait, 2)
    second_tick = asyncio.create_task(second.tick())
    await asyncio.sleep(0.05)
    release_first.set()
    results = await asyncio.gather(first_tick, second_tick)

    assert sum(result.created for result in results) == 1
    assert {result.reason for result in results} == {"created", "active_task"}
    assert database.conn.execute("SELECT COUNT(*) FROM xhs_tasks").fetchone()[0] == 1
    assert kicks == ["xhs"]


@pytest.mark.asyncio
async def test_terminal_active_state_is_cleared_before_next_due_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = default_source_bootstrap_state()
    state["source_incremental"] = {
        "cursor": "",
        "last_attempt_at": {},
        "active_task": {"source": "xhs", "task_id": "done"},
    }
    scheduler, database, memory, _presence, _clock, _kicks = _harness(monkeypatch, state=state)
    database.conn.execute(
        "INSERT INTO xhs_tasks (id, type, payload_json, status, created_at) "
        "VALUES ('done', 'bootstrap_profile', '{}', 'completed', '2026-08-03')"
    )
    database.conn.commit()

    result = await scheduler.tick()
    assert result.created is True
    assert memory.state["source_incremental"]["active_task"]["task_id"] == "xhs-1"  # type: ignore[index]


@pytest.mark.asyncio
async def test_crash_window_adopts_unrecorded_incremental_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, database, memory, _presence, clock, kicks = _harness(monkeypatch)
    database.conn.execute(
        "INSERT INTO xhs_tasks (id, type, payload_json, status, created_at) "
        "VALUES ('crashed', 'bootstrap_profile', "
        "'{\"incremental\": true}', 'in_progress', '2026-08-03')"
    )
    database.conn.commit()

    result = await scheduler.tick()
    assert (result.reason, result.task_id) == ("active_task", "crashed")
    assert memory.state["source_incremental"]["active_task"] == {  # type: ignore[index]
        "source": "xhs",
        "task_id": "crashed",
    }
    assert memory.state["source_incremental"]["cursor"] == "xhs"  # type: ignore[index]
    assert (
        memory.state["source_incremental"]["last_attempt_at"]["xhs"]  # type: ignore[index]
        == "2026-08-03T00:00:00+00:00"
    )
    assert kicks == []

    scheduler.source_enabled = {source: source == "xhs" for source in SOURCE_ORDER}
    _complete(database, "xhs", "crashed")
    assert (await scheduler.tick()).reason == "not_due"

    clock.advance(hours=24)
    assert (await scheduler.tick()).source == "xhs"


@pytest.mark.asyncio
async def test_corrupt_state_and_timestamp_are_safe_and_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "source_incremental": {
            "cursor": "not-a-source",
            "last_attempt_at": {"xhs": "not-a-timestamp"},
            "active_task": "corrupt",
        }
    }
    scheduler, _db, memory, _presence, _clock, _kicks = _harness(monkeypatch, state=state)

    result = await scheduler.tick()
    assert result.created is True
    assert memory.state["source_incremental"]["cursor"] == "xhs"  # type: ignore[index]


@pytest.mark.asyncio
async def test_future_timestamp_is_treated_as_corrupt_and_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = default_source_bootstrap_state()
    state["source_incremental"] = {
        "cursor": "reddit",
        "last_attempt_at": {"xhs": "2099-01-01T00:00:00+00:00"},
        "active_task": None,
    }
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        state=state,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )

    assert (await scheduler.tick()).source == "xhs"


@pytest.mark.asyncio
async def test_naive_recent_timestamp_is_treated_as_corrupt_and_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = default_source_bootstrap_state()
    state["source_incremental"] = {
        "cursor": "reddit",
        "last_attempt_at": {"xhs": "2026-08-03T00:00:00"},
        "active_task": None,
    }
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(
        monkeypatch,
        state=state,
        enabled={source: source == "xhs" for source in SOURCE_ORDER},
    )

    assert (await scheduler.tick()).source == "xhs"


@pytest.mark.asyncio
async def test_kick_only_runs_for_created_task_and_failure_keeps_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, _db, memory, _presence, _clock, kicks = _harness(monkeypatch)
    assert (await scheduler.tick()).created is True
    assert kicks == ["xhs"]

    scheduler2, _db2, memory2, _presence2, _clock2, _kicks2 = _harness(monkeypatch)

    async def failing_kick(_source: str) -> None:
        raise RuntimeError("stream offline")

    scheduler2.kick = failing_kick
    result = await scheduler2.tick()
    assert result.reason == "created_kick_failed"
    assert result.created is True
    assert memory2.state["source_incremental"]["active_task"]["task_id"] == "xhs-1"  # type: ignore[index]


@pytest.mark.asyncio
async def test_database_enqueue_runs_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler, _db, _memory, _presence, _clock, _kicks = _harness(monkeypatch)
    event_thread = threading.get_ident()
    worker_threads: list[int] = []
    table, task_type, _ = scheduler_module._TASK_SPECS["xhs"]

    def enqueue(
        database: _FakeDatabase,
        *,
        force: bool,
        incremental: bool,
        incremental_owner: str,
    ) -> BootstrapEnqueueResult:
        assert force and incremental and table and task_type
        assert incremental_owner == "scheduler"
        worker_threads.append(threading.get_ident())
        database.conn.execute(
            "INSERT INTO xhs_tasks (id, type, payload_json, status, created_at) "
            "VALUES ('threaded', 'bootstrap_profile', '{\"incremental\": true}', 'pending', 'now')"
        )
        database.conn.commit()
        return BootstrapEnqueueResult("threaded", True, "created")

    monkeypatch.setattr(
        scheduler_module,
        "_TASK_SPECS",
        {**scheduler_module._TASK_SPECS, "xhs": ("xhs_tasks", "bootstrap_profile", enqueue)},
    )
    await scheduler.tick()
    assert worker_threads and worker_threads[0] != event_thread


def test_scheduler_has_no_cli_or_localhost_http_dependency() -> None:
    import inspect

    source = inspect.getsource(scheduler_module)
    assert "openbiliclaw.cli" not in source
    assert "localhost" not in source
    assert "httpx" not in source
