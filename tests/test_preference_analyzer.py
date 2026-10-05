from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponse
from openbiliclaw.llm.prompts import build_preference_analysis_prompt
from openbiliclaw.llm.service import LLMServiceError
from openbiliclaw.soul.preference_analyzer import (
    DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE,
    MAX_CONCURRENT_PREFERENCE_CHUNKS,
    PREFERENCE_CHUNK_MAX_TOKENS,
    PREFERENCE_REASONING_FALLBACK_MAX_TOKENS,
    PreferenceAnalysisError,
    PreferenceAnalyzer,
)


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


class CacheFlagStructuredService:
    def __init__(self) -> None:
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
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "inject_core_memory": inject_core_memory,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            }
        )
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="openai",
        )


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


class SequenceStructuredService:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = list(responses)
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
        inject_core_memory: bool = True,
    ) -> LLMResponse:
        del history, temperature, max_tokens, caller, inject_core_memory
        self.calls.append({"system_instruction": system_instruction, "user_input": user_input})
        payload = self._responses.pop(0) if self._responses else {}
        return LLMResponse(content=json.dumps(payload, ensure_ascii=False), provider="openai")


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


def test_preference_chunk_defaults_bound_initial_batch_events() -> None:
    assert DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE == 200
    assert MAX_CONCURRENT_PREFERENCE_CHUNKS == 16
    assert DEFAULT_PREFERENCE_EVENT_CHUNK_SIZE * MAX_CONCURRENT_PREFERENCE_CHUNKS == 3200


@pytest.mark.asyncio
async def test_chunked_preference_analysis_disables_core_memory_injection() -> None:
    service = CacheFlagStructuredService()
    analyzer = PreferenceAnalyzer(service)

    await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "AI 进展", "metadata": {"up_name": "甲"}},
            {"event_type": "like", "title": "工程实践", "metadata": {"up_name": "乙"}},
        ],
        existing_preference={"interests": [{"name": "旧兴趣", "category": "知识"}]},
        event_chunk_size=1,
    )

    assert len(service.calls) == 2
    assert [call["inject_core_memory"] for call in service.calls] == [False, False]
    assert [call["max_tokens"] for call in service.calls] == [
        PREFERENCE_CHUNK_MAX_TOKENS,
        PREFERENCE_CHUNK_MAX_TOKENS,
    ]
    assert [call["reasoning_effort"] for call in service.calls] == [None, None]


@pytest.mark.asyncio
async def test_chunked_preference_analysis_merges_init_cognition_context() -> None:
    service = SequenceStructuredService(
        [
            {
                "interests": [{"name": "AI 工具链", "category": "科技", "weight": 0.8}],
                "awareness_candidates": [
                    {
                        "observation": "连续停留在高信息密度的工具链内容上。",
                        "trend": "从泛泛浏览转向验证具体工作流。",
                        "emotion_guess": "带着掌控感需求的好奇。",
                    }
                ],
                "insight_candidates": [
                    {
                        "hypothesis": "用户可能在通过工具链内容寻找可落地的长期产出方式。",
                        "evidence": ["多条 AI 工具链观看信号"],
                        "confidence": 0.72,
                    }
                ],
            },
            {
                "interests": [{"name": "长期项目复盘", "category": "知识", "weight": 0.7}],
                "awareness_candidates": [
                    {
                        "observation": "连续停留在高信息密度的工具链内容上。",
                        "trend": "重复候选应被去重。",
                    },
                    {
                        "observation": "对长期项目拆解内容也有稳定正反馈。",
                        "trend": "兴趣从工具扩展到执行节奏。",
                    },
                ],
                "insight_candidates": [
                    {
                        "hypothesis": "用户可能在通过工具链内容寻找可落地的长期产出方式。",
                        "evidence": ["重复候选应被去重"],
                        "confidence": 0.6,
                    },
                    {
                        "hypothesis": "用户不只追新工具，更在意工具能否支撑长期推进。",
                        "evidence": ["长期项目复盘内容也被保留"],
                        "confidence": 0.66,
                    },
                ],
            },
        ]
    )
    analyzer = PreferenceAnalyzer(service)

    updated = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "AI 工具链实战"},
            {"event_type": "favorite", "title": "长期项目复盘"},
        ],
        existing_preference={},
        event_chunk_size=1,
    )

    context = updated["_init_cognition_context"]
    assert [item["observation"] for item in context["awareness"]] == [
        "连续停留在高信息密度的工具链内容上。",
        "对长期项目拆解内容也有稳定正反馈。",
    ]
    assert [item["hypothesis"] for item in context["insights"]] == [
        "用户可能在通过工具链内容寻找可落地的长期产出方式。",
        "用户不只追新工具，更在意工具能否支撑长期推进。",
    ]
    assert context["insights"][0]["confidence"] == 0.72


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


