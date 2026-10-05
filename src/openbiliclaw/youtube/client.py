"""YouTube scraper client for discovery strategies.

Wraps scrapetube (search + channel) and YouTube InnerTube API (trending)
behind a single async interface, with anonymous yt-dlp flat-playlist
extraction as the fallback layer where yt-dlp has a working entry point
(yt-dlp tracks YouTube markup changes fastest, so a broken primary
degrades instead of silently starving the source). All blocking calls run
in the default thread executor so they don't stall the event loop.

Supports three discovery modes:
  - search_videos       — keyword search via scrapetube → yt-dlp ytsearch
  - get_trending        — InnerTube FEtrending → public topic pages
  - get_channel_videos  — channel uploads via scrapetube → yt-dlp

Field-name notes (scrapetube returns YouTube's internal renderer dicts):
  title         → {"runs": [{"text": "..."}]}  or  {"simpleText": "..."}
  ownerText     → {"runs": [{"text": "channel name"}]}
  viewCountText → {"simpleText": "1,234,567 views"}
  lengthText    → {"simpleText": "12:34"}
  thumbnail     → {"thumbnails": [{"url": "...", "width": N, "height": N}]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.published_time import normalize_published_time

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_DEFAULT_REGION = "US"
_TRENDING_TOPIC_PATHS: tuple[str, ...] = (
    "gaming",
    "sports",
    "news",
    "podcasts",
    "live",
)

# InnerTube client config for anonymous web requests
_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_INNERTUBE_CLIENT_VERSION = "2.20240101.00.00"
_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": _INNERTUBE_CLIENT_VERSION,
        "hl": "en",
    }
}

# Shared yt-dlp options for anonymous flat-playlist metadata extraction
# (search / channel / trending fallbacks — never downloads media).
_YTDLP_FLAT_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "extract_flat": True,
    "skip_download": True,
    "noplaylist": False,
    "ignoreerrors": True,
    "socket_timeout": 20,
}


def _ytdlp_options(**extra: Any) -> dict[str, Any]:
    """Base flat yt-dlp options plus the overseas outbound proxy when set.

    YouTube is an overseas source, so it honors ``[network].proxy``. yt-dlp's
    native ``proxy`` option routes its HTTP through it; omitting the key keeps
    yt-dlp's default (env-inheriting) behavior — zero drift when unset.
    """
    from openbiliclaw.network import outbound_ytdlp_proxy

    options: dict[str, Any] = {**_YTDLP_FLAT_OPTIONS, **extra}
    proxy = outbound_ytdlp_proxy()
    if proxy is not None:
        options["proxy"] = proxy
    return options


@dataclass(frozen=True)
class InnerTubeConfig:
    api_key: str = _INNERTUBE_KEY
    client_version: str = _INNERTUBE_CLIENT_VERSION
    client_name: str = "WEB"
    client_name_header: str = "1"


# ---------------------------------------------------------------------------
# Blocking helpers (run in executor)
# ---------------------------------------------------------------------------


def _scrapetube_search(query: str, limit: int) -> list[dict[str, Any]]:
    try:
        import scrapetube  # type: ignore[import-untyped]

        from openbiliclaw.network import outbound_requests_proxies

        results = [
            dict(v)
            for v in scrapetube.get_search(
                query,
                results_type="video",
                limit=limit,
                proxies=outbound_requests_proxies(),
            )
        ]
        if results:
            return results
        logger.info("scrapetube.search(%r) returned 0 items; falling back to yt-dlp", query)
    except Exception as exc:
        logger.warning("scrapetube.search(%r) failed (%s); falling back to yt-dlp", query, exc)
    return _ytdlp_search(query, limit)


def _ytdlp_search(query: str, limit: int) -> list[dict[str, Any]]:
    """Search fallback via yt-dlp's ``ytsearchN:`` pseudo-URL.

    yt-dlp tracks YouTube markup changes far faster than scrapetube, so a
    broken/blocked scrapetube search degrades to a slower-but-working path
    instead of silently starving the YouTube candidate supply.
    """
    if not query.strip():
        return []
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]

        with YoutubeDL(_ytdlp_options()) as ydl:
            info = ydl.extract_info(f"ytsearch{max(1, limit)}:{query}", download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp.search(%r) failed: %s", query, exc)
        return []


def _scrapetube_channel(channel_id: str, limit: int) -> list[dict[str, Any]]:
    try:
        import scrapetube

        from openbiliclaw.network import outbound_requests_proxies

        proxies = outbound_requests_proxies()
        if channel_id.startswith("@") or channel_id.startswith("UC"):
            results = [
                dict(v)
                for v in scrapetube.get_channel(
                    channel_url=None,
                    channel_id=channel_id,
                    limit=limit,
                    proxies=proxies,
                )
            ]
        else:
            results = [
                dict(v)
                for v in scrapetube.get_channel(
                    channel_url=channel_id,
                    limit=limit,
                    proxies=proxies,
                )
            ]
        if results:
            return results
    except Exception as exc:
        logger.warning("scrapetube.channel(%r) failed: %s", channel_id, exc)
    return _ytdlp_channel(channel_id, limit)


def _ytdlp_entries(info: Any, limit: int) -> list[dict[str, Any]]:
    """Map a yt-dlp flat-playlist info dict to video dicts for normalize_yt_video."""
    if not isinstance(info, dict):
        return []
    entries = info.get("entries")
    if not isinstance(entries, list):
        return []
    results: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        if not item.get("videoId") and item.get("id"):
            item["videoId"] = item["id"]
        if not item.get("channel") and info.get("channel"):
            item["channel"] = info.get("channel")
        results.append(item)
    return results


def _ytdlp_channel(channel_ref: str, limit: int) -> list[dict[str, Any]]:
    """Fetch channel uploads with yt-dlp when scrapetube cannot resolve handles."""
    url = _channel_uploads_url(channel_ref)
    if not url:
        return []
    try:
        from yt_dlp import YoutubeDL

        with YoutubeDL(_ytdlp_options(playlistend=limit)) as ydl:
            info = ydl.extract_info(url, download=False)
        return _ytdlp_entries(info, limit)
    except Exception as exc:
        logger.warning("yt-dlp.channel(%r) failed: %s", channel_ref, exc)
        return []


def _channel_uploads_url(channel_ref: str) -> str:
    ref = channel_ref.strip()
    if not ref:
        return ""
    if ref.startswith("http://") or ref.startswith("https://"):
        base = ref.rstrip("/")
        return base if base.endswith("/videos") else f"{base}/videos"
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}/videos"
    if ref.startswith("UC"):
        return f"https://www.youtube.com/channel/{ref}/videos"
    return ""


def _innertube_trending(region_code: str, limit: int) -> list[dict[str, Any]]:
    """Fetch YouTube trending via the InnerTube browse API (no API key needed).

    Uses the FEtrending browseId when YouTube still exposes it. If that
    endpoint is unavailable, falls back to public YouTube topic pages that
    still ship video renderers in ytInitialData. (yt-dlp is deliberately NOT
    a layer here: /feed/trending was removed by YouTube — verified 2026-07 to
    redirect to the home page — and yt-dlp's flat extraction gets nothing out
    of the shelf-based topic/browse surfaces.)
    Returns a flat list of video dicts ready for normalize_yt_video().
    """
    results = _innertube_trending_feed(region_code, limit)
    if results:
        return results
    return _topic_page_trending(region_code, limit)


def _innertube_trending_feed(region_code: str, limit: int) -> list[dict[str, Any]]:
    """Fetch the legacy FEtrending InnerTube browse feed."""
    try:
        config = _fetch_innertube_config(region_code)
        payload = json.dumps(
            {
                "browseId": "FEtrending",
                "context": {
                    **_INNERTUBE_CONTEXT,
                    "client": {
                        **_INNERTUBE_CONTEXT["client"],
                        "clientName": config.client_name,
                        "clientVersion": config.client_version,
                        "gl": region_code,
                    },
                },
            },
            ensure_ascii=False,
        ).encode()

        url = f"https://www.youtube.com/youtubei/v1/browse?key={config.api_key}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-YouTube-Client-Name": config.client_name_header,
            "X-YouTube-Client-Version": config.client_version,
        }
        from openbiliclaw.network import outbound_httpx_kwargs

        with httpx.Client(timeout=15, **outbound_httpx_kwargs()) as client:
            response = client.post(url, content=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        return list(_extract_innertube_videos(data, limit=limit))
    except Exception as exc:
        logger.warning("InnerTube trending(%s) failed: %s", region_code, exc)
        return []


def _topic_page_trending(
    region_code: str,
    limit: int,
    *,
    fetch_html: Callable[[str], str] | None = None,
    topic_paths: tuple[str, ...] = _TRENDING_TOPIC_PATHS,
) -> list[dict[str, Any]]:
    """Fallback trending supply from public YouTube topic pages."""
    fetch = fetch_html or _fetch_youtube_html
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for path in topic_paths:
        if len(results) >= limit:
            break
        url = f"https://www.youtube.com/{path}?gl={region_code}&persist_gl=1"
        try:
            html = fetch(url)
        except Exception as exc:
            logger.warning("YouTube topic page %s failed: %s", path, exc)
            continue
        for item in _extract_yt_initial_data_videos(html, limit=limit):
            video_id = str(item.get("videoId") or item.get("id") or "").strip()
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            results.append(item)
            if len(results) >= limit:
                break
    if results:
        logger.info("YouTube topic-page trending fallback returned %d videos", len(results))
    return results


def _fetch_youtube_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "CONSENT=YES+1",
    }
    from openbiliclaw.network import outbound_httpx_kwargs

    with httpx.Client(timeout=20, **outbound_httpx_kwargs()) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.text


def _extract_yt_initial_data_videos(html: str, limit: int) -> list[dict[str, Any]]:
    data = _extract_yt_initial_data(html)
    if data is None:
        return []
    return _extract_innertube_videos(data, limit=limit)


def _extract_yt_initial_data(html: str) -> dict[str, Any] | None:
    for marker in (
        "var ytInitialData",
        'window["ytInitialData"]',
        "window['ytInitialData']",
    ):
        start = html.find(marker)
        if start < 0:
            continue
        parsed = _extract_json_object_after(html, start)
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_json_object_after(text: str, start: int) -> object | None:
    object_start = text.find("{", start)
    if object_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(object_start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                parsed: object = json.loads(text[object_start : index + 1])
                return parsed
    return None


def _fetch_innertube_config(region_code: str) -> InnerTubeConfig:
    """Read the current web client config from YouTube's trending page."""
    try:
        url = f"https://www.youtube.com/feed/trending?gl={region_code}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        from openbiliclaw.network import outbound_httpx_kwargs

        with httpx.Client(timeout=15, **outbound_httpx_kwargs()) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
        return _extract_innertube_config(html)
    except Exception as exc:
        logger.debug("Failed to read YouTube InnerTube config; using fallback: %s", exc)
        return InnerTubeConfig()


