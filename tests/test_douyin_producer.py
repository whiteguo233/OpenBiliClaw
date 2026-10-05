from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.runtime.douyin_producer import (
    DouyinDiscoveryProducer,
    build_douyin_discovery_producer,
    douyin_runtime_hot_budget,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeSoulEngine:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "ok"}


class _FakePresence:
    def __init__(self, *, present: bool) -> None:
        self.present = present
        self.grace_calls: list[int] = []

    def is_present(self, grace_seconds: int) -> bool:
        self.grace_calls.append(grace_seconds)
        return self.present


class _FakeCandidatePipeline:
    def __init__(
        self,
        *,
        pool_full: bool = False,
        on_candidates_enqueued: Callable[[int], None] | None = None,
    ) -> None:
        self._pool_full = pool_full
        self.on_candidates_enqueued = on_candidates_enqueued
        self.enqueued: list[tuple[list[object], str]] = []
        self.drains: list[int] = []

    def pool_full(self) -> bool:
        return self._pool_full

    def enqueue_candidates(self, items: list[object], *, source_context: str = "") -> int:
        self.enqueued.append((list(items), source_context))
        inserted = len(items)
        if inserted > 0 and self.on_candidates_enqueued is not None:
            self.on_candidates_enqueued(inserted)
        return inserted

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
    assert options.keywords_per_run == 3


async def test_douyin_producer_skips_browser_tasks_when_extension_is_absent() -> None:
    calls = 0

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        nonlocal calls
        calls += 1
        return DouyinDiscoveryResult(items=[], cached=False, source_counts={})

    presence = _FakePresence(present=False)
    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        presence=presence,
        presence_grace_seconds=12,
        min_interval_minutes=0,
    )

    result = await producer.produce_if_due(limit=3)

    assert result == {"discovered": 0, "reason": "extension_absent"}
    assert presence.grace_calls == [12]
    assert calls == 0


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


async def test_douyin_producer_defers_to_coordinator_owned_candidate_evaluation() -> None:
    notifications: list[int] = []
    pipeline = _FakeCandidatePipeline(
        on_candidates_enqueued=notifications.append,
    )
    raw_items = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
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
        candidate_evaluation_owned_by_coordinator=True,
    )

    result = await producer.produce_if_due(limit=12)

    assert pipeline.enqueued == [(raw_items, "douyin")]
    assert notifications == [2]
    assert pipeline.drains == []
    assert result["enqueued"] == 2
    assert "cached" not in result


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

    await producer.produce_if_due(limit=12)

    assert calls[1].sources == ("search", "feed")


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


async def test_douyin_producer_throttles_empty_plugin_attempt_with_productive_ledger() -> None:
    """The productive-only DB ledger must not bypass the local attempt floor."""

    class _ProductiveOnlyLedger:
        def record_source_producer_run(self, platform: str, discovered: int) -> None:
            assert platform == "douyin"
            assert discovered > 0

        def source_producer_ran_within(self, platform: str, minutes: int) -> bool:
            assert platform == "douyin"
            assert minutes == 3
            return False

    calls = 0

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        nonlocal calls
        calls += 1
        return DouyinDiscoveryResult(
            items=[],
            cached=False,
            source_counts={},
            source_outcomes={"feed": "empty"},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=3,
        database=_ProductiveOnlyLedger(),
    )

    first = await producer.produce_if_due(limit=3)
    second = await producer.produce_if_due(limit=3)

    assert first["reason"] == "empty"
    assert second == {"discovered": 0, "reason": "throttled"}
    assert calls == 1


async def test_douyin_producer_backs_off_after_plugin_infrastructure_failure() -> None:
    calls = 0

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        nonlocal calls
        calls += 1
        return DouyinDiscoveryResult(
            items=[],
            cached=False,
            source_counts={},
            source_outcomes={"feed": "failed"},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        # A zero normal cadence still must not turn a broken extension into a
        # once-per-refresh browser-task storm.
        min_interval_minutes=0,
    )

    first = await producer.produce_if_due(limit=3)
    second = await producer.produce_if_due(limit=3)

    assert first["reason"] == "error"
    assert second == {"discovered": 0, "reason": "throttled"}
    assert calls == 1


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
            items=[SimpleNamespace(), SimpleNamespace()],
            cached=True,
            source_counts={},
            keyword_outcomes={"kw-a": "used", "kw-b": "used"},
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
        return DouyinDiscoveryResult(
            items=[],
            cached=True,
            source_counts={},
            keyword_outcomes={"kw-a": "empty"},
            source_outcomes={"search": "empty", "hot": "empty"},
        )

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


async def test_douyin_flag_on_empty_store_falls_back_to_profile_keywords(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)  # store empty
    seen: list[DouyinDiscoveryOptions] = []

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        seen.append(options)
        return DouyinDiscoveryResult(
            items=[],
            cached=True,
            source_counts={},
            source_outcomes={"search": "empty", "hot": "empty"},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(database=db, discovery_config=_DiscoveryCfg(True)),
    )
    result = await producer.produce_if_due(limit=12)
    assert result["reason"] == "empty"
    assert seen[0].keywords == ()
    assert seen[0].sources == ("search", "hot")
    assert _dy_statuses(db) == {}


class _StubCoordinator:
    """Records the claim ``n`` and hands back that many stub keywords."""

    def __init__(self, available: int = 5) -> None:
        self.available = available
        self.claim_calls: list[int] = []
        self.used: list[list[Any]] = []

    def should_claim(self) -> bool:
        return True

    def claim(self, platform: str, n: int | None = None) -> list[Any]:
        count = self.available if n is None else int(n)
        self.claim_calls.append(count)
        return [SimpleNamespace(keyword=f"kw-{i}", id=i) for i in range(min(count, self.available))]

    def mark_used(self, claimed: list[Any]) -> None:
        self.used.append(list(claimed))

    def mark_failed(self, claimed: list[Any]) -> None:  # pragma: no cover - not hit here
        pass

    def rollback(self, claimed: Any) -> None:  # pragma: no cover - not hit here
        pass