def test_compact_event_for_prompt_preserves_comment_text_and_kind() -> None:
    analyzer = PreferenceAnalyzer(registry=ContextOverflowOnceStructuredService())

    compact = analyzer._compact_event_for_prompt(
        {
            "event_type": "comment",
            "title": "一个视频",
            "metadata": {
                "source_platform": "bilibili",
                "comment_text": "写得真好",
                "comment_kind": "danmaku",
                "unused": "drop me",
            },
        }
    )

    assert compact["metadata"] == {
        "source_platform": "bilibili",
        "comment_text": "写得真好",
        "comment_kind": "danmaku",
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


class RejectingContextStructuredService:
    """Reject long/context-heavy prompts, accept title-only safe prompts."""

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
        if "FORBIDDEN_CONTEXT" in user_input:
            return LLMResponse(content="你好，我无法给到相关内容。", provider="deepseek")
        return LLMResponse(
            content='{"interests": [{"name": "AI Agent", "category": "科技", "weight": 0.9}]}',
            provider="deepseek",
        )


class ContentRiskChunkStructuredService:
    """Raise a DeepSeek-style content-moderation error for prompts containing RISKY."""

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
        if "RISKY" in user_input:
            raise LLMServiceError(
                "All providers failed (deepseek). Last error: deepseek request failed: "
                'HTTP 400: {"code": "invalid_request_error", '
                '"message": "Content Exists Risk", "param": null, '
                '"type": "invalid_request_error"}'
            )
        return LLMResponse(
            content='{"interests": [{"name": "科技", "category": "知识", "weight": 0.7}]}',
            provider="deepseek",
        )


class ConcurrentChunkStructuredService:
    def __init__(self, *, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.active_calls = 0
        self.max_active_calls = 0
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
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(self.delay_seconds)
        finally:
            self.active_calls -= 1
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


def test_merge_preferences_decay_is_independent_of_processing_frequency() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())
    start = datetime(2026, 9, 1, 0, 0, 0)
    preference: dict[str, object] = {
        "interests": [
            {
                "name": "滑雪",
                "category": "体育",
                "weight": 1.0,
                "first_seen": start.isoformat(),
                "last_seen": start.isoformat(),
                "source": "old",
            }
        ]
    }

    once = analyzer.merge_preferences(
        preference,
        {"interests": []},
        now=start + timedelta(days=7),
    )
    twice = analyzer.merge_preferences(
        once,
        {"interests": []},
        now=start + timedelta(days=7),
    )

    assert once["interests"][0]["weight"] == pytest.approx(0.9)
    assert twice["interests"][0]["weight"] == pytest.approx(0.9)

    daily = preference
    for day in range(1, 8):
        daily = analyzer.merge_preferences(
            daily,
            {"interests": []},
            now=start + timedelta(days=day),
        )
    assert daily["interests"][0]["weight"] == pytest.approx(0.9)


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
async def test_chunked_analysis_skips_deepseek_content_risk_events() -> None:
    """Provider-raised Content Exists Risk must split/skip, not abort the batch."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = ContentRiskChunkStructuredService()
    preference = await PreferenceAnalyzer(service).analyze_events(
        events=[
            {"event_type": "view", "title": "GOOD 1", "metadata": {"source_platform": "bilibili"}},
            {"event_type": "view", "title": "RISKY BAD", "metadata": {"source_platform": "douyin"}},
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
    assert len(service.calls) > 2


@pytest.mark.asyncio
async def test_single_batch_content_risk_falls_back_to_chunked_isolation() -> None:
    """A moderation refusal on the unchunked path should split, not abort."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = ContentRiskChunkStructuredService()
    preference = await PreferenceAnalyzer(service).analyze_events(
        events=[
            {"event_type": "view", "title": "GOOD 1", "metadata": {"source_platform": "bilibili"}},
            {"event_type": "view", "title": "RISKY BAD", "metadata": {"source_platform": "douyin"}},
            {"event_type": "like", "title": "GOOD 2", "metadata": {"source_platform": "bilibili"}},
        ],
        existing_preference={},
    )

    assert preference["interests"][0]["name"] == "科技"
    assert preference["source_platform_mix"] == {
        "bilibili": 2 / 3,
        "douyin": 1 / 3,
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
        awareness_notes: list[dict[str, object]] | None = None,
        active_insights: list[dict[str, object]] | None = None,
        input_view: str = "legacy",
    ) -> list[dict[str, str]]:
        if len(events) > 1:
            raise AssertionError("count-based chunking must not build a whole-batch prompt")
        return original_build_prompt(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
            input_view=input_view,
        )

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
async def test_chunked_analysis_batches_initial_chunk_fanout() -> None:
    service = ConcurrentChunkStructuredService()
    events = [
        {"event_type": "view", "title": f"事件 {idx}", "metadata": {"source_platform": "bilibili"}}
        for idx in range(20)
    ]

    preference = await PreferenceAnalyzer(service).analyze_events(
        events=events,
        existing_preference={},
        event_chunk_size=1,
    )

    assert preference["interests"][0]["name"] == "科技"
    assert len(service.calls) == 20
    assert service.max_active_calls <= 16


@pytest.mark.asyncio
async def test_chunked_analysis_respects_configured_llm_concurrency() -> None:
    service = ConcurrentChunkStructuredService()
    service.concurrency = 2

    await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": f"事件 {idx}"} for idx in range(8)],
        existing_preference={},
        event_chunk_size=1,
    )

    assert service.max_active_calls == 2


