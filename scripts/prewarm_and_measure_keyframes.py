#!/usr/bin/env python
"""Prewarm P3 keyframes + measure keyframe↔centroid pos/neg similarity.

Read-only measurement except for the intended P3 enrichment writes (keyframe
embedding cache + ``keyframes_fetched_at`` stamp). Builds the real
recommendation engine (same wiring as the CLI), runs
``prewarm_pool_keyframes``, then for every Bilibili pool candidate that now has
cached keyframe vectors, computes the max-pool best cosine against the P1
pos/neg centroids and prints percentiles.

This is the P3 analogue of the P1 pos/neg overlap measurement. It answers two
questions at once:

1. Do keyframes (actual video frames) separate liked vs disliked visual taste
   better than covers did? If ``neg best sim`` still exceeds ``pos best sim``
   at p50, P3 inherits P1's neg-cancellation problem (P3 reuses the SAME pos/neg
   centroids and the SAME ``positive - penalty`` formula).
2. What are the real p50/p95 of keyframe-vs-centroid cosine, to set
   ``_KEYFRAME_SIM_FLOOR/CEIL``? The borrowed 0.22/0.41 is the cover-PAIR
   distribution and will saturate here, exactly like P1 did before
   recalibration to 0.31/0.61.

Never calls ``serve()``: no recommendation rows inserted, no LLM, no pool
marking. Safe to run repeatedly.

Usage:
    python scripts/prewarm_and_measure_keyframes.py
    python scripts/prewarm_and_measure_keyframes.py --limit 100
    python scripts/prewarm_and_measure_keyframes.py --measure-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.cli import _build_recommendation_engine
from openbiliclaw.llm.embedding import cosine_similarity, keyframe_embedding_cache_key

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database


def _percentiles(vals: list[float], pts: tuple[int, ...]) -> dict[int, float]:
    if not vals:
        return {p: 0.0 for p in pts}
    s = sorted(vals)
    n = len(s)
    out: dict[int, float] = {}
    for p in pts:
        # nearest-rank percentile (matches the cover/P1 calibration convention)
        k = max(1, min(n, (p * n + 99) // 100))
        out[p] = s[k - 1]
    return out


def _best_sim(frame_vecs: list[list[float]], centroids: list[list[float]]) -> float:
    """Max cosine across all frames × all centroids (mirrors P3's max-pool)."""
    best = 0.0
    for fv in frame_vecs:
        if not fv:
            continue
        for c in centroids:
            sim = cosine_similarity(fv, c)
            if sim > best:
                best = sim
    return best


async def _run(limit: int, measure_only: bool) -> dict[str, Any]:
    engine = _build_recommendation_engine()
    if not engine._keyframe_active():  # type: ignore[attr-defined]
        raise SystemExit(
            "keyframe not active: set [discovery].keyframe_enabled=true and ensure "
            "image embedding is configured"
        )
    db: Database = engine._database  # type: ignore[attr-defined]

    prewarmed = 0
    if not measure_only:
        prewarmed = await engine.prewarm_pool_keyframes(limit=limit)  # type: ignore[attr-defined]

    # Load P1 centroids — the SAME cache P3 reads in _keyframe_bonus_map.
    cache = engine._load_visual_profile_cache()  # type: ignore[attr-defined]
    pos_centroids = [list(r["centroid"]) for r in cache if r.get("polarity") == "pos"]
    neg_centroids = [list(r["centroid"]) for r in cache if r.get("polarity") == "neg"]
    if not pos_centroids and not neg_centroids:
        raise SystemExit("no P1 visual centroids found — rebuild_visual_profile first")

    lookup = getattr(engine._embedding_service, "lookup_cached_image", None)
    if not callable(lookup):
        raise SystemExit("embedding service has no lookup_cached_image")

    max_frames = int(getattr(engine, "_keyframe_max_frames", 4))

    # Bilibili video pool rows (keyframes only exist for Bilibili videos).
    rows = db.conn.execute(
        "SELECT bvid FROM content_cache "
        "WHERE COALESCE(source_platform,'bilibili')='bilibili' "
        "  AND COALESCE(content_type,'video')='video' "
        "  AND COALESCE(bvid,'') != '' "
        "ORDER BY COALESCE(relevance_score,0) DESC"
    ).fetchall()

    pos_best: list[float] = []
    neg_best: list[float] = []
    net: list[float] = []
    # Track which (pos_i, neg_j) pair each candidate lands on, to see if
    # contested pairs are eating the boost/suppress tail.
    pair_counts: dict[tuple[int, int], int] = {}
    equipped = 0
    for r in rows:
        bvid = str(r[0] or "")
        frame_vecs: list[list[float]] = []
        for fi in range(max_frames):
            vec = lookup(keyframe_embedding_cache_key(bvid, fi)) or []
            if vec:
                frame_vecs.append(vec)
        if not frame_vecs:
            continue
        equipped += 1
        # best pos index + sim
        bpi, bps = -1, 0.0
        for idx, c in enumerate(pos_centroids):
            best = 0.0
            for fv in frame_vecs:
                if fv:
                    s = cosine_similarity(fv, c)
                    if s > best: best = s
            if best > bps: bps, bpi = best, idx
        bni, bns = -1, 0.0
        for idx, c in enumerate(neg_centroids):
            best = 0.0
            for fv in frame_vecs:
                if fv:
                    s = cosine_similarity(fv, c)
                    if s > best: best = s
            if best > bns: bns, bni = best, idx
        p = bps if bpi >= 0 else 0.0
        n = bns if bni >= 0 else 0.0
        pos_best.append(p)
        neg_best.append(n)
        net.append(p - n)
        if bpi >= 0 and bni >= 0:
            pair_counts[(bpi, bni)] = pair_counts.get((bpi, bni), 0) + 1

    pts = (5, 10, 25, 50, 75, 90, 95, 99)
    pos_pct = _percentiles(pos_best, pts)
    result = {
        "prewarmed_this_run": prewarmed,
        "centroids": {"pos": len(pos_centroids), "neg": len(neg_centroids)},
        "keyframe_equipped_candidates": equipped,
        "pos_best_sim": pos_pct,
        "neg_best_sim": _percentiles(neg_best, pts),
        "net_pos_minus_neg": _percentiles(net, pts),
        "overlap_neg_gt_pos": sum(1 for x in net if x < 0),
        "pair_counts": {f"pos{p}_neg{n}": c for (p, n), c in sorted(pair_counts.items())},
        "contested_pairs": [(0, 0), (1, 1)],  # at threshold 0.45 (see geometry script)
        "suggested": {
            "_KEYFRAME_SIM_FLOOR": round(pos_pct[50], 2),
            "_KEYFRAME_SIM_CEIL": round(pos_pct[95], 2),
        },
    }
    return result


def _print(r: dict[str, Any]) -> None:
    print("=" * 68)
    print(f"prewarmed this run : {r['prewarmed_this_run']}")
    print(f"centroids          : pos={r['centroids']['pos']} neg={r['centroids']['neg']}")
    print(f"keyframe-equipped  : {r['keyframe_equipped_candidates']} candidates")
    print()

    def _fmt(d: dict[int, float]) -> str:
        return "  ".join(f"p{p}={d[p]:.3f}" for p in sorted(d))

    print(f"pos best sim : {_fmt(r['pos_best_sim'])}")
    print(f"neg best sim : {_fmt(r['neg_best_sim'])}")
    print(f"net pos-neg  : {_fmt(r['net_pos_minus_neg'])}")
    equipped = r["keyframe_equipped_candidates"]
    overlap = r["overlap_neg_gt_pos"]
    pct = (100.0 * overlap / equipped) if equipped else 0.0
    print(f"overlap (neg>pos): {overlap} / {equipped}  ({pct:.0f}%)")
    print()
    print(
        f"SUGGESTED _KEYFRAME_SIM_FLOOR = {r['suggested']['_KEYFRAME_SIM_FLOOR']:.2f}"
        "   # p50 of pos best sim"
    )
    print(
        f"SUGGESTED _KEYFRAME_SIM_CEIL  = {r['suggested']['_KEYFRAME_SIM_CEIL']:.2f}"
        "   # p95 of pos best sim"
    )
    print("=" * 68)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=100, help="prewarm batch size (default 100)")
    ap.add_argument(
        "--measure-only",
        action="store_true",
        help="skip prewarm, just measure already-cached keyframes",
    )
    args = ap.parse_args()
    r = asyncio.run(_run(args.limit, args.measure_only))
    _print(r)
    out = Path("data") / "keyframe_distribution.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