async def test_douyin_producer_claims_exactly_keywords_per_run() -> None:
    seen: list[DouyinDiscoveryOptions] = []
    coordinator = _StubCoordinator(available=5)

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        seen.append(options)
        return DouyinDiscoveryResult(
            items=[SimpleNamespace()],
            cached=True,
            source_counts={},
            keyword_outcomes={f"kw-{i}": "used" for i in range(3)},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keywords_per_run=3,
        keyword_fetch=coordinator,
    )

    result = await producer.produce_if_due(limit=12)

    assert result["reason"] == "ok"
    # Claim count is aligned with the search count — no unsearched words burned.
    assert coordinator.claim_calls == [3]
    # Exactly the claimed words flow into the strategy's seed keywords.
    assert sorted(seen[0].keywords) == ["kw-0", "kw-1", "kw-2"]
    assert seen[0].keywords_per_run == 3
    assert [item.keyword for batch in coordinator.used for item in batch] == [
        "kw-0",
        "kw-1",
        "kw-2",
    ]


async def test_douyin_producer_finalizes_each_keyword_independently(tmp_path: Any) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a", "kw-b", "kw-c"], "dig")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(
            items=[SimpleNamespace(source_strategy="dy-plugin-hot-related")],
            cached=True,
            source_counts={"dy-plugin-hot-related": 1},
            keyword_outcomes={
                "kw-a": "used",
                "kw-b": "empty",
                "kw-c": "timeout",
            },
            source_outcomes={"search": "used", "hot": "used"},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(
            database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=3)
        ),
    )

    result = await producer.produce_if_due(limit=12)

    assert result["reason"] == "ok"
    assert _dy_statuses(db) == {
        "kw-a": "used",
        "kw-b": "failed",
        "kw-c": "pending",
    }


async def test_douyin_producer_preserves_success_before_search_budget_exhaustion(
    tmp_path: Any,
) -> None:
    db = _mk_db(tmp_path)
    db.insert_pending_keywords("douyin", ["kw-a", "kw-b", "kw-c"], "dig")

    async def discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        return DouyinDiscoveryResult(
            items=[SimpleNamespace(source_strategy="dy-plugin-search")],
            cached=True,
            source_counts={"dy-plugin-search": 1},
            keyword_outcomes={
                "kw-a": "used",
                "kw-b": "budget_exhausted",
                "kw-c": "budget_exhausted",
            },
            source_outcomes={"search": "used", "hot": "empty"},
        )

    producer = DouyinDiscoveryProducer(
        soul_engine=_FakeSoulEngine(),
        discover=discover,
        enabled=True,
        min_interval_minutes=0,
        keyword_fetch=KeywordFetchCoordinator(
            database=db, discovery_config=_DiscoveryCfg(True, fetch_batch=3)
        ),
    )

    result = await producer.produce_if_due(limit=12)

    assert result["reason"] == "ok"
    assert _dy_statuses(db) == {
        "kw-a": "used",
        "kw-b": "pending",
        "kw-c": "pending",
    }


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


async def test_build_douyin_producer_enables_direct_cookie_fallback(
    monkeypatch: Any,
) -> None:
    """Runtime Douyin discovery must fall back to direct cookie when plugin tasks are empty."""
    import openbiliclaw.discovery.douyin as discovery_douyin_module
    import openbiliclaw.sources.douyin_auth as douyin_auth_module
    import openbiliclaw.sources.douyin_direct as douyin_direct_module
    import openbiliclaw.sources.douyin_plugin_search as dy_plugin_search_module

    class FakeDirectClient:
        def __init__(self, *, cookie: str) -> None:
            self.cookie = cookie

        async def __aenter__(self) -> FakeDirectClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakeService:
        def __init__(self, *, client: object, discovery_engine: object) -> None:
            self.client = client

        async def discover(
            self, profile: object, options: DouyinDiscoveryOptions
        ) -> DouyinDiscoveryResult:
            return DouyinDiscoveryResult(
                items=[],
                cached=False,
                source_counts={},
                source_outcomes={"search": "used"},
            )

    captured: dict[str, object] = {}

    class FakePluginClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(douyin_auth_module, "resolve_douyin_cookie", lambda **_: "cookie")
    monkeypatch.setattr(douyin_direct_module, "DouyinDirectClient", FakeDirectClient)
    monkeypatch.setattr(dy_plugin_search_module, "DouyinPluginSearchClient", FakePluginClient)
    monkeypatch.setattr(discovery_douyin_module, "DouyinDiscoveryService", FakeService)

    cfg = SimpleNamespace(
        data_path=Path("unused"),
        scheduler=SimpleNamespace(enabled=True),
        sources=SimpleNamespace(
            douyin=SimpleNamespace(
                enabled=True,
                mode="direct",
                cookie_env="OPENBILICLAW_DOUYIN_COOKIE",
                daily_search_budget=0,
                daily_hot_budget=0,
                daily_feed_budget=0,
                min_interval_minutes=3,
            )
        ),
    )
    database = SimpleNamespace(conn=True)

    producer = build_douyin_discovery_producer(
        config=cfg,
        database=database,
        soul_engine=_FakeSoulEngine(),
        discovery_engine=object(),
    )

    assert producer is not None
    await producer.discover(
        {"profile": "ok"},
        DouyinDiscoveryOptions(sources=("search",)),
    )
    assert captured.get("allow_direct_fallback") is True
