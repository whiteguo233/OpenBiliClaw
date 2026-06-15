"""Tests for the Bilibili extension-search fallback producer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.bilibili_producer import BilibiliExtensionSearchProducer
from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
from openbiliclaw.sources.bili_tasks import BiliTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bili-producer.db")
    database.initialize()
    return database


@pytest.fixture
def queue(db: Database) -> BiliTaskQueue:
    return BiliTaskQueue(db)


class _Presence:
    def __init__(self, present: bool = True) -> None:
        self.present = present
        self.grace_calls: list[int] = []

    def is_present(self, grace_seconds: int) -> bool:
        self.grace_calls.append(grace_seconds)
        return self.present


class _BiliClient:
    def __init__(self, cooldown: float = 120.0) -> None:
        self.cooldown = cooldown

    def search_cooldown_remaining(self) -> float:
        return self.cooldown


_DEFAULT_PROFILE = object()


class _Soul:
    def __init__(self, profile: Any = _DEFAULT_PROFILE) -> None:
        self.profile = profile

    async def get_profile(self) -> Any:
        return self.profile


class _LLM:
    async def complete_structured_task(self, **_kwargs: Any) -> Any:
        raise AssertionError("LLM keyword generation should be monkeypatched in tests")


class _Pipeline:
    def __init__(self, full: bool = False) -> None:
        self.full = full

    def pool_full(self) -> bool:
        return self.full


@dataclass
class _DiscoveryCfg:
    unified_keyword_planner_enabled: bool = False
    fetch_batch: int = 5


def _task_payloads(db: Database) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT payload_json FROM bili_tasks WHERE type = 'search' ORDER BY created_at ASC, id ASC"
    ).fetchall()
    return [json.loads(str(row[0])) for row in rows]


def _kw_statuses(db: Database) -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT keyword, status FROM discovery_keywords WHERE platform = 'bilibili' ORDER BY id"
    ).fetchall()
    return {str(row["keyword"]): str(row["status"]) for row in rows}


@pytest.mark.asyncio
async def test_bilibili_producer_skips_when_disabled(queue: BiliTaskQueue) -> None:
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(),
        presence=_Presence(),
        enabled=False,
    )

    result = await producer.produce_if_due()

    assert result == {"enqueued": 0, "attempted": 0, "reason": "disabled"}
    assert queue.next_pending() is None


@pytest.mark.asyncio
async def test_bilibili_producer_skips_without_search_cooldown(queue: BiliTaskQueue) -> None:
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=0),
        presence=_Presence(),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "search_not_cooling"
    assert queue.next_pending() is None


@pytest.mark.asyncio
async def test_bilibili_producer_skips_when_extension_absent(queue: BiliTaskQueue) -> None:
    presence = _Presence(present=False)
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=presence,
        presence_grace_seconds=12,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "extension_absent"
    assert presence.grace_calls == [12]
    assert queue.next_pending() is None


@pytest.mark.asyncio
async def test_bilibili_producer_skips_when_candidate_pool_full(queue: BiliTaskQueue) -> None:
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        candidate_pipeline=_Pipeline(full=True),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "pool_full"
    assert queue.next_pending() is None


@pytest.mark.asyncio
async def test_bilibili_producer_enqueues_generated_keywords_and_kicks(
    db: Database,
    queue: BiliTaskQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_keywords(_llm: Any, _profile: Any, *, count: int) -> list[str]:
        return [f"kw-{i}" for i in range(count)]

    monkeypatch.setattr(
        "openbiliclaw.runtime.bilibili_producer.generate_bili_search_keywords",
        fake_keywords,
    )
    kicks = 0

    async def kick() -> None:
        nonlocal kicks
        kicks += 1

    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        kick=kick,
        keywords_per_cycle=5,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=2)

    assert result == {"enqueued": 2, "attempted": 2, "reason": "ok"}
    payloads = _task_payloads(db)
    assert {payload["query"] for payload in payloads} == {"kw-0", "kw-1"}
    assert all(payload["source"] == "bili-extension-search" for payload in payloads)
    assert all(payload["page_size"] == 20 for payload in payloads)
    assert kicks == 1


@pytest.mark.asyncio
async def test_bilibili_producer_flag_on_claims_keywords_and_marks_executing(
    db: Database,
    queue: BiliTaskQueue,
) -> None:
    db.insert_pending_keywords("bilibili", ["claim-a", "claim-b"], "dig")
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        keyword_fetch=KeywordFetchCoordinator(
            database=db,
            discovery_config=_DiscoveryCfg(True, fetch_batch=5),
        ),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "ok"
    assert result["enqueued"] == 2
    assert _kw_statuses(db) == {"claim-a": "executing", "claim-b": "executing"}
    payloads = _task_payloads(db)
    assert {payload["query"] for payload in payloads} == {"claim-a", "claim-b"}
    assert all(isinstance(payload["source_keyword_id"], int) for payload in payloads)


@pytest.mark.asyncio
async def test_bilibili_producer_flag_on_empty_store_skips(
    db: Database,
    queue: BiliTaskQueue,
) -> None:
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "no_keywords"
    assert _task_payloads(db) == []


@pytest.mark.asyncio
async def test_bilibili_producer_budget_rejection_rolls_back_claims(
    db: Database,
    queue: BiliTaskQueue,
) -> None:
    db.insert_pending_keywords("bilibili", ["a", "b", "c"], "dig")
    queue.enqueue("search", {"query": "preexisting"}, daily_budget=1)
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        daily_budget=1,
        keyword_fetch=KeywordFetchCoordinator(
            database=db,
            discovery_config=_DiscoveryCfg(True, fetch_batch=3),
        ),
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due()

    assert result["enqueued"] == 0
    assert _kw_statuses(db) == {"a": "pending", "b": "pending", "c": "pending"}


@pytest.mark.asyncio
async def test_bilibili_producer_skips_recent_task_when_throttled(
    queue: BiliTaskQueue,
) -> None:
    queue.enqueue("search", {"query": "existing"}, daily_budget=0)
    producer = BilibiliExtensionSearchProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        llm_service=_LLM(),
        bilibili_client=_BiliClient(cooldown=180),
        presence=_Presence(),
        min_interval_minutes=30,
    )

    result = await producer.produce_if_due()

    assert result["reason"] == "throttled"
