"""Tests for plugin-backed Douyin search discovery."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.sources.douyin_plugin_search import (
    DouyinPluginSearchClient,
    plugin_search_item_to_aweme,
)
from openbiliclaw.sources.dy_tasks import DyTaskQueue
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _FallbackClient:
    def __init__(self) -> None:
        self.keywords: list[str] = []
        self.hot_board_calls = 0
        self.feed_calls = 0

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        self.keywords.append(keyword)
        return [{"aweme_id": "fallback", "desc": "fallback result"}]

    async def get_hot_terms(self, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"word": "热点词", "sentence_id": "2495363", "hot_value": 12345}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        self.hot_board_calls += 1
        return []

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, object]]:
        return []

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        self.feed_calls += 1
        return []


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "openbiliclaw.db")
    db.initialize()
    return db


def test_plugin_search_item_to_aweme_maps_fields() -> None:
    aweme = plugin_search_item_to_aweme(
        {
            "aweme_id": "123",
            "title": "插件搜索结果",
            "author": "作者",
            "author_sec_uid": "sec-1",
            "cover_url": "https://cover.example/a.jpg",
        }
    )

    assert aweme == {
        "aweme_id": "123",
        "desc": "插件搜索结果",
        "author": {"nickname": "作者", "sec_uid": "sec-1"},
        "video": {"cover": {"url_list": ["https://cover.example/a.jpg"]}},
    }


@pytest.mark.asyncio
async def test_plugin_search_client_returns_completed_task_items(database: Database) -> None:
    queue = DyTaskQueue(database)
    kicked: list[str] = []
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: kicked.append("dy"),
    )

    async def complete_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                queue.merge_result(
                    str(task["id"]),
                    videos=[
                        {
                            "scope": "dy_search",
                            "aweme_id": "plugin-1",
                            "title": "插件结果",
                            "author": "作者",
                            "author_sec_uid": "sec-1",
                            "cover_url": "",
                        }
                    ],
                    scope_counts={"dy_search": 1},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("search task was not enqueued")

    result, _ = await asyncio.gather(client.search_aweme("猫", limit=5), complete_task())

    assert kicked == ["dy"]
    assert result == [
        {
            "aweme_id": "plugin-1",
            "desc": "插件结果",
            "author": {"nickname": "作者", "sec_uid": "sec-1"},
            "video": {},
        }
    ]


@pytest.mark.asyncio
async def test_plugin_search_client_falls_back_to_direct_on_empty_task(
    database: Database,
) -> None:
    fallback = _FallbackClient()
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    async def complete_empty_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                queue.merge_result(
                    str(task["id"]),
                    videos=[],
                    scope_counts={"dy_search": 0},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("search task was not enqueued")

    result, _ = await asyncio.gather(client.search_aweme("猫", limit=5), complete_empty_task())

    assert fallback.keywords == ["猫"]
    assert result == [{"aweme_id": "fallback", "desc": "fallback result"}]


@pytest.mark.asyncio
async def test_plugin_search_client_expires_stale_pending_discovery_tasks(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    stale_id = queue.enqueue_with_id(
        "search",
        {"keywords": ["旧任务"], "max_items_per_keyword": 20},
        daily_budget=100,
    )
    assert stale_id is not None
    database.conn.execute(
        "UPDATE dy_tasks SET created_at = datetime('now', '-10 minutes') WHERE id = ?",
        (stale_id,),
    )
    database.conn.commit()

    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    async def complete_fresh_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                assert task["id"] != stale_id
                assert '"新任务"' in str(task["payload_json"])
                queue.merge_result(
                    str(task["id"]),
                    videos=[
                        {
                            "scope": "dy_search",
                            "aweme_id": "fresh-1",
                            "title": "新任务结果",
                            "author": "作者",
                            "author_sec_uid": "sec-1",
                        }
                    ],
                    scope_counts={"dy_search": 1},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("fresh search task was not enqueued")

    result, _ = await asyncio.gather(client.search_aweme("新任务", limit=5), complete_fresh_task())

    stale_task = queue.get(stale_id)
    assert stale_task is not None
    assert stale_task["status"] == "failed"
    assert "stale_pending" in str(stale_task["result_json"])
    assert result[0]["aweme_id"] == "fresh-1"


@pytest.mark.asyncio
async def test_plugin_search_client_returns_hot_related_task_items(database: Database) -> None:
    fallback = _FallbackClient()
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=2,
        poll_interval_seconds=0.01,
        daily_hot_budget=7,
        kick=lambda: None,
    )

    async def complete_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                assert task["type"] == "hot"
                assert '"sentence_id": "2495363"' in str(task["payload_json"])
                assert '"max_items": 5' in str(task["payload_json"])
                queue.merge_result(
                    str(task["id"]),
                    videos=[
                        {
                            "scope": "dy_hot",
                            "aweme_id": "hot-rel-1",
                            "title": "热点相关视频",
                            "author": "热点作者",
                            "author_sec_uid": "sec-hot",
                            "cover_url": "https://cover.example/hot.jpg",
                        }
                    ],
                    scope_counts={"dy_hot": 1},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("hot task was not enqueued")

    result, _ = await asyncio.gather(client.get_hot_board(limit=5), complete_task())

    assert fallback.hot_board_calls == 0
    assert result == [
        {
            "aweme_id": "hot-rel-1",
            "desc": "热点相关视频",
            "author": {"nickname": "热点作者", "sec_uid": "sec-hot"},
            "video": {"cover": {"url_list": ["https://cover.example/hot.jpg"]}},
        }
    ]


@pytest.mark.asyncio
async def test_plugin_search_client_returns_feed_task_items(database: Database) -> None:
    fallback = _FallbackClient()
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=2,
        poll_interval_seconds=0.01,
        daily_feed_budget=9,
        kick=lambda: None,
    )

    async def complete_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                assert task["type"] == "feed"
                assert '"max_items": 5' in str(task["payload_json"])
                queue.merge_result(
                    str(task["id"]),
                    videos=[
                        {
                            "scope": "dy_feed",
                            "aweme_id": "feed-1",
                            "title": "首页推荐视频",
                            "author": "推荐作者",
                            "author_sec_uid": "sec-feed",
                            "cover_url": "https://cover.example/feed.jpg",
                        }
                    ],
                    scope_counts={"dy_feed": 1},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("feed task was not enqueued")

    result, _ = await asyncio.gather(client.get_recommend_feed(limit=5), complete_task())

    assert fallback.feed_calls == 0
    assert result == [
        {
            "aweme_id": "feed-1",
            "desc": "首页推荐视频",
            "author": {"nickname": "推荐作者", "sec_uid": "sec-feed"},
            "video": {"cover": {"url_list": ["https://cover.example/feed.jpg"]}},
        }
    ]


# ── P1.7 distinguishable budget-rejection signal ─────────────────────────


async def test_search_aweme_raises_budget_sentinel_when_armed(database: Database) -> None:
    from openbiliclaw.sources.douyin_plugin_search import DouyinBudgetExhausted

    queue = DyTaskQueue(database)
    # Exhaust today's search-task budget so enqueue is refused.
    queue.enqueue_with_id("search", {"keywords": ["x"]}, daily_budget=1)
    fallback = _FallbackClient()
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=1.0,
        daily_search_budget=1,
        kick=lambda: None,
        raise_on_budget=True,
    )
    with pytest.raises(DouyinBudgetExhausted):
        await client.search_aweme("猫", limit=5)
    # Budget-rejected path must NOT fall back to direct-cookie search.
    assert fallback.keywords == []


async def test_search_aweme_budget_falls_back_to_direct_when_not_armed(database: Database) -> None:
    # Legacy default (raise_on_budget=False): budget exhaustion → fall back to
    # direct-cookie search (byte-identical to pre-P1.7), NOT a sentinel.
    queue = DyTaskQueue(database)
    queue.enqueue_with_id("search", {"keywords": ["x"]}, daily_budget=1)
    fallback = _FallbackClient()
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=1.0,
        daily_search_budget=1,
        kick=lambda: None,
    )
    result = await client.search_aweme("猫", limit=5)
    assert fallback.keywords == ["猫"]
    assert result == [{"aweme_id": "fallback", "desc": "fallback result"}]
