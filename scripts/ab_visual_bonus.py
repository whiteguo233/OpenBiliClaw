#!/usr/bin/env python
"""Offline A/B harness: does the cover-visual bonus actually move ranking?

The cover-visual bonus (``_VISUAL_COVER_BONUS_MAX=0.05``; floor/ceil now
calibrated from real covers — see ``recommendation/engine.py``). This script
answers two questions with a synthetic candidate pool whose cover↔anchor cosine
is controlled exactly (no real multimodal model needed):

1. Effect size — does 0.05 flip top-K under a realistic relevance_score spread,
   or is it pure noise?
2. Calibration — does the bonus cluster at 0 (floor too high) or at the cap
   (ceil too low)? Where should floor/ceil move?

It reuses the REAL ranking path (``RecommendationEngine._select_diversified_batch``
+ ``_cover_bonus_from_vec``) so the measurement can't drift from production.
Direction quality (is the signal "right"?) is a proxy only here: "relevant" =
high-cosine cover. Real direction needs a live-library replay — noted in output.

Run:  python scripts/ab_visual_bonus.py [--out data/ab_visual_bonus_report.json]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.llm.embedding import image_embedding_cache_key_for_url
from openbiliclaw.recommendation import engine as engine_mod
from openbiliclaw.recommendation.engine import RecommendationEngine

# ---------------------------------------------------------------------------
# Defaults — mirror the production constants in recommendation/engine.py.
# Read live at call time so sensitivity sweeps can override them.
# ---------------------------------------------------------------------------

DEFAULT_N_CANDIDATES = 60
DEFAULT_K = 10
DEFAULT_DIM = 64
DEFAULT_RELEVANCE_LO, DEFAULT_RELEVANCE_HI = 0.70, 0.90
DEFAULT_COSINE_LO, DEFAULT_COSINE_HI = 0.0, 0.6
DEFAULT_SEED = 20260726

# Histogram buckets aligned to the production floor/ceil boundaries.
SIM_BUCKETS: list[tuple[str, float, float]] = [
    ("[0.00,0.15)", 0.00, 0.15),
    ("[0.15,0.25)", 0.15, 0.25),
    ("[0.25,0.35)", 0.25, 0.35),
    ("[0.35,0.45)", 0.35, 0.45),
    ("[0.45,0.60]", 0.45, 0.60),
]


# ---------------------------------------------------------------------------
# Synthetic embedding service — same shape as the test fake
# (tests/test_recommendation_engine.py::_CoverVisualEmb) but self-contained.
# ---------------------------------------------------------------------------


class _SyntheticEmb:
    """Fake multimodal embedding service for the A/B harness.

    All text anchors map to one unit direction ``a``; cover vectors come from a
    URL-keyed map and are constructed to hit an exact target cosine with ``a``.
    ``lookup_cached`` returns [] so MMR is skipped (base tuple ranking only).
    """

    multimodal_enabled = True
    supports_image_embedding = True
    similarity_threshold = 0.82

    def __init__(self, anchor_vec: list[float], key_to_vec: dict[str, list[float]]) -> None:
        self._anchor = anchor_vec
        self._map = key_to_vec

    def image_embedding_active(self) -> bool:
        return True

    async def embed(self, text: str) -> list[float]:
        return list(self._anchor)

    def lookup_cached(self, text: str) -> list[float]:
        return []

    def lookup_cached_image(self, cache_key: str) -> list[float]:
        return list(self._map.get(cache_key, []))

    async def embed_image(self, *args: object, **kwargs: object) -> list[float]:
        raise AssertionError("harness must be lookup-only (no cover fetch)")


# ---------------------------------------------------------------------------
# Vector construction — exact target cosine, no real model.
# ---------------------------------------------------------------------------


def _unit_anchor(dim: int) -> list[float]:
    a = [0.0] * dim
    a[0] = 1.0
    return a


def _cover_vec_for_cosine(target_cos: float, dim: int) -> list[float]:
    """A unit vector at exactly ``cosine(·, anchor) = target_cos``.

    anchor = e0. cover = target_cos·e0 + sqrt(1-target_cos²)·e1. Both unit, so
    their cosine is exactly target_cos (clamped to [-1,1] for safety).
    """
    c = max(-1.0, min(1.0, float(target_cos)))
    s = math.sqrt(max(0.0, 1.0 - c * c))
    v = [0.0] * dim
    v[0] = c
    if dim > 1:
        v[1] = s
    return v


# ---------------------------------------------------------------------------
# Candidate pool generation.
# ---------------------------------------------------------------------------


@dataclass
class CandidateSpec:
    bvid: str
    relevance_score: float
    target_cos: float
    topic_group: str


@dataclass
class PoolConfig:
    n: int = DEFAULT_N_CANDIDATES
    dim: int = DEFAULT_DIM
    rel_lo: float = DEFAULT_RELEVANCE_LO
    rel_hi: float = DEFAULT_RELEVANCE_HI
    cos_lo: float = DEFAULT_COSINE_LO
    cos_hi: float = DEFAULT_COSINE_HI
    seed: int = DEFAULT_SEED
    n_topics: int = 4


def _generate_specs(cfg: PoolConfig) -> list[CandidateSpec]:
    rng = random.Random(cfg.seed)
    specs: list[CandidateSpec] = []
    for i in range(cfg.n):
        rel = rng.uniform(cfg.rel_lo, cfg.rel_hi)
        cos = rng.uniform(cfg.cos_lo, cfg.cos_hi)
        topic = f"topic_{i % max(1, cfg.n_topics)}"
        specs.append(
            CandidateSpec(
                bvid=f"BV{i:04d}",
                relevance_score=round(rel, 4),
                target_cos=round(cos, 4),
                topic_group=topic,
            )
        )
    return specs


def _spec_to_content(spec: CandidateSpec) -> DiscoveredContent:
    return DiscoveredContent(
        bvid=spec.bvid,
        content_id=spec.bvid,
        title=f"内容 {spec.bvid}",
        cover_url=f"https://i0.hdslb.com/bfs/archive/{spec.bvid}.jpg",
        relevance_score=spec.relevance_score,
        topic_group=spec.topic_group,
        source_platform="bilibili",
        candidate_tier="primary",
    )


def _build_key_map(specs: list[CandidateSpec], dim: int) -> dict[str, list[float]]:
    key_map: dict[str, list[float]] = {}
    for spec in specs:
        url = f"https://i0.hdslb.com/bfs/archive/{spec.bvid}.jpg"
        key_map[image_embedding_cache_key_for_url(url)] = _cover_vec_for_cosine(spec.target_cos, dim)
    return key_map


# ---------------------------------------------------------------------------
# Bonus computation — reuse the REAL formula via the engine's staticmethod.
# ---------------------------------------------------------------------------


def _compute_bonus_map(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    emb: _SyntheticEmb,
) -> dict[str, float]:
    """Per-bvid bonus using the production _cover_bonus_from_vec + URL-keyed lookup."""
    anchor_vecs = [emb._anchor]  # one anchor; embed() returns it for any text
    bonus: dict[str, float] = {}
    for content in contents:
        cover_vec = emb.lookup_cached_image(image_embedding_cache_key_for_url(content.cover_url))
        if not cover_vec:
            continue
        b = RecommendationEngine._cover_bonus_from_vec(cover_vec, anchor_vecs)
        if b > 0.0:
            bonus[content.bvid] = b
    return bonus


# ---------------------------------------------------------------------------
# Ranking — reuse the REAL _select_diversified_batch (no MMR, no DB needed).
# ---------------------------------------------------------------------------


def _rank(contents: list[DiscoveredContent], limit: int, bonus: dict[str, float]) -> list[DiscoveredContent]:
    return RecommendationEngine._select_diversified_batch(
        list(contents),
        limit=limit,
        embeddings=None,  # skip MMR — pure tuple ranking, bonus in the relevance term
        relevance_bonus=bonus,
    )


def _rank_raw(contents: list[DiscoveredContent], limit: int, bonus: dict[str, float]) -> list[DiscoveredContent]:
    """Raw sort by _ranking_key only — no topic cap / interleave. Cleanest effect signal."""
    ranked = sorted(contents, key=lambda item: RecommendationEngine._ranking_key(item, bonus))
    return ranked[:limit]


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------


def _kendall_tau(a: list[str], b: list[str]) -> float:
    """Kendall tau over the common prefix of two bvid orderings (no scipy)."""
    # Rank by position in each list; items only in one list get a large rank.
    n = max(len(a), len(b))
    if n < 2:
        return 1.0
    rank_a = {bvid: i for i, bvid in enumerate(a)}
    rank_b = {bvid: i for i, bvid in enumerate(b)}
    big = n
    items = list(set(a) | set(b))
    concordant = 0
    discordant = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            da = rank_a.get(items[i], big) - rank_a.get(items[j], big)
            db = rank_b.get(items[i], big) - rank_b.get(items[j], big)
            if (da > 0 and db > 0) or (da < 0 and db < 0):
                concordant += 1
            elif da != 0 and db != 0 and ((da > 0) != (db > 0)):
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return 1.0
    return (concordant - discordant) / total


def _n_flips(off_order: list[str], on_order: list[str]) -> int:
    """Count OFF-adjacent pairs whose relative order flipped under ON."""
    on_pos = {bvid: i for i, bvid in enumerate(on_order)}
    flips = 0
    for i in range(len(off_order) - 1):
        a, b = off_order[i], off_order[i + 1]
        if a in on_pos and b in on_pos and on_pos[a] > on_pos[b]:
            flips += 1
    return flips


def _dcg(rels: list[float]) -> float:
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels))


def _ndcg_at_k(order: list[str], relevance: dict[str, float], k: int) -> float:
    top = order[:k]
    rels = [relevance.get(bvid, 0.0) for bvid in top]
    dcg = _dcg(rels)
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    return dcg / idcg if idcg > 0 else 0.0


def _bonus_stats(bonus_map: dict[str, float], specs: list[CandidateSpec]) -> dict[str, Any]:
    values = list(bonus_map.values())
    all_bonus = [bonus_map.get(s.bvid, 0.0) for s in specs]
    # Per-bucket mean bonus.
    buckets: list[dict[str, Any]] = []
    for label, lo, hi in SIM_BUCKETS:
        in_bucket = [s for s in specs if lo <= s.target_cos < hi or (label == "[0.45,0.60]" and s.target_cos >= lo)]
        bonused = [bonus_map.get(s.bvid, 0.0) for s in in_bucket]
        buckets.append(
            {
                "bucket": label,
                "count": len(in_bucket),
                "mean_bonus": round(statistics.fmean(bonused), 5) if bonused else 0.0,
                "n_nonzero": sum(1 for b in bonused if b > 0.0),
            }
        )
    return {
        "n_nonzero": len(values),
        "frac_nonzero": round(len(values) / max(1, len(specs)), 4),
        "mean": round(statistics.fmean(all_bonus), 5),
        "median": round(statistics.median(all_bonus), 5),
        "max": round(max(all_bonus), 5) if all_bonus else 0.0,
        "min_nonzero": round(min(values), 5) if values else 0.0,
        "buckets": buckets,
    }


def _effect_metrics(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    bonus: dict[str, float],
    k: int,
) -> dict[str, Any]:
    off = _rank(contents, k, {})
    on = _rank(contents, k, bonus)
    off_raw = _rank_raw(contents, len(contents), {})
    on_raw = _rank_raw(contents, len(contents), bonus)

    off_bvids = [c.bvid for c in off]
    on_bvids = [c.bvid for c in on]
    off_full = [c.bvid for c in off_raw]
    on_full = [c.bvid for c in on_raw]

    overlap = set(off_bvids) & set(on_bvids)
    union = set(off_bvids) | set(on_bvids)
    jaccard = len(overlap) / len(union) if union else 1.0

    # Proxy ground truth: relevant = top-25% target_cos.
    cos_values = sorted((s.target_cos for s in specs), reverse=True)
    cutoff = cos_values[max(0, len(cos_values) // 4 - 1)] if cos_values else 0.0
    relevance = {s.bvid: s.target_cos for s in specs if s.target_cos >= cutoff}

    return {
        "k": k,
        "topk_overlap": len(overlap),
        "topk_jaccard": round(jaccard, 4),
        "topk_changed": len(overlap) < k,
        "kendall_tau_full": round(_kendall_tau(off_full, on_full), 4),
        "n_flips": _n_flips(off_full, on_full),
        "ndcg_off": round(_ndcg_at_k(off_full, relevance, k), 4),
        "ndcg_on": round(_ndcg_at_k(on_full, relevance, k), 4),
        "ndcg_delta": round(_ndcg_at_k(on_full, relevance, k) - _ndcg_at_k(off_full, relevance, k), 4),
        "off_topk": off_bvids,
        "on_topk": on_bvids,
    }


# ---------------------------------------------------------------------------
# Sensitivity sweep.
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    label: str
    bonus_max: float
    floor: float
    ceil: float
    topk_overlap: int
    topk_jaccard: float
    n_flips: int
    mean_bonus: float
    ndcg_delta: float


def _run_one(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    emb: _SyntheticEmb,
    k: int,
) -> dict[str, Any]:
    bonus = _compute_bonus_map(specs, contents, emb)
    return {
        "bonus": bonus,
        "effect": _effect_metrics(specs, contents, bonus, k),
        "bonus_stats": _bonus_stats(bonus, specs),
    }


def _with_constants(bonus_max: float, floor: float, ceil: float) -> Any:
    """Context manager: temporarily override the engine module constants."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        saved = (
            engine_mod._VISUAL_COVER_BONUS_MAX,
            engine_mod._VISUAL_COVER_SIM_FLOOR,
            engine_mod._VISUAL_COVER_SIM_CEIL,
        )
        engine_mod._VISUAL_COVER_BONUS_MAX = bonus_max
        engine_mod._VISUAL_COVER_SIM_FLOOR = floor
        engine_mod._VISUAL_COVER_SIM_CEIL = ceil
        try:
            yield
        finally:
            (
                engine_mod._VISUAL_COVER_BONUS_MAX,
                engine_mod._VISUAL_COVER_SIM_FLOOR,
                engine_mod._VISUAL_COVER_SIM_CEIL,
            ) = saved

    return _cm()


