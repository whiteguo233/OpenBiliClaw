#!/usr/bin/env python
"""Snapshot the current pool ranking for baseline-vs-P2 comparison.

Read-only: builds the real recommendation engine (same wiring as the CLI),
loads the servable pool from ``content_cache``, calls the four ranking bonus
maps exactly as ``serve()`` does, and dumps the top-K by
``relevance_score + combined_bonus`` with a per-bonus breakdown.

It never calls ``serve()`` itself, so it does NOT insert recommendation rows,
mark pool rows shown, call the LLM, or fetch anything over the network — every
bonus map is cache-lookup-only by design (see their docstrings). That makes it
safe to run repeatedly as a fixed reference: snapshot now with the flags off
(baseline), enable P2, run again, and diff the two JSON files to see exactly
which candidates the danmaku signal lifted and by how much.

Output file is chosen by the danmaku flag so back-to-back runs don't clobber
the baseline: ``data/baseline_ranking.json`` while danmaku is off,
``data/p2_ranking.json`` once it is on. Pass ``--out PATH`` to override.

Usage:
    python scripts/snapshot_ranking.py
    python scripts/snapshot_ranking.py --limit 30
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Reuse the CLI's engine/profile wiring so the snapshot cannot drift from
# production. These are private helpers, but they are the canonical builders.
from openbiliclaw.cli import _build_recommendation_engine, _build_soul_engine
from openbiliclaw.discovery.engine import DiscoveredContent

if TYPE_CHECKING:
    from openbiliclaw.storage.database import Database


def _load_pool_candidates(db: Database) -> list[DiscoveredContent]:
    """Load servable pool rows from content_cache as minimal DiscoveredContent.

    Only the fields the bonus maps read are populated (bvid, cover_url, title,
    relevance_score, source_platform). The bonus maps are lookup-only and
    ignore the rest, so this stays a faithful read of what serve() would score.
    """
    conn = db.conn
    rows = conn.execute(
        "SELECT bvid, title, cover_url, relevance_score, source_platform, pool_status "
        "FROM content_cache "
        "WHERE cover_url != '' AND relevance_score > 0 "
        "ORDER BY relevance_score DESC"
    ).fetchall()
    items: list[DiscoveredContent] = []
    for r in rows:
        bvid = str(r[0] or "")
        platform = str(r[4] or "bilibili") or "bilibili"
        items.append(
            DiscoveredContent(
                bvid=bvid,
                title=str(r[1] or ""),
                cover_url=str(r[2] or ""),
                relevance_score=float(r[3] or 0.0),
                source_platform=platform,
                content_id=bvid,
            )
        )
    return items


async def _snapshot(limit: int, out_override: str, full: bool = False) -> dict[str, Any]:
    engine = _build_recommendation_engine()
    soul_engine = _build_soul_engine()
    try:
        profile = await soul_engine.get_profile()
    except Exception as exc:  # noqa: BLE001 - profile is required to score
        raise SystemExit(f"could not load soul profile: {exc}") from exc

    db: Database = engine._database  # type: ignore[attr-defined]
    candidates = _load_pool_candidates(db)
    if not candidates:
        raise SystemExit("no servable pool candidates with a cover_url found")

    # Mirror serve()'s four bonus maps. Each is lookup-only and returns {} when
    # its flag is off or its cache is empty, so a missing signal is recorded as
    # 0 rather than crashing the snapshot.
    cover_bonus: dict[str, float] = {}
    visual_profile_bonus: dict[str, float] = {}
    keyframe_bonus: dict[str, float] = {}
    danmaku_bonus: dict[str, float] = {}
    with contextlib.suppress(Exception):
        cover_bonus = await engine._visual_bonus_map(candidates, profile)
    with contextlib.suppress(Exception):
        visual_profile_bonus = await engine._visual_profile_bonus_map(candidates)
    with contextlib.suppress(Exception):
        keyframe_bonus = await engine._keyframe_bonus_map(candidates)
    with contextlib.suppress(Exception):
        danmaku_bonus = await engine._danmaku_bonus_map(candidates, profile)

    flags = {
        "visual_profile_enabled": bool(getattr(engine, "_visual_profile_enabled", False)),
        "keyframe_enabled": bool(getattr(engine, "_keyframe_enabled", False)),
        "danmaku_enabled": bool(getattr(engine, "_danmaku_enabled", False)),
        "cover_embedding_active": bool(engine._cover_embedding_active()),
    }

    # Mirror serve()'s per-platform normalization of the stacked bonus, so the
    # snapshot's combined_bonus matches what serve() actually feeds the MMR
    # selector. Without this, the snapshot would show un-normalized heights and
    # misrepresent the cross-platform ranking.
    combined_bonus_raw: dict[str, float] = {}
    for c in candidates:
        bvid = c.bvid
        combined_bonus_raw[bvid] = (
            cover_bonus.get(bvid, 0.0)
            + visual_profile_bonus.get(bvid, 0.0)
            + keyframe_bonus.get(bvid, 0.0)
            + danmaku_bonus.get(bvid, 0.0)
        )
    combined_bonus_norm = engine._normalize_bonus_per_platform(  # type: ignore[attr-defined]
        candidates, combined_bonus_raw
    )

    rows: list[dict[str, Any]] = []
    for c in candidates:
        bvid = c.bvid
        cb = cover_bonus.get(bvid, 0.0)
        vp = visual_profile_bonus.get(bvid, 0.0)
        kf = keyframe_bonus.get(bvid, 0.0)
        dm = danmaku_bonus.get(bvid, 0.0)
        combined = combined_bonus_norm.get(bvid, 0.0)
        rows.append(
            {
                "bvid": bvid,
                "title": c.title,
                "source_platform": c.source_platform,
                "relevance_score": round(c.relevance_score, 6),
                "cover_bonus": round(cb, 6),
                "visual_profile_bonus": round(vp, 6),
                "keyframe_bonus": round(kf, 6),
                "danmaku_bonus": round(dm, 6),
                "combined_bonus": round(combined, 6),
                "final_score": round(c.relevance_score + combined, 6),
            }
        )
    rows.sort(key=lambda r: r["final_score"], reverse=True)

    def _stats(key: str) -> dict[str, Any]:
        vals = [r[key] for r in rows if r[key] != 0]
        pos = [v for v in vals if v > 0]
        neg = [v for v in vals if v < 0]
        return {
            "nonzero": len(vals),
            "pos": len(pos),
            "neg": len(neg),
            "min": round(min(vals), 6) if vals else 0.0,
            "max": round(max(vals), 6) if vals else 0.0,
            "mean": round(sum(vals) / len(vals), 6) if vals else 0.0,
        }

    snapshot = {
        "flags": flags,
        "pool_size": len(candidates),
        "bonus_stats": {
            "cover": _stats("cover_bonus"),
            "visual_profile": _stats("visual_profile_bonus"),
            "keyframe": _stats("keyframe_bonus"),
            "danmaku": _stats("danmaku_bonus"),
        },
        "top": rows if full else rows[:limit],
    }

    # Default output reflects the danmaku flag so baseline and P2 runs don't
    # clobber each other.
    if out_override:
        out_path = Path(out_override)
    else:
        danmaku_on = bool(getattr(engine, "_danmaku_enabled", False))
        name = "p2_ranking.json" if danmaku_on else "baseline_ranking.json"
        out_path = Path("data") / name

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot["out_path"] = str(out_path)
    return snapshot


def _print_summary(snap: dict[str, Any]) -> None:
    print("=" * 68)
    print("flags:", snap["flags"])
    print("pool_size:", snap["pool_size"])
    print("bonus_stats:")
    for name, st in snap["bonus_stats"].items():
        print(
            f"  {name:16s} nonzero={st['nonzero']:4d}  min={st['min']:.4f}  "
            f"max={st['max']:.4f}  mean={st['mean']:.4f}"
        )
    print()
    print(f"top {len(snap['top'])} by final_score:")
    if len(snap["top"]) > 60:
        print("  (full-pool dump — rows in JSON file, not printed)")
    else:
        print(
            f"  {'#':>2}  {'final':>7}  {'rel':>6}  {'cover':>6}  {'vp':>6}  {'kf':>6}  {'dm':>6}  bvid  title"
        )
        for i, r in enumerate(snap["top"], 1):
            title = r["title"][:30]
            print(
                f"  {i:>2}  {r['final_score']:7.4f}  {r['relevance_score']:6.3f}  "
                f"{r['cover_bonus']:6.4f}  {r['visual_profile_bonus']:6.4f}  "
                f"{r['keyframe_bonus']:6.4f}  {r['danmaku_bonus']:6.4f}  {r['bvid']}  {title}"
            )
    print("=" * 68)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=30, help="top-K to dump (default 30)")
    ap.add_argument("--out", type=str, default="", help="output JSON path (auto if empty)")
    ap.add_argument(
        "--full",
        action="store_true",
        help="dump the full pool (sorted), not just top-K",
    )
    args = ap.parse_args()

    snap = asyncio.run(_snapshot(args.limit, args.out, args.full))
    _print_summary(snap)
    print(f"\nsaved -> {snap.get('out_path')}")


if __name__ == "__main__":
    main()
