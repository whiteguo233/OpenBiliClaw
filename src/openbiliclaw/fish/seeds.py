"""Seed videos for the fish, taken verbatim from the ranked pool.

Both the action layer and the comment-thread discovery route start from the
same place: rows the main pipeline already scored, admitted and (optionally)
explained. The fish never re-scores them — ``exclude_processed=True`` also
drops anything the owner already liked / disliked / dismissed / commented on,
so the fish does not walk into a place the owner has already reacted to.

The fish's *own* history never touches these rows; it lives in
``fish/ledger.jsonl``.
"""

from __future__ import annotations

from typing import Any

from .engine import FishCandidate


def recommendation_pool_seeds(database: Any, limit: int) -> list[FishCandidate]:
    """Return up to ``limit`` Bilibili seeds from the recommendation pool.

    The database call is deliberately over-fetched (4×) before filtering
    because the pool mixes platforms and non-BV ids; ``limit`` applies to the
    usable rows only.
    """

    target = max(1, int(limit))
    rows = database.get_recommendations(
        limit=max(target * 4, 12),
        exclude_processed=True,
    )
    seeds: list[FishCandidate] = []
    for row in rows:
        if str(row.get("source_platform") or "bilibili") != "bilibili":
            continue
        bvid = str(row.get("bvid") or row.get("content_id") or "")
        if not bvid.startswith("BV"):
            continue
        seeds.append(
            FishCandidate(
                bvid=bvid,
                title=str(row.get("title") or ""),
                up_name=str(row.get("up_name") or ""),
                score=float(row.get("confidence") or 0.0),
                description=str(row.get("description") or ""),
                # Trace id only: the ledger records which recommendation the
                # fish acted on; nothing is written back to that row.
                recommendation_id=int(row.get("id") or 0),
                topic=str(row.get("topic") or row.get("pool_topic_label") or ""),
                view_count=int(row.get("view_count") or 0),
                published_at=str(row.get("published_at") or ""),
            )
        )
        if len(seeds) >= target:
            break
    return seeds
