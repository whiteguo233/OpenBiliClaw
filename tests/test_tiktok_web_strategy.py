"""Tests for the TikTok feed strategy and web-backend strategy paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.strategies.tiktok import (
    TiktokFeedStrategy,
    TiktokTagStrategy,
    TiktokUserStrategy,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


def _profile() -> SoulProfile:
    return SoulProfile(
        preferences=PreferenceLayer(
            interests=[InterestTag(name="人工智能", category="科技", weight=0.9)]
        )
    )


def _content(video_id: str, **overrides: Any) -> DiscoveredContent:
    kwargs: dict[str, Any] = {
        "content_id": video_id,
        "content_url": f"https://www.tiktok.com/@creator/video/{video_id}",
        "source_platform": "tiktok",
        "title": f"tiktok {video_id}",
        "author_name": "Creator",
    }
    kwargs.update(overrides)
    return DiscoveredContent(**kwargs)


@dataclass
class _FakeLLMService:
    payload: str = "{}"
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
        self.calls.append({"caller": caller, "user_input": user_input})
        return LLMResponse(content=self.payload, provider="test", model="test-model")


@dataclass
class _WebLikeClient:
    """Mimics the web backend / router: DiscoveredContent or None results."""

    feed_result: Any = field(default_factory=list)
    tag_result: Any = field(default_factory=list)
    user_result: Any = field(default_factory=list)
    feed_calls: list[int] = field(default_factory=list)

    async def get_feed(self, *, limit: int = 12) -> Any:
        self.feed_calls.append(limit)
        return self.feed_result

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> Any:
        return self.tag_result

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> Any:
        return self.user_result


async def test_feed_strategy_returns_stamped_candidates() -> None:
    client = _WebLikeClient(feed_result=[_content("f1"), _content("f2"), _content("f1")])
    strategy = TiktokFeedStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [item.content_id for item in results] == ["f1", "f2"]
    assert all(item.source_strategy == "tiktok_feed" for item in results)
    assert all(item.source_platform == "tiktok" for item in results)
    assert strategy.last_intermediates == {"fetched": 2}
    assert client.feed_calls == [12]


async def test_feed_strategy_backend_unavailable_returns_empty() -> None:
    client = _WebLikeClient(feed_result=None)
    strategy = TiktokFeedStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    assert await strategy.discover(_profile(), limit=10) == []
    assert strategy.last_intermediates == {"fetched": 0}


def test_feed_strategy_platform_and_name() -> None:
    strategy = TiktokFeedStrategy(
        client=_WebLikeClient(),  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
    )
    assert (strategy.name, strategy.source_platform) == ("tiktok_feed", "tiktok")


async def test_tag_strategy_accepts_discovered_content_from_web_backend() -> None:
    client = _WebLikeClient(tag_result=[_content("w1")])
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload='{"tags": ["cat"]}'),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [item.content_id for item in results] == ["w1"]
    assert results[0].source_strategy == "tiktok_tag"


async def test_user_strategy_accepts_discovered_content_from_web_backend() -> None:
    client = _WebLikeClient(user_result=[_content("w2")])
    strategy = TiktokUserStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
        creators=("alice",),
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [item.content_id for item in results] == ["w2"]
    assert results[0].source_strategy == "tiktok_user"


# ---------------------------------------------------------------------------
# TiktokSearchStrategy
# ---------------------------------------------------------------------------


@dataclass
class _SearchClient:
    """Mimics the router's search surface."""

    results: dict[str, Any] = field(default_factory=dict)
    search_calls: list[tuple[str, int]] = field(default_factory=list)

    async def search_videos(self, keyword: str, *, limit: int = 20) -> Any:
        self.search_calls.append((keyword, limit))
        return self.results.get(keyword, [])


async def test_search_strategy_uses_planner_words_verbatim() -> None:
    from openbiliclaw.discovery.strategies.tiktok import TiktokSearchStrategy

    client = _SearchClient(
        results={
            "machine learning": [_content("s1")],
            "cooking tips": [_content("s2")],
        }
    )
    llm = _FakeLLMService(payload='{"keywords": ["unused"]}')
    strategy = TiktokSearchStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=llm,  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(
        _profile(),
        limit=10,
        # The whole point of search vs hashtags: multi-word phrases stay intact.
        queries=["machine learning", "cooking tips", "Machine Learning"],
        keyword_ids={"machine learning": 3, "cooking tips": 4},
    )

    called = [keyword for keyword, _ in client.search_calls]
    assert called == ["machine learning", "cooking tips"]
    # Injected words never hit the LLM.
    assert llm.calls == []
    by_id = {item.content_id: item for item in results}
    assert by_id["s1"].source_keyword_id == 3
    assert by_id["s2"].source_keyword_id == 4
    assert all(item.source_strategy == "tiktok_search" for item in results)
    assert strategy.last_intermediates["queries"] == ["machine learning", "cooking tips"]


async def test_search_strategy_llm_generates_keywords() -> None:
    from openbiliclaw.discovery.strategies.tiktok import TiktokSearchStrategy

    client = _SearchClient(results={"ai tools": [_content("s1")]})
    strategy = TiktokSearchStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload='{"keywords": ["ai tools", "booktok"]}'),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [keyword for keyword, _ in client.search_calls] == ["ai tools", "booktok"]
    assert [item.content_id for item in results] == ["s1"]


async def test_search_strategy_llm_failure_falls_back_to_interests() -> None:
    from openbiliclaw.discovery.strategies.tiktok import TiktokSearchStrategy

    class _BrokenLLM(_FakeLLMService):
        async def complete_structured_task(self, **kwargs: Any) -> object:
            raise RuntimeError("llm down")

    client = _SearchClient(results={"人工智能": [_content("s9")]})
    strategy = TiktokSearchStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_BrokenLLM(),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [keyword for keyword, _ in client.search_calls] == ["人工智能"]
    assert [item.content_id for item in results] == ["s9"]


async def test_search_strategy_tolerates_batch_failures_and_junk() -> None:
    from openbiliclaw.discovery.strategies.tiktok import TiktokSearchStrategy

    class _FlakySearchClient(_SearchClient):
        async def search_videos(self, keyword: str, *, limit: int = 20) -> Any:
            self.search_calls.append((keyword, limit))
            if keyword == "bad":
                raise RuntimeError("transport")
            return [_content("ok-1"), "not-a-content", _content("ok-1")]

    client = _FlakySearchClient()
    strategy = TiktokSearchStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10, queries=["good", "bad"])

    assert [item.content_id for item in results] == ["ok-1"]


def test_search_strategy_platform_and_name() -> None:
    from openbiliclaw.discovery.strategies.tiktok import TiktokSearchStrategy

    strategy = TiktokSearchStrategy(
        client=_SearchClient(),  # type: ignore[arg-type]
        llm_service=_FakeLLMService(),  # type: ignore[arg-type]
    )
    assert (strategy.name, strategy.source_platform) == ("tiktok_search", "tiktok")
