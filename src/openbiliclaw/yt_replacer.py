"""
YouTube Replacer — detects Bilibili reposts of foreign-language content
and replaces them with the original YouTube video URL.

Two-tier cache:
  1. In-memory LRU dict (fast, per-process)
  2. JSON file at <data_dir>/yt_replacer_cache.json (persistent across restarts)

Config: [sources.youtube].replace_bilibili_reposts = true
"""

from __future__ import annotations

import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

# ── In-memory cache ────────────────────────────────────────────────
_yt_cache: dict[str, dict | None] = {}  # bvid -> {yt_url, yt_title, yt_author} or None
_yt_cache_mtime: float = 0.0
_CACHE_TTL = 86400  # 24h

# ── Heuristic: is this likely a repost of foreign content? ─────────

# Signs in the title that indicate this is a translation/repost
_REPOST_KEYWORDS = [
    "翻译", "中字", "字幕", "自译", "译制", "熟肉",
    "搬运", "英文字幕", "中英字幕", "双语字幕",
    "英文", "外语", "外文", "英文原版", "原版视频",
    "sub", "subtitle", "translation", "translate",
    "CC", "English", "中英", "英文解说",
]

# Known non-Chinese content categories that often get reposted
_FOREIGN_CATEGORIES = [
    "Gamespot", "IGN", "Gamesradar", "Polygon", "Kotaku",
    "GameSpot", "Nintendo", "PlayStation", "Xbox",
    "TED", "TEDx", "BBC", "CNN", "NPR", "PBS",
    "Netflix", "HBO", "Disney+", "Apple TV",
    "Vox", "Verge", "Wired", "TechCrunch", "Ars Technica",
    "NYT", "New York Times", "Guardian", "Reuters", "AP",
    "National Geographic", "Discovery", "Science", "Nature",
    "Vsauce", "Veritasium", "SmarterEveryDay", "Kurzgesagt",
    "3Blue1Brown", "Numberphile", "Computerphile",
    "Tom Scott", "LTT", "Linus Tech Tips", "Gamers Nexus",
    "MKBHD", "Marques Brownlee", "Dave2D", "Dave Lee",
    "iJustine", "UrAvgConsumer", "Austin Evans",
    "Fstoppers", "DPReview", "PetaPixel",
    "The Wall Street Journal", "Bloomberg", "Forbes",
    "CNET", "Engadget", "Gizmodo", "GizChina",
    "Digital Trends", "Tom's Guide", "TechSpot",
    "AnandTech", "SemiAnalysis", "Chip War",
    "Asianometry", "Asianometry", "High Yield",
    "Two Minute Papers", "Yannic Kilcher",
]


def _extract_english_terms(title: str) -> list[str]:
    """Extract meaningful English words / terms from a mixed-language title.

    E.g. ``"【翻译】15 Chord Progressions for 15 Different Emotions"``
    → ``["15 Chord Progressions for 15 Different Emotions"]``
    """
    terms = []
    # Find runs of Latin + digit characters (≥4 chars to filter noise)
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9 .!?,:;'\"\-\(\)]{3,}", title):
        chunk = match.group().strip().strip(" -:;,.\\\"'()[]")
        if len(chunk) >= 4:
            terms.append(chunk)
    # Also catch terms starting with digits (e.g. "1080P", "4K", "3D")
    for match in re.finditer(r"[0-9][A-Za-z0-9]{1,}", title):
        chunk = match.group().strip()
        # Must contain letters too (to avoid standalone numbers)
        if any(c.isalpha() for c in chunk) and chunk not in terms:
            terms.append(chunk)
    return terms


def _has_repost_keywords(title: str, description: str = "") -> bool:
    """Check if title or description contains repost/translation keywords."""
    combined = f"{title} {description}".lower()
    for kw in _REPOST_KEYWORDS:
        if kw.lower() in combined:
            return True
    return False


def _has_foreign_brand(title: str) -> bool:
    """Check if title mentions a known foreign brand/channel."""
    for brand in _FOREIGN_CATEGORIES:
        if brand.lower() in title.lower():
            return True
    return False


