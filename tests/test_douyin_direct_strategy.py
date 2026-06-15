"""Tests for the Douyin direct-cookie discovery strategy."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from openbiliclaw.discovery.strategies.douyin_direct import DouyinDirectStrategy
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.soul.profile import InterestTag, PreferenceLayer, SoulProfile


@dataclass
class _FakeKeywordLLM:
    """Records the keyword-gen call and returns a canned payload."""

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


class _FakeDouyinClient:
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "1", "desc": f"{keyword} 视频", "author": {"nickname": "A"}}]

    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "2", "desc": "热点视频", "author": {"nickname": "B"}}]

    async def get_creator_posts(self, sec_uid: str, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "3", "desc": f"{sec_uid} 作者视频", "author": {"nickname": "C"}}]

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]:
        return [{"aweme_id": "4", "desc": "首页推荐视频", "author": {"nickname": "D"}}]


class _RecordingSearchClient(_FakeDouyinClient):
    def __init__(self) -> None:
        self.keywords: list[str] = []

    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
        self.keywords.append(keyword)
        return []


def _profile() -> SoulProfile:
    return SoulProfile(
        personality_portrait="喜欢理解复杂系统",
        core_traits=["理性", "好奇"],
        preferences=PreferenceLayer(
            interests=[
                InterestTag(name="机械键盘", category="科技", weight=0.9),
                InterestTag(name="城市观察", category="生活", weight=0.7),
            ]
        ),
    )


@pytest.mark.asyncio
async def test_strategy_returns_douyin_discovered_content() -> None:
    strategy = DouyinDirectStrategy(
        client=_FakeDouyinClient(),
        sources=("search", "hot", "creator"),
        seed_keywords=["机械键盘"],
        creator_sec_uids=["sec-1"],
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert {item.source_platform for item in items} == {"douyin"}
    assert {item.source_strategy for item in items} == {
        "dy-direct-search",
        "dy-direct-hot",
        "dy-direct-creator",
    }
    assert [item.content_id for item in items] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_strategy_dedupes_before_returning() -> None:
    class DuplicateClient(_FakeDouyinClient):
        async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]:
            return [{"aweme_id": "1", "desc": "重复视频", "author": {"nickname": "B"}}]

    strategy = DouyinDirectStrategy(
        client=DuplicateClient(),
        sources=("search", "hot"),
        seed_keywords=["机械键盘"],
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert [item.content_id for item in items] == ["1"]


@pytest.mark.asyncio
async def test_strategy_uses_profile_interests_as_fallback_keywords() -> None:
    class RecordingClient(_FakeDouyinClient):
        def __init__(self) -> None:
            self.keywords: list[str] = []

        async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]:
            self.keywords.append(keyword)
            return []

    client = RecordingClient()
    strategy = DouyinDirectStrategy(
        client=client,
        sources=("search",),
        seed_keywords=(),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == ["机械键盘", "城市观察"]


@pytest.mark.asyncio
async def test_strategy_uses_client_search_source_strategy() -> None:
    class PluginSearchClient(_FakeDouyinClient):
        search_source_strategy = "dy-plugin-search"

    strategy = DouyinDirectStrategy(
        client=PluginSearchClient(),
        sources=("search",),
        seed_keywords=["机械键盘"],
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert [item.source_strategy for item in items] == ["dy-plugin-search"]


@pytest.mark.asyncio
async def test_strategy_uses_client_hot_source_strategy() -> None:
    class PluginHotClient(_FakeDouyinClient):
        hot_source_strategy = "dy-plugin-hot-related"

    strategy = DouyinDirectStrategy(
        client=PluginHotClient(),
        sources=("hot",),
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert [item.source_strategy for item in items] == ["dy-plugin-hot-related"]


@pytest.mark.asyncio
async def test_strategy_uses_client_feed_source_strategy() -> None:
    class PluginFeedClient(_FakeDouyinClient):
        feed_source_strategy = "dy-plugin-feed"

    strategy = DouyinDirectStrategy(
        client=PluginFeedClient(),
        sources=("feed",),
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert [item.source_strategy for item in items] == ["dy-plugin-feed"]
    assert [item.content_id for item in items] == ["4"]


@pytest.mark.asyncio
async def test_strategy_uses_llm_generated_keywords() -> None:
    # Aligned with B站 / 小红书 / X: when an llm_service is wired, Douyin search
    # keywords come from the LLM fed the full build_profile_summary dict.
    client = _RecordingSearchClient()
    llm = _FakeKeywordLLM('{"keywords": ["露营装备测评", "和田玉鉴别教程"]}')
    strategy = DouyinDirectStrategy(
        client=client,
        llm_service=llm,
        sources=("search",),
        seed_keywords=(),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == ["露营装备测评", "和田玉鉴别教程"]
    assert llm.calls[0]["caller"] == "discovery.douyin.keyword_gen"
    user_input = str(llm.calls[0]["user_input"])
    assert "<profile_summary>" in user_input
    assert "interest_domains" in user_input
    assert "name | category | weight" not in user_input


@pytest.mark.asyncio
async def test_strategy_falls_back_to_interests_when_llm_keyword_fails() -> None:
    # Non-JSON keyword reply → parse yields nothing → deterministic interest
    # names keep Douyin discovering (original behavior is the safety net).
    client = _RecordingSearchClient()
    llm = _FakeKeywordLLM("not valid json")
    strategy = DouyinDirectStrategy(
        client=client,
        llm_service=llm,
        sources=("search",),
        seed_keywords=(),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == ["机械键盘", "城市观察"]


@pytest.mark.asyncio
async def test_strategy_seed_keywords_skip_llm() -> None:
    client = _RecordingSearchClient()
    llm = _FakeKeywordLLM('{"keywords": ["不应被使用"]}')
    strategy = DouyinDirectStrategy(
        client=client,
        llm_service=llm,
        sources=("search",),
        seed_keywords=("机械键盘",),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == ["机械键盘"]
    assert llm.calls == []  # explicit seeds short-circuit keyword generation


# ── P1.5 keyword source-gating + seed injection ──────────────────────


@pytest.mark.asyncio
async def test_strategy_hot_only_does_not_synthesize_keywords() -> None:
    # hot/feed/creator-only modes (no 'search' in sources) must NOT run keyword
    # synthesis — no LLM keyword-gen call and no interest-name fallback.
    llm = _FakeKeywordLLM('{"keywords": ["不应被生成"]}')
    strategy = DouyinDirectStrategy(
        client=_FakeDouyinClient(),
        llm_service=llm,
        sources=("hot",),
        seed_keywords=(),
        llm_evaluation=False,
    )

    items = await strategy.discover(_profile(), limit=10)

    assert llm.calls == []  # no keyword generation when search source is absent
    assert strategy.last_intermediates["keywords"] == []
    # hot source still produced its candidate.
    assert [item.content_id for item in items] == ["2"]


@pytest.mark.asyncio
async def test_strategy_feed_only_does_not_synthesize_keywords() -> None:
    llm = _FakeKeywordLLM('{"keywords": ["不应被生成"]}')
    strategy = DouyinDirectStrategy(
        client=_FakeDouyinClient(),
        llm_service=llm,
        sources=("feed",),
        seed_keywords=(),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert llm.calls == []
    assert strategy.last_intermediates["keywords"] == []


@pytest.mark.asyncio
async def test_strategy_hot_feed_only_skips_keywords_even_with_seeds() -> None:
    # Even if seeds are present, a hot/feed-only run never searches them — and
    # never reports them as the run's keywords.
    client = _RecordingSearchClient()
    strategy = DouyinDirectStrategy(
        client=client,
        sources=("hot", "feed"),
        seed_keywords=("机械键盘",),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == []  # search_aweme never invoked
    assert strategy.last_intermediates["keywords"] == []


@pytest.mark.asyncio
async def test_strategy_seed_keywords_injected_to_search() -> None:
    # Planner injection path: seed_keywords flow straight to search_aweme.
    client = _RecordingSearchClient()
    strategy = DouyinDirectStrategy(
        client=client,
        sources=("search",),
        seed_keywords=("露营装备测评", "和田玉鉴别教程"),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert client.keywords == ["露营装备测评", "和田玉鉴别教程"]
    assert strategy.last_intermediates["keywords"] == ["露营装备测评", "和田玉鉴别教程"]
