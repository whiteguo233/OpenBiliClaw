"""Discovery candidate pool primitives.

Discovery producers enqueue raw cross-platform content here first.  A
separate evaluator can then claim mixed-source batches and persist accepted
items into ``content_cache`` through the existing ``DiscoveredContent`` path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openbiliclaw.discovery.engine import DiscoveredContent

PENDING_EVAL = "pending_eval"
EVALUATING = "evaluating"
EVALUATED = "evaluated"
CACHED = "cached"
REJECTED_LOW_SCORE = "rejected_low_score"
REJECTED_DUPLICATE = "rejected_duplicate"
REJECTED_CACHE_ADMISSION = "rejected_cache_admission"
REJECTED_RECENTLY_VIEWED = "rejected_recently_viewed"
REJECTED_FRANCHISE_QUOTA = "rejected_franchise_quota"
FAILED_EVAL = "failed_eval"


def discovery_candidate_pending_cap(pool_target_count: int) -> int:
    """Return the per-source candidate-row cap used by all enqueue paths."""

    target = max(0, int(pool_target_count))
    return max(target * 2, target + 120, 600)


@dataclass
class DiscoveryCandidateWrite:
    """Serializable row shape for enqueuing raw discovery candidates."""

    candidate_key: str
    source_platform: str
    source_strategy: str
    content_type: str = "video"
    body_text: str = ""
    bvid: str = ""
    content_id: str = ""
    content_url: str = ""
    title: str = ""
    author_name: str = ""
    up_name: str = ""
    up_mid: int = 0
    description: str = ""
    cover_url: str = ""
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    favorite_count: int = 0
    collect_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    reply_count: int = 0
    retweet_count: int = 0
    bookmark_count: int = 0
    tags: list[str] = field(default_factory=list)
    source_context: str = ""
    candidate_tier: str = "primary"
    score_threshold: float = 0.0
    raw_payload: dict[str, Any] = field(default_factory=dict)
    # P1.8 yield provenance: the discovery_keywords.id that produced this
    # candidate (NULL for non-search / legacy / flag-off). Survives the
    # discovery_candidates round-trip so admit can backfill yield.
    source_keyword_id: int | None = None


def _canonical_platform(raw_platform: object) -> str:
    raw = str(raw_platform or "").strip().lower()
    if raw in {"bili", "bilibili", "哔哩哔哩", "b站"}:
        return "bilibili"
    if raw in {"xhs", "xiaohongshu", "小红书"}:
        return "xiaohongshu"
    if raw in {"dy", "douyin", "抖音"}:
        return "douyin"
    if raw in {"yt", "youtube"}:
        return "youtube"
    if raw in {"x", "twitter"}:
        return "twitter"
    return raw or "unknown"


def _canonical_url(raw_url: object) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or parts.path,
            query,
            "",
        )
    )


def candidate_key_for(item: DiscoveredContent) -> str:
    """Return a stable dedupe key for a discovered item."""

    platform = _canonical_platform(item.source_platform or ("bilibili" if item.bvid else ""))
    content_id = str(item.content_id or item.bvid or "").strip()
    if content_id:
        return f"{platform}:{content_id}"
    canonical_url = _canonical_url(item.content_url)
    if canonical_url:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
        return f"{platform}:url:{digest}"
    digest_source = f"{platform}:{item.title}:{item.author_name or item.up_name}"
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:24]
    return f"{platform}:fallback:{digest}"


def resolve_content_type(item_content_type: object, platform: str) -> str:
    """Resolve a candidate's content shape, honoring an explicit value first.

    ``DiscoveredContent.content_type`` defaults to ``"video"`` (the
    platform-neutral sentinel). Sources that set a real shape — e.g. X
    ("tweet"/"thread") — win outright. Items that left the default in
    place fall back to the per-platform default (xiaohongshu → "note",
    everything else → "video").
    """

    explicit = str(item_content_type or "").strip()
    if explicit and explicit != "video":
        return explicit
    return "note" if platform == "xiaohongshu" else "video"


def discovered_content_to_candidate_write(
    item: DiscoveredContent,
    *,
    source_context: str = "",
    raw_payload: dict[str, Any] | None = None,
) -> DiscoveryCandidateWrite:
    """Convert a discovered item into a candidate-pool write payload."""

    platform = _canonical_platform(item.source_platform or ("bilibili" if item.bvid else ""))
    content_id = str(item.content_id or item.bvid or "").strip()
    bvid = str(item.bvid or content_id or "").strip()
    payload = dict(raw_payload or {})
    score_threshold = float(getattr(item, "score_threshold", 0.0) or 0.0)
    if score_threshold > 0 and "score_threshold" not in payload:
        payload["score_threshold"] = score_threshold
    return DiscoveryCandidateWrite(
        candidate_key=candidate_key_for(item),
        source_platform=platform,
        source_strategy=item.source_strategy,
        content_type=resolve_content_type(item.content_type, platform),
        body_text=item.body_text,
        bvid=bvid,
        content_id=content_id or bvid,
        content_url=item.content_url,
        title=item.title,
        author_name=item.author_name or item.up_name,
        up_name=item.up_name or item.author_name,
        up_mid=item.up_mid,
        description=item.description,
        cover_url=item.cover_url,
        duration=item.duration,
        view_count=item.view_count,
        like_count=item.like_count,
        favorite_count=item.favorite_count,
        collect_count=item.collect_count,
        comment_count=item.comment_count,
        share_count=item.share_count,
        danmaku_count=item.danmaku_count,
        reply_count=item.reply_count,
        retweet_count=item.retweet_count,
        bookmark_count=item.bookmark_count,
        tags=list(item.tags),
        source_context=source_context,
        candidate_tier=item.candidate_tier,
        score_threshold=score_threshold,
        raw_payload=payload,
        source_keyword_id=getattr(item, "source_keyword_id", None),
    )


def _coerce_optional_int(value: object) -> int | None:
    """Coerce a DB cell to ``int`` or ``None`` (P1.8 source_keyword_id)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def row_to_discovered_content(row: dict[str, Any]) -> DiscoveredContent:
    """Convert a ``discovery_candidates`` row into ``DiscoveredContent``."""

    content_id = str(row.get("content_id") or row.get("bvid") or "").strip()
    bvid = str(row.get("bvid") or content_id).strip()
    author_name = str(row.get("author_name") or row.get("up_name") or "").strip()
    return DiscoveredContent(
        bvid=bvid,
        title=str(row.get("title") or ""),
        up_name=str(row.get("up_name") or author_name),
        up_mid=int(row.get("up_mid") or 0),
        cover_url=str(row.get("cover_url") or ""),
        duration=int(row.get("duration") or 0),
        view_count=int(row.get("view_count") or 0),
        like_count=int(row.get("like_count") or 0),
        favorite_count=int(row.get("favorite_count") or 0),
        collect_count=int(row.get("collect_count") or 0),
        comment_count=int(row.get("comment_count") or 0),
        share_count=int(row.get("share_count") or 0),
        danmaku_count=int(row.get("danmaku_count") or 0),
        reply_count=int(row.get("reply_count") or 0),
        retweet_count=int(row.get("retweet_count") or 0),
        bookmark_count=int(row.get("bookmark_count") or 0),
        tags=_json_list(row.get("tags")),
        topic_key=str(row.get("topic_key") or ""),
        topic_group=str(row.get("topic_group") or ""),
        style_key=str(row.get("style_key") or ""),
        franchise_key=str(row.get("franchise_key") or ""),
        description=str(row.get("description") or ""),
        source_strategy=str(row.get("source_strategy") or ""),
        relevance_score=float(row.get("relevance_score") or 0.0),
        relevance_reason=str(row.get("relevance_reason") or ""),
        pool_expression=str(row.get("pool_expression") or ""),
        pool_topic_label=str(row.get("pool_topic_label") or ""),
        candidate_tier=str(row.get("candidate_tier") or "primary"),
        content_id=content_id or bvid,
        content_url=str(row.get("content_url") or ""),
        source_platform=_canonical_platform(row.get("source_platform") or "bilibili"),
        author_name=author_name,
        score_threshold=float(row.get("score_threshold") or 0.0),
        body_text=str(row.get("body_text") or ""),
        content_type=str(row.get("content_type") or "video"),
        source_keyword_id=_coerce_optional_int(row.get("source_keyword_id")),
    )