def _extract_innertube_config(html: str) -> InnerTubeConfig:
    """Extract InnerTube config constants from a YouTube HTML response."""
    api_key = _extract_js_string(html, "INNERTUBE_API_KEY") or _INNERTUBE_KEY
    client_version = (
        _extract_js_string(html, "INNERTUBE_CLIENT_VERSION") or _INNERTUBE_CLIENT_VERSION
    )
    client_name_header = _extract_js_number(html, "INNERTUBE_CONTEXT_CLIENT_NAME") or "1"
    return InnerTubeConfig(
        api_key=api_key,
        client_version=client_version,
        client_name_header=client_name_header,
    )


def _extract_js_string(html: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', html)
    return match.group(1) if match else ""


def _extract_js_number(html: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+)', html)
    return match.group(1) if match else ""


def _extract_innertube_videos(data: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    """Walk InnerTube's nested renderer tree and extract video renderer dicts."""
    results: list[dict[str, Any]] = []
    _walk(data, results, limit)
    return results


def _walk(node: Any, out: list[dict[str, Any]], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        if "videoId" in node and "title" in node:
            out.append(node)
            return
        for v in node.values():
            _walk(v, out, limit)
    elif isinstance(node, list):
        for item in node:
            if len(out) >= limit:
                return
            _walk(item, out, limit)


# ---------------------------------------------------------------------------
# Exact publication time enrichment (channel Atom feed)
# ---------------------------------------------------------------------------

_RSS_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_RSS_VIDEO_ID_RE = re.compile(r"<yt:videoId>([^<]+)</yt:videoId>")
_RSS_PUBLISHED_RE = re.compile(r"<published>([^<]+)</published>")
_CHANNEL_ID_IN_URL_RE = re.compile(r"/channel/(UC[\w-]+)")


def _extract_channel_id(raw: dict[str, Any]) -> str:
    """Return the ``UC...`` channel id exposed by a renderer or yt-dlp entry.

    scrapetube / InnerTube renderers carry it in ``ownerText`` (or the short /
    long byline variants) inside the browse endpoint; yt-dlp flat entries put
    it in ``channel_id`` / ``channel_url``.
    """
    for key in ("channel_id", "channelId"):
        direct = str(raw.get(key) or "").strip()
        if direct.startswith("UC"):
            return direct
    for key in ("ownerText", "shortBylineText", "longBylineText", "bylineText"):
        container = raw.get(key)
        if not isinstance(container, dict):
            continue
        for run in container.get("runs") or []:
            if not isinstance(run, dict):
                continue
            endpoint = ((run.get("navigationEndpoint") or {}).get("browseEndpoint")) or {}
            candidate = str(endpoint.get("browseId") or "").strip()
            if candidate.startswith("UC"):
                return candidate
    for key in ("channel_url", "uploader_url"):
        match = _CHANNEL_ID_IN_URL_RE.search(str(raw.get(key) or ""))
        if match:
            return match.group(1)
    return ""


def _parse_channel_rss(text: str) -> dict[str, str]:
    """Map ``videoId -> exact RFC3339 published`` from a channel Atom feed."""

    published: dict[str, str] = {}
    for entry in _RSS_ENTRY_RE.findall(text):
        video_id = _RSS_VIDEO_ID_RE.search(entry)
        published_at = _RSS_PUBLISHED_RE.search(entry)
        if video_id and published_at:
            published[video_id.group(1).strip()] = published_at.group(1).strip()
    return published


def _fetch_channel_rss(channel_id: str) -> dict[str, str]:
    """Fetch and parse one channel's public Atom feed (exact ``published``)."""

    from openbiliclaw.network import outbound_httpx_kwargs

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=20, **outbound_httpx_kwargs()) as client:
        response = client.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"channel_id": channel_id},
            headers=headers,
        )
        response.raise_for_status()
        return _parse_channel_rss(response.text)


