#!/usr/bin/env python
"""Measure the real cosine distributions that the visual/danmaku bonuses need.

The P1/P3 thresholds were originally guessed, and a synthetic check on flat
colour blocks actively misled: two similar reds scored 0.99 and red-vs-blue
0.62, suggesting the thresholds were too LOW. Real Bilibili covers are
information-dense and spread out very differently — an early 21-pair sample
landed entirely in 0.06-0.37, meaning the guessed floor of 0.55 pinned the
bonus to exactly zero and the feature silently did nothing.

21 pairs is too small to set a ceiling on, though: the ceiling is decided by
the *tail* (the most-similar pairs), which a small sample barely reaches. This
script accumulates across runs so the distribution can be built up over several
batches.

Usage
-----
    python scripts/calibrate_visual_thresholds.py                # one batch
    python scripts/calibrate_visual_thresholds.py --batches 5    # several
    python scripts/calibrate_visual_thresholds.py --report       # stats only

State accumulates in data/calibration_vectors.json, so repeated runs keep
growing the sample instead of starting over. Re-run --report any time to see
the current recommendation; hand the output back for threshold selection.

Batches are made to DIFFER even within one sitting: the ranking endpoint is a
slow-moving (roughly daily) source, so re-fetching it back-to-back returns the
same list and dedup leaves later batches empty. Each batch therefore pulls
from a paginated popular feed (cursor persisted in the state file, so page N+1
follows page N across batches AND across runs) and then graph-walks the
related-videos endpoint from a random sample of already-collected covers —
which by construction explores new neighbourhoods every time. The ranking
sweep still runs once per sitting as a category-diversity floor.

Network flakiness (hdslb truncating responses, ranking endpoints dropping) is
expected and retried; partial batches still contribute.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import sys
from pathlib import Path
from typing import Any

import httpx

from openbiliclaw.config import load_config
from openbiliclaw.discovery.multimodal import prepare_cover_bytes_for_embedding
from openbiliclaw.llm import registry as _registry
from openbiliclaw.llm.embedding import cosine_similarity

STATE_PATH = Path("data/calibration_vectors.json")

# Ranking regions spanning very different visual styles, so the sample is not
# dominated by one category's look.
RANKING_RIDS = (0, 1, 3, 4, 10, 64, 119, 129, 144, 168, 188, 214)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
}

# Interest anchors for the cross-modal (cover <-> text) measurement. Kept
# generic so the numbers are not tied to one user's profile.
ANCHOR_TEXTS = (
    "数码测评",
    "游戏实况",
    "音乐",
    "科技科普",
    "生活日常",
    "动画",
    "美食",
    "影视解说",
)


def _load_state() -> dict[str, Any]:
    state: dict[str, Any] = {"covers": {}, "anchors": {}, "popular_pn": 1}
    if STATE_PATH.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            state.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    return state


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _item_from(raw: dict[str, Any], rid: str) -> dict[str, str] | None:
    pic = str(raw.get("pic") or "")
    bvid = str(raw.get("bvid") or "")
    if not pic or not bvid:
        return None
    return {
        "bvid": bvid,
        "pic": pic,
        # related/popular items carry tid/tname instead of a ranking rid; use
        # tid so the same/cross-category split keeps working for them.
        "rid": rid or str(raw.get("tid") or ""),
        "tname": str(raw.get("tname") or ""),
    }


async def _collect_ranking(client: httpx.AsyncClient, per_region: int) -> list[dict[str, str]]:
    """Ranking sweep — slow-moving source, useful once per sitting for category spread."""
    found: list[dict[str, str]] = []
    rids = list(RANKING_RIDS)
    random.shuffle(rids)
    for rid in rids:
        for _attempt in range(3):
            try:
                resp = await client.get(
                    "https://api.bilibili.com/x/web-interface/ranking/v2",
                    params={"rid": rid},
                )
                data = (resp.json().get("data") or {}).get("list") or []
                for raw in data[:per_region]:
                    item = _item_from(raw, str(rid))
                    if item:
                        found.append(item)
                break
            except Exception:
                await asyncio.sleep(1.5)
        await asyncio.sleep(0.4)
    return found


async def _collect_popular_page(
    client: httpx.AsyncClient, state: dict[str, Any], count: int
) -> list[dict[str, str]]:
    """Paginated popular feed; the page cursor persists in state, so every
    batch (and every future run) advances to fresh pages instead of
    re-reading the same list."""
    found: list[dict[str, str]] = []
    pn = int(state.get("popular_pn", 1) or 1)
    while len(found) < count:
        page: list[dict[str, Any]] = []
        for _attempt in range(3):
            try:
                resp = await client.get(
                    "https://api.bilibili.com/x/web-interface/popular",
                    params={"ps": 20, "pn": pn},
                )
                page = (resp.json().get("data") or {}).get("list") or []
                break
            except Exception:
                await asyncio.sleep(1.5)
        pn += 1
        if not page:
            break
        for raw in page:
            item = _item_from(raw, "")
            if item:
                found.append(item)
        await asyncio.sleep(0.4)
        if pn > int(state.get("popular_pn", 1) or 1) + 10:
            break  # popular feed exhausted for now
    state["popular_pn"] = pn
    return found[:count]


async def _collect_related_walk(
    client: httpx.AsyncClient, state: dict[str, Any], seeds: int, per_seed: int
) -> list[dict[str, str]]:
    """Graph-walk: related videos of a random sample of covers we already
    hold. Seeds are drawn fresh each batch, so consecutive batches explore
    different neighbourhoods by construction."""
    have = list(state.get("covers", {}))
    if not have:
        return []
    found: list[dict[str, str]] = []
    for seed in random.sample(have, min(seeds, len(have))):
        rel: list[dict[str, Any]] = []
        for _attempt in range(2):
            try:
                resp = await client.get(
                    "https://api.bilibili.com/x/web-interface/archive/related",
                    params={"bvid": seed},
                )
                rel = resp.json().get("data") or []
                break
            except Exception:
                await asyncio.sleep(1.2)
        random.shuffle(rel)
        kept = 0
        for raw in rel:
            item = _item_from(raw, "")
            if item and item["bvid"] not in state["covers"]:
                found.append(item)
                kept += 1
            if kept >= per_seed:
                break
        await asyncio.sleep(0.4)
    return found


async def _embed_covers(svc: Any, items: list[dict[str, str]], state: dict[str, Any]) -> int:
    """Embed each cover, skipping ones already in the accumulated state."""
    added = 0
    for item in items:
        bvid = item["bvid"]
        if bvid in state["covers"]:
            continue
        vector: list[float] = []
        for _attempt in range(3):
            try:
                prepared = await prepare_cover_bytes_for_embedding(
                    item["pic"], max_px=384, quality=72, timeout_seconds=12
                )
                if prepared is None:
                    break
                vector = await svc.embed_image(prepared[0], mime_type=prepared[1])
                if vector:
                    break
            except Exception:
                await asyncio.sleep(1.2)
        if vector:
            state["covers"][bvid] = {
                "vec": vector,
                "rid": item["rid"],
                "tname": item["tname"],
            }
            added += 1
    return added


async def _embed_anchors(svc: Any, state: dict[str, Any]) -> None:
    for text in ANCHOR_TEXTS:
        if text in state["anchors"]:
            continue
        try:
            vec = await svc.embed(text)
        except Exception:
            continue
        if vec:
            state["anchors"][text] = vec


def _percentiles(values: list[float], points: tuple[int, ...]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    return {p: ordered[min(n - 1, p * n // 100)] for p in points}


def _report(state: dict[str, Any]) -> None:
    covers = state.get("covers", {})
    anchors = state.get("anchors", {})
    print("=" * 68)
    print(f"accumulated covers: {len(covers)}   anchors: {len(anchors)}")
    if len(covers) < 2:
        print("not enough covers yet — run more batches")
        return

    keys = list(covers)
    same: list[float] = []
    cross: list[float] = []
    all_pairs: list[float] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = covers[keys[i]], covers[keys[j]]
            sim = cosine_similarity(a["vec"], b["vec"])
            all_pairs.append(sim)
            (same if a.get("rid") == b.get("rid") else cross).append(sim)

    pts = (0, 5, 10, 25, 50, 75, 90, 95, 99, 100)
    print(f"\n--- IMAGE<->IMAGE (same-modal), pairs={len(all_pairs)} ---")
    for p, v in _percentiles(all_pairs, pts).items():
        print(f"  p{p:<3d}: {v:.4f}")
    if same:
        print(f"  same-category  mean={sum(same)/len(same):.4f} (n={len(same)})")
    if cross:
        print(f"  cross-category mean={sum(cross)/len(cross):.4f} (n={len(cross)})")

    if anchors:
        best_per_cover: list[float] = []
        for meta in covers.values():
            best = 0.0
            for avec in anchors.values():
                best = max(best, cosine_similarity(meta["vec"], avec))
            best_per_cover.append(best)
        print(f"\n--- COVER<->TEXT ANCHOR (cross-modal), n={len(best_per_cover)} ---")
        for p, v in _percentiles(best_per_cover, pts).items():
            print(f"  p{p:<3d}: {v:.4f}")

    print("\n--- SUGGESTED CONSTANTS ---")
    pct = _percentiles(all_pairs, (50, 95))
    if pct:
        print(f"  _VISUAL_PROFILE_SIM_FLOOR = {pct[50]:.2f}   # p50 of image pairs")
        print(f"  _VISUAL_PROFILE_SIM_CEIL  = {pct[95]:.2f}   # p95 of image pairs")
        print("  (keyframes: re-measure separately — downscaled stills differ)")
    if anchors:
        apct = _percentiles(best_per_cover, (50, 95))
        print(f"  _VISUAL_COVER_SIM_FLOOR   = {apct[50]:.2f}   # p50 of max anchor cosine")
        print(f"  _VISUAL_COVER_SIM_CEIL    = {apct[95]:.2f}   # p95 of max anchor cosine")
    print(
        "\nNOTE: floor at p50 means about half of all pairs earn some bonus.\n"
        "Raise the floor if that feels too generous once you see live ranking."
    )
    print("=" * 68)


async def _run_batch(batch_index: int, per_region: int) -> None:
    cfg = load_config()
    svc = _registry.build_embedding_service(cfg, _registry.build_llm_registry(cfg))
    if svc is None:
        print("no embedding service configured — check [llm.embedding]")
        return
    if not svc.image_embedding_active():
        print("image embedding inactive — need multimodal_enabled + a capable model")
        return

    state = _load_state()
    async with httpx.AsyncClient(headers=_HEADERS, timeout=25.0, trust_env=False) as client:
        await _embed_anchors(svc, state)
        items: list[dict[str, str]] = []
        # Ranking only on the first batch of a sitting — it's a slow-moving
        # source, so later same-sitting batches would just re-fetch it and
        # dedup to nothing. Popular pagination + related-graph walking are
        # what make batch N+1 genuinely different from batch N.
        if batch_index == 1:
            items += await _collect_ranking(client, per_region)
        items += await _collect_popular_page(client, state, count=per_region * 4)
        items += await _collect_related_walk(client, state, seeds=6, per_seed=3)
        # In-batch dedup (sources can overlap).
        seen: set[str] = set()
        items = [i for i in items if not (i["bvid"] in seen or seen.add(i["bvid"]))]
        print(
            f"[batch {batch_index}] candidates={len(items)} popular_pn={state['popular_pn']}",
            flush=True,
        )
        added = await _embed_covers(svc, items, state)
        print(
            f"[batch {batch_index}] new covers embedded={added} " f"(total={len(state['covers'])})",
            flush=True,
        )
    _save_state(state)


async def _main_async(args: argparse.Namespace) -> int:
    if args.report:
        _report(_load_state())
        return 0
    for index in range(1, max(1, args.batches) + 1):
        await _run_batch(index, args.per_region)
        if index < args.batches:
            await asyncio.sleep(args.sleep)
    _report(_load_state())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=int, default=1, help="how many batches to run")
    parser.add_argument("--per-region", type=int, default=6, help="videos per ranking region")
    parser.add_argument("--sleep", type=float, default=5.0, help="seconds between batches")
    parser.add_argument("--report", action="store_true", help="print stats and exit")
    parser.add_argument("--reset", action="store_true", help="discard accumulated state")
    args = parser.parse_args(argv)
    if args.reset and STATE_PATH.exists():
        STATE_PATH.unlink()
        print("state cleared")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
