from __future__ import annotations

import json

import pytest

from openbiliclaw.llm.base import LLMResponse


class FakeRegistry:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content, provider="openai")


class FakeStructuredService:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "history": history,
            }
        )
        return LLMResponse(content=self.content, provider="openai")


@pytest.mark.asyncio
async def test_profile_builder_creates_soul_profile_from_json() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "这是一个长期保持好奇心、偏好深度内容、做判断较为克制的人。"
                * 8,
                "core_traits": ["理性", "好奇", "谨慎"],
                "values": ["真实", "成长"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )

    profile = await ProfileBuilder(service).build(
        history=[{"title": "AI 视频", "author": "科技UP主"}],
        preference={"interests": [{"name": "科技", "category": "知识"}]},
    )

    assert profile.personality_portrait.startswith("这是一个长期保持好奇心")
    assert profile.core_traits == ["理性", "好奇", "谨慎"]
    assert profile.values == ["真实", "成长"]
    assert profile.life_stage == "处于探索与积累阶段"
    assert profile.deep_needs == ["被理解", "持续成长"]
    assert service.calls


@pytest.mark.asyncio
async def test_profile_builder_raises_on_invalid_json() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    with pytest.raises(SoulProfileBuildError, match="invalid JSON"):
        await ProfileBuilder(FakeStructuredService("not-json")).build(
            history=[{"title": "AI 视频"}],
            preference={},
        )


@pytest.mark.asyncio
async def test_profile_builder_raises_on_empty_response() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    with pytest.raises(SoulProfileBuildError, match="empty soul profile"):
        await ProfileBuilder(FakeStructuredService("")).build(
            history=[{"title": "AI 视频"}],
            preference={},
        )


@pytest.mark.asyncio
async def test_profile_builder_raises_when_portrait_is_too_short() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "过短描述",
                "core_traits": ["理性", "好奇", "谨慎"],
                "values": ["真实", "成长"],
                "life_stage": "探索阶段",
                "deep_needs": ["被理解"],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(SoulProfileBuildError, match="at least 200"):
        await ProfileBuilder(service).build(
            history=[{"title": "AI 视频"}],
            preference={},
        )


@pytest.mark.asyncio
async def test_profile_builder_allows_missing_preference_data() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "喜欢长期积累、偏好深度内容、处理信息比较审慎的人。"
                * 8,
                "core_traits": ["理性", "自驱", "克制"],
                "values": ["成长", "真实"],
                "life_stage": "稳定积累阶段",
                "deep_needs": ["确认方向", "持续成长"],
            },
            ensure_ascii=False,
        )
    )

    profile = await ProfileBuilder(service).build(
        history=[{"title": "AI 视频"}],
        preference={},
    )

    assert profile.core_traits == ["理性", "自驱", "克制"]


@pytest.mark.asyncio
async def test_profile_builder_can_use_unified_service() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "这是一个长期保持好奇心、偏好深度内容、做判断较为克制的人。"
                * 8,
                "core_traits": ["理性", "好奇", "谨慎"],
                "values": ["真实", "成长"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )

    profile = await ProfileBuilder(service).build(history=[{"title": "AI 视频"}], preference={})

    assert profile.core_traits == ["理性", "好奇", "谨慎"]
    assert service.calls


@pytest.mark.asyncio
async def test_profile_builder_injects_old_friend_tone_in_prompt() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "这是一个长期保持好奇心、偏好深度内容、做判断较为克制的人。"
                * 8,
                "core_traits": ["理性", "好奇", "谨慎"],
                "values": ["真实", "成长"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )

    await ProfileBuilder(service).build(
        history=[{"title": "国际新闻", "author": "时事UP"}],
        preference={},
    )

    assert "老朋友" in str(service.calls[0]["system_instruction"])


def test_profile_builder_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    with pytest.raises(TypeError, match="complete_structured_task"):
        ProfileBuilder(FakeRegistry("{}"))