def _sweep(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    emb: _SyntheticEmb,
    k: int,
) -> list[SweepResult]:
    results: list[SweepResult] = []
    # Baseline production constants.
    base_floor = engine_mod._VISUAL_COVER_SIM_FLOOR
    base_ceil = engine_mod._VISUAL_COVER_SIM_CEIL
    # Main sweep: BONUS_MAX, floor/ceil fixed.
    for bm in (0.0, 0.05, 0.10, 0.20):
        with _with_constants(bm, base_floor, base_ceil):
            r = _run_one(specs, contents, emb, k)
            results.append(
                SweepResult(
                    label=f"BONUS_MAX={bm}",
                    bonus_max=bm,
                    floor=base_floor,
                    ceil=base_ceil,
                    topk_overlap=r["effect"]["topk_overlap"],
                    topk_jaccard=r["effect"]["topk_jaccard"],
                    n_flips=r["effect"]["n_flips"],
                    mean_bonus=r["bonus_stats"]["mean"],
                    ndcg_delta=r["effect"]["ndcg_delta"],
                )
            )
    # Aux sweep: floor/ceil variants at BONUS_MAX=0.05.
    for floor, ceil in ((0.25, 0.55), (0.05, 0.35)):
        with _with_constants(0.05, floor, ceil):
            r = _run_one(specs, contents, emb, k)
            results.append(
                SweepResult(
                    label=f"floor={floor},ceil={ceil}",
                    bonus_max=0.05,
                    floor=floor,
                    ceil=ceil,
                    topk_overlap=r["effect"]["topk_overlap"],
                    topk_jaccard=r["effect"]["topk_jaccard"],
                    n_flips=r["effect"]["n_flips"],
                    mean_bonus=r["bonus_stats"]["mean"],
                    ndcg_delta=r["effect"]["ndcg_delta"],
                )
            )
    return results


