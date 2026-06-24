from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.prompts import build_preference_analysis_prompt
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer


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


class BudgetCapturingStructuredService:
    def __init__(self, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.calls: list[dict[str, str]] = []

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
        assert len(system_instruction) + len(user_input) <= self.max_prompt_chars
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )


class ContextOverflowOnceStructuredService:
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
        if "PAIR_ONLY_OVERFLOWS" in user_input and user_input.count("PAIR_ONLY_OVERFLOWS") > 1:
            raise LLMProviderError(
                "openai request failed: HTTP 400: The number of tokens to keep "
                "from the initial prompt is greater than the context length "
                "(n_keep: 135132 >= n_ctx: 36096)"
            )
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )


def test_preference_prompt_treats_comment_feedback_as_direct_neutral_feedback() -> None:
    messages = build_preference_analysis_prompt(events=[], existing_preference={})
    system_prompt = messages[0]["content"]

    assert "metadata.feedback_type 是 comment" in system_prompt
    assert "中性反馈容器" in system_prompt
    assert "直接反馈" in system_prompt
    assert "根据备注" in system_prompt
    assert "喜欢" in system_prompt
    assert "不喜欢" in system_prompt


def test_preference_prompt_explains_cross_platform_signal_strength() -> None:
    messages = build_preference_analysis_prompt(events=[], existing_preference={})
    system_prompt = messages[0]["content"]

    assert "metadata.signal_strength" in system_prompt
    assert "不是最终 interest.weight" in system_prompt
    assert "favorite / bookmark / save / collect" in system_prompt
    assert "follow / subscription" in system_prompt
    assert "view / history" in system_prompt
    assert "hover / scroll / snapshot" in system_prompt
    assert "负向反馈" in system_prompt
    assert "不能被 signal_strength 抵消" in system_prompt


def test_compact_event_for_prompt_preserves_signal_strength() -> None:
    analyzer = PreferenceAnalyzer(registry=ContextOverflowOnceStructuredService())

    compact = analyzer._compact_event_for_prompt(
        {
            "event_type": "view",
            "title": "浏览历史",
            "metadata": {
                "source_platform": "bilibili",
                "signal_strength": 0.35,
                "unused": "drop me",
            },
        }
    )

    assert compact["metadata"] == {
        "signal_strength": 0.35,
        "source_platform": "bilibili",
    }


class ServiceContextOverflowOnceStructuredService(ContextOverflowOnceStructuredService):
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
        if (
            "SERVICE_PAIR_ONLY_OVERFLOWS" in user_input
            and user_input.count("SERVICE_PAIR_ONLY_OVERFLOWS") > 1
        ):
            raise LLMServiceError("structured task failed: prompt is too long for context length")
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )


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


class StubEmbedding:
    def __init__(self, aliases: dict[str, str]) -> None:
        self._aliases = aliases
        self._vectors: dict[str, list[float]] = {}

    async def embed(self, text: str) -> list[float]:
        key = self._aliases.get(text, text)
        if key not in self._vectors:
            axis = len(self._vectors)
            vec = [0.0] * 64
            vec[axis] = 1.0
            self._vectors[key] = vec
        return self._vectors[key]


@pytest.fixture(autouse=True)
def _clear_vocab_vector_cache() -> None:
    from openbiliclaw.soul import taxonomy

    taxonomy._vocab_vectors.clear()


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


def test_merge_preferences_reactivates_matching_archived_interest() -> None:
    analyzer = PreferenceAnalyzer(FakeStructuredService())
    merged = analyzer.merge_preferences(
        existing_preference={
            "interests": [
                {
                    "name": "活跃兴趣",
                    "category": "知识",
                    "weight": 0.8,
                    "first_seen": "2026-01-01T00:00:00",
                    "last_seen": "2026-06-01T00:00:00",
                    "source": "old",
                }
            ],
            "archived_interests": [
                {
                    "name": "归档兴趣",
                    "category": "科技",
                    "weight": 0.2,
                    "first_seen": "2026-02-01T00:00:00",
                    "last_seen": "2026-03-01T00:00:00",
                    "source": "archive",
                },
                {
                    "name": "仍归档兴趣",
                    "category": "生活",
                    "weight": 0.1,
                    "first_seen": "2026-02-01T00:00:00",
                    "last_seen": "2026-03-01T00:00:00",
                    "source": "archive",
                },
            ],
        },
        new_preference={
            "interests": [
                {"name": "归档兴趣", "category": "科技", "weight": 0.7, "source": "new"},
            ],
        },
        now=datetime(2026, 6, 24, 0, 0, 0),
    )

    active_names = {str(item["name"]) for item in merged["interests"]}
    archived_names = {str(item["name"]) for item in merged["archived_interests"]}
    revived = next(item for item in merged["interests"] if item["name"] == "归档兴趣")
    assert "归档兴趣" in active_names
    assert "归档兴趣" not in archived_names
    assert archived_names == {"仍归档兴趣"}
    assert revived["first_seen"] == "2026-02-01T00:00:00"
    assert revived["last_seen"] == "2026-06-24T00:00:00"
    assert revived["weight"] == 0.7


