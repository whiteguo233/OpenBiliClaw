from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openbiliclaw.discovery.candidate_pool import (
    DiscoveryCandidateWrite,
    discovered_content_to_candidate_write,
    row_to_discovered_content,
)
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


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
                tags=["tag-a", "tag-b"],
                published_at="2026-06-01T10:00:00+00:00",
                source_context="feed",
                candidate_tier="backfill",
                raw_payload={"scope": "feed"},
            )
        ]
    )
    row = db.claim_discovery_candidates_for_eval(limit=1)[0]

    item = row_to_discovered_content(row)

    assert item.content_id == "aweme-1"
    assert item.bvid == "aweme-1"
    assert item.source_platform == "douyin"
    assert item.source_strategy == "dy-plugin-feed"
    assert item.author_name == "Creator"
    assert item.tags == ["tag-a", "tag-b"]
    assert item.published_at == "2026-06-01T10:00:00+00:00"
    assert item.candidate_tier == "backfill"
    assert item.favorite_count == 8
    assert item.collect_count == 7
    assert item.comment_count == 6
    assert item.share_count == 5
    assert item.danmaku_count == 4
    assert item.reply_count == 3
    assert item.retweet_count == 2
    assert item.bookmark_count == 1


def test_duplicate_discovery_candidate_can_backfill_publish_time(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BV1PUB",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BV1PUB",
                title="Missing time",
            )
        ]
    )
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="bilibili:BV1PUB",
                source_platform="bilibili",
                source_strategy="search",
                content_id="BV1PUB",
                title="Missing time",
                published_at="2026-06-01T10:00:00+00:00",
            )
        ]
    )

    row = db.claim_discovery_candidates_for_eval(limit=1)[0]

    assert row["published_at"] == "2026-06-01T10:00:00+00:00"


def test_discovery_candidate_row_defaults_missing_platform_to_bilibili() -> None:
    item = row_to_discovered_content(
        {
            "bvid": "BVDEFAULT",
            "title": "Default platform",
            "source_strategy": "search",
        }
    )

    assert item.source_platform == "bilibili"


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
        SELECT content_id
        FROM discovery_candidates
        WHERE source_platform = 'xiaohongshu'
        ORDER BY id ASC
        """
    ).fetchall()
    assert inserted == 5
    assert [row["content_id"] for row in rows] == ["xhs-2", "xhs-3", "xhs-4"]


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
        SELECT status, content_id
        FROM discovery_candidates
        WHERE source_platform = 'youtube'
        ORDER BY id ASC
        """
    ).fetchall()
    assert len(rows) == 3
    assert [row["status"] for row in rows].count("evaluating") == 2


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
