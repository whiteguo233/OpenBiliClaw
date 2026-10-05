"""Tests for the delight threshold policy and the delight storage queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _make_database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


def _mark_delight_ready(
    database: Database,
    bvid: str,
    *,
    delight_score: float,
    reason: str,
    hook: str,
) -> None:
    database.update_pool_copy(bvid, expression=reason, topic_label=hook)
    database.update_delight_score(
        bvid,
        delight_score=delight_score,
        delight_reason=reason,
        delight_hook=hook,
    )


def _mark_pool_copy_ready(
    database: Database,
    bvid: str,
    *,
    reason: str = "测试推荐文案",
    hook: str = "测试主题",
) -> None:
    database.update_pool_copy(bvid, expression=reason, topic_label=hook)


# ---------------------------------------------------------------------------
# Threshold behavior
# ---------------------------------------------------------------------------


def test_effective_threshold_raises_for_conservative_users() -> None:
    from openbiliclaw.recommendation.delight import effective_delight_threshold

    assert effective_delight_threshold(0.6, threshold=0.70) == 0.70
    assert effective_delight_threshold(0.2, threshold=0.70) == 0.80  # Conservative user


def test_default_thresholds_keep_evo_delight_bar_high() -> None:
    """Delight copy should only be generated for high-confidence Evo fits."""
    from openbiliclaw.recommendation.delight import (
        CONSERVATIVE_DELIGHT_THRESHOLD,
        DEFAULT_DELIGHT_THRESHOLD,
    )

    assert DEFAULT_DELIGHT_THRESHOLD == 0.75
    assert CONSERVATIVE_DELIGHT_THRESHOLD == 0.80
    # And the conservative bar must remain strictly above the default.
    assert CONSERVATIVE_DELIGHT_THRESHOLD > DEFAULT_DELIGHT_THRESHOLD


def test_score_065_rejected_at_default_threshold() -> None:
    """A 0.65 Evo relevance score must NOT receive proactive delight copy."""
    from openbiliclaw.recommendation.delight import effective_delight_threshold

    threshold = effective_delight_threshold(exploration_openness=0.5)
    assert threshold > 0.65, (
        f"effective_threshold={threshold} would admit score=0.65, but that is below "
        "the proactive delight copy bar."
    )


# ---------------------------------------------------------------------------
# Database — delight columns
# ---------------------------------------------------------------------------


def test_database_delight_columns_exist_after_init(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(content_cache)").fetchall()
    }
    assert "delight_score" in columns
    assert "delight_reason" in columns
    assert "delight_hook" in columns
    assert "delight_notified" in columns
    assert "delight_notified_at" in columns
    assert "delight_seen" in columns


def test_database_update_and_get_delight_candidate(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DL", title="惊喜内容", relevance_score=0.9)
    _mark_delight_ready(
        database,
        "BV1DL",
        delight_score=0.92,
        reason="这条会戳到你的深层需求",
        hook="深层共鸣",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.85)

    assert candidate is not None
    assert candidate["bvid"] == "BV1DL"
    assert candidate["delight_score"] == 0.92
    assert candidate["delight_reason"] == "这条会戳到你的深层需求"
    assert candidate["delight_hook"] == "深层共鸣"


def test_database_get_delight_candidate_returns_none_below_threshold(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LOW", title="普通内容", relevance_score=0.5)
    _mark_pool_copy_ready(database, "BV1LOW")
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.3,
        delight_reason="",
        delight_hook="",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.85)

    assert candidate is None


def test_database_rejects_delight_state_before_pool_copy_is_ready(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content(
        "BV1BLANK",
        title="只有分数没有文案",
        relevance_score=0.9,
        relevance_reason="评估器判断它与用户兴趣高度相关。",
    )
    persisted = database.update_delight_score(
        "BV1BLANK",
        delight_score=0.92,
        delight_reason="评估器判断它与用户兴趣高度相关。",
        delight_hook="高契合",
    )

    row = database.conn.execute(
        """
        SELECT delight_score, delight_reason, delight_hook
        FROM content_cache
        WHERE bvid = 'BV1BLANK'
        """
    ).fetchone()
    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert persisted is False
    assert row is not None
    assert float(row["delight_score"]) == 0.0
    assert row["delight_reason"] == ""
    assert row["delight_hook"] == ""
    assert candidate is None
    assert database.count_delight_candidates(min_delight_score=0.70) == 0


def test_database_rejects_evaluator_reason_after_pool_copy_is_ready(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1WRONGCOPY", title="已有正式推荐词", relevance_score=0.92)
    _mark_pool_copy_ready(
        database,
        "BV1WRONGCOPY",
        reason="这是正式推荐理由。",
        hook="正式主题",
    )

    persisted = database.update_delight_score(
        "BV1WRONGCOPY",
        delight_score=0.92,
        delight_reason="评估器内部判断",
        delight_hook="高契合",
    )

    row = database.conn.execute(
        """
        SELECT delight_score, delight_reason, delight_hook
        FROM content_cache
        WHERE bvid = 'BV1WRONGCOPY'
        """
    ).fetchone()
    assert persisted is False
    assert row is not None
    assert float(row["delight_score"]) == 0.0
    assert row["delight_reason"] == ""
    assert row["delight_hook"] == ""


def test_database_get_delight_candidate_excludes_suppressed_pool_items(
    tmp_path: Path,
) -> None:
    """Suppressed items must NOT surface as delight.

    A previous version of this test asserted the opposite (suppressed
    delight items are still surfaced, with the rationale "虽然普通池压
    掉了，但这条对你还是很可能是惊喜"). In practice this caused 20
    stale "delights" to appear on every popup reload — items that had
    been trimmed out by topic-group caps or source-quota balancing
    months ago, with delight scores baked under earlier looser
    calibrations. After the v0.3.32 dislike/threshold recalibration,
    9991 such ghosts were sitting on the suppressed graveyard.
    Restricting to ``pool_status IN ('fresh', 'shown')`` keeps delight
    in lockstep with the active pool.
    """
    database = _make_database(tmp_path)
    database.cache_content(
        "BV1SUPPRESS",
        title="被普通池压下去的惊喜内容",
        relevance_score=0.92,
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1SUPPRESS",),
    )
    database.conn.commit()
    _mark_delight_ready(
        database,
        "BV1SUPPRESS",
        delight_score=0.91,
        reason="历史评分残留，应当被新规则过滤。",
        hook="压箱惊喜",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert candidate is None


def test_database_mark_delight_notified(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DLN", title="已通知", relevance_score=0.9)
    _mark_delight_ready(
        database,
        "BV1DLN",
        delight_score=0.95,
        reason="reason",
        hook="hook",
    )
    database.mark_delight_notified("BV1DLN")

    # Should not appear since it's already notified
    candidate = database.get_delight_candidate(min_delight_score=0.85)
    assert candidate is None


def test_database_delivered_but_unseen_delight_still_rehydrates(tmp_path: Path) -> None:
    """A delivered-only delight (background /delight/sent ack) must remain
    visible to popup re-hydration until the user actually views/dismisses it.

    Regression: the extension service worker ACKs every WebSocket
    ``delight.candidate`` through ``/api/delight/sent``, which used to set
    ``delight_notified=1`` and made ``pending-batch`` return nothing even
    though the user never saw the card.
    """
    database = _make_database(tmp_path)
    bvid = "BV1SENTBUTUNSEEN"
    database.cache_content(bvid, title="已推送未看", relevance_score=0.9)
    _mark_delight_ready(
        database,
        bvid,
        delight_score=0.95,
        reason="reason",
        hook="hook",
    )

    # Initial state: visible to both proactive push and re-hydration.
    assert database.get_delight_candidates(min_delight_score=0.85)[0]["bvid"] == bvid
    assert (
        database.get_delight_candidates(min_delight_score=0.85, include_delivered=True)[0]["bvid"]
        == bvid
    )

    # /api/delight/sent: only marks delivered, not seen.
    database.mark_delight_notified(bvid)
    assert database.get_delight_candidates(min_delight_score=0.85, include_delivered=True) != []
    # Proactive push/CLI still excludes delivered items.
    assert database.get_delight_candidates(min_delight_score=0.85) == []

    # User actually views the candidate -> it leaves re-hydration as well.
    database.mark_delight_viewed(bvid)
    assert database.get_delight_candidates(min_delight_score=0.85, include_delivered=True) == []


def test_mark_delight_viewed_and_seen_set_delight_seen(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    bvid = "BV1VIEWSEEN"
    database.cache_content(bvid, title="查看/关闭", relevance_score=0.9)
    _mark_delight_ready(
        database,
        bvid,
        delight_score=0.95,
        reason="reason",
        hook="hook",
    )

    database.mark_delight_viewed(bvid)
    row = database.conn.execute(
        "SELECT delight_notified, delight_seen FROM content_cache WHERE bvid = ?",
        (bvid,),
    ).fetchone()
    assert row is not None
    assert row["delight_notified"] == 1
    assert row["delight_seen"] == 1

    database.mark_delight_seen(bvid)
    row = database.conn.execute(
        "SELECT delight_notified, delight_seen FROM content_cache WHERE bvid = ?",
        (bvid,),
    ).fetchone()
    assert row is not None
    assert row["delight_notified"] == 1
    assert row["delight_seen"] == 1


def test_database_delight_candidates_skip_feedbacked_items(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LIKE", title="已反馈", relevance_score=0.9)
    database.cache_content("BV1FRESH", title="新惊喜", relevance_score=0.9)
    database.cache_content("BV1HATE", title="已点不感兴趣", relevance_score=0.9)
    _mark_delight_ready(
        database,
        "BV1LIKE",
        delight_score=0.95,
        reason="liked reason",
        hook="liked hook",
    )
    _mark_delight_ready(
        database,
        "BV1FRESH",
        delight_score=0.94,
        reason="fresh reason",
        hook="fresh hook",
    )
    _mark_delight_ready(
        database,
        "BV1HATE",
        delight_score=0.93,
        reason="disliked reason",
        hook="disliked hook",
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'like' WHERE bvid = ?",
        ("BV1LIKE",),
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'dislike' WHERE bvid = ?",
        ("BV1HATE",),
    )
    database.conn.commit()

    candidates = database.get_delight_candidates(min_delight_score=0.85)

    assert [row["bvid"] for row in candidates] == ["BV1FRESH"]
    assert database.count_delight_candidates(min_delight_score=0.85) == 1


def test_delight_claim_threshold_floor_in_sync() -> None:
    """storage mirrors DEFAULT_DELIGHT_THRESHOLD as the dynamic floor.

    Storage stays leaf-only and receives profile-aware threshold floors
    from callers where available, so this constant only locks the
    default fallback floor.
    """
    from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
    from openbiliclaw.storage.database import _DELIGHT_CLAIM_MIN_SCORE

    assert _DELIGHT_CLAIM_MIN_SCORE == DEFAULT_DELIGHT_THRESHOLD


def _seed_delight_scored_pool(
    database: Database,
    count: int,
    *,
    relevance_score: Callable[[int], float],
    delight_score: Callable[[int], float],
    prefix: str,
) -> None:
    for index in range(count):
        bvid = f"BV1{prefix}{index:04d}"
        database.cache_content(
            bvid,
            title=f"{prefix} {index}",
            relevance_score=relevance_score(index),
        )
        _mark_delight_ready(
            database,
            bvid,
            delight_score=delight_score(index),
            reason=f"{prefix} 推荐文案 {index}",
            hook=f"{prefix} 主题 {index}",
        )


def test_database_delight_candidates_include_liked_keeps_liked_rows(tmp_path: Path) -> None:
    """Queue re-hydration must keep liked delights visible (v0.3.63 contract).

    ``include_liked=True`` is what /api/delight/pending-batch passes so a
    liked card survives popup reopen; disliked rows stay excluded either way.
    """
    database = _make_database(tmp_path)
    database.cache_content("BV1LIKE", title="已喜欢", relevance_score=0.9)
    database.cache_content("BV1FRESH", title="新惊喜", relevance_score=0.9)
    database.cache_content("BV1HATE", title="已点不感兴趣", relevance_score=0.9)
    _mark_delight_ready(
        database,
        "BV1LIKE",
        delight_score=0.95,
        reason="liked reason",
        hook="liked hook",
    )
    _mark_delight_ready(
        database,
        "BV1FRESH",
        delight_score=0.94,
        reason="fresh reason",
        hook="fresh hook",
    )
    _mark_delight_ready(
        database,
        "BV1HATE",
        delight_score=0.93,
        reason="disliked reason",
        hook="disliked hook",
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'like' WHERE bvid = ?",
        ("BV1LIKE",),
    )
    database.conn.execute(
        "UPDATE content_cache SET feedback_type = 'dislike' WHERE bvid = ?",
        ("BV1HATE",),
    )
    database.conn.commit()

    candidates = database.get_delight_candidates(min_delight_score=0.85, include_liked=True)

    assert [row["bvid"] for row in candidates] == ["BV1LIKE", "BV1FRESH"]

    # Explicit dismissal still removes a liked delight from re-hydration.
    database.mark_delight_notified("BV1LIKE")
    candidates = database.get_delight_candidates(min_delight_score=0.85, include_liked=True)
    assert [row["bvid"] for row in candidates] == ["BV1FRESH"]


def test_database_count_delight_candidates(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1A", title="A", relevance_score=0.9)
    database.cache_content("BV1B", title="B", relevance_score=0.8)
    _mark_delight_ready(
        database,
        "BV1A",
        delight_score=0.92,
        reason="r1",
        hook="h1",
    )
    _mark_delight_ready(
        database,
        "BV1B",
        delight_score=0.88,
        reason="r2",
        hook="h2",
    )

    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 2

    database.mark_delight_notified("BV1A")
    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 1

    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1B",),
    )
    database.conn.commit()
    assert database.get_delight_candidates(min_delight_score=0.85) == []
    assert database.count_delight_candidates(min_delight_score=0.85) == 0


def test_database_delight_paths_exclude_durable_seen_items(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    bvid = "BV1SEENDELIGHT"
    database.cache_content(bvid, title="已经看过", relevance_score=0.94)
    _mark_delight_ready(
        database,
        bvid,
        delight_score=0.94,
        reason="旧惊喜文案",
        hook="旧惊喜主题",
    )

    database.mark_items_seen("bilibili", [bvid])

    assert database.get_delight_candidates(min_delight_score=0.75) == []
    assert database.count_delight_candidates(min_delight_score=0.75) == 0
    assert (
        database.get_pool_candidates_needing_delight_score(
            limit=10,
            min_delight_score_for_reason=0.95,
        )
        == []
    )


def test_mark_delight_seen_records_cross_source_identity_and_consumes_queue(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    storage_key = "youtube:video-42"
    database.cache_content(
        storage_key,
        title="跨平台惊喜",
        source="youtube-search",
        source_platform="youtube",
        content_id="video-42",
        relevance_score=0.92,
    )
    _mark_delight_ready(
        database,
        storage_key,
        delight_score=0.92,
        reason="值得看看",
        hook="新方向",
    )

    assert database.mark_delight_seen(storage_key) is True

    assert "youtube:video-42" in database.get_seen_content_keys()
    assert database.get_delight_candidates(min_delight_score=0.75) == []
    notified = database.conn.execute(
        "SELECT delight_notified FROM content_cache WHERE bvid = ?",
        (storage_key,),
    ).fetchone()
    assert notified is not None
    assert notified["delight_notified"] == 1


def test_database_dynamic_delight_threshold_keeps_floor_before_min_sample_size(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        149,
        relevance_score=lambda index: 0.91 + (index * 0.0001),
        delight_score=lambda index: 0.91 + (index * 0.0001),
        prefix="SMPL",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_uses_top_ten_percent_for_homogeneous_high_pool(
    tmp_path: Path,
) -> None:
    """A homogeneous above-floor pool must still use the Top 10% boundary.

    The fixed floor would make every scored row delight-claimed and starve
    the regular feed (issue #220). With scores 0.91..0.9259 the boundary is
    the 16th-highest score (0.9244), keeping the other 144 rows servable.
    """
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.91 + (index * 0.0001),
        delight_score=lambda index: 0.91 + (index * 0.0001),
        prefix="HOMO",
    )

    assert database.dynamic_delight_threshold(default_threshold=0.75) == pytest.approx(0.9244)


def test_database_dynamic_delight_threshold_keeps_floor_for_homogeneous_low_pool(
    tmp_path: Path,
) -> None:
    """A homogeneous below-floor pool keeps the floor: percentile is a no-op."""
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.30 + (index * 0.0001),
        delight_score=lambda index: 0.30 + (index * 0.0001),
        prefix="HOML",
    )

    assert database.dynamic_delight_threshold(default_threshold=0.75) == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_uses_delight_top_ten_percent_boundary(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.30 + (index * 0.004),
        delight_score=lambda index: 0.30 + (index * 0.004),
        prefix="DYNB",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.876)


def test_database_dynamic_delight_threshold_uses_delight_score_not_relevance_score(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.60 + (index * 0.001),
        delight_score=lambda index: 0.35 + (index * 0.004),
        prefix="DSRC",
    )

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.926)


def test_database_dynamic_delight_threshold_never_drops_below_default(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.01 + (index * 0.003),
        delight_score=lambda index: 0.01 + (index * 0.003),
        prefix="LOWB",
    )

    assert database.dynamic_delight_threshold(default_threshold=0.75) == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_ignores_legacy_uncopied_scores(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    for index in range(160):
        bvid = f"BV1LEGACY{index:04d}"
        score = 0.20 + (index * 0.005)
        database.cache_content(bvid, title=bvid, relevance_score=score)
        database.conn.execute(
            "UPDATE content_cache SET delight_score = ? WHERE bvid = ?",
            (score, bvid),
        )
    database.conn.commit()

    threshold = database.dynamic_delight_threshold(default_threshold=0.75)

    assert threshold == pytest.approx(0.75)


def test_database_dynamic_delight_threshold_bad_default_uses_claim_floor(
    tmp_path: Path,
) -> None:
    from openbiliclaw.storage.database import _DELIGHT_CLAIM_MIN_SCORE

    database = _make_database(tmp_path)

    assert database.dynamic_delight_threshold(default_threshold="bad") == pytest.approx(
        _DELIGHT_CLAIM_MIN_SCORE
    )


def test_pool_candidates_use_dynamic_delight_claim_threshold(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for index in range(40):
        score = 0.50 + (index * 0.01)
        bvid = f"BV1BASE{index:02d}"
        database.cache_content(bvid, title=bvid, relevance_score=score)
        database.conn.execute(
            """
            UPDATE content_cache
            SET pool_expression = 'copy',
                pool_topic_label = 'topic',
                style_key = 'deep_focus',
                topic_group = 'base'
            WHERE bvid = ?
            """,
            (bvid,),
        )

    database.cache_content("BV1MID", title="mid delight", relevance_score=0.72)
    database.conn.execute(
        """
        UPDATE content_cache
        SET pool_expression = 'copy',
            pool_topic_label = 'topic',
            style_key = 'deep_focus',
            topic_group = 'mid'
        WHERE bvid = 'BV1MID'
        """
    )
    database.update_delight_score(
        "BV1MID",
        delight_score=0.72,
        delight_reason="ready",
        delight_hook="hook",
    )
    database.conn.commit()

    rows = database.get_pool_candidates(limit=50, max_per_topic_group=0)

    assert "BV1MID" in [row["bvid"] for row in rows]


def test_pool_candidates_keep_homogeneous_high_delight_pool_servable(
    tmp_path: Path,
) -> None:
    """Regression for issue #220: homogeneous above-floor delight scores
    must not claim every row and starve the regular pool.
    """
    database = _make_database(tmp_path)
    _seed_delight_scored_pool(
        database,
        160,
        relevance_score=lambda index: 0.91 + (index * 0.0001),
        delight_score=lambda index: 0.91 + (index * 0.0001),
        prefix="HOMO",
    )
    database.conn.execute(
        """
        UPDATE content_cache
        SET style_key = 'deep_focus',
            topic_group = 'base'
        WHERE bvid LIKE 'BV1HOMO%'
        """
    )
    database.conn.commit()

    rows = database.get_pool_candidates(limit=200, max_per_topic_group=0)

    # Top 10% (16 rows, score >= 0.9244) are reserved for delight; the
    # remaining 144 rows stay available for the regular feed.
    assert len(rows) == 144
    assert database.count_pool_candidates(max_per_topic_group=0) == 144


def test_database_get_pool_candidates_needing_delight_score(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    # Unscored item (delight_score = 0.0 default)
    database.cache_content("BV1UNSCORE", title="Unscored", relevance_score=0.8)
    _mark_pool_copy_ready(database, "BV1UNSCORE")
    # Already scored item
    database.cache_content("BV1SCORED", title="Scored", relevance_score=0.7)
    _mark_pool_copy_ready(database, "BV1SCORED")
    database.update_delight_score(
        "BV1SCORED",
        delight_score=0.5,
        delight_reason="",
        delight_hook="",
    )

    candidates = database.get_pool_candidates_needing_delight_score(limit=10)

    bvids = [c["bvid"] for c in candidates]
    assert "BV1UNSCORE" in bvids
    assert "BV1SCORED" not in bvids


def test_database_delight_scoring_backlog_excludes_uncopied_rows(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1WAITCOPY", title="推荐词仍在生成", relevance_score=0.91)
    database.cache_content("BV1COPYREADY", title="推荐词已完成", relevance_score=0.90)
    _mark_pool_copy_ready(database, "BV1COPYREADY")

    candidates = database.get_pool_candidates_needing_delight_score(limit=10)

    assert [candidate["bvid"] for candidate in candidates] == ["BV1COPYREADY"]


def test_database_get_pool_candidates_needing_delight_score_includes_high_score_backfill(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1READY", title="Ready", relevance_score=0.9)
    _mark_pool_copy_ready(database, "BV1READY", reason="已经有解释", hook="已完成")
    database.update_delight_score(
        "BV1READY",
        delight_score=0.77,
        delight_reason="已经有解释",
        delight_hook="已完成",
    )
    database.cache_content("BV1BACKFILL", title="Backfill", relevance_score=0.88)
    _mark_pool_copy_ready(database, "BV1BACKFILL", reason="等待补齐", hook="待完成")
    database.update_delight_score(
        "BV1BACKFILL",
        delight_score=0.76,
        delight_reason="",
        delight_hook="",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1BACKFILL",),
    )
    database.conn.commit()
    database.cache_content("BV1LOW", title="Low", relevance_score=0.7)
    _mark_pool_copy_ready(database, "BV1LOW", reason="普通推荐", hook="普通")
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.55,
        delight_reason="",
        delight_hook="",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.75,
    )

    bvids = [c["bvid"] for c in candidates]
    assert "BV1BACKFILL" in bvids
    assert "BV1READY" in bvids
    assert "BV1LOW" in bvids


def test_database_get_pool_candidates_needing_delight_score_includes_stale_score_backfill(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1STALE", title="Stale", relevance_score=0.92)
    _mark_pool_copy_ready(
        database,
        "BV1STALE",
        reason="旧阈值留下的理由",
        hook="旧钩子",
    )
    database.update_delight_score(
        "BV1STALE",
        delight_score=0.72,
        delight_reason="旧阈值留下的理由",
        delight_hook="旧钩子",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1STALE" in [c["bvid"] for c in candidates]


def test_database_get_pool_candidates_needing_delight_score_prioritizes_current_relevance(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1LOWOLD", title="Old high score", relevance_score=0.60)
    _mark_pool_copy_ready(database, "BV1LOWOLD", reason="旧高分", hook="旧")
    database.update_delight_score(
        "BV1LOWOLD",
        delight_score=0.99,
        delight_reason="旧高分",
        delight_hook="旧",
    )
    database.cache_content("BV1HIGHNEW", title="Current high score", relevance_score=0.92)
    _mark_pool_copy_ready(database, "BV1HIGHNEW", reason="旧低分", hook="旧")
    database.update_delight_score(
        "BV1HIGHNEW",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=1,
        min_delight_score_for_reason=0.91,
    )

    assert [c["bvid"] for c in candidates] == ["BV1HIGHNEW"]


def test_database_get_pool_candidates_needing_delight_score_includes_shown_stale_rows(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1SHOWN", title="Shown stale", relevance_score=0.92)
    _mark_pool_copy_ready(database, "BV1SHOWN", reason="旧低分", hook="旧")
    database.update_delight_score(
        "BV1SHOWN",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown' WHERE bvid = ?",
        ("BV1SHOWN",),
    )
    database.conn.commit()

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1SHOWN" in [c["bvid"] for c in candidates]


def test_database_get_pool_candidates_needing_delight_score_includes_shown_history_rows(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1HISTORY", title="Shown history stale", relevance_score=0.92)
    _mark_pool_copy_ready(database, "BV1HISTORY", reason="旧低分", hook="旧")
    database.update_delight_score(
        "BV1HISTORY",
        delight_score=0.72,
        delight_reason="旧低分",
        delight_hook="旧",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'shown' WHERE bvid = ?",
        ("BV1HISTORY",),
    )
    database.insert_recommendation(
        "BV1HISTORY",
        confidence=0.92,
        expression="普通推荐历史",
        topic="普通推荐",
        presented=1,
    )
    database.conn.commit()

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.91,
    )

    assert "BV1HISTORY" in [c["bvid"] for c in candidates]


# ---------------------------------------------------------------------------
# Delight owns the threshold policy only — no scorer of its own
# ---------------------------------------------------------------------------


def test_delight_module_no_longer_exposes_llm_score_compat_api() -> None:
    import openbiliclaw.recommendation.delight as delight

    assert not hasattr(delight, "LLMDelightScorer")
    assert not hasattr(delight, "DelightLLMResult")
    assert not hasattr(delight, "_extract_delight_entries")


def test_delight_module_exposes_threshold_policy_only() -> None:
    """v0.3.174+: the standalone embedding scorer is gone for good.

    ``delight_score`` is produced by ``precompute_delight_scores()`` reusing
    Evo's ``relevance_score``; catalogue signals reach the score through the
    shared evaluator prompt. A parallel weighted scorer here drifted out of
    the pipeline and sat unreferenced — don't resurrect it.
    """
    import openbiliclaw.recommendation.delight as delight

    for removed in (
        "DelightScorer",
        "DelightSignals",
        "DelightWeights",
        "SupportsDelightCandidate",
        "SupportsRecommendationSignalStore",
    ):
        assert not hasattr(delight, removed), f"{removed} was removed as dead code"

    # The threshold policy is the module's whole public surface.
    assert callable(delight.effective_delight_threshold)
    assert isinstance(delight.DEFAULT_DELIGHT_THRESHOLD, float)
    assert isinstance(delight.CONSERVATIVE_DELIGHT_THRESHOLD, float)


def test_get_pool_candidates_filters_by_min_relevance(tmp_path: Path) -> None:
    """relevance_score gate cuts weak-fit items before delight backfill."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HIGH", title="High fit", relevance_score=0.85)
    database.cache_content("BV1MED", title="Moderate", relevance_score=0.60)
    database.cache_content("BV1LOW", title="Weak", relevance_score=0.40)
    for bvid in ("BV1HIGH", "BV1MED", "BV1LOW"):
        _mark_pool_copy_ready(database, bvid)

    rows = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_relevance_score=0.55,
    )
    bvids = {r["bvid"] for r in rows}
    assert "BV1HIGH" in bvids
    assert "BV1MED" in bvids
    assert "BV1LOW" not in bvids


def test_get_pool_candidates_default_min_relevance_is_055(tmp_path: Path) -> None:
    """v0.3.35: default gate must remain 0.55 (any change is a behaviour
    swing affecting how many candidates are checked per cycle)."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HALF", title="Right at edge", relevance_score=0.54)
    database.cache_content("BV1OVER", title="Just over", relevance_score=0.56)
    _mark_pool_copy_ready(database, "BV1HALF")
    _mark_pool_copy_ready(database, "BV1OVER")

    # No min_relevance_score passed — uses default
    rows = database.get_pool_candidates_needing_delight_score(limit=10)
    bvids = {r["bvid"] for r in rows}
    assert "BV1OVER" in bvids
    assert "BV1HALF" not in bvids


def test_delight_serving_uses_exact_explore_relaxed_admission_floor(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    for bvid, source in (("BVEXP", "explore"), ("BVTREND", "trending")):
        database.cache_content(bvid, title=bvid, source=source, relevance_score=0.58)
        _mark_delight_ready(
            database,
            bvid,
            delight_score=0.90,
            reason="值得看看",
            hook="新方向",
        )

    assert [
        row["bvid"] for row in database.get_delight_candidates(min_delight_score=0.75, limit=10)
    ] == ["BVEXP"]
