"""Tests for the Google Takeout parser (youtube/takeout.py)."""

from __future__ import annotations

import io
import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.youtube.takeout import parse_takeout

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers to build in-memory Takeout fixtures
# ---------------------------------------------------------------------------

_WATCH_HISTORY_JSON = json.dumps(
    [
        {
            "header": "YouTube",
            "title": "Watched How transformers work",
            "titleUrl": "https://www.youtube.com/watch?v=abc1234defg",
            "subtitles": [
                {
                    "name": "3Blue1Brown",
                    "url": "https://www.youtube.com/channel/UCYO_jab_esuFRV4b17AJtAw",
                }
            ],
            "time": "2024-03-01T10:00:00.000Z",
            "products": ["YouTube"],
        },
        {
            "header": "YouTube",
            "title": "Watched Python typing deep dive",
            "titleUrl": "https://www.youtube.com/watch?v=xyz9876wxyz",
            "subtitles": [{"name": "ArjanCodes", "url": "https://www.youtube.com/@ArjanCodes"}],
            "time": "2024-03-02T12:00:00.000Z",
        },
        # Should be skipped: non-YouTube header
        {
            "header": "YouTube Music",
            "title": "Watched some song",
            "titleUrl": "https://www.youtube.com/watch?v=aaaabbbbccc",
            "time": "2024-03-03T08:00:00.000Z",
        },
        # Should be skipped: removed video
        {
            "header": "YouTube",
            "title": "Watched a video that has been removed",
            "titleUrl": "",
            "time": "2024-03-04T08:00:00.000Z",
        },
    ],
    ensure_ascii=False,
)

_SUBSCRIPTIONS_CSV = "Channel ID,Channel URL,Channel Title\nUC123,https://www.youtube.com/channel/UC123,Kurzgesagt\nUC456,https://www.youtube.com/channel/UC456,Veritasium\n"

_LIKED_CSV = (
    "# Liked videos\n"
    "# ...\n"
    "# ...\n"
    "# ...\n"
    "Video ID,Video URL,Video Title\n"
    "vid001,https://www.youtube.com/watch?v=vid001,Interesting Talk\n"
    "vid002,https://www.youtube.com/watch?v=vid002,Great Lecture\n"
)

_WATCH_HISTORY_HTML = """
<html><body>
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
  <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
    <a href="https://www.youtube.com/watch?v=htmlvid01">HTML Video One</a><br>
    <a href="https://www.youtube.com/channel/UCfoo">ChannelFoo</a><br>
    Mar 5, 2024, 9:00:00 AM UTC
  </div>
</div>
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">
  <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
    <a href="https://www.youtube.com/watch?v=htmlvid02">HTML Video Two</a><br>
    <a href="https://www.youtube.com/@ChannelBar">ChannelBar</a><br>
    Mar 6, 2024, 9:00:00 AM UTC
  </div>
</div>
</body></html>
"""


