"""Crash-recovery and immutable-first-final regressions for source tasks."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.sources.dy_tasks import DyTaskQueue
from openbiliclaw.sources.linuxdo_tasks import LinuxdoTaskQueue
from openbiliclaw.sources.reddit_tasks import RedditTaskQueue
from openbiliclaw.sources.task_result_protocol import staged_terminal_status
from openbiliclaw.sources.xhs_tasks import XhsTaskQueue
from openbiliclaw.sources.yt_tasks import YtTaskQueue
from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


SOURCE_CASES: tuple[dict[str, Any], ...] = (
    {
        "source": "xhs",
        "table": "xhs_tasks",
        "queue": XhsTaskQueue,
        "task_type": "bootstrap_profile",
        "task_payload": {"scopes": ["saved"]},
        "field": "notes",
        "item": {
            "scope": "saved",
            "title": "ORIGINAL xhs",
            "url": "https://www.xiaohongshu.com/explore/source-protocol-xhs",
            "note_id": "source-protocol-xhs",
        },
        "state_key": "xhs_seen_note_keys",
        "bootstrap_key": "saved:source-protocol-xhs",
    },
    {
        "source": "dy",
        "table": "dy_tasks",
        "queue": DyTaskQueue,
        "task_type": "bootstrap_profile",
        "task_payload": {"scopes": ["dy_like"]},
        "field": "videos",
        "item": {
            "scope": "dy_like",
            "title": "ORIGINAL dy",
            "url": "https://www.douyin.com/video/source-protocol-dy",
            "aweme_id": "source-protocol-dy",
        },
        "state_key": "dy_seen_video_keys",
        "bootstrap_key": "dy_like:source-protocol-dy",
    },
    {
        "source": "yt",
        "table": "yt_tasks",
        "queue": YtTaskQueue,
        "task_type": "bootstrap_profile",
        "task_payload": {"scopes": ["yt_history"]},
        "field": "items",
        "item": {
            "scope": "yt_history",
            "title": "ORIGINAL yt",
            "url": "https://www.youtube.com/watch?v=source-protocol-yt",
            "video_id": "source-protocol-yt",
        },
        "state_key": "yt_seen_item_keys",
        "bootstrap_key": "yt_history:source-protocol-yt",
    },
    {
        "source": "zhihu",
        "table": "zhihu_tasks",
        "queue": ZhihuTaskQueue,
        "task_type": "bootstrap_events",
        "task_payload": {
            "scopes": ["zhihu_read_history"],
            "profile_update": True,
        },
        "field": "items",
        "item": {
            "scope": "zhihu_read_history",
            "title": "ORIGINAL zhihu",
            "url": "https://www.zhihu.com/question/1/answer/source-protocol-zhihu",
            "content_type": "answer",
            "content_id": "source-protocol-zhihu",
        },
        "state_key": "zhihu_seen_item_keys",
        "bootstrap_key": "zhihu_read_history:answer:source-protocol-zhihu",
    },
    {
        "source": "reddit",
        "table": "reddit_tasks",
        "queue": RedditTaskQueue,
        "task_type": "bootstrap_events",
        "task_payload": {
            "scopes": ["reddit_saved", "reddit_upvoted", "reddit_subscribed"],
            "incremental": True,
        },
        "field": "items",
        "item": {
            "scope": "reddit_saved",
            "content_type": "post",
            "title": "ORIGINAL reddit",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/source-protocol-reddit/original/",
            "id": "source-protocol-reddit",
        },
        "state_key": "reddit_seen_item_keys",
        "bootstrap_key": "t3_source-protocol-reddit",
    },
    {
        "source": "linuxdo",
        "table": "linuxdo_tasks",
        "queue": LinuxdoTaskQueue,
        "task_type": "bootstrap_events",
        "task_payload": {
            "scopes": ["linuxdo_bookmarks"],
            "incremental": True,
        },
        "field": "items",
        "item": {
            "scope": "linuxdo_bookmarks",
            "content_type": "post",
            "title": "ORIGINAL linuxdo",
            "url": "https://linux.do/t/source-protocol/987654",
            "topic_id": 987654,
            "content_id": "topic:987654",
        },
        "state_key": "linuxdo_seen_item_keys",
        "bootstrap_key": ("sha256:" + "d" * 64 + ":linuxdo_bookmarks:topic:987654"),
    },
)


class _ReadySoul:
    def is_profile_ready(self) -> bool:
        return True


@pytest.fixture
def durable_source_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Database, MemoryManager]:
    database = Database(tmp_path / "tasks.db")
    database.initialize()
    memory = MemoryManager(tmp_path / "memory-data")
    memory.initialize()
    fake_config = SimpleNamespace(
        data_path=tmp_path,
        bilibili=SimpleNamespace(cookie="", proxy="", browser_executable="", browser_headed=False),
        sources=SimpleNamespace(
            browser_cdp_url="",
            browser_headed=False,
            xiaohongshu=SimpleNamespace(
                enabled=True,
                daily_search_budget=20,
                daily_creator_budget=10,
                task_interval_seconds=45,
            ),
            zhihu=SimpleNamespace(enabled=True),
            linuxdo=SimpleNamespace(enabled=True),
        ),
        scheduler=SimpleNamespace(
            enabled=True,
            pool_target_count=300,
            account_sync_interval_hours=24,
        ),
    )
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: fake_config)

    from openbiliclaw.api.app import create_app

    app = create_app(
        database=database,
        memory_manager=memory,
        soul_engine=_ReadySoul(),
        runtime_controller=SimpleNamespace(memory_manager=memory),
        recommendation_engine=None,
    )
    # Injection-mode RuntimeContext intentionally omits config. Source polling
    # reads the live context, so expose the fixture's enabled source policy.
    app.state.runtime_context.config = fake_config
    return TestClient(app, raise_server_exceptions=False), database, memory


def _enqueue(database: Database, case: dict[str, Any]) -> tuple[Any, str]:
    queue = case["queue"](database)
    task_id = queue.enqueue_with_id(
        case["task_type"],
        case["task_payload"],
        daily_budget=0,
    )
    assert task_id is not None
    return queue, task_id


def _callback(case: dict[str, Any], task_id: str, *, changed: bool = False) -> dict[str, Any]:
    item = dict(case["item"])
    if changed:
        item["title"] = f"CHANGED {case['source']}"
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": "failed" if changed else "ok",
        case["field"]: [item],
    }
    if case["source"] == "xhs":
        payload["self_info"] = {
            "user_id": "changed-user" if changed else "original-user",
            "nickname": "changed" if changed else "original",
        }
    if case["source"] == "linuxdo":
        payload.update(
            {
                "claim_token": case.get("_claim_tokens", {}).get(task_id, ""),
                "account_key": "sha256:" + "d" * 64,
                "scope_counts": {"linuxdo_bookmarks": 1},
                "response_observed": True,
                "complete_scopes": ["linuxdo_bookmarks"],
            }
        )
    return payload


def _claim(client: TestClient, case: dict[str, Any], task_id: str) -> None:
    claimed = client.get(f"/api/sources/{case['source']}/next-task")
    assert claimed.status_code == 200
    assert claimed.json()["id"] == task_id
    if case["source"] == "linuxdo":
        case.setdefault("_claim_tokens", {})[task_id] = claimed.json()["claim_token"]


def _expire_lease_and_reclaim(
    client: TestClient,
    database: Database,
    case: dict[str, Any],
    task_id: str,
) -> None:
    database.conn.execute(
        f"UPDATE {case['table']} SET claimed_at = ? WHERE id = ?",  # noqa: S608
        ("2000-01-01 00:00:00", task_id),
    )
    database.conn.commit()
    _claim(client, case, task_id)


@pytest.mark.parametrize("case", SOURCE_CASES, ids=lambda case: str(case["source"]))
def test_retry_repairs_crash_after_canonical_stage_before_event_ingress(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    client, database, memory = durable_source_app
    queue, task_id = _enqueue(database, case)
    real_persist = memory.persist_events_with_receipts
    attempts = 0

    async def fail_once(events: list[dict[str, Any]]) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("crash after canonical stage")
        return await real_persist(events)

    monkeypatch.setattr(memory, "persist_events_with_receipts", fail_once)
    endpoint = f"/api/sources/{case['source']}/task-result"
    _claim(client, case, task_id)

    first = client.post(endpoint, json=_callback(case, task_id))
    assert first.status_code == 500
    staged = queue.get(task_id)
    assert staged is not None
    assert staged["status"] not in {"completed", "failed"}
    staged_result = json.loads(str(staged["result_json"]))
    assert staged_terminal_status(staged_result) == "ok"
    assert memory.query_events(limit=20) == []

    # The original dispatcher does not retry a non-2xx result POST. Recovery
    # comes from the ordinary stale lease: a later poll reclaims this staged
    # row and its new callback repairs solely from the frozen canonical data.
    _expire_lease_and_reclaim(client, database, case, task_id)
    repaired = client.post(endpoint, json=_callback(case, task_id, changed=True))
    assert repaired.status_code == 200
    completed = queue.get(task_id)
    assert completed is not None
    assert completed["status"] == "completed"
    canonical = json.loads(str(completed["result_json"]))
    assert canonical[case["field"]][0]["title"] == case["item"]["title"]
    assert "CHANGED" not in json.dumps(canonical, ensure_ascii=False)
    if case["source"] == "xhs":
        assert canonical["debug"]["_source_self_info"]["user_id"] == "original-user"
    events = memory.query_events(limit=20)
    assert len(events) == 1
    assert events[0]["title"] == case["item"]["title"]
    assert case["bootstrap_key"] in memory.load_source_bootstrap_state()[case["state_key"]]

    ignored = client.post(endpoint, json=_callback(case, task_id, changed=True))
    assert ignored.status_code == 200
    assert ignored.json() == {"ok": True, "ignored": True}
    assert len(memory.query_events(limit=20)) == 1


@pytest.mark.parametrize(
    "case", (SOURCE_CASES[0], SOURCE_CASES[-1]), ids=lambda case: str(case["source"])
)
def test_retry_repairs_crash_after_ingress_before_strict_seen_key_update(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    client, database, memory = durable_source_app
    queue, task_id = _enqueue(database, case)
    real_update = memory.update_source_bootstrap_state
    attempts = 0

    def fail_once(
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("seen-key atomic update failed")
        return real_update(mutator)

    monkeypatch.setattr(memory, "update_source_bootstrap_state", fail_once)
    endpoint = f"/api/sources/{case['source']}/task-result"
    _claim(client, case, task_id)

    first = client.post(endpoint, json=_callback(case, task_id))
    assert first.status_code == 500
    assert len(memory.query_events(limit=20)) == 1
    assert memory.load_source_bootstrap_state()[case["state_key"]] == []

    staged = queue.get(task_id)
    assert staged is not None and staged["status"] not in {"completed", "failed"}

    _expire_lease_and_reclaim(client, database, case, task_id)
    repaired = client.post(endpoint, json=_callback(case, task_id, changed=True))
    assert repaired.status_code == 200
    assert queue.get(task_id)["status"] == "completed"
    assert len(memory.query_events(limit=20)) == 1
    assert memory.load_source_bootstrap_state()[case["state_key"]] == [case["bootstrap_key"]]


@pytest.mark.parametrize(
    "case", (SOURCE_CASES[0], SOURCE_CASES[-1]), ids=lambda case: str(case["source"])
)
def test_retry_repairs_crash_after_seen_key_before_terminal_flip(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
    monkeypatch: pytest.MonkeyPatch,
    case: dict[str, Any],
) -> None:
    client, database, memory = durable_source_app
    queue, task_id = _enqueue(database, case)
    queue_type = case["queue"]
    original_complete: Callable[..., bool] = queue_type.complete_staged_result
    attempts = 0

    def fail_once(self: Any, current_task_id: str, **kwargs: Any) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("crash before terminal flip")
        return original_complete(self, current_task_id, **kwargs)

    monkeypatch.setattr(queue_type, "complete_staged_result", fail_once)
    endpoint = f"/api/sources/{case['source']}/task-result"
    _claim(client, case, task_id)

    first = client.post(endpoint, json=_callback(case, task_id))
    assert first.status_code == 500
    assert len(memory.query_events(limit=20)) == 1

    staged = queue.get(task_id)
    assert staged is not None and staged["status"] not in {"completed", "failed"}

    _expire_lease_and_reclaim(client, database, case, task_id)
    repaired = client.post(endpoint, json=_callback(case, task_id, changed=True))
    assert repaired.status_code == 200
    assert queue.get(task_id)["status"] == "completed"
    assert len(memory.query_events(limit=20)) == 1


@pytest.mark.parametrize("flag", ("profile_update", "incremental"))
def test_reddit_bootstrap_filters_old_rows_and_repeat_cycle_adds_no_events(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
    flag: str,
) -> None:
    client, database, memory = durable_source_app
    case = dict(
        next(case for case in SOURCE_CASES if case["source"] == "reddit"),
        task_payload={flag: True},
    )
    old_item = dict(case["item"], id="old-reddit", title="OLD reddit")
    new_item = dict(case["item"], id="new-reddit", title="NEW reddit")
    state = memory.load_source_bootstrap_state()
    state[case["state_key"]] = ["t3_old-reddit"]
    memory.save_source_bootstrap_state(state)

    queue, first_task_id = _enqueue(database, case)
    endpoint = "/api/sources/reddit/task-result"
    _claim(client, case, first_task_id)
    first = client.post(
        endpoint,
        json={
            "task_id": first_task_id,
            "status": "ok",
            "items": [old_item, new_item],
        },
    )

    assert first.status_code == 200, first.text
    assert queue.get(first_task_id)["status"] == "completed"
    events = memory.query_events(limit=20)
    assert len(events) == 1
    assert events[0]["title"] == "NEW reddit"
    metadata = json.loads(str(events[0]["metadata"]))
    assert metadata["profile_update_owner"] == "generic"
    assert memory.load_source_bootstrap_state()[case["state_key"]] == [
        "t3_old-reddit",
        "t3_new-reddit",
    ]

    queue, repeat_task_id = _enqueue(database, case)
    _claim(client, case, repeat_task_id)
    repeated = client.post(
        endpoint,
        json={
            "task_id": repeat_task_id,
            "status": "ok",
            "items": [old_item, new_item],
        },
    )

    assert repeated.status_code == 200, repeated.text
    assert queue.get(repeat_task_id)["status"] == "completed"
    assert len(memory.query_events(limit=20)) == 1
    assert memory.load_source_bootstrap_state()[case["state_key"]] == [
        "t3_old-reddit",
        "t3_new-reddit",
    ]


def test_reddit_seen_checkpoint_preserves_canonical_result_order(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
) -> None:
    client, database, memory = durable_source_app
    case = next(case for case in SOURCE_CASES if case["source"] == "reddit")
    first_item = dict(case["item"], id="z-first", title="FIRST reddit")
    second_item = dict(case["item"], id="a-second", title="SECOND reddit")
    queue, task_id = _enqueue(database, case)
    _claim(client, case, task_id)

    response = client.post(
        "/api/sources/reddit/task-result",
        json={
            "task_id": task_id,
            "status": "ok",
            "items": [first_item, second_item],
        },
    )

    assert response.status_code == 200, response.text
    assert queue.get(task_id)["status"] == "completed"
    assert memory.load_source_bootstrap_state()[case["state_key"]] == [
        "t3_z-first",
        "t3_a-second",
    ]


def test_xhs_source_event_identity_ignores_rotating_url_and_title(
    durable_source_app: tuple[TestClient, Database, MemoryManager],
) -> None:
    client, database, memory = durable_source_app
    case = SOURCE_CASES[0]
    queue, first_task_id = _enqueue(database, case)
    first_item = {
        **case["item"],
        "title": "首写标题",
        "url": ("https://www.xiaohongshu.com/explore/source-protocol-xhs?xsec_token=old"),
    }
    endpoint = "/api/sources/xhs/task-result"
    _claim(client, case, first_task_id)

    first = client.post(
        endpoint,
        json={"task_id": first_task_id, "status": "ok", "notes": [first_item]},
    )

    assert first.status_code == 200
    assert queue.get(first_task_id)["status"] == "completed"
    assert len(memory.query_events(limit=20)) == 1

    # Simulate loss/corruption of the auxiliary seen-key projection. A later
    # source task must still dedupe at the append-only ingress identity even
    # when its signed URL and mutable title have both changed.
    state = memory.load_source_bootstrap_state()
    state[case["state_key"]] = []
    memory.save_source_bootstrap_state(state)
    database.conn.execute(
        """
        UPDATE xhs_task_runtime_state
        SET next_claim_at = '', cooldown_until = ''
        """
    )
    database.conn.commit()
    queue, replay_task_id = _enqueue(database, case)
    replay_item = {
        **case["item"],
        "title": "重渲染标题",
        "url": ("https://www.xiaohongshu.com/explore/source-protocol-xhs?xsec_token=new"),
    }
    _claim(client, case, replay_task_id)

    replay = client.post(
        endpoint,
        json={"task_id": replay_task_id, "status": "ok", "notes": [replay_item]},
    )

    assert replay.status_code == 200
    assert queue.get(replay_task_id)["status"] == "completed"
    events = memory.query_events(limit=20)
    assert len(events) == 1
    assert events[0]["title"] == "首写标题"
    assert events[0]["url"] == first_item["url"]
    assert memory.load_source_bootstrap_state()[case["state_key"]] == [case["bootstrap_key"]]


def _stage(queue: Any, case: dict[str, Any], task_id: str, title: str) -> dict[str, Any]:
    item = dict(case["item"], title=title)
    kwargs = {case["field"]: [item]}
    return queue.stage_final_result(task_id, terminal_status="ok", **kwargs)


def _late_merge(queue: Any, case: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    item = dict(case["item"], title="LATE PARTIAL")
    kwargs: dict[str, Any] = {case["field"]: [item], "complete": False}
    return queue.merge_result(task_id, **kwargs)


@pytest.mark.parametrize("case", SOURCE_CASES, ids=lambda case: str(case["source"]))
def test_concurrent_first_final_wins_and_all_late_queue_mutations_are_ignored(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    database = Database(tmp_path / f"{case['source']}.db")
    database.initialize()
    queue, task_id = _enqueue(database, case)
    barrier = Barrier(2)

    def contender(title: str) -> dict[str, Any]:
        barrier.wait()
        return _stage(queue, case, task_id, title)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(contender, ("FINAL A", "FINAL B")))

    assert results[0] == results[1]
    winner = results[0][case["field"]][0]["title"]
    assert winner in {"FINAL A", "FINAL B"}
    assert _late_merge(queue, case, task_id) == []
    assert queue.fail(task_id, error="late failure") is False
    reclaimed = queue.next_pending()
    assert reclaimed is not None and reclaimed["id"] == task_id
    if case["source"] == "xhs":
        queue.record_rate_limit(task_id, error="late rate limit", cooldown_seconds=1)
    row = queue.get(task_id)
    assert row is not None
    canonical = json.loads(str(row["result_json"]))
    assert canonical[case["field"]][0]["title"] == winner
    assert "LATE" not in json.dumps(canonical, ensure_ascii=False)
    assert row["status"] not in {"completed", "failed"}
    assert queue.complete_staged_result(task_id) is True
    assert queue.get(task_id)["status"] == "completed"