# ---------------------------------------------------------------------------
# P1 variant: +visual_profile (user liked-cover centroids).
# ---------------------------------------------------------------------------


def _build_profile_centroids(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    emb: _SyntheticEmb,
    dim: int,
) -> tuple[list[list[float]], list[list[float]]]:
    """Pretend the user liked the top-cosine covers and disliked the bottom.

    Builds pos/neg mean centroids via the real ``build_centroids`` so the
    variant measures the P1 signal against the same ranking path. Returns
    (pos_centroids, neg_centroids) as raw float lists.
    """
    from openbiliclaw.recommendation.visual_profile import build_centroids

    sorted_specs = sorted(specs, key=lambda s: s.target_cos)
    n = len(sorted_specs)
    # Top 25% cosine = "liked", bottom 25% = "disliked".
    liked = sorted_specs[max(0, int(n * 0.75)):]
    disliked = sorted_specs[: max(0, int(n * 0.25))]
    pos_vecs = [_cover_vec_for_cosine(s.target_cos, dim) for s in liked]
    neg_vecs = [_cover_vec_for_cosine(s.target_cos, dim) for s in disliked]
    pos = [list(c.centroid) for c in build_centroids(pos_vecs, min_members=1)]
    neg = [list(c.centroid) for c in build_centroids(neg_vecs, min_members=1)]
    return pos, neg