def test_merge_preferences_matches_active_interest_alias() -> None:
    analyzer = PreferenceAnalyzer(FakeStructuredService())
    merged = analyzer.merge_preferences(
        existing_preference={
            "interests": [
                {
                    "name": "AI工程工具链",
                    "category": "科技",
                    "weight": 0.6,
                    "aliases": ["AI工具与技术", "AI工具与工程实践"],
                    "first_seen": "2026-02-01T00:00:00",
                    "last_seen": "2026-03-01T00:00:00",
                    "source": "consolidation",
                }
            ],
        },
        new_preference={
            "interests": [
                {"name": "AI工具与技术", "category": "科技", "weight": 0.8, "source": "new"},
            ],
        },
        now=datetime(2026, 6, 24, 0, 0, 0),
    )

    assert len(merged["interests"]) == 1
    interest = merged["interests"][0]
    assert interest["name"] == "AI工程工具链"
    assert interest["weight"] == 0.8
    assert interest["first_seen"] == "2026-02-01T00:00:00"
    assert interest["last_seen"] == "2026-06-24T00:00:00"
    assert interest["aliases"] == ["AI工具与技术", "AI工具与工程实践"]


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


@pytest.mark.asyncio
async def test_off_vocab_category_clamped_via_embedding_nn() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    service = FakeStructuredService(
        LLMResponse(
            content=json.dumps(
                {"interests": [{"name": "AI 工具", "category": "内容消费方式", "weight": 0.8}]},
                ensure_ascii=False,
            ),
            provider="openai",
        )
    )

    merged = await PreferenceAnalyzer(
        service,
        embedding_service=StubEmbedding({"内容消费方式": "生活"}),
    ).analyze_events(events=[{"event_type": "view", "title": "AI 工具"}], existing_preference={})

    categories = {item["category"] for item in merged["interests"]}
    assert categories <= set(CATEGORY_VOCAB)
    assert categories == {"生活"}


@pytest.mark.asyncio
async def test_off_vocab_category_without_embedding_falls_to_other() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content=json.dumps(
                {"interests": [{"name": "AI 工具", "category": "内容消费方式", "weight": 0.8}]},
                ensure_ascii=False,
            ),
            provider="openai",
        )
    )

    merged = await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": "AI 工具"}],
        existing_preference={},
    )

    assert merged["interests"][0]["category"] == "其他"


@pytest.mark.asyncio
async def test_in_vocab_category_passthrough_unchanged() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content=json.dumps(
                {"interests": [{"name": "AI 工具", "category": "科技", "weight": 0.8}]},
                ensure_ascii=False,
            ),
            provider="openai",
        )
    )

    merged = await PreferenceAnalyzer(
        service,
        embedding_service=StubEmbedding({"科技": "生活"}),
    ).analyze_events(events=[{"event_type": "view", "title": "AI 工具"}], existing_preference={})

    assert merged["interests"][0]["category"] == "科技"


@pytest.mark.asyncio
async def test_clamp_collapses_variants_onto_same_merge_key() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content=json.dumps(
                {"interests": [{"name": "Python", "category": "技术", "weight": 0.8}]},
                ensure_ascii=False,
            ),
            provider="openai",
        )
    )

    merged = await PreferenceAnalyzer(
        service,
        embedding_service=StubEmbedding({"技术": "科技"}),
    ).analyze_events(
        events=[{"event_type": "view", "title": "Python"}],
        existing_preference={
            "interests": [
                {
                    "name": "Python",
                    "category": "科技",
                    "weight": 0.5,
                    "last_seen": datetime.now().isoformat(),
                }
            ]
        },
    )

    python_tags = [item for item in merged["interests"] if item["name"] == "Python"]
    assert len(python_tags) == 1
    assert python_tags[0]["category"] == "科技"
    assert python_tags[0]["weight"] == 0.8


