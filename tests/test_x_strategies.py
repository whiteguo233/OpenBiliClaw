"""Tests for the three X (Twitter) discovery strategies.

Each strategy wraps an :class:`XClient` read method and normalizes the raw
``tweet_to_dict`` dicts into :class:`DiscoveredContent`. ``XSearchStrategy``
generates keyword(s) from the Soul profile (reusing the xhs-keyword approach),
``XForYouStrategy`` reads the For-You home timeline, and ``XCreatorStrategy``
reads a creator's recent tweets by handle.

No network, no real cookie: a fake ``XClient`` records calls and returns
canned ``tweet_to_dict`` dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openbiliclaw.discovery.strategies.x import (
    XCreatorStrategy,
    XForYouStrategy,
    XSearchStrategy,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="rust async", category="科技", weight=0.9)]
        )
    )


def _tweet(tweet_id: str, text: str = "hello world", screen_name: str = "handle") -> dict[str, Any]:
    return {
        "id": tweet_id,
        "text": text,
        "author": {"screenName": screen_name, "name": "Handle"},
        "metrics": {"likes": 3, "views": 100},
    }


@dataclass
class _FakeXClient:
    """Records which read method was called and returns canned tweet dicts."""

    search_result: list[dict[str, Any]] = field(default_factory=list)
    feed_result: list[dict[str, Any]] = field(default_factory=list)
    creator_result: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    async def search(self, query: str, *, limit: int, product: str = "Top") -> list[dict[str, Any]]:
        self.calls.append(("search", (query,), {"limit": limit, "product": product}))
        return list(self.search_result)

    async def for_you(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("for_you", (), {"limit": limit}))
        return list(self.feed_result)

    async def user_tweets(self, handle: str, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("user_tweets", (handle,), {"limit": limit}))
        return list(self.creator_result)


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
        self.calls.append({"user_input": user_input, "caller": caller})
        return LLMResponse(content=self.payload, provider="test", model="test-model")


# ── XSearchStrategy ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_strategy_calls_client_search_and_normalizes() -> None:
    client = _FakeXClient(search_result=[_tweet("1790000000000000001")])
    llm = _FakeLLMService('{"keywords": ["rust async runtime"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    items = await strategy.discover(_profile(), limit=5)

    assert [c[0] for c in client.calls] == ["search"]
    assert client.calls[0][2]["limit"] == 5
    assert client.calls[0][1][0] == "rust async runtime"  # keyword from LLM
    assert items and all(i.source_platform == "twitter" for i in items)
    assert all(i.source_strategy == "x-search" for i in items)
    assert items[0].content_id == "1790000000000000001"


@pytest.mark.asyncio
async def test_search_keyword_prompt_uses_unified_profile_summary() -> None:
    # Keyword generation now feeds the same build_profile_summary dict as
    # B站 / YouTube query-gen — not the old top-15 name|category|weight tuples.
    client = _FakeXClient(search_result=[_tweet("1790000000000000009")])
    llm = _FakeLLMService('{"keywords": ["rust async runtime"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    await strategy.discover(_profile(), limit=5)

    assert llm.calls and llm.calls[0]["caller"] == "discovery.x.keyword_gen"
    user_input = str(llm.calls[0]["user_input"])
    assert "<profile_summary>" in user_input
    # interest_domains / disliked_topics are structured fields the old tuple
    # prompt never carried — their presence proves the unified dict is in use.
    assert "interest_domains" in user_input
    assert "disliked_topics" in user_input
    assert "name | category | weight" not in user_input


@pytest.mark.asyncio
async def test_search_strategy_explicit_query_skips_llm() -> None:
    client = _FakeXClient(search_result=[_tweet("1790000000000000002")])
    llm = _FakeLLMService('{"keywords": ["should not be used"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    items = await strategy.discover(_profile(), limit=5, query="explicit query")

    assert client.calls[0][1][0] == "explicit query"
    assert llm.calls == []  # explicit query short-circuits keyword generation
    assert len(items) == 1


@pytest.mark.asyncio
async def test_search_strategy_drops_tombstones() -> None:
    client = _FakeXClient(
        search_result=[_tweet("1790000000000000003"), {"id": "", "text": "tombstone"}]
    )
    llm = _FakeLLMService('{"keywords": ["rust"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    items = await strategy.discover(_profile(), limit=5)

    assert len(items) == 1  # the empty-id tombstone normalized to None and was dropped


@pytest.mark.asyncio
async def test_search_strategy_falls_back_to_interest_names_without_llm() -> None:
    # P1.3: no LLM → deterministic interest-name keywords (not empty), so the
    # unified planner / legacy path never loses X to a missing LLM.
    client = _FakeXClient(search_result=[_tweet("1790000000000000020")])
    strategy = XSearchStrategy(client=client, llm_service=None)

    items = await strategy.discover(_profile(), limit=5)

    assert client.calls[0][1][0] == "rust async"  # the profile's interest name
    assert items


@pytest.mark.asyncio
async def test_search_strategy_falls_back_on_llm_failure() -> None:
    class _BoomLLM(_FakeLLMService):
        async def complete_structured_task(self, **kwargs: object) -> object:
            raise RuntimeError("llm down")

    client = _FakeXClient(search_result=[_tweet("1790000000000000021")])
    strategy = XSearchStrategy(client=client, llm_service=_BoomLLM(payload=""))

    items = await strategy.discover(_profile(), limit=5)

    assert client.calls[0][1][0] == "rust async"
    assert items


# ── XForYouStrategy ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_for_you_strategy_calls_client_for_you() -> None:
    client = _FakeXClient(feed_result=[_tweet("1790000000000000004")])
    strategy = XForYouStrategy(client=client)

    items = await strategy.discover(_profile(), limit=8)

    assert [c[0] for c in client.calls] == ["for_you"]
    assert client.calls[0][2]["limit"] == 8
    assert items and all(i.source_platform == "twitter" for i in items)
    assert all(i.source_strategy == "x-feed" for i in items)


# ── XCreatorStrategy ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creator_strategy_calls_user_tweets_with_handle() -> None:
    client = _FakeXClient(creator_result=[_tweet("1790000000000000005")])
    strategy = XCreatorStrategy(client=client)

    items = await strategy.discover(_profile(), limit=6, handle="@handle")

    assert [c[0] for c in client.calls] == ["user_tweets"]
    assert client.calls[0][1][0] == "@handle"
    assert client.calls[0][2]["limit"] == 6
    assert items and all(i.source_platform == "twitter" for i in items)
    assert all(i.source_strategy == "x-creator" for i in items)


@pytest.mark.asyncio
async def test_creator_strategy_without_handle_is_noop() -> None:
    client = _FakeXClient(creator_result=[_tweet("1790000000000000006")])
    strategy = XCreatorStrategy(client=client)

    items = await strategy.discover(_profile(), limit=6, handle="")

    assert client.calls == []  # no handle → nothing to fetch
    assert items == []


# ── P1.5 strategy keyword injection ──────────────────────────────────


@pytest.mark.asyncio
async def test_x_search_injected_keywords_skip_llm_generation() -> None:
    client = _FakeXClient(search_result=[_tweet("1790000000000000030")])
    llm = _FakeLLMService('{"keywords": ["should not be used"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    items = await strategy.discover(
        _profile(),
        limit=5,
        queries=["rust async runtime", "ml papers"],
    )

    assert [c[0] for c in client.calls] == ["search", "search"]
    assert [c[1][0] for c in client.calls] == ["rust async runtime", "ml papers"]
    assert llm.calls == []  # injected keywords skip keyword generation
    assert items
    assert strategy.last_intermediates == {"keywords": ["rust async runtime", "ml papers"]}


@pytest.mark.asyncio
async def test_x_search_explicit_query_wins_over_injected_keywords() -> None:
    client = _FakeXClient(search_result=[_tweet("1790000000000000031")])
    llm = _FakeLLMService('{"keywords": ["nope"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    await strategy.discover(
        _profile(),
        limit=5,
        query="explicit query",
        queries=["ignored kw"],
    )

    # The single explicit query short-circuit still wins.
    assert [c[1][0] for c in client.calls] == ["explicit query"]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_x_search_injected_keywords_are_deduped() -> None:
    client = _FakeXClient(search_result=[])
    llm = _FakeLLMService('{"keywords": ["x"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    await strategy.discover(
        _profile(),
        limit=5,
        queries=["  rust  ", "rust", "", "go"],
    )

    assert [c[1][0] for c in client.calls] == ["rust", "go"]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_x_search_without_injection_still_generates() -> None:
    # Flag-off / no-injection regression: query="" and queries=None → LLM gen.
    client = _FakeXClient(search_result=[_tweet("1790000000000000032")])
    llm = _FakeLLMService('{"keywords": ["rust async runtime"]}')
    strategy = XSearchStrategy(client=client, llm_service=llm)

    await strategy.discover(_profile(), limit=5)

    assert llm.calls and llm.calls[0]["caller"] == "discovery.x.keyword_gen"
    assert client.calls[0][1][0] == "rust async runtime"
