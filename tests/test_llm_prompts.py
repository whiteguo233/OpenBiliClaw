"""Tests for prompt builders and core memory rendering."""

from pathlib import Path

from openbiliclaw.llm.prompts import (
    build_explore_domains_prompt,
    build_recommendation_expression_prompt,
    build_socratic_dialogue_prompt,
    build_soul_profile_prompt,
)
from openbiliclaw.memory.manager import MemoryManager


def test_render_core_memory_prompt_includes_soul_and_preferences(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)
    memory.get_layer("soul").update("personality_portrait", "一个理性又敏感的人")
    memory.get_layer("preference").update("favorite_up_users", ["影视飓风", "小约翰可汗"])

    prompt = memory.render_core_memory_prompt()

    assert "理性又敏感" in prompt
    assert "常看UP主" in prompt
    assert "影视飓风" in prompt


def test_render_core_memory_prompt_handles_empty_memory(tmp_path: Path) -> None:
    memory = MemoryManager(tmp_path)

    prompt = memory.render_core_memory_prompt()

    assert "尚未建立完整画像" in prompt


def test_build_socratic_dialogue_prompt_orders_messages_correctly() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我最近有点迷上纪录片",
        core_memory_text="## 用户画像\n喜欢深度内容",
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        history=[
            {"role": "user", "content": "我最近总在看长视频"},
            {"role": "assistant", "content": "你更在意信息密度还是叙事感？"},
        ],
    )

    assert messages[0]["role"] == "system"
    assert "喜欢深度内容" in messages[0]["content"]
    assert messages[1]["content"] == "我最近总在看长视频"
    assert messages[2]["content"] == "你更在意信息密度还是叙事感？"
    assert messages[3]["content"] == "我最近有点迷上纪录片"


def test_build_socratic_dialogue_prompt_includes_dialogue_instructions() -> None:
    messages = build_socratic_dialogue_prompt(
        user_message="我喜欢那种讲得很透的内容",
        core_memory_text="（尚未建立完整画像）",
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        history=[],
    )

    assert "苏格拉底" in messages[0]["content"]
    assert "老B友" in messages[0]["content"]


def test_build_recommendation_expression_prompt_mentions_old_friend_tone() -> None:
    messages = build_recommendation_expression_prompt(
        profile_summary={"personality_portrait": "偏好高信息密度内容"},
        content_summary={"title": "讲透国际局势", "up_name": "某UP"},
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
    )

    assert "老B友" in messages[0]["content"]
    assert "不像算法推荐" in messages[0]["content"]


def test_build_soul_profile_prompt_avoids_report_tone() -> None:
    messages = build_soul_profile_prompt(
        history_summary={"recent_topics": ["国际新闻"]},
        preference_summary={"interests": ["国际关系"]},
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
    )

    assert "老朋友" in messages[0]["content"]
    assert "不要写成心理报告" in messages[0]["content"]
    assert "3 到 6 条" in messages[0]["content"]


def test_build_explore_domains_prompt_requires_directional_diversity() -> None:
    messages = build_explore_domains_prompt(
        profile_summary={
            "personality_portrait": "偏好把复杂问题讲透，也愿意接受有陌生感的新内容。",
            "interests": ["策略游戏", "深度讲解"],
            "deep_needs": ["建立判断确定性"],
        }
    )

    system_prompt = messages[0]["content"]

    assert "至少覆盖 3 类不同内容方向" in system_prompt
    assert "同一母题的换皮变体最多只能保留 1 个" in system_prompt
    assert "先说明它对应用户的哪种认知需求" in system_prompt