@pytest.mark.asyncio
async def test_speculative_interests_clamped_too() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(
        LLMResponse(
            content=json.dumps(
                {
                    "speculative_interests": [
                        {"name": "低负担生活工具", "category": "内容消费方式", "weight": 0.4}
                    ]
                },
                ensure_ascii=False,
            ),
            provider="openai",
        )
    )

    merged = await PreferenceAnalyzer(
        service,
        embedding_service=StubEmbedding({"内容消费方式": "生活"}),
    ).analyze_events(events=[{"event_type": "view", "title": "轻工具"}], existing_preference={})

    assert merged["speculative_interests"][0]["category"] == "生活"


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
async def test_analyze_events_count_chunking_avoids_whole_batch_prompt_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.soul import preference_analyzer as analyzer_module
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    original_build_prompt = analyzer_module.build_preference_analysis_prompt

    def build_prompt_rejecting_whole_batch(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> list[dict[str, str]]:
        if len(events) > 1:
            raise AssertionError("count-based chunking must not build a whole-batch prompt")
        return original_build_prompt(events=events, existing_preference=existing_preference)

    monkeypatch.setattr(
        analyzer_module,
        "build_preference_analysis_prompt",
        build_prompt_rejecting_whole_batch,
    )
    service = FakeStructuredService(
        LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )
    )

    await PreferenceAnalyzer(service).analyze_events(
        events=[
            {"event_type": "view", "title": "事件 1"},
            {"event_type": "view", "title": "事件 2"},
        ],
        existing_preference={},
        event_chunk_size=1,
    )

    assert len(service.calls) == 2


@pytest.mark.asyncio
async def test_chunked_analysis_splits_by_prompt_budget_before_llm_call() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 1800
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    events = [
        {
            "event_type": "view",
            "title": f"长事件 {idx}",
            "context": "这是一段偏好上下文" * 80,
            "metadata": {"source_platform": "bilibili", "bvid": f"BV{idx}"},
        }
        for idx in range(4)
    ]

    preference = await analyzer.analyze_events(
        events=events,
        existing_preference={},
        event_chunk_size=4,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) > 1
    assert all(
        len(call["system_instruction"]) + len(call["user_input"]) <= budget
        for call in service.calls
    )


@pytest.mark.asyncio
async def test_analyze_events_splits_by_prompt_budget_without_explicit_chunk_size() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 1800
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    events = [
        {
            "event_type": "view",
            "title": f"自动分片 {idx}",
            "context": "这是一段偏好上下文" * 80,
            "metadata": {"source_platform": "bilibili", "bvid": f"BV_AUTO_{idx}"},
        }
        for idx in range(4)
    ]

    await analyzer.analyze_events(events=events, existing_preference={})

    assert len(service.calls) > 1
    assert all(
        len(call["system_instruction"]) + len(call["user_input"]) <= budget
        for call in service.calls
    )


@pytest.mark.asyncio
async def test_single_oversized_preference_event_is_compacted_before_llm_call() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 2200
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    await analyzer.analyze_events(
        events=[
            {
                "event_type": "feedback",
                "title": "很长但重要的标题" + "x" * 2000,
                "context": "用户明确点踩了这条内容。" + "y" * 20_000,
                "inferred_satisfaction": "negative",
                "satisfaction_reason": "explicit_negative",
                "metadata": {
                    "source_platform": "bilibili",
                    "up_name": "测试UP",
                    "bvid": "BV_LONG",
                    "feedback_type": "dislike",
                    "raw_context": "z" * 50_000,
                },
            }
        ],
        existing_preference={},
    )

    assert len(service.calls) == 1
    user_input = service.calls[0]["user_input"]
    assert "测试UP" in user_input
    assert "BV_LONG" in user_input
    assert "feedback_type" in user_input
    assert "raw_context" not in user_input
    assert "z" * 1000 not in user_input


@pytest.mark.asyncio
async def test_single_event_is_skipped_when_compact_prompt_still_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul import preference_analyzer as analyzer_module
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    compact_prompt_events: list[dict[str, object]] = []
    original_build_prompt = analyzer_module.build_preference_analysis_prompt

    def capture_prompt_events(
        *,
        events: list[dict[str, object]],
        existing_preference: dict[str, object],
    ) -> list[dict[str, str]]:
        if len(events) == 1 and events[0].get("title"):
            compact_prompt_events.append(dict(events[0]))
        return original_build_prompt(events=events, existing_preference=existing_preference)

    monkeypatch.setattr(
        analyzer_module,
        "build_preference_analysis_prompt",
        capture_prompt_events,
    )

    base_messages = build_preference_analysis_prompt(events=[], existing_preference={})
    budget = len(base_messages[0]["content"]) + 20
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    preference = await analyzer.analyze_events(
        events=[
            {
                "event_type": "view",
                "title": "too large",
                "context": "x" * 10_000,
                "raw_context": "y" * 10_000,
                "payload": {"comments": "z" * 10_000},
            }
        ],
        existing_preference={},
    )

    assert service.calls == []
    assert preference["source_platform_mix"] == {"bilibili": 1.0}
    compact_event = compact_prompt_events[-1]
    assert "raw_context" not in compact_event
    assert "payload" not in compact_event


