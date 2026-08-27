from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery.candidate_pool import (
    DiscoveryCandidateWrite,
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_weibo_alias_is_canonicalized_before_candidate_storage() -> None:
    item = DiscoveredContent(
        title="微博正文",
        content_id="post-1",
        content_url="https://weibo.com/123/post-1",
        source_platform="wb",
        source_strategy="weibo-search",
        content_type="post",
    )

    write = discovered_content_to_candidate_write(item)

    assert write.source_platform == "weibo"
    assert write.candidate_key == "weibo:post-1"


def test_enqueue_discovery_candidates_dedupes_by_source_key(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        title="XHS note",
        content_id="note-1",
        content_url="https://www.xiaohongshu.com/explore/note-1?xsec_token=abc",
        source_platform="xiaohongshu",
        source_strategy="xhs-extension-search",
        author_name="author",
    )

    first = db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    second = db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])

    assert first == 1
    assert second == 0
    counts = db.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 1


def test_claim_pending_candidates_interleaves_sources(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    writes = [
        DiscoveryCandidateWrite(
            candidate_key=f"bilibili:BV{i}",
            source_platform="bilibili",
            source_strategy="search",
            content_id=f"BV{i}",
            content_url=f"https://www.bilibili.com/video/BV{i}",
            title=f"Bili {i}",
        )
        for i in range(3)
    ] + [
        DiscoveryCandidateWrite(
            candidate_key=f"youtube:yt{i}",
            source_platform="youtube",
            source_strategy="yt_search",
            content_id=f"yt{i}",
            content_url=f"https://www.youtube.com/watch?v=yt{i}",
            title=f"YT {i}",
        )
        for i in range(3)
    ]
    db.enqueue_discovery_candidates(writes)

    rows = db.claim_discovery_candidates_for_eval(limit=4)

    assert len(rows) == 4
    assert {row["source_platform"] for row in rows} == {"bilibili", "youtube"}
    assert db.count_discovery_candidates_by_status()["evaluating"] == 4


def test_discovery_candidate_row_round_trips_to_discovered_content(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="douyin:aweme-1",
                source_platform="douyin",
                source_strategy="dy-plugin-feed",
                content_type="video",
                content_id="aweme-1",
                content_url="https://www.douyin.com/video/aweme-1",
                title="Feed item",
                author_name="Creator",
                description="Short description",
                cover_url="https://example.test/cover.jpg",
                duration=42,
                view_count=100,
                like_count=9,
                favorite_count=8,
                collect_count=7,
                comment_count=6,
                share_count=5,
                danmaku_count=4,
                reply_count=3,
                retweet_count=2,
                bookmark_count=1,
                rating_score=9.2,
                rating_count=9959,
                source_rank=1,
                tags=["tag-a", "tag-b"],
                source_context="feed",
                candidate_tier="backfill",
                raw_payload={"scope": "feed"},
            )
        ]
    )
    row = {
        **db.claim_discovery_candidates_for_eval(limit=1)[0],
        "temporal_class": "versioned",
        "temporal_confidence": 0.84,
        "temporal_reason": "内容依赖产品版本",
        "temporal_policy_version": "v2",
        "temporal_validity_mode": "version_state",
        "temporal_valid_until": "",
        "temporal_scope": "core",
        "temporal_evidence": "产品版本",
        "temporal_state": "active",
        "temporal_next_review_at": "2026-12-10T00:00:00Z",
        "temporal_evaluated_at": "2026-08-12T00:00:00Z",
        "temporal_evidence_complete": 1,
    }

    item = row_to_discovered_content(row)

    assert item.content_id == "aweme-1"
    assert item.bvid == "aweme-1"
    assert item.source_platform == "douyin"
    assert item.source_strategy == "dy-plugin-feed"
    assert item.author_name == "Creator"
    assert item.tags == ["tag-a", "tag-b"]
    assert item.candidate_tier == "backfill"
    assert item.favorite_count == 8
    assert item.collect_count == 7
    assert item.comment_count == 6
    assert item.share_count == 5
    assert item.danmaku_count == 4
    assert item.reply_count == 3
    assert item.retweet_count == 2
    assert item.bookmark_count == 1
    assert item.rating_score == 9.2
    assert item.rating_count == 9959
    assert item.source_rank == 1
    assert item.temporal_class == "versioned"
    assert item.temporal_confidence == 0.84
    assert item.temporal_reason == "内容依赖产品版本"
    assert item.temporal_policy_version == "v2"
    assert item.temporal_validity_mode == "version_state"
    assert item.temporal_valid_until == ""
    assert item.temporal_scope == "core"
    assert item.temporal_evidence == "产品版本"
    assert item.temporal_state == "active"
    assert item.temporal_next_review_at == "2026-12-10T00:00:00Z"
    assert item.temporal_evaluated_at == "2026-08-12T00:00:00Z"
    assert item.temporal_evidence_complete is True