def _profile_bonus_map(
    specs: list[CandidateSpec],
    contents: list[DiscoveredContent],
    emb: _SyntheticEmb,
    pos_centroids: list[list[float]],
    neg_centroids: list[list[float]],
) -> dict[str, float]:
    """Per-bvid visual-profile bonus using the real engine helper."""
    engine = RecommendationEngine
    bonus: dict[str, float] = {}
    for content in contents:
        cover_vec = emb.lookup_cached_image(
            image_embedding_cache_key_for_url(content.cover_url)
        )
        if not cover_vec:
            continue
        b = engine._visual_profile_bonus_from_vec(cover_vec, pos_centroids, neg_centroids)
        if b > 0.0:
            bonus[content.bvid] = b
    return bonus





@dataclass
class ABConfig:
    pool: PoolConfig = field(default_factory=PoolConfig)
    k: int = DEFAULT_K


def run_ab_visual_bonus(config: ABConfig | None = None) -> dict[str, Any]:
    """Run the A/B harness and return the full report dict."""
    cfg = config or ABConfig()
    pool = cfg.pool
    specs = _generate_specs(pool)
    contents = [_spec_to_content(s) for s in specs]
    anchor = _unit_anchor(pool.dim)
    key_map = _build_key_map(specs, pool.dim)
    emb = _SyntheticEmb(anchor, key_map)

    # Baseline (production constants) measurement.
    baseline = _run_one(specs, contents, emb, cfg.k)
    sweep = _sweep(specs, contents, emb, cfg.k)

    # P1 variant: +visual_profile (user liked-cover centroids) on top of the
    # existing cover↔text bonus. Measures the incremental lift from the user
    # visual profile vs the cover bonus alone.
    pos_cent, neg_cent = _build_profile_centroids(specs, contents, emb, pool.dim)
    profile_bonus = _profile_bonus_map(specs, contents, emb, pos_cent, neg_cent)
    combined = dict(baseline["bonus"])
    for bvid, b in profile_bonus.items():
        combined[bvid] = combined.get(bvid, 0.0) + b
    profile_effect = _effect_metrics(specs, contents, combined, cfg.k)
    profile_stats = _bonus_stats(combined, specs)

    return {
        "config": {
            "n_candidates": pool.n,
            "k": cfg.k,
            "dim": pool.dim,
            "relevance_range": [pool.rel_lo, pool.rel_hi],
            "cosine_range": [pool.cos_lo, pool.cos_hi],
            "seed": pool.seed,
        },
        "production_constants": {
            "bonus_max": engine_mod._VISUAL_COVER_BONUS_MAX,
            "floor": engine_mod._VISUAL_COVER_SIM_FLOOR,
            "ceil": engine_mod._VISUAL_COVER_SIM_CEIL,
        },
        "effect": baseline["effect"],
        "bonus_stats": baseline["bonus_stats"],
        "sweep": [s.__dict__ for s in sweep],
        "visual_profile": {
            "pos_centroids": len(pos_cent),
            "neg_centroids": len(neg_cent),
            "effect": profile_effect,
            "bonus_stats": profile_stats,
            "ndcg_delta_vs_off": round(
                profile_effect["ndcg_on"] - baseline["effect"]["ndcg_off"], 4
            ),
            "ndcg_delta_vs_cover_bonus": round(
                profile_effect["ndcg_on"] - baseline["effect"]["ndcg_on"], 4
            ),
        },
        "caveat": (
            "Direction (nDCG) is a PROXY: 'relevant' = high target_cos cover. "
            "Real direction quality needs a live-library replay with user feedback."
        ),
    }


