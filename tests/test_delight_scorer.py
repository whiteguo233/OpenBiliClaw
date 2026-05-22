"""Tests for the proactive delight scoring module."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.delight import (
    DelightScorer,
    DelightSignals,
    DelightWeights,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _make_candidate(**overrides: object) -> DiscoveredContent:
    defaults = dict(
        bvid="BV1TEST",
        title="复杂系统的底层逻辑",
        up_name="系统观察者",
        up_mid=12345,
        duration=600,
        description="从控制论到信息论，一次讲透复杂系统的核心原理",
        cover_url="https://example.com/cover.jpg",
        view_count=50000,
        like_count=3000,
        tags=["科普", "系统论"],
        topic_key="复杂系统",
        topic_group="科学方法",
        style_key="deep_dive",
        source_strategy="explore",
        relevance_score=0.85,
        relevance_reason="deep resonance",
        pool_expression="",
        pool_topic_label="",
        candidate_tier="primary",
        discovered_at="2026-04-08T12:00:00",
        last_scored_at="2026-04-08T12:00:00",
    )
    defaults.update(overrides)
    return DiscoveredContent(**defaults)


def _make_profile(**overrides: object) -> SimpleNamespace:
    prefs = SimpleNamespace(
        interests=[],
        exploration_openness=overrides.pop("exploration_openness", 0.6),
    )
    defaults = dict(
        personality_portrait="你会反复追问问题背后的结构。",
        core_traits=["深究", "克制"],
        deep_needs=["对事物运作原理的深层理解", "不受干扰的个人空间与自由"],
        active_insights=[
            SimpleNamespace(
                hypothesis="这个人在试图理解复杂系统如何自组织",
                confidence=0.8,
            ),
        ],
        preferences=prefs,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Unit tests — DelightSignals
# ---------------------------------------------------------------------------


def test_delight_signals_defaults_to_zero() -> None:
    signals = DelightSignals()
    assert signals.deep_need_alignment == 0.0
    assert signals.insight_resonance == 0.0
    assert signals.novelty_factor == 0.0
    assert signals.quality_indicator == 0.0
    assert signals.exploration_match == 0.0


def test_delight_weights_defaults_sum_to_one() -> None:
    w = DelightWeights()
    total = w.deep_need + w.insight + w.likes + w.novelty + w.quality + w.exploration
    assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# DelightScorer — no embedding (fallback behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scorer_without_embedding_returns_valid_score(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)
    candidate = _make_candidate()
    profile = _make_profile()

    score, signals, reason_stub = await scorer.score(candidate, profile)

    assert 0.0 <= score <= 1.0
    assert isinstance(signals, DelightSignals)
    assert isinstance(reason_stub, str)
    # Without embeddings, deep_need and insight should be 0
    assert signals.deep_need_alignment == 0.0
    assert signals.insight_resonance == 0.0


@pytest.mark.asyncio
async def test_novelty_factor_explore_scores_higher_than_search(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)
    profile = _make_profile()

    explore_candidate = _make_candidate(source_strategy="explore")
    search_candidate = _make_candidate(source_strategy="search", bvid="BV2TEST")

    _, explore_signals, _ = await scorer.score(explore_candidate, profile)
    _, search_signals, _ = await scorer.score(search_candidate, profile)

    assert explore_signals.novelty_factor > search_signals.novelty_factor


@pytest.mark.asyncio
async def test_quality_indicator_uses_like_ratio(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)
    profile = _make_profile()

    high_quality = _make_candidate(view_count=100000, like_count=8000)
    low_quality = _make_candidate(view_count=100000, like_count=100, bvid="BV2TEST")

    _, high_signals, _ = await scorer.score(high_quality, profile)
    _, low_signals, _ = await scorer.score(low_quality, profile)

    assert high_signals.quality_indicator > low_signals.quality_indicator


@pytest.mark.asyncio
async def test_exploration_match_scales_with_openness(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)

    candidate = _make_candidate(source_strategy="explore")
    open_profile = _make_profile(exploration_openness=0.9)
    conservative_profile = _make_profile(exploration_openness=0.2)

    _, open_signals, _ = await scorer.score(candidate, open_profile)
    _, conservative_signals, _ = await scorer.score(candidate, conservative_profile)

    # Open users should get higher exploration_match for novel content
    assert open_signals.exploration_match > conservative_signals.exploration_match


# ---------------------------------------------------------------------------
# Threshold behavior
# ---------------------------------------------------------------------------


def test_effective_threshold_raises_for_conservative_users(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database, threshold=0.70)

    assert scorer.effective_threshold(0.6) == 0.70
    assert scorer.effective_threshold(0.2) == 0.80  # Conservative user


def test_default_thresholds_align_with_llm_rubric() -> None:
    """v0.3.49 regression: delight thresholds must match the LLM scoring rubric.

    `_DELIGHT_BATCH_SCORE_SYSTEM_PROMPT` defines:
      0.70-0.85: "跨域呼应,用户大概率会感兴趣但自己不会主动找"  ← real delight
      0.55-0.70: "有惊喜潜力但相对常规"                          ← NOT delight

    Earlier defaults (0.57 / 0.67) sat inside the "相对常规" band, so
    every batch surfaced ~60% false-positive "delight" content with
    hooks like "常规补给"/"实用工具"/"信息整合" — items the LLM
    itself flagged as **not** surprising. Lock the floor at 0.70.
    """
    from openbiliclaw.recommendation.delight import (
        CONSERVATIVE_DELIGHT_THRESHOLD,
        DEFAULT_DELIGHT_THRESHOLD,
    )

    assert DEFAULT_DELIGHT_THRESHOLD >= 0.70, (
        "Default threshold must clear the LLM's 0.70 '跨域呼应' boundary; "
        "values below admit content the LLM itself rated 'relatively normal'."
    )
    assert CONSERVATIVE_DELIGHT_THRESHOLD >= 0.80
    # And the conservative bar must remain strictly above the default.
    assert CONSERVATIVE_DELIGHT_THRESHOLD > DEFAULT_DELIGHT_THRESHOLD


def test_score_065_rejected_at_default_threshold(tmp_path: Path) -> None:
    """A 0.65 score (the prompt's '相对常规' band) must NOT pass."""
    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)

    threshold = scorer.effective_threshold(exploration_openness=0.5)
    assert threshold > 0.65, (
        f"effective_threshold={threshold} would admit score=0.65, but the "
        "LLM rubric explicitly tags 0.55-0.70 as '相对常规' (not delight)."
    )


