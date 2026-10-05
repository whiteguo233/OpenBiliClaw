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
        caller: str = "",
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "history": history,
            }
        )
        return LLMResponse(content=self.content, provider="openai")


class SequenceStructuredService:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
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
        self.calls.append(
            {
                "system_instruction": system_instruction,
                "user_input": user_input,
                "history": history,
            }
        )
        content = self.contents.pop(0)
        return LLMResponse(content=content, provider="openai")


_VALID_PROFILE_PAYLOAD = json.dumps(
    {
        "personality_portrait": (
            "你这人不是随便被内容推着走的类型，心里有一套自己的筛子。"
            "你需要事情有意思，也需要它说得通；只热闹不扎实会让你很快失去耐心，"
            "只严肃不鲜活又会让你觉得生活被拧得太紧。你更舒服的状态，是在松弛和较真之间自由切换，"
            "既能接住轻松的情绪能量，也愿意花时间把一件事拆到里面看。最近你的节奏像是在重新校准："
            "一边保留玩心，一边想把判断、能力和生活秩序都捋得更清楚。"
        ),
        "core_traits": ["杂食", "较真", "自我节奏感强"],
        "cognitive_style": ["会先看结构", "对信息密度敏感"],
        "motivational_drivers": ["保持有趣", "把事情想明白"],
        "current_phase": "最近在把轻松感和认真感重新调到一个更舒服的位置。",
        "values": ["真实", "成长"],
        "life_stage": "稳定积累阶段",
        "deep_needs": ["在玩乐与正经之间自由切换的空间", "不被打扰的深度专注时间"],
    },
    ensure_ascii=False,
)


@pytest.mark.asyncio
async def test_profile_builder_creates_soul_profile_from_json() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": (
                    "我觉得你是那种看视频之前会先看弹幕密度的人。"
                    "你不是随便刷刷就完了，"
                    "你得看明白——不管是技术原理还是游戏数值平衡，"
                    "你都得追到底层逻辑那一层才算消化完。"
                    "心理学上这叫场独立型认知——"
                    "就是你处理信息时不太受表面包装影响，会自己去拆结构。"
                    "你的开放性其实很高，但挑剔度也很高。"
                    "这不矛盾——你是选择性开放，"
                    "不是什么都接受，而是对好东西的接收天线特别灵敏。"
                    "最近的你看起来在做一件事："
                    "在信息洪流和个人生活之间找平衡点。"
                    "一边追前沿科技，一边练传统功法——"
                    "这在心理学里叫自主感和胜任感都到位了，开始补身心整合。"
                    "不是焦虑，是进阶。"
                ),
                "core_traits": ["理性", "好奇", "谨慎"],
                "cognitive_style": ["会先看结构", "对证据比较敏感", "偏好把问题讲透"],
                "motivational_drivers": ["建立判断确定性", "持续扩展理解边界"],
                "current_phase": "最近更像在一边吸收高密度信息，一边整理自己的判断框架。",
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
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.personality_portrait.startswith("我觉得你是那种")
    assert profile.core_traits == ["理性", "好奇", "谨慎"]
    assert profile.cognitive_style == ["会先看结构", "对证据比较敏感", "偏好把问题讲透"]
    assert profile.motivational_drivers == ["建立判断确定性", "持续扩展理解边界"]
    assert profile.current_phase == "最近更像在一边吸收高密度信息，一边整理自己的判断框架。"
    assert profile.values == ["真实", "成长"]
    assert profile.life_stage == "处于探索与积累阶段"
    assert profile.deep_needs == ["被理解", "持续成长"]
    assert service.calls


