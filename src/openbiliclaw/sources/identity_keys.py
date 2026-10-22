"""Shared cross-source identity-key normalization.

Maps an event URL to a stable identity key so the same content is
recognized across sources (extension vs account_sync vs server-built
events). Two independent consumers rely on this:

1. ``runtime.account_sync`` cross-source dedup (originally home of these
   helpers, PR #85) — collapses ``/i/status/<id>`` and
   ``/<handle>/status/<id>`` onto one tweet key, etc.
2. Retraction discounting (event-capture-completion Phase 0) — a
   retraction event's URL is normalized to the same key as the positive
   event it undoes, so the discount hits the right rows.

Keys are globally unique per content id (tweet_id / bvid / mid / xhs
note_id), so there is no "same URL, different content" collision to
window against.
"""

from __future__ import annotations

import re

# xhs note ids are 24 hex chars. All three canonical note URL shapes carry
# the id as the trailing path segment before any query string.
_XHS_NOTE_RE = re.compile(
    r"xiaohongshu\.com/(?:explore|discovery/item|search_result)/([0-9a-fA-F]{24})"
)

# Instagram shortcodes are case-sensitive; never lowercase the capture.
_INSTAGRAM_MEDIA_RE = re.compile(
    r"^(?i:https)://(?i:(?:www\.)?instagram\.com)/(?:p|reel|tv)/([A-Za-z0-9_-]+)"
)


def bvid_from_url(url: str) -> str:
    """Extract the ``BV...`` id from a ``bilibili.com/video/<bvid>`` URL."""
    marker = "bilibili.com/video/"
    if marker not in url:
        return ""
    tail = url.split(marker, 1)[1]
    return tail.split("/", 1)[0].split("?", 1)[0].strip()


def mid_from_url(url: str) -> str:
    """Extract the UP mid from a ``space.bilibili.com/<mid>`` URL."""
    marker = "space.bilibili.com/"
    if marker not in url:
        return ""
    tail = url.split(marker, 1)[1]
    return tail.split("/", 1)[0].split("?", 1)[0].strip()


def tweet_id_from_url(url: str) -> str:
    """Extract the tweet id from the trailing ``/status/<id>`` URL segment.

    Handles both ``x.com/i/status/<id>`` (extension GraphQL tap) and
    ``x.com/<handle>/status/<id>`` (server-side events).
    """
    marker = "/status/"
    if marker not in url:
        return ""
    tail = url.split(marker, 1)[1]
    return tail.split("/", 1)[0].split("?", 1)[0].strip()


def note_id_from_url(url: str) -> str:
    """Extract the 24-hex xhs note id from an ``explore`` / ``discovery/item``
    / ``search_result`` URL. Returns lowercase, or ``""`` when absent."""
    match = _XHS_NOTE_RE.search(url)
    return match.group(1).lower() if match else ""


def instagram_shortcode_from_url(url: str) -> str:
    """Extract a case-sensitive Instagram media shortcode from a canonical URL."""
    match = _INSTAGRAM_MEDIA_RE.search(url)
    return match.group(1) if match else ""


def dedup_key(url: str) -> str:
    """Map an event URL to a normalized cross-source identity key.

    X keys on the tweet id (URL handle differs across sources), bilibili
    videos on the bvid, follows on the UP mid, xhs notes on the note id.
    Empty when no key applies.
    """
    tweet_id = tweet_id_from_url(url)
    if tweet_id:
        return f"x:{tweet_id}"
    bvid = bvid_from_url(url)
    if bvid:
        return f"bv:{bvid}"
    mid = mid_from_url(url)
    if mid:
        return f"mid:{mid}"
    note_id = note_id_from_url(url)
    if note_id:
        return f"xhs:{note_id}"
    instagram_shortcode = instagram_shortcode_from_url(url)
    if instagram_shortcode:
        return f"instagram:{instagram_shortcode}"
    return ""