def test_discovery_candidate_row_defaults_missing_platform_to_bilibili() -> None:
    item = row_to_discovered_content(
        {
            "bvid": "BVDEFAULT",
            "title": "Default platform",
            "source_strategy": "search",
        }
    )

    assert item.source_platform == "bilibili"


def test_catalog_metrics_round_trip_through_content_cache(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        bvid="326",
        content_id="326",
        content_url="https://bgm.tv/subject/326",
        source_platform="bangumi",
        source_strategy="bangumi-ranked",
        content_type="subject",
        title="攻壳机动队",
        rating_score=9.2,
        rating_count=9959,
        source_rank=1,
    )

    db.cache_content(item.bvid, **item.to_cache_kwargs())

    row = db.conn.execute(
        "SELECT rating_score, rating_count, source_rank FROM content_cache WHERE item_key = ?",
        ("bangumi:326",),
    ).fetchone()
    assert dict(row) == {"rating_score": 9.2, "rating_count": 9959, "source_rank": 1}


def test_enqueue_discovery_candidates_replaces_invalid_json_payload(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            {
                "candidate_key": "bilibili:BVJSON",
                "source_platform": "bilibili",
                "source_strategy": "search",
                "content_id": "BVJSON",
                "title": "Bad JSON",
                "raw_payload": "{not-json",
            }
        ]
    )

    row = db.claim_discovery_candidates_for_eval(limit=1)[0]

    assert json.loads(row["raw_payload"]) == {}


def test_initialize_resets_stale_evaluating_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVSTALE",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVSTALE",
                title="Stale",
            )
        ]
    )
    row = db.claim_discovery_candidates_for_eval(limit=1)[0]
    db.conn.execute(
        """
        UPDATE discovery_candidates
        SET claimed_at = datetime('now', '-60 minutes')
        WHERE id = ?
        """,
        (row["id"],),
    )
    db.conn.commit()
    db.close()

    reopened = Database(db_path)
    reopened.initialize()

    counts = reopened.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 1
    assert counts.get("evaluating", 0) == 0