@pytest.mark.asyncio
async def test_profile_builder_retries_with_compact_history_after_invalid_json() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = SequenceStructuredService(
        [
            "The request was rejected because it was considered high risk",
            _VALID_PROFILE_PAYLOAD,
        ]
    )

    profile = await ProfileBuilder(service).build(
        history=[{"title": f"标题 {idx}", "author": "作者"} for idx in range(120)],
        preference={"interests": [{"name": "科技", "category": "知识"}]},
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.core_traits == ["杂食", "较真", "自我节奏感强"]
    assert len(service.calls) == 2
    assert "history omitted after profile-build retry" in str(service.calls[1]["user_input"])


@pytest.mark.asyncio
async def test_profile_builder_raises_on_invalid_json() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    with pytest.raises(SoulProfileBuildError, match="invalid JSON"):
        await ProfileBuilder(FakeStructuredService("not-json")).build(
            history=[{"title": "AI 视频"}],
            preference={},
            awareness_notes=[],
            active_insights=[],
        )


@pytest.mark.asyncio
async def test_profile_builder_raises_on_empty_response() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    with pytest.raises(SoulProfileBuildError, match="empty soul profile"):
        await ProfileBuilder(FakeStructuredService("")).build(
            history=[{"title": "AI 视频"}],
            preference={},
            awareness_notes=[],
            active_insights=[],
        )


@pytest.mark.asyncio
async def test_profile_builder_raises_when_portrait_is_too_short() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder, SoulProfileBuildError

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "过短描述",
                "core_traits": ["理性", "好奇", "谨慎"],
                "cognitive_style": ["会先看结构"],
                "motivational_drivers": ["建立判断确定性"],
                "current_phase": "最近在整理判断。",
                "values": ["真实", "成长"],
                "life_stage": "探索阶段",
                "deep_needs": ["被理解"],
            },
            ensure_ascii=False,
        )
    )

    with pytest.raises(SoulProfileBuildError, match="expected 120-500 chars"):
        await ProfileBuilder(service).build(
            history=[{"title": "AI 视频"}],
            preference={},
            awareness_notes=[],
            active_insights=[],
        )


@pytest.mark.asyncio
async def test_profile_builder_accepts_slightly_long_real_model_portrait() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    portrait = (
        "你这人不是那种被内容推着走的人，心里一直有一套自己的筛子。"
        "你需要事情有趣，也需要它说得通；只热闹不扎实会让你很快失去耐心，"
        "只严肃不鲜活又会让你觉得生活被拧得太紧。你更舒服的状态，"
        "是在松弛和较真之间自由切换，既能接住轻松的情绪能量，"
        "也愿意花时间把一件事拆到里面看。你对世界的兴趣不只是消费，"
        "更像是在找一种能让自己持续长出判断力的生活方式。最近你的节奏像是在重新校准："
        "一边保留玩心，一边想把判断、能力和生活秩序都捋得更清楚。"
        "这种状态并不焦虑，反而说明你开始更在意什么东西真正值得留下。"
        "你不想被单一身份框住，也不太愿意为了迎合外界节奏放弃自己的好奇心。"
        "所以你真正看重的不是某一个固定标签，而是自己能不能在不断变化的内容和生活里，"
        "仍然保留判断、选择和重新出发的余地。"
    )
    assert len(portrait) > 320

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": portrait,
                "core_traits": ["杂食", "较真", "自我节奏感强"],
                "cognitive_style": ["会先看结构"],
                "motivational_drivers": ["把事情想明白"],
                "current_phase": "最近在重新校准自己的节奏。",
                "values": ["真实", "成长"],
                "life_stage": "稳定积累阶段",
                "deep_needs": ["保留自由切换的空间"],
            },
            ensure_ascii=False,
        )
    )

    profile = await ProfileBuilder(service).build(
        history=[{"title": "AI 视频"}],
        preference={"interests": [{"name": "科技"}]},
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.personality_portrait == portrait


@pytest.mark.asyncio
async def test_profile_builder_defaults_missing_auxiliary_list_fields() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    payload = json.loads(_VALID_PROFILE_PAYLOAD)
    payload.pop("motivational_drivers")
    service = FakeStructuredService(json.dumps(payload, ensure_ascii=False))

    profile = await ProfileBuilder(service).build(
        history=[{"title": "AI 视频"}],
        preference={"interests": [{"name": "科技"}]},
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.motivational_drivers == []
    assert profile.core_traits == ["杂食", "较真", "自我节奏感强"]


@pytest.mark.asyncio
async def test_profile_builder_allows_missing_preference_data() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "喜欢长期积累、偏好深度内容、处理信息比较审慎的人。" * 8,
                "core_traits": ["理性", "自驱", "克制"],
                "cognitive_style": ["偏好先想清楚再表态", "对信息密度要求较高"],
                "motivational_drivers": ["确认方向", "积累长期能力"],
                "current_phase": "最近更像在稳定积累，不急着追逐表面热度。",
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
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.core_traits == ["理性", "自驱", "克制"]


@pytest.mark.asyncio
async def test_profile_builder_can_use_unified_service() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": (
                    "我觉得你是那种看视频之前会先看弹幕密度的人。"
                    "你不是随便刷刷就完了，"
                    "你得看明白——不管是技术原理还是游戏数值平衡，"
                    "你都得追到底层逻辑那一层才算消化完。"
                    "心理学上这叫场独立型认知——"
                    "就是你处理信息时不太受表面包装影响，会自己去拆结构。"
                    "你的开放性其实很高，但挑剔度也很高。"
                    "这不矛盾——你是选择性开放，"
                    "不是什么都接受，而是对好东西的接收天线特别灵敏。"
                    "最近的你看起来在做一件事："
                    "在信息洪流和个人生活之间找平衡点。"
                    "一边追前沿科技，一边练传统功法——"
                    "这在心理学里叫自主感和胜任感都到位了，开始补身心整合。"
                    "不是焦虑，是进阶。"
                ),
                "core_traits": ["理性", "好奇", "谨慎"],
                "cognitive_style": ["会先看结构", "偏好讲透"],
                "motivational_drivers": ["扩大理解边界"],
                "current_phase": "最近更像在主动扩张认知边界。",
                "values": ["真实", "成长"],
                "life_stage": "处于探索与积累阶段",
                "deep_needs": ["被理解", "持续成长"],
            },
            ensure_ascii=False,
        )
    )

    profile = await ProfileBuilder(service).build(
        history=[{"title": "AI 视频"}],
        preference={},
        awareness_notes=[],
        active_insights=[],
    )

    assert profile.core_traits == ["理性", "好奇", "谨慎"]
    assert service.calls


