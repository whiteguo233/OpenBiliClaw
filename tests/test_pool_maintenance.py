"""Regression tests for atomic, availability-safe pool maintenance."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
from openbiliclaw.storage import database as database_module
from openbiliclaw.storage.database import Database


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pool-maintenance.db")
    db.initialize()
    return db


def _seed_ready(
    db: Database,
    bvid: str,
    *,
    topic_group: str,
    source: str = "search",
    source_platform: str = "bilibili",
    content_url: str | None = None,
    relevance_score: float = 0.9,
    author_name: str = "",
    published_at: str = "",
) -> None:
    db.cache_content(
        bvid,
        title=f"Ready {bvid}",
        source=source,
        source_platform=source_platform,
        content_url=content_url or f"https://www.bilibili.com/video/{bvid}",
        relevance_score=relevance_score,
        pool_expression="测试推荐文案",
        pool_topic_label="测试主题",
        style_key="tutorial",
        topic_group=topic_group,
        author_name=author_name,
        published_at=published_at,
    )


def _suppress(db: Database, bvid: str) -> None:
    db.conn.execute(
        "UPDATE content_cache SET pool_status='suppressed' WHERE bvid=?",
        (bvid,),
    )
    db.conn.commit()


def _seed_unready(
    db: Database,
    bvid: str,
    *,
    topic_group: str,
    source: str = "search",
    source_platform: str = "bilibili",
    content_url: str | None = None,
) -> None:
    db.cache_content(
        bvid,
        title=f"Raw {bvid}",
        source=source,
        source_platform=source_platform,
        content_url=content_url or f"https://www.bilibili.com/video/{bvid}",
        relevance_score=0.9,
        topic_group=topic_group,
    )


def _enqueue_candidates(db: Database, count: int, *, prefix: str = "candidate") -> list[int]:
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:{prefix}-{index}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=f"{prefix}-{index}",
                title=f"Candidate {index}",
            )
            for index in range(count)
        ]
    )
    return [
        int(row["id"])
        for row in db.conn.execute(
            "SELECT id FROM discovery_candidates WHERE candidate_key LIKE ? ORDER BY id",
            (f"bilibili:{prefix}-%",),
        ).fetchall()
    ]


def _candidate_state(db: Database) -> list[tuple[int, str, str | None, str | None]]:
    return [
        (int(row["id"]), str(row["status"]), row["claim_token"], row["eval_error"])
        for row in db.conn.execute(
            "SELECT id, status, claim_token, eval_error FROM discovery_candidates ORDER BY id"
        ).fetchall()
    ]


class _BeginImmediateFailure:
    def __init__(self) -> None:
        self.rolled_back = False
        self.closed = False

    def execute(self, sql: str, *_: Any) -> None:
        assert sql == "BEGIN IMMEDIATE"
        raise database_module.sqlite3.OperationalError("database is locked")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_user_a_shape_raw_trim_cannot_erase_sixteen_available(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(16):
        _seed_ready(db, f"BV_READY_{index:03d}", topic_group=f"ready-{index}")
    for index in range(602):
        _seed_unready(db, f"BV_RAW_{index:03d}", topic_group=f"raw-{index % 5}")

    before = db.count_pool_candidates()
    result = db.maintain_pool_inventory(
        target=600,
        raw_ceiling=600,
        source_share_quotas={"bilibili": 5},
        raw_source_share_quotas={"bilibili": 600},
        max_per_topic_group=3,
    )

    assert before == 16
    assert result.available_before == 16
    assert result.available_after >= 16
    assert result.raw_before == 618
    assert result.raw_after == 600
    assert result.rolled_back is False


def test_strict_publication_preference_counts_effective_inventory_only(tmp_path: Path) -> None:
    db = _database(tmp_path)
    _seed_ready(
        db,
        "BV_IN_RANGE",
        topic_group="date",
        published_at="2026-08-10T12:00:00Z",
    )
    _seed_ready(
        db,
        "BV_OUT_OF_RANGE",
        topic_group="date",
        published_at="2025-08-10T12:00:00Z",
    )
    db.set_publication_date_preference(
        PublicationDatePreference(
            preset="custom",
            start_date="2026-01-01",
            end_date="2026-12-31",
            weight=1.0,
        )
    )

    assert db.count_pool_candidates(max_per_topic_group=0) == 1
    assert db.conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0] == 2


def test_user_b_source_trim_defers_to_ten_available_zhihu_rows(tmp_path: Path) -> None:
    db = _database(tmp_path)
    sources = ("zhihu-creator", "zhihu-hot", "zhihu-feed", "zhihu-related")
    for index in range(10):
        _seed_ready(
            db,
            f"ZH_READY_{index:03d}",
            topic_group=f"ready-{index}",
            source=sources[index % len(sources)],
            source_platform="zhihu",
            content_url=f"https://www.zhihu.com/question/1/answer/{index + 1}",
        )
    for index in range(12):
        _seed_unready(
            db,
            f"ZH_RAW_{index:03d}",
            topic_group=f"raw-{index}",
            source=sources[index % len(sources)],
            source_platform="zhihu",
            content_url=f"https://www.zhihu.com/question/2/answer/{index + 1}",
        )

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=10,
        source_share_quotas={"zhihu": 3},
        raw_source_share_quotas={"zhihu": 10},
    )

    assert result.available_before == 10
    assert result.available_after == 10
    assert result.trimmed_raw == 12
    assert result.deferred_source_trim >= 7
    assert db.count_pool_available_candidates_by_source() == {"zhihu": 10}


def test_cross_table_raw_trim_preserves_claims_and_prefers_pending(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(4):
        _seed_ready(db, f"BV_READY_{index}", topic_group=f"ready-{index}")
    for index in range(3):
        _seed_unready(db, f"BV_RAW_{index}", topic_group=f"raw-{index}")
    evaluating_ids = _enqueue_candidates(db, 2, prefix="owned")
    claimed = db.claim_discovery_candidates_for_eval(limit=2, claim_token="owned-token")
    assert {int(row["id"]) for row in claimed} == set(evaluating_ids)
    candidate_ids = _enqueue_candidates(db, 6)
    db.conn.execute(
        "UPDATE discovery_candidates SET status='evaluated' WHERE id IN (?, ?)",
        (candidate_ids[4], candidate_ids[5]),
    )
    db.conn.commit()
    total_rows_before = int(
        db.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0]
    )

    result = db.maintain_pool_inventory(
        target=4,
        raw_ceiling=8,
        source_share_quotas={"bilibili": 4},
        raw_source_share_quotas={"bilibili": 8},
    )

    statuses = db.count_discovery_candidates_by_status()
    pending_ids = candidate_ids[:4]
    pending_placeholders = ", ".join("?" for _ in pending_ids)
    pending_statuses = {
        str(row["status"])
        for row in db.conn.execute(
            f"SELECT status FROM discovery_candidates WHERE id IN ({pending_placeholders})",
            pending_ids,
        ).fetchall()
    }
    assert result.available_after == 4
    assert result.raw_after == 8
    assert statuses["evaluating"] == 2
    assert statuses["trimmed_capacity"] >= 1
    assert pending_statuses == {"trimmed_capacity"}
    total_rows_after = int(
        db.conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0]
    )
    assert total_rows_after == total_rows_before
    assert {
        str(row["claim_token"])
        for row in db.conn.execute(
            "SELECT claim_token FROM discovery_candidates WHERE status='evaluating'"
        ).fetchall()
    } == {"owned-token"}


def test_source_queue_cap_ignores_terminal_history_and_terminalizes_excess(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    candidate_ids = _enqueue_candidates(db, 6, prefix="source-cap")
    db.conn.execute(
        "UPDATE discovery_candidates SET status='rejected_low_score' WHERE id=?",
        (candidate_ids[0],),
    )
    db.conn.commit()
    claimed = db.claim_discovery_candidates_for_eval(limit=2, claim_token="source-owner")
    assert len(claimed) == 2
    before = _candidate_state(db)

    trimmed = db.trim_discovery_candidates_for_source(
        source_platform="bilibili",
        max_pending=3,
    )

    after = _candidate_state(db)
    active_after = [row for row in after if row[1] in {"pending_eval", "evaluating", "evaluated"}]
    assert trimmed == 2
    assert len(after) == len(before)
    assert len(active_after) == 3
    assert sum(row[1] == "trimmed_capacity" for row in after) == 2
    assert {row[2] for row in after if row[1] == "evaluating"} == {"source-owner"}
    assert {row[3] for row in after if row[1] == "trimmed_capacity"} == {
        "source_raw_ceiling:bilibili"
    }


def test_source_queue_cap_does_not_trim_fresh_pending_after_large_stale_backlog(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    stale_ids = _enqueue_candidates(db, 601, prefix="stale-cap")
    pending_ids = _enqueue_candidates(db, 500, prefix="new-cap")
    db.conn.executemany(
        """
        UPDATE discovery_candidates
        SET status='evaluated', relevance_score=0.9,
            published_at='2000-01-01T00:00:00Z',
            temporal_class='breaking', temporal_confidence=0.95,
            temporal_reason='价值依赖即时状态', evaluated_at=?
        WHERE id=?
        """,
        [
            (datetime(2026, 8, 12, tzinfo=UTC).isoformat(), candidate_id)
            for candidate_id in stale_ids
        ],
    )
    db.conn.commit()

    trimmed = db.trim_discovery_candidates_for_source(
        source_platform="bilibili",
        max_pending=600,
    )

    statuses = {
        int(row["id"]): str(row["status"])
        for row in db.conn.execute("SELECT id, status FROM discovery_candidates").fetchall()
    }
    # The first 500 review-due rows are requeued for evaluation. The source
    # cap keeps all genuinely new candidates and sheds only excess review
    # retries instead of letting old inventory crowd out fresh supply.
    assert trimmed == 400
    assert all(statuses[candidate_id] == "pending_eval" for candidate_id in pending_ids)
    assert all(
        statuses[candidate_id] in {"pending_eval", "evaluated", "trimmed_capacity"}
        for candidate_id in stale_ids
    )
    assert sum(statuses[candidate_id] == "evaluated" for candidate_id in stale_ids) == 101
    assert sum(statuses[candidate_id] == "trimmed_capacity" for candidate_id in stale_ids) == 400


def test_available_surplus_only_trims_down_to_target(tmp_path: Path) -> None:
    db = _database(tmp_path)
    for index in range(16):
        _seed_ready(db, f"BV_SURPLUS_{index}", topic_group=f"ready-{index}")

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=10,
        source_share_quotas={"bilibili": 10},
        raw_source_share_quotas={"bilibili": 10},
    )

    assert result.available_before == 16
    assert result.available_after == 10
    assert result.trimmed_ready_reserve == 6
    assert result.rolled_back is False


def test_bounded_maintenance_batches_release_lock_and_eventually_converge(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    for index in range(130):
        _seed_ready(db, f"BV_BATCH_{index:03d}", topic_group=f"batch-{index}")

    results = []
    for _ in range(10):
        result = db.maintain_pool_inventory(
            target=10,
            raw_ceiling=10,
            source_share_quotas={"bilibili": 10},
            raw_source_share_quotas={"bilibili": 10},
            max_mutations=25,
        )
        results.append(result)
        assert result.mutation_count <= 25
        assert result.rolled_back is False
        if not result.has_more:
            break

    assert len(results) > 1
    assert results[-1].has_more is False
    assert results[-1].available_after == 10
    assert results[-1].raw_after == 10
    assert db.count_pool_candidates() == 10


def test_maintenance_defers_quickly_when_interactive_writer_owns_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database(tmp_path)
    _seed_ready(db, "BV_WRITER", topic_group="writer")
    writer = db.open_connection()
    writer.execute("BEGIN IMMEDIATE")
    statements: list[str] = []
    open_connection = db.open_connection

    def _open_traced_connection() -> database_module.sqlite3.Connection:
        connection = open_connection()
        connection.set_trace_callback(statements.append)
        return connection

    def _fail_on_retry_sleep(seconds: float) -> None:
        pytest.fail(f"maintenance lock deferral retried with time.sleep({seconds})")

    monkeypatch.setattr(db, "open_connection", _open_traced_connection)
    monkeypatch.setattr(database_module.time, "sleep", _fail_on_retry_sleep)
    try:
        with pytest.raises(
            database_module.PoolMaintenanceDeferredError,
            match="writer busy",
        ):
            db.maintain_pool_inventory(
                target=1,
                raw_ceiling=5,
                source_share_quotas={"bilibili": 1},
            )
    finally:
        writer.rollback()
        writer.close()

    assert (
        statements.count(f"PRAGMA busy_timeout = {database_module._MAINTENANCE_DB_BUSY_TIMEOUT_MS}")
        == 1
    )
    assert statements.count("BEGIN IMMEDIATE") == 1
    # The budget constant IS the "quickly" contract now that the wall-clock
    # assertion is gone. Without this pin, bumping the maintenance busy
    # timeout to 60s would pass this test (slowly) and ship a 60s stall on
    # every interactive-writer collision. 500ms is generous headroom over the
    # current 75ms while still being an obvious "defer, don't wait" budget.
    assert database_module._MAINTENANCE_DB_BUSY_TIMEOUT_MS <= 500


def test_invariant_failure_rolls_back_every_victim_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database(tmp_path)
    for index in range(4):
        _seed_ready(db, f"BV_READY_{index}", topic_group=f"ready-{index}")
    for index in range(2):
        _seed_unready(db, f"BV_RAW_{index}", topic_group=f"raw-{index}")
    _seed_ready(db, "BV_RECOVER_ROLLBACK", topic_group="recover-rollback")
    _suppress(db, "BV_RECOVER_ROLLBACK")
    candidate_ids = _enqueue_candidates(db, 3, prefix="rollback")
    claimed = db.claim_discovery_candidates_for_eval(limit=1, claim_token="rollback-owner")
    assert len(claimed) == 1
    content_before = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    candidates_before = _candidate_state(db)

    def _force_failure(**_: Any) -> None:
        raise database_module.PoolMaintenanceInvariantError("forced test failure")

    monkeypatch.setattr(db, "_validate_pool_maintenance_invariant", _force_failure)

    result = db.maintain_pool_inventory(
        target=5,
        raw_ceiling=4,
        source_share_quotas={"bilibili": 4},
        raw_source_share_quotas={"bilibili": 4},
    )

    content_after = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    assert result.rolled_back is True
    assert result.reason == "forced test failure"
    assert content_after == content_before
    assert _candidate_state(db) == candidates_before
    assert candidate_ids


def test_begin_immediate_failure_does_not_fabricate_zero_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _database(tmp_path)
    _seed_ready(db, "BV_LOCKED_READY", topic_group="ready")
    failing_connection = _BeginImmediateFailure()
    monkeypatch.setattr(db, "open_connection", lambda: failing_connection)
    snapshot_error = getattr(
        database_module,
        "PoolMaintenanceSnapshotUnavailableError",
        RuntimeError,
    )

    with pytest.raises(snapshot_error, match="snapshot unavailable"):
        db.maintain_pool_inventory(
            target=1,
            raw_ceiling=2,
            source_share_quotas={"bilibili": 1},
        )

    assert db.count_pool_candidates() == 1
    assert failing_connection.rolled_back is True
    assert failing_connection.closed is True


def test_recover_suppressed_exclusion_matrix_and_idempotency(tmp_path: Path) -> None:
    db = _database(tmp_path)
    eligible_rows = (
        ("BV_ELIGIBLE", "search", "bilibili", "https://www.bilibili.com/video/BV_ELIGIBLE", 0.99),
        ("ZH_ELIGIBLE", "zhihu-hot", "zhihu", "https://www.zhihu.com/question/1/answer/2", 0.98),
        (
            "XHS_ELIGIBLE",
            "xhs-search",
            "xiaohongshu",
            "https://www.xiaohongshu.com/explore/XHS_ELIGIBLE?xsec_token=ok",
            0.97,
        ),
    )
    for bvid, source, platform, url, score in eligible_rows:
        _seed_ready(
            db,
            bvid,
            topic_group=bvid,
            source=source,
            source_platform=platform,
            content_url=url,
            relevance_score=score,
        )
        _suppress(db, bvid)

    excluded = {
        "BV_RECOMMENDED": {},
        "BV_VIEWED": {},
        "BV_DISLIKED": {"feedback_type": "dislike"},
        "BV_PURGED": {"pool_status": "purged_by_dislike"},
        "BV_SHOWN": {"pool_status": "shown"},
        "BV_RECOMMENDED_AT": {"recommended_at": "2026-07-12 09:00:00"},
        "BV_MISSING_EXPRESSION": {"pool_expression": ""},
        "BV_MISSING_TOPIC": {"pool_topic_label": ""},
        "BV_MISSING_STYLE": {"style_key": ""},
        "BV_MISSING_GROUP": {"topic_group": ""},
        "BV_LOW_SCORE": {"relevance_score": 0.1},
        "BV_DELIGHT_CLAIM": {
            "delight_score": 0.99,
            "delight_reason": "surprising",
            "delight_hook": "open me",
        },
    }
    for bvid, updates in excluded.items():
        _seed_ready(db, bvid, topic_group=bvid, relevance_score=0.96)
        _suppress(db, bvid)
        if updates:
            assignments = ", ".join(f"{column}=?" for column in updates)
            db.conn.execute(
                f"UPDATE content_cache SET {assignments} WHERE bvid=?",
                (*updates.values(), bvid),
            )
    _seed_ready(
        db,
        "XHS_SELF",
        topic_group="XHS_SELF",
        source="xhs-search",
        source_platform="xiaohongshu",
        content_url="https://www.xiaohongshu.com/explore/XHS_SELF?xsec_token=ok",
        relevance_score=0.96,
        author_name="myself",
    )
    _suppress(db, "XHS_SELF")
    _seed_ready(
        db,
        "XHS_UNLINKABLE",
        topic_group="XHS_UNLINKABLE",
        source="xhs-search",
        source_platform="xiaohongshu",
        content_url="https://www.xiaohongshu.com/explore/XHS_UNLINKABLE",
        relevance_score=0.96,
    )
    _suppress(db, "XHS_UNLINKABLE")
    db.conn.commit()
    db.insert_recommendation("BV_RECOMMENDED", confidence=0.96)
    db.insert_event(
        "view",
        url="https://www.bilibili.com/video/BV_VIEWED",
        metadata={"bvid": "BV_VIEWED", "source_platform": "bilibili"},
    )

    result = db.maintain_pool_inventory(
        target=2,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 2, "zhihu": 1, "xiaohongshu": 1},
        xhs_self_nickname="myself",
    )

    statuses = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()
    }
    assert result.recovered_suppressed == 2
    assert statuses["BV_ELIGIBLE"] == "fresh"
    assert statuses["ZH_ELIGIBLE"] == "fresh"
    assert statuses["XHS_ELIGIBLE"] == "suppressed"
    assert all(statuses[bvid] != "fresh" for bvid in excluded)
    assert statuses["XHS_SELF"] == "suppressed"
    assert statuses["XHS_UNLINKABLE"] == "suppressed"

    snapshot = dict(statuses)
    repeated = db.maintain_pool_inventory(
        target=2,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 2, "zhihu": 1, "xiaohongshu": 1},
        xhs_self_nickname="myself",
    )
    repeated_statuses = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()
    }
    assert repeated.recovered_suppressed == 0
    assert repeated.available_before == repeated.available_after == 2
    assert repeated_statuses == snapshot

    xhs_recovery = db.maintain_pool_inventory(
        target=3,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 2, "zhihu": 1, "xiaohongshu": 1},
        xhs_self_nickname="myself",
    )
    assert xhs_recovery.recovered_suppressed == 1
    assert (
        db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid='XHS_ELIGIBLE'"
        ).fetchone()[0]
        == "fresh"
    )


def test_recover_suppressed_prioritizes_source_deficit(tmp_path: Path) -> None:
    db = _database(tmp_path)
    _seed_ready(db, "BV_FRESH", topic_group="fresh", relevance_score=0.99)
    for bvid, platform, source, score in (
        ("ZH_DEFICIT", "zhihu", "zhihu-hot", 0.80),
        ("BV_HIGH_1", "bilibili", "search", 0.98),
        ("BV_HIGH_2", "bilibili", "search", 0.97),
    ):
        url = (
            f"https://www.zhihu.com/question/1/answer/{bvid}"
            if platform == "zhihu"
            else f"https://www.bilibili.com/video/{bvid}"
        )
        _seed_ready(
            db,
            bvid,
            topic_group=bvid,
            source=source,
            source_platform=platform,
            content_url=url,
            relevance_score=score,
        )
        _suppress(db, bvid)

    result = db.maintain_pool_inventory(
        target=2,
        raw_ceiling=20,
        source_share_quotas={"bilibili": 1, "zhihu": 1},
    )

    assert result.recovered_suppressed == 1
    assert (
        db.conn.execute("SELECT pool_status FROM content_cache WHERE bvid='ZH_DEFICIT'").fetchone()[
            0
        ]
        == "fresh"
    )
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM content_cache WHERE bvid LIKE 'BV_HIGH_%' AND pool_status='fresh'"
        ).fetchone()[0]
        == 0
    )


def test_recover_suppressed_rebalances_source_deficit_after_each_restore(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    for bvid, source, platform, score, url in (
        (
            "BV_RECOVER_99",
            "search",
            "bilibili",
            0.99,
            "https://www.bilibili.com/video/BV_RECOVER_99",
        ),
        (
            "BV_RECOVER_98",
            "search",
            "bilibili",
            0.98,
            "https://www.bilibili.com/video/BV_RECOVER_98",
        ),
        (
            "ZH_RECOVER_70",
            "zhihu-hot",
            "zhihu",
            0.70,
            "https://www.zhihu.com/question/1/answer/70",
        ),
    ):
        _seed_ready(
            db,
            bvid,
            topic_group=bvid,
            source=source,
            source_platform=platform,
            content_url=url,
            relevance_score=score,
        )
        _suppress(db, bvid)

    result = db.maintain_pool_inventory(
        target=2,
        raw_ceiling=20,
        source_share_quotas={"bilibili": 1, "zhihu": 1},
    )

    statuses = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute("SELECT bvid, pool_status FROM content_cache").fetchall()
    }
    assert result.recovered_suppressed == 2
    assert result.available_after == 2
    assert statuses["BV_RECOVER_99"] == "fresh"
    assert statuses["BV_RECOVER_98"] == "suppressed"
    assert statuses["ZH_RECOVER_70"] == "fresh"


def test_recover_suppressed_allows_over_quota_source_to_fill_global_gap(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path)
    _seed_ready(db, "BV_FRESH", topic_group="fresh", relevance_score=0.99)
    for bvid, score in (("BV_HIGH_1", 0.98), ("BV_HIGH_2", 0.97)):
        _seed_ready(db, bvid, topic_group=bvid, relevance_score=score)
        _suppress(db, bvid)

    result = db.maintain_pool_inventory(
        target=2,
        raw_ceiling=20,
        source_share_quotas={"bilibili": 1, "zhihu": 1},
    )

    assert result.recovered_suppressed == 1
    assert result.available_after == 2
    assert (
        db.conn.execute("SELECT pool_status FROM content_cache WHERE bvid='BV_HIGH_1'").fetchone()[
            0
        ]
        == "fresh"
    )


def test_recover_suppressed_uses_seen_filtered_topic_headroom_without_oscillation(
    tmp_path: Path,
) -> None:
    """Recovery fills seen-filtered topic headroom once, then stays stable.

    The production failure behind this case alternated forever between
    restoring suppressed rows and trimming the same rows back out. Rows from a
    topic already at the public three-item window can only displace an existing
    item. A viewed row no longer occupies that canonical topic window, so the
    low-ranked fresh row in the second topic must fill the real one-item gap
    exactly once while maintenance suppresses the viewed head; the next pass
    must then be mutation-free.
    """
    db = _database(tmp_path)
    for index, score in enumerate((0.93, 0.92, 0.91)):
        _seed_ready(
            db,
            f"BV_SATURATED_FRESH_{index}",
            topic_group="saturated",
            relevance_score=score,
        )
    for index, score in enumerate((0.99, 0.98, 0.97)):
        _seed_ready(
            db,
            f"BV_WINDOW_FRESH_{index}",
            topic_group="windowed",
            relevance_score=score,
        )
    db.insert_event(
        "view",
        url="https://www.bilibili.com/video/BV_WINDOW_FRESH_2",
        metadata={"bvid": "BV_WINDOW_FRESH_2", "source_platform": "bilibili"},
    )

    suppressed_ids: list[str] = []
    for index in range(10):
        bvid = f"BV_SATURATED_SUPPRESSED_{index}"
        suppressed_ids.append(bvid)
        _seed_ready(
            db,
            bvid,
            topic_group="saturated",
            relevance_score=0.96 - index * 0.001,
        )
        _suppress(db, bvid)
    suppressed_ids.append("BV_WINDOW_TOO_LOW")
    _seed_ready(
        db,
        "BV_WINDOW_TOO_LOW",
        topic_group="windowed",
        relevance_score=0.70,
    )
    _suppress(db, "BV_WINDOW_TOO_LOW")

    before_statuses = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    assert db.count_pool_candidates() == 5
    assert db.count_pool_raw_material_candidates() == 5

    results = [
        db.maintain_pool_inventory(
            target=6,
            raw_ceiling=6,
            source_share_quotas={"bilibili": 6},
            raw_source_share_quotas={"bilibili": 6},
            max_per_topic_group=3,
        )
        for _ in range(2)
    ]

    after_statuses = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    first, second = results
    assert (first.available_before, first.available_after) == (5, 6)
    assert (first.raw_before, first.raw_after) == (5, 6)
    assert first.recovered_suppressed == 1
    assert first.mutation_count == 2
    assert (second.available_before, second.available_after) == (6, 6)
    assert (second.raw_before, second.raw_after) == (6, 6)
    assert second.recovered_suppressed == 0
    assert second.mutation_count == 0
    assert all(result.has_more is False for result in results)
    expected_statuses = dict(before_statuses)
    expected_statuses["BV_WINDOW_TOO_LOW"] = "fresh"
    expected_statuses["BV_WINDOW_FRESH_2"] = "suppressed"
    assert after_statuses == expected_statuses
    assert all(
        after_statuses[bvid] == "suppressed"
        for bvid in suppressed_ids
        if bvid != "BV_WINDOW_TOO_LOW"
    )


def test_raw_ceiling_blocks_recovery_and_stops_on_untrimmable_excess(
    tmp_path: Path,
) -> None:
    """Protected/claimed raw excess must not start a restore/trim livelock."""
    db = _database(tmp_path)
    for index in range(2):
        _seed_ready(db, f"BV_PROTECTED_{index}", topic_group=f"protected-{index}")
    candidate_ids = _enqueue_candidates(db, 2, prefix="claimed-excess")
    claimed = db.claim_discovery_candidates_for_eval(limit=2, claim_token="active-owner")
    assert {int(row["id"]) for row in claimed} == set(candidate_ids)
    _seed_ready(db, "BV_SUPPRESSED", topic_group="recoverable")
    _suppress(db, "BV_SUPPRESSED")

    result = db.maintain_pool_inventory(
        target=3,
        raw_ceiling=3,
        source_share_quotas={"bilibili": 3},
        raw_source_share_quotas={"bilibili": 3},
        max_mutations=2,
    )

    assert result.available_before == result.available_after == 2
    assert result.raw_before == result.raw_after == 4
    assert result.recovered_suppressed == 0
    assert result.mutation_count == 0
    assert result.untrimmed_raw_excess == 1
    assert result.has_more is False
    assert (
        db.conn.execute(
            "SELECT pool_status FROM content_cache WHERE bvid='BV_SUPPRESSED'"
        ).fetchone()[0]
        == "suppressed"
    )


class _SteppingDatetime(datetime):
    """Deterministic clock: returns each queued instant once, then the last."""

    times: list[datetime] = []
    calls = 0

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        value = cls.times[min(cls.calls, len(cls.times) - 1)]
        cls.calls += 1
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


_BOUNDARY_T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
_BOUNDARY_T1 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def _seed_temporal_boundary_row(db: Database, bvid: str) -> None:
    """Seed a row eligible at t0 but review-due at t1 (legacy 3-day TTL)."""
    _seed_ready(db, bvid, topic_group="boundary")
    db.conn.execute(
        """
        UPDATE content_cache
        SET temporal_class = 'breaking',
            temporal_confidence = 1.0,
            temporal_policy_version = 'v1',
            published_at = '2026-09-28T00:00:00+00:00'
        WHERE bvid = ?
        """,
        (bvid,),
    )
    db.conn.commit()


def test_availability_scan_result_depends_on_pinned_now(tmp_path: Path) -> None:
    """Document the noise mechanism: an unpinned clock flips boundary rows."""
    db = _database(tmp_path)
    _seed_ready(db, "BV_STABLE_1", topic_group="stable-1")
    _seed_ready(db, "BV_STABLE_2", topic_group="stable-2")
    _seed_temporal_boundary_row(db, "BV_BOUNDARY")

    at_t0 = db._load_available_pool_candidate_rows_on(
        db.conn,
        _now=_BOUNDARY_T0,
    )
    at_t1 = db._load_available_pool_candidate_rows_on(
        db.conn,
        _now=_BOUNDARY_T1,
    )

    assert len(at_t0) == 3
    assert len(at_t1) == 2
    assert {str(row["bvid"]) for row in at_t1} == {"BV_STABLE_1", "BV_STABLE_2"}


def test_maintenance_below_target_commits_when_clock_crosses_temporal_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool below target + a mid-transaction temporal flip must not roll back.

    Regression: before/after availability scans each took a fresh clock (and
    a fresh delight threshold), so a row crossing its temporal boundary — or
    a percentile boundary shifted by the transaction's own writes — faked an
    inventory drop and the invariant rolled every batch back forever once
    the pool sat below target (field log 2026-08: 652 consecutive rollbacks).
    Both scans now share one baseline captured at transaction start.
    """
    db = _database(tmp_path)
    _seed_ready(db, "BV_STABLE_1", topic_group="stable-1")
    _seed_ready(db, "BV_STABLE_2", topic_group="stable-2")
    _seed_temporal_boundary_row(db, "BV_BOUNDARY")
    _SteppingDatetime.times = [_BOUNDARY_T0] * 5 + [_BOUNDARY_T1] * 100
    _SteppingDatetime.calls = 0
    monkeypatch.setattr(database_module, "datetime", _SteppingDatetime)

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 100},
    )

    assert result.rolled_back is False
    assert result.available_before == 3
    assert result.available_after == 3
    # The boundary row is still measured against the transaction baseline:
    # maintenance neither drops it from the after scan nor transitions it.
    assert db.conn.execute(
        "SELECT pool_status FROM content_cache WHERE bvid = 'BV_BOUNDARY'"
    ).fetchone()[0] in (None, "fresh")