@pytest.mark.asyncio
async def test_chunked_analysis_recursive_recovery_does_not_expand_fanout() -> None:
    class SplitUntilSingleService(FakeStructuredService):
        concurrency = 2

        def __init__(self) -> None:
            super().__init__()
            self.active_calls = 0
            self.max_active_calls = 0

        async def complete_structured_task(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
            try:
                await asyncio.sleep(0.005)
                if str(kwargs["user_input"]).count('"event_type"') > 1:
                    return LLMResponse(content="not json", provider="openai")
                return LLMResponse(content='{"interests": []}', provider="openai")
            finally:
                self.active_calls -= 1

    service = SplitUntilSingleService()

    await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": f"事件 {idx}"} for idx in range(8)],
        existing_preference={},
        event_chunk_size=4,
    )

    assert service.max_active_calls == 2
    assert len(service.calls) == 14


@pytest.mark.asyncio
async def test_chunked_analysis_retries_transient_rate_limit(monkeypatch) -> None:
    from openbiliclaw.soul import preference_analyzer as module

    class RateLimitedOnceService(FakeStructuredService):
        concurrency = 1

        async def complete_structured_task(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LLMServiceError("openai_compatible rate limit exceeded")
            return LLMResponse(content='{"interests": []}', provider="openai")

    monkeypatch.setattr(module, "PREFERENCE_RATE_LIMIT_RETRY_SECONDS", 0)
    service = RateLimitedOnceService()

    await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": "事件"}],
        existing_preference={},
        event_chunk_size=1,
    )

    assert len(service.calls) == 2


