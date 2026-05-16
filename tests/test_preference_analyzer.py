from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponse


class FakeRegistry:
    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[list[dict[str, str]]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")


class FakeStructuredService:
    def __init__(self, response: LLMResponse | None = None) -> None:
        self.response = response or LLMResponse(content="{}", provider="openai")
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
        return self.response


class FakeErrorStructuredService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        raise self.error


class RejectingChunkStructuredService:
    """Reject prompts containing BAD, return a minimal preference otherwise."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append(user_input)
        if "BAD" in user_input:
            return LLMResponse(
                content="The request was rejected because it was considered high risk",
                provider="openai",
            )
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )


@pytest.mark.asyncio
async def test_analyze_events_parses_structured_preference_output() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content="""
            {
              "interests": [
                {"name": "历史", "category": "知识", "weight": 1.2, "source": "history videos"},
                {"name": "纪录片", "category": "影视", "weight": 0.72, "source": "watch history"}
              ],
              "style": {"preferred_duration": "long", "depth_preference": 0.91},
              "context": {"session_type": "deep_dive"},
              "exploration_openness": 0.66,
              "disliked_topics": ["低质标题党"],
              "favorite_up_users": ["小约翰可汗"]
            }
            """,
            provider="openai",
        )
    )
    analyzer = PreferenceAnalyzer(service)

    preference = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "一战史解说", "metadata": {"bvid": "BV1"}},
            {"event_type": "view", "title": "长篇纪录片", "metadata": {"bvid": "BV2"}},
        ],
        existing_preference={},
    )

    assert "output_schema" in service.calls[0]["system_instruction"]
    assert preference["interests"][0]["name"] == "历史"
    assert preference["interests"][0]["weight"] == 1.0
    assert preference["style"]["preferred_duration"] == "long"
    assert preference["favorite_up_users"] == ["小约翰可汗"]


@pytest.mark.asyncio
async def test_invalid_json_response_raises_preference_analysis_error() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(
        FakeStructuredService(LLMResponse(content="not-json", provider="openai"))
    )

    with pytest.raises(PreferenceAnalysisError):
        await analyzer.analyze_events(
            events=[{"event_type": "view", "title": "x"}],
            existing_preference={},
        )


def test_merge_preferences_applies_decay_and_deduplicates_tags() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    merged = analyzer.merge_preferences(
        existing_preference={
            "interests": [
                {
                    "name": "历史",
                    "category": "知识",
                    "weight": 0.8,
                    "first_seen": "2026-02-01T00:00:00",
                    "last_seen": (datetime.now() - timedelta(days=14)).isoformat(),
                    "source": "old",
                }
            ],
            "favorite_up_users": ["旧UP"],
        },
        new_preference={
            "interests": [
                {"name": "历史", "category": "知识", "weight": 0.7, "source": "new"},
                {"name": "纪录片", "category": "影视", "weight": 0.6, "source": "new"},
            ],
            "favorite_up_users": ["旧UP", "新UP"],
        },
        now=datetime.now(),
    )

    assert len(merged["interests"]) == 2
    history_tag = next(item for item in merged["interests"] if item["name"] == "历史")
    assert 0.7 <= history_tag["weight"] <= 1.0
    assert history_tag["first_seen"] == "2026-02-01T00:00:00"
    assert set(merged["favorite_up_users"]) == {"旧UP", "新UP"}


@pytest.mark.asyncio
async def test_provider_error_is_wrapped() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(FakeErrorStructuredService(LLMProviderError("provider down")))

    with pytest.raises(PreferenceAnalysisError):
        await analyzer.analyze_events(
            events=[{"event_type": "view", "title": "x"}],
            existing_preference={},
        )


@pytest.mark.asyncio
async def test_preference_analyzer_can_use_unified_service() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )
    )

    preference = await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": "AI 视频"}],
        existing_preference={},
    )

    assert preference["interests"][0]["name"] == "科技"
    assert service.calls


def test_preference_analyzer_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    with pytest.raises(TypeError, match="complete_structured_task"):
        PreferenceAnalyzer(FakeRegistry())


def test_compute_source_platform_mix_counts_events_per_source() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    mix = analyzer.compute_source_platform_mix(
        [
            {"metadata": {"source_platform": "bilibili"}},
            {"metadata": {"source_platform": "bilibili"}},
            {"metadata": {"source_platform": "xiaohongshu"}},
            # Events missing source_platform are attributed to bilibili for
            # back-compat with records written before multi-source support.
            {"metadata": {}},
        ]
    )
    assert mix == {"bilibili": 0.75, "xiaohongshu": 0.25}


def test_compute_source_platform_mix_returns_empty_when_no_events() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    assert analyzer.compute_source_platform_mix([]) == {}


def test_merge_source_mix_ema_blends_prior_and_batch() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    blended = analyzer._merge_source_mix(
        {"bilibili": 1.0},
        {"xiaohongshu": 1.0},
    )
    # alpha=0.3 by default → prior bilibili keeps 0.7 weight, new xhs gets 0.3.
    assert blended == {"bilibili": 0.7, "xiaohongshu": 0.3}


def test_merge_source_mix_keeps_prior_when_batch_empty() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    assert analyzer._merge_source_mix(
        {"bilibili": 0.6, "xiaohongshu": 0.4},
        {},
    ) == {"bilibili": 0.6, "xiaohongshu": 0.4}


@pytest.mark.asyncio
async def test_analyze_events_populates_source_platform_mix() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )
    )
    preference = await PreferenceAnalyzer(service).analyze_events(
        events=[
            {"event_type": "view", "title": "A", "metadata": {"source_platform": "bilibili"}},
            {"event_type": "view", "title": "B", "metadata": {"source_platform": "xiaohongshu"}},
        ],
        existing_preference={},
    )
    assert preference["source_platform_mix"] == {"bilibili": 0.5, "xiaohongshu": 0.5}


@pytest.mark.asyncio
async def test_chunked_analysis_splits_and_skips_rejected_single_event() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = RejectingChunkStructuredService()
    preference = await PreferenceAnalyzer(service).analyze_events(
        events=[
            {"event_type": "view", "title": "GOOD 1", "metadata": {"source_platform": "bilibili"}},
            {"event_type": "view", "title": "BAD", "metadata": {"source_platform": "douyin"}},
            {
                "event_type": "favorite",
                "title": "GOOD 2",
                "metadata": {"source_platform": "xiaohongshu"},
            },
            {"event_type": "like", "title": "GOOD 3", "metadata": {"source_platform": "bilibili"}},
        ],
        existing_preference={},
        event_chunk_size=2,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert preference["source_platform_mix"] == {
        "bilibili": 0.5,
        "douyin": 0.25,
        "xiaohongshu": 0.25,
    }
    assert len(service.calls) > 1


@pytest.mark.asyncio
async def test_analyze_events_passes_unfiltered_when_satisfaction_flag_off() -> None:
    """Default behavior (flag off): every event the caller passes shows up
    verbatim in the LLM user prompt, including quick-exit / negative rows."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(content="{}", provider="openai")
    )
    analyzer = PreferenceAnalyzer(service, satisfaction_filter_enabled=False)
    events = [
        {"event_type": "click", "title": "好内容", "inferred_satisfaction": "positive"},
        {"event_type": "click", "title": "标题党", "inferred_satisfaction": "negative"},
    ]
    await analyzer.analyze_events(events=events, existing_preference={})
    user_input = service.calls[0]["user_input"]
    assert "好内容" in user_input
    assert "标题党" in user_input, "flag-off path must include negatives"


@pytest.mark.asyncio
async def test_analyze_events_drops_negative_events_when_satisfaction_flag_on() -> None:
    """Flag-on path drops quick-exit / explicit-negative events from the prompt."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(content="{}", provider="openai")
    )
    analyzer = PreferenceAnalyzer(service, satisfaction_filter_enabled=True)
    events = [
        {"event_type": "click", "title": "好内容", "inferred_satisfaction": "positive"},
        {"event_type": "click", "title": "标题党", "inferred_satisfaction": "negative"},
        {"event_type": "click", "title": "未知", "inferred_satisfaction": None},
    ]
    await analyzer.analyze_events(events=events, existing_preference={})
    user_input = service.calls[0]["user_input"]
    assert "好内容" in user_input
    assert "未知" in user_input, "unknown / null rows must be kept by the positive+unknown filter"
    assert "标题党" not in user_input, "negative rows must be filtered out"