def test_maintenance_snapshots_delight_threshold_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One dynamic-threshold computation per transaction, shared by all scans."""
    db = _database(tmp_path)
    for index in range(4):
        _seed_ready(db, f"BV_THRESHOLD_{index}", topic_group=f"threshold-{index}")
    calls = 0
    original = db._dynamic_delight_threshold_on

    def _counting(conn: Any, *, default_threshold: float) -> float:
        nonlocal calls
        calls += 1
        return original(conn, default_threshold=default_threshold)

    monkeypatch.setattr(db, "_dynamic_delight_threshold_on", _counting)

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 100},
    )

    assert result.rolled_back is False
    assert calls == 1


def test_rollback_result_reports_attempted_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rolled-back result must describe the reverted batch, not fake zeros."""
    db = _database(tmp_path)
    for index in range(3):
        _seed_ready(db, f"BV_KEEP_{index}", topic_group=f"keep-{index}")
    for index in range(2):
        _seed_unready(db, f"BV_STALE_{index}", topic_group=f"stale-{index}")
    db.conn.execute(
        "UPDATE content_cache SET discovered_at = '2026-01-01 00:00:00' "
        "WHERE bvid LIKE 'BV_STALE_%'"
    )
    db.conn.commit()
    content_before = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }

    def _force_failure(**_: Any) -> None:
        raise database_module.PoolMaintenanceInvariantError("forced test failure")

    monkeypatch.setattr(db, "_validate_pool_maintenance_invariant", _force_failure)

    result = db.maintain_pool_inventory(
        target=10,
        raw_ceiling=100,
        source_share_quotas={"bilibili": 100},
    )

    assert result.rolled_back is True
    assert result.reason == "forced test failure"
    assert result.trimmed_stale == 2
    assert result.mutation_count == 2
    assert result.has_more is False
    # Inventory counters still describe the durable post-rollback state.
    assert result.available_after == result.available_before == 3
    content_after = {
        str(row["bvid"]): str(row["pool_status"])
        for row in db.conn.execute(
            "SELECT bvid, pool_status FROM content_cache ORDER BY bvid"
        ).fetchall()
    }
    assert content_after == content_before
