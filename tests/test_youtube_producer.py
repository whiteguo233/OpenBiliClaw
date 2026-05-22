from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.runtime.youtube_producer import (
    YoutubeDiscoveryProducer,
    YoutubeStrategyRunResult,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "yt-producer.db")
    database.initialize()
    return database


class _Soul:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _EmptySoul:
    async def get_profile(self) -> None:
        return None


class _RaisingSoul:
    async def get_profile(self) -> None:
        raise RuntimeError("profile unavailable")


@dataclass
class _Discover:
    calls: list[tuple[str, int, int]]

    async def __call__(
        self,
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        assert profile == {"profile": "ok"}
        self.calls.append((strategy, unit_budget, result_limit))
        return YoutubeStrategyRunResult(
            items=[object()] * min(2, result_limit),
            units_used=unit_budget,
            source_counts={strategy: min(2, result_limit)},
        )


@dataclass
class _SometimesFailingDiscover:
    fail: set[str]
    calls: list[str] = field(default_factory=list)

    async def __call__(
        self,
        profile: Any,
        *,
        strategy: str,
        unit_budget: int,
        result_limit: int,
    ) -> YoutubeStrategyRunResult:
        self.calls.append(strategy)
        if strategy in self.fail:
            raise RuntimeError(f"{strategy} failed")
        return YoutubeStrategyRunResult(
            items=[object()],
            units_used=unit_budget,
            source_counts={strategy: 1},
        )


async def test_youtube_producer_produces_when_due(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        daily_search_budget=3,
        daily_trending_budget=5,
        daily_channel_budget=2,
    )

    result = await producer.produce_if_due(limit=4)

    assert result == {
        "discovered": 6,
        "source_counts": {"yt_search": 2, "yt_trending": 2, "yt_channel": 2},
        "reason": "ok",
    }
    assert discover.calls == [
        ("yt_search", 3, 4),
        ("yt_trending", 5, 4),
        ("yt_channel", 2, 4),
    ]
    assert producer.consumed_today("yt_search") == 3
    assert producer.consumed_today("yt_trending") == 5
    assert producer.consumed_today("yt_channel") == 2


async def test_youtube_producer_throttles_recent_run(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=60,
    )
    producer._last_run_at = datetime.now(UTC) - timedelta(minutes=5)

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "throttled"}
    assert discover.calls == []


async def test_youtube_producer_skips_when_daily_budget_exhausted(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        daily_channel_budget=1,
    )
    producer.record_strategy_run("yt_search", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_trending", units_used=1, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=1, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "budget_exhausted"}
    assert discover.calls == []


async def test_youtube_producer_skips_when_disabled(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        enabled=False,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "disabled"}
    assert discover.calls == []


async def test_youtube_producer_skips_without_profile(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_EmptySoul(),
        discover=discover,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}
    assert discover.calls == []


async def test_youtube_producer_skips_when_profile_raises(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_RaisingSoul(),
        discover=discover,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=5)

    assert result == {"discovered": 0, "reason": "no_profile"}
    assert discover.calls == []


async def test_youtube_producer_min_interval_zero_is_always_due(db: Database) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=0,
        daily_channel_budget=0,
    )
    producer._last_run_at = datetime.now(UTC)

    result = await producer.produce_if_due(limit=5)

    assert result["reason"] == "ok"
    assert discover.calls == [("yt_search", 1, 5)]


async def test_youtube_producer_runs_only_strategies_with_remaining_budget(
    db: Database,
) -> None:
    discover = _Discover([])
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=2,
        daily_trending_budget=4,
        daily_channel_budget=1,
    )
    producer.record_strategy_run("yt_search", units_used=2, discovered=0, reason="ok")
    producer.record_strategy_run("yt_channel", units_used=1, discovered=0, reason="ok")

    result = await producer.produce_if_due(limit=3)

    assert result["reason"] == "ok"
    assert discover.calls == [("yt_trending", 4, 3)]


async def test_youtube_producer_returns_ok_when_one_strategy_fails(
    db: Database,
) -> None:
    discover = _SometimesFailingDiscover(fail={"yt_search"})
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        daily_channel_budget=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result == {
        "discovered": 1,
        "source_counts": {"yt_trending": 1},
        "reason": "ok",
    }
    assert discover.calls == ["yt_search", "yt_trending"]
    assert producer.consumed_today("yt_search") == 0
    assert producer.consumed_today("yt_trending") == 1


async def test_youtube_producer_returns_error_when_all_strategies_fail(
    db: Database,
) -> None:
    discover = _SometimesFailingDiscover(fail={"yt_search", "yt_trending"})
    producer = YoutubeDiscoveryProducer(
        database=db,
        soul_engine=_Soul(),
        discover=discover,
        min_interval_minutes=0,
        daily_search_budget=1,
        daily_trending_budget=1,
        daily_channel_budget=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result == {"discovered": 0, "reason": "error"}
    assert discover.calls == ["yt_search", "yt_trending"]
