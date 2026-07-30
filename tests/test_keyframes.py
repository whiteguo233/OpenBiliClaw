"""Tests for video keyframe extraction (P3) and its ranking bonus.

Covers: videoshot payload parsing, frame-position sampling, sprite cropping
(including the multi-sprite case that a naive ``image[0]`` implementation gets
wrong), the keyframe bonus map, and the prewarm idempotency contract.
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.keyframes import (
    VideoshotMeta,
    crop_frames_from_sprite,
    parse_videoshot_payload,
    select_frame_positions,
)
from openbiliclaw.llm.embedding import (
    image_embedding_cache_key_for_url,
    keyframe_embedding_cache_key,
)
from openbiliclaw.recommendation.engine import RecommendationEngine
from openbiliclaw.storage.database import Database

# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def _payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "image": ["//i0.hdslb.com/bfs/videoshot/1.jpg"],
        "img_x_len": 10,
        "img_y_len": 10,
        "img_x_size": 160,
        "img_y_size": 90,
        "index": [0, 5, 11],
    }
    data.update(overrides)
    return {"code": 0, "data": data}


def test_parse_videoshot_completes_protocol_relative_urls() -> None:
    """Real responses return ``//host/path`` — must become https."""
    meta = parse_videoshot_payload(_payload())
    assert meta is not None
    assert meta.image_urls == ("https://i0.hdslb.com/bfs/videoshot/1.jpg",)
    assert meta.total_frames == 100


def test_parse_videoshot_reads_tile_size_from_payload() -> None:
    """Tile size is NOT fixed — 480x270 was observed alongside 160x90."""
    meta = parse_videoshot_payload(_payload(img_x_size=480, img_y_size=270))
    assert meta is not None
    assert (meta.tile_width, meta.tile_height) == (480, 270)


def test_parse_videoshot_multi_sprite_totals() -> None:
    meta = parse_videoshot_payload(
        _payload(image=[f"//i0.hdslb.com/bfs/videoshot/{i}.jpg" for i in range(11)])
    )
    assert meta is not None
    assert len(meta.image_urls) == 11
    assert meta.total_frames == 1100


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not-a-dict",
        {"code": -404, "message": "gone"},
        {"code": 0},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"image": []}},
        _payload(img_x_len=0),
        _payload(img_x_size=0),
    ],
)
def test_parse_videoshot_rejects_unusable(payload: Any) -> None:
    assert parse_videoshot_payload(payload) is None


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------


def test_select_frame_positions_spreads_and_skips_edges() -> None:
    pos = select_frame_positions(100, 4)
    assert len(pos) == 4
    assert pos == sorted(pos)
    assert len(set(pos)) == 4
    # Openings/endings trimmed: nothing at the very start or very end.
    assert pos[0] > 0
    assert pos[-1] < 99