# ---------------------------------------------------------------------------
# Console rendering.
# ---------------------------------------------------------------------------


def _print_report(report: dict[str, Any]) -> None:
    cfg = report["config"]
    pc = report["production_constants"]
    eff = report["effect"]
    bs = report["bonus_stats"]

    print("=" * 72)
    print("Cover-visual bonus A/B — synthetic fixture")
    print("=" * 72)
    print(
        f"pool: N={cfg['n_candidates']}  k={cfg['k']}  dim={cfg['dim']}  "
        f"relevance∈[{cfg['relevance_range'][0]},{cfg['relevance_range'][1]}]  "
        f"cosine∈[{cfg['cosine_range'][0]},{cfg['cosine_range'][1]}]  seed={cfg['seed']}"
    )
    print(
        f"production: BONUS_MAX={pc['bonus_max']}  floor={pc['floor']}  ceil={pc['ceil']}"
    )
    print("-" * 72)
    print("EFFECT (bonus ON vs OFF, production constants):")
    print(f"  top-{eff['k']} overlap      : {eff['topk_overlap']}/{eff['k']}  "
          f"(jaccard={eff['topk_jaccard']}, changed={eff['topk_changed']})")
    print(f"  Kendall tau (full)  : {eff['kendall_tau_full']}")
    print(f"  adjacent flips      : {eff['n_flips']}")
    print(f"  nDCG@{eff['k']} (proxy)    : OFF={eff['ndcg_off']}  ON={eff['ndcg_on']}  "
          f"Δ={eff['ndcg_delta']:+.4f}")
    print("-" * 72)
    print("BONUS DISTRIBUTION:")
    print(f"  nonzero : {bs['n_nonzero']}/{cfg['n_candidates']} ({bs['frac_nonzero']*100:.0f}%)")
    print(f"  mean={bs['mean']}  median={bs['median']}  max={bs['max']}  min(nonzero)={bs['min_nonzero']}")
    print("  per cosine bucket:")
    for b in bs["buckets"]:
        print(f"    {b['bucket']:14s}  n={b['count']:3d}  mean_bonus={b['mean_bonus']:.5f}  "
              f"nonzero={b['n_nonzero']}")
    print("-" * 72)
    print("SENSITIVITY SWEEP:")
    print(f"  {'variant':24s} {'overlap':>8s} {'jaccard':>8s} {'flips':>6s} {'mean_b':>8s} {'ndcgΔ':>9s}")
    for s in report["sweep"]:
        print(f"  {s['label']:24s} {s['topk_overlap']:>8d} {s['topk_jaccard']:>8.3f} "
              f"{s['n_flips']:>6d} {s['mean_bonus']:>8.5f} {s['ndcg_delta']:>+9.4f}")
    print("-" * 72)
    vp = report.get("visual_profile")
    if vp:
        ve = vp["effect"]
        print("P1 VARIANT: +visual_profile (cover bonus + user liked-cover centroids)")
        print(f"  pos_centroids={vp['pos_centroids']}  neg_centroids={vp['neg_centroids']}")
        print(f"  top-{ve['k']} overlap (vs OFF)  : {ve['topk_overlap']}/{ve['k']}  "
              f"(jaccard={ve['topk_jaccard']})")
        print(f"  nDCG@{ve['k']} (proxy)          : ON={ve['ndcg_on']}  "
              f"Δ vs OFF={vp['ndcg_delta_vs_off']:+.4f}  "
              f"Δ vs cover-bonus-only={vp['ndcg_delta_vs_cover_bonus']:+.4f}")
        print(f"  mean bonus (combined)   : {vp['bonus_stats']['mean']}")
    print("-" * 72)
    print(f"CAVEAT: {report['caveat']}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/ab_visual_bonus_report.json",
                        help="JSON report path (default: data/ab_visual_bonus_report.json)")
    parser.add_argument("--n", type=int, default=DEFAULT_N_CANDIDATES, help="candidate pool size")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="top-K for effect metrics")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed")
    args = parser.parse_args(argv)

    config = ABConfig(pool=PoolConfig(n=args.n, seed=args.seed), k=args.k)
    report = run_ab_visual_bonus(config)
    _print_report(report)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
