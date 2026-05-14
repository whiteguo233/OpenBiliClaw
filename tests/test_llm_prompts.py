"""Tests for prompt builders and core memory rendering."""

from pathlib import Path

from openbiliclaw.llm.prompts import (
    build_explore_domains_prompt,
    build_recommendation_expression_prompt,
    build_search_queries_prompt,
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
    """v0.3.28+: tone-profile rendering with 老B友 lives in user_prompt
    instead of system_prompt. System keeps the algorithm-rejection rule."""
    messages = build_recommendation_expression_prompt(
        profile_summary={"personality_portrait": "偏好高信息密度内容"},
        content_summary={"title": "讲透国际局势", "up_name": "某UP"},
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        source_platform="bilibili",
    )

    # 老B友 now in user_prompt's tone block (not system)
    assert "老B友" in messages[1]["content"]
    # System keeps the algorithm-recommendation taboo
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

    assert "朋友" in messages[0]["content"]
    assert "3 到 6 条" in messages[0]["content"]


def test_search_prompt_includes_pool_distribution_hints() -> None:
    messages = build_search_queries_prompt(
        profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
        pool_hints={
            "avoid_topics": ["AI 编程", "原神"],
            "prefer_axes": ["人物纪录", "审美体验"],
            "avoid_styles": ["deep_dive"],
            "avoid_franchises": ["原神"],
        },
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    assert "avoid_franchises" in system_prompt
    assert "<pool_distribution_hints>" in user_prompt
    assert "AI 编程" in user_prompt
    assert "人物纪录" in user_prompt


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


def test_build_explore_domains_prompt_requires_core_interest_anchors() -> None:
    messages = build_explore_domains_prompt(
        profile_summary={
            "personality_portrait": "偏好高信息密度内容，也接受适度陌生感。",
            "interests": ["咒术回战", "Fate", "AI技术与大模型"],
            "deep_needs": ["建立判断确定性"],
        }
    )

    system_prompt = messages[0]["content"]

    assert "domain" in system_prompt
    assert "novelty_level" in system_prompt
    assert "why_it_might_resonate" in system_prompt


def test_build_explore_domains_prompt_passes_covered_groups_into_user_msg() -> None:
    """v0.3.31+: covered_topic_groups feeds into the user message and
    the system prompt names the rule. Together this lets the LLM avoid
    re-proposing already-saturated areas."""
    covered = ["人工智能", "认知科学", "体育预测"]
    messages = build_explore_domains_prompt(
        profile_summary={"interests": ["AI"]},
        covered_topic_groups=covered,
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    # System rule must reference the constraint by name so the LLM
    # actually applies it rather than ignoring the user-msg block.
    assert "covered_topic_groups" in system_prompt
    assert "盲区优先" in system_prompt or "禁止" in system_prompt

    # User msg must carry the actual list (deduped, JSON-serialized).
    assert "<covered_topic_groups>" in user_prompt
    for label in covered:
        assert label in user_prompt


def test_build_explore_domains_prompt_omits_block_when_no_covered_groups() -> None:
    """Empty / None covered list → original prompt shape, no extra
    block added (back-compat for callers that don't pass DB)."""
    messages_none = build_explore_domains_prompt(
        profile_summary={"interests": []},
        covered_topic_groups=None,
    )
    messages_empty = build_explore_domains_prompt(
        profile_summary={"interests": []},
        covered_topic_groups=[],
    )

    for m in (messages_none, messages_empty):
        assert "<covered_topic_groups>" not in m[1]["content"]


def test_build_explore_domains_prompt_caps_covered_groups_at_12() -> None:
    """Defensive: don't over-constrain the model. Cap at 12 so the most-
    saturated topic_groups make it into the avoidance signal but the
    model still has room to maneuver. Larger caps (e.g. 30) caused
    DeepSeek to return empty content on ~half of explore cycles."""
    covered = [f"topic_{i}" for i in range(100)]
    messages = build_explore_domains_prompt(
        profile_summary={"interests": []},
        covered_topic_groups=covered,
    )
    user_prompt = messages[1]["content"]

    # First 12 included, anything past 12 dropped to keep model unboxed
    assert "topic_0" in user_prompt
    assert "topic_11" in user_prompt
    assert "topic_30" not in user_prompt
    assert "topic_99" not in user_prompt


# ----------------------------------------------------------------------
# v0.3.28+: prompt-cache convention enforcement.
#
# All prompt builders MUST emit a system message that's byte-identical
# across different per-call inputs. Provider-side prompt cache (DeepSeek,
# OpenAI, Claude, Gemini, most relays) only fires when the prefix is
# completely stable; any builder that interpolates per-call data into
# the system message effectively turns off caching for every call.
#
# Contract: system_prompt is a function ONLY of the prompt template
# itself, never of the call arguments. Verify by calling each builder
# with two distinctly-different argument sets and asserting the system
# message is identical.


def _builder_test_inputs() -> list[tuple[str, dict, dict]]:
    """(builder_name, args1, args2) — two materially different inputs each.

    Add a row here when introducing a new prompt builder; the test below
    will then guard its system-prompt stability automatically.
    """
    return [
        (
            "build_batch_content_evaluation_prompt",
            dict(
                profile_summary={"a": 1},
                content_items=[{"x": 1}],
                source_context="search",
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_items=[{"x": 2}],
                source_context="trending",
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_content_evaluation_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                source_context="search",
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                source_context="explore",
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_recommendation_expression_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                tone_profile={
                    "density": "dense",
                    "warmth": "warm",
                    "playfulness": "low",
                    "directness": "direct",
                },
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_batch_expression_prompt",
            dict(
                profile_summary={"a": 1},
                content_items=[{"x": 1}],
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_items=[{"x": 2}],
                tone_profile={
                    "density": "balanced",
                    "warmth": "neutral",
                    "playfulness": "high",
                    "directness": "balanced",
                },
                source_platform="xiaohongshu",
            ),
        ),
        (
            "build_delight_reason_prompt",
            dict(
                profile_summary={"a": 1},
                content_summary={"x": 1},
                reason_stub="x",
                tone_profile=None,
                source_platform="bilibili",
            ),
            dict(
                profile_summary={"a": 2},
                content_summary={"x": 2},
                reason_stub="y",
                tone_profile={
                    "density": "dense",
                    "warmth": "warm",
                    "playfulness": "medium",
                    "directness": "balanced",
                },
                source_platform="xiaohongshu",
            ),
        ),
        # NOTE: build_socratic_dialogue_prompt is intentionally NOT in
        # this list — its system prompt embeds per-user core memory /
        # tone / friend label, which is fine for OpenBiliClaw's single-
        # user model (per-user state is stable across sessions for the
        # same install, so cache still fires on repeated dialogue
        # turns). A multi-user deployment would refactor it.
    ]


def test_prompt_builder_system_messages_are_call_invariant() -> None:
    """Every prompt builder must emit a system message that does NOT
    depend on per-call arguments. Required for provider-side prompt
    cache to actually hit.

    If this test fails for a NEW builder you just added: refactor so
    the variables move to user_prompt and only the static template
    stays in system. See ``build_batch_content_evaluation_prompt`` for
    the canonical pattern.
    """
    from openbiliclaw.llm import prompts as prompts_mod

    failures: list[str] = []
    for name, args1, args2 in _builder_test_inputs():
        fn = getattr(prompts_mod, name, None)
        assert fn is not None, f"missing builder: {name}"
        m1 = fn(**args1)
        m2 = fn(**args2)
        assert m1 and m1[0].get("role") == "system", f"{name}: no system msg"
        sys1 = m1[0]["content"]
        sys2 = m2[0]["content"]
        if sys1 != sys2:
            failures.append(name)

    assert not failures, (
        "Cache-poisoning prompt builders (system message changed with "
        "input — extends provider cache miss across all calls): "
        f"{failures}. Refactor to put per-call variables in user_prompt."
    )