def test_select_frame_positions_spans_all_sprites_for_long_video() -> None:
    """The bug a naive ``image[0]`` implementation has: long videos must be
    sampled end-to-end, not just their opening sprite."""
    total, per_sheet = 1100, 100
    pos = select_frame_positions(total, 4)
    sheets = {p // per_sheet for p in pos}
    assert len(sheets) > 1
    assert max(sheets) >= 7  # reaches deep into the video, not just the start


def test_select_frame_positions_handles_small_and_empty() -> None:
    assert select_frame_positions(0, 4) == []
    assert select_frame_positions(100, 0) == []
    assert select_frame_positions(3, 4) == [0, 1, 2]  # fewer frames than wanted


# ---------------------------------------------------------------------------
# Sprite cropping
# ---------------------------------------------------------------------------


def _sprite_bytes(grid_x: int, grid_y: int, tile_w: int, tile_h: int) -> bytes:
    """A sprite sheet where each tile is a distinct solid colour."""
    sheet = Image.new("RGB", (grid_x * tile_w, grid_y * tile_h))
    for idx in range(grid_x * grid_y):
        col, row = idx % grid_x, idx // grid_x
        tile = Image.new("RGB", (tile_w, tile_h), (idx * 2 % 256, 64, 128))
        sheet.paste(tile, (col * tile_w, row * tile_h))
    buf = BytesIO()
    sheet.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_crop_frames_extracts_requested_tiles() -> None:
    meta = VideoshotMeta(
        image_urls=("https://x/1.jpg",),
        grid_x=4, grid_y=4, tile_width=32, tile_height=18,
    )
    frames = crop_frames_from_sprite(_sprite_bytes(4, 4, 32, 18), meta, [0, 5, 15])
    assert len(frames) == 3
    for f in frames:
        assert f  # non-empty JPEG bytes
        assert Image.open(BytesIO(f)).size == (32, 18)


def test_crop_frames_skips_out_of_bounds_tiles() -> None:
    """Real sheets are sometimes short on the final row — skip, don't raise."""
    meta = VideoshotMeta(
        image_urls=("https://x/1.jpg",),
        grid_x=10, grid_y=10, tile_width=32, tile_height=18,
    )
    # Sheet only actually holds a 2x2 grid.
    frames = crop_frames_from_sprite(_sprite_bytes(2, 2, 32, 18), meta, [0, 1, 99])
    assert len(frames) == 2  # index 99 lies outside the real image


def test_crop_frames_returns_empty_on_garbage() -> None:
    meta = VideoshotMeta(
        image_urls=("https://x/1.jpg",),
        grid_x=4, grid_y=4, tile_width=32, tile_height=18,
    )
    assert crop_frames_from_sprite(b"not-an-image", meta, [0]) == []
    assert crop_frames_from_sprite(b"", meta, [0]) == []


# ---------------------------------------------------------------------------
# Cache keys
# ---------------------------------------------------------------------------


def test_keyframe_cache_keys_are_distinct_and_stable() -> None:
    a0 = keyframe_embedding_cache_key("BV1", 0)
    a1 = keyframe_embedding_cache_key("BV1", 1)
    b0 = keyframe_embedding_cache_key("BV2", 0)
    assert a0 != a1 and a0 != b0
    assert a0 == keyframe_embedding_cache_key("BV1", 0)  # stable
    assert a0 != image_embedding_cache_key_for_url("BV1")  # no cover collision
    assert a0.startswith("img:")  # same vector space as covers


# ---------------------------------------------------------------------------
# Bonus map
# ---------------------------------------------------------------------------


class _KeyframeEmb:
    """Fake multimodal embedding service keyed by keyframe cache key."""

    multimodal_enabled = True
    supports_image_embedding = True
    similarity_threshold = 0.82

    def __init__(self, key_to_vec: dict[str, list[float]], *, active: bool = True) -> None:
        self._map = key_to_vec
        self._active = active

    def image_embedding_active(self) -> bool:
        return self._active

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def lookup_cached(self, text: str) -> list[float]:
        return []

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        return list(self._map.get(cache_key, []))

    async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
        raise AssertionError("serve() hot path must be lookup-only")


class _DummyLLM:
    async def complete_structured_task(self, **kwargs: object) -> Any:
        from openbiliclaw.llm.base import LLMResponse

        return LLMResponse(content="{}", provider="test", model="dummy", usage={})


def _engine(db: Database, emb: _KeyframeEmb, *, enabled: bool = True) -> RecommendationEngine:
    return RecommendationEngine(
        llm=_DummyLLM(),
        database=db,
        embedding_service=emb,  # type: ignore[arg-type]
        keyframe_enabled=enabled,
    )


@pytest.mark.asyncio
async def test_keyframe_bonus_positive_for_on_taste_frames() -> None:
    key_map = {keyframe_embedding_cache_key("BVX", 0): [1.0, 0.0, 0.0]}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.replace_user_visual_clusters(
            [{"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 3}]
        )
        engine = _engine(db, _KeyframeEmb(key_map))
        engine._visual_profile_cache = None
        cand = DiscoveredContent(bvid="BVX", title="t", relevance_score=0.8)
        bonus = await engine._keyframe_bonus_map([cand])
        assert bonus.get("BVX", 0.0) > 0.0
        db.close()


@pytest.mark.asyncio
async def test_keyframe_bonus_max_pools_across_frames() -> None:
    """One strongly-matching frame should lift the video even if others miss."""
    key_map = {
        keyframe_embedding_cache_key("BVX", 0): [0.0, 1.0, 0.0],  # orthogonal
        keyframe_embedding_cache_key("BVX", 1): [1.0, 0.0, 0.0],  # on taste
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.replace_user_visual_clusters(
            [{"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 3}]
        )
        engine = _engine(db, _KeyframeEmb(key_map))
        engine._visual_profile_cache = None
        cand = DiscoveredContent(bvid="BVX", title="t", relevance_score=0.8)
        bonus = await engine._keyframe_bonus_map([cand])
        assert bonus.get("BVX", 0.0) > 0.0
        db.close()


@pytest.mark.asyncio
async def test_keyframe_bonus_contested_pair_grays_out() -> None:
    """A frame whose best pos/neg centroids are contested → gray (no nudge).

    P3 reuses P1's centroids and contested set. When the liked and disliked
    centroids coincide (love-hate region), the margin design abstains instead
    of boost-minus-penalty cancelling to ~0.
    """
    key_map = {keyframe_embedding_cache_key("BVX", 0): [1.0, 0.0, 0.0]}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.replace_user_visual_clusters(
            [
                {"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 3},
                {"polarity": "neg", "centroid": [1.0, 0.0, 0.0], "member_count": 3},
            ]
        )
        engine = _engine(db, _KeyframeEmb(key_map))
        engine._visual_profile_cache = None
        cand = DiscoveredContent(bvid="BVX", title="t", relevance_score=0.8)
        bonus = await engine._keyframe_bonus_map([cand])
        # Contested → gray: no entry.
        assert bonus.get("BVX", 0.0) == 0.0
        db.close()


@pytest.mark.asyncio
async def test_keyframe_bonus_clear_pos_boosts_clear_neg_suppresses() -> None:
    """Separated centroids: a pos-leaning frame boosts, neg-leaning suppresses."""
    key_map = {
        keyframe_embedding_cache_key("BVPOS", 0): [1.0, 0.0, 0.0],
        keyframe_embedding_cache_key("BVNEG", 0): [0.0, 1.0, 0.0],
    }
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.replace_user_visual_clusters(
            [
                {"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 3},
                {"polarity": "neg", "centroid": [0.0, 1.0, 0.0], "member_count": 3},
            ]
        )
        engine = _engine(db, _KeyframeEmb(key_map))
        engine._visual_profile_cache = None
        pos_cand = DiscoveredContent(bvid="BVPOS", title="t", relevance_score=0.8)
        neg_cand = DiscoveredContent(bvid="BVNEG", title="t", relevance_score=0.8)
        bonus = await engine._keyframe_bonus_map([pos_cand, neg_cand])
        assert bonus.get("BVPOS", 0.0) > 0.0   # leans liked → boost
        assert bonus.get("BVNEG", 0.0) < 0.0   # leans disliked → suppress
        db.close()


@pytest.mark.asyncio
async def test_keyframe_bonus_empty_when_disabled() -> None:
    key_map = {keyframe_embedding_cache_key("BVX", 0): [1.0, 0.0, 0.0]}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.replace_user_visual_clusters(
            [{"polarity": "pos", "centroid": [1.0, 0.0, 0.0], "member_count": 3}]
        )
        engine = _engine(db, _KeyframeEmb(key_map), enabled=False)
        cand = DiscoveredContent(bvid="BVX", title="t", relevance_score=0.8)
        assert await engine._keyframe_bonus_map([cand]) == {}
        db.close()


@pytest.mark.asyncio
async def test_keyframe_bonus_empty_without_centroids() -> None:
    key_map = {keyframe_embedding_cache_key("BVX", 0): [1.0, 0.0, 0.0]}
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        engine = _engine(db, _KeyframeEmb(key_map))
        engine._visual_profile_cache = []
        cand = DiscoveredContent(bvid="BVX", title="t", relevance_score=0.8)
        assert await engine._keyframe_bonus_map([cand]) == {}
        db.close()


# ---------------------------------------------------------------------------
# Prewarm idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prewarm_marks_even_when_no_frames() -> None:
    """A video without videoshot data must not be re-fetched every cycle."""
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.cache_content(
            bvid="BVNONE", title="t", cover_url="", relevance_score=0.8,
            source="search", pool_expression="e", pool_topic_label="t",
            topic_group="g", style_key="s",
        )
        engine = _engine(db, _KeyframeEmb({}))

        async def _no_frames(bvid: str, **kwargs: object) -> list[bytes]:
            return []

        import openbiliclaw.discovery.keyframes as kf_mod

        original = kf_mod.fetch_keyframes
        kf_mod.fetch_keyframes = _no_frames  # type: ignore[assignment]
        try:
            processed = await engine.prewarm_pool_keyframes(limit=10)
        finally:
            kf_mod.fetch_keyframes = original  # type: ignore[assignment]

        assert processed == 1
        assert db.get_candidates_needing_keyframes(limit=10) == []
        db.close()


@pytest.mark.asyncio
async def test_prewarm_does_not_stamp_when_embed_fails_on_real_frames() -> None:
    """A transient embed outage (frames fetched but every embed_image returns [])
    must NOT be stamped as done — otherwise the video is permanently excluded
    from P3 re-prewarm (pitfall rule 2: never persist failed/empty results).

    Only the genuine no-videoshot case (frames == []) and the success case
    (embedded > 0) are definitive and get stamped.
    """
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "t.db")
        db.initialize()
        db.cache_content(
            bvid="BVEMBFAIL", title="t", cover_url="", relevance_score=0.8,
            source="search", pool_expression="e", pool_topic_label="t",
            topic_group="g", style_key="s",
        )
        # embed_image returns [] for every frame (simulates backend down /
        # rate-limited / transient failure — embed_image's own contract is to
        # return [] on failure so the next call retries).
        emb = _KeyframeEmb({})

        async def _embed_image_fail(*args: object, **kwargs: object) -> list[float]:
            return []

        emb.embed_image = _embed_image_fail  # type: ignore[assignment]
        engine = _engine(db, emb)

        async def _real_frames(bvid: str, **kwargs: object) -> list[bytes]:
            # Non-empty frames: the video HAS videoshot data, but embedding fails.
            return [b"\x00" * 10]

        import openbiliclaw.discovery.keyframes as kf_mod

        original = kf_mod.fetch_keyframes
        kf_mod.fetch_keyframes = _real_frames  # type: ignore[assignment]
        try:
            processed = await engine.prewarm_pool_keyframes(limit=10)
        finally:
            kf_mod.fetch_keyframes = original  # type: ignore[assignment]

        assert processed == 1
        # NOT stamped — still needs keyframes, will retry next cycle.
        remaining = db.get_candidates_needing_keyframes(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["bvid"] == "BVEMBFAIL"
        db.close()