# ---------------------------------------------------------------------------
# Normalization — handles both scrapetube and InnerTube renderer shapes
# ---------------------------------------------------------------------------


def _extract_text(value: Any) -> str:
    """Unwrap YouTube's nested text objects to a plain string."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        if "simpleText" in value:
            return str(value["simpleText"]).strip()
        runs = value.get("runs")
        if isinstance(runs, list):
            return "".join(str(r.get("text", "")) for r in runs).strip()
    return ""


def _parse_number(text: str) -> int:
    """Parse '1,234,567 views' or '1.2M' → int."""
    text = text.lower().replace(",", "").strip()
    m = re.search(r"([\d.]+)\s*([kmb]?)", text)
    if not m:
        return 0
    num = float(m.group(1))
    suffix = m.group(2)
    return int(num * {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1))


def _parse_optional_count(raw: Any) -> int:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    text = _extract_text(raw) if isinstance(raw, dict) else str(raw or "")
    return _parse_number(text) if text else 0


def _parse_duration(value: Any) -> int:
    """Parse seconds (int/str) or 'H:MM:SS' / 'M:SS' text → seconds."""
    if isinstance(value, (int, float)):
        return int(value)
    text = _extract_text(value) if isinstance(value, dict) else str(value or "")
    if ":" in text:
        parts = text.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            pass
    try:
        return int(text)
    except (ValueError, TypeError):
        return 0


def normalize_yt_video(
    raw: dict[str, Any],
    *,
    source_strategy: str,
) -> DiscoveredContent | None:
    """Map a scrapetube / InnerTube video renderer dict to DiscoveredContent."""
    video_id = str(raw.get("videoId") or raw.get("id") or "").strip()
    if not video_id:
        return None

    title = _extract_text(raw.get("title") or raw.get("fulltitle") or "")
    if not title:
        return None

    # Channel name — try scrapetube fields first, then yt-dlp / InnerTube fields
    channel = _extract_text(
        raw.get("ownerText")
        or raw.get("shortBylineText")
        or raw.get("longBylineText")
        or raw.get("channel")
        or raw.get("uploader")
        or raw.get("channelTitle")
        or ""
    )

    # View count — scrapetube uses viewCountText, yt-dlp uses view_count (int)
    view_count = 0
    for vc_key in ("viewCountText", "viewCount", "view_count"):
        vc = raw.get(vc_key)
        if vc is None:
            continue
        if isinstance(vc, int):
            view_count = vc
            break
        text = _extract_text(vc) if isinstance(vc, dict) else str(vc)
        if text:
            view_count = _parse_number(text)
            break

    # Duration — scrapetube: lengthText (simpleText "12:34"); yt-dlp: duration (int)
    duration = _parse_duration(
        raw.get("lengthText") or raw.get("lengthSeconds") or raw.get("duration")
    )
    like_count = 0
    for key in ("like_count", "likeCount", "likes"):
        like_count = _parse_optional_count(raw.get(key))
        if like_count:
            break
    comment_count = 0
    for key in ("comment_count", "commentCount", "comments"):
        comment_count = _parse_optional_count(raw.get(key))
        if comment_count:
            break

    # Thumbnail — prefer highest resolution
    cover_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    thumbs_raw = raw.get("thumbnail") or {}
    if isinstance(thumbs_raw, dict):
        thumbs = thumbs_raw.get("thumbnails") or []
        if thumbs and isinstance(thumbs[-1], dict):
            cover_url = str(thumbs[-1].get("url", cover_url))
    elif isinstance(thumbs_raw, list) and thumbs_raw:
        cover_url = str(thumbs_raw[-1].get("url", cover_url))

    # Description snippet
    description = _extract_text(raw.get("descriptionSnippet") or raw.get("description") or "")[:300]
    published = normalize_published_time(
        raw.get("timestamp")
        or raw.get("release_timestamp")
        or raw.get("upload_date")
        or raw.get("publishedAt"),
        label=_extract_text(raw.get("publishedTimeText") or ""),
    )

    return DiscoveredContent(
        content_id=video_id,
        content_url=f"https://www.youtube.com/watch?v={video_id}",
        source_platform="youtube",
        title=title,
        author_name=channel,
        up_name=channel,
        cover_url=cover_url,
        duration=duration,
        view_count=view_count,
        like_count=like_count,
        comment_count=comment_count,
        description=description,
        source_strategy=source_strategy,
        published_at=published.published_at,
        published_label=published.published_label,
    )


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


@dataclass
class YtScraperClient:
    """Async YouTube discovery client backed by scrapetube + InnerTube API."""

    region_code: str = _DEFAULT_REGION
    # Exact-published enrichment budget. The Atom feed only lists a channel's
    # latest ~15 uploads, so this is a best-effort recent-date source, not a
    # history backfill: one bounded feed per channel, cached across strategies.
    channel_rss_ttl_seconds: float = 600.0
    channel_rss_max_feeds: int = 12
    channel_rss_concurrency: int = 4
    _executor: Any = field(default=None, init=False, repr=False)
    _channel_rss_cache: dict[str, tuple[float, dict[str, str]]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    async def search_videos(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_scrapetube_search, query, limit))

    async def get_trending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(_innertube_trending, self.region_code, limit)
        )

    async def get_channel_videos(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_scrapetube_channel, channel_id, limit))

    async def enrich_missing_published_at(
        self,
        items: list[dict[str, Any]],
        *,
        max_feeds: int | None = None,
    ) -> int:
        """Fill exact ``publishedAt`` from channel Atom feeds when available.

        Only raw items without a machine-readable timestamp and with a
        resolvable ``UC...`` channel id are considered.  The feed exposes the
        channel's latest ~15 uploads, so an older or unresolvable video keeps
        its relative ``publishedTimeText`` label only — a fabricated date is
        never written into ``published_at``.
        """

        if not items:
            return 0
        limit = self.channel_rss_max_feeds if max_feeds is None else int(max_feeds)
        if limit <= 0:
            return 0

        pending: dict[str, list[dict[str, Any]]] = {}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if any(
                raw.get(key)
                for key in ("timestamp", "release_timestamp", "upload_date", "publishedAt")
            ):
                continue
            channel_id = _extract_channel_id(raw)
            if not channel_id:
                continue
            pending.setdefault(channel_id, []).append(raw)
        if not pending:
            return 0

        loop = asyncio.get_running_loop()
        semaphore = asyncio.Semaphore(max(1, int(self.channel_rss_concurrency)))

        async def load(channel_id: str) -> tuple[str, dict[str, str]]:
            async with semaphore:
                cached = self._channel_rss_cache.get(channel_id)
                if cached is not None and time.monotonic() - cached[0] < float(
                    self.channel_rss_ttl_seconds
                ):
                    return channel_id, cached[1]
                try:
                    published = await loop.run_in_executor(
                        None, partial(_fetch_channel_rss, channel_id)
                    )
                except Exception as exc:
                    logger.debug("YouTube RSS enrichment failed for %s: %s", channel_id, exc)
                    return channel_id, {}
                self._channel_rss_cache[channel_id] = (time.monotonic(), published)
                return channel_id, published

        loaded = await asyncio.gather(*(load(cid) for cid in list(pending)[:limit]))
        enriched = 0
        for channel_id, published in loaded:
            for raw in pending.get(channel_id, []):
                video_id = str(raw.get("videoId") or raw.get("id") or "").strip()
                exact = published.get(video_id)
                if exact and not raw.get("publishedAt"):
                    raw["publishedAt"] = exact
                    enriched += 1
        return enriched