def test_terminal_candidate_rows_are_not_rewritten_by_stale_updates(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVTERM",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVTERM",
                title="Terminal",
            )
        ]
    )
    row = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(row["id"])
    db.mark_discovery_candidate_cached(candidate_id)

    updated = db.update_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.90,
            }
        ]
    )
    db.reject_discovery_candidate(candidate_id, status="rejected_duplicate", reason="late")

    final = db.conn.execute(
        "SELECT status, eval_error FROM discovery_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert updated == 0
    assert final["status"] == "cached"
    assert final["eval_error"] == ""


def test_malformed_rereview_preserves_due_v2_temporal_evidence(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVREVIEWV2",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVREVIEWV2",
                title="这份教程所用产品版本 1.0 仍受支持",
            )
        ]
    )
    first_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(first_claim["id"])
    old_evidence = {
        "candidate_id": candidate_id,
        "status": "evaluated",
        "relevance_score": 0.9,
        "temporal_class": "versioned",
        "temporal_confidence": 0.95,
        "temporal_reason": "核心步骤依赖该产品版本",
        "temporal_policy_version": "v2",
        "temporal_validity_mode": "version_state",
        "temporal_valid_until": "",
        "temporal_scope": "core",
        "temporal_state": "active",
        "temporal_evidence": "产品版本 1.0 仍受支持",
        "temporal_next_review_at": "2000-05-01T00:00:00Z",
        "temporal_evaluated_at": "2000-01-01T00:00:00Z",
        "temporal_evidence_complete": 1,
    }
    assert db.persist_claimed_discovery_candidate_evaluations(
        [old_evidence],
        claim_token=str(first_claim["claim_token"]),
    ) == {candidate_id}
    assert db.count_discovery_candidates_by_status()["pending_eval"] == 1

    assert db.claim_discovery_candidates_for_eval(limit=1) == []
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    second_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    # Simulate a slow model call that outlives the lease created at claim.
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    malformed_review = {
        "candidate_id": candidate_id,
        "status": "evaluated",
        "relevance_score": 0.93,
        "temporal_class": "versioned",
        "temporal_confidence": 0.95,
        "temporal_reason": "",
        "temporal_policy_version": "v2",
        "temporal_validity_mode": "version_state",
        "temporal_valid_until": "",
        "temporal_scope": "core",
        "temporal_state": "active",
        "temporal_evidence": "产品版本 1.0 仍受支持",
        "temporal_next_review_at": "",
        "temporal_evaluated_at": "",
        "temporal_evidence_complete": 0,
    }
    assert db.persist_claimed_discovery_candidate_evaluations(
        [malformed_review],
        claim_token=str(second_claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        """
        SELECT status, eval_error, relevance_score, temporal_class,
               temporal_policy_version, temporal_validity_mode,
               temporal_state, temporal_evidence, temporal_next_review_at,
               temporal_evaluated_at, temporal_evidence_complete,
               temporal_review_attempts, temporal_review_retry_at
        FROM discovery_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert stored["status"] == "pending_eval"
    assert str(stored["eval_error"]).startswith("temporal_review_due:")
    assert stored["relevance_score"] == 0.93
    assert stored["temporal_class"] == "versioned"
    assert stored["temporal_policy_version"] == "v2"
    assert stored["temporal_validity_mode"] == "version_state"
    assert stored["temporal_state"] == "active"
    assert stored["temporal_evidence"] == "产品版本 1.0 仍受支持"
    assert stored["temporal_next_review_at"] == "2000-03-01T00:00:00Z"
    assert stored["temporal_evaluated_at"] == "2000-01-01T00:00:00Z"
    assert stored["temporal_evidence_complete"] == 1
    assert stored["temporal_review_attempts"] == 2
    assert stored["temporal_review_retry_at"]
    assert db.get_evaluated_discovery_candidates_for_admission(limit=10) == []
    assert db.count_discovery_candidates_by_status()["pending_eval_ready"] == 0
    assert db.claim_discovery_candidates_for_eval(limit=1) == []
    assert db.count_pool_raw_material_candidates() == 0

    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    retry_claim = db.claim_discovery_candidates_for_eval(limit=1)
    assert [int(row["id"]) for row in retry_claim] == [candidate_id]
    assert int(retry_claim[0]["temporal_review_attempts"]) == 3
    assert retry_claim[0]["temporal_review_retry_at"]


def test_temporal_review_release_and_orphan_reset_preserve_retry_lease(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVREVIEWRELEASE",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVREVIEWRELEASE",
                title="复审失败退避",
            )
        ]
    )
    candidate_id = int(
        db.conn.execute(
            "SELECT id FROM discovery_candidates WHERE candidate_key = ?",
            ("bilibili:BVREVIEWRELEASE",),
        ).fetchone()["id"]
    )
    db.conn.execute(
        """
        UPDATE discovery_candidates
        SET eval_error = 'temporal_review_due:class=current',
            temporal_review_attempts = 1,
            temporal_review_retry_at = '2000-01-01T00:00:00Z'
        WHERE id = ?
        """,
        (candidate_id,),
    )
    db.conn.commit()

    first_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    assert first_claim["temporal_review_attempts"] == 2
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    assert (
        db.reset_claimed_discovery_candidates_to_pending(
            [candidate_id],
            claim_token=str(first_claim["claim_token"]),
            reason="evaluation_response_missing",
            increment_attempts=False,
        )
        == 1
    )
    released = db.conn.execute(
        "SELECT status, eval_error, temporal_review_retry_at "
        "FROM discovery_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert released["status"] == "pending_eval"
    assert str(released["eval_error"]).startswith("temporal_review_due:")
    assert released["temporal_review_retry_at"]
    assert db.claim_discovery_candidates_for_eval(limit=1) == []

    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    second_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    assert second_claim["temporal_review_attempts"] == 3
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    assert db.reset_stale_discovery_candidate_evaluations(max_age_minutes=0) == 1
    orphaned = db.conn.execute(
        "SELECT status, eval_error, temporal_review_retry_at "
        "FROM discovery_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert orphaned["status"] == "pending_eval"
    assert str(orphaned["eval_error"]).startswith("temporal_review_due:")
    assert orphaned["temporal_review_retry_at"]
    assert db.claim_discovery_candidates_for_eval(limit=1) == []


def test_neutral_rereview_preserves_due_v1_temporal_evidence(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVREVIEWV1",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVREVIEWV1",
                title="旧的突发内容",
                published_at="2000-01-01T00:00:00Z",
            )
        ]
    )
    db.conn.execute(
        """
        UPDATE discovery_candidates
        SET temporal_class = 'breaking',
            temporal_confidence = 0.95,
            temporal_reason = 'legacy classified evidence',
            temporal_policy_version = 'v1'
        WHERE candidate_key = 'bilibili:BVREVIEWV1'
        """
    )
    db.conn.commit()
    claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(claim["id"])

    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.91,
                "temporal_class": "unknown",
                "temporal_confidence": 0.0,
                "temporal_reason": "",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "none",
                "temporal_valid_until": "",
                "temporal_scope": "none",
                "temporal_state": "unknown",
                "temporal_evidence": "",
                "temporal_next_review_at": "",
                "temporal_evaluated_at": "2001-01-01T00:00:00Z",
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        """
        SELECT status, eval_error, temporal_class, temporal_confidence,
               temporal_reason, temporal_policy_version
        FROM discovery_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert stored["status"] == "pending_eval"
    assert str(stored["eval_error"]).startswith("temporal_review_due:")
    assert stored["temporal_class"] == "breaking"
    assert stored["temporal_confidence"] == 0.95
    assert stored["temporal_reason"] == "legacy classified evidence"
    assert stored["temporal_policy_version"] == "v1"


def test_ungrounded_terminal_rereview_cannot_replace_due_grounded_evidence(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVREVIEWUNGROUNDED",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVREVIEWUNGROUNDED",
                title="这份教程所用产品版本 1.0 仍受支持",
            )
        ]
    )
    first_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(first_claim["id"])
    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.9,
                "temporal_class": "versioned",
                "temporal_confidence": 0.95,
                "temporal_reason": "核心步骤依赖该产品版本",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "version_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_state": "active",
                "temporal_evidence": "产品版本 1.0 仍受支持",
                "temporal_next_review_at": "2000-05-01T00:00:00Z",
                "temporal_evaluated_at": "2000-01-01T00:00:00Z",
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(first_claim["claim_token"]),
    ) == {candidate_id}
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    review_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]

    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "rejected_temporal_stale",
                "relevance_score": 0.94,
                "temporal_class": "breaking",
                "temporal_confidence": 0.96,
                "temporal_reason": "活动状态已经失效",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "event_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_state": "expired",
                # The excerpt is not present in this candidate's visible text.
                "temporal_evidence": "活动已经结束",
                "temporal_next_review_at": "",
                "temporal_evaluated_at": datetime.now(UTC).isoformat(),
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(review_claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        """
        SELECT status, temporal_class, temporal_validity_mode, temporal_state,
               temporal_evidence, temporal_evidence_complete
        FROM discovery_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert stored["status"] == "pending_eval"
    assert stored["temporal_class"] == "versioned"
    assert stored["temporal_validity_mode"] == "version_state"
    assert stored["temporal_state"] == "active"
    assert stored["temporal_evidence"] == "产品版本 1.0 仍受支持"
    assert stored["temporal_evidence_complete"] == 1
    assert db.get_evaluated_discovery_candidates_for_admission(limit=10) == []


@pytest.mark.parametrize(
    "rereview_kind",
    ["low_confidence", "hook_only", "ungrounded_freshness", "conditional_active"],
)
def test_weak_rereview_cannot_replace_due_candidate_evidence(
    tmp_path: Path,
    rereview_kind: str,
) -> None:
    db = Database(tmp_path / f"candidate-weak-{rereview_kind}.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVWEAKCANDIDATE",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVWEAKCANDIDATE",
                title=(
                    "活动仍在进行，报名入口仍然开放；标题写着今天最新；"
                    "如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"
                ),
            )
        ]
    )
    first_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(first_claim["id"])
    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.9,
                "temporal_class": "current",
                "temporal_confidence": 0.95,
                "temporal_reason": "核心状态需要复审",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "event_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_state": "active",
                "temporal_evidence": "活动仍在进行，报名入口仍然开放",
                "temporal_next_review_at": "2000-01-15T00:00:00Z",
                "temporal_evaluated_at": "2000-01-01T00:00:00Z",
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(first_claim["claim_token"]),
    ) == {candidate_id}
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    review_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    if rereview_kind == "low_confidence":
        rereview = {
            "temporal_class": "evergreen",
            "temporal_confidence": 0.10,
            "temporal_reason": "方法本身长期有效",
            "temporal_validity_mode": "none",
            "temporal_valid_until": "",
            "temporal_scope": "none",
            "temporal_state": "unknown",
            "temporal_evidence": "",
        }
    elif rereview_kind == "hook_only":
        rereview = {
            "temporal_class": "current",
            "temporal_confidence": 0.95,
            "temporal_reason": "只有标题钩子依赖新鲜度",
            "temporal_validity_mode": "freshness_only",
            "temporal_valid_until": "",
            "temporal_scope": "hook",
            "temporal_state": "unknown",
            "temporal_evidence": "标题写着今天最新",
        }
    elif rereview_kind == "ungrounded_freshness":
        rereview = {
            "temporal_class": "current",
            "temporal_confidence": 0.95,
            "temporal_reason": "价格依赖今天的状态",
            "temporal_validity_mode": "freshness_only",
            "temporal_valid_until": "",
            "temporal_scope": "core",
            "temporal_state": "unknown",
            "temporal_evidence": "正文不存在的今日价格",
        }
    else:
        rereview = {
            "temporal_class": "versioned",
            "temporal_confidence": 0.95,
            "temporal_reason": "版本变化后需要重新核验",
            "temporal_validity_mode": "version_state",
            "temporal_valid_until": "",
            "temporal_scope": "core",
            "temporal_state": "active",
            "temporal_evidence": ("如果支持版本发生变化，本文的迁移命令和字段契约就必须重新核验"),
        }
    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.94,
                "temporal_policy_version": "v2",
                "temporal_next_review_at": "",
                "temporal_evaluated_at": datetime.now(UTC).isoformat(),
                "temporal_evidence_complete": 1,
                **rereview,
            }
        ],
        claim_token=str(review_claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        "SELECT status, temporal_class, temporal_scope, temporal_evidence "
        "FROM discovery_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    assert dict(stored) == {
        "status": "pending_eval",
        "temporal_class": "current",
        "temporal_scope": "core",
        "temporal_evidence": "活动仍在进行，报名入口仍然开放",
    }
    assert db.get_evaluated_discovery_candidates_for_admission(limit=10) == []


def test_valid_v2_rereview_replaces_old_due_temporal_evidence(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVREVIEWFRESH",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVREVIEWFRESH",
                title="这份教程所用产品版本 1.0 仍受支持",
            )
        ]
    )
    first_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(first_claim["id"])
    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.9,
                "temporal_class": "versioned",
                "temporal_confidence": 0.95,
                "temporal_reason": "核心步骤依赖该产品版本",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "version_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_state": "active",
                "temporal_evidence": "产品版本 1.0 仍受支持",
                "temporal_next_review_at": "2000-05-01T00:00:00Z",
                "temporal_evaluated_at": "2000-01-01T00:00:00Z",
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(first_claim["claim_token"]),
    ) == {candidate_id}
    assert db.claim_discovery_candidates_for_eval(limit=1) == []
    db.conn.execute(
        "UPDATE discovery_candidates SET temporal_review_retry_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00Z", candidate_id),
    )
    db.conn.commit()
    second_claim = db.claim_discovery_candidates_for_eval(limit=1)[0]

    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "evaluated",
                "relevance_score": 0.94,
                "temporal_class": "evergreen",
                "temporal_confidence": 0.92,
                "temporal_reason": "核心方法不依赖当前版本",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "none",
                "temporal_valid_until": "",
                "temporal_scope": "none",
                "temporal_state": "unknown",
                "temporal_evidence": "",
                "temporal_next_review_at": "",
                "temporal_evaluated_at": "2001-01-01T00:00:00Z",
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(second_claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        """
        SELECT status, eval_error, temporal_class, temporal_confidence,
               temporal_reason, temporal_policy_version,
               temporal_validity_mode, temporal_evidence_complete
        FROM discovery_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert stored["status"] == "evaluated"
    assert stored["eval_error"] == ""
    assert stored["temporal_class"] == "evergreen"
    assert stored["temporal_confidence"] == 0.92
    assert stored["temporal_reason"] == "核心方法不依赖当前版本"
    assert stored["temporal_policy_version"] == "v2"
    assert stored["temporal_validity_mode"] == "none"
    assert stored["temporal_evidence_complete"] == 1
    admitted = db.get_evaluated_discovery_candidates_for_admission(limit=10)
    assert [row["id"] for row in admitted] == [candidate_id]


def test_candidate_sink_grounds_terminal_evidence_before_status_decision(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BVUNGROUNDED",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BVUNGROUNDED",
                title="普通教程正文，没有活动状态声明",
                description=("甲" * 401) + "活动已经结束",
            )
        ]
    )
    claim = db.claim_discovery_candidates_for_eval(limit=1)[0]
    candidate_id = int(claim["id"])

    assert db.persist_claimed_discovery_candidate_evaluations(
        [
            {
                "candidate_id": candidate_id,
                "status": "rejected_temporal_stale",
                "eval_error": "caller asserted stale",
                "relevance_score": 0.94,
                "temporal_class": "breaking",
                "temporal_confidence": 0.96,
                "temporal_reason": "声称活动已经结束",
                "temporal_policy_version": "v2",
                "temporal_validity_mode": "event_state",
                "temporal_valid_until": "",
                "temporal_scope": "core",
                "temporal_state": "expired",
                "temporal_evidence": "活动已经结束",
                "temporal_next_review_at": "2099-01-01T00:00:00Z",
                "temporal_evaluated_at": datetime.now(UTC).isoformat(),
                "temporal_evidence_complete": 1,
            }
        ],
        claim_token=str(claim["claim_token"]),
    ) == {candidate_id}

    stored = db.conn.execute(
        """
        SELECT status, eval_error, temporal_class, temporal_validity_mode,
               temporal_state, temporal_next_review_at,
               temporal_evidence_complete
        FROM discovery_candidates
        WHERE id = ?
        """,
        (candidate_id,),
    ).fetchone()
    assert stored["status"] == "evaluated"
    assert stored["eval_error"] == ""
    assert stored["temporal_class"] == "breaking"
    assert stored["temporal_validity_mode"] == "freshness_only"
    assert stored["temporal_state"] == "unknown"
    assert stored["temporal_next_review_at"] != "2099-01-01T00:00:00Z"
    assert stored["temporal_evidence_complete"] == 1


