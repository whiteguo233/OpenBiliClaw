"""Tests for WebSourceAdapter with pluggable browser backend.

The xhs-specific adapter used to live here as a ``XiaohongshuAdapter``
subclass. It was removed when xhs detail enrichment moved to the sidecar
HTTP adapter — see ``test_xiaohongshu_adapter.py``. The URL-backfill
behaviour tested below is still exercised by the generic web adapter
against a simulated xhs page.
"""

from __future__ import annotations

from typing import Any

import pytest

from openbiliclaw.sources import web_adapter as web_adapter_module
from openbiliclaw.sources.browser import PageSnapshot
from openbiliclaw.sources.protocol import SourceRecipe
from openbiliclaw.sources.web_adapter import WebSourceAdapter


class _RecordingBrowser:
    """Stand-in for BrowserManager that records how it was built and called."""

    last_init: dict[str, Any] = {}
    next_snapshot: PageSnapshot = PageSnapshot(text="fake-xhs-page", anchors=[])

    def __init__(self, **kwargs: Any) -> None:
        _RecordingBrowser.last_init = dict(kwargs)
        self._closed = False

    @property
    def is_available(self) -> bool:
        return True

    async def get_page_snapshot(self, url: str) -> PageSnapshot:
        _RecordingBrowser.last_init["visited_url"] = url
        return _RecordingBrowser.next_snapshot

    async def get_page_text(self, url: str) -> str:
        snap = await self.get_page_snapshot(url)
        return snap.text

    async def close(self) -> None:
        self._closed = True


class TestWebSourceAdapterBrowserWiring:
    """WebSourceAdapter must forward cdp_url to BrowserManager."""

    @pytest.mark.asyncio
    async def test_forwards_cdp_url_to_browser_manager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(web_adapter_module, "BrowserManager", _RecordingBrowser)

        async def fake_extract(text: str, **kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(web_adapter_module, "extract_content_from_page", fake_extract)

        adapter = WebSourceAdapter(
            llm_service=None,
            browser_cdp_url="http://127.0.0.1:9222",
        )

        recipe = SourceRecipe(
            id="r1",
            source_type="web",
            name="generic-search",
            strategy="web_extract",
            config={
                "url_template": "https://example.com/search?keyword={query}",
                "query": "机械键盘",
            },
        )

        await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert _RecordingBrowser.last_init["cdp_url"] == "http://127.0.0.1:9222"
        assert (
            _RecordingBrowser.last_init["visited_url"]
            == "https://example.com/search?keyword=机械键盘"
        )

    @pytest.mark.asyncio
    async def test_empty_cdp_url_passes_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(web_adapter_module, "BrowserManager", _RecordingBrowser)

        async def fake_extract(text: str, **kwargs: Any) -> list[Any]:
            return []

        monkeypatch.setattr(web_adapter_module, "extract_content_from_page", fake_extract)

        adapter = WebSourceAdapter(llm_service=None)
        recipe = SourceRecipe(
            id="r2",
            source_type="web",
            name="generic",
            strategy="web_extract",
            config={"url": "https://example.com"},
        )

        await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert _RecordingBrowser.last_init["cdp_url"] == ""


class TestWebSourceAdapterURLBackfill:
    """Adapter must rebuild URLs that innerText throws away.

    The LLM extractor only sees visible text, so for pages like the XHS
    search result every item comes back with ``content_url = ""``. The
    adapter pairs that extraction pass with the anchor list captured
    alongside innerText and backfills URLs by fuzzy title matching.
    """

    @pytest.mark.asyncio
    async def test_backfills_content_url_from_anchors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.discovery.engine import DiscoveredContent

        _RecordingBrowser.next_snapshot = PageSnapshot(
            text="some page text",
            anchors=[
                (
                    "Filco用了五六年，手痒试了试国产机械键盘",
                    "https://www.xiaohongshu.com/explore/abc123def456",
                ),
                ("unrelated nav link", "https://www.xiaohongshu.com/user/profile/xxx"),
            ],
        )
        monkeypatch.setattr(web_adapter_module, "BrowserManager", _RecordingBrowser)

        async def fake_extract(text: str, **kwargs: Any) -> list[DiscoveredContent]:
            return [
                DiscoveredContent(
                    content_id="Filco用了五六年，手痒试了试国产机械键盘"[:32],
                    content_url="",
                    source_platform="xiaohongshu",
                    title="Filco用了五六年，手痒试了试国产机械键盘",
                    source_strategy="web_extract",
                )
            ]

        monkeypatch.setattr(web_adapter_module, "extract_content_from_page", fake_extract)

        adapter = WebSourceAdapter(llm_service=None, browser_cdp_url="http://127.0.0.1:9222")
        recipe = SourceRecipe(
            id="r3",
            source_type="xiaohongshu",
            name="小红书",
            strategy="web_extract",
            config={"url": "https://www.xiaohongshu.com/search_result?keyword=x"},
        )

        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]

        assert len(items) == 1
        assert items[0].content_url == "https://www.xiaohongshu.com/explore/abc123def456"
        assert items[0].content_id == "abc123def456"

    @pytest.mark.asyncio
    async def test_keeps_existing_url_if_extractor_already_populated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openbiliclaw.discovery.engine import DiscoveredContent

        _RecordingBrowser.next_snapshot = PageSnapshot(
            text="x",
            anchors=[("irrelevant", "https://example.com/wrong")],
        )
        monkeypatch.setattr(web_adapter_module, "BrowserManager", _RecordingBrowser)

        preset_url = "https://www.xiaohongshu.com/explore/preset999"

        async def fake_extract(text: str, **kwargs: Any) -> list[DiscoveredContent]:
            return [
                DiscoveredContent(
                    content_id="preset999",
                    content_url=preset_url,
                    source_platform="xiaohongshu",
                    title="some note",
                    source_strategy="web_extract",
                )
            ]

        monkeypatch.setattr(web_adapter_module, "extract_content_from_page", fake_extract)

        adapter = WebSourceAdapter(llm_service=None, browser_cdp_url="http://127.0.0.1:9222")
        recipe = SourceRecipe(
            id="r4",
            source_type="xiaohongshu",
            name="x",
            strategy="web_extract",
            config={"url": "https://www.xiaohongshu.com/explore/preset999"},
        )

        items = await adapter.fetch(recipe, profile=None, limit=5)  # type: ignore[arg-type]
        assert items[0].content_url == preset_url
        assert items[0].content_id == "preset999"
