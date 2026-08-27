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
    ScoringWeights,
)
from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
from openbiliclaw.storage.database import Database


def _make_db() -> tuple[Database, str]:
    tmpdir = tempfile.mkdtemp()
    db = Database(Path(tmpdir) / "test.db")
    db.initialize()
    return db, tmpdir


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Publication-time temporal bonus
# ---------------------------------------------------------------------------


def _temporal_item(
    *,
    bvid: str = "BV_TEMPORAL",
    temporal_class: str = "current",
    temporal_confidence: object = 0.8,
    published_at: str = "",
    discovered_at: str = "",
) -> DiscoveredContent:
    item = DiscoveredContent(
        bvid=bvid,
        published_at=published_at,
        discovered_at=discovered_at,
    )
    item.temporal_class = temporal_class
    item.temporal_confidence = temporal_confidence  # type: ignore[assignment]
    return item


def test_temporal_bonus_uses_class_weight_for_new_content() -> None:
    now = _now()
    breaking = _temporal_item(
        temporal_class="breaking",
        published_at=now.isoformat(),
    )
    current = _temporal_item(
        temporal_class="current",
        published_at=now.isoformat(),
    )
    versioned = _temporal_item(
        temporal_class="versioned",
        published_at=now.isoformat(),
    )

    assert PoolCurator._temporal_bonus_component(breaking, now) == 0.85
    assert PoolCurator._temporal_bonus_component(current, now) == 0.60
    assert PoolCurator._temporal_bonus_component(versioned, now) == 0.30


def test_temporal_bonus_reaches_half_at_each_class_half_life() -> None:
    now = _now()
    cases = (("breaking", 1, 0.85), ("current", 14, 0.60), ("versioned", 60, 0.30))

    for temporal_class, half_life_days, class_weight in cases:
        item = _temporal_item(
            temporal_class=temporal_class,
            published_at=(now - timedelta(days=half_life_days)).isoformat(),
        )
        assert PoolCurator._temporal_bonus_component(item, now) == class_weight / 2


def test_temporal_bonus_confidence_thresholds_are_conservative() -> None:
    now = _now()
    full = _temporal_item(temporal_confidence=0.8, published_at=now.isoformat())
    half = _temporal_item(temporal_confidence=0.6, published_at=now.isoformat())
    below = _temporal_item(temporal_confidence=0.599, published_at=now.isoformat())

    assert PoolCurator._temporal_bonus_component(full, now) == 0.60
    assert PoolCurator._temporal_bonus_component(half, now) == 0.30
    assert PoolCurator._temporal_bonus_component(below, now) == 0.0


def test_temporal_bonus_rejects_malformed_confidence() -> None:
    now = _now()

    for confidence in (True, -0.1, 1.1, float("nan"), float("inf"), "high"):
        item = _temporal_item(
            temporal_confidence=confidence,
            published_at=now.isoformat(),
        )
        assert PoolCurator._temporal_bonus_component(item, now) == 0.0


def test_temporal_bonus_neutral_for_non_temporal_classes() -> None:
    now = _now()
    for temporal_class in ("evergreen", "historical", "unknown", ""):
        item = _temporal_item(temporal_class=temporal_class, published_at=now.isoformat())
        assert PoolCurator._temporal_bonus_component(item, now) == 0.0


def test_temporal_bonus_neutral_for_missing_invalid_or_future_publication() -> None:
    now = _now()
    for published_at in (
        "",
        "not-a-date",
        now.replace(tzinfo=None).isoformat(),
        (now + timedelta(days=1)).isoformat(),
    ):
        item = _temporal_item(published_at=published_at)
        assert PoolCurator._temporal_bonus_component(item, now) == 0.0


def test_temporal_bonus_ignores_discovery_and_scoring_clocks() -> None:
    now = _now()
    item = _temporal_item(
        published_at="",
        discovered_at=now.isoformat(),
    )
    item.last_scored_at = now.isoformat()

    assert PoolCurator._temporal_bonus_component(item, now) == 0.0


def test_temporal_freshness_weight_defaults_to_soft_positive_bonus() -> None:
    assert ScoringWeights().freshness == 0.10


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


