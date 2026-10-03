"""Tests for the runtime TikTok discovery producer."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from openbiliclaw.runtime.keyword_fetch import KeywordFetchCoordinator
from openbiliclaw.runtime.tiktok_producer import (
    TiktokDiscoveryProducer,
    TiktokStrategyRunResult,
)
from openbiliclaw.storage.database import Database


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
        return {"evaluated": batch_size, "cached": 1, "rejected": 0}


@dataclass
class _DiscoveryCfg:
    unified_keyword_planner_enabled: bool = False
    fetch_batch: int = 5


def _mk_db(tmp_path: Any) -> Database:
    db = Database(tmp_path / "tiktok_kw.db")
    db.initialize()
    return db


def _result(strategy: str, count: int, *, units: int = 1) -> TiktokStrategyRunResult:
    return TiktokStrategyRunResult(
        items=[SimpleNamespace(score_threshold=0.0) for _ in range(count)],
        units_used=units,
        source_counts={strategy: count},
    )


async def test_tiktok_producer_runs_strategies_and_enqueues(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    seen: list[dict[str, Any]] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        seen.append(kwargs)
        return _result(str(kwargs["strategy"]), 2)

    pipeline = _FakeCandidatePipeline()
    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=pipeline,
    )

    result = await producer.produce_if_due(limit=5)

    assert [call["strategy"] for call in seen] == ["tiktok_feed", "tiktok_tag", "tiktok_user"]
    assert result["discovered"] == 6
    assert result["enqueued"] == 6
    assert [ctx for _, ctx in pipeline.enqueued] == [
        "tiktok_feed",
        "tiktok_tag",
        "tiktok_user",
    ]
    rows = db.conn.execute(
        "SELECT strategy, units, discovered, reason FROM tiktok_discovery_runs ORDER BY id"
    ).fetchall()
    assert [(r["strategy"], r["discovered"], r["reason"]) for r in rows] == [
        ("tiktok_feed", 2, "ok"),
        ("tiktok_tag", 2, "ok"),
        ("tiktok_user", 2, "ok"),
    ]


async def test_tiktok_producer_daily_budget_gates_strategy(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    calls: list[str] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        calls.append(str(kwargs["strategy"]))
        return _result(str(kwargs["strategy"]), 1, units=int(kwargs["unit_budget"]))

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_feed_budget=-1,
        daily_tag_budget=1,
        daily_user_budget=-1,
    )

    first = await producer.produce_if_due(limit=5)
    second = await producer.produce_if_due(limit=5)

    assert first["reason"] == "ok"
    # tiktok_user is disabled by the negative budget from the start; tiktok_tag
    # spends its single daily unit on the first run.
    assert calls == ["tiktok_tag"]
    assert second["reason"] == "budget_exhausted"


async def test_tiktok_producer_skips_when_disabled_or_pool_full(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        raise AssertionError("must not run")

    disabled = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=False,
    )
    assert (await disabled.produce_if_due(limit=5))["reason"] == "disabled"

    pooled = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=_FakeCandidatePipeline(pool_full=True),
    )
    assert (await pooled.produce_if_due(limit=5))["reason"] == "pool_full"


async def test_tiktok_keyword_fetch_marks_words_used_after_handoff(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("tiktok", ["machine learning"], "digest-1")
    coordinator = KeywordFetchCoordinator(
        database=db,
        discovery_config=_DiscoveryCfg(unified_keyword_planner_enabled=True),  # type: ignore[arg-type]
    )
    seen: list[dict[str, Any]] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        seen.append(kwargs)
        return _result(str(kwargs["strategy"]), 1)

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        candidate_pipeline=_FakeCandidatePipeline(),
        keyword_fetch=coordinator,
    )

    result = await producer.produce_if_due(limit=5)

    tag_call = next(call for call in seen if call["strategy"] == "tiktok_tag")
    assert tag_call["queries"] == ["machine learning"]
    assert tag_call["keyword_ids"] == {"machine learning": 1}
    row = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE platform = 'tiktok' AND keyword = ?",
        ("machine learning",),
    ).fetchone()
    assert row["status"] == "used"
    assert result["discovered"] == 3


async def test_tiktok_keyword_fetch_empty_store_drops_tag_strategy(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    coordinator = KeywordFetchCoordinator(
        database=db,
        discovery_config=_DiscoveryCfg(unified_keyword_planner_enabled=True),  # type: ignore[arg-type]
    )
    calls: list[str] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        calls.append(str(kwargs["strategy"]))
        return _result(str(kwargs["strategy"]), 1)

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=coordinator,
    )

    await producer.produce_if_due(limit=5)

    # No claimable words → tiktok_tag drops out; feed and user still run.
    assert calls == ["tiktok_feed", "tiktok_user"]


async def test_tiktok_keyword_fetch_marks_failed_on_strategy_error(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("tiktok", ["cooking"], "digest-2")
    coordinator = KeywordFetchCoordinator(
        database=db,
        discovery_config=_DiscoveryCfg(unified_keyword_planner_enabled=True),  # type: ignore[arg-type]
    )

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        if kwargs["strategy"] == "tiktok_tag":
            raise RuntimeError("yt-dlp exploded")
        return _result(str(kwargs["strategy"]), 0)

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=coordinator,
    )

    await producer.produce_if_due(limit=5)

    row = db.conn.execute(
        "SELECT status FROM discovery_keywords WHERE platform = 'tiktok' AND keyword = ?",
        ("cooking",),
    ).fetchone()
    assert row["status"] == "failed"


async def test_tiktok_producer_feed_budget_gates_feed_strategy(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    calls: list[str] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        calls.append(str(kwargs["strategy"]))
        # One unit = one feed pull, regardless of the items it returned.
        return _result(str(kwargs["strategy"]), 1, units=1)

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_feed_budget=1,
        daily_tag_budget=-1,
        daily_user_budget=-1,
    )

    first = await producer.produce_if_due(limit=5)
    second = await producer.produce_if_due(limit=5)

    assert first["reason"] == "ok"
    assert calls == ["tiktok_feed"]
    assert second["reason"] == "budget_exhausted"


async def test_tiktok_producer_search_strategy_budget_and_opt_in_tuple(tmp_path: Any) -> None:
    from openbiliclaw.runtime.tiktok_producer import (
        TIKTOK_DISCOVERY_STRATEGIES_WITH_SEARCH,
    )

    db = _mk_db(tmp_path)
    calls: list[str] = []

    async def discover(profile: Any, **kwargs: Any) -> TiktokStrategyRunResult:
        calls.append(str(kwargs["strategy"]))
        return _result(str(kwargs["strategy"]), 1, units=int(kwargs["unit_budget"]))

    producer = TiktokDiscoveryProducer(
        database=db,
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        strategies=TIKTOK_DISCOVERY_STRATEGIES_WITH_SEARCH,
        daily_feed_budget=-1,
        daily_search_budget=2,
        daily_tag_budget=-1,
        daily_user_budget=-1,
    )

    first = await producer.produce_if_due(limit=5)
    second = await producer.produce_if_due(limit=5)

    assert first["reason"] == "ok"
    # Search spends both daily keyword units on the first run.
    assert calls == ["tiktok_search"]
    assert second["reason"] == "budget_exhausted"


def test_tiktok_search_not_in_default_strategies() -> None:
    from openbiliclaw.runtime.tiktok_producer import (
        TIKTOK_DISCOVERY_STRATEGIES,
        TIKTOK_DISCOVERY_STRATEGIES_WITH_SEARCH,
    )

    # Search is opt-in by credential: never in the default tuple.
    assert "tiktok_search" not in TIKTOK_DISCOVERY_STRATEGIES
    assert TIKTOK_DISCOVERY_STRATEGIES_WITH_SEARCH == (
        "tiktok_feed",
        "tiktok_search",
        "tiktok_tag",
        "tiktok_user",
    )
