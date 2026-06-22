from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.douyin_producer import (
    DouyinDiscoveryProducer,
    douyin_runtime_hot_budget,
)


class _FakeSoulEngine:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _FakeCandidatePipeline:
    def __init__(self, *, pool_full: bool = False) -> None:
        self._pool_full = pool_full
        self.enqueued: list[tuple[list[object], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self._pool_full

    def enqueue_candidates(self, items: list[object], *, source_context: str = "") -> int:
        self.enqueued.append((list(items), source_context))
        return len(items)

    async def drain_pending(self, *, profile: object, batch_size: int = 30) -> dict[str, int]:
        self.drains.append(batch_size)
        return {"evaluated": batch_size, "cached": 2, "rejected": 0}


async def test_douyin_producer_invokes_discovery_with_cache_options() -> None:
    calls: list[tuple[dict[str, object], DouyinDiscoveryOptions]] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append((profile, options))
        return DouyinDiscoveryResult(
            items=[SimpleNamespace(), SimpleNamespace()],
            cached=True,
            source_counts={"dy-plugin-search": 2},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    result = await producer.produce_if_due(limit=12)

    assert result == {
        "discovered": 2,
        "cached": True,
        "source_counts": {"dy-plugin-search": 2},
        "reason": "ok",
    }
    assert len(calls) == 1
    profile, options = calls[0]
    assert profile == {"profile": "ok"}
    assert options.limit == 12
    assert options.sources == ("search", "hot")
    assert options.cache is True
    assert options.evaluate is True
    assert options.keywords_per_run == 1


async def test_douyin_producer_enqueues_raw_candidates_when_pipeline_is_available() -> None:
    calls: list[DouyinDiscoveryOptions] = []
    pipeline = _FakeCandidatePipeline()
    raw_items = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(
            items=raw_items,
            cached=False,
            source_counts={"dy-plugin-search": 2},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot"),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=12)

    assert calls[0].cache is False
    assert calls[0].evaluate is False
    assert pipeline.enqueued == [(raw_items, "douyin")]
    assert pipeline.drains == [12]
    assert result["discovered"] == 2
    assert result["enqueued"] == 2
    assert result["cached"] == 2


async def test_douyin_producer_stamps_strategy_score_threshold_before_enqueue() -> None:
    pipeline = _FakeCandidatePipeline()
    raw_items = [
        DiscoveredContent(
            content_id="dy-search-1",
            title="Search",
            source_platform="douyin",
            source_strategy="dy-plugin-search",
        ),
        DiscoveredContent(
            content_id="dy-hot-1",
            title="Hot",
            source_platform="douyin",
            source_strategy="dy-direct-hot",
        ),
        DiscoveredContent(
            content_id="dy-feed-1",
            title="Feed",
            source_platform="douyin",
            source_strategy="dy-plugin-feed",
        ),
    ]

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(
            items=raw_items,
            cached=False,
            source_counts={"search": 1, "hot": 1, "feed": 1},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("hot",),
        candidate_pipeline=pipeline,
    )

    await producer.produce_if_due(limit=3)

    assert pipeline.enqueued
    thresholds = {item.content_id: item.score_threshold for item in pipeline.enqueued[0][0]}
    assert thresholds == {
        "dy-search-1": 0.60,
        "dy-hot-1": 0.60,
        "dy-feed-1": 0.60,
    }


async def test_douyin_producer_skips_discovery_when_pipeline_pool_is_full() -> None:
    calls: list[DouyinDiscoveryOptions] = []
    pipeline = _FakeCandidatePipeline(pool_full=True)

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[SimpleNamespace()], cached=False, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot"),
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=12)

    assert result["reason"] == "pool_full"
    assert calls == []
    assert pipeline.enqueued == []
    assert pipeline.drains == []


async def test_douyin_producer_uses_feed_only_for_tiny_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=3)

    assert calls[0].sources == ("feed",)
    assert calls[0].per_source_limit == 3


async def test_douyin_producer_restores_search_for_larger_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=12)

    assert calls[0].sources == ("search", "hot")


async def test_douyin_producer_uses_hot_before_feed_for_medium_runtime_gap() -> None:
    calls: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        calls.append(options)
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        sources=("search", "hot", "feed"),
    )

    await producer.produce_if_due(limit=7)

    assert calls[0].sources == ("hot", "feed")


def test_douyin_runtime_hot_budget_scales_with_runtime_deficit() -> None:
    assert douyin_runtime_hot_budget(base_budget=5, requested_limit=30) == 30
    assert douyin_runtime_hot_budget(base_budget=40, requested_limit=30) == 40
    assert douyin_runtime_hot_budget(base_budget=5, requested_limit=3) == 5


