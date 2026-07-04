"""Normalize an X (Twitter) tweet dict into :class:`DiscoveredContent`.

**Input contract.** ``normalize_tweet`` consumes ONE dict in the shape emitted by
``twitter_cli.serialization.tweet_to_dict`` — i.e. ``twitter-cli`` has already
parsed and unwrapped the raw GraphQL response (``TweetWithVisibilityResults`` /
``note_tweet`` long-form / retweet-quote nesting). We do NOT re-implement
``prinsss/twitter-web-exporter``'s ``extractDataFromResponse`` here; the library
owns that. We only map the plain dict onto the unified ``DiscoveredContent`` shape
(see ``docs/plans/2026-06-08-x-twitter-source-spec.md`` §5.2).

The keys ``tweet_to_dict`` emits (mirror of ``x_client.py``'s contract):

``id`` (rest_id), ``text`` (full tweet text), ``author``
(``{id, name, screenName, profileImageUrl, verified}``), ``metrics``
(``{likes, retweets, replies, quotes, views, bookmarks}``),
``createdAt`` / ``createdAtLocal`` / ``createdAtISO``, ``media``
(``[{type, url, width, height}]``), ``urls``, ``isRetweet``, ``retweetedBy``,
``lang``, ``score``, and the optional ``articleTitle`` / ``articleText``
(long-form note_tweet) / ``quotedTweet``.

This function is **pure and offline**: it never imports ``twitter_cli`` and never
touches the network. Return ``None`` for tombstones / unavailable tweets — those
surface as a missing/empty ``id`` once ``tweet_to_dict`` has run.
"""

from __future__ import annotations

import re
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.strategies._utils import normalize_published_at

# Card titles read better when short; the full text always lives in body_text.
_TITLE_MAX_LEN = 140

# Leading thread markers a self-thread head commonly uses: "1/", "1/7",
# "1.", "(1/n)", or the 🧵 emoji anywhere in the first line.
_THREAD_MARKER_RE = re.compile(r"^\s*\(?\s*1\s*[/.)]")
_THREAD_EMOJI = "\U0001f9f5"  # 🧵

# Hashtags: "#word" with unicode letters/digits/underscore, no leading digit-only.
_HASHTAG_RE = re.compile(r"#(\w*[^\W\d_]\w*)", re.UNICODE)


def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_media_url(media: Any) -> str:
    """Return the first media thumbnail URL, or "" for a text-only tweet."""
    if not isinstance(media, list):
        return ""
    for entry in media:
        if isinstance(entry, dict):
            url = _as_str(entry.get("url"))
            if url:
                return url
    return ""


def _extract_hashtags(text: str) -> list[str]:
    """Pull hashtags out of the tweet text (deduped, order-preserving).

    ``tweet_to_dict`` does not emit an ``entities``/``hashtags`` block, so we
    recover them from the text. Returns the tag bodies without the leading ``#``.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for match in _HASHTAG_RE.finditer(text or ""):
        tag = match.group(1)
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return (text or "").strip()


def _truncate(text: str, limit: int = _TITLE_MAX_LEN) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"  # … ellipsis


def _looks_like_thread(text: str) -> bool:
    first = _first_line(text)
    if _THREAD_MARKER_RE.match(first):
        return True
    return _THREAD_EMOJI in first


def normalize_tweet(
    raw: dict[str, Any],
    *,
    source_strategy: str = "",
) -> DiscoveredContent | None:
    """Map one ``tweet_to_dict`` dict onto :class:`DiscoveredContent`.

    Returns ``None`` for tombstones / unavailable tweets — detected as a missing
    or empty ``id`` (the only durable signal once ``tweet_to_dict`` has parsed
    the raw response).
    """
    if not isinstance(raw, dict):
        return None

    tweet_id = _as_str(raw.get("id"))
    if not tweet_id:
        return None

    raw_author = raw.get("author")
    author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
    screen_name = _as_str(author.get("screenName"))
    handle = screen_name or "i"  # x.com/i/status/<id> resolves even without a handle
    author_name = f"@{screen_name}" if screen_name else ""

    text = _as_str(raw.get("text"))
    article_text = _as_str(raw.get("articleText"))
    article_title = _as_str(raw.get("articleTitle"))

    # body_text: long-form note_tweet wins, else the full tweet text.
    body_text = article_text or text

    # title: a note's own title wins; otherwise the (truncated) first line.
    title = _truncate(article_title) if article_title else _truncate(_first_line(text))

    # content_type: note_tweet long-form OR a thread-marked head → "thread".
    is_thread = bool(article_text) or _looks_like_thread(text)
    content_type = "thread" if is_thread else "tweet"

    raw_metrics = raw.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    view_count = _as_int(metrics.get("views"))
    like_count = _as_int(metrics.get("likes"))
    reply_count = _as_int(metrics.get("replies"))
    retweet_count = _as_int(metrics.get("retweets"))
    quote_count = _as_int(metrics.get("quotes"))
    bookmark_count = _as_int(metrics.get("bookmarks"))

    cover_url = _first_media_url(raw.get("media"))
    tags = _extract_hashtags(text)

    return DiscoveredContent(
        title=title,
        content_id=tweet_id,
        content_url=f"https://x.com/{handle}/status/{tweet_id}",
        source_platform="twitter",
        source_strategy=source_strategy,
        author_name=author_name,
        body_text=body_text,
        content_type=content_type,
        cover_url=cover_url,
        view_count=view_count,
        like_count=like_count,
        favorite_count=bookmark_count,
        comment_count=reply_count,
        share_count=retweet_count + quote_count,
        reply_count=reply_count,
        retweet_count=retweet_count,
        bookmark_count=bookmark_count,
        tags=tags,
        description=body_text,
        published_at=normalize_published_at(
            raw.get("createdAtISO"),
            raw.get("createdAt"),
        ),
    )
