from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Soul:
    async def get_profile(self) -> object:
        return SimpleNamespace(preferences=SimpleNamespace(interests=[]))


class _Pipeline:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.drained = False
        self.source_contexts: list[str] = []

    def pool_full(self) -> bool:
        return False

    def enqueue_candidates(self, items: list[object], *, source_context: str = "") -> int:
        self.items.extend(items)
        self.source_context = source_context
        self.source_contexts.append(source_context)
        return len(items)

    async def drain_pending(self, *, profile: object, batch_size: int) -> dict[str, int]:
        self.drained = True
        return {"evaluated": 0, "cached": 0, "rejected": 0}


class _KeywordFetch:
    def __init__(self, claimed: list[object]) -> None:
        self.claimed = claimed
        self.used: list[object] = []
        self.failed: list[object] = []
        self.rolled_back: list[object] = []
        self.claim_sizes: list[int | None] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[object]:
        self.platform = platform
        self.claim_sizes.append(n)
        return self.claimed[: n or len(self.claimed)]

    def mark_used(self, claimed: list[object]) -> None:
        self.used.extend(claimed)

    def mark_failed(self, claimed: list[object]) -> None:
        self.failed.extend(claimed)

    def rollback(self, claimed: object) -> None:
        self.rolled_back.append(claimed)


class _Queue:
    def __init__(self, result: dict[str, object], *, task_id: str | None = "task-1") -> None:
        self.result = result
        self.task_id = task_id
        self.payload: dict[str, object] = {}

    def expire_stale_pending(self, task_types: object, *, older_than_seconds: float) -> int:
        return 0

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        self.task_type = task_type
        self.payload = payload
        return self.task_id

    def get(self, task_id: str) -> dict[str, object] | None:
        if self.task_id is None:
            return None
        return {
            "id": task_id,
            "status": "completed",
            "result_json": json.dumps(self.result, ensure_ascii=False),
        }


class _MultiQueue:
    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        self.results = results
        self.enqueued: list[tuple[str, dict[str, object], int]] = []

    def expire_stale_pending(self, task_types: object, *, older_than_seconds: float) -> int:
        return 0

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget: int = 100,
    ) -> str | None:
        self.enqueued.append((task_type, payload, daily_budget))
        return f"{task_type}-task"

    def get(self, task_id: str) -> dict[str, object] | None:
        task_type = task_id.replace("-task", "")
        return {
            "id": task_id,
            "status": "completed",
            "result_json": json.dumps(
                self.results.get(task_type, {"items": []}), ensure_ascii=False
            ),
        }


@pytest.mark.asyncio
async def test_zhihu_producer_enqueues_search_results_and_marks_keyword_used() -> None:
    from openbiliclaw.runtime.zhihu_producer import ZhihuDiscoveryProducer

    claimed = SimpleNamespace(id=11, keyword="AI 工程化")
    keyword_fetch = _KeywordFetch([claimed])
    queue = _Queue(
        {
            "items": [
                {
                    "scope": "zhihu_search",
                    "search_keyword": "AI 工程化",
                    "title": "如何做 AI 工程化？",
                    "url": "https://www.zhihu.com/question/1/answer/2",
                    "content_type": "answer",
                    "content_id": "2",
                }
            ]
        }
    )
    pipeline = _Pipeline()
    producer = ZhihuDiscoveryProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        candidate_pipeline=pipeline,
        keyword_fetch=keyword_fetch,
        min_interval_minutes=0,
        wait_seconds=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert result["enqueued"] == 1
    assert queue.task_type == "search"
    assert queue.payload["keywords"] == ["AI 工程化"]
    assert queue.payload["source_keyword_ids"] == {"AI 工程化": 11}
    assert keyword_fetch.platform == "zhihu"
    assert keyword_fetch.claim_sizes == [None]
    assert keyword_fetch.used == [claimed]
    assert keyword_fetch.failed == []
    assert pipeline.source_context == "zhihu-search"
    assert pipeline.items[0].source_keyword_id == 11


@pytest.mark.asyncio
async def test_zhihu_producer_rolls_back_keyword_when_task_budget_exhausted() -> None:
    from openbiliclaw.runtime.zhihu_producer import ZhihuDiscoveryProducer

    claimed = SimpleNamespace(id=12, keyword="数据库")
    keyword_fetch = _KeywordFetch([claimed])
    queue = _Queue({"items": []}, task_id=None)
    producer = ZhihuDiscoveryProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        keyword_fetch=keyword_fetch,
        min_interval_minutes=0,
        wait_seconds=0,
        daily_search_budget=1,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "budget_exhausted"}
    assert keyword_fetch.rolled_back == [claimed]