def test_reason_stub_includes_relevance_fallback(tmp_path: Path) -> None:
    from openbiliclaw.recommendation.delight import DelightScorer

    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)

    signals = DelightSignals()  # All zeros
    candidate = _make_candidate(relevance_score=0.88)
    profile = _make_profile()

    stub = scorer._build_reason_stub(signals, candidate, profile)

    assert "relevance:0.88" in stub


def test_reason_stub_includes_deep_need_when_alignment_high(tmp_path: Path) -> None:
    from openbiliclaw.recommendation.delight import DelightScorer

    database = _make_database(tmp_path)
    scorer = DelightScorer(embedding_service=None, database=database)

    signals = DelightSignals(deep_need_alignment=0.8)
    candidate = _make_candidate()
    profile = _make_profile()

    stub = scorer._build_reason_stub(signals, candidate, profile)

    assert "deep_need:" in stub


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


def test_database_update_and_get_delight_candidate(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DL", title="惊喜内容", relevance_score=0.9)
    database.update_delight_score(
        "BV1DL",
        delight_score=0.92,
        delight_reason="这条会戳到你的深层需求",
        delight_hook="深层共鸣",
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
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.3,
        delight_reason="",
        delight_hook="",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.85)

    assert candidate is None


def test_database_get_delight_candidate_requires_ready_copy(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1BLANK", title="只有分数没有文案", relevance_score=0.9)
    database.update_delight_score(
        "BV1BLANK",
        delight_score=0.92,
        delight_reason="",
        delight_hook="",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert candidate is None


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
    database.update_delight_score(
        "BV1SUPPRESS",
        delight_score=0.91,
        delight_reason="历史评分残留，应当被新规则过滤。",
        delight_hook="压箱惊喜",
    )

    candidate = database.get_delight_candidate(min_delight_score=0.70)

    assert candidate is None


def test_database_mark_delight_notified(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1DLN", title="已通知", relevance_score=0.9)
    database.update_delight_score(
        "BV1DLN",
        delight_score=0.95,
        delight_reason="reason",
        delight_hook="hook",
    )
    database.mark_delight_notified("BV1DLN")

    # Should not appear since it's already notified
    candidate = database.get_delight_candidate(min_delight_score=0.85)
    assert candidate is None


def test_database_count_delight_candidates(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1A", title="A", relevance_score=0.9)
    database.cache_content("BV1B", title="B", relevance_score=0.8)
    database.update_delight_score(
        "BV1A",
        delight_score=0.92,
        delight_reason="r1",
        delight_hook="h1",
    )
    database.update_delight_score(
        "BV1B",
        delight_score=0.88,
        delight_reason="r2",
        delight_hook="h2",
    )

    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 2

    database.mark_delight_notified("BV1A")
    count = database.count_delight_candidates(min_delight_score=0.85)
    assert count == 1


def test_database_get_pool_candidates_needing_delight_score(tmp_path: Path) -> None:
    database = _make_database(tmp_path)
    # Unscored item (delight_score = 0.0 default)
    database.cache_content("BV1UNSCORE", title="Unscored", relevance_score=0.8)
    # Already scored item
    database.cache_content("BV1SCORED", title="Scored", relevance_score=0.7)
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


def test_database_get_pool_candidates_needing_delight_score_includes_high_score_backfill(
    tmp_path: Path,
) -> None:
    database = _make_database(tmp_path)
    database.cache_content("BV1READY", title="Ready", relevance_score=0.9)
    database.update_delight_score(
        "BV1READY",
        delight_score=0.72,
        delight_reason="已经有解释",
        delight_hook="已完成",
    )
    database.cache_content("BV1BACKFILL", title="Backfill", relevance_score=0.88)
    database.update_delight_score(
        "BV1BACKFILL",
        delight_score=0.71,
        delight_reason="",
        delight_hook="",
    )
    database.conn.execute(
        "UPDATE content_cache SET pool_status = 'suppressed' WHERE bvid = ?",
        ("BV1BACKFILL",),
    )
    database.conn.commit()
    database.cache_content("BV1LOW", title="Low", relevance_score=0.7)
    database.update_delight_score(
        "BV1LOW",
        delight_score=0.55,
        delight_reason="",
        delight_hook="",
    )

    candidates = database.get_pool_candidates_needing_delight_score(
        limit=10,
        min_delight_score_for_reason=0.70,
    )

    bvids = [c["bvid"] for c in candidates]
    assert "BV1BACKFILL" in bvids
    assert "BV1READY" not in bvids
    assert "BV1LOW" not in bvids


# ---------------------------------------------------------------------------
# v0.3.34+ — LLMDelightScorer + JSON shape tolerance + retrieval gate
# ---------------------------------------------------------------------------


def test_extract_delight_entries_handles_plain_list() -> None:
    """DeepSeek default: clean root-level array."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    payload = '[{"bvid":"BV1","score":0.7,"rationale":"r","hook":"h"}]'
    entries = _extract_delight_entries(payload, expected_count=1)
    assert len(entries) == 1
    assert entries[0]["bvid"] == "BV1"


def test_extract_delight_entries_handles_dict_wrapped() -> None:
    """mimo-v2.5-pro default: ``{"results": [...]}``."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    for wrap in ("results", "items", "delights", "data", "scores", "candidates", "output"):
        payload = f'{{"{wrap}": [{{"bvid":"BV1","score":0.7}}]}}'
        entries = _extract_delight_entries(payload, expected_count=1)
        assert len(entries) == 1, f"failed to unwrap {wrap}"


def test_extract_delight_entries_handles_fenced_wrapper() -> None:
    """Some gateways fence the wrapped batch result instead of the raw list."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    payload = """```json
{"output":[{"bvid":"BV1","score":0.72,"rationale":"r","hook":"h"}]}
```"""
    entries = _extract_delight_entries(payload, expected_count=1)
    assert len(entries) == 1
    assert entries[0]["bvid"] == "BV1"


def test_extract_delight_entries_handles_jsonl_extra_data() -> None:
    """mimo "Extra data" mode: multiple roots newline-separated."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    payload = '{"bvid":"BV1","score":0.7}\n{"bvid":"BV2","score":0.5}'
    entries = _extract_delight_entries(payload, expected_count=2)
    assert len(entries) == 2
    assert {e["bvid"] for e in entries} == {"BV1", "BV2"}


def test_extract_delight_entries_handles_single_dict_with_bvid() -> None:
    """batch=1 case: LLM returns a single object, not wrapped in a list."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    payload = '{"bvid":"BV1","score":0.8,"rationale":"x","hook":"y"}'
    entries = _extract_delight_entries(payload, expected_count=1)
    assert len(entries) == 1
    assert entries[0]["bvid"] == "BV1"


def test_extract_delight_entries_handles_garbage() -> None:
    """Invalid JSON yields empty list (caller must treat as scoring failure)."""
    from openbiliclaw.recommendation.delight import _extract_delight_entries

    assert _extract_delight_entries("not json", expected_count=5) == []
    assert _extract_delight_entries("", expected_count=5) == []
    assert _extract_delight_entries("{}", expected_count=5) == []


def test_get_pool_candidates_filters_by_min_relevance(tmp_path: Path) -> None:
    """v0.3.35: relevance_score gate cuts weak-fit items before LLM judgement."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HIGH", title="High fit", relevance_score=0.85)
    database.cache_content("BV1MED", title="Moderate", relevance_score=0.60)
    database.cache_content("BV1LOW", title="Weak", relevance_score=0.40)

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
    swing affecting how many candidates the LLM sees per cycle)."""
    database = _make_database(tmp_path)
    database.cache_content("BV1HALF", title="Right at edge", relevance_score=0.54)
    database.cache_content("BV1OVER", title="Just over", relevance_score=0.56)

    # No min_relevance_score passed — uses default
    rows = database.get_pool_candidates_needing_delight_score(limit=10)
    bvids = {r["bvid"] for r in rows}
    assert "BV1OVER" in bvids
    assert "BV1HALF" not in bvids
