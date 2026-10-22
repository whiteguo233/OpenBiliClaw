"""Strict Instagram media and public-user normalization.

Instagram network access is owned by the browser extension.  This module only
accepts the bounded, credential-free row contract returned by that extension
and converts it into the shared discovery model.  Cookies, request headers and
raw response bodies are intentionally outside this API.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.published_time import normalize_published_time

INSTAGRAM_SOURCE_STRATEGIES = {
    "topic": "instagram-topic",
    "creator": "instagram-creator",
}
INSTAGRAM_MEDIA_CONTENT_TYPES = frozenset({"post", "reel", "carousel"})

_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_MEDIA_PATH_RE = re.compile(r"^/(p|reel)/([A-Za-z0-9_-]{3,64})/?$")
_PROFILE_PATH_RE = re.compile(r"^/([A-Za-z0-9._]{1,30})/?$")
_PLACEHOLDER_TEXT = frozenset({"none", "null", "undefined", "nan"})
_MAX_TITLE_CHARS = 300
_MAX_BODY_CHARS = 8_000


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, *, limit: int) -> str:
    """Return bounded scalar text without stringifying containers."""

    if not isinstance(value, str):
        return ""
    cleaned = "".join(
        char
        for char in value
        if not unicodedata.category(char).startswith("C") or char in {"\n", "\t"}
    )
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    normalized = "\n".join(line for line in lines if line).strip()
    if normalized.casefold() in _PLACEHOLDER_TEXT:
        return ""
    return normalized[:limit]


def _positive_id(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value) if value > 0 else ""
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit() and int(normalized) > 0 and len(normalized) <= 40:
            return normalized
    return ""


def _shortcode(value: object) -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if _SHORTCODE_RE.fullmatch(candidate) else ""


def _https_image_url(value: object) -> str:
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    allowed = hostname == "cdninstagram.com" or hostname.endswith(".cdninstagram.com")
    allowed = allowed or hostname == "fbcdn.net" or hostname.endswith(".fbcdn.net")
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password:
        return ""
    return raw


def _media_url(value: object, *, code: str, content_type: str) -> tuple[str, str]:
    if code:
        route = "reel" if content_type == "reel" else "p"
        return f"https://www.instagram.com/{route}/{code}/", code
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return "", ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return "", ""
    hostname = str(parsed.hostname or "").casefold().rstrip(".")
    match = _MEDIA_PATH_RE.fullmatch(parsed.path)
    if parsed.scheme != "https" or hostname not in {"instagram.com", "www.instagram.com"}:
        return "", ""
    if parsed.username or parsed.password or match is None:
        return "", ""
    route, extracted = match.groups()
    expected_route = "reel" if content_type == "reel" else "p"
    if route != expected_route:
        return "", ""
    return f"https://www.instagram.com/{route}/{extracted}/", extracted


def _profile_url(value: object, *, username_hint: str = "") -> tuple[str, str]:
    username = username_hint if _USERNAME_RE.fullmatch(username_hint) else ""
    raw = value.strip() if isinstance(value, str) else ""
    if raw:
        try:
            parsed = urlparse(raw)
        except ValueError:
            return "", ""
        hostname = str(parsed.hostname or "").casefold().rstrip(".")
        match = _PROFILE_PATH_RE.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or hostname not in {"instagram.com", "www.instagram.com"}
            or parsed.username
            or parsed.password
            or match is None
        ):
            return "", ""
        from_url = match.group(1)
        if username and username.casefold() != from_url.casefold():
            return "", ""
        username = from_url
    if not username:
        return "", ""
    canonical_username = username.casefold()
    return f"https://www.instagram.com/{canonical_username}/", canonical_username


def _content_type(row: Mapping[str, Any]) -> str:
    declared = _text(row.get("content_type"), limit=32).casefold()
    if declared in INSTAGRAM_MEDIA_CONTENT_TYPES | {"user"}:
        return declared
    typename = _text(row.get("__typename"), limit=64)
    if typename == "GraphVideo":
        return "reel"
    if typename == "GraphSidecar":
        return "carousel"
    if typename == "GraphImage":
        return "post"
    if typename == "XIGPolarisVideoMedia":
        return "reel"
    if typename == "XIGPolarisImageMedia":
        return "post"
    # Newer Comet/Relay typenames are less stable than the Graph* set above;
    # classify by suffix so a future ``XIGPolarisSidecarMedia`` or
    # ``XIGPolarisVideoMedia`` variant still normalizes to the right bucket.
    if typename.endswith("VideoMedia") or "video" in typename:
        return "reel"
    if typename.endswith("SidecarMedia") or "sidecar" in typename:
        return "carousel"
    if typename.endswith("ImageMedia") or "image" in typename:
        return "post"
    media_type = row.get("media_type")
    if media_type == 2:
        return "reel"
    if media_type == 8:
        return "carousel"
    if media_type == 1:
        return "post"
    return ""


def _caption(row: Mapping[str, Any]) -> str:
    caption = row.get("caption")
    if isinstance(caption, Mapping):
        caption = caption.get("text")
    return _text(
        row.get("description") or caption or row.get("title"),
        limit=_MAX_BODY_CHARS,
    )


def _author(row: Mapping[str, Any]) -> tuple[str, str]:
    user = _mapping(row.get("user") or row.get("owner"))
    author_id = _positive_id(row.get("author_id") or user.get("id") or user.get("pk"))
    author_name = _text(
        row.get("author_name") or user.get("username"),
        limit=128,
    )
    return author_id, author_name


def _published_at(row: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """Use only an authoritative media timestamp and reject future values."""

    raw = row.get("published_at")
    if raw in (None, ""):
        raw = row.get("taken_at") or row.get("taken_at_timestamp")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    value = normalize_published_time(raw, now=current).published_at
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return value if parsed <= current else ""


def instagram_media_to_content(
    row: Mapping[str, Any] | object,
    *,
    strategy: str = INSTAGRAM_SOURCE_STRATEGIES["topic"],
    source_keyword_id: int | None = None,
    now: datetime | None = None,
) -> DiscoveredContent | None:
    """Normalize one credential-free Instagram media row.

    Aggregate engagement fields are deliberately absent.  A private liked or
    saved membership is an account event, not evidence of a public aggregate.
    """

    if not isinstance(row, Mapping):
        return None
    content_id = _positive_id(row.get("pk")) or _positive_id(row.get("id") or row.get("content_id"))
    content_type = _content_type(row)
    if not content_id or content_type not in INSTAGRAM_MEDIA_CONTENT_TYPES:
        return None
    code = _shortcode(row.get("code") or row.get("shortcode"))
    url, code = _media_url(
        row.get("url") or row.get("content_url"),
        code=code,
        content_type=content_type,
    )
    if not url or not code:
        return None
    author_id, author_name = _author(row)
    body = _caption(row)
    explicit_title = _text(row.get("title"), limit=_MAX_TITLE_CHARS)
    title = explicit_title or body.split("\n", 1)[0][:_MAX_TITLE_CHARS]
    if not title:
        label = {"post": "Post", "reel": "Reel", "carousel": "Carousel"}[content_type]
        title = f"Instagram {label}"
    cover = _https_image_url(
        row.get("cover_url") or row.get("display_uri") or row.get("thumbnail_url")
    )
    normalized_keyword_id = (
        source_keyword_id
        if isinstance(source_keyword_id, int)
        and not isinstance(source_keyword_id, bool)
        and source_keyword_id > 0
        else None
    )
    author_mid = int(author_id) if author_id and int(author_id) <= 2**63 - 1 else 0
    return DiscoveredContent(
        bvid=content_id,
        content_id=content_id,
        content_url=url,
        source_platform="instagram",
        content_type=content_type,
        source_strategy=_text(strategy, limit=80) or INSTAGRAM_SOURCE_STRATEGIES["topic"],
        source_keyword_id=normalized_keyword_id,
        title=title,
        up_name=author_name,
        author_name=author_name,
        up_mid=author_mid,
        body_text=body,
        description=body[:500],
        cover_url=cover,
        published_at=_published_at(row, now=now),
        engagement_available=[],
        view_count=0,
        like_count=0,
        favorite_count=0,
        comment_count=0,
        share_count=0,
        danmaku_count=0,
    )


def instagram_user_to_content(
    row: Mapping[str, Any] | object,
    *,
    strategy: str = INSTAGRAM_SOURCE_STRATEGIES["creator"],
    source_keyword_id: int | None = None,
) -> DiscoveredContent | None:
    """Normalize one public user row without confusing it with media identity."""

    if not isinstance(row, Mapping) or _content_type(row) != "user":
        return None
    user_id = _positive_id(row.get("id") or row.get("author_id") or row.get("user_id"))
    author_name = _text(row.get("author_name") or row.get("username"), limit=128)
    url, username = _profile_url(
        row.get("url") or row.get("content_url"),
        username_hint=author_name,
    )
    if not user_id or not url or not username:
        return None
    title = _text(row.get("title"), limit=_MAX_TITLE_CHARS) or f"@{username}"
    description = _text(row.get("description") or row.get("biography"), limit=_MAX_BODY_CHARS)
    normalized_keyword_id = (
        source_keyword_id
        if isinstance(source_keyword_id, int)
        and not isinstance(source_keyword_id, bool)
        and source_keyword_id > 0
        else None
    )
    return DiscoveredContent(
        bvid=f"user:{user_id}",
        content_id=f"user:{user_id}",
        content_url=url,
        source_platform="instagram",
        content_type="user",
        source_strategy=_text(strategy, limit=80) or INSTAGRAM_SOURCE_STRATEGIES["creator"],
        source_keyword_id=normalized_keyword_id,
        title=title,
        up_name=username,
        author_name=username,
        body_text=description,
        description=description[:500],
        cover_url=_https_image_url(row.get("cover_url") or row.get("profile_pic_url")),
        engagement_available=[],
    )


def instagram_item_to_content(
    row: Mapping[str, Any] | object,
    *,
    strategy: str,
    source_keyword_id: int | None = None,
    now: datetime | None = None,
) -> DiscoveredContent | None:
    """Dispatch a sanitized row to the media or user normalizer."""

    if isinstance(row, Mapping) and _content_type(row) == "user":
        return instagram_user_to_content(
            row,
            strategy=strategy,
            source_keyword_id=source_keyword_id,
        )
    return instagram_media_to_content(
        row,
        strategy=strategy,
        source_keyword_id=source_keyword_id,
        now=now,
    )


def non_negative_int(value: object) -> int:
    """Strict integer helper retained for fixture/schema regression tests."""

    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value)) if math.isfinite(value) else 0
    if isinstance(value, str):
        candidate = value.strip()
        return int(candidate) if candidate.isdigit() else 0
    return 0


__all__ = [
    "INSTAGRAM_MEDIA_CONTENT_TYPES",
    "INSTAGRAM_SOURCE_STRATEGIES",
    "instagram_item_to_content",
    "instagram_media_to_content",
    "instagram_user_to_content",
    "non_negative_int",
]