@pytest.mark.asyncio
async def test_zhihu_producer_schedules_hot_feed_creator_and_related_without_keyword_claims() -> (
    None
):
    from openbiliclaw.runtime.zhihu_producer import ZhihuDiscoveryProducer

    queue = _MultiQueue(
        {
            "hot": {
                "items": [
                    {
                        "scope": "zhihu_hot",
                        "title": "热榜问题",
                        "url": "https://www.zhihu.com/question/1",
                        "content_type": "question",
                        "content_id": "1",
                        "source_strategy": "zhihu-hot",
                    }
                ]
            },
            "feed": {
                "items": [
                    {
                        "scope": "zhihu_feed",
                        "title": "首页回答",
                        "url": "https://www.zhihu.com/question/2/answer/3",
                        "content_type": "answer",
                        "content_id": "3",
                        "source_strategy": "zhihu-feed",
                    }
                ]
            },
            "creator": {
                "items": [
                    {
                        "scope": "zhihu_creator",
                        "title": "作者文章",
                        "url": "https://zhuanlan.zhihu.com/p/4",
                        "content_type": "article",
                        "content_id": "4",
                        "source_strategy": "zhihu-creator",
                    }
                ]
            },
            "related": {
                "items": [
                    {
                        "scope": "zhihu_related",
                        "title": "相关回答",
                        "url": "https://www.zhihu.com/question/5/answer/6",
                        "content_type": "answer",
                        "content_id": "6",
                        "source_strategy": "zhihu-related",
                    }
                ]
            },
        }
    )
    keyword_fetch = _KeywordFetch([])
    pipeline = _Pipeline()
    producer = ZhihuDiscoveryProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        candidate_pipeline=pipeline,
        keyword_fetch=keyword_fetch,
        sources=("hot", "feed", "creator", "related"),
        creator_seed_loader=lambda: ["https://www.zhihu.com/people/demo"],
        related_seed_loader=lambda: ["https://www.zhihu.com/question/5/answer/6"],
        min_interval_minutes=0,
        wait_seconds=0,
        daily_hot_budget=2,
        daily_feed_budget=3,
        daily_creator_budget=4,
        daily_related_budget=5,
    )

    result = await producer.produce_if_due(limit=8)

    assert result["reason"] == "ok"
    assert result["discovered"] == 4
    assert [task_type for task_type, _payload, _budget in queue.enqueued] == [
        "hot",
        "feed",
        "creator",
        "related",
    ]
    assert [budget for _task_type, _payload, budget in queue.enqueued] == [2, 3, 4, 5]
    assert queue.enqueued[0][1] == {"max_items": 8}
    assert queue.enqueued[2][1]["creator_urls"] == ["https://www.zhihu.com/people/demo"]
    assert queue.enqueued[3][1]["related_urls"] == ["https://www.zhihu.com/question/5/answer/6"]
    assert keyword_fetch.claim_sizes == []
    assert pipeline.source_contexts == [
        "zhihu-hot",
        "zhihu-feed",
        "zhihu-creator",
        "zhihu-related",
    ]


@pytest.mark.asyncio
async def test_zhihu_producer_derives_creator_and_related_seeds_from_same_run_items() -> None:
    from openbiliclaw.runtime.zhihu_producer import ZhihuDiscoveryProducer

    queue = _MultiQueue(
        {
            "hot": {
                "items": [
                    {
                        "scope": "zhihu_hot",
                        "title": "热榜问题",
                        "url": "https://www.zhihu.com/question/10",
                        "author_url": "https://www.zhihu.com/people/hot-author",
                        "content_type": "question",
                        "content_id": "10",
                        "source_strategy": "zhihu-hot",
                    }
                ]
            },
            "feed": {
                "items": [
                    {
                        "scope": "zhihu_feed",
                        "title": "首页回答",
                        "url": "https://www.zhihu.com/question/11/answer/12",
                        "author_url": "https://www.zhihu.com/people/feed-author",
                        "content_type": "answer",
                        "content_id": "12",
                        "source_strategy": "zhihu-feed",
                    }
                ]
            },
            "creator": {
                "items": [
                    {
                        "scope": "zhihu_creator",
                        "title": "作者文章",
                        "url": "https://zhuanlan.zhihu.com/p/13",
                        "content_type": "article",
                        "content_id": "13",
                        "source_strategy": "zhihu-creator",
                    }
                ]
            },
            "related": {
                "items": [
                    {
                        "scope": "zhihu_related",
                        "title": "相关回答",
                        "url": "https://www.zhihu.com/question/14/answer/15",
                        "content_type": "answer",
                        "content_id": "15",
                        "source_strategy": "zhihu-related",
                    }
                ]
            },
        }
    )
    producer = ZhihuDiscoveryProducer(
        task_queue=queue,
        soul_engine=_Soul(),
        candidate_pipeline=_Pipeline(),
        sources=("hot", "feed", "creator", "related"),
        creator_seed_loader=lambda: [],
        related_seed_loader=lambda: [],
        min_interval_minutes=0,
        wait_seconds=0,
        daily_hot_budget=2,
        daily_feed_budget=3,
        daily_creator_budget=4,
        daily_related_budget=5,
        max_seed_count=1,
    )

    result = await producer.produce_if_due(limit=6)

    assert result["reason"] == "ok"
    assert [task_type for task_type, _payload, _budget in queue.enqueued] == [
        "hot",
        "feed",
        "creator",
        "related",
    ]
    assert queue.enqueued[2][1]["creator_urls"] == ["https://www.zhihu.com/people/hot-author"]
    assert queue.enqueued[3][1]["related_urls"] == ["https://www.zhihu.com/question/10"]
