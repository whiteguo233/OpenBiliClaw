"""Tests for TikTok discovery strategy integration edges."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openbiliclaw.discovery.strategies.tiktok import (
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


def _entry(video_id: str, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": video_id,
        "title": f"tiktok {video_id}",
        "uploader": "creator",
        "channel": "Creator",
        "webpage_url": f"https://www.tiktok.com/@creator/video/{video_id}",
    }
    entry.update(overrides)
    return entry


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
        self.calls.append({"caller": caller, "user_input": user_input})
        return LLMResponse(content=self.payload, provider="test", model="test-model")


@dataclass
class _FakeTiktokClient:
    tag_calls: list[tuple[str, int]] = field(default_factory=list)
    user_calls: list[tuple[str, int]] = field(default_factory=list)

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[dict[str, Any]]:
        self.tag_calls.append((tag, limit))
        return [_entry(f"tag-{tag}-1"), _entry(f"tag-{tag}-2")]

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> list[dict[str, Any]]:
        self.user_calls.append((handle, limit))
        return [_entry(f"user-{handle}-1")]


async def test_tag_strategy_llm_generates_hashtags() -> None:
    client = _FakeTiktokClient()
    llm = _FakeLLMService(payload='{"tags": ["aitools", "BookTok"]}')
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=llm,  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert client.tag_calls[0][0] == "aitools"
    assert client.tag_calls[1][0] == "BookTok"
    assert {item.content_id for item in results} == {
        "tag-aitools-1",
        "tag-aitools-2",
        "tag-BookTok-1",
        "tag-BookTok-2",
    }
    assert all(item.source_platform == "tiktok" for item in results)
    assert strategy.last_intermediates["tags"] == ["aitools", "BookTok"]


async def test_tag_strategy_configured_baseline_tags_merge_and_dedupe() -> None:
    client = _FakeTiktokClient()
    llm = _FakeLLMService(payload='{"tags": ["cooking", "#DIY"]}')
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=llm,  # type: ignore[arg-type]
        tags=("#cooking",),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    called = [tag for tag, _ in client.tag_calls]
    assert called == ["cooking", "DIY"]
    assert strategy.last_intermediates["tags"] == ["cooking", "DIY"]


async def test_tag_strategy_planner_injection_maps_words_to_hashtags() -> None:
    client = _FakeTiktokClient()
    llm = _FakeLLMService(payload='{"tags": ["unused"]}')
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=llm,  # type: ignore[arg-type]
        llm_evaluation=False,
    )

    results = await strategy.discover(
        _profile(),
        limit=10,
        queries=["machine learning", "satisfying"],
        keyword_ids={"machine learning": 7, "satisfying": 9},
    )

    # Injected words are compacted into hashtags and never hit the LLM.
    assert [tag for tag, _ in client.tag_calls] == ["machinelearning", "satisfying"]
    assert llm.calls == []
    by_id = {item.content_id: item for item in results}
    assert by_id["tag-machinelearning-1"].source_keyword_id == 7
    assert by_id["tag-satisfying-1"].source_keyword_id == 9


async def test_tag_strategy_planner_words_are_never_truncated_by_baseline() -> None:
    client = _FakeTiktokClient()
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload="{}"),
        tags=("baseline1", "baseline2", "baseline3"),
        tags_per_run=2,
        llm_evaluation=False,
    )

    await strategy.discover(
        _profile(),
        limit=10,
        queries=["claimed one", "claimed two"],
    )

    # tags_per_run=2 would normally keep only two tags, but the claimed words
    # are searched first — the producer marks them ``used`` on handoff, so
    # truncating one here would burn it unsearched.
    called = [tag for tag, _ in client.tag_calls]
    assert called[:2] == ["claimedone", "claimedtwo"]


async def test_tag_strategy_llm_failure_falls_back_to_interest_hashtags() -> None:
    class _BrokenLLM(_FakeLLMService):
        async def complete_structured_task(self, **kwargs: Any) -> object:
            raise RuntimeError("llm down")

    client = _FakeTiktokClient()
    strategy = TiktokTagStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_BrokenLLM(payload=""),
        llm_evaluation=False,
    )

    await strategy.discover(_profile(), limit=10)

    assert [tag for tag, _ in client.tag_calls] == ["人工智能"]


async def test_tag_strategy_deduplicates_across_tags() -> None:
    @dataclass
    class _SameForAllClient(_FakeTiktokClient):
        async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[dict[str, Any]]:
            self.tag_calls.append((tag, limit))
            return [_entry("dup-1"), _entry("dup-2")]

    strategy = TiktokTagStrategy(
        client=_SameForAllClient(),  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload='{"tags": ["a", "b"]}'),
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [item.content_id for item in results] == ["dup-1", "dup-2"]


async def test_user_strategy_fetches_configured_creators() -> None:
    client = _FakeTiktokClient()
    strategy = TiktokUserStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload="{}"),
        creators=("@alice", "bob", "not a handle"),
        llm_evaluation=False,
    )

    results = await strategy.discover(_profile(), limit=10)

    assert [handle for handle, _ in client.user_calls] == ["alice", "bob"]
    assert {item.content_id for item in results} == {"user-alice-1", "user-bob-1"}
    assert strategy.last_intermediates["creators"] == ["alice", "bob"]


async def test_user_strategy_without_creators_returns_empty() -> None:
    client = _FakeTiktokClient()
    strategy = TiktokUserStrategy(
        client=client,  # type: ignore[arg-type]
        llm_service=_FakeLLMService(payload="{}"),
        creators=(),
        llm_evaluation=False,
    )

    assert await strategy.discover(_profile(), limit=10) == []
    assert client.user_calls == []


def test_strategy_platform_and_names() -> None:
    client = _FakeTiktokClient()
    llm = _FakeLLMService(payload="{}")
    tag = TiktokTagStrategy(client=client, llm_service=llm)  # type: ignore[arg-type]
    user = TiktokUserStrategy(client=client, llm_service=llm)  # type: ignore[arg-type]
    assert (tag.name, tag.source_platform) == ("tiktok_tag", "tiktok")
    assert (user.name, user.source_platform) == ("tiktok_user", "tiktok")


async def test_engine_default_phase_preserves_claimed_tiktok_keyword_and_identity() -> None:
    """The default engine path must consume the claimed word without asking an LLM."""
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine

    client = _FakeTiktokClient()
    llm = _FakeLLMService(payload='{"tags": ["unclaimed"]}')
    engine = ContentDiscoveryEngine(llm_service=llm)
    engine.register_strategy(TiktokTagStrategy(client=client, llm_service=llm))

    rows = await engine.produce_candidates(
        _profile(),
        strategies=["tiktok_tag"],
        limit=2,
        keywords=["science experiments"],
        keyword_ids={"science experiments": 42},
    )

    assert [tag for tag, _ in client.tag_calls] == ["scienceexperiments"]
    assert llm.calls == []
    assert rows
    assert all(row.source_keyword_id == 42 for row in rows)
