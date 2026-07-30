"""Tests for the cover-visual bonus A/B harness (scripts/ab_visual_bonus.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from openbiliclaw.recommendation.engine import RecommendationEngine

# scripts/ isn't a package; load the module by path.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ab_visual_bonus.py"
_spec = importlib.util.spec_from_file_location("ab_visual_bonus", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ab_visual_bonus"] = _mod
_spec.loader.exec_module(_mod)

ABConfig = _mod.ABConfig
PoolConfig = _mod.PoolConfig
run_ab_visual_bonus = _mod.run_ab_visual_bonus
_compute_bonus_map = _mod._compute_bonus_map
_spec_to_content = _mod._spec_to_content
_rank = _mod._rank
_SyntheticEmb = _mod._SyntheticEmb
_unit_anchor = _mod._unit_anchor
_cover_vec_for_cosine = _mod._cover_vec_for_cosine
CandidateSpec = _mod.CandidateSpec


def _specs(n: int, target_cos: float, rel: float = 0.80) -> list[CandidateSpec]:
    return [
        CandidateSpec(bvid=f"BV{i:04d}", relevance_score=rel, target_cos=target_cos,
                      topic_group=f"topic_{i % 4}")
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_ab_all_orthogonal_covers_no_change() -> None:
    """All covers orthogonal to the anchor → bonus=0 for everyone, ON==OFF ranking."""
    specs = _specs(20, target_cos=0.0, rel=0.80)
    contents = [_spec_to_content(s) for s in specs]
    anchor = _unit_anchor(64)
    key_map = {
        _mod.image_embedding_cache_key_for_url(f"https://i0.hdslb.com/bfs/archive/{s.bvid}.jpg"):
            _cover_vec_for_cosine(0.0, 64)
        for s in specs
    }
    emb = _SyntheticEmb(anchor, key_map)
    bonus = _compute_bonus_map(specs, contents, emb)
    assert bonus == {}
    on = _rank(contents, 10, bonus)
    off = _rank(contents, 10, {})
    assert [c.bvid for c in on] == [c.bvid for c in off]


@pytest.mark.asyncio
async def test_ab_aligned_covers_flip_top_k() -> None:
    """High-cosine cover with slightly lower relevance flips above a plain cover."""
    align = CandidateSpec(bvid="BV1ALIGN", relevance_score=0.80, target_cos=0.5,
                          topic_group="topic_0")
    plain = CandidateSpec(bvid="BV1PLAIN", relevance_score=0.83, target_cos=0.0,
                          topic_group="topic_1")
    specs = [align, plain]
    contents = [_spec_to_content(s) for s in specs]
    anchor = _unit_anchor(64)
    key_map = {
        _mod.image_embedding_cache_key_for_url("https://i0.hdslb.com/bfs/archive/BV1ALIGN.jpg"):
            _cover_vec_for_cosine(0.5, 64),
        _mod.image_embedding_cache_key_for_url("https://i0.hdslb.com/bfs/archive/BV1PLAIN.jpg"):
            _cover_vec_for_cosine(0.0, 64),
    }
    emb = _SyntheticEmb(anchor, key_map)
    bonus = _compute_bonus_map(specs, contents, emb)
    # Align gets a nonzero bonus; plain gets none.
    assert bonus.get("BV1ALIGN", 0.0) > 0.0
    assert bonus.get("BV1PLAIN", 0.0) == 0.0
    on = _rank(contents, 2, bonus)
    off = _rank(contents, 2, {})
    assert [c.bvid for c in off] == ["BV1PLAIN", "BV1ALIGN"]
    assert [c.bvid for c in on] == ["BV1ALIGN", "BV1PLAIN"]


@pytest.mark.asyncio
async def test_ab_bonus_matches_real_formula() -> None:
    """Harness bonus equals the production _cover_bonus_from_vec for a known cosine."""
    specs = [CandidateSpec(bvid="BVX", relevance_score=0.80, target_cos=0.40,
                           topic_group="topic_0")]
    contents = [_spec_to_content(s) for s in specs]
    anchor = _unit_anchor(64)
    cover_vec = _cover_vec_for_cosine(0.40, 64)
    key_map = {
        _mod.image_embedding_cache_key_for_url("https://i0.hdslb.com/bfs/archive/BVX.jpg"):
            cover_vec
    }
    emb = _SyntheticEmb(anchor, key_map)
    bonus = _compute_bonus_map(specs, contents, emb)
    expected = RecommendationEngine._cover_bonus_from_vec(cover_vec, [anchor])
    assert bonus.get("BVX", 0.0) == pytest.approx(expected, abs=1e-9)
    # And it's > 0 at cos=0.40 (above the production floor).
    assert expected > 0.0


def test_ab_report_has_expected_fields() -> None:
    """run_ab_visual_bonus() returns a dict with all metric keys the report needs."""
    report = run_ab_visual_bonus(ABConfig(pool=PoolConfig(n=20, seed=1), k=5))
    for top_key in (
        "config", "production_constants", "effect", "bonus_stats",
        "sweep", "caveat", "visual_profile",
    ):
        assert top_key in report, f"missing top-level key: {top_key}"
    eff = report["effect"]
    for k in ("k", "topk_overlap", "topk_jaccard", "topk_changed", "kendall_tau_full",
              "n_flips", "ndcg_off", "ndcg_on", "ndcg_delta", "off_topk", "on_topk"):
        assert k in eff, f"missing effect key: {k}"
    bs = report["bonus_stats"]
    for k in ("n_nonzero", "frac_nonzero", "mean", "median", "max", "min_nonzero", "buckets"):
        assert k in bs, f"missing bonus_stats key: {k}"
    assert isinstance(report["sweep"], list) and len(report["sweep"]) >= 4
    for s in report["sweep"]:
        for k in ("label", "bonus_max", "floor", "ceil", "topk_overlap", "topk_jaccard",
                  "n_flips", "mean_bonus", "ndcg_delta"):
            assert k in s, f"missing sweep key: {k}"
