from __future__ import annotations

import json
import sqlite3
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from openbiliclaw.runtime.reddit_producer import RedditDiscoveryProducer


class _FakeSoulEngine:
    async def get_profile(self) -> Any:
        return SimpleNamespace(
            preferences=SimpleNamespace(interests=[SimpleNamespace(name="local agents")])
        )


class _FakeCandidatePipeline:
    def __init__(self) -> None:
        self.enqueued: list[Any] = []
        self.contexts: list[str] = []

    def enqueue_candidates(self, items: list[Any], *, source_context: str) -> int:
        self.enqueued.extend(items)
        self.contexts.append(source_context)
        return len(items)

    async def drain_pending(self, *, profile: Any, batch_size: int) -> dict[str, object]:
        return {"accepted": len(self.enqueued), "batch_size": batch_size}


class _FetchOnlyPipeline(_FakeCandidatePipeline):
    async def drain_pending(self, *, profile: Any, batch_size: int) -> dict[str, object]:
        raise AssertionError("Reddit producer must not synchronously drain candidates")


class _EmptyKeywordFetch:
    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str) -> list[Any]:
        assert platform == "reddit"
        return []

    def mark_failed(self, items: list[Any]) -> None:
        raise AssertionError("empty keyword claim should fall back, not fail")

    def mark_used(self, items: list[Any]) -> None:
        raise AssertionError("empty keyword claim should fall back, not mark used")


class _FakeRedditTaskQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, object], int]] = []
        self.get_calls = 0

    def expire_stale_pending(
        self,
        task_types: tuple[str, ...],
        *,
        older_than_seconds: float,
    ) -> int:
        return 0

    def enqueue_with_id(
        self,
        task_type: str,
        payload: dict[str, object],
        *,
        daily_budget: int,
    ) -> str:
        self.enqueued.append((task_type, payload, daily_budget))
        return f"task-{len(self.enqueued)}"

    def get(self, task_id: str) -> dict[str, object]:
        self.get_calls += 1
        return {
            "id": task_id,
            "status": "completed",
            "result_json": json.dumps(
                {
                    "items": [
                        {
                            "id": "abc123",
                            "title": "Local-first agents",
                            "permalink": "/r/LocalLLaMA/comments/abc123/local_first_agents/",
                            "subreddit": "LocalLLaMA",
                            "author": "agent_builder",
                            "search_keyword": "local agents",
                        }
                    ],
                    "scope_counts": {"reddit_search": 1},
                }
            ),
        }


@pytest.mark.asyncio
async def test_reddit_producer_returns_missing_when_command_backend_absent() -> None:
    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="auto",
        which=lambda _name: None,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["discovered"] == 0
    assert result["reason"] == "missing"
    assert "opencli" in str(result["message"])


@pytest.mark.asyncio
async def test_reddit_producer_fetches_search_and_enqueues_candidates() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["opencli", "daemon", "status"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Daemon: running\nExtension: connected\n",
            )
        assert args[:3] == ["opencli", "reddit", "search"]
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="""
items:
  - id: abc123
    title: Local-first agents
    url: https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/
    subreddit: LocalLLaMA
    author: agent_builder
    score: 42
    num_comments: 7
    selftext: A practical write-up.
""",
        )

    pipeline = _FakeCandidatePipeline()
    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="opencli",
        sources=("search",),
        which=lambda name: f"/usr/bin/{name}",
        runner=runner,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert result["backend"] == "opencli"
    assert result["enqueued"] == 1
    assert result["source_counts"] == {"reddit-search": 1}
    assert pipeline.contexts == ["reddit-search"]
    assert pipeline.enqueued[0].source_platform == "reddit"
    assert pipeline.enqueued[0].source_strategy == "reddit-search"
    assert calls[1][:4] == ["opencli", "reddit", "search", "local agents"]


