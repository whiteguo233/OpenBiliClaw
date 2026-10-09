from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from openbiliclaw.sources.platforms import normalize_source_platform

_LOCAL_ONLY_NATIVE_SAVE_PLATFORMS = frozenset({"github", "weibo", "tiktok", "instagram"})


def canonical_source_platform(value: str) -> str:
    """Resolve aliases through the canonical source-family registry."""

    return normalize_source_platform(value)


def is_native_save_local_only(value: str) -> bool:
    """Return whether a platform intentionally has no upstream save action."""

    return canonical_source_platform(value) in _LOCAL_ONLY_NATIVE_SAVE_PLATFORMS


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def make_item_key(source_platform: str, content_id: str, content_url: str = "") -> str:
    platform = canonical_source_platform(source_platform)
    stable_id = content_id.strip()
    if not platform:
        raise ValueError("source_platform is required")
    if stable_id:
        return f"{platform}:{stable_id}"
    canonical_url = _canonical_url(content_url)
    if not canonical_url:
        raise ValueError("content_id or canonical content_url is required")
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return f"{platform}:url:{digest}"


def content_storage_key(source_platform: str, content_id: str, content_url: str = "") -> str:
    """Keep legacy Bilibili cache keys; namespace every other platform."""
    platform = canonical_source_platform(source_platform)
    if platform == "bilibili" and content_id.strip():
        return content_id.strip()
    return make_item_key(platform, content_id, content_url)
