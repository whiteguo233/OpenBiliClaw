"""Tests for Zhihu bootstrap task helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


def test_zhihu_bootstrap_items_to_events_maps_history_activity_and_collections() -> None:
    from openbiliclaw.sources.zhihu_tasks import zhihu_bootstrap_items_to_events

    events = zhihu_bootstrap_items_to_events(
        [
            {
                "scope": "zhihu_read_history",
                "title": "最近浏览回答",
                "url": "https://www.zhihu.com/question/1/answer/2",
                "content_type": "answer",
                "content_id": "2",
                "author": "作者 A",
            },
            {
                "scope": "zhihu_activity",
                "interaction_action": "赞同了回答",
                "title": "赞同回答",
                "url": "https://www.zhihu.com/question/3/answer/4",
                "content_type": "answer",
                "content_id": "4",
                "author": "作者 B",
            },
            {
                "scope": "zhihu_collection",
                "title": "收藏文章",
                "url": "https://zhuanlan.zhihu.com/p/5",
                "content_type": "article",
                "content_id": "5",
                "author": "作者 C",
                "collection_name": "我的收藏",
            },
        ]
    )

    assert [event["event_type"] for event in events] == ["view", "like", "favorite"]
    assert [event["metadata"]["source_platform"] for event in events] == [
        "zhihu",
        "zhihu",
        "zhihu",
    ]
    assert [event["metadata"]["import_source"] for event in events] == [
        "zhihu_bootstrap_read_history",
        "zhihu_bootstrap_activity_like",
        "zhihu_bootstrap_collection",
    ]


def test_zhihu_discovery_items_to_contents_maps_search_candidates() -> None:
    from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

    contents = zhihu_discovery_items_to_contents(
        [
            {
                "scope": "zhihu_search",
                "search_keyword": "AI 工程化",
                "title": "如何做 AI 工程化？",
                "url": "https://www.zhihu.com/question/1/answer/2",
                "content_type": "answer",
                "content_id": "2",
                "question_id": "1",
                "author": "作者 A",
                "summary": "回答摘要",
                "voteup": 88,
            },
            {
                "scope": "zhihu_search",
                "search_keyword": "AI 工程化",
                "title": "知乎文章",
                "url": "https://zhuanlan.zhihu.com/p/3",
                "content_type": "article",
                "content_id": "3",
                "author": "作者 B",
            },
            {"scope": "zhihu_read_history", "title": "不是 discovery"},
        ],
        source_keyword_ids={"AI 工程化": 42},
    )

    assert len(contents) == 2
    first = contents[0]
    assert first.source_platform == "zhihu"
    assert first.source_strategy == "zhihu-search"
    assert first.content_type == "answer"
    assert first.content_id == "answer:2"
    assert first.content_url == "https://www.zhihu.com/question/1/answer/2"
    assert first.title == "如何做 AI 工程化？"
    assert first.author_name == "作者 A"
    assert first.description == "回答摘要"
    assert first.like_count == 88
    assert first.score_threshold == 0.60
    assert first.source_keyword_id == 42


def test_zhihu_discovery_items_to_contents_maps_non_search_sources() -> None:
    from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

    contents = zhihu_discovery_items_to_contents(
        [
            {
                "scope": "zhihu_hot",
                "title": "热榜问题",
                "url": "https://www.zhihu.com/question/10",
                "content_type": "question",
                "content_id": "10",
                "summary": "热榜摘要",
                "source_strategy": "zhihu-hot",
            },
            {
                "scope": "zhihu_feed",
                "title": "首页回答",
                "url": "https://www.zhihu.com/question/11/answer/12",
                "content_type": "answer",
                "content_id": "12",
                "question_id": "11",
                "author": "作者 F",
                "source_strategy": "zhihu-feed",
            },
            {
                "scope": "zhihu_creator",
                "title": "作者文章",
                "url": "https://zhuanlan.zhihu.com/p/13",
                "content_type": "article",
                "content_id": "13",
                "author": "作者 C",
                "source_strategy": "zhihu-creator",
            },
            {
                "scope": "zhihu_related",
                "title": "相关回答",
                "url": "https://www.zhihu.com/question/14/answer/15",
                "content_type": "answer",
                "content_id": "15",
                "question_id": "14",
                "source_strategy": "zhihu-related",
            },
        ]
    )

    assert [item.source_strategy for item in contents] == [
        "zhihu-hot",
        "zhihu-feed",
        "zhihu-creator",
        "zhihu-related",
    ]
    assert [item.content_type for item in contents] == ["question", "answer", "article", "answer"]
    assert [item.content_id for item in contents] == [
        "question:10",
        "answer:12",
        "article:13",
        "answer:15",
    ]
    assert contents[0].description == "热榜摘要"
    assert contents[1].author_name == "作者 F"


def test_zhihu_task_queue_claims_pending_task_until_terminal_status(
    database: Database,
) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    queue = ZhihuTaskQueue(database)
    task_id = queue.enqueue_with_id(
        "bootstrap_events",
        {"scopes": ["zhihu_read_history"], "max_items_per_scope": 20},
    )
    assert task_id is not None

    first = queue.next_pending()

    assert first is not None
    assert first["id"] == task_id
    assert first["status"] == "in_progress"
    assert queue.next_pending() is None

    queue.merge_result(task_id, items=[], complete=True)
    assert queue.next_pending() is None


def test_zhihu_task_queue_finds_recent_bootstrap_task(database: Database) -> None:
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    queue = ZhihuTaskQueue(database)
    task_id = queue.enqueue_with_id("bootstrap_events", {"scopes": ["zhihu_read_history"]})
    assert task_id is not None

    recent = queue.find_recent_task("bootstrap_events", recent_hours=6)

    assert recent is not None
    assert recent["id"] == task_id


def test_recent_zhihu_seed_helpers_read_completed_task_results(database: Database) -> None:
    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        recent_zhihu_creator_urls,
        recent_zhihu_related_urls,
    )

    queue = ZhihuTaskQueue(database)
    task_id = queue.enqueue_with_id("search", {"keywords": ["AI"]})
    assert task_id is not None
    queue.merge_result(
        task_id,
        items=[
            {
                "scope": "zhihu_search",
                "url": "https://www.zhihu.com/question/1/answer/2",
                "author_url": "https://www.zhihu.com/people/demo",
            },
            {
                "scope": "zhihu_hot",
                "url": "https://www.zhihu.com/question/3",
                "author_url": "https://www.zhihu.com/people/demo",
            },
        ],
        complete=True,
    )

    assert recent_zhihu_creator_urls(database, limit=5) == ["https://www.zhihu.com/people/demo"]
    assert recent_zhihu_related_urls(database, limit=5) == [
        "https://www.zhihu.com/question/1/answer/2",
        "https://www.zhihu.com/question/3",
    ]
