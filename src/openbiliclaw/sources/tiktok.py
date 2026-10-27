"""TikTok discovery client backed by yt-dlp (no login, no cookie).

Covers exactly the surfaces yt-dlp's TikTok extractors support for
anonymous metadata listing (verified against yt-dlp 2026.03.17):

  - ``get_user_videos``  — creator uploads via the ``tiktok:user`` extractor
  - ``get_tag_videos``   — hashtag listing via the ``tiktok:tag`` extractor
  - ``get_video_metadata`` — single-video metadata via the ``TikTok`` extractor

yt-dlp ships no TikTok *search* extractor, so there is deliberately no
search method: keyword-planner words are mapped onto hashtags by the
strategy layer instead. All blocking yt-dlp calls run in the default
thread executor; ``extract_flat`` still yields rich aweme entries (title,
uploader, timestamp, stats, thumbnails) because both playlist extractors
parse the web API item list. Nothing here downloads media.

Field-name notes (yt-dlp ``_parse_aweme_video_web`` flat entries):
  id / title (desc truncated) / description (full desc)
  channel (nickname) / uploader (unique id) / uploader_id / channel_id
  timestamp (unix seconds) / duration / thumbnails [{url, ...}]
  view_count / like_count / comment_count / repost_count / save_count
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.published_time import normalize_published_time

logger = logging.getLogger(__name__)

_TIKTOK_BASE_URL = "https://www.tiktok.com"

# Shared yt-dlp options for anonymous metadata extraction — never downloads.
_YTDLP_FLAT_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
    "skip_download": True,
    "noplaylist": False,
    "ignoreerrors": True,
    "socket_timeout": 20,
    # TikTok's edge aggressively resets connections from datacenter IPs;
    # retries are cheap for metadata-only extraction and materially raise
    # the success rate observed in real-network runs.
    "retries": 5,
    "extractor_retries": 3,
}

_YTDLP_DETAIL_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "skip_download": True,
    "noplaylist": True,
    "socket_timeout": 20,
    "retries": 5,
    "extractor_retries": 3,
}

_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_TAG_PATTERN = re.compile(r"^[\w-]+$", re.UNICODE)
# TikTok's web API reports ``video.duration`` in milliseconds; yt-dlp passes
# it through unscaled. A genuine TikTok cannot exceed 60 minutes, so any
# value above two hours must be milliseconds.
_DURATION_MILLISECONDS_THRESHOLD_SECONDS = 7200


def _ytdlp_options(**extra: Any) -> dict[str, Any]:
    """Base yt-dlp options plus the overseas outbound proxy when set.

    TikTok is an overseas source, so it honors ``[network].proxy`` exactly
    like the YouTube client; omitting the key keeps yt-dlp's default
    (env-inheriting) behavior when no proxy is configured.
    """
    from openbiliclaw.network import outbound_ytdlp_proxy

    options: dict[str, Any] = {**extra}
    proxy = outbound_ytdlp_proxy()
    if proxy is not None:
        options["proxy"] = proxy
    return options


def normalize_tiktok_handle(value: object) -> str:
    """Normalize a TikTok creator handle (``@`` optional) or return ``""``."""
    handle = str(value or "").strip().lstrip("@").strip()
    if not handle or not _HANDLE_PATTERN.fullmatch(handle):
        return ""
    return handle


def normalize_tiktok_tag(value: object) -> str:
    """Normalize a hashtag (leading ``#`` / whitespace stripped) or ``""``."""
    tag = str(value or "").strip().lstrip("#").strip()
    if not tag or not _TAG_PATTERN.fullmatch(tag):
        return ""
    return tag


def tag_from_query(value: object) -> str:
    """Map a free-form search word onto a TikTok hashtag candidate.

    TikTok hashtags carry no spaces, so a keyword-planner phrase like
    ``machine learning`` becomes ``machinelearning``. Returns ``""`` when
    nothing usable remains.
    """
    compact = re.sub(r"[^\w-]", "", str(value or "").strip().lstrip("#"))
    return compact if compact and _TAG_PATTERN.fullmatch(compact) else ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _normalize_duration_seconds(value: Any) -> int:
    duration = _to_int(value)
    if duration > _DURATION_MILLISECONDS_THRESHOLD_SECONDS:
        return duration // 1000
    return duration


def _cover_url(raw: dict[str, Any]) -> str:
    thumbnails = raw.get("thumbnails")
    if isinstance(thumbnails, list):
        urls = [
            str(item.get("url", "")).strip()
            for item in thumbnails
            if isinstance(item, dict) and str(item.get("url", "")).strip()
        ]
        if urls:
            return urls[0]
    return _first_text(raw.get("thumbnail"))


def normalize_tiktok_video(
    raw: dict[str, Any],
    *,
    source_strategy: str,
) -> DiscoveredContent | None:
    """Map a yt-dlp TikTok (flat or full) entry to ``DiscoveredContent``."""
    video_id = _first_text(raw.get("id"), raw.get("video_id"), raw.get("aweme_id"))
    if not video_id:
        return None

    description = _first_text(raw.get("description"))
    title = _first_text(raw.get("title"), raw.get("fulltitle"), description)
    if not title:
        return None

    author = _first_text(
        raw.get("channel"),
        raw.get("uploader"),
        raw.get("creator"),
        raw.get("artist"),
    )
    content_url = _first_text(raw.get("webpage_url"), raw.get("url"))
    if not content_url.startswith("http"):
        owner = _first_text(raw.get("uploader"), raw.get("channel")) or "tiktok"
        content_url = f"{_TIKTOK_BASE_URL}/@{owner}/video/{video_id}"

    published = normalize_published_time(
        raw.get("timestamp") or raw.get("release_timestamp") or raw.get("upload_date"),
    )

    return DiscoveredContent(
        content_id=video_id,
        content_url=content_url,
        source_platform="tiktok",
        title=title,
        author_name=author,
        up_name=author,
        cover_url=_cover_url(raw),
        duration=_normalize_duration_seconds(raw.get("duration")),
        view_count=_to_int(raw.get("view_count") or raw.get("play_count")),
        like_count=_to_int(raw.get("like_count") or raw.get("digg_count")),
        comment_count=_to_int(raw.get("comment_count")),
        share_count=_to_int(raw.get("repost_count") or raw.get("share_count")),
        favorite_count=_to_int(raw.get("save_count") or raw.get("collect_count")),
        collect_count=_to_int(raw.get("save_count") or raw.get("collect_count")),
        description=description[:300],
        source_strategy=source_strategy,
        published_at=published.published_at,
        published_label=published.published_label,
        source_metadata={"tiktok_aweme_id": video_id},
    )


# ---------------------------------------------------------------------------
# Blocking helpers (run in executor)
# ---------------------------------------------------------------------------


def _ytdlp_entries(info: Any, limit: int) -> list[dict[str, Any]]:
    """Map a yt-dlp playlist info dict to entry dicts for normalize_tiktok_video."""
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if not isinstance(entries, list):
        return []
    results: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        if isinstance(entry, dict):
            results.append(dict(entry))
    return results


def _ytdlp_user_videos(handle: str, limit: int) -> list[dict[str, Any]]:
    url = f"{_TIKTOK_BASE_URL}/@{handle}"
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

        with YoutubeDL(_ytdlp_options(**_YTDLP_FLAT_OPTIONS, playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp tiktok:user(%r) failed: %s", handle, exc)
        return []


def _ytdlp_tag_videos(tag: str, limit: int) -> list[dict[str, Any]]:
    url = f"{_TIKTOK_BASE_URL}/tag/{tag}"
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(_ytdlp_options(**_YTDLP_FLAT_OPTIONS, playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp tiktok:tag(%r) failed: %s", tag, exc)
        return []


def _ytdlp_video_metadata(url: str) -> dict[str, Any]:
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(_ytdlp_options(**_YTDLP_DETAIL_OPTIONS)) as ydl:
            info = ydl.extract_info(url, download=False)
        return dict(info) if isinstance(info, dict) else {}
    except Exception as exc:
        logger.warning("yt-dlp tiktok video(%r) failed: %s", url, exc)
        return {}


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


@dataclass
class TiktokClient:
    """Async TikTok discovery client backed by yt-dlp flat extraction."""

    _executor: Any = field(default=None, init=False, repr=False)

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch one creator's recent uploads via the ``tiktok:user`` extractor."""
        normalized = normalize_tiktok_handle(handle)
        if not normalized:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(_ytdlp_user_videos, normalized, max(1, limit))
        )

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[dict[str, Any]]:
        """Fetch a hashtag's video listing via the ``tiktok:tag`` extractor."""
        normalized = normalize_tiktok_tag(tag)
        if not normalized:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(_ytdlp_tag_videos, normalized, max(1, limit))
        )

    async def get_video_metadata(self, url: str) -> dict[str, Any]:
        """Fetch one video's full metadata via the ``TikTok`` extractor."""
        text = str(url or "").strip()
        if not text:
            return {}
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_ytdlp_video_metadata, text))