def is_likely_repost(title: str, description: str = "") -> bool:
    """Return True if the video is likely a repost of foreign content.

    Combines multiple signals:
      1. Title has >35% Latin characters (predominantly English)
      2. Title contains known foreign brands/channels + at least some English
      3. Title or description contains repost keywords AND English terms
      4. Title has meaningful English content phrases (≥2 terms or 1 long term)
    """
    if not title:
        return False

    total = len(title)
    if total < 5:
        return False

    latin = sum(1 for ch in title if 'a' <= ch.lower() <= 'z')
    latin_ratio = latin / total if total > 0 else 0.0
    english_terms = _extract_english_terms(title)
    meaningful = [t for t in english_terms if len(t) >= 6]
    has_meaningful = len(meaningful) >= 2 or (len(meaningful) == 1 and len(meaningful[0]) >= 10)

    # Signal 1: High Latin ratio — title is mostly English
    if latin_ratio > 0.35:
        return True

    # Signal 2: Foreign brand mention + at least some Latin chars
    if _has_foreign_brand(title) and latin_ratio > 0.05:
        return True

    # Signal 3: Repost keywords + English terms or YT link in description
    if _has_repost_keywords(title, description):
        if len(english_terms) >= 1 or latin_ratio > 0.10 or "youtu" in (description or "").lower():
            return True

    # Signal 4: Strong English phrases (low threshold but requires solid English)
    if has_meaningful and latin_ratio > 0.15:
        return True

    return False


# ── YouTube search via yt-dlp ─────────────────────────────────────


