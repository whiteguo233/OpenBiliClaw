"""Tests for the user visual-profile feature (P1).

Covers: clustering (mean centroids, multi-peak, noise pruning), the bonus map
(positive nudge / negative penalty / gate / fairness), and the rebuild path
(feedback → centroids → persist → cache refresh).
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.recommendation.visual_profile import (
    best_centroid_similarity,
    build_centroids,
    contested_pairs,
    cross_clean_labels,
)
from openbiliclaw.recommendation.visual_profile import VisualCluster
from openbiliclaw.storage.database import Database

# Reuse the A/B harness vector helpers (exact-cosine cover vectors).
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ab_visual_bonus.py"
_spec = importlib.util.spec_from_file_location("ab_visual_bonus", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ab_visual_bonus"] = _mod
_spec.loader.exec_module(_mod)
_unit_anchor = _mod._unit_anchor
_cover_vec_for_cosine = _mod._cover_vec_for_cosine


# ---------------------------------------------------------------------------
# Clustering — pure functions.
# ---------------------------------------------------------------------------


def test_build_centroids_single_cluster_mean() -> None:
    """Three near-identical vectors cluster into one mean centroid."""
    dim = 8
    a = _cover_vec_for_cosine(0.95, dim)  # ~ e0
    b = _cover_vec_for_cosine(0.93, dim)
    c = _cover_vec_for_cosine(0.92, dim)
    clusters = build_centroids([a, b, c], threshold=0.80, min_members=2)
    assert len(clusters) == 1
    assert clusters[0].member_count == 3
    # Centroid is the mean — not equal to any single member.
    assert clusters[0].centroid != tuple(a)


def test_build_centroids_multi_peak_keeps_separate() -> None:
    """Two visually distinct groups stay as two clusters (multi-peak taste)."""
    dim = 8
    dark = [_cover_vec_for_cosine(0.95, dim) for _ in range(3)]
    bright = [_cover_vec_for_cosine(0.0, dim) for _ in range(3)]  # orthogonal
    clusters = build_centroids(dark + bright, threshold=0.80, min_members=2)
    assert len(clusters) == 2
    # The two centroids are near-orthogonal (different peaks).
    sim = best_centroid_similarity(list(bright[0]), clusters)
    # bright[0] matches its own peak strongly; the dark peak is far.
    assert sim > 0.9


def test_build_centroids_drops_singleton_noise() -> None:
    """A lone vector with no neighbours is pruned (min_members=2)."""
    dim = 8
    cluster_a = [_cover_vec_for_cosine(0.95, dim) for _ in range(3)]
    loner = _cover_vec_for_cosine(0.0, dim)  # orthogonal, only one
    clusters = build_centroids(cluster_a + [loner], threshold=0.80, min_members=2)
    assert len(clusters) == 1
    assert clusters[0].member_count == 3


def test_build_centroids_skips_zero_vectors() -> None:
    """Zero/empty vectors (provider failures) never enter a centroid."""
    dim = 8
    good = [_cover_vec_for_cosine(0.95, dim) for _ in range(2)]
    clusters = build_centroids(good + [[0.0] * dim, []], threshold=0.80, min_members=2)
    assert len(clusters) == 1
    assert clusters[0].member_count == 2


def test_build_centroids_empty_input() -> None:
    assert build_centroids([]) == []


# ---------------------------------------------------------------------------
# Cross-clean + contested — pure functions.
# ---------------------------------------------------------------------------


def test_cross_clean_drops_cover_in_enemy_territory() -> None:
    """A liked cover closer to disliked covers than to liked ones is dropped."""
    dim = 8
    # Two tight liked clusters far apart; one "like" planted among dislikes.
    pos_core = [_cover_vec_for_cosine(0.95, dim) for _ in range(4)]
    neg_core = [_cover_vec_for_cosine(0.0, dim) for _ in range(4)]  # orthogonal
    mislabeled_like = _cover_vec_for_cosine(0.02, dim)  # near the dislikes
    res = cross_clean_labels(pos_core + [mislabeled_like], neg_core, k=3, drop_margin=0.05)
    assert mislabeled_like in res.dropped_pos
    assert mislabeled_like not in res.kept_pos
    # Core likes are kept.
    assert len(res.kept_pos) == 4
    assert not res.dropped_neg


def test_cross_clean_never_flips_polarity() -> None:
    """A dropped like is NOT moved into the neg set (kept raw, not relabeled)."""
    dim = 8
    pos = [_cover_vec_for_cosine(0.95, dim) for _ in range(4)]
    neg = [_cover_vec_for_cosine(0.0, dim) for _ in range(4)]
    stray = _cover_vec_for_cosine(0.02, dim)
    res = cross_clean_labels(pos + [stray], neg, k=3, drop_margin=0.05)
    assert stray in res.dropped_pos
    assert stray not in res.kept_neg  # never relabeled to negative


def test_cross_clean_conservative_margin_keeps_borderline() -> None:
    """A near-tie (enemy barely above own) is kept when drop_margin is high."""
    dim = 8
    pos = [_cover_vec_for_cosine(0.95, dim) for _ in range(4)]
    neg = [_cover_vec_for_cosine(0.0, dim) for _ in range(4)]
    # Borderline: closer to own than enemy by a hair -> not dropped at 0.08.
    borderline = _cover_vec_for_cosine(0.90, dim)
    res = cross_clean_labels(pos + [borderline], neg, k=3, drop_margin=0.08)
    assert borderline in res.kept_pos


def test_contested_pairs_flags_overlapping_centroids() -> None:
    """A pos/neg centroid pair above threshold is contested; separated is not."""
    dim = 8
    pos = [VisualCluster(tuple(_cover_vec_for_cosine(0.95, dim)), 2)]
    neg_close = [VisualCluster(tuple(_cover_vec_for_cosine(0.93, dim)), 2)]  # ~0.95 cos
    neg_far = [VisualCluster(tuple(_cover_vec_for_cosine(0.0, dim)), 2)]  # ~0
    assert contested_pairs(pos, neg_close, threshold=0.40) == {(0, 0)}
    assert contested_pairs(pos, neg_far, threshold=0.40) == set()


def test_contested_pairs_empty_when_no_centroids() -> None:
    assert contested_pairs([], []) == set()


# ---------------------------------------------------------------------------
# Bonus map + rebuild — engine integration with a synthetic embedding service.
# ---------------------------------------------------------------------------


class _ProfileEmb:
    """Fake multimodal embedding service for visual-profile tests.

    Cover vectors come from a URL-keyed map (exact-cosine construction).
    ``embed_image`` raises so a test fails loudly if the hot path ever fetches.
    """

    multimodal_enabled = True
    supports_image_embedding = True
    similarity_threshold = 0.82

    def __init__(self, key_to_vec: dict[str, list[float]], *, active: bool = True) -> None:
        self._map = key_to_vec
        self._active = active

    def image_embedding_active(self) -> bool:
        return self._active

    async def embed(self, text: str) -> list[float]:
        return list(_unit_anchor(8))

    def lookup_cached(self, text: str) -> list[float]:
        return []

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        return list(self._map.get(cache_key, []))

    async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
        raise AssertionError("hot path must be lookup-only (no cover fetch)")


def _seed_feedback(db: Database, bvid: str, cover_url: str, feedback_type: str) -> None:
    """Insert a recommendation row + content_cache row with feedback."""
    db.cache_content(
        bvid=bvid,
        title=f"title {bvid}",
        cover_url=cover_url,
        relevance_score=0.80,
        source="search",
        pool_expression="文案",
        pool_topic_label="主题",
        topic_group="tg",
        style_key="tutorial",
    )
    db.conn.execute(
        "INSERT INTO recommendations (bvid, expression, topic, confidence, "
        "feedback_type, feedback_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (bvid, "expr", "topic", 0.8, feedback_type),
    )
    db.conn.execute(
        "UPDATE content_cache SET feedback_type = ? WHERE bvid = ?",
        (feedback_type, bvid),
    )
    db.conn.commit()


def _engine(db: Database, emb: _ProfileEmb) -> RecommendationEngine:
    return RecommendationEngine(
        llm=_DummyLLM(),
        database=db,
        embedding_service=emb,  # type: ignore[arg-type]
        visual_profile_enabled=True,
    )


class _DummyLLM:
    async def complete_structured_task(self, **kwargs: object) -> Any:
        from openbiliclaw.llm.base import LLMResponse
        return LLMResponse(content="{}", provider="test", model="dummy", usage={})


@pytest.mark.asyncio
async def test_bonus_map_positive_nudge_for_liked_style() -> None:
    """A candidate cover matching a liked-cover centroid gets a positive bonus."""
    dim = 8
    liked_url = "https://i0.hdslb.com/bfs/archive/liked.jpg"
    cand_url = "https://i0.hdslb.com/bfs/archive/cand.jpg"
    key_map = {
        image_embedding_cache_key_for_url(liked_url): _cover_vec_for_cosine(0.95, dim),
        image_embedding_cache_key_for_url(cand_url): _cover_vec_for_cosine(0.90, dim),
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed_feedback(db, "BVLIKED", liked_url, "like")
        emb = _ProfileEmb(key_map)
        engine = _engine(db, emb)
        # Manually build centroids from the liked cover vec (rebuild would
        # cold-fetch; tested separately). Inject via the DB so the cache loads.
        from openbiliclaw.recommendation.visual_profile import build_centroids
        clusters = build_centroids([_cover_vec_for_cosine(0.95, dim)], min_members=1)
        db.replace_user_visual_clusters(
            [{"polarity": "pos", "centroid": list(clusters[0].centroid), "member_count": 1}]
        )
        engine._visual_profile_cache = None  # force reload
        cand = DiscoveredContent(bvid="BVCAND", cover_url=cand_url, relevance_score=0.80)
        bonus = await engine._visual_profile_bonus_map([cand])
        assert bonus.get("BVCAND", 0.0) > 0.0
        db.close()


@pytest.mark.asyncio
async def test_bonus_map_contested_pair_grays_out() -> None:
    """A candidate whose best pos/neg centroids are contested → gray (no nudge).

    When the liked and disliked centroids are themselves close (a love-hate
    region — the cover modality cannot distinguish like/dislike there), the
    margin design abstains: neither boost nor suppress. This is the fix for
    the neg-cancellation problem — instead of boost-minus-penalty cancelling
    to ~0, the geometry explicitly says "no opinion".
    """
    dim = 8
    liked_url = "https://i0.hdslb.com/bfs/archive/liked.jpg"
    disliked_url = "https://i0.hdslb.com/bfs/archive/disliked.jpg"
    cand_url = "https://i0.hdslb.com/bfs/archive/cand.jpg"
    # pos and neg centroids are near-identical (contested); candidate matches both.
    key_map = {
        image_embedding_cache_key_for_url(liked_url): _cover_vec_for_cosine(0.95, dim),
        image_embedding_cache_key_for_url(disliked_url): _cover_vec_for_cosine(0.95, dim),
        image_embedding_cache_key_for_url(cand_url): _cover_vec_for_cosine(0.90, dim),
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        emb = _ProfileEmb(key_map)
        engine = _engine(db, emb)
        from openbiliclaw.recommendation.visual_profile import build_centroids
        pos_c = build_centroids([_cover_vec_for_cosine(0.95, dim)], min_members=1)[0]
        neg_c = build_centroids([_cover_vec_for_cosine(0.95, dim)], min_members=1)[0]
        db.replace_user_visual_clusters([
            {"polarity": "pos", "centroid": list(pos_c.centroid), "member_count": 1},
            {"polarity": "neg", "centroid": list(neg_c.centroid), "member_count": 1},
        ])
        engine._visual_profile_cache = None
        cand = DiscoveredContent(bvid="BVCAND", cover_url=cand_url, relevance_score=0.80)
        bonus = await engine._visual_profile_bonus_map([cand])
        # Contested → gray: no entry (the candidate is in the love-hate band).
        assert bonus.get("BVCAND", 0.0) == 0.0
        db.close()


async def test_bonus_map_clear_pos_boosts_clear_neg_suppresses() -> None:
    """Separated centroids: a pos-leaning candidate boosts, neg-leaning suppresses.

    When pos and neg centroids are far apart (the cover CAN distinguish), the
    margin design acts: a candidate closer to liked gets a positive nudge, one
    closer to disliked gets a negative (suppression) nudge. The neg centroid is
    used here — safely, because the region is not contested and the margin gate
    only fires on a clear win.
    """
    dim = 8
    liked_url = "https://i0.hdslb.com/bfs/archive/liked.jpg"
    disliked_url = "https://i0.hdslb.com/bfs/archive/disliked.jpg"
    pos_cand_url = "https://i0.hdslb.com/bfs/archive/poscand.jpg"
    neg_cand_url = "https://i0.hdslb.com/bfs/archive/negcand.jpg"
    key_map = {
        image_embedding_cache_key_for_url(liked_url): _cover_vec_for_cosine(0.95, dim),
        image_embedding_cache_key_for_url(disliked_url): _cover_vec_for_cosine(0.0, dim),
        image_embedding_cache_key_for_url(pos_cand_url): _cover_vec_for_cosine(0.90, dim),
        image_embedding_cache_key_for_url(neg_cand_url): _cover_vec_for_cosine(0.10, dim),
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        emb = _ProfileEmb(key_map)
        engine = _engine(db, emb)
        from openbiliclaw.recommendation.visual_profile import build_centroids
        pos_c = build_centroids([_cover_vec_for_cosine(0.95, dim)], min_members=1)[0]
        neg_c = build_centroids([_cover_vec_for_cosine(0.0, dim)], min_members=1)[0]
        db.replace_user_visual_clusters([
            {"polarity": "pos", "centroid": list(pos_c.centroid), "member_count": 1},
            {"polarity": "neg", "centroid": list(neg_c.centroid), "member_count": 1},
        ])
        engine._visual_profile_cache = None
        pos_cand = DiscoveredContent(bvid="BVPOS", cover_url=pos_cand_url, relevance_score=0.80)
        neg_cand = DiscoveredContent(bvid="BVNEG", cover_url=neg_cand_url, relevance_score=0.80)
        bonus = await engine._visual_profile_bonus_map([pos_cand, neg_cand])
        assert bonus.get("BVPOS", 0.0) > 0.0   # clearly leans liked → boost
        assert bonus.get("BVNEG", 0.0) < 0.0   # clearly leans disliked → suppress
        db.close()


@pytest.mark.asyncio
async def test_bonus_map_empty_when_feature_off() -> None:
    """visual_profile_enabled=False → empty map (byte-identical ranking)."""
    dim = 8
    url = "https://i0.hdslb.com/bfs/archive/x.jpg"
    key_map = {image_embedding_cache_key_for_url(url): _cover_vec_for_cosine(0.95, dim)}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        emb = _ProfileEmb(key_map, active=True)
        engine = RecommendationEngine(
            llm=_DummyLLM(), database=db, embedding_service=emb,  # type: ignore[arg-type]
            visual_profile_enabled=False,
        )
        cand = DiscoveredContent(bvid="BVX", cover_url=url, relevance_score=0.80)
        assert await engine._visual_profile_bonus_map([cand]) == {}
        db.close()


@pytest.mark.asyncio
async def test_bonus_map_empty_when_no_centroids() -> None:
    """No centroids loaded yet → empty map (no crash)."""
    dim = 8
    url = "https://i0.hdslb.com/bfs/archive/x.jpg"
    key_map = {image_embedding_cache_key_for_url(url): _cover_vec_for_cosine(0.95, dim)}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        engine = _engine(db, _ProfileEmb(key_map))
        engine._visual_profile_cache = []  # loaded but empty
        cand = DiscoveredContent(bvid="BVX", cover_url=url, relevance_score=0.80)
        assert await engine._visual_profile_bonus_map([cand]) == {}
        db.close()


@pytest.mark.asyncio
async def test_rebuild_persists_centroids_from_feedback() -> None:
    """rebuild_visual_profile reads feedback, clusters, persists, refreshes cache."""
    dim = 8
    # Two liked covers (same style → one cluster) + two disliked (another style).
    liked_urls = [f"https://i0.hdslb.com/bfs/archive/liked{i}.jpg" for i in range(2)]
    disliked_urls = [f"https://i0.hdslb.com/bfs/archive/disliked{i}.jpg" for i in range(2)]
    key_map: dict[str, list[float]] = {}
    for u in liked_urls:
        key_map[image_embedding_cache_key_for_url(u)] = _cover_vec_for_cosine(0.95, dim)
    for u in disliked_urls:
        key_map[image_embedding_cache_key_for_url(u)] = _cover_vec_for_cosine(0.0, dim)
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        for i, u in enumerate(liked_urls):
            _seed_feedback(db, f"BVLIK{i}", u, "like")
        for i, u in enumerate(disliked_urls):
            _seed_feedback(db, f"BVDIS{i}", u, "dislike")
        emb = _ProfileEmb(key_map)
        engine = _engine(db, emb)
        n = await engine.rebuild_visual_profile()
        assert n >= 1
        rows = db.get_user_visual_clusters()
        polarities = {r["polarity"] for r in rows}
        assert "pos" in polarities
        assert "neg" in polarities
        # Cache was refreshed.
        assert engine._visual_profile_cache is not None
        assert len(engine._visual_profile_cache) == len(rows)
        db.close()


@pytest.mark.asyncio
async def test_rebuild_dispatch_uses_registry_track() -> None:
    """The detached rebuild must go through BackgroundTaskRegistry.track.

    Regression: the dispatch originally probed for ``create_task``, a method
    BackgroundTaskRegistry does not define — so the hasattr check was always
    False and the task silently ran untracked via a bare loop.create_task,
    surviving hot reload instead of being cancellable by RuntimeContext.
    """
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

    # The real registry exposes track(), not create_task().
    assert hasattr(BackgroundTaskRegistry, "track")
    assert not hasattr(BackgroundTaskRegistry, "create_task")

    tracked: list[str] = []

    class _SpyRegistry:
        def track(self, name: str, coro: Any) -> None:
            tracked.append(name)
            coro.close()  # we only assert dispatch, don't run the rebuild

    dim = 8
    url = "https://i0.hdslb.com/bfs/archive/x.jpg"
    key_map = {
        _mod.image_embedding_cache_key_for_url(url): _cover_vec_for_cosine(0.95, dim)
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        _seed_feedback(db, "BVL", url, "like")
        engine = RecommendationEngine(
            llm=_DummyLLM(),
            database=db,
            embedding_service=_ProfileEmb(key_map),  # type: ignore[arg-type]
            visual_profile_enabled=True,
        )
        engine.task_registry = _SpyRegistry()  # type: ignore[assignment]
        engine._maybe_rebuild_visual_profile()
        assert tracked == ["rebuild_visual_profile_detached"]
        db.close()
