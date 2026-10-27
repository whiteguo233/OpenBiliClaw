"""Tests for the yt-dlp backed TikTok discovery client."""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.sources import tiktok as tiktok_module
from openbiliclaw.sources.tiktok import (
    TiktokClient,
    normalize_tiktok_handle,
    normalize_tiktok_tag,
    normalize_tiktok_video,
    tag_from_query,
)


def _flat_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "7234567890123456789",
        "title": "a useful tiktok",
        "description": "a useful tiktok #diy",
        "channel": "Creator Nick",
        "uploader": "creator_handle",
        "timestamp": 1751900000,
        "duration": 34,
        "view_count": 12345,
        "like_count": 678,
        "comment_count": 90,
        "repost_count": 12,
        "save_count": 45,
        "thumbnails": [{"id": "cover", "url": "https://p16-sign.tiktokcdn.com/cover.jpg"}],
        "webpage_url": "https://www.tiktok.com/@creator_handle/video/7234567890123456789",
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@creator", "creator"),
        ("creator", "creator"),
        ("  @some.user_1  ", "some.user_1"),
        ("https://www.tiktok.com/@creator", ""),
        ("", ""),
        ("@bad handle", ""),
    ],
)
def test_normalize_tiktok_handle(raw: str, expected: str) -> None:
    assert normalize_tiktok_handle(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#booktok", "booktok"),
        ("cooking", "cooking"),
        ("  #历史  ", "历史"),
        ("", ""),
        ("#", ""),
        ("two words", ""),
    ],
)
def test_normalize_tiktok_tag(raw: str, expected: str) -> None:
    assert normalize_tiktok_tag(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("machine learning", "machinelearning"),
        ("#BookTok", "BookTok"),
        ("城市规划", "城市规划"),
        ("a-b_c", "a-b_c"),
        ("!!!", ""),
        ("", ""),
    ],
)
def test_tag_from_query_compacts_search_phrases(raw: str, expected: str) -> None:
    assert tag_from_query(raw) == expected


def test_normalize_tiktok_video_maps_flat_entry() -> None:
    content = normalize_tiktok_video(_flat_entry(), source_strategy="tiktok_tag")

    assert content is not None
    assert content.content_id == "7234567890123456789"
    assert content.item_key == "tiktok:7234567890123456789"
    assert content.source_platform == "tiktok"
    assert content.content_url == (
        "https://www.tiktok.com/@creator_handle/video/7234567890123456789"
    )
    assert content.title == "a useful tiktok"
    assert content.author_name == "Creator Nick"
    assert content.cover_url == "https://p16-sign.tiktokcdn.com/cover.jpg"
    assert content.duration == 34
    assert content.view_count == 12345
    assert content.like_count == 678
    assert content.comment_count == 90
    assert content.share_count == 12
    assert content.collect_count == 45
    assert content.published_at.startswith("2025-")
    assert content.source_strategy == "tiktok_tag"
    assert content.source_metadata == {"tiktok_aweme_id": "7234567890123456789"}


def test_normalize_tiktok_video_drops_missing_identity_or_title() -> None:
    assert normalize_tiktok_video(_flat_entry(id=""), source_strategy="s") is None
    assert (
        normalize_tiktok_video(_flat_entry(title="", description=""), source_strategy="s") is None
    )


def test_normalize_tiktok_video_builds_url_and_scales_millisecond_duration() -> None:
    content = normalize_tiktok_video(
        _flat_entry(webpage_url="", url="", duration=34567),
        source_strategy="tiktok_user",
    )

    assert content is not None
    assert content.content_url == (
        "https://www.tiktok.com/@creator_handle/video/7234567890123456789"
    )
    assert content.duration == 34


def test_normalize_tiktok_video_title_falls_back_to_description() -> None:
    content = normalize_tiktok_video(_flat_entry(title=""), source_strategy="s")

    assert content is not None
    assert content.title == "a useful tiktok #diy"


async def test_get_user_videos_builds_user_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_ytdlp_user_videos(handle: str, limit: int) -> list[dict[str, Any]]:
        calls.append((handle, limit))
        return [_flat_entry()]

    monkeypatch.setattr(tiktok_module, "_ytdlp_user_videos", fake_ytdlp_user_videos)
    client = TiktokClient()

    entries = await client.get_user_videos("@creator", limit=7)

    assert calls == [("creator", 7)]
    assert len(entries) == 1
    # An unparseable handle never reaches yt-dlp.
    assert await client.get_user_videos("not a handle", limit=7) == []
    assert calls == [("creator", 7)]


async def test_get_tag_videos_builds_tag_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_ytdlp_tag_videos(tag: str, limit: int) -> list[dict[str, Any]]:
        calls.append((tag, limit))
        return [_flat_entry()]

    monkeypatch.setattr(tiktok_module, "_ytdlp_tag_videos", fake_ytdlp_tag_videos)
    client = TiktokClient()

    entries = await client.get_tag_videos("#cooking", limit=11)

    assert calls == [("cooking", 11)]
    assert len(entries) == 1
    assert await client.get_tag_videos("two words", limit=11) == []
    assert calls == [("cooking", 11)]


def test_ytdlp_user_and_tag_extractors_exist() -> None:
    """Pin the yt-dlp support surface this client relies on (no network)."""
    from yt_dlp.extractor import gen_extractor_classes

    ies = [ie for ie in gen_extractor_classes() if "tiktok" in (ie.IE_NAME or "").lower()]
    assert any(
        ie.IE_NAME == "tiktok:user" and ie.suitable("https://www.tiktok.com/@u") for ie in ies
    )
    assert any(
        ie.IE_NAME == "tiktok:tag" and ie.suitable("https://www.tiktok.com/tag/cooking")
        for ie in ies
    )
    assert any(
        ie.IE_NAME == "TikTok"
        and ie.suitable("https://www.tiktok.com/@u/video/7234567890123456789")
        for ie in ies
    )
    # No TikTok *search* extractor: the keyword path must stay hashtag-mapped.
    assert not any(ie.IE_NAME == "tiktok:search" for ie in ies)


def test_ytdlp_options_apply_overseas_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openbiliclaw.network.outbound_ytdlp_proxy", lambda: "http://127.0.0.1:7890"
    )
    options = tiktok_module._ytdlp_options(socket_timeout=1)
    assert options["proxy"] == "http://127.0.0.1:7890"
    assert options["socket_timeout"] == 1

    monkeypatch.setattr("openbiliclaw.network.outbound_ytdlp_proxy", lambda: None)
    assert "proxy" not in tiktok_module._ytdlp_options()