def _search_yt(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube via yt-dlp and return raw result entries."""
    import yt_dlp

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "source_address": "0.0.0.0",
        "extractor_args": {"youtube": {"skip": ["dash", "hls", "comment"]}},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            return list(info.get("entries", [])) if info else []
    except Exception:
        logger.debug("yt-dlp search failed for query=%r", query, exc_info=True)
        return []


def _title_similarity(a: str, b: str) -> float:
    """Fuzzy similarity between two title strings (0.0–1.0)."""
    a_clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff\s]", "", a).strip().lower()
    b_clean = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff\s]", "", b).strip().lower()
    if not a_clean or not b_clean:
        return 0.0
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def _build_search_query(title: str) -> str:
    """Build an effective YouTube search query from a Bilibili title.

    For Chinese-dominant titles, extract English terms and use those.
    For mixed titles, use the full title cleanly.
    """
    english_terms = _extract_english_terms(title)
    if english_terms:
        # Use the longest English term as the primary query
        best = max(english_terms, key=len)
        return best[:200]  # yt-dlp has its own limits
    return title[:200]


def find_original(title: str, author: str = "", description: str = "") -> dict | None:
    """Search YouTube for the original of a video described by *title*
    (and optionally *author*). Returns ``{url, title, uploader, cover_url}``
    on match, or ``None``.

    Builds a search query from the English terms in the title, then
    scores results by title similarity. If the description already
    contains a YouTube link, that's used directly.
    """
    # Fast path: description already has YouTube URL
    if description:
        yt_match = re.search(r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})", description)
        if yt_match:
            vid = yt_match.group(1)
            logger.info("YT replacer: fast-path from description url for %r", title)
            return {
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "uploader": author or "",
                "cover_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            }

    query = _build_search_query(title)
    if not query or len(query) < 5:
        return None

    results = _search_yt(query, max_results=10)

    if not results:
        return None

    # Score and sort by title similarity
    scored = []
    for entry in results:
        yt_title = entry.get("title", "") or ""
        sim = _title_similarity(title, yt_title)

        # Bonus if author name matches
        if author and entry.get("uploader"):
            author_sim = _title_similarity(author, entry["uploader"])
            sim = max(sim, sim * 0.8 + author_sim * 0.2)

        scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_sim, best = scored[0]

    if best_sim < 0.35:
        logger.debug("YT replacer: no good match for %r (best=%.2f %s)",
                      title, best_sim, best.get("title", ""))
        return None

    url = f"https://www.youtube.com/watch?v={best.get('id', '')}"
    vid = best.get("id", "")
    cover_url = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else ""
    return {
        "url": url,
        "title": best.get("title", ""),
        "uploader": best.get("uploader", ""),
        "cover_url": cover_url,
    }


# ── Cache persistence ─────────────────────────────────────────────


def _cache_path(data_dir: str = "") -> Path:
    base = Path(data_dir) if data_dir else Path.cwd() / "data"
    return base / "yt_replacer_cache.json"


def _load_cache(data_dir: str = "") -> dict:
    global _yt_cache, _yt_cache_mtime
    p = _cache_path(data_dir)
    if p.exists():
        mtime = p.stat().st_mtime
        if mtime > _yt_cache_mtime:
            try:
                with open(p, encoding="utf-8") as f:
                    _yt_cache.update(json.load(f))
                _yt_cache_mtime = mtime
            except Exception:
                logger.debug("YT replacer: failed to load cache", exc_info=True)
    return _yt_cache


def _save_cache(data_dir: str = "") -> None:
    p = _cache_path(data_dir)
    try:
        parent = p.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_yt_cache, f, ensure_ascii=False, indent=2)
        global _yt_cache_mtime
        _yt_cache_mtime = time.time()
    except Exception:
        logger.debug("YT replacer: failed to save cache", exc_info=True)


# ── Public API ────────────────────────────────────────────────────


def replace_if_foreign(
    bvid: str,
    title: str,
    author: str = "",
    description: str = "",
    *,
    data_dir: str = "",
    force: bool = False,
) -> dict | None:
    """Check if a Bilibili video is a foreign repost and return the
    original YouTube URL + metadata.

    Returns ``None`` if:
      - The video doesn't look like a repost
      - No good YouTube match is found
      - The result is cached as ``None``
    """
    _load_cache(data_dir)

    # Check cache
    if bvid in _yt_cache and not force:
        cached = _yt_cache[bvid]
        if cached is None:
            return None
        return dict(cached)

    # Detection
    if not is_likely_repost(title, description=description):
        _yt_cache[bvid] = None
        return None

    # Search YouTube with intelligently built query
    logger.info("YT replacer: searching YouTube for %r (author=%r)", title, author)
    result = find_original(title, author=author, description=description)

    if result is None:
        _yt_cache[bvid] = None
        _save_cache(data_dir)
        logger.info("YT replacer: no match for bvid=%s title=%r", bvid, title)
        return None

    entry = {
        "bvid": bvid,
        "yt_url": result["url"],
        "yt_title": result["title"],
        "yt_uploader": result.get("uploader", ""),
        "yt_cover_url": result.get("cover_url", ""),
    }
    _yt_cache[bvid] = entry
    _save_cache(data_dir)
    logger.info(
        "YT replacer: %s → %s (%.2f) %r",
        bvid, result["url"],
        SequenceMatcher(None, title.lower(), result["title"].lower()).ratio(),
        result["title"],
    )
    return entry


def replace_recommendation_row(
    row: dict,
    *,
    data_dir: str = "",
) -> dict | None:
    """Take a recommendation row dict (from ``get_recommendations()``) and
    return the YT replacement data if applicable.

    Returns a dict with ``content_url``, ``source_platform``, ``expression``
    overrides, or ``None`` if no replacement is needed.
    """
    bvid = str(row.get("bvid", "") or "")
    title = str(row.get("title", "") or "")
    author = str(row.get("up_name", "") or "")
    description = str(row.get("description", "") or "")
    source_platform = str(row.get("source_platform", "") or "")

    # Skip: already YouTube, or not a bilibili-sourced item
    if source_platform == "youtube":
        return None
    if not bvid or not title:
        return None

    yt = replace_if_foreign(bvid, title, author, description=description, data_dir=data_dir)
    if yt is None:
        return None

    original_expr = str(row.get("expression", "") or "")
    yt_url = yt["yt_url"]
    yt_cover = yt.get("yt_cover_url", "")
    # Fallback: construct cover from video ID if cache entry predates cover_url
    if not yt_cover and "youtube.com/watch?v=" in yt_url:
        vid_match = re.search(r"v=([a-zA-Z0-9_-]{11})", yt_url)
        if vid_match:
            yt_cover = f"https://i.ytimg.com/vi/{vid_match.group(1)}/hqdefault.jpg"
    expr_suffix = (
        f"\n💡 这是搬运，原视频在 YouTube：{yt_url}"
    )
    override = {
        "content_url": yt_url,
        "source_platform": "youtube",
        "expression": (original_expr + expr_suffix) if original_expr else f"原视频在 YouTube：{yt_url}",
    }
    if yt_cover:
        override["cover_url"] = yt_cover
    return override


def clear_cache(data_dir: str = "") -> None:
    """Clear both in-memory and file cache."""
    _yt_cache.clear()
    p = _cache_path(data_dir)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