def test_enqueue_discovery_candidates_can_bound_pending_rows_per_source(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    writes = [
        DiscoveryCandidateWrite(
            candidate_key=f"xiaohongshu:xhs-{i}",
            source_platform="xiaohongshu",
            source_strategy="xhs-extension-search",
            content_id=f"xhs-{i}",
            title=f"XHS {i}",
        )
        for i in range(5)
    ]

    inserted = db.enqueue_discovery_candidates(writes, max_pending_per_source=3)

    rows = db.conn.execute(
        """
        SELECT content_id, status, eval_error
        FROM discovery_candidates
        WHERE source_platform = 'xiaohongshu'
        ORDER BY id ASC
        """
    ).fetchall()
    assert inserted == 5
    assert len(rows) == 5
    assert [row["content_id"] for row in rows if row["status"] == "pending_eval"] == [
        "xhs-2",
        "xhs-3",
        "xhs-4",
    ]
    assert [row["content_id"] for row in rows if row["status"] == "trimmed_capacity"] == [
        "xhs-0",
        "xhs-1",
    ]
    assert {row["eval_error"] for row in rows if row["status"] == "trimmed_capacity"} == {
        "source_raw_ceiling:xiaohongshu"
    }


def test_source_cap_counts_evaluating_rows_without_deleting_them(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"youtube:seed-{i}",
                source_platform="youtube",
                source_strategy="yt_search",
                content_id=f"seed-{i}",
                title=f"Seed {i}",
            )
            for i in range(3)
        ]
    )
    claimed = db.claim_discovery_candidates_for_eval(limit=2)
    assert len(claimed) == 2

    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"youtube:new-{i}",
                source_platform="youtube",
                source_strategy="yt_search",
                content_id=f"new-{i}",
                title=f"New {i}",
            )
            for i in range(3)
        ],
        max_pending_per_source=3,
    )

    rows = db.conn.execute(
        """
        SELECT status, content_id, claim_token, eval_error
        FROM discovery_candidates
        WHERE source_platform = 'youtube'
        ORDER BY id ASC
        """
    ).fetchall()
    assert len(rows) == 6
    assert [row["status"] for row in rows].count("evaluating") == 2
    assert [row["status"] for row in rows].count("pending_eval") == 1
    assert [row["status"] for row in rows].count("trimmed_capacity") == 3
    assert all(row["claim_token"] for row in rows if row["status"] == "evaluating")
    assert {row["eval_error"] for row in rows if row["status"] == "trimmed_capacity"} == {
        "source_raw_ceiling:youtube"
    }