@pytest.mark.asyncio
async def test_provider_context_overflow_splits_chunk_and_retries() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = ContextOverflowOnceStructuredService()
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=0)

    preference = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "PAIR_ONLY_OVERFLOWS A"},
            {"event_type": "view", "title": "PAIR_ONLY_OVERFLOWS B"},
        ],
        existing_preference={},
        event_chunk_size=2,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_service_context_overflow_splits_chunk_and_retries() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = ServiceContextOverflowOnceStructuredService()
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=0)

    preference = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "SERVICE_PAIR_ONLY_OVERFLOWS A"},
            {"event_type": "view", "title": "SERVICE_PAIR_ONLY_OVERFLOWS B"},
        ],
        existing_preference={},
        event_chunk_size=2,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) == 3


@pytest.mark.asyncio
async def test_non_context_provider_error_still_aborts_chunked_analysis() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(
        FakeErrorStructuredService(LLMProviderError("provider down")),
        max_prompt_chars=0,
    )

    with pytest.raises(PreferenceAnalysisError, match="provider down"):
        await analyzer.analyze_events(
            events=[
                {"event_type": "view", "title": "x"},
                {"event_type": "view", "title": "y"},
                {"event_type": "view", "title": "z"},
            ],
            existing_preference={},
            event_chunk_size=2,
        )


@pytest.mark.asyncio
async def test_non_context_service_error_still_aborts_chunked_analysis() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(
        FakeErrorStructuredService(LLMServiceError("service unavailable")),
        max_prompt_chars=0,
    )

    with pytest.raises(PreferenceAnalysisError, match="service unavailable"):
        await analyzer.analyze_events(
            events=[
                {"event_type": "view", "title": "x"},
                {"event_type": "view", "title": "y"},
                {"event_type": "view", "title": "z"},
            ],
            existing_preference={},
            event_chunk_size=2,
        )


@pytest.mark.asyncio
async def test_analyze_events_passes_unfiltered_when_satisfaction_flag_off() -> None:
    """Default behavior (flag off): every event the caller passes shows up
    verbatim in the LLM user prompt, including quick-exit / negative rows."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(LLMResponse(content="{}", provider="openai"))
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
async def test_analyze_events_default_drops_quick_exit_but_keeps_explicit_dislike() -> None:
    """Default path should drop accidental quick exits but retain explicit dislikes.

    Explicit dislike feedback is negative evidence, not positive interest
    evidence. It must remain available so the LLM can update disliked_topics.
    """
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(LLMResponse(content="{}", provider="openai"))
    analyzer = PreferenceAnalyzer(service)
    events = [
        {"event_type": "click", "title": "好内容", "inferred_satisfaction": "positive"},
        {"event_type": "search", "title": "搜索线索", "inferred_satisfaction": "neutral"},
        {
            "event_type": "click",
            "title": "标题党",
            "inferred_satisfaction": "negative",
            "satisfaction_reason": "quick_exit",
        },
        {
            "event_type": "feedback",
            "title": "低质混剪",
            "inferred_satisfaction": "negative",
            "satisfaction_reason": "explicit_negative",
            "metadata": {"feedback_type": "dislike"},
        },
        {
            "event_type": "feedback",
            "title": "没写 reason 的点踩",
            "inferred_satisfaction": "negative",
            "metadata": {"reaction": "thumbs_down"},
        },
        {"event_type": "click", "title": "未知", "inferred_satisfaction": None},
    ]
    await analyzer.analyze_events(events=events, existing_preference={})
    user_input = service.calls[0]["user_input"]
    system_instruction = service.calls[0]["system_instruction"]
    assert "好内容" in user_input
    assert "搜索线索" in user_input, "neutral rows remain useful context"
    assert "未知" in user_input, "unknown / null rows must be kept by the positive+unknown filter"
    assert "标题党" not in user_input, "quick-exit rows must be filtered out"
    assert "低质混剪" in user_input, "explicit dislikes must remain dislike evidence"
    assert "没写 reason 的点踩" in user_input, "metadata-level explicit dislikes must be kept"
    assert "不要把负向事件提取为 interests" in str(system_instruction)
