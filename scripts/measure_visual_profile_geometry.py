#!/usr/bin/env python
"""Measure visual-profile geometry: centroid separation, label noise, net margin.

Read-only diagnostic for the margin-based visual-profile redesign. Answers the
three questions that decide whether neg can come back (margin-gated) and how:

1. CONTESTED — are the pos and neg centroids separated, or do they overlap?
   Pairwise cosine between every pos and neg centroid. If a pair is close
   (>= ~0.40), the cover modality cannot distinguish like/dislike in that
   region -> it must abstain (gray), not boost-and-cancel.

2. CROSS-CLEAN — which liked/disliked covers sit in the ENEMY's territory?
   For each feedback cover, kNN-sim to its own polarity vs the opposite. If a
   "like" is closer to disliked covers than to liked ones, it is likely a
   misclick / contradictory label -> drop from centroid construction (kept raw
   for later hard-negative use). Reports the dropped set so it can be eyeballed.
   Conservative: only drop on a clear margin, never flip a like into a negative.

3. MARGIN — the s_pos - s_neg distribution over pool candidates, to set the
   margin threshold (boost when net >= margin, suppress when net <= -margin,
   gray in between). One number on the difference, not two floor/ceil on
   absolutes -> self-calibrating (the 0.80 lesson generalized).

Read-only: never calls serve(), no DB writes. Reuses the CLI engine wiring.

Usage:
    python scripts/measure_visual_profile_geometry.py
    python scripts/measure_visual_profile_geometry.py --k 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from openbiliclaw.cli import _build_recommendation_engine
from openbiliclaw.llm.embedding import (
    cosine_similarity,
    image_embedding_cache_key_for_url,
)

# Covers whose feedback_type maps to each polarity, mirroring rebuild_visual_profile.
_POS_TYPES = ("like", "save")
_NEG_TYPES = ("dislike",)


def _percentiles(vals: list[float], pts: tuple[int, ...]) -> dict[int, float]:
    if not vals:
        return {p: 0.0 for p in pts}
    s = sorted(vals)
    n = len(s)
    return {p: s[max(1, min(n, (p * n + 99) // 100)) - 1] for p in pts}


def _knn_avg(vec: list[float], others: list[list[float]], k: int) -> float:
    """Mean cosine to the k nearest vectors in `others` (0 if none)."""
    if not others:
        return 0.0
    sims = sorted((cosine_similarity(vec, o) for o in others), reverse=True)
    take = min(k, len(sims))
    return sum(sims[:take]) / take if take else 0.0


def _best_centroid_sim(vec: list[float], centroids: list[list[float]]) -> float:
    best = 0.0
    for c in centroids:
        sim = cosine_similarity(vec, c)
        if sim > best:
            best = sim
    return best


async def _run(k: int, drop_margin: float) -> dict[str, Any]:
    engine = _build_recommendation_engine()
    db = engine._database  # type: ignore[attr-defined]
    cache = engine._load_visual_profile_cache()  # type: ignore[attr-defined]
    pos_c = [list(r["centroid"]) for r in cache if r.get("polarity") == "pos"]
    neg_c = [list(r["centroid"]) for r in cache if r.get("polarity") == "neg"]

    result: dict[str, Any] = {
        "centroids": {"pos": len(pos_c), "neg": len(neg_c)},
        "k": k,
        "drop_margin": drop_margin,
    }

    # 1. CONTESTED — pairwise pos vs neg centroid cosine.
    pairs: list[dict[str, Any]] = []
    for i, p in enumerate(pos_c):
        for j, n in enumerate(neg_c):
            sim = cosine_similarity(p, n)
            pairs.append({"pos": i, "neg": j, "cosine": round(sim, 4)})
    contested_threshold = 0.45  # centroid-vs-centroid; live pairs split 0.674/0.576 vs 0.377-0.219, gap at ~0.45
    result["contested_pairs"] = pairs
    result["contested_count"] = sum(1 for pr in pairs if pr["cosine"] >= contested_threshold)
    result["contested_threshold"] = contested_threshold

    # 2. CROSS-CLEAN — feedback covers in enemy territory.
    lookup = getattr(engine._embedding_service, "lookup_cached_image", None)
    rows = db.get_feedback_covers(limit=500) if callable(getattr(db, "get_feedback_covers", None)) else []
    pos_vecs: list[dict[str, Any]] = []  # {vec, bvid, cover_url}
    neg_vecs: list[dict[str, Any]] = []
    if callable(lookup):
        for r in rows:
            ftype = str(r.get("feedback_type") or "")
            url = str(r.get("cover_url") or "").strip()
            if not url:
                continue
            vec = lookup(image_embedding_cache_key_for_url(url)) or []
            if not vec:
                continue
            entry = {"vec": vec, "bvid": str(r.get("bvid") or ""), "cover_url": url, "ftype": ftype}
            if ftype in _POS_TYPES:
                pos_vecs.append(entry)
            elif ftype in _NEG_TYPES:
                neg_vecs.append(entry)

    def _clean(group, own, opp, label):
        dropped = []
        kept = 0
        own_vecs = [g["vec"] for g in own]
        for g in group:
            own_others = [v for v in own_vecs if v is not g["vec"]]
            nn_own = _knn_avg(g["vec"], own_others, k)
            nn_opp = _knn_avg(g["vec"], [o["vec"] for o in opp], k)
            # Drop only on a CLEAR margin: enemy territory beats own by drop_margin.
            if nn_opp > nn_own + drop_margin:
                dropped.append({
                    "bvid": g["bvid"], "cover_url": g["cover_url"], "ftype": g["ftype"],
                    "nn_own": round(nn_own, 4), "nn_opp": round(nn_opp, 4),
                    "diff": round(nn_opp - nn_own, 4),
                })
            else:
                kept += 1
        return dropped, kept

    pos_dropped, pos_kept = _clean(pos_vecs, pos_vecs, neg_vecs, "pos")
    neg_dropped, neg_kept = _clean(neg_vecs, neg_vecs, pos_vecs, "neg")
    result["cross_clean"] = {
        "pos_total": len(pos_vecs), "pos_kept": pos_kept, "pos_dropped": pos_dropped,
        "neg_total": len(neg_vecs), "neg_kept": neg_kept, "neg_dropped": neg_dropped,
    }

    # 3. MARGIN — s_pos / s_neg / net distribution over pool candidate covers.
    pool_rows = db.conn.execute(
        "SELECT bvid, cover_url FROM content_cache "
        "WHERE cover_url != '' AND relevance_score > 0 "
        "ORDER BY relevance_score DESC"
    ).fetchall()
    s_pos_vals: list[float] = []
    s_neg_vals: list[float] = []
    net_vals: list[float] = []
    equipped = 0
    if callable(lookup):
        for r in pool_rows:
            url = str(r[1] or "").strip()
            if not url:
                continue
            vec = lookup(image_embedding_cache_key_for_url(url)) or []
            if not vec:
                continue
            equipped += 1
            sp = _best_centroid_sim(vec, pos_c) if pos_c else 0.0
            sn = _best_centroid_sim(vec, neg_c) if neg_c else 0.0
            s_pos_vals.append(sp)
            s_neg_vals.append(sn)
            net_vals.append(sp - sn)
    pts = (5, 10, 25, 50, 75, 90, 95, 99)
    result["margin"] = {
        "pool_cover_equipped": equipped,
        "s_pos": _percentiles(s_pos_vals, pts),
        "s_neg": _percentiles(s_neg_vals, pts),
        "net_pos_minus_neg": _percentiles(net_vals, pts),
        "net_positive_share": round(sum(1 for x in net_vals if x > 0) / max(1, len(net_vals)), 3),
        "net_negative_share": round(sum(1 for x in net_vals if x < 0) / max(1, len(net_vals)), 3),
    }
    return result


def _print(r: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"centroids: pos={r['centroids']['pos']} neg={r['centroids']['neg']}  "
                 f"(k={r['k']}, drop_margin={r['drop_margin']})")
    lines.append("")
    lines.append("1. CONTESTED (pos x neg centroid pairwise cosine, threshold "
                 f">= {r['contested_threshold']}):")
    if r["centroids"]["pos"] and r["centroids"]["neg"]:
        for pr in r["contested_pairs"]:
            flag = "  <-- CONTESTED" if pr["cosine"] >= r["contested_threshold"] else ""
            lines.append(f"   pos{pr['pos']} x neg{pr['neg']}: {pr['cosine']:.4f}{flag}")
        lines.append(f"   contested pairs: {r['contested_count']} / {len(r['contested_pairs'])}")
    else:
        lines.append("   (no centroids on one or both sides)")
    lines.append("")
    lines.append("2. CROSS-CLEAN (feedback covers in enemy territory):")
    cc = r["cross_clean"]
    lines.append(f"   pos: {cc['pos_kept']} kept / {cc['pos_total']} total, "
                 f"{len(cc['pos_dropped'])} dropped")
    for d in cc["pos_dropped"]:
        lines.append(f"     DROP pos {d['bvid']}  nn_own={d['nn_own']:.3f} nn_opp={d['nn_opp']:.3f} "
                     f"diff={d['diff']:.3f}  {d['cover_url'][:60]}")
    lines.append(f"   neg: {cc['neg_kept']} kept / {cc['neg_total']} total, "
                 f"{len(cc['neg_dropped'])} dropped")
    for d in cc["neg_dropped"]:
        lines.append(f"     DROP neg {d['bvid']}  nn_own={d['nn_own']:.3f} nn_opp={d['nn_opp']:.3f} "
                     f"diff={d['diff']:.3f}  {d['cover_url'][:60]}")
    lines.append("")
    lines.append("3. MARGIN (s_pos / s_neg / net over pool candidate covers):")
    m = r["margin"]
    lines.append(f"   equipped covers: {m['pool_cover_equipped']}")

    def _fmt(d):
        return "  ".join(f"p{p}={d[p]:.3f}" for p in sorted(d))
    lines.append(f"   s_pos : {_fmt(m['s_pos'])}")
    lines.append(f"   s_neg : {_fmt(m['s_neg'])}")
    lines.append(f"   net   : {_fmt(m['net_pos_minus_neg'])}")
    lines.append(f"   net>0 share: {m['net_positive_share']}   net<0 share: {m['net_negative_share']}")
    lines.append("=" * 70)
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=3, help="kNN neighbors (default 3)")
    ap.add_argument("--drop-margin", type=float, default=0.05,
                    help="clear-margin to drop a noisy label (default 0.05)")
    args = ap.parse_args()
    r = asyncio.run(_run(args.k, args.drop_margin))
    _print(r)
    out = Path("data") / "visual_profile_geometry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