def test_text_candidate_round_trips_body_text_and_content_type(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        title="A thread on systems",
        content_id="1790000000000000001",
        content_url="https://x.com/handle/status/1790000000000000001",
        source_platform="twitter",
        source_strategy="search",
        author_name="@handle",
        content_type="thread",
        body_text="1/ long-form note_tweet body ...",
    )
    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    rows = db.claim_discovery_candidates_for_eval(limit=1)
    assert rows[0]["content_type"] == "thread"
    assert rows[0]["body_text"].startswith("1/ long-form")
    back = row_to_discovered_content(rows[0])
    assert back.content_type == "thread"
    assert back.body_text.startswith("1/ long-form")


def test_candidate_write_carries_social_metrics(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    item = DiscoveredContent(
        title="Metrics note",
        content_id="xhs-metrics",
        content_url="https://www.xiaohongshu.com/explore/xhs-metrics",
        source_platform="xiaohongshu",
        source_strategy="xhs-extension-search",
        author_name="author",
        view_count=1200,
        like_count=120,
        favorite_count=110,
        collect_count=100,
        comment_count=90,
        share_count=80,
        danmaku_count=70,
        reply_count=60,
        retweet_count=50,
        bookmark_count=40,
    )

    db.enqueue_discovery_candidates([discovered_content_to_candidate_write(item)])
    row = db.claim_discovery_candidates_for_eval(limit=1)[0]
    back = row_to_discovered_content(row)

    assert row["view_count"] == 1200
    assert row["like_count"] == 120
    assert row["favorite_count"] == 110
    assert row["collect_count"] == 100
    assert row["comment_count"] == 90
    assert row["share_count"] == 80
    assert row["danmaku_count"] == 70
    assert row["reply_count"] == 60
    assert row["retweet_count"] == 50
    assert row["bookmark_count"] == 40
    assert back.view_count == 1200
    assert back.like_count == 120
    assert back.favorite_count == 110
    assert back.collect_count == 100
    assert back.comment_count == 90
    assert back.share_count == 80
    assert back.danmaku_count == 70
    assert back.reply_count == 60
    assert back.retweet_count == 50
    assert back.bookmark_count == 40


def _enqueue_one(db: Database, key: str) -> None:
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key=f"bilibili:{key}",
                source_platform="bilibili",
                source_strategy="search",
                content_id=key,
                title=key,
            )
        ]
    )