def test_douyin_runtime_hot_budget_preserves_zero_as_no_daily_cap() -> None:
    assert douyin_runtime_hot_budget(base_budget=0, requested_limit=30) == 0


async def test_douyin_producer_throttles_recent_runs() -> None:
    calls = 0

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        nonlocal calls
        calls += 1
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=30,
    )
    producer._last_run_at = datetime.now(UTC) - timedelta(minutes=5)

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "throttled"}
    assert calls == 0


async def test_douyin_producer_soft_skips_when_profile_unavailable() -> None:
    class _BrokenSoulEngine:
        async def get_profile(self) -> object:
            raise RuntimeError("not ready")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        raise AssertionError("should not discover without profile")

    producer = DouyinDiscoveryProducer(
        soul_engine=_BrokenSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}


# ── P1.7 unified keyword planner fetch path (inline-admit lifecycle) ─────


from dataclasses import dataclass as _dataclass  # noqa: E402

from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator  # noqa: E402
from openbiliclaw.sources.douyin_plugin_search import DouyinBudgetExhausted  # noqa: E402
from openbiliclaw.storage.database import Database  # noqa: E402


@_dataclass
class _DiscoveryCfg:
    unified_keyword_planner_enabled: bool = False
    fetch_batch: int = 5


def _dy_statuses(db: Database) -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT keyword, status FROM discovery_keywords WHERE platform = 'douyin' ORDER BY id"
    ).fetchall()
    return {str(r["keyword"]): str(r["status"]) for r in rows}


def _mk_db(tmp_path: Any) -> Database:
    db = Database(tmp_path / "dy_kw.db")
    db.initialize()
    return db


async def test_douyin_flag_off_does_not_claim(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["stored"], "dig")
    seen: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        seen.append(options)
        return DouyinDiscoveryResult(items=[SimpleNamespace()], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(False)),
    )
    await producer.produce_if_due(limit=12)
    # Flag off → no claim, options carry no seed keywords, store untouched.
    assert seen and seen[0].keywords == ()
    assert _dy_statuses(db) == {"stored": "pending"}


async def test_douyin_flag_on_marks_used_on_success(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a", "kw-b"], "dig")
    seen: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        seen.append(options)
        return DouyinDiscoveryResult(
            items=[SimpleNamespace(), SimpleNamespace()], cached=True, source_counts={}
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(
            database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=5)
        ),
    )
    # limit>=10 selects ("search", "hot") so search runs (claim applies).
    result = await producer.produce_if_due(limit=12)
    assert result["reason"] == "ok"
    # Claimed words injected as seed keywords + raise_on_budget armed.
    assert sorted(seen[0].keywords) == ["kw-a", "kw-b"]
    assert seen[0].raise_on_budget is True
    # Inline-admit success (items produced) → both words USED.
    assert _dy_statuses(db) == {"kw-a": "used", "kw-b": "used"}


async def test_douyin_flag_on_marks_failed_on_empty(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a"], "dig")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(items=[], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    await producer.produce_if_due(limit=12)
    # Empty fetch → word FAILED (retry).
    assert _dy_statuses(db) == {"kw-a": "failed"}


async def test_douyin_flag_on_budget_sentinel_rolls_back(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a", "kw-b"], "dig")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        # Simulate the plugin-search budget wall surfacing the distinguishable
        # sentinel (search_aweme with raise_on_budget=True).
        raise DouyinBudgetExhausted("budget")

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    result = await producer.produce_if_due(limit=12)
    assert result["reason"] == "budget_exhausted"
    # Budget rejection after claim → both words rolled back to pending (not burned).
    assert _dy_statuses(db) == {"kw-a": "pending", "kw-b": "pending"}


async def test_douyin_flag_on_empty_store_skips(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)  # store empty

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        raise AssertionError("discover must not run when the store is empty")

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    result = await producer.produce_if_due(limit=12)
    assert result["reason"] == "no_keywords"
    assert _dy_statuses(db) == {}


async def test_douyin_flag_on_feed_only_run_does_not_claim(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a"], "dig")
    seen: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        seen.append(options)
        return DouyinDiscoveryResult(items=[SimpleNamespace()], cached=True, source_counts={})

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    # Tiny gap (limit<=3) selects feed-only — no search → keyword store untouched.
    await producer.produce_if_due(limit=2)
    assert "search" not in seen[0].sources
    assert seen[0].keywords == ()
    assert _dy_statuses(db) == {"kw-a": "pending"}
