"""Exact YouTube publication-time enrichment via channel Atom feeds."""

from __future__ import annotations

from typing import Any

from openbiliclaw.youtube.client import (
    YtScraperClient,
    _extract_channel_id,
    _parse_channel_rss,
    normalize_yt_video,
)

_RSS_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <id>yt:video:abc123</id>
    <yt:videoId>abc123</yt:videoId>
    <published>2026-09-14T07:01:45+00:00</published>
    <title>recent</title>
  </entry>
  <entry>
    <id>yt:video:old456</id>
    <yt:videoId>old456</yt:videoId>
    <published>2017-10-31T04:44:25+00:00</published>
    <title>old</title>
  </entry>
  <entry>
    <id>yt:video:noDate</id>
    <yt:videoId>noDate</yt:videoId>
    <title>missing published</title>
  </entry>
</feed>
"""


def test_parse_channel_rss_maps_video_ids_to_exact_published() -> None:
    assert _parse_channel_rss(_RSS_TEXT) == {
        "abc123": "2026-09-14T07:01:45+00:00",
        "old456": "2017-10-31T04:44:25+00:00",
    }


def test_extract_channel_id_supports_ytdlp_and_renderer_shapes() -> None:
    assert _extract_channel_id({"channel_id": "UCKWaEZ-_VweaEx1j62do_vQ"}) == (
        "UCKWaEZ-_VweaEx1j62do_vQ"
    )
    renderer = {
        "ownerText": {
            "runs": [
                {
                    "text": "IBM Technology",
                    "navigationEndpoint": {
                        "browseEndpoint": {"browseId": "UCKWaEZ-_VweaEx1j62do_vQ"}
                    },
                }
            ]
        }
    }
    assert _extract_channel_id(renderer) == "UCKWaEZ-_VweaEx1j62do_vQ"
    byline = {
        "bylineText": {
            "runs": [
                {
                    "text": "Wemmbu",
                    "navigationEndpoint": {
                        "browseEndpoint": {"browseId": "UCkzzNLnuM-VsATWC53ehwOQ"}
                    },
                }
            ]
        }
    }
    assert _extract_channel_id(byline) == "UCkzzNLnuM-VsATWC53ehwOQ"
    assert _extract_channel_id(
        {"uploader_url": "https://www.youtube.com/channel/UCabc123/videos"}
    ) == ("UCabc123")
    assert _extract_channel_id({"videoId": "abc"}) == ""


async def test_enrich_missing_published_at_injects_only_exact_rss_matches(monkeypatch) -> None:
    requested: list[str] = []

    def fake_fetch(channel_id: str) -> dict[str, str]:
        requested.append(channel_id)
        return {"abc123": "2026-09-14T07:01:45+00:00"}

    monkeypatch.setattr("openbiliclaw.youtube.client._fetch_channel_rss", fake_fetch)
    client = YtScraperClient()
    items: list[dict[str, Any]] = [
        {"videoId": "abc123", "title": {"simpleText": "recent"}, "channel_id": "UCabc"},
        {"videoId": "unknown", "channel_id": "UCabc"},
        {"videoId": "already", "channel_id": "UCabc", "timestamp": 1700000000},
        {"videoId": "nochannel", "title": {"simpleText": "x"}},
    ]

    enriched = await client.enrich_missing_published_at(items)

    assert enriched == 1
    assert requested == ["UCabc"]
    assert items[0]["publishedAt"] == "2026-09-14T07:01:45+00:00"
    assert "publishedAt" not in items[1]
    assert "publishedAt" not in items[2]
    assert "publishedAt" not in items[3]

    normalized = normalize_yt_video(items[0], source_strategy="yt_search")
    assert normalized is not None
    assert normalized.published_at == "2026-09-14T07:01:45Z"
    assert normalized.published_label == ""


async def test_enrich_missing_published_at_is_bounded_and_caches_feeds(monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch(channel_id: str) -> dict[str, str]:
        calls.append(channel_id)
        return {}

    monkeypatch.setattr("openbiliclaw.youtube.client._fetch_channel_rss", fake_fetch)
    bounded = YtScraperClient(channel_rss_max_feeds=1)
    await bounded.enrich_missing_published_at(
        [
            {"videoId": "a", "channel_id": "UCone"},
            {"videoId": "b", "channel_id": "UCtwo"},
        ]
    )
    assert calls == ["UCone"]

    cached = YtScraperClient()
    await cached.enrich_missing_published_at([{"videoId": "a", "channel_id": "UCcache"}])
    await cached.enrich_missing_published_at([{"videoId": "a", "channel_id": "UCcache"}])
    assert calls.count("UCcache") == 1