def test_reset_stale_evaluations_zero_minutes_releases_fresh_and_null_claims(
    tmp_path: Path,
) -> None:
    """minutes=0 is the process-start sweep: seconds-old restart orphans and
    un-ageable NULL claimed_at rows must all rejoin pending_eval."""
    db = Database(tmp_path / "test.db")
    db.initialize()
    _enqueue_one(db, "BVFRESH")
    _enqueue_one(db, "BVNULL")
    rows = db.claim_discovery_candidates_for_eval(limit=2)
    assert len(rows) == 2
    db.conn.execute("UPDATE discovery_candidates SET claimed_at = NULL WHERE content_id = 'BVNULL'")
    db.conn.commit()

    released = db.reset_stale_discovery_candidate_evaluations(max_age_minutes=0)

    assert released == 2
    counts = db.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 2
    assert counts.get("evaluating", 0) == 0


def test_reset_stale_evaluations_default_keeps_fresh_but_releases_null_claimed_at(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    _enqueue_one(db, "BVLIVE")
    _enqueue_one(db, "BVNULL2")
    rows = db.claim_discovery_candidates_for_eval(limit=2)
    assert len(rows) == 2
    db.conn.execute(
        "UPDATE discovery_candidates SET claimed_at = NULL WHERE content_id = 'BVNULL2'"
    )
    db.conn.commit()

    released = db.reset_stale_discovery_candidate_evaluations(max_age_minutes=30)

    # The live (seconds-old) claim survives; the NULL claimed_at row cannot
    # age out so the periodic sweep must release it.
    assert released == 1
    counts = db.count_discovery_candidates_by_status()
    assert counts["pending_eval"] == 1
    assert counts["evaluating"] == 1
