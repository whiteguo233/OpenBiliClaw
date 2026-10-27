"""Tests for plugin-backed Douyin search discovery."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

import openbiliclaw.sources.douyin_plugin_search as dy_plugin_search
from openbiliclaw.sources.douyin_plugin_search import (
    DouyinPluginSearchClient,
    _hot_term_sentence_id,
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
        self.hot_terms_calls = 0
        self.hot_terms: list[dict[str, object]] = [
            {"word": "热点词", "sentence_id": "2495363", "hot_value": 12345}
        ]

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        self.keywords.append(keyword)
        return [{"aweme_id": "fallback", "desc": "fallback result"}]

    async def get_hot_terms(self, *, limit: int = 30) -> list[dict[str, object]]:
        self.hot_terms_calls += 1
        return self.hot_terms[:limit]

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


@pytest.mark.parametrize("source", ["search", "hot", "feed"])
async def test_claimed_failure_cools_every_discovery_path_without_direct_fallback(
    database: Database,
    source: str,
) -> None:
    queue = DyTaskQueue(database)
    failed = queue.enqueue_with_id("hot", {"hot_items": [{"sentence_id": "one"}]})
    assert queue.next_pending()["id"] == failed
    queue.fail(failed, error="api_rate_limited")
    direct = _FallbackClient()
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=direct,
        wait_seconds=0,
        allow_direct_fallback=True,
    )
    if source == "hot":
        items = await client.get_hot_board(limit=12)
    elif source == "feed":
        items = await client.get_recommend_feed(limit=12)
    else:
        items = await client.search_aweme("next", limit=12)
    assert items == []
    assert database.conn.execute("SELECT COUNT(*) FROM dy_tasks").fetchone()[0] == 1
    assert direct.keywords == []
    assert direct.hot_terms_calls == direct.hot_board_calls == direct.feed_calls == 0


@pytest.mark.parametrize("source", ["search", "hot", "feed"])
async def test_default_dispatcher_wakeup_uses_configured_backend_port(
    database: Database, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setattr(
        "openbiliclaw.config.load_config",
        lambda: SimpleNamespace(api=SimpleNamespace(port=18420)),
    )
    urlopen = Mock()
    monkeypatch.setattr(dy_plugin_search.request, "urlopen", urlopen)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=0,
    )

    if source == "search":
        await client.search_aweme("测试关键词", limit=1)
    elif source == "hot":
        await client.get_hot_board(limit=1)
    else:
        await client.get_recommend_feed(limit=1)

    urlopen.assert_called_once()
    assert urlopen.call_args.args[0].full_url == "http://127.0.0.1:18420/api/sources/dy/kick"


@pytest.mark.parametrize("source", ["search", "hot", "feed"])
@pytest.mark.parametrize("failure", ["failed", "timeout"])
async def test_plugin_discovery_retains_accepted_partial_items_on_failure(
    database: Database, source: str, failure: str
) -> None:
    queue = DyTaskQueue(database)
    task_ids: list[str] = []

    def report_partial() -> None:
        task = queue.next_pending()
        assert task is not None
        task_id = str(task["id"])
        task_ids.append(task_id)
        queue.merge_result(
            task_id,
            videos=[{"scope": f"dy_{source}", "aweme_id": "partial-1", "title": "已回传内容"}],
            scope_counts={f"dy_{source}": 1},
            complete=False,
        )
        if failure == "failed":
            queue.fail(task_id, error="task_timeout")

    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=0.03,
        poll_interval_seconds=0.005,
        kick=report_partial,
    )
    if source == "search":
        items = await client.search_aweme("测试", limit=5)
    elif source == "hot":
        items = await client.get_hot_board(limit=5)
    else:
        items = await client.get_recommend_feed(limit=5)

    assert [item["aweme_id"] for item in items] == ["partial-1"]
    assert getattr(client, f"last_{source}_outcome") == failure
    task = queue.get(task_ids[0])
    assert task is not None and task["status"] == "failed"
    result = json.loads(task["result_json"])
    assert result["videos"][0]["aweme_id"] == "partial-1"
    assert result["error"] == ("task_timeout" if failure == "failed" else "wait_timeout")


def test_plugin_search_item_to_aweme_maps_fields() -> None:
    aweme = plugin_search_item_to_aweme(
        {
            "aweme_id": "123",
            "title": "插件搜索结果",
            "author": "作者",
            "author_sec_uid": "sec-1",
            "cover_url": "https://cover.example/a.jpg",
            "view_count": 1000,
            "like_count": 100,
            "collect_count": 90,
            "comment_count": 80,
            "share_count": 70,
        }
    )

    assert aweme == {
        "aweme_id": "123",
        "desc": "插件搜索结果",
        "author": {"nickname": "作者", "sec_uid": "sec-1"},
        "video": {"cover": {"url_list": ["https://cover.example/a.jpg"]}},
        "statistics": {
            "play_count": 1000,
            "digg_count": 100,
            "collect_count": 90,
            "comment_count": 80,
            "share_count": 70,
        },
    }


def test_plugin_search_item_to_aweme_preserves_published_time() -> None:
    aweme = plugin_search_item_to_aweme(
        {
            "aweme_id": "published-123",
            "title": "带发布时间的插件搜索结果",
            "published_at": 1783492200,
        }
    )

    assert aweme is not None
    assert aweme["create_time"] == 1783492200


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
async def test_plugin_search_client_does_not_fallback_to_direct_on_empty_task_by_default(
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

    assert fallback.keywords == []
    assert result == []
    assert client.last_search_outcome == "empty"


@pytest.mark.asyncio
async def test_plugin_search_client_distinguishes_timeout_from_empty(
    database: Database,
) -> None:
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=0,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    result = await client.search_aweme("猫", limit=5)

    assert result == []
    assert client.last_search_outcome == "timeout"
    task = database.conn.execute(
        "SELECT status, result_json, completed_at FROM dy_tasks ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert task is not None
    assert task["status"] == "failed"
    assert task["completed_at"]
    assert json.loads(str(task["result_json"])) == {
        "error": "wait_timeout",
        "debug": {"wait_seconds": 0.0},
    }


@pytest.mark.asyncio
async def test_plugin_search_client_cancellation_terminalizes_owned_task(
    database: Database,
) -> None:
    DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=30,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    request = asyncio.create_task(client.search_aweme("猫", limit=5))
    task = None
    for _ in range(100):
        task = database.conn.execute(
            "SELECT id FROM dy_tasks ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if task is not None:
            break
        await asyncio.sleep(0.01)
    assert task is not None

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    terminal = database.conn.execute(
        "SELECT status, result_json, completed_at FROM dy_tasks WHERE id = ?",
        (task["id"],),
    ).fetchone()
    assert terminal is not None
    assert terminal["status"] == "failed"
    assert terminal["completed_at"]
    assert json.loads(str(terminal["result_json"])) == {
        "error": "wait_cancelled",
        "debug": {"wait_seconds": 30.0},
    }


@pytest.mark.asyncio
async def test_plugin_search_client_distinguishes_failed_task_from_empty(
    database: Database,
) -> None:
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    async def fail_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                queue.fail(str(task["id"]), error="captcha")
                return
            await asyncio.sleep(0.01)
        raise AssertionError("search task was not enqueued")

    result, _ = await asyncio.gather(client.search_aweme("猫", limit=5), fail_task())

    assert result == []
    assert client.last_search_outcome == "failed"


@pytest.mark.asyncio
async def test_plugin_search_client_does_not_fallback_to_direct_hot_on_empty_task_by_default(
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
                    scope_counts={"dy_hot": 0},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("hot task was not enqueued")

    result, _ = await asyncio.gather(client.get_hot_board(limit=5), complete_empty_task())

    assert fallback.hot_board_calls == 0
    assert result == []


@pytest.mark.asyncio
async def test_plugin_hot_preserves_seed_aweme_id_from_hot_terms(database: Database) -> None:
    fallback = _FallbackClient()
    fallback.hot_terms = [
        {
            "word": "热点词",
            "sentence_id": "2495363",
            "group_id": "7652229189183427849",
            "hot_value": 123,
        }
    ]
    queue = DyTaskQueue(database)
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=2,
        poll_interval_seconds=0.01,
        kick=lambda: None,
    )

    async def complete_task() -> None:
        for _ in range(100):
            task = queue.next_pending()
            if task:
                assert task["type"] == "hot"
                assert '"sentence_id": "2495363"' in str(task["payload_json"])
                assert '"seed_aweme_id": "7652229189183427849"' in str(task["payload_json"])
                queue.merge_result(
                    str(task["id"]),
                    videos=[],
                    scope_counts={"dy_hot": 0},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("hot task was not enqueued")

    await asyncio.gather(client.get_hot_board(limit=5), complete_task())


async def test_plugin_hot_prioritizes_seeded_terms_before_selection_and_rotation(
    database: Database,
) -> None:
    fallback = _FallbackClient()
    fallback.hot_terms = [
        {"sentence_id": "unseeded", "word": "仅有热词"},
        *[
            {"sentence_id": str(index), "word": "可展开热词", "group_id": f"seed-{index}"}
            for index in range(1, 5)
        ],
    ]
    queue = DyTaskQueue(database)
    selected: list[list[str]] = []

    def complete_task() -> None:
        task = queue.next_pending()
        assert task is not None
        payload = json.loads(str(task["payload_json"]))
        selected.append([item["sentence_id"] for item in payload["hot_items"]])
        queue.merge_result(str(task["id"]), videos=[], scope_counts={"dy_hot": 0}, complete=True)

    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=0.1,
        kick=complete_task,
    )

    await client.get_hot_board(limit=12)
    await client.get_hot_board(limit=12)

    assert selected == [["1", "2"], ["3", "4"]]
    assert fallback.hot_board_calls == 0


@pytest.mark.asyncio
async def test_plugin_search_client_does_not_fallback_to_direct_feed_on_empty_task_by_default(
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
                    scope_counts={"dy_feed": 0},
                    complete=True,
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("feed task was not enqueued")

    result, _ = await asyncio.gather(client.get_recommend_feed(limit=5), complete_empty_task())

    assert fallback.feed_calls == 0
    assert result == []


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
    assert client.last_search_outcome == "budget_exhausted"
    # Budget-rejected path must NOT fall back to direct-cookie search.
    assert fallback.keywords == []


# ── hot-seed rotation ────────────────────────────────────────────────────


def _rotation_client(database: Database) -> DouyinPluginSearchClient:
    return DouyinPluginSearchClient(
        database=database,
        direct_client=_FallbackClient(),
        kick=lambda: None,
    )


def test_rotate_hot_terms_filters_recent_sentence_ids(database: Database) -> None:
    client = _rotation_client(database)
    terms = [{"sentence_id": "1"}, {"sentence_id": "2"}, {"sentence_id": "3"}]

    first = client._rotate_hot_terms(terms, seed_count=2)
    assert [_hot_term_sentence_id(t) for t in first] == ["1", "2"]

    # Second call within TTL: 1 & 2 are recent → the fresh term (3) comes first,
    # then a stale term tops up the tail to keep seed_count.
    second = client._rotate_hot_terms(terms, seed_count=2)
    assert _hot_term_sentence_id(second[0]) == "3"
    assert len(second) == 2


def test_rotate_hot_terms_falls_back_when_all_recent(database: Database) -> None:
    client = _rotation_client(database)
    terms = [{"sentence_id": "1"}, {"sentence_id": "2"}]

    client._rotate_hot_terms(terms, seed_count=2)  # marks both recent
    again = client._rotate_hot_terms(terms, seed_count=2)
    # Prefer stale over nothing: still return both rather than starving the source.
    assert sorted(_hot_term_sentence_id(t) for t in again) == ["1", "2"]


def test_rotate_hot_terms_reuses_after_ttl_expiry(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(dy_plugin_search.time, "monotonic", lambda: now[0])
    client = _rotation_client(database)
    terms = [{"sentence_id": "1"}, {"sentence_id": "2"}]

    assert _hot_term_sentence_id(client._rotate_hot_terms(terms, seed_count=1)[0]) == "1"
    now[0] += 60
    # "1" still recent → the fresh term "2" is picked.
    assert _hot_term_sentence_id(client._rotate_hot_terms(terms, seed_count=1)[0]) == "2"

    # Advance past the TTL — both prior uses expire and are pruned, so "1" is
    # eligible (and first in the list) again.
    now[0] += dy_plugin_search._HOT_SEED_REUSE_TTL_SECONDS + 1
    picked = client._rotate_hot_terms(terms, seed_count=1)
    assert _hot_term_sentence_id(picked[0]) == "1"
    assert client._recent_hot_sentence_ids == {"1": now[0]}


async def test_search_aweme_budget_falls_back_to_direct_when_not_armed(database: Database) -> None:
    # Compatibility mode: budget exhaustion can still fall back to direct-cookie
    # search when explicitly requested, but the default discovery path keeps it off.
    queue = DyTaskQueue(database)
    queue.enqueue_with_id("search", {"keywords": ["x"]}, daily_budget=1)
    fallback = _FallbackClient()
    client = DouyinPluginSearchClient(
        database=database,
        direct_client=fallback,
        wait_seconds=1.0,
        daily_search_budget=1,
        kick=lambda: None,
        allow_direct_fallback=True,
    )
    result = await client.search_aweme("猫", limit=5)
    assert fallback.keywords == ["猫"]
    assert result == [{"aweme_id": "fallback", "desc": "fallback result"}]