@pytest.mark.asyncio
async def test_profile_builder_injects_old_friend_tone_in_prompt() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": (
                    "我觉得你是那种看视频之前会先看弹幕密度的人。"
                    "你不是随便刷刷就完了，"
                    "你得看明白——不管是技术原理还是游戏数值平衡，"
                    "你都得追到底层逻辑那一层才算消化完。"
                    "心理学上这叫场独立型认知——"
                    "就是你处理信息时不太受表面包装影响，会自己去拆结构。"
                    "你的开放性其实很高，但挑剔度也很高。"
                    "这不矛盾——你是选择性开放，"
                    "不是什么都接受，而是对好东西的接收天线特别灵敏。"
                    "最近的你看起来在做一件事："
                    "在信息洪流和个人生活之间找平衡点。"
                    "一边追前沿科技，一边练传统功法——"
                    "这在心理学里叫自主感和胜任感都到位了，开始补身心整合。"
                    "不是焦虑，是进阶。"
                ),
                "core_traits": ["理性", "好奇", "谨慎"],
                "cognitive_style": ["会先看结构", "偏好讲透"],
                "motivational_drivers": ["扩大理解边界"],
                "current_phase": "最近更像在主动扩张认知边界。",
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
        awareness_notes=[
            {
                "date": "2026-03-20",
                "observation": "最近会在高信息密度内容里停留更久。",
                "trend": "更偏向讲透结构，而不是只看热点结论。",
            }
        ],
        active_insights=[
            {
                "hypothesis": "用户可能在通过深度内容建立判断确定性。",
                "confidence": 0.71,
            }
        ],
    )

    assert "朋友" in str(service.calls[0]["system_instruction"])
    assert "人格画像" in str(service.calls[0]["system_instruction"])
    assert "core_traits" in str(service.calls[0]["system_instruction"])
    assert "<recent_awareness>" in str(service.calls[0]["user_input"])
    assert "<active_insights>" in str(service.calls[0]["user_input"])