@pytest.mark.asyncio
async def test_chunked_analysis_does_not_retry_exhausted_balance(monkeypatch) -> None:
    from openbiliclaw.soul import preference_analyzer as module

    class ExhaustedBalanceService(FakeStructuredService):
        concurrency = 1

        async def complete_structured_task(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            raise LLMServiceError("HTTP 402: insufficient balance")

    monkeypatch.setattr(module, "PREFERENCE_RATE_LIMIT_RETRY_SECONDS", 0)
    service = ExhaustedBalanceService()

    with pytest.raises(PreferenceAnalysisError, match="insufficient balance"):
        await PreferenceAnalyzer(service).analyze_events(
            events=[{"event_type": "view", "title": "事件"}],
            existing_preference={},
            event_chunk_size=1,
        )

    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_chunked_analysis_retries_reasoning_only_length_once_with_larger_budget() -> None:
    class ReasoningExhaustedOnceService(FakeStructuredService):
        concurrency = 1

        def __init__(self) -> None:
            super().__init__()
            self.max_token_calls: list[int] = []

        async def complete_structured_task(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            self.max_token_calls.append(int(kwargs["max_tokens"]))
            if len(self.calls) == 1:
                raise LLMServiceError(
                    "All providers failed. Last error: openai_compatible returned reasoning "
                    "but no final content (finish_reason=length); disable thinking/reasoning "
                    "or increase max_tokens"
                )
            return LLMResponse(content='{"interests": []}', provider="openai")

    service = ReasoningExhaustedOnceService()

    await PreferenceAnalyzer(service).analyze_events(
        events=[{"event_type": "view", "title": "事件"}],
        existing_preference={},
        event_chunk_size=1,
    )

    assert service.max_token_calls == [
        PREFERENCE_CHUNK_MAX_TOKENS,
        PREFERENCE_REASONING_FALLBACK_MAX_TOKENS,
    ]


@pytest.mark.asyncio
async def test_chunked_analysis_cancels_and_drains_sibling_after_hard_failure() -> None:
    class FailAndBlockService(FakeStructuredService):
        concurrency = 2

        def __init__(self) -> None:
            super().__init__()
            self.active_calls = 0
            self.both_started = asyncio.Event()
            self.sibling_cancelled = False

        async def complete_structured_task(self, **kwargs) -> LLMResponse:
            self.calls.append(kwargs)
            self.active_calls += 1
            if self.active_calls == 2:
                self.both_started.set()
            try:
                if "失败事件" in str(kwargs["user_input"]):
                    await self.both_started.wait()
                    raise LLMServiceError("hard provider failure")
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.sibling_cancelled = True
                    raise
            finally:
                self.active_calls -= 1

    service = FailAndBlockService()

    with pytest.raises(PreferenceAnalysisError, match="hard provider failure"):
        await PreferenceAnalyzer(service).analyze_events(
            events=[
                {"event_type": "view", "title": "失败事件"},
                {"event_type": "view", "title": "等待事件"},
            ],
            existing_preference={},
            event_chunk_size=1,
        )

    assert service.sibling_cancelled is True
    assert service.active_calls == 0


@pytest.mark.asyncio
async def test_chunked_analysis_logs_per_chunk_lifecycle(caplog) -> None:
    """Each chunk logs an indexed start + done line so ``openbiliclaw.log``
    pinpoints which chunk is in flight (a started-without-done line is the one
    that stalled or was cancelled by the init timeout)."""
    import logging

    service = ConcurrentChunkStructuredService()
    events = [
        {"event_type": "view", "title": f"事件 {idx}", "metadata": {"source_platform": "bilibili"}}
        for idx in range(3)
    ]

    with caplog.at_level(logging.INFO, logger="openbiliclaw.soul.preference_analyzer"):
        await PreferenceAnalyzer(service).analyze_events(
            events=events,
            existing_preference={},
            event_chunk_size=1,
        )

    messages = [rec.getMessage() for rec in caplog.records]
    for idx in range(1, 4):
        assert any(f"preference chunk {idx}/3 started" in m for m in messages), idx
        assert any(f"preference chunk {idx}/3 done" in m for m in messages), idx


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
async def test_automatic_budget_uses_independent_request_shape_without_false_split() -> None:
    events = [
        {
            "event_type": "view",
            "title": f"完整保留事件 {idx}",
            "metadata": {"source_platform": "bilibili", "bvid": f"BV_PACK_{idx}"},
        }
        for idx in range(4)
    ]
    independent_messages = build_preference_analysis_prompt(
        events=events,
        existing_preference={},
    )
    budget = sum(len(message["content"]) for message in independent_messages)
    oversized_existing = {"legacy_padding": "旧偏好上下文" * 2_000}
    whole_messages = build_preference_analysis_prompt(
        events=events,
        existing_preference=oversized_existing,
    )
    assert sum(len(message["content"]) for message in whole_messages) > budget

    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)
    await analyzer.analyze_events(
        events=events,
        existing_preference=oversized_existing,
    )

    assert len(service.calls) == 1
    user_input = service.calls[0]["user_input"]
    assert all(event["title"] in user_input for event in events)


def test_automatic_budget_finds_largest_fitting_event_prefix() -> None:
    events = [
        {
            "event_type": "view",
            "title": f"装箱事件 {idx}",
            "context": "不同长度上下文" * (idx + 1) * 10,
            "metadata": {"source_platform": "bilibili"},
        }
        for idx in range(4)
    ]
    two_event_messages = build_preference_analysis_prompt(
        events=events[:2],
        existing_preference={},
    )
    budget = sum(len(message["content"]) for message in two_event_messages)
    three_event_messages = build_preference_analysis_prompt(
        events=events[:3],
        existing_preference={},
    )
    assert sum(len(message["content"]) for message in three_event_messages) > budget

    analyzer = PreferenceAnalyzer(FakeStructuredService(), max_prompt_chars=budget)

    assert (
        analyzer._largest_fitting_independent_chunk_size(
            events=events,
            awareness_notes=None,
            active_insights=None,
        )
        == 2
    )


@pytest.mark.asyncio
async def test_automatic_budget_repacks_after_skewed_oversized_event() -> None:
    small_events = [
        {
            "event_type": "view",
            "title": f"小事件 {idx}",
            "context": "小段上下文" * 60,
            "metadata": {"source_platform": "bilibili", "bvid": f"BV_SMALL_{idx}"},
        }
        for idx in range(4)
    ]
    events = [
        *small_events[:2],
        {
            "event_type": "view",
            "title": "局部超长事件",
            "context": "超长上下文" * 5_000,
            "metadata": {"source_platform": "bilibili", "bvid": "BV_LARGE"},
        },
        *small_events[2:],
    ]
    two_small_messages = build_preference_analysis_prompt(
        events=small_events[:2],
        existing_preference={},
    )
    budget = sum(len(message["content"]) for message in two_small_messages)
    service = BudgetCapturingStructuredService(max_prompt_chars=budget)
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=budget)

    await analyzer.analyze_events(events=events, existing_preference={})

    assert len(service.calls) == 3
    assert all(
        len(call["system_instruction"]) + len(call["user_input"]) <= budget
        for call in service.calls
    )
    assert all(event["title"] in service.calls[0]["user_input"] for event in small_events[:2])
    assert "局部超长事件" in service.calls[1]["user_input"]
    assert all(event["title"] in service.calls[2]["user_input"] for event in small_events[2:])


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
        awareness_notes: list[dict[str, object]] | None = None,
        active_insights: list[dict[str, object]] | None = None,
        input_view: str = "legacy",
    ) -> list[dict[str, str]]:
        if len(events) == 1 and events[0].get("title"):
            compact_prompt_events.append(dict(events[0]))
        return original_build_prompt(
            events=events,
            existing_preference=existing_preference,
            awareness_notes=awareness_notes,
            active_insights=active_insights,
            input_view=input_view,
        )

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
async def test_invalid_json_single_event_retries_with_safe_compact_prompt() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = RejectingContextStructuredService()
    analyzer = PreferenceAnalyzer(service, max_prompt_chars=0)

    preference = await analyzer.analyze_events(
        events=[
            {
                "event_type": "view",
                "title": "如何评价新的 AI Agent 编程框架？",
                "context": "FORBIDDEN_CONTEXT " * 50,
                "metadata": {
                    "source_platform": "zhihu",
                    "content_id": "answer-1",
                    "author": "知乎作者",
                },
            }
        ],
        existing_preference={},
        event_chunk_size=1,
    )

    assert preference["interests"][0]["name"] == "AI Agent"
    assert len(service.calls) == 2
    assert "FORBIDDEN_CONTEXT" in service.calls[0]
    assert "如何评价新的 AI Agent 编程框架？" in service.calls[1]
    assert "zhihu" in service.calls[1]
    assert "FORBIDDEN_CONTEXT" not in service.calls[1]


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


