"""Tests for recommendation pool curator."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.curator import (
    FeedbackSignals,
    PoolCurator,
    ScoringContext,
)
from openbiliclaw.storage.database import Database


def _make_db() -> tuple[Database, str]:
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    return db, tmpdir


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Freshness scoring
# ---------------------------------------------------------------------------


def test_freshness_score_new_content_is_high() -> None:
    now = _now()
    score = PoolCurator._freshness_score(now.isoformat(), now)
    assert score > 0.9


def test_freshness_score_old_content_decays() -> None:
    now = _now()
    old = (now - timedelta(days=7)).isoformat()
    score = PoolCurator._freshness_score(old, now)
    assert score < 0.3


def test_freshness_score_half_life_around_three_days() -> None:
    now = _now()
    at_half_life = (now - timedelta(days=3)).isoformat()
    score = PoolCurator._freshness_score(at_half_life, now)
    assert 0.3 < score < 0.7


def test_freshness_score_empty_timestamp_returns_default() -> None:
    score = PoolCurator._freshness_score("", _now())
    assert score == 0.5


# ---------------------------------------------------------------------------
# Topic fatigue
# ---------------------------------------------------------------------------


def test_topic_fatigue_zero_for_unseen_topic() -> None:
    score = PoolCurator._topic_fatigue("ai", ("games", "music", "food"))
    assert score == 0.0


def test_topic_fatigue_high_for_dominant_topic() -> None:
    recent = ("ai",) * 10
    score = PoolCurator._topic_fatigue("ai", recent)
    assert score >= 0.9


def test_topic_fatigue_empty_inputs() -> None:
    assert PoolCurator._topic_fatigue("", ("ai",)) == 0.0
    assert PoolCurator._topic_fatigue("ai", ()) == 0.0


def test_topic_fatigue_curve_grows_steeply_after_first_repeat() -> None:
    """Each additional occurrence should add noticeably more fatigue.

    The pre-fix curve (count/len*3) gave count=3/30 only 0.30 fatigue,
    which after the 0.15 weight only deducted 0.045 from the score —
    not enough to dethrone a high-relevance candidate. The new curve
    must escalate sharply after count=2 so a topic that's been served
    three times in a row gets a near-saturating penalty.
    """
    f1 = PoolCurator._topic_fatigue("games", ("games",) + ("other",) * 29)  # 1/30
    f2 = PoolCurator._topic_fatigue("games", ("games",) * 2 + ("other",) * 28)
    f3 = PoolCurator._topic_fatigue("games", ("games",) * 3 + ("other",) * 27)
    f4 = PoolCurator._topic_fatigue("games", ("games",) * 4 + ("other",) * 26)
    assert 0.0 < f1 < 0.3
    assert f2 > f1 + 0.1  # second occurrence ≫ first
    assert f3 > 0.7  # three occurrences are already heavy
    assert f4 == 1.0  # four+ saturates


def test_combined_topic_fatigue_uses_max_of_key_and_group_axes() -> None:
    """Sibling topic_keys (动漫杂谈/补番/解说) escape per-key fatigue but
    saturate the topic_group axis (动漫). The combined helper must take
    the max so the group signal isn't lost."""
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.recommendation.curator import ScoringContext

    item = DiscoveredContent(bvid="BV1A", title="t", topic_key="动漫杂谈", topic_group="动漫")
    # No exact key match in history, but topic_group="动漫" appears 3 times
    context = ScoringContext(
        recent_topic_keys=("动漫补番", "动漫解说", "动漫资讯", "音乐", "游戏"),
        recent_topic_groups=("动漫", "动漫", "动漫", "音乐", "游戏"),
    )
    fatigue = PoolCurator._combined_topic_fatigue(item, context)
    # topic_key fatigue would be 0 (no "动漫杂谈" in history); group axis
    # carries the signal and saturates because 3/5 → high
    assert fatigue > 0.5


# ---------------------------------------------------------------------------
# Source monotony
# ---------------------------------------------------------------------------


def test_source_monotony_zero_for_unseen_source() -> None:
    score = PoolCurator._source_monotony("explore", ("search", "trending"))
    assert score == 0.0


def test_source_monotony_high_when_repeated() -> None:
    recent = ("search",) * 10
    score = PoolCurator._source_monotony("search", recent)
    assert score >= 0.9