def test_summarize_history_includes_favorites_and_following() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    history: list[dict[str, object]] = [
        {"title": f"视频{i}", "author_name": f"UP主{i % 3}"} for i in range(10)
    ]
    history.append(
        {
            "title": "[收藏夹汇总]",
            "_favorites": [{"title": "收藏A", "folder": "默认"}],
            "_favorites_summary": "共 1 个收藏，涵盖: 默认",
        }
    )
    history.append(
        {
            "title": "[关注列表汇总]",
            "_following": [{"name": "大佬A"}],
            "_following_summary": "共关注 1 人，包括: 大佬A",
        }
    )

    summary = ProfileBuilder._summarize_history(history)  # type: ignore[arg-type]

    # Enriched summaries should be present
    assert summary["favorites_summary"] == "共 1 个收藏，涵盖: 默认"
    assert summary["following_summary"] == "共关注 1 人，包括: 大佬A"
    # count should exclude the two enriched items
    assert summary["count"] == 10
    # titles should not contain the placeholder titles
    assert "[收藏夹汇总]" not in summary["titles"]  # type: ignore[operator]
    assert "[关注列表汇总]" not in summary["titles"]  # type: ignore[operator]


def test_summarize_history_works_without_enriched_items() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    history: list[dict[str, object]] = [
        {"title": f"视频{i}", "author_name": "某UP"} for i in range(5)
    ]

    summary = ProfileBuilder._summarize_history(history)  # type: ignore[arg-type]

    assert summary["count"] == 5
    assert "favorites_summary" not in summary
    assert "following_summary" not in summary


def test_summarize_history_synthesises_context_for_raw_bilibili_items() -> None:
    """v0.3.23+: raw B站 history items don't carry a ``context`` field
    natively. _summarize_history should synthesise one via
    format_event_context so the LLM sees a uniform stream of
    natural-language descriptions across sources."""
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    history: list[dict[str, object]] = [
        {"title": "讲透历史叙事", "author_name": "历史实验室"},
        {"title": "Rust 重写老代码", "author": "独立编程人"},
    ]
    summary = ProfileBuilder._summarize_history(history)  # type: ignore[arg-type]

    contexts = summary.get("contexts")
    assert isinstance(contexts, list)
    assert len(contexts) == 2
    # Synthesised context carries platform + verb + author
    assert any("B 站" in c and "讲透历史叙事" in c and "历史实验室" in c for c in contexts)
    assert any("B 站" in c and "Rust" in c and "独立编程人" in c for c in contexts)
    # Hint string lives alongside contexts so the LLM knows what they are
    assert "contexts_hint" in summary


def test_summarize_history_preserves_xhs_native_context() -> None:
    """v0.3.23+: history items already carrying ``context`` (xhs items
    via _xhs_events_to_history_items) should pass through verbatim,
    not be overwritten by the synthesised fallback."""
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    history: list[dict[str, object]] = [
        {
            "title": "手冲咖啡入门",
            "context": "小红书收藏：手冲咖啡入门 作者：豆子老师",
            "metadata": {"source_platform": "xiaohongshu", "author": "豆子老师"},
            "event_type": "favorite",
        },
        {
            "title": "讲透历史叙事",
            "author_name": "历史实验室",
        },
    ]
    summary = ProfileBuilder._summarize_history(history)  # type: ignore[arg-type]

    contexts = summary.get("contexts", [])
    assert isinstance(contexts, list)
    # XHS context preserved verbatim (uses fullwidth ":" / scope label)
    assert "小红书收藏" in contexts[0]
    assert "豆子老师" in contexts[0]
    # B站 raw item synthesised in unified format
    assert "B 站" in contexts[1]


