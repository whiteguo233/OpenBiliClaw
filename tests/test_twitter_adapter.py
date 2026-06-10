"""Tests for the X (Twitter) source adapter dispatch.

``XAdapter`` exposes ``source_type == "twitter"`` and dispatches ``fetch()``
by ``recipe.strategy`` (``search`` / ``feed`` / ``creator``) to the three
injected strategy callables. Tests inject fakes so no XClient / network is
involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.protocol import SourceAdapter, SourceRecipe
from openbiliclaw.sources.twitter_adapter import XAdapter


def _item(content_id: str, strategy: str) -> DiscoveredContent:
    return DiscoveredContent(
        title=f"tweet {content_id}",
        content_id=content_id,
        content_url=f"https://x.com/h/status/{content_id}",
        source_platform="twitter",
        source_strategy=strategy,
        content_type="tweet",
        body_text="hello",
    )


@dataclass
class _FakeStrategy:
    """A strategy stub recording each ``discover`` call and its kwargs."""

    items: list[DiscoveredContent]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def discover(
        self, profile: Any, *, limit: int = 20, **kwargs: Any
    ) -> list[DiscoveredContent]:
        self.calls.append({"profile": profile, "limit": limit, **kwargs})
        return list(self.items)


class _FakeXClient:
    """Placeholder — the adapter never calls it directly; strategies do."""


def _profile() -> object:
    return object()


# ── source_type / protocol ───────────────────────────────────────────


def test_source_type_is_twitter() -> None:
    adapter = XAdapter(
        client=_FakeXClient(),
        search=_FakeStrategy([]),
        feed=_FakeStrategy([]),
        creator=_FakeStrategy([]),
    )
    assert adapter.source_type == "twitter"


def test_satisfies_source_adapter_protocol() -> None:
    adapter = XAdapter(
        client=_FakeXClient(),
        search=_FakeStrategy([]),
        feed=_FakeStrategy([]),
        creator=_FakeStrategy([]),
    )
    assert isinstance(adapter, SourceAdapter)


# ── dispatch by strategy ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xadapter_dispatches_by_strategy() -> None:
    search = _FakeStrategy([_item("1790000000000000001", "x-search")])
    feed = _FakeStrategy([_item("1790000000000000002", "x-feed")])
    creator = _FakeStrategy([_item("1790000000000000003", "x-creator")])
    adapter = XAdapter(client=_FakeXClient(), search=search, feed=feed, creator=creator)

    items = await adapter.fetch(
        SourceRecipe(
            id="1",
            source_type="twitter",
            name="X-search",
            strategy="search",
            config={"query": "rust"},
        ),
        _profile(),
        limit=5,
    )

    assert items and all(i.source_platform == "twitter" for i in items)
    assert adapter.source_type == "twitter"
    assert len(search.calls) == 1
    assert search.calls[0]["limit"] == 5
    assert search.calls[0]["query"] == "rust"
    assert feed.calls == [] and creator.calls == []


@pytest.mark.asyncio
async def test_feed_strategy_maps_to_strategy_feed() -> None:
    search = _FakeStrategy([])
    feed = _FakeStrategy([_item("1790000000000000010", "x-feed")])
    creator = _FakeStrategy([])
    adapter = XAdapter(client=_FakeXClient(), search=search, feed=feed, creator=creator)

    items = await adapter.fetch(
        SourceRecipe(id="2", source_type="twitter", name="X-feed", strategy="feed"),
        _profile(),
        limit=7,
    )

    assert len(feed.calls) == 1
    assert feed.calls[0]["limit"] == 7
    assert items[0].source_strategy == "x-feed"
    assert search.calls == [] and creator.calls == []


@pytest.mark.asyncio
async def test_creator_strategy_receives_handle_from_recipe_config() -> None:
    creator = _FakeStrategy([_item("1790000000000000020", "x-creator")])
    adapter = XAdapter(
        client=_FakeXClient(),
        search=_FakeStrategy([]),
        feed=_FakeStrategy([]),
        creator=creator,
    )

    items = await adapter.fetch(
        SourceRecipe(
            id="3",
            source_type="twitter",
            name="X-creator",
            strategy="creator",
            config={"handle": "@somebody"},
        ),
        _profile(),
        limit=4,
    )

    assert len(creator.calls) == 1
    assert creator.calls[0]["handle"] == "@somebody"
    assert items[0].source_platform == "twitter"


@pytest.mark.asyncio
async def test_unknown_strategy_returns_empty() -> None:
    search = _FakeStrategy([_item("x", "x-search")])
    adapter = XAdapter(
        client=_FakeXClient(),
        search=search,
        feed=_FakeStrategy([]),
        creator=_FakeStrategy([]),
    )

    items = await adapter.fetch(
        SourceRecipe(id="4", source_type="twitter", name="X-?", strategy="bogus"),
        _profile(),
        limit=5,
    )

    assert items == []
    assert search.calls == []


@pytest.mark.asyncio
async def test_fetch_backfills_source_platform() -> None:
    """Items missing source_platform get it backfilled by the adapter."""
    bare = DiscoveredContent(
        title="bare",
        content_id="1790000000000000099",
        content_url="https://x.com/h/status/1790000000000000099",
        source_strategy="x-search",
        content_type="tweet",
    )
    bare.source_platform = ""  # simulate a strategy that forgot to set it
    adapter = XAdapter(
        client=_FakeXClient(),
        search=_FakeStrategy([bare]),
        feed=_FakeStrategy([]),
        creator=_FakeStrategy([]),
    )

    items = await adapter.fetch(
        SourceRecipe(id="5", source_type="twitter", name="X-search", strategy="search"),
        _profile(),
        limit=5,
    )

    assert items[0].source_platform == "twitter"
