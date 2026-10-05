"""Pool-share fairness: production-side deficit uses own-share口径.

Spec: docs/plans/2026-07-20-pool-share-fairness-spec.md (Phase 1, invariant 2).

Before this fix ``_source_requested_count`` clamped every source's deficit by
the *global* available headroom, so once the global pool hit ``pool_target``
any under-share source reported deficit 0 and its producer never ran — even
when that source sat far below its own configured share. These tests pin the
new口径: ``available(s) < target(s)`` ⇒ deficit > 0, bounded only by raw
headroom, regardless of the global pool being full.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openbiliclaw.runtime.refresh import ContinuousRefreshController
from openbiliclaw.storage.database import Database
from tests.test_refresh_runtime import (
    _FakeDatabase,
    _FakeDiscoveryEngine,
    _FakeMemoryManager,
    _FakeRecommendationEngine,
    _FakeSoulEngine,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeBangumiProducer:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        self.calls.append(limit)
        return {"discovered": 3, "reason": "ok"}


class _FakeGitHubProducer(_FakeBangumiProducer):
    pass


def _controller(**kwargs: object) -> ContinuousRefreshController:
    base: dict[str, object] = {
        "memory_manager": _FakeMemoryManager(),
        "soul_engine": _FakeSoulEngine(),
        "discovery_engine": _FakeDiscoveryEngine(),
        "recommendation_engine": _FakeRecommendationEngine(),
    }
    base.update(kwargs)
    return ContinuousRefreshController(**base)  # type: ignore[arg-type]


def test_under_share_source_has_deficit_even_when_global_pool_is_full() -> None:
    # 全局 available=300 (bilibili:288, bangumi:12), target 300, shares 5:1.
    # bangumi own target = 50 → deficit 50-12 = 38, NOT clamped to global 0.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 12},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    assert controller._source_deficit("bangumi") == 38


async def test_bangumi_producer_runs_when_under_share_and_global_pool_full() -> None:
    producer = _FakeBangumiProducer()
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 12},
        ),
        bangumi_producer=producer,
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
        discovery_limit=30,
    )

    await controller._tick_bangumi_producer()

    assert producer.calls == [30]


async def test_github_producer_runs_when_under_share_and_global_pool_full() -> None:
    producer = _FakeGitHubProducer()
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "github": 12},
            source_raw_counts={"bilibili": 288, "github": 12},
        ),
        github_producer=producer,
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "github": 1},
        discovery_limit=30,
    )

    await controller._tick_github_producer()

    assert producer.calls == [30]


def test_source_at_or_above_share_keeps_zero_deficit() -> None:
    # bangumi at its own share (50/50) → deficit 0 even though global is short.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=250,
            source_available_counts={"bilibili": 200, "bangumi": 50},
            source_raw_counts={"bilibili": 200, "bangumi": 50},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    assert controller._source_deficit("bangumi") == 0


def test_deficit_is_clamped_by_raw_headroom() -> None:
    # bangumi wants 38 available rows but raw headroom is only 5 → deficit 5.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 288, "bangumi": 12},
            source_raw_counts={"bilibili": 288, "bangumi": 95},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    # raw ceiling = max(600, 420) = 600; bangumi raw target = 100; raw already 95
    # → raw_headroom 5 caps the 38-row available deficit.
    assert controller._source_deficit("bangumi") == 5


# ── Phase 3: gentle pool-share rebalance (evict over-share to seat under-share) ──


class _FakeRebalanceDB:
    def __init__(
        self,
        *,
        pool_count: int,
        available_by_family: dict[str, int],
        evaluated_by_family: dict[str, int],
        pending_by_family: dict[str, int] | None = None,
    ) -> None:
        self.pool_count = pool_count
        self.available_by_family = dict(available_by_family)
        self.evaluated_by_family = dict(evaluated_by_family)
        # Task 9: admission-waiting supply = evaluated + pending_eval (+ evaluating).
        self.pending_by_family = dict(pending_by_family or {})
        self.demote_calls: list[tuple[str, int]] = []

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int:
        return self.pool_count

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 3, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        return dict(self.available_by_family)

    def count_evaluated_discovery_candidates_by_source(self) -> dict[str, int]:
        return dict(self.evaluated_by_family)

    def count_admission_waiting_discovery_candidates_by_source(self) -> dict[str, int]:
        merged: dict[str, int] = dict(self.evaluated_by_family)
        for family, count in self.pending_by_family.items():
            merged[family] = merged.get(family, 0) + int(count)
        return merged

    def demote_lowest_ranked_pool_rows(self, *, source_family: str, limit: int) -> int:
        self.demote_calls.append((source_family, limit))
        self.available_by_family[source_family] = max(
            0, self.available_by_family.get(source_family, 0) - limit
        )
        self.pool_count -= limit
        return limit


# shares {bilibili:8, reddit:1, bangumi:1} over 300 → targets 240 / 30 / 30.
_REBALANCE_SHARES = {"bilibili": 8, "reddit": 1, "bangumi": 1}


def test_rebalance_demotes_three_over_share_rows_for_waiting_under_share() -> None:
    # reddit 169/30 over-share, bangumi 0/30 under-share with 5 evaluated waiting,
    # global pool full → evict exactly 3 (the per-tick cap) reddit rows.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 169, "bilibili": 131},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 3
    assert db.demote_calls == [("reddit", 3)]


def test_rebalance_is_a_noop_without_under_share_waiting_supply() -> None:
    # reddit over-share but no under-share source has evaluated supply waiting.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 169, "bilibili": 131},
        evaluated_by_family={},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    assert controller._rebalance_pool_shares() == 0
    assert db.demote_calls == []


def test_rebalance_caps_eviction_by_source_overage() -> None:
    # reddit is the only over-share family and its overage is just 2 (32/30) →
    # evict min(3, overage=2, waiting=9) = 2.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"reddit": 32, "bilibili": 200},
        evaluated_by_family={"bangumi": 9},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 2
    assert db.demote_calls == [("reddit", 2)]


def test_rebalance_skipped_when_global_pool_below_target() -> None:
    db = _FakeRebalanceDB(
        pool_count=250,
        available_by_family={"reddit": 169, "bilibili": 81},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares=dict(_REBALANCE_SHARES),
    )

    assert controller._rebalance_pool_shares() == 0
    assert db.demote_calls == []


def test_rebalance_reclaims_disabled_source_rows_absent_from_targets() -> None:
    # Task 8 (D8): only bangumi+reddit are configured (150 each), but the pool
    # still holds bilibili 141 + xiaohongshu 7 rows from now-disabled sources
    # that are absent from target_counts. Those "orphan" occupiers must count
    # as fully over-share (target 0) and be reclaimable — otherwise bangumi's
    # 150-slot deficit can never be freed (reddit is only 2 over). The single
    # most over-share source (bilibili, 141) is demoted, ≤3 per tick.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"bilibili": 141, "reddit": 152, "xiaohongshu": 7},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bangumi": 1, "reddit": 1},
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 3
    assert db.demote_calls == [("bilibili", 3)]


def test_rebalance_fillable_counts_pending_eval_not_only_evaluated() -> None:
    # Task 9 (D9): the pool is pinned full by an orphan occupier, so bangumi
    # can never REACH the evaluated stage — its supply sits in pending_eval. If
    # fillable only counted evaluated rows, rebalance would never fire and the
    # pool stays full forever (third chicken-and-egg). Counting pending_eval as
    # waiting supply lets the demote proceed; the second-round admission backfill
    # keeps the global cap intact even if a demoted row later fails eval.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"bilibili": 141, "reddit": 152, "xiaohongshu": 7},
        evaluated_by_family={},  # nothing has reached 'evaluated' yet
        pending_by_family={"bangumi": 5},  # supply is stuck in pending_eval
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bangumi": 1, "reddit": 1},
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 3
    assert db.demote_calls == [("bilibili", 3)]


def test_rebalance_still_noop_when_no_waiting_supply_of_any_stage() -> None:
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"bilibili": 141, "reddit": 152, "xiaohongshu": 7},
        evaluated_by_family={},
        pending_by_family={},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bangumi": 1, "reddit": 1},
    )

    assert controller._rebalance_pool_shares() == 0
    assert db.demote_calls == []


def test_rebalance_merges_bilibili_strategy_keys_when_reclaiming_orphan() -> None:
    # Note #2: a disabled bilibili source may surface under its four strategy
    # names rather than the "bilibili" family key. They must merge to one
    # family before computing overage.
    db = _FakeRebalanceDB(
        pool_count=300,
        available_by_family={"search": 100, "explore": 41, "reddit": 152},
        evaluated_by_family={"bangumi": 5},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bangumi": 1, "reddit": 1},
    )

    demoted = controller._rebalance_pool_shares()

    assert demoted == 3
    assert db.demote_calls == [("bilibili", 3)]


def test_count_admission_waiting_includes_pending_and_evaluating(tmp_path: Path) -> None:
    from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite

    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bangumi:{i}",
                source_platform="bangumi",
                source_strategy="bangumi",
                content_id=f"bangumi-{i}",
                title=f"b{i}",
            )
            for i in range(4)
        ]
    )
    # 2 stay pending_eval, 1 evaluating, 1 evaluated.
    db.conn.execute(
        "UPDATE discovery_candidates SET status='evaluating' WHERE candidate_key='bangumi:0'"
    )
    db.conn.execute(
        "UPDATE discovery_candidates SET status='evaluated' WHERE candidate_key='bangumi:1'"
    )
    db.conn.commit()

    # evaluated-only counter sees 1; admission-waiting counter sees all 4.
    assert db.count_evaluated_discovery_candidates_by_source().get("bangumi", 0) == 1
    assert db.count_admission_waiting_discovery_candidates_by_source().get("bangumi", 0) == 4


def test_demote_lowest_ranked_pool_rows_evicts_lowest_score_first(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    for bvid, score in (("BVhi", 0.9), ("BVmid", 0.6), ("BVlo", 0.3)):
        db.cache_content(
            bvid,
            title=bvid,
            up_name="UP",
            source="reddit",
            source_platform="reddit",
            relevance_score=score,
            relevance_reason="seed",
            pool_expression="推荐文案",
            pool_topic_label="推荐主题",
            style_key="deep_dive",
            topic_group="技术",
        )
    # A bilibili row must NOT be touched (different family).
    db.cache_content(
        "BVbili",
        title="BVbili",
        up_name="UP",
        source="search",
        source_platform="bilibili",
        relevance_score=0.1,
        pool_expression="x",
        pool_topic_label="x",
        style_key="deep_dive",
        topic_group="技术",
    )

    demoted = db.demote_lowest_ranked_pool_rows(source_family="reddit", limit=2)

    assert demoted == 2
    statuses = {
        row["bvid"]: row["pool_status"]
        for row in db.conn.execute(
            "SELECT bvid, COALESCE(pool_status, 'fresh') AS pool_status FROM content_cache"
        ).fetchall()
    }
    assert statuses["BVlo"] == "stale"
    assert statuses["BVmid"] == "stale"
    assert statuses["BVhi"] == "fresh"
    assert statuses["BVbili"] == "fresh"


def test_demote_lowest_ranked_pool_rows_ignores_temporally_stale_rows(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.cache_content(
        "BVexpired",
        title="已经过期的突发内容",
        source="reddit",
        source_platform="reddit",
        relevance_score=0.1,
        pool_expression="旧内容",
        pool_topic_label="旧主题",
        style_key="news",
        topic_group="新闻",
        published_at="2000-01-01T00:00:00+00:00",
        temporal_class="breaking",
        temporal_confidence=0.95,
        temporal_reason="价值依赖即时状态",
    )
    db.cache_content(
        "BVeligible",
        title="仍可推荐的内容",
        source="reddit",
        source_platform="reddit",
        relevance_score=0.9,
        pool_expression="有效内容",
        pool_topic_label="有效主题",
        style_key="deep_dive",
        topic_group="技术",
    )

    assert db.count_pool_candidates() == 1
    assert db.demote_lowest_ranked_pool_rows(source_family="reddit", limit=1) == 1
    assert db.count_pool_candidates() == 0
    statuses = {
        row["bvid"]: row["pool_status"]
        for row in db.conn.execute(
            "SELECT bvid, COALESCE(pool_status, 'fresh') AS pool_status FROM content_cache"
        ).fetchall()
    }
    assert statuses == {"BVexpired": "temporal_review_hold", "BVeligible": "stale"}


# ── Phase 4: change-throttled per-source deficit summary logging ──


def test_source_deficit_summary_logs_once_per_change(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    db = _FakeDatabase(
        [],
        pool_count=250,
        source_available_counts={"bilibili": 200, "bangumi": 50},
        source_raw_counts={"bilibili": 200, "bangumi": 50},
    )
    controller = _controller(
        database=db,
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )

    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.refresh"):
        controller._log_source_deficit_summary()
        controller._log_source_deficit_summary()  # unchanged → no second line

    summary_lines = [r for r in caplog.records if r.message.startswith("pool source shares:")]
    assert len(summary_lines) == 1

    # A change in the availability picture emits exactly one more line.
    db.source_available_counts = {"bilibili": 200, "bangumi": 40}
    with caplog.at_level(logging.INFO, logger="openbiliclaw.runtime.refresh"):
        controller._log_source_deficit_summary()

    summary_lines = [r for r in caplog.records if r.message.startswith("pool source shares:")]
    assert len(summary_lines) == 2


# ── Task 7: rebalance + summary reachable from the coordinator assembly ──


def test_run_pool_share_maintenance_invokes_rebalance_then_summary() -> None:
    # Both candidate-eval assemblies (legacy drain + CandidateEvalCoordinator)
    # funnel through this single controller entry point, so the Phase 3/4 hooks
    # are no longer dead code under the production (coordinator) wiring.
    controller = _controller(
        database=_FakeDatabase(
            [],
            pool_count=300,
            source_available_counts={"bilibili": 250, "bangumi": 50},
            source_raw_counts={"bilibili": 250, "bangumi": 50},
        ),
        pool_target_count=300,
        pool_source_shares={"bilibili": 5, "bangumi": 1},
    )
    calls: list[str] = []
    controller._rebalance_pool_shares = lambda: (calls.append("rebalance"), 0)[1]  # type: ignore[method-assign]
    controller._log_source_deficit_summary = lambda: calls.append("summary")  # type: ignore[method-assign]

    controller.run_pool_share_maintenance()

    assert calls == ["rebalance", "summary"]


# ── Task 9 (integration): coordinator chain — rebalance frees a slot from
#    pending-only under-share supply, then share-aware claim pulls it. ──


class _ClaimCachingEngine:
    def cache_evaluated_results(self, items: list[object]) -> int:
        return len(items)


def _seed_pending_row(db: Database, *, key: str, platform: str, strategy: str, order: int) -> None:
    from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite

    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=key,
                source_platform=platform,
                source_strategy=strategy,
                content_id=key,
                title=key,
            )
        ]
    )
    db.conn.execute(
        "UPDATE discovery_candidates "
        "SET last_seen_at = datetime('2026-07-20 00:00:00', '+' || ? || ' seconds') "
        "WHERE candidate_key = ?",
        (order, key),
    )
    db.conn.commit()


def test_chain_rebalance_from_pending_then_claim_under_share(tmp_path: Path) -> None:
    from openbiliclaw.discovery.candidate_pipeline import DiscoveryCandidatePipeline

    db = Database(tmp_path / "test.db")
    db.initialize()
    # Pool pinned full by an orphan (disabled) bilibili source absent from shares.
    for i in range(4):
        db.cache_content(
            f"BVbili{i}",
            title=f"bili {i}",
            up_name="UP",
            source="search",
            source_platform="bilibili",
            relevance_score=0.8,
            relevance_reason="seed",
            pool_expression="x",
            pool_topic_label="x",
            style_key="deep_dive",
            topic_group=f"g{i}",
        )
    # Under-share bangumi supply exists ONLY as pending_eval (never evaluated,
    # because the full pool idles the evaluator); reddit backlog is older.
    for i in range(5):
        _seed_pending_row(db, key=f"reddit:{i}", platform="reddit", strategy="reddit", order=i)
    for i in range(3):
        _seed_pending_row(
            db, key=f"bangumi:{i}", platform="bangumi", strategy="bangumi", order=10 + i
        )

    controller = _controller(
        database=db,
        pool_target_count=3,
        pool_source_shares={"bangumi": 1},
    )

    # (1) The coordinator's pre-admit hook: rebalance must fire even though the
    #     only under-share supply is pending_eval, freeing 3 orphan bilibili slots.
    controller.run_pool_share_maintenance()
    stale = db.conn.execute(
        "SELECT COUNT(*) AS c FROM content_cache WHERE pool_status = 'stale'"
    ).fetchone()["c"]
    assert stale == 3

    # (2) The coordinator's fill path: share-aware claim pulls bangumi ahead of
    #     the older reddit backlog.
    pipeline = DiscoveryCandidatePipeline(
        database=db,
        discovery_engine=_ClaimCachingEngine(),  # type: ignore[arg-type]
        pool_target_count=3,
    )
    pipeline.source_share_targets = controller._source_target_counts
    claim = pipeline.claim_batch(limit=3)
    assert claim is not None
    assert [row["source_platform"] for row in claim.rows] == ["bangumi", "bangumi", "bangumi"]