def test_summarize_history_recent_contexts_split_matches_recent_titles() -> None:
    """recent_contexts / older_contexts mirror the same recent/older
    cutoff used by recent_titles / older_titles, so a downstream
    consumer can assume they're index-aligned."""
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    history: list[dict[str, object]] = [
        {"title": f"视频{i}", "author_name": f"UP{i}"} for i in range(20)
    ]
    summary = ProfileBuilder._summarize_history(history)  # type: ignore[arg-type]

    assert "recent_titles" in summary
    assert "recent_contexts" in summary
    assert "older_titles" in summary
    assert "older_contexts" in summary
    # The cutoff is 30% of items (max 1) — for 20 items that's 6
    assert len(summary["recent_titles"]) == len(summary["recent_contexts"])  # type: ignore[arg-type]
    assert len(summary["older_titles"]) == len(summary["older_contexts"])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_e2e_init_favorites_following_reach_llm_prompt() -> None:
    """Simulate the cli init flow and verify favorites/following reach the LLM prompt.

    This is the end-to-end regression test for the Docker profile completeness bug:
    cli.py builds combined_history with _favorites_summary/_following_summary,
    ProfileBuilder._summarize_history extracts them, and they appear in the
    user_input sent to the LLM.
    """
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    # 1. Simulate what cli.py init builds as combined_history
    history: list[dict[str, object]] = [
        {"title": f"视频{i}", "author_name": f"UP主{i % 5}"} for i in range(500)
    ]
    favorites_data = [
        {"title": "收藏A", "folder": "游戏", "upper": "UP主0"},
        {"title": "收藏B", "folder": "科技", "upper": "UP主1"},
        {"title": "收藏C", "folder": "游戏", "upper": "UP主2"},
    ]
    following_data = [
        {"name": "影视飓风", "sign": "科技影视"},
        {"name": "老番茄", "sign": "游戏搞笑"},
    ]

    combined_history: list[dict[str, object]] = list(history)
    combined_history.append(
        {
            "title": "[收藏夹汇总]",
            "_favorites": favorites_data,
            "_favorites_summary": f"共 {len(favorites_data)} 个收藏，涵盖: "
            + ", ".join(set(f["folder"] for f in favorites_data)),
        }
    )
    combined_history.append(
        {
            "title": "[关注列表汇总]",
            "_following": following_data,
            "_following_summary": f"共关注 {len(following_data)} 人，包括: "
            + ", ".join(f["name"] for f in following_data),
        }
    )

    # 2. Build profile with a fake LLM that captures the prompt
    service = FakeStructuredService(
        json.dumps(
            {
                "personality_portrait": "x" * 200,
                "core_traits": ["好奇"],
                "cognitive_style": ["偏好深度"],
                "motivational_drivers": ["探索"],
                "current_phase": "当前阶段描述",
                "values": ["成长"],
                "life_stage": "学生",
                "deep_needs": ["被理解"],
            },
            ensure_ascii=False,
        )
    )

    await ProfileBuilder(service).build(
        history=combined_history,  # type: ignore[arg-type]
        preference={"interests": []},
        awareness_notes=[],
        active_insights=[],
    )

    # 3. Verify the LLM prompt contains favorites and following summaries
    user_input = str(service.calls[0]["user_input"])
    assert "共 3 个收藏" in user_input, "favorites_summary missing from LLM prompt"
    assert "影视飓风" in user_input, "following names missing from LLM prompt"
    assert "老番茄" in user_input, "following names missing from LLM prompt"

    # 4. Verify placeholder titles are NOT in the prompt's history_summary titles
    assert "[收藏夹汇总]" not in user_input, "placeholder title leaked into prompt"
    assert "[关注列表汇总]" not in user_input, "placeholder title leaked into prompt"

    # 5. Verify history count is correct (500, not 502)
    assert '"count": 500' in user_input, "history count should exclude enriched items"


def test_profile_builder_requires_core_memory_task_service() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    with pytest.raises(TypeError, match="complete_structured_task"):
        ProfileBuilder(FakeRegistry("{}"))


