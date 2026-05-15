"""Google Takeout parser for YouTube watch history, subscriptions, and likes.

Supports both extracted directories and zip archives.  The parser is
intentionally lenient: missing files are silently skipped so a partial
Takeout export still yields whatever data is present.

Takeout directory layout (JSON format selected at export time):

    Takeout/
      YouTube and YouTube Music/
        history/
          watch-history.json
          search-history.json   (ignored — searches are weak signals)
        subscriptions/
          subscriptions.csv
        playlists/
          Liked videos.csv      (title varies by locale)

HTML format (the default when no format is chosen):

    Takeout/
      YouTube and YouTube Music/
        history/
          watch-history.html

Each file is optional.  The returned event list uses the unified
``build_event`` contract so it can be fed directly into
``SoulEngine.analyze_events`` and ``MemoryManager.propagate_event``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openbiliclaw.sources.event_format import SOURCE_YOUTUBE, build_event

logger = logging.getLogger(__name__)

# Signal strength mirrors xhs convention: subscriptions ≈ favorites (high
# intent), likes ≈ xhs-liked (moderate), watch history ≈ xhs-history (low).
_SIGNAL_STRENGTH: dict[str, float] = {
    "follow": 1.0,
    "like": 0.85,
    "view": 0.35,
}

# Candidate filenames for the liked-videos playlist (locale-dependent).
_LIKED_VIDEOS_NAMES = frozenset(
    [
        "liked videos.csv",
        "liked videos.json",
        "likes.csv",
    ]
)

# Regex to extract a video ID from a YouTube watch URL.
_VIDEO_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")


@dataclass
class TakeoutStats:
    """Summary counts returned alongside the parsed events."""

    watch_history: int = 0
    subscriptions: int = 0
    liked_videos: int = 0

    @property
    def total(self) -> int:
        return self.watch_history + self.subscriptions + self.liked_videos


@dataclass
class TakeoutParseResult:
    events: list[dict[str, Any]] = field(default_factory=list)
    stats: TakeoutStats = field(default_factory=TakeoutStats)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_takeout(path: str | Path) -> TakeoutParseResult:
    """Parse a Google Takeout export and return unified events.

    *path* may point to:
    - A ``.zip`` file (the raw download from Google Takeout).
    - A directory produced by extracting that zip.
    - The inner ``YouTube and YouTube Music`` subdirectory directly.

    Returns a :class:`TakeoutParseResult` with the event list and stats.
    """
    path = Path(path)
    if path.suffix.lower() == ".zip":
        return _parse_zip(path)
    if path.is_dir():
        return _parse_dir(path)
    raise ValueError(f"Takeout path must be a .zip file or directory, got: {path}")


# ---------------------------------------------------------------------------
# Zip vs directory dispatch
# ---------------------------------------------------------------------------


def _parse_zip(zip_path: Path) -> TakeoutParseResult:
    result = TakeoutParseResult()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}

            watch_json = _find_zip_entry(names_lower, "history/watch-history.json")
            watch_html = _find_zip_entry(names_lower, "history/watch-history.html")
            subs_csv = _find_zip_entry(names_lower, "subscriptions/subscriptions.csv")
            liked_csv = _find_zip_liked(names_lower)

            if watch_json:
                data = zf.read(watch_json)
                _parse_watch_json(data.decode("utf-8", errors="replace"), result)
            elif watch_html:
                data = zf.read(watch_html)
                _parse_watch_html(data.decode("utf-8", errors="replace"), result)
            else:
                result.warnings.append(
                    "watch-history.json / watch-history.html not found in zip"
                )

            if subs_csv:
                data = zf.read(subs_csv)
                _parse_subscriptions_csv(data.decode("utf-8", errors="replace"), result)

            if liked_csv:
                data = zf.read(liked_csv)
                _parse_liked_csv(data.decode("utf-8", errors="replace"), result)

    except zipfile.BadZipFile as exc:
        result.warnings.append(f"Invalid zip file: {exc}")
    return result


def _parse_dir(root: Path) -> TakeoutParseResult:
    result = TakeoutParseResult()

    # Accept both the outer Takeout dir and the inner YouTube subdir.
    yt_dir = _find_yt_subdir(root)

    history_dir = yt_dir / "history"
    subs_dir = yt_dir / "subscriptions"
    playlists_dir = yt_dir / "playlists"

    watch_json = history_dir / "watch-history.json"
    watch_html = history_dir / "watch-history.html"

    if watch_json.exists():
        _parse_watch_json(watch_json.read_text(encoding="utf-8", errors="replace"), result)
    elif watch_html.exists():
        _parse_watch_html(watch_html.read_text(encoding="utf-8", errors="replace"), result)
    else:
        result.warnings.append(
            f"watch-history.json / watch-history.html not found under {history_dir}"
        )

    subs_file = subs_dir / "subscriptions.csv"
    if subs_file.exists():
        _parse_subscriptions_csv(
            subs_file.read_text(encoding="utf-8", errors="replace"), result
        )

    liked_file = _find_dir_liked(playlists_dir)
    if liked_file:
        _parse_liked_csv(liked_file.read_text(encoding="utf-8", errors="replace"), result)

    return result


# ---------------------------------------------------------------------------
# watch-history.json  (JSON format selected in Takeout settings)
# ---------------------------------------------------------------------------


def _parse_watch_json(text: str, result: TakeoutParseResult) -> None:
    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        result.warnings.append(f"watch-history.json parse error: {exc}")
        return
    if not isinstance(records, list):
        result.warnings.append("watch-history.json: expected a JSON array at root")
        return

    for record in records:
        if not isinstance(record, dict):
            continue
        # Skip non-YouTube records (e.g. YouTube Music has header "YouTube Music")
        header = str(record.get("header", "")).strip()
        if header and header.lower() not in {"youtube", ""}:
            continue
        # Skip ads and auto-plays that were never really watched.
        title_raw = str(record.get("title", "")).strip()
        if title_raw.startswith("Watched a video that has been removed") or not title_raw:
            continue
        # Strip "Watched " prefix Google adds in the title field.
        title = re.sub(r"^Watched\s+", "", title_raw, count=1)

        url = str(record.get("titleUrl", "")).strip()
        channel = ""
        channel_url = ""
        subtitles = record.get("subtitles")
        if isinstance(subtitles, list) and subtitles:
            first = subtitles[0]
            if isinstance(first, dict):
                channel = str(first.get("name", "")).strip()
                channel_url = str(first.get("url", "")).strip()

        video_id = _extract_video_id(url)
        result.events.append(
            build_event(
                event_type="view",
                source_platform=SOURCE_YOUTUBE,
                title=title,
                url=url,
                author=channel,
                metadata={
                    "video_id": video_id,
                    "channel_url": channel_url,
                    "watched_at": str(record.get("time", "")),
                    "signal_strength": _SIGNAL_STRENGTH["view"],
                },
            )
        )
        result.stats.watch_history += 1


# ---------------------------------------------------------------------------
# watch-history.html  (default HTML format)
# ---------------------------------------------------------------------------

# Minimal HTML parser — avoids the html.parser quirks with Takeout's encoding.
# Each watch entry looks like:
#
#   <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
#     <a href="https://www.youtube.com/watch?v=...">Title</a>
#     <br>
#     <a href="https://www.youtube.com/channel/...">Channel</a>
#     <br>
#     Jan 1, 2024, 12:00:00 PM UTC
#   </div>
#
# We extract href + inner text pairs from <a> tags and use positional
# heuristics: first link = video, second link (if channel URL) = channel.

_CONTENT_CELL_RE = re.compile(
    r'<div[^>]+class="[^"]*content-cell[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_ANCHOR_RE = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub("", html).strip()


def _parse_watch_html(text: str, result: TakeoutParseResult) -> None:
    for cell_match in _CONTENT_CELL_RE.finditer(text):
        cell = cell_match.group(1)
        anchors = _ANCHOR_RE.findall(cell)
        if not anchors:
            continue
        video_href, video_title_html = anchors[0]
        video_title = _strip_tags(video_title_html).strip()
        if not video_title or "youtube.com/watch" not in video_href:
            continue

        channel = ""
        channel_url = ""
        if len(anchors) >= 2:
            ch_href, ch_name_html = anchors[1]
            if "youtube.com/channel" in ch_href or "youtube.com/@" in ch_href:
                channel = _strip_tags(ch_name_html).strip()
                channel_url = ch_href

        video_id = _extract_video_id(video_href)
        result.events.append(
            build_event(
                event_type="view",
                source_platform=SOURCE_YOUTUBE,
                title=video_title,
                url=video_href,
                author=channel,
                metadata={
                    "video_id": video_id,
                    "channel_url": channel_url,
                    "signal_strength": _SIGNAL_STRENGTH["view"],
                },
            )
        )
        result.stats.watch_history += 1


# ---------------------------------------------------------------------------
# subscriptions.csv
# ---------------------------------------------------------------------------
# Columns: Channel ID, Channel URL, Channel Title
# (may have a header row — detected by checking if the first value looks
# like a channel ID, i.e. starts with "UC").


def _parse_subscriptions_csv(text: str, result: TakeoutParseResult) -> None:
    reader = csv.reader(io.StringIO(text))
    for i, row in enumerate(reader):
        if not row:
            continue
        if i == 0 and row[0].strip().lower() in {"channel id", "channel_id"}:
            continue
        channel_id = row[0].strip() if len(row) > 0 else ""
        channel_url = row[1].strip() if len(row) > 1 else ""
        channel_title = row[2].strip() if len(row) > 2 else ""
        if not channel_title:
            continue
        result.events.append(
            build_event(
                event_type="follow",
                source_platform=SOURCE_YOUTUBE,
                title=channel_title,
                url=channel_url,
                author=channel_title,
                metadata={
                    "channel_id": channel_id,
                    "signal_strength": _SIGNAL_STRENGTH["follow"],
                },
            )
        )
        result.stats.subscriptions += 1


# ---------------------------------------------------------------------------
# Liked videos CSV
# ---------------------------------------------------------------------------
# Columns vary by locale/version but typically:
#   Video ID, Video URL, [Video Title]   (older)
#   Video ID, Video URL, Video Title, ...  (newer, title in col 2 or 3)
# First ~4 lines are metadata comments (starting with #), skip them.


def _parse_liked_csv(text: str, result: TakeoutParseResult) -> None:
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    if not lines:
        return
    reader = csv.reader(io.StringIO("\n".join(lines)))
    header_skipped = False
    for row in reader:
        if not row:
            continue
        if not header_skipped:
            header_skipped = True
            # If the first column looks like a header label, skip.
            if row[0].strip().lower() in {"video id", "videoid"}:
                continue
        video_id = row[0].strip() if len(row) > 0 else ""
        video_url = row[1].strip() if len(row) > 1 else ""
        video_title = row[2].strip() if len(row) > 2 else ""
        if not video_id:
            continue
        if not video_url:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        result.events.append(
            build_event(
                event_type="like",
                source_platform=SOURCE_YOUTUBE,
                title=video_title or video_id,
                url=video_url,
                metadata={
                    "video_id": video_id,
                    "signal_strength": _SIGNAL_STRENGTH["like"],
                },
            )
        )
        result.stats.liked_videos += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_video_id(url: str) -> str:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def _find_yt_subdir(root: Path) -> Path:
    """Return the YouTube data directory inside a Takeout root.

    Handles three layouts:
    - root IS the YouTube dir (contains 'history/' directly)
    - root/Takeout/YouTube and YouTube Music/
    - root/YouTube and YouTube Music/
    """
    if (root / "history").is_dir():
        return root
    for candidate in root.rglob("YouTube and YouTube Music"):
        if candidate.is_dir():
            return candidate
    return root


def _find_dir_liked(playlists_dir: Path) -> Path | None:
    if not playlists_dir.is_dir():
        return None
    for entry in playlists_dir.iterdir():
        if entry.name.lower() in _LIKED_VIDEOS_NAMES:
            return entry
    return None


def _find_zip_entry(names_lower: dict[str, str], suffix: str) -> str | None:
    """Find the first zip entry whose lowercased name ends with *suffix*."""
    for lower, original in names_lower.items():
        if lower.endswith(suffix.lower()):
            return original
    return None


def _find_zip_liked(names_lower: dict[str, str]) -> str | None:
    for lower, original in names_lower.items():
        basename = lower.rsplit("/", 1)[-1]
        if basename in _LIKED_VIDEOS_NAMES:
            return original
    return None