def _make_zip(contents: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# watch-history.json parsing
# ---------------------------------------------------------------------------


def test_watch_json_events_and_stats(tmp_path: Path) -> None:
    yt_dir = tmp_path / "Takeout" / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.json").write_text(_WATCH_HISTORY_JSON, encoding="utf-8")

    result = parse_takeout(tmp_path / "Takeout")

    assert result.stats.watch_history == 2
    assert result.stats.subscriptions == 0
    assert result.stats.liked_videos == 0
    assert result.stats.total == 2


def test_watch_json_strips_watched_prefix(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.json").write_text(_WATCH_HISTORY_JSON, encoding="utf-8")

    result = parse_takeout(yt_dir)

    titles = {e["title"] for e in result.events}
    assert "How transformers work" in titles
    assert "Python typing deep dive" in titles


def test_watch_json_event_shape(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.json").write_text(_WATCH_HISTORY_JSON, encoding="utf-8")

    result = parse_takeout(yt_dir)
    ev = next(e for e in result.events if e["title"] == "How transformers work")

    assert ev["event_type"] == "view"
    assert ev["metadata"]["source_platform"] == "youtube"
    assert ev["metadata"]["author"] == "3Blue1Brown"
    assert ev["metadata"]["video_id"] == "abc1234defg"
    assert "youtube.com/watch" in ev["url"]


# ---------------------------------------------------------------------------
# subscriptions.csv parsing
# ---------------------------------------------------------------------------


def test_subscriptions_csv(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "subscriptions").mkdir(parents=True)
    (yt_dir / "subscriptions" / "subscriptions.csv").write_text(
        _SUBSCRIPTIONS_CSV, encoding="utf-8"
    )

    result = parse_takeout(yt_dir)

    assert result.stats.subscriptions == 2
    names = {e["title"] for e in result.events}
    assert "Kurzgesagt" in names
    assert "Veritasium" in names
    ev = next(e for e in result.events if e["title"] == "Kurzgesagt")
    assert ev["event_type"] == "follow"
    assert ev["metadata"]["source_platform"] == "youtube"


# ---------------------------------------------------------------------------
# liked videos CSV parsing
# ---------------------------------------------------------------------------


def test_liked_csv(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "playlists").mkdir(parents=True)
    (yt_dir / "playlists" / "Liked videos.csv").write_text(_LIKED_CSV, encoding="utf-8")

    result = parse_takeout(yt_dir)

    assert result.stats.liked_videos == 2
    ev = next(e for e in result.events if e["metadata"].get("video_id") == "vid001")
    assert ev["event_type"] == "like"
    assert ev["title"] == "Interesting Talk"


# ---------------------------------------------------------------------------
# HTML fallback
# ---------------------------------------------------------------------------


def test_watch_html_fallback(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.html").write_text(
        _WATCH_HISTORY_HTML, encoding="utf-8"
    )

    result = parse_takeout(yt_dir)

    assert result.stats.watch_history == 2
    titles = {e["title"] for e in result.events}
    assert "HTML Video One" in titles
    assert "HTML Video Two" in titles


def test_watch_html_channel_extraction(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.html").write_text(
        _WATCH_HISTORY_HTML, encoding="utf-8"
    )

    result = parse_takeout(yt_dir)
    ev = next(e for e in result.events if e["title"] == "HTML Video One")
    assert ev["metadata"]["author"] == "ChannelFoo"


# ---------------------------------------------------------------------------
# zip archive
# ---------------------------------------------------------------------------


def test_zip_all_sources(tmp_path: Path) -> None:
    contents = {
        "Takeout/YouTube and YouTube Music/history/watch-history.json": _WATCH_HISTORY_JSON,
        "Takeout/YouTube and YouTube Music/subscriptions/subscriptions.csv": _SUBSCRIPTIONS_CSV,
        "Takeout/YouTube and YouTube Music/playlists/Liked videos.csv": _LIKED_CSV,
    }
    zip_path = tmp_path / "takeout.zip"
    zip_path.write_bytes(_make_zip(contents))

    result = parse_takeout(zip_path)

    assert result.stats.watch_history == 2
    assert result.stats.subscriptions == 2
    assert result.stats.liked_videos == 2
    assert result.stats.total == 6


def test_zip_html_fallback(tmp_path: Path) -> None:
    contents = {
        "Takeout/YouTube and YouTube Music/history/watch-history.html": _WATCH_HISTORY_HTML,
    }
    zip_path = tmp_path / "takeout.zip"
    zip_path.write_bytes(_make_zip(contents))

    result = parse_takeout(zip_path)

    assert result.stats.watch_history == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_files_yield_warnings(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    yt_dir.mkdir(parents=True)

    result = parse_takeout(yt_dir)

    assert result.stats.total == 0
    assert any("watch-history" in w for w in result.warnings)


def test_invalid_zip(tmp_path: Path) -> None:
    bad_zip = tmp_path / "bad.zip"
    bad_zip.write_bytes(b"not a zip file")

    result = parse_takeout(bad_zip)

    assert result.stats.total == 0
    assert result.warnings


def test_invalid_path_raises() -> None:
    with pytest.raises(ValueError, match="zip file or directory"):
        parse_takeout("/nonexistent/file.txt")


def test_watch_json_malformed(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.json").write_text("not json!", encoding="utf-8")

    result = parse_takeout(yt_dir)

    assert result.stats.watch_history == 0
    assert any("watch-history.json" in w for w in result.warnings)


def test_source_platform_mix_is_youtube(tmp_path: Path) -> None:
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "history").mkdir(parents=True)
    (yt_dir / "history" / "watch-history.json").write_text(_WATCH_HISTORY_JSON, encoding="utf-8")

    result = parse_takeout(yt_dir)

    platforms = {e["metadata"]["source_platform"] for e in result.events}
    assert platforms == {"youtube"}


def test_liked_csv_no_title_uses_video_id(tmp_path: Path) -> None:
    csv_content = (
        "# Liked videos\n# \n# \n# \n"
        "Video ID,Video URL,Video Title\n"
        "noid001,,\n"
    )
    yt_dir = tmp_path / "YouTube and YouTube Music"
    (yt_dir / "playlists").mkdir(parents=True)
    (yt_dir / "playlists" / "Liked videos.csv").write_text(csv_content, encoding="utf-8")

    result = parse_takeout(yt_dir)

    assert result.stats.liked_videos == 1
    ev = result.events[0]
    assert ev["title"] == "noid001"
    assert ev["url"] == "https://www.youtube.com/watch?v=noid001"