@pytest.mark.asyncio
async def test_reddit_producer_is_fetch_only_for_manual_formal_discover() -> None:
    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["opencli", "daemon", "status"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Daemon: running\nExtension: connected\n",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="""
items:
  - id: abc123
    title: Local-first agents
    url: https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/
    subreddit: LocalLLaMA
""",
        )

    pipeline = _FetchOnlyPipeline()
    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="opencli",
        sources=("search",),
        which=lambda name: f"/usr/bin/{name}",
        runner=runner,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["backend"] == "opencli"
    assert result["enqueued"] == 1
    assert pipeline.contexts == ["reddit-search"]


@pytest.mark.asyncio
async def test_reddit_producer_falls_back_to_profile_keywords_when_keyword_store_empty() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["opencli", "daemon", "status"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Daemon: running\nExtension: connected\n",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="""
items:
  - id: abc123
    title: Local-first agents
    url: https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/
    subreddit: LocalLLaMA
""",
        )

    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="opencli",
        sources=("search",),
        which=lambda name: f"/usr/bin/{name}",
        runner=runner,
        candidate_pipeline=_FetchOnlyPipeline(),
        keyword_fetch=_EmptyKeywordFetch(),
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["source_counts"] == {"reddit-search": 1}
    assert any(call[:4] == ["opencli", "reddit", "search", "local agents"] for call in calls)


@pytest.mark.asyncio
async def test_reddit_producer_uses_extension_task_queue_without_command_probe() -> None:
    queue = _FakeRedditTaskQueue()
    kicks = 0

    async def kick() -> None:
        nonlocal kicks
        kicks += 1

    pipeline = _FakeCandidatePipeline()
    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="extension",
        sources=("search",),
        task_queue=queue,
        kick=kick,
        which=lambda _name: pytest.fail("extension backend must not probe command binaries"),
        runner=lambda _args, timeout: pytest.fail("extension backend must not run commands"),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert result["backend"] == "extension"
    assert kicks == 1
    assert queue.enqueued == [
        (
            "search",
            {"keywords": ["local agents"], "max_items_per_keyword": 5},
            300,
        )
    ]
    assert pipeline.contexts == ["reddit-search"]
    assert pipeline.enqueued[0].source_platform == "reddit"


@pytest.mark.asyncio
async def test_reddit_producer_falls_back_to_extension_when_rdt_missing() -> None:
    queue = _FakeRedditTaskQueue()
    kicks = 0

    async def kick() -> None:
        nonlocal kicks
        kicks += 1

    pipeline = _FakeCandidatePipeline()
    producer = RedditDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        backend="rdt",
        sources=("search",),
        task_queue=queue,
        kick=kick,
        which=lambda _name: None,
        runner=lambda _args, timeout: pytest.fail("missing rdt should use extension fallback"),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["discovered"] == 1
    assert result["backend"] == "extension"
    assert kicks == 1
    assert queue.enqueued == [
        (
            "search",
            {"keywords": ["local agents"], "max_items_per_keyword": 5},
            300,
        )
    ]
    assert pipeline.contexts == ["reddit-search"]


@pytest.mark.asyncio
async def test_reddit_producer_enforces_independent_daily_budgets() -> None:
    database = SimpleNamespace(conn=sqlite3.connect(":memory:"))
    calls: list[list[str]] = []

    def runner(args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["opencli", "daemon", "status"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Daemon: running\nExtension: connected\n",
            )
        assert args[:3] == ["opencli", "reddit", "hot"]
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="""
items:
  - id: hot123
    title: Hot Reddit topic
    permalink: /r/all/comments/hot123/hot_reddit_topic/
    subreddit: all
    author: hot_author
""",
        )

    producer = RedditDiscoveryProducer(
        database=database,
        soul_engine=_FakeSoulEngine(),
        backend="opencli",
        sources=("search", "hot"),
        daily_search_budget=1,
        daily_hot_budget=1,
        which=lambda name: f"/usr/bin/{name}",
        runner=runner,
        candidate_pipeline=_FakeCandidatePipeline(),
    )
    producer.record_strategy_run("search", units_used=1, discovered=1, reason="ok")

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert result["source_counts"] == {"reddit-hot": 1}
    assert all(call[:3] != ["opencli", "reddit", "search"] for call in calls)
    assert any(call[:3] == ["opencli", "reddit", "hot"] for call in calls)
    assert producer.remaining_budgets(per_run_budget=5)["search"] == 0
    assert producer.remaining_budgets(per_run_budget=5)["hot"] == 0