# ---------------------------------------------------------------------------
# Serendipity bonus
# ---------------------------------------------------------------------------


def test_serendipity_bonus_for_explore() -> None:
    assert PoolCurator._serendipity_bonus("explore") == 1.0
    assert PoolCurator._serendipity_bonus("search") == 0.0


# ---------------------------------------------------------------------------
# Feedback adjustment
# ---------------------------------------------------------------------------


def test_feedback_dislike_up_penalty() -> None:
    feedback = FeedbackSignals(disliked_up_mids=frozenset({42}))
    item = DiscoveredContent(bvid="BV1", up_mid=42)
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj < 0


def test_feedback_dislike_topic_penalty() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"game"}))
    item = DiscoveredContent(bvid="BV1", topic_key="game")
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj < 0


def test_feedback_like_topic_bonus() -> None:
    feedback = FeedbackSignals(liked_topic_keys=frozenset({"ai"}))
    item = DiscoveredContent(bvid="BV1", topic_key="ai")
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj > 0


def test_feedback_neutral_when_no_signals() -> None:
    feedback = FeedbackSignals()
    item = DiscoveredContent(bvid="BV1", topic_key="ai", up_mid=1)
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj == 0.0


def test_feedback_dislike_franchise_penalty_propagates_to_same_ip() -> None:
    """Regression for the user-reported case: disliking ONE 原神
    摄影 video used to only block that exact bvid; the related_chain
    strategy then surfaced 5 other 原神 / 提瓦特 / 蒙德 candidates
    untouched. Now the curator pulls the LLM-tagged ``franchise_key``
    from each candidate's content_cache row, and any candidate whose
    franchise_key matches a disliked one takes a soft penalty.
    """
    feedback = FeedbackSignals(disliked_franchises=frozenset({"原神"}))
    # Different topic_key, different up_mid — only the franchise_key
    # links them. Pre-fix this got adj == 0.
    item = DiscoveredContent(
        bvid="BV2",
        title="提瓦特 摄影 集锦",
        topic_key="游戏摄影",
        franchise_key="原神",
        up_mid=99,
    )
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj < 0


def test_feedback_dislike_franchise_does_not_penalize_unrelated_ip() -> None:
    """Counterpart: a 原神 dislike must NOT down-rank a 塞尔达 video.
    The franchise penalty is keyed strictly on franchise_key equality;
    different IPs are unaffected."""
    feedback = FeedbackSignals(disliked_franchises=frozenset({"原神"}))
    item = DiscoveredContent(
        bvid="BV3",
        title="塞尔达传说 王国之泪 速通",
        topic_key="游戏",
        franchise_key="塞尔达传说",
    )
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj == 0.0


def test_feedback_dislike_franchise_no_penalty_when_franchise_key_empty() -> None:
    """Items the LLM didn't tag (general-interest content) must pass
    through with zero franchise penalty even if any franchise is
    currently disliked. Otherwise we'd silently penalize untagged rows
    when the LLM hadn't yet processed them — wrong default."""
    feedback = FeedbackSignals(disliked_franchises=frozenset({"原神"}))
    item = DiscoveredContent(
        bvid="BV4",
        title="番茄炒蛋 5 分钟教程",
        topic_key="美食",
        franchise_key="",  # general interest, not an IP
    )
    adj = PoolCurator._feedback_adjustment(item, feedback)
    assert adj == 0.0


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def test_score_candidates_returns_all_bvids() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    candidates = [
        DiscoveredContent(bvid="BV1", relevance_score=0.9, source_strategy="search"),
        DiscoveredContent(bvid="BV2", relevance_score=0.7, source_strategy="explore"),
    ]
    context = ScoringContext()
    scores = curator.score_candidates(candidates, context)
    assert set(scores.keys()) == {"BV1", "BV2"}
    assert all(v >= 0.0 for v in scores.values())


def test_score_candidates_explore_gets_serendipity_bonus() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    ts = now.isoformat()
    search = DiscoveredContent(
        bvid="BVS",
        relevance_score=0.8,
        source_strategy="search",
        discovered_at=ts,
    )
    explore = DiscoveredContent(
        bvid="BVE",
        relevance_score=0.8,
        source_strategy="explore",
        discovered_at=ts,
    )
    context = ScoringContext(now=now)
    scores = curator.score_candidates([search, explore], context)
    assert scores["BVE"] > scores["BVS"]


