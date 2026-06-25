"""Tests for the BilibiliAdapter and SourceAdapter protocol (Phase 1)."""

from __future__ import annotations

import asyncio

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.bilibili_adapter import BilibiliAdapter
from openbiliclaw.sources.protocol import SourceAdapter, SourceRecipe
from openbiliclaw.sources.registry import AdapterRegistry

# ── Fake strategy for testing ───────────────────────────────────────


class FakeStrategy:
    def __init__(self, name: str, items: list[DiscoveredContent]) -> None:
        self._name = name
        self._items = items
        self.calls: list[tuple[object, int]] = []

    @property
    def name(self) -> str:
        return self._name

    async def discover(self, profile: object, limit: int = 20) -> list[DiscoveredContent]:
        self.calls.append((profile, limit))
        return list(self._items)

    def create_backfill_strategy(self):
        return None


# ── BilibiliAdapter tests ───────────────────────────────────────────


class TestBilibiliAdapter:
    def test_source_type_is_bilibili(self) -> None:
        adapter = BilibiliAdapter()
        assert adapter.source_type == "bilibili"

    def test_satisfies_source_adapter_protocol(self) -> None:
        adapter = BilibiliAdapter()
        assert isinstance(adapter, SourceAdapter)

    def test_available_strategies_lists_registered(self) -> None:
        search = FakeStrategy("search", [])
        trending = FakeStrategy("trending", [])
        adapter = BilibiliAdapter(search=search, trending=trending)
        assert sorted(adapter.available_strategies) == ["search", "trending"]

    def test_fetch_delegates_to_correct_strategy(self) -> None:
        items = [DiscoveredContent(bvid="BV1test", title="Test", up_name="UP")]
        search = FakeStrategy("search", items)
        trending = FakeStrategy("trending", [])
        adapter = BilibiliAdapter(search=search, trending=trending)

        recipe = SourceRecipe(
            id="r1",
            source_type="bilibili",
            name="搜索",
            strategy="search",
        )
        result = asyncio.run(
            adapter.fetch(recipe, profile=object(), limit=15),
        )

        assert len(result) == 1
        assert result[0].bvid == "BV1test"
        assert search.calls == [(object, 15)] or len(search.calls) == 1
        assert search.calls[0][1] == 15
        assert trending.calls == []

    def test_fetch_populates_multisource_fields(self) -> None:
        items = [DiscoveredContent(bvid="BV1ms", up_name="老番茄")]
        search = FakeStrategy("search", items)
        adapter = BilibiliAdapter(search=search)

        recipe = SourceRecipe(
            id="r1",
            source_type="bilibili",
            name="搜索",
            strategy="search",
        )
        result = asyncio.run(
            adapter.fetch(recipe, profile=object()),
        )

        item = result[0]
        assert item.source_platform == "bilibili"
        assert item.content_id == "BV1ms"
        assert item.content_url == "https://www.bilibili.com/video/BV1ms"
        assert item.author_name == "老番茄"

    def test_fetch_unknown_strategy_returns_empty(self) -> None:
        adapter = BilibiliAdapter()
        recipe = SourceRecipe(
            id="r1",
            source_type="bilibili",
            name="???",
            strategy="nonexistent",
        )
        result = asyncio.run(
            adapter.fetch(recipe, profile=object()),
        )
        assert result == []


# ── AdapterRegistry tests ──────────────────────────────────────────


class TestAdapterRegistry:
    def test_register_and_resolve(self) -> None:
        registry = AdapterRegistry()
        adapter = BilibiliAdapter()
        registry.register(adapter)

        recipe = SourceRecipe(
            id="r1",
            source_type="bilibili",
            name="搜索",
            strategy="search",
        )
        assert registry.resolve(recipe) is adapter

    def test_resolve_unknown_returns_none(self) -> None:
        registry = AdapterRegistry()
        recipe = SourceRecipe(
            id="r1",
            source_type="youtube",
            name="YT",
            strategy="search",
        )
        assert registry.resolve(recipe) is None

    def test_has(self) -> None:
        registry = AdapterRegistry()
        adapter = BilibiliAdapter()
        registry.register(adapter)
        assert registry.has("bilibili")
        assert not registry.has("youtube")

    def test_source_types(self) -> None:
        registry = AdapterRegistry()
        adapter = BilibiliAdapter()
        registry.register(adapter)
        assert registry.source_types == ["bilibili"]