class TestHistorySamplingIsRepresentative:
    """Init used to feed the model whatever history arrived first.

    `_summarize_history` sliced `titles[:100]` / `contexts[:100]` /
    `recent|older[:50]` off the fetch order, which on a real account is the
    newest tail. With 1000 rows the profile was built from ~100 of them and any
    long-standing interest older than that window was invisible no matter how
    strongly the user had engaged with it. Selection now mirrors the incremental
    path: weight by the same satisfaction semantics, spread across time.
    """

    @staticmethod
    def _row(index: int, *, day: int, event_type: str = "view", ratio: float = 0.5) -> dict:
        return {
            "title": f"标题-{index}",
            "event_type": event_type,
            "view_at": 1_700_000_000 + day * 86_400,
            "duration": 1000,
            "progress": int(1000 * ratio),
            "metadata": {},
        }

    def test_sample_covers_the_whole_span_not_just_the_newest_tail(self) -> None:
        from openbiliclaw.soul.profile_builder import _sample_representative

        # 600 rows over 300 days, arriving newest-first like the real fetch does
        items = [self._row(i, day=300 - i // 2) for i in range(600)]
        picked = _sample_representative(items, 100)

        assert len(picked) == 100
        days = [item["view_at"] for item in picked]
        oldest, newest = min(days), max(days)
        span = newest - oldest
        full_span = max(i["view_at"] for i in items) - min(i["view_at"] for i in items)
        assert span > full_span * 0.8, "样本必须铺开整个时间跨度，而不是只取最近一段"
        # every sixth of the timeline contributes something
        buckets = {int((d - oldest) / max(1, span) * 5.999) for d in days}
        assert len(buckets) == 6, f"每个时间段都应有代表，实际覆盖 {sorted(buckets)}"

    def test_explicit_interactions_outrank_bounced_views(self) -> None:
        from openbiliclaw.soul.profile_builder import _sample_representative

        # Same day so time bucketing cannot be what decides it
        bounced = [self._row(i, day=10, ratio=0.05) for i in range(90)]
        collected = [self._row(900 + i, day=10, event_type="favorite") for i in range(10)]
        picked = _sample_representative(bounced + collected, 20)

        kept_titles = {item["title"] for item in picked}
        assert all(f"标题-{900 + i}" in kept_titles for i in range(10)), (
            "收藏这类明确互动必须全部入选，不能被大量划走行为挤掉"
        )

    def test_favorites_reach_the_portrait_as_individual_rows(self) -> None:
        """收藏此前整体塌成一句「共 N 个收藏」，一个标题都进不了画像。

        `_favorites` 那份列表写进了 combined_history 但没有任何人读它——
        `_summarize_history` 只取 `_favorites_summary`。用户主动存下来的内容是最强的
        意图信号，却是画像里唯一看不见的那部分。
        """
        from openbiliclaw.soul.profile_builder import ProfileBuilder

        views = [self._row(i, day=200 - i // 3, ratio=0.1) for i in range(300)]
        favorites = [
            {
                "title": f"收藏-{i}",
                "author_name": "UP",
                "event_type": "favorite",
                "source_platform": "bilibili",
                "fav_time": 1_700_000_000 + i * 86_400,
            }
            for i in range(40)
        ]
        summary_row = {"title": "[收藏夹汇总]", "_favorites_summary": "共 40 个收藏，涵盖: AI"}

        result = ProfileBuilder._summarize_history([*views, *favorites, summary_row])

        titles = [*result["recent_titles"], *result["older_titles"]]
        contexts = [*result["recent_contexts"], *result["older_contexts"]]
        assert any(title.startswith("收藏-") for title in titles), "收藏必须作为独立行进画像"
        assert any("收藏了" in line for line in contexts), "语境要说明这是收藏而不是观看"
        assert result["favorites_summary"] == "共 40 个收藏，涵盖: AI", "收藏夹名仍作为汇总保留"

    def test_favorites_are_dated_by_fav_time(self) -> None:
        """收藏没有 view_at；读不到 fav_time 就等于没时间戳，会掉出时间分层。"""
        from openbiliclaw.soul.profile_builder import _history_timestamp

        assert _history_timestamp({"fav_time": 1_700_000_000}) == 1_700_000_000
        assert _history_timestamp({"metadata": {"fav_time": 1_700_000_000}}) == 1_700_000_000

    def test_finished_watches_outrank_bounces(self) -> None:
        from openbiliclaw.soul.profile_builder import _history_weight

        finished = self._row(1, day=1, ratio=0.95)
        partial = self._row(2, day=1, ratio=0.5)
        bounced = self._row(3, day=1, ratio=0.05)
        favorite = self._row(4, day=1, event_type="favorite")

        assert _history_weight(favorite) > _history_weight(finished)
        assert _history_weight(finished) > _history_weight(partial)
        assert _history_weight(partial) > _history_weight(bounced)
        assert _history_weight(bounced) > 0, "划走也是信号，不该被完全归零"

    def test_rows_without_timestamps_degrade_to_arrival_order(self) -> None:
        from openbiliclaw.soul.profile_builder import _sample_representative

        items = [{"title": f"无时间-{i}"} for i in range(300)]
        picked = _sample_representative(items, 50)

        assert len(picked) == 50
        assert [i["title"] for i in picked] == [f"无时间-{i}" for i in range(50)]

    def test_small_history_is_passed_through_untouched(self) -> None:
        from openbiliclaw.soul.profile_builder import _sample_representative

        items = [self._row(i, day=i) for i in range(20)]
        assert _sample_representative(items, 100) == items

    def test_summary_reports_true_total_and_explains_the_sample(self) -> None:
        from openbiliclaw.soul.profile_builder import ProfileBuilder

        items = [self._row(i, day=300 - i // 2) for i in range(400)]
        summary = ProfileBuilder._summarize_history(items)

        assert summary["count"] == 400, "count 必须是真实总量，不是样本量"
        assert len(summary["titles"]) <= 100
        assert "sampling_hint" in summary, "必须告诉模型这是抽样而非全量"
        assert "400" in str(summary["sampling_hint"])

    def test_summarized_titles_span_the_history_not_its_newest_prefix(self) -> None:
        """Guards the wiring, not just the sampler.

        A mutation that put `regular_items[:100]` back survived every other test
        here, because they exercise `_sample_representative` directly. This one
        asserts the summary the LLM actually receives is time-spread.
        """
        from openbiliclaw.soul.profile_builder import ProfileBuilder

        # 400 rows arriving newest-first, spanning ~200 days
        items = [self._row(i, day=300 - i // 2) for i in range(400)]
        summary = ProfileBuilder._summarize_history(items)

        indexes = sorted(int(t.split("-")[1]) for t in summary["titles"])
        assert len(indexes) <= 100
        # Arrival-order slicing can only ever reach index 99; real sampling has
        # to reach deep into the older tail.
        assert max(indexes) > 300, (
            f"标题只覆盖到 index {max(indexes)}，说明仍在按到达顺序取前 100 条"
        )
        assert min(indexes) < 100, "最近的行为也应有代表"
        # and the spread should not clump into one end
        midpoint = sum(1 for i in indexes if i > 200)
        assert midpoint >= len(indexes) // 4, (
            f"较早的一半只贡献了 {midpoint}/{len(indexes)} 条，分布不均"
        )


@pytest.mark.asyncio
async def test_profile_builder_forwards_reply_style_into_tone_block() -> None:
    """issue #255: ProfileBuilder.reply_style reaches the profile prompt's tone block."""
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(_VALID_PROFILE_PAYLOAD)
    builder = ProfileBuilder(service, reply_style="像个老朋友一样直白")

    await builder.build(
        history=[{"title": "AI 视频", "author": "科技UP主"}],
        preference={"interests": [{"name": "科技", "category": "知识"}]},
        awareness_notes=[],
        active_insights=[],
    )

    user_input = str(service.calls[0]["user_input"])
    assert "- 回复风格: 像个老朋友一样直白" in user_input


@pytest.mark.asyncio
async def test_profile_builder_default_reply_style_leaves_prompt_untouched() -> None:
    from openbiliclaw.soul.profile_builder import ProfileBuilder

    service = FakeStructuredService(_VALID_PROFILE_PAYLOAD)
    builder = ProfileBuilder(service)

    await builder.build(
        history=[{"title": "AI 视频", "author": "科技UP主"}],
        preference={"interests": [{"name": "科技", "category": "知识"}]},
        awareness_notes=[],
        active_insights=[],
    )

    user_input = str(service.calls[0]["user_input"])
    assert "- 回复风格:" not in user_input