def test_score_candidates_penalises_fatigued_topic() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    ts = now.isoformat()
    fresh_topic = DiscoveredContent(
        bvid="BV1",
        relevance_score=0.8,
        topic_key="new_topic",
        source_strategy="search",
        discovered_at=ts,
    )
    stale_topic = DiscoveredContent(
        bvid="BV2",
        relevance_score=0.8,
        topic_key="repeated",
        source_strategy="search",
        discovered_at=ts,
    )
    context = ScoringContext(
        recent_topic_keys=("repeated",) * 10,
        now=now,
    )
    scores = curator.score_candidates([fresh_topic, stale_topic], context)
    assert scores["BV1"] > scores["BV2"]


# ---------------------------------------------------------------------------
# Build context from DB
# ---------------------------------------------------------------------------


def test_build_context_from_empty_db() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    ctx = curator.build_context()
    assert ctx.recent_topic_keys == ()
    assert ctx.recent_sources == ()
    assert ctx.feedback.disliked_up_mids == frozenset()


def test_build_context_reads_recommendation_history() -> None:
    db, _ = _make_db()
    db.cache_content("BV1", title="A", up_name="UP", source="search", topic_key="ai")
    db.insert_recommendation("BV1", confidence=0.9)
    curator = PoolCurator(db)
    ctx = curator.build_context()
    assert "ai" in ctx.recent_topic_keys
    assert "search" in ctx.recent_sources


def test_build_context_reads_feedback_signals() -> None:
    db, _ = _make_db()
    db.cache_content("BV1", title="A", up_name="UP", up_mid=42, source="search", topic_key="game")
    rec_id = db.insert_recommendation("BV1", confidence=0.9)
    db.update_recommendation_feedback(rec_id, feedback_type="dislike")
    curator = PoolCurator(db)
    ctx = curator.build_context()
    assert 42 in ctx.feedback.disliked_up_mids
    assert "game" in ctx.feedback.disliked_topic_keys


# ---------------------------------------------------------------------------
# Pool health
# ---------------------------------------------------------------------------


def test_needs_replenishment_when_pool_empty() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    assert curator.needs_replenishment() is True


def test_needs_replenishment_false_when_pool_full() -> None:
    db, _ = _make_db()
    for i in range(60):
        db.cache_content(
            f"BV{i}",
            title=f"T{i}",
            up_name="UP",
            source="search",
            pool_expression="x",
            pool_topic_label="y",
            style_key="tutorial",
            topic_group=f"分组{i}",
        )
    curator = PoolCurator(db)
    assert curator.needs_replenishment() is False


# ---------------------------------------------------------------------------
# Staleness eviction (database-level)
# ---------------------------------------------------------------------------


def test_evict_stale_pool_items_marks_old_items() -> None:
    db, _ = _make_db()
    db.cache_content(
        "BV_OLD",
        title="Old",
        up_name="UP",
        source="search",
        pool_expression="x",
        pool_topic_label="y",
        style_key="tutorial",
        topic_group="测试分组",
    )
    # Backdate the discovered_at to 20 days ago
    db.conn.execute(
        "UPDATE content_cache SET discovered_at = datetime('now', '-20 days') WHERE bvid = 'BV_OLD'"
    )
    db.conn.commit()
    db.cache_content(
        "BV_NEW",
        title="New",
        up_name="UP",
        source="search",
        pool_expression="x",
        pool_topic_label="y",
        style_key="tutorial",
        topic_group="测试分组",
    )
    evicted = db.evict_stale_pool_items(max_age_days=14)
    assert evicted == 1
    # Old item is stale, new item still fresh
    fresh = db.get_pool_candidates(limit=10)
    bvids = [row["bvid"] for row in fresh]
    assert "BV_NEW" in bvids
    assert "BV_OLD" not in bvids


def test_evict_stale_pool_items_ignores_recommended() -> None:
    db, _ = _make_db()
    db.cache_content("BV_OLD_REC", title="Old Recommended", up_name="UP", source="search")
    db.conn.execute(
        "UPDATE content_cache "
        "SET discovered_at = datetime('now', '-20 days') "
        "WHERE bvid = 'BV_OLD_REC'"
    )
    db.conn.commit()
    db.insert_recommendation("BV_OLD_REC", confidence=0.9)
    evicted = db.evict_stale_pool_items(max_age_days=14)
    assert evicted == 0