def test_maybe_filter_events_keeps_bangumi_view_and_dislike() -> None:
    """Bangumi collection events carry no ``inferred_satisfaction`` — a 'view'
    (完成/搁置收藏 → 中性浏览) resolves to 'unknown' and must survive, while an
    explicit dislike (低评分 → feedback/dislike) must survive as negative
    evidence. The satisfaction filter runs for real here (no LLM involved);
    earlier guided-init tests bypassed this layer with a soul double, so this
    pins the real ``_maybe_filter_events`` path for Bangumi signals."""
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeStructuredService())  # filter on by default
    events = [
        # view → no inferred_satisfaction → treated as "unknown" → kept
        {
            "event_type": "view",
            "title": "某番剧 EP1",
            "metadata": {"source_platform": "bangumi"},
        },
        # feedback + feedback_type=dislike → explicit negative → kept
        {
            "event_type": "feedback",
            "title": "不感兴趣的番",
            "metadata": {"source_platform": "bangumi", "feedback_type": "dislike"},
        },
        # event_type=dislike → explicit negative → kept
        {
            "event_type": "dislike",
            "title": "点踩的番",
            "metadata": {"source_platform": "bangumi"},
        },
    ]

    kept = analyzer._maybe_filter_events(events)

    assert kept == events, "bangumi view + explicit dislikes must all pass the filter"


