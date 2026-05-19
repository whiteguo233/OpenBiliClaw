"""Tests for YouTube discovery strategy integration edges."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.discovery.strategies.youtube import (
    YoutubeChannelStrategy,
    YoutubeSearchStrategy,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile
from openbiliclaw.youtube.client import (
    _channel_uploads_url,
    _extract_innertube_config,
    _extract_yt_initial_data_videos,
    _topic_page_trending,
)


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="人工智能", category="科技", weight=0.9)]
        )
    )


@dataclass
class _FakeLLMService:
    payload: str
    calls: list[dict[str, object]] = field(default_factory=list)

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "caller": caller,
            }
        )
        return LLMResponse(content=self.payload, provider="test", model="test-model")


@dataclass
class _FakeYtClient:
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def search_videos(self, query: str, limit: int = 15) -> list[dict[str, Any]]:
        self.calls.append((query, limit))
        return [
            {
                "videoId": f"video-{len(self.calls)}",
                "title": {"simpleText": f"{query} result"},
            }
        ]

    async def get_channel_videos(self, channel_id: str, limit: int = 5) -> list[dict[str, Any]]:
        self.calls.append((channel_id, limit))
        return []


@pytest.mark.asyncio
async def test_youtube_search_uses_queries_from_llm_response_content() -> None:
    llm = _FakeLLMService('{"queries": ["ai documentary", "systems design"]}')
    client = _FakeYtClient()
    strategy = YoutubeSearchStrategy(
        client=client,
        llm_service=llm,
        queries_per_run=2,
        results_per_query=3,
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=5)

    assert strategy.last_intermediates == {"queries": ["ai documentary", "systems design"]}
    assert [call[0] for call in client.calls] == ["ai documentary", "systems design"]
    assert [item.source_strategy for item in results] == ["yt_search", "yt_search"]


class _MemoryWithYoutubeUrls:
    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        assert event_types == ["follow"]
        return [
            {
                "url": "https://www.youtube.com/@AswathDamodaranonValuation",
                "metadata": json.dumps({"source_platform": "youtube"}),
            },
            {
                "url": "https://www.youtube.com/@ignored",
                "metadata": json.dumps({"source_platform": "bilibili"}),
            },
            {
                "url": "",
                "metadata": {
                    "source_platform": "youtube",
                    "channel_id": "UC123",
                },
            },
            {
                "url": "https://www.youtube.com/@AswathDamodaranonValuation",
                "metadata": json.dumps({"source_platform": "youtube"}),
            },
        ]


def test_youtube_channel_reads_channel_url_when_channel_id_missing() -> None:
    strategy = YoutubeChannelStrategy(
        client=_FakeYtClient(),
        llm_service=_FakeLLMService("{}"),
        memory=_MemoryWithYoutubeUrls(),
        max_channels=10,
    )

    assert strategy._subscribed_channel_ids() == [
        "https://www.youtube.com/@AswathDamodaranonValuation",
        "UC123",
    ]


def test_runtime_youtube_strategy_builder_uses_source_config() -> None:
    from openbiliclaw.api.runtime_context import build_youtube_discovery_strategies
    from openbiliclaw.config import Config

    config = Config()
    config.sources.youtube.daily_search_budget = 4
    config.sources.youtube.daily_trending_budget = 37
    config.sources.youtube.daily_channel_budget = 6

    strategies = build_youtube_discovery_strategies(
        config=config,
        client=_FakeYtClient(),
        llm_service=_FakeLLMService("{}"),
        memory=_MemoryWithYoutubeUrls(),
        concurrency=None,
    )

    assert [strategy.name for strategy in strategies] == [
        "yt_search",
        "yt_trending",
        "yt_channel",
    ]
    assert strategies[0].queries_per_run == 4
    assert strategies[1].fetch_limit == 37
    assert strategies[2].max_channels == 6


def test_extract_innertube_config_reads_current_youtube_constants() -> None:
    html = (
        '{"INNERTUBE_API_KEY":"key-1",'
        '"INNERTUBE_CLIENT_VERSION":"2.20260514.01.00",'
        '"INNERTUBE_CONTEXT_CLIENT_NAME":1}'
    )

    config = _extract_innertube_config(html)

    assert config.api_key == "key-1"
    assert config.client_version == "2.20260514.01.00"
    assert config.client_name_header == "1"


def test_channel_uploads_url_accepts_handles_ids_and_urls() -> None:
    assert (
        _channel_uploads_url("https://www.youtube.com/@AswathDamodaranonValuation")
        == "https://www.youtube.com/@AswathDamodaranonValuation/videos"
    )
    assert _channel_uploads_url("@demo") == "https://www.youtube.com/@demo/videos"
    assert _channel_uploads_url("UC123") == "https://www.youtube.com/channel/UC123/videos"


def test_extract_yt_initial_data_videos_reads_video_renderers() -> None:
    html = """
    <script>
      var ytInitialData = {
        "contents": {
          "twoColumnBrowseResultsRenderer": {
            "tabs": [
              {"tabRenderer": {"content": {"richGridRenderer": {"contents": [
                {"richItemRenderer": {"content": {"videoRenderer": {
                  "videoId": "abc123",
                  "title": {"runs": [{"text": "Topic video"}]},
                  "ownerText": {"runs": [{"text": "Topic channel"}]}
                }}}},
                {"richItemRenderer": {"content": {"videoRenderer": {
                  "videoId": "def456",
                  "title": {"simpleText": "Second topic video"}
                }}}}
              ]}}}}
            ]
          }
        }
      };
    </script>
    """

    videos = _extract_yt_initial_data_videos(html, limit=10)

    assert [item["videoId"] for item in videos] == ["abc123", "def456"]


def test_topic_page_trending_dedupes_public_topic_pages() -> None:
    first_page = """
    <script>
      var ytInitialData = {"contents": [
        {"videoRenderer": {"videoId": "abc123", "title": {"simpleText": "A"}}},
        {"videoRenderer": {"videoId": "def456", "title": {"simpleText": "B"}}}
      ]};
    </script>
    """
    second_page = """
    <script>
      var ytInitialData = {"contents": [
        {"videoRenderer": {"videoId": "abc123", "title": {"simpleText": "A duplicate"}}},
        {"videoRenderer": {"videoId": "ghi789", "title": {"simpleText": "C"}}}
      ]};
    </script>
    """
    pages = [first_page, second_page]

    def fake_fetch(_url: str) -> str:
        return pages.pop(0)

    videos = _topic_page_trending("US", 3, fetch_html=fake_fetch, topic_paths=("gaming", "news"))

    assert [item["videoId"] for item in videos] == ["abc123", "def456", "ghi789"]