def test_feedback_dislike_matches_key_when_group_is_present() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说"}))
    item = DiscoveredContent(
        bvid="BV_KEY",
        topic_key="动漫解说",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_dislike_matches_group_when_key_differs() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫"}))
    item = DiscoveredContent(
        bvid="BV_GROUP",
        topic_key="动画资讯",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_topic_double_match_applies_once() -> None:
    feedback = FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说", "动漫"}))
    item = DiscoveredContent(
        bvid="BV_BOTH",
        topic_key="动漫解说",
        topic_group="动漫",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == -0.10


def test_feedback_like_matches_either_topic_axis_once() -> None:
    feedback = FeedbackSignals(liked_topic_keys=frozenset({"建筑", "建筑史"}))
    item = DiscoveredContent(
        bvid="BV_LIKE",
        topic_key="建筑史",
        topic_group="建筑",
    )
    assert PoolCurator._feedback_adjustment(item, feedback) == 0.05


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


def test_publication_preference_penalizes_out_of_range_bilibili_score() -> None:
    db, _ = _make_db()
    curator = PoolCurator(
        db,
        weights=ScoringWeights(
            relevance=1.0,
            freshness=0.0,
            topic_fatigue=0.0,
            source_monotony=0.0,
            serendipity=0.0,
        ),
        publication_preference=PublicationDatePreference(
            preset="custom",
            start_date="2023-01-01",
            end_date="2023-12-31",
            weight=0.5,
        ),
    )
    in_range = DiscoveredContent(
        bvid="BV_IN",
        source_platform="bilibili",
        published_at="2023-06-01T00:00:00Z",
        relevance_score=0.8,
    )
    out_of_range = DiscoveredContent(
        bvid="BV_OUT",
        source_platform="bilibili",
        published_at="2022-06-01T00:00:00Z",
        relevance_score=0.8,
    )

    scores = curator.score_candidates(
        [in_range, out_of_range],
        ScoringContext(now=datetime(2024, 1, 1, tzinfo=UTC)),
    )

    assert scores["BV_IN"] == 0.8
    assert scores["BV_OUT"] == 0.4


def test_strict_publication_preference_filters_only_bilibili_candidates() -> None:
    db, _ = _make_db()
    curator = PoolCurator(
        db,
        publication_preference=PublicationDatePreference(
            preset="custom",
            start_date="2023-01-01",
            end_date="2023-12-31",
            weight=1.0,
        ),
    )
    candidates = [
        DiscoveredContent(
            bvid="BV_KEEP",
            source_platform="bilibili",
            published_at="2023-06-01T00:00:00Z",
        ),
        DiscoveredContent(
            bvid="BV_DROP",
            source_platform="bilibili",
            published_at="2022-06-01T00:00:00Z",
        ),
        DiscoveredContent(
            bvid="YT_KEEP",
            source_platform="youtube",
            published_at="2022-06-01T00:00:00Z",
        ),
    ]

    filtered = curator.filter_candidates_for_serving(
        candidates,
        now=datetime(2024, 1, 1, tzinfo=UTC),
    )

    assert [item.bvid for item in filtered] == ["BV_KEEP", "YT_KEEP"]


def test_missing_platform_uses_source_strategy_instead_of_bilibili_default() -> None:
    db, _ = _make_db()
    curator = PoolCurator(
        db,
        publication_preference=PublicationDatePreference(
            preset="custom",
            start_date="2023-01-01",
            end_date="2023-12-31",
            weight=1.0,
        ),
    )
    youtube_item = DiscoveredContent(
        bvid="YT_MISSING_PLATFORM",
        source_strategy="youtube-search",
        published_at="2022-06-01T00:00:00Z",
    )
    # Simulate a legacy row whose platform column was empty after hydration.
    youtube_item.source_platform = ""

    filtered = curator.filter_candidates_for_serving(
        [youtube_item],
        now=datetime(2024, 1, 1, tzinfo=UTC),
    )

    assert [item.bvid for item in filtered] == ["YT_MISSING_PLATFORM"]


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


def test_score_candidates_ranks_recent_confident_temporal_content_higher() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    recent = _temporal_item(
        bvid="BV_RECENT",
        temporal_class="current",
        temporal_confidence=0.8,
        published_at=now.isoformat(),
    )
    older = _temporal_item(
        bvid="BV_OLDER",
        temporal_class="current",
        temporal_confidence=0.8,
        published_at=(now - timedelta(days=14)).isoformat(),
    )
    evergreen = _temporal_item(
        bvid="BV_EVERGREEN",
        temporal_class="evergreen",
        temporal_confidence=1.0,
        published_at=now.isoformat(),
    )
    for item in (recent, older, evergreen):
        item.relevance_score = 0.8
        item.source_strategy = "search"

    scores = curator.score_candidates([recent, older, evergreen], ScoringContext(now=now))

    assert scores["BV_RECENT"] > scores["BV_OLDER"] > scores["BV_EVERGREEN"]


def test_temporal_ranking_shadow_records_aggregate_topk_counterfactual() -> None:
    db, _ = _make_db()
    curator = PoolCurator(
        db,
        weights=ScoringWeights(
            relevance=1.0,
            freshness=0.10,
            topic_fatigue=0.0,
            source_monotony=0.0,
            serendipity=0.0,
        ),
    )
    now = _now()
    candidates = [
        _temporal_item(
            bvid=f"BV_EVERGREEN_{index}",
            temporal_class="evergreen",
            temporal_confidence=1.0,
            published_at=now.isoformat(),
        )
        for index in range(11)
    ]
    for index, item in enumerate(candidates):
        item.relevance_score = 0.99 - index * 0.01
        item.source_strategy = "search"
    breaking = _temporal_item(
        bvid="BV_BREAKING_PRIVATE_ID",
        temporal_class="breaking",
        temporal_confidence=0.8,
        published_at=now.isoformat(),
    )
    breaking.relevance_score = 0.85
    breaking.source_strategy = "search"
    candidates.append(breaking)
    context = ScoringContext(now=now)
    scores = curator.score_candidates(candidates, context)

    audit = curator.build_temporal_ranking_shadow_audit(candidates, scores, context)

    assert audit is not None
    assert audit.candidate_count == 12
    assert audit.parseable_published_count == 12
    assert audit.bonus_eligible_count == 1
    assert audit.class_counts == {"breaking": 1, "evergreen": 11}
    top10 = audit.top_k_metrics[0]
    assert top10.requested_top_k == 10
    assert top10.overlap_count == 9
    assert top10.jaccard == 0.818182
    assert top10.entered_classes == {"breaking": 1}
    assert top10.exited_classes == {"evergreen": 1}
    assert curator.record_temporal_ranking_shadow_audit(candidates, scores, context) is True

    stored = db.query_temporal_ranking_shadow_audit()
    assert len(stored) == 1
    assert stored[0]["candidate_count"] == 12
    assert stored[0]["top_k_metrics"][0]["overlap_count"] == 9
    assert "BV_BREAKING_PRIVATE_ID" not in str(stored[0])


async def test_async_scoring_uses_same_publication_temporal_bonus_as_sync() -> None:
    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    candidates = [
        _temporal_item(
            bvid="BV_BREAKING",
            temporal_class="breaking",
            temporal_confidence=0.8,
            published_at=(now - timedelta(hours=12)).isoformat(),
        ),
        _temporal_item(
            bvid="BV_UNKNOWN",
            temporal_class="unknown",
            temporal_confidence=1.0,
            published_at=now.isoformat(),
        ),
    ]
    for item in candidates:
        item.relevance_score = 0.8
        item.source_strategy = "search"
    context = ScoringContext(now=now)

    sync_scores = curator.score_candidates(candidates, context)
    async_scores = await curator.score_candidates_async(candidates, context)

    assert async_scores == sync_scores


async def test_async_feedback_embedding_checks_key_and_group_once() -> None:
    class FakeEmbeddingService:
        similarity_threshold = 0.99

        async def embed(self, text: str) -> list[float]:
            vectors = {
                "动漫解说": [1.0, 0.0, 0.0],
                "动漫": [0.0, 1.0, 0.0],
                "科技": [0.0, 0.0, 1.0],
            }
            return vectors.get(text, [0.0, 0.0, 0.0])

    db, _ = _make_db()
    curator = PoolCurator(db)
    now = _now()
    matching = DiscoveredContent(
        bvid="BV_MATCH",
        relevance_score=0.8,
        topic_key="动漫解说",
        topic_group="科技",
        discovered_at=now.isoformat(),
    )
    neutral = DiscoveredContent(
        bvid="BV_NEUTRAL",
        relevance_score=0.8,
        topic_key="科技",
        topic_group="科技",
        discovered_at=now.isoformat(),
    )
    context = ScoringContext(
        feedback=FeedbackSignals(disliked_topic_keys=frozenset({"动漫解说"})),
        now=now,
    )

    scores = await curator.score_candidates_async(
        [matching, neutral],
        context,
        embedding_service=FakeEmbeddingService(),
    )

    assert scores["BV_MATCH"] == scores["BV_NEUTRAL"] - 0.10


async def _assert_async_exact_feedback_survives_partial_embeddings(
    *,
    polarity: str,
    expected_adjustment: float,
) -> None:
    class SelectiveEmbeddingService:
        similarity_threshold = 0.99

        def __init__(self, unavailable: str) -> None:
            self.unavailable = unavailable
            self.exact_calls = 0

        async def embed(self, text: str) -> list[float]:
            if text == "exact":
                self.exact_calls += 1
                if self.unavailable == "feedback":
                    return [] if self.exact_calls == 1 else [1.0, 0.0]
                return [1.0, 0.0] if self.exact_calls == 1 else []
            return [0.0, 1.0]

    feedback_kwargs = {f"{polarity}_topic_keys": frozenset({"exact"})}
    feedback = FeedbackSignals(**feedback_kwargs)
    now = _now()
    matching = DiscoveredContent(
        bvid="BV_MATCH",
        relevance_score=0.8,
        topic_key="exact",
        topic_group="available",
        discovered_at=now.isoformat(),
    )
    neutral = DiscoveredContent(
        bvid="BV_NEUTRAL",
        relevance_score=0.8,
        topic_key="neutral",
        topic_group="available",
        discovered_at=now.isoformat(),
    )

    for unavailable in ("feedback", "candidate"):
        db, _ = _make_db()
        scores = await PoolCurator(db).score_candidates_async(
            [matching, neutral],
            ScoringContext(feedback=feedback, now=now),
            embedding_service=SelectiveEmbeddingService(unavailable),
        )

        assert scores["BV_MATCH"] == scores["BV_NEUTRAL"] + expected_adjustment


async def test_async_disliked_exact_fallback_survives_partial_embeddings() -> None:
    await _assert_async_exact_feedback_survives_partial_embeddings(
        polarity="disliked",
        expected_adjustment=-0.10,
    )


async def test_async_liked_exact_fallback_survives_partial_embeddings() -> None:
    await _assert_async_exact_feedback_survives_partial_embeddings(
        polarity="liked",
        expected_adjustment=0.05,
    )


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


def test_get_recommendation_signals_since_uses_presented_at_rolling_budget_window() -> None:
    db, _ = _make_db()
    now = datetime.now(UTC)
    db.cache_content(
        "BV_OLD",
        title="Old",
        up_name="UP",
        source="search",
        topic_key="old",
        topic_group="旧方向",
    )
    db.cache_content(
        "BV_RECENT",
        title="Recent",
        up_name="UP",
        source="search",
        topic_key="recent",
        topic_group="城市基础设施观察",
    )
    old_id = db.insert_recommendation("BV_OLD", confidence=0.8)
    recent_id = db.insert_recommendation("BV_RECENT", confidence=0.8)
    db._execute_write(
        "UPDATE recommendations SET presented = 1, presented_at = ? WHERE id = ?",
        ((now - timedelta(days=2)).isoformat(sep=" "), old_id),
    )
    db._execute_write(
        "UPDATE recommendations SET presented = 1, presented_at = ? WHERE id = ?",
        ((now - timedelta(hours=1)).isoformat(sep=" "), recent_id),
    )

    rows = db.get_recent_recommendation_signals_since(
        since=now - timedelta(hours=24),
    )

    assert len(rows) == 1
    assert rows[0]["bvid"] == "BV_RECENT"
    assert rows[0]["topic_group"] == "城市基础设施观察"


def test_pool_curator_marks_over_budget_amplification_key() -> None:
    db, _ = _make_db()
    now = datetime.now(UTC)
    db.cache_content(
        "BV_RECENT",
        title="Recent",
        up_name="UP",
        source="search",
        topic_key="城市基础设施观察:桥梁",
        topic_group="城市基础设施观察",
    )
    rec_id = db.insert_recommendation("BV_RECENT", confidence=0.8)
    db._execute_write(
        "UPDATE recommendations SET presented = 1, presented_at = ? WHERE id = ?",
        ((now - timedelta(hours=1)).isoformat(sep=" "), rec_id),
    )
    curator = PoolCurator(db)

    context = curator.build_context(
        newly_confirmed_amplification_keys={"城市基础设施观察"},
        rolling_window_hours=24,
    )

    assert "城市基础设施观察" in context.over_budget_amplification_keys


def test_build_context_reads_feedback_signals() -> None:
    db, _ = _make_db()
    db.cache_content(
        "BV1",
        title="A",
        up_name="UP",
        up_mid=42,
        source="search",
        topic_key="game",
        topic_group="游戏",
    )
    rec_id = db.insert_recommendation("BV1", confidence=0.9)
    db.update_recommendation_feedback(rec_id, feedback_type="dislike")
    curator = PoolCurator(db)
    ctx = curator.build_context()
    assert 42 in ctx.feedback.disliked_up_mids
    assert "game" in ctx.feedback.disliked_topic_keys
    assert "游戏" in ctx.feedback.disliked_topic_keys


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
            relevance_score=0.90,
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
        relevance_score=0.90,
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
        relevance_score=0.90,
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


# ---------------------------------------------------------------------------
# issue #90: explore is the only strategy with a rec-score privilege
# ---------------------------------------------------------------------------


def test_serendipity_bonus_only_rewards_explore() -> None:
    assert PoolCurator._serendipity_bonus("explore") == 1.0
    for strategy in ("trending", "hot", "feed", "search", "related_chain", "channel", "creator"):
        assert PoolCurator._serendipity_bonus(strategy) == 0.0, strategy