def test_normalize_style_coerces_schema_defying_llm_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LLM output that violates the style schema must be coerced, not persisted
    verbatim, and the coercion must be logged so the user gets a diagnosable line."""
    analyzer = PreferenceAnalyzer(FakeStructuredService())
    with caplog.at_level("WARNING", logger="openbiliclaw.soul.preference_analyzer"):
        normalized = analyzer._normalize_preference(
            {
                "style": {
                    "preferred_duration": "unknown",
                    "preferred_pace": "unknown",
                    "quality_sensitivity": 0,
                    "humor_preference": 0,
                    "depth_preference": "unknown",
                },
                "exploration_openness": "unknown",
                "context": {"session_type": "未知"},
            }
        )

    style = normalized["style"]
    assert isinstance(style, dict)
    # Illegal enums reset to "" so UIs fall back to their observing copy.
    assert style["preferred_duration"] == ""
    assert style["preferred_pace"] == ""
    # Non-numeric taste field resets to the field default (0.5).
    assert style["depth_preference"] == 0.5
    # A literal numeric 0 is a legitimate extreme and must survive untouched.
    assert style["quality_sensitivity"] == 0.0
    assert style["humor_preference"] == 0.0
    # Garbage openness falls back to the field default (0.5), NOT 0.0.
    assert normalized["exploration_openness"] == 0.5
    # Unknown-ish context placeholder cleared for UI fallback.
    assert normalized["context"]["session_type"] == ""

    warnings = [rec for rec in caplog.records if rec.levelname == "WARNING"]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    for field in (
        "preferred_duration",
        "preferred_pace",
        "depth_preference",
        "exploration_openness",
        "session_type",
    ):
        assert field in message
    # Legal numeric-0 fields must NOT be listed as corrected.
    assert "quality_sensitivity" not in message
    assert "humor_preference" not in message


def test_normalize_style_accepts_and_clamps_valid_numerics() -> None:
    analyzer = PreferenceAnalyzer(FakeStructuredService())
    normalized = analyzer._normalize_preference(
        {
            "style": {
                "preferred_duration": "LONG",
                "preferred_pace": "moderate",
                "quality_sensitivity": "0.7",
                "humor_preference": 1.7,
                "depth_preference": 0.0,
            },
            "exploration_openness": "0.9",
        }
    )
    style = normalized["style"]
    assert isinstance(style, dict)
    # Case-insensitive enum accepted and normalized to canonical lowercase.
    assert style["preferred_duration"] == "long"
    assert style["preferred_pace"] == "moderate"
    # Numeric string parsed.
    assert style["quality_sensitivity"] == 0.7
    # Out-of-range clamped to [0, 1].
    assert style["humor_preference"] == 1.0
    # Literal 0.0 accepted unchanged.
    assert style["depth_preference"] == 0.0
    assert normalized["exploration_openness"] == 0.9


def test_normalize_style_clean_payload_logs_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    analyzer = PreferenceAnalyzer(FakeStructuredService())
    with caplog.at_level("WARNING", logger="openbiliclaw.soul.preference_analyzer"):
        analyzer._normalize_preference(
            {
                "style": {
                    "preferred_duration": "medium",
                    "preferred_pace": "fast",
                    "quality_sensitivity": 0.4,
                    "humor_preference": 0.6,
                    "depth_preference": 0.8,
                },
                "exploration_openness": 0.5,
                "context": {"session_type": "深度钻研型"},
            }
        )
    assert [rec for rec in caplog.records if rec.levelname == "WARNING"] == []


# ── init-progress-visibility Phase 1: analyze_events progress_callback ────────


@pytest.mark.asyncio
async def test_chunked_analysis_reports_progress_per_chunk() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(LLMResponse(content='{"interests": []}', provider="openai"))
    analyzer = PreferenceAnalyzer(service)
    calls: list[tuple[int, int]] = []

    async def _cb(done: int, total: int) -> None:
        calls.append((done, total))

    await analyzer.analyze_events(
        events=[{"event_type": "view", "title": f"t{i}"} for i in range(8)],
        existing_preference={},
        event_chunk_size=1,
        progress_callback=_cb,
    )

    # Exactly one report per chunk, done strictly increasing 1..8 against total 8.
    assert calls == [(i, 8) for i in range(1, 9)]


@pytest.mark.asyncio
async def test_single_path_reports_one_progress_tick() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(LLMResponse(content='{"interests": []}', provider="openai"))
    analyzer = PreferenceAnalyzer(service)
    calls: list[tuple[int, int]] = []

    async def _cb(done: int, total: int) -> None:
        calls.append((done, total))

    await analyzer.analyze_events(
        events=[{"event_type": "view", "title": "solo"}],
        existing_preference={},
        progress_callback=_cb,
    )

    # Un-chunked path fires a single (1, 1) completion tick (locked semantics).
    assert calls == [(1, 1)]


@pytest.mark.asyncio
async def test_progress_callback_error_does_not_break_analysis() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    service = FakeStructuredService(LLMResponse(content='{"interests": []}', provider="openai"))
    analyzer = PreferenceAnalyzer(service)

    async def _boom(done: int, total: int) -> None:
        raise RuntimeError("observer down")

    # Callback failure is swallowed (WARNING) — analysis still returns a result.
    result = await analyzer.analyze_events(
        events=[{"event_type": "view", "title": f"t{i}"} for i in range(3)],
        existing_preference={},
        event_chunk_size=1,
        progress_callback=_boom,
    )
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_engine_analyze_events_forwards_progress_callback() -> None:
    """SoulEngine.analyze_events threads the callback down to the analyzer."""
    from unittest.mock import AsyncMock

    from openbiliclaw.soul.engine import SoulEngine
    from openbiliclaw.soul.ledger import ProfileLedger

    engine = SoulEngine.__new__(SoulEngine)
    engine._preference_analyzer = AsyncMock()  # type: ignore[attr-defined]
    engine._preference_analyzer.analyze_events = AsyncMock(return_value={})
    engine._init_cognition_context = {}  # type: ignore[attr-defined]
    # __new__ bypasses __init__; the init_preference_build write point (Wave A)
    # touches the best-effort ledger, so give it a no-op (database=None) one.
    engine._ledger = ProfileLedger(None)  # type: ignore[attr-defined]

    class _Layer:
        data: dict[str, object] = {}

        def save(self) -> None:
            pass

    class _Mem:
        def get_layer(self, name: str) -> _Layer:
            return _Layer()

    engine._memory = _Mem()  # type: ignore[attr-defined]

    async def _cb(done: int, total: int) -> None:
        pass

    await engine.analyze_events([{"event_type": "view", "title": "x"}], progress_callback=_cb)
    kwargs = engine._preference_analyzer.analyze_events.await_args.kwargs
    assert kwargs["progress_callback"] is _cb


class TestInitCognitionCandidatesSpreadAcrossChunks:
    """Init's awareness/insight drafts must not be monopolised by recent chunks.

    `_merge_init_cognition_contexts` walked chunk results in order and broke on
    the cap (12 awareness / 8 insights). Chunks are `events[i:i+200]` and the
    fetch returns newest first, so the newest one or two chunks filled the quota
    and every older period contributed nothing — the same "first arrival wins"
    failure the history summary had.
    """

    @staticmethod
    def _raw(era: str, count: int = 6) -> dict[str, object]:
        return {
            "awareness_candidates": [
                {"observation": f"{era}观察{i}", "trend": "t", "emotion_guess": "e"}
                for i in range(count)
            ],
            "insight_candidates": [
                {"hypothesis": f"{era}假设{i}", "confidence": 0.7, "evidence": ["x"]}
                for i in range(count)
            ],
        }

    def _analyzer(self):
        from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

        class _Stub:
            async def complete_structured_task(self, **_kwargs: object) -> object:
                raise AssertionError("merging must not call the LLM")

        return PreferenceAnalyzer(registry=_Stub())

    def test_every_chunk_contributes_awareness(self) -> None:
        analyzer = self._analyzer()
        chunks = [self._raw("最近"), self._raw("中期"), self._raw("早期")]

        merged = analyzer._merge_init_cognition_contexts(chunks)
        eras = {str(item["observation"])[:2] for item in merged["awareness"]}

        assert eras == {"最近", "中期", "早期"}, f"每个时期都应有觉察代表，实际只有 {sorted(eras)}"

    def test_every_chunk_contributes_insights(self) -> None:
        analyzer = self._analyzer()
        chunks = [self._raw("最近"), self._raw("中期"), self._raw("早期")]

        merged = analyzer._merge_init_cognition_contexts(chunks)
        eras = {str(item["hypothesis"])[:2] for item in merged["insights"]}

        assert eras == {"最近", "中期", "早期"}, f"每个时期都应有洞察代表，实际只有 {sorted(eras)}"

    def test_caps_are_still_respected(self) -> None:
        from openbiliclaw.soul.preference_analyzer import (
            _INIT_AWARENESS_CANDIDATES_CAP,
            _INIT_INSIGHT_CANDIDATES_CAP,
        )

        analyzer = self._analyzer()
        chunks = [self._raw(f"期{i}", count=20) for i in range(8)]

        merged = analyzer._merge_init_cognition_contexts(chunks)

        assert len(merged["awareness"]) == _INIT_AWARENESS_CANDIDATES_CAP
        assert len(merged["insights"]) == _INIT_INSIGHT_CANDIDATES_CAP

    def test_a_thin_chunk_does_not_waste_the_budget(self) -> None:
        """分片产出不均时，空出的名额要回流，而不是浪费。"""
        from openbiliclaw.soul.preference_analyzer import _INIT_AWARENESS_CANDIDATES_CAP

        analyzer = self._analyzer()
        chunks = [self._raw("多", count=10), self._raw("少", count=1)]

        merged = analyzer._merge_init_cognition_contexts(chunks)

        assert len(merged["awareness"]) == min(11, _INIT_AWARENESS_CANDIDATES_CAP)
        assert any(str(i["observation"]).startswith("少") for i in merged["awareness"])

    def test_duplicates_across_chunks_are_still_collapsed(self) -> None:
        analyzer = self._analyzer()
        same = self._raw("同", count=3)
        merged = analyzer._merge_init_cognition_contexts([same, dict(same), dict(same)])

        observations = [str(i["observation"]) for i in merged["awareness"]]
        assert len(observations) == len(set(observations)) == 3

    def test_lopsided_chunk_counts_still_reach_the_earliest_period(self) -> None:
        """真实历史的分片数量并不均衡——一次刷屏就能占掉大部分分片。

        纯轮转在这种形状下仍然偏向最近：洞察只有 8 个名额，而最近的刷屏
        占了十几个分片中的前八个，最早期照样一条都进不去。真机 A/B 就是
        这么暴露出来的（修复前洞察里木工/摄影各 1 条，纯轮转后变成 0 条）。
        """
        analyzer = self._analyzer()
        # 10 个"最近刷屏"分片 + 1 个中期 + 1 个最早期，模拟真实的倾斜。
        # 每片内容必须各不相同——若各片重复，去重会替纯轮转把名额腾出来，
        # 测试就守不住了（这个坑第一版踩过，突变能存活）。
        chunks = [self._raw(f"刷屏{i}", count=6) for i in range(10)]
        chunks.append(self._raw("中期", count=6))
        chunks.append(self._raw("早期", count=6))

        merged = analyzer._merge_init_cognition_contexts(chunks)
        insight_eras = {str(i["hypothesis"])[:2] for i in merged["insights"]}

        assert "早期" in insight_eras, f"最早期必须进入洞察草稿，实际只有 {sorted(insight_eras)}"
