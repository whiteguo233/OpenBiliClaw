"""Tests for prompt builders and core memory rendering."""

from pathlib import Path

from openbiliclaw.llm.prompts import (
    _AWARENESS_SYSTEM_PROMPT,
    _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT,
    build_avoidance_generation_prompt,
    build_awareness_prompt,
    build_batch_content_evaluation_prompt,
    build_batch_expression_prompt,
    build_explore_domains_prompt,
    build_recommendation_expression_prompt,
    build_search_queries_prompt,
    build_socratic_dialogue_prompt,
    build_soul_profile_prompt,
    build_speculation_generation_prompt,
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


def test_recommendation_expression_prompts_treat_dislikes_as_avoidance() -> None:
    profile_summary = {
        "personality_portrait": "偏好高信息密度内容",
        "disliked_topics": ["标题党", "低质混剪"],
    }

    single = build_recommendation_expression_prompt(
        profile_summary=profile_summary,
        content_summary={"title": "讲透国际局势", "up_name": "某UP"},
        tone_profile=None,
        source_platform="bilibili",
    )
    batch = build_batch_expression_prompt(
        profile_summary=profile_summary,
        content_items=[{"title": "讲透国际局势", "up_name": "某UP"}],
        tone_profile=None,
        source_platform="bilibili",
    )

    assert "disliked_topics" in single[1]["content"]
    assert "disliked_topics" in batch[1]["content"]
    assert "避开 profile_summary.disliked_topics" in single[0]["content"]
    assert "避开 profile_summary.disliked_topics" in batch[0]["content"]


def test_avoidance_generation_prompt_requires_source_modes() -> None:
    messages = build_avoidance_generation_prompt(
        profile_summary={
            "likes": ["AI"],
            "disliked_topics": ["标题党"],
            "style": {"preferred_pace": "dense"},
        },
        existing_avoidances=["浅层热点复读"],
        cooldown_domains=["营销号带货"],
        confirmed_dislikes=["标题党"],
        confirmed_likes=["AI"],
        count=5,
    )

    assert messages[0]["role"] == "system"
    text = messages[0]["content"] + messages[1]["content"]
    assert "negative_signal" in text
    assert "positive_boundary" in text
    assert "style_boundary" in text
    assert "不能直接把正向兴趣本身当成讨厌对象" in text
    assert "同一 source_mode + 同一粗主题" in text
    assert "existing_avoidance_details" in messages[1]["content"]
    assert "disliked_topics" in messages[1]["content"]
    assert "cooldown_domains" in messages[1]["content"]


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


def test_awareness_prompt_orders_stable_context_before_recent_events() -> None:
    messages = build_awareness_prompt(
        events=[{"event_type": "view", "title": "本次最新事件"}],
        preference_summary={"interests": ["长期偏好"]},
        soul_profile={"core_traits": ["稳定画像"]},
    )

    user_prompt = messages[1]["content"]

    assert user_prompt.index("<soul_profile>") < user_prompt.index("<preference_summary>")
    assert user_prompt.index("<preference_summary>") < user_prompt.index("<recent_events>")


def test_build_awareness_prompt_system_message_equals_constant() -> None:
    """The system message is the literal _AWARENESS_SYSTEM_PROMPT — no
    interpolation, no concatenation. Required for provider-side prompt
    cache to fire on the awareness call."""
    messages = build_awareness_prompt(
        events=[{"event_type": "view", "title": "X"}],
        preference_summary={"a": 1},
        soul_profile={"x": 1},
    )

    assert messages[0]["content"] == _AWARENESS_SYSTEM_PROMPT


def test_awareness_prompt_mentions_dislike_as_awareness_signal() -> None:
    messages = build_awareness_prompt(
        events=[
            {
                "event_type": "feedback",
                "title": "低质混剪",
                "inferred_satisfaction": "negative",
                "metadata": {"feedback_type": "dislike"},
            }
        ],
        preference_summary={"disliked_topics": ["低质混剪"]},
        soul_profile={"core_traits": ["谨慎"]},
    )

    assert "feedback_type=dislike" in messages[0]["content"]
    assert "最近开始避开" in messages[0]["content"]


def test_build_awareness_prompt_user_block_ends_with_recent_events() -> None:
    """Recent events is the most-variable block and must be the suffix.
    Anything stable after it would shrink the cache prefix on every call."""
    messages = build_awareness_prompt(
        events=[{"event_type": "view", "title": "本次最新事件"}],
        preference_summary={"interests": ["长期偏好"]},
        soul_profile={"core_traits": ["稳定画像"]},
    )

    user_prompt = messages[1]["content"]

    assert user_prompt.rstrip().endswith("</recent_events>")


def test_build_awareness_prompt_serialization_is_deterministic() -> None:
    """Differently-ordered dict keys with identical semantic payloads must
    yield byte-identical user messages. Validates sort_keys=True on the
    profile, preference, and event-object json.dumps calls. Without this,
    every call writes a new cache prefix and the awareness call loses
    its ~36k-token cache hit."""
    soul_profile_a = {"core_traits": ["稳定画像"], "values": ["求真"]}
    soul_profile_b = {"values": ["求真"], "core_traits": ["稳定画像"]}

    preference_a = {"interests": ["深度内容"], "disliked_topics": ["标题党"]}
    preference_b = {"disliked_topics": ["标题党"], "interests": ["深度内容"]}

    events_a = [{"event_type": "view", "title": "事件 A", "url": "https://a"}]
    events_b = [{"title": "事件 A", "url": "https://a", "event_type": "view"}]

    msg_a = build_awareness_prompt(
        events=events_a,
        preference_summary=preference_a,
        soul_profile=soul_profile_a,
    )
    msg_b = build_awareness_prompt(
        events=events_b,
        preference_summary=preference_b,
        soul_profile=soul_profile_b,
    )

    assert msg_a[1]["content"] == msg_b[1]["content"]


def test_speculation_prompt_requests_probe_mode_distance_bands() -> None:
    messages = build_speculation_generation_prompt(
        profile_summary="likes: 机器人技术",
        existing_speculations=[],
        cooldown_domains=[],
        confirmed_domains=["机器人技术"],
        count=5,
    )

    system = messages[0]["content"]
    # Distance definitions are static and should stay in the system prompt for prompt-cache reuse.
    assert "probe_mode" in system
    for band in ("near", "lateral", "bridge", "wildcard"):
        assert band in system


def test_speculation_prompt_accepts_slot_aware_probe_mode_request() -> None:
    messages = build_speculation_generation_prompt(
        profile_summary="likes: 机器人技术",
        existing_speculations=[],
        cooldown_domains=[],
        confirmed_domains=["机器人技术"],
        count=3,
        probe_mode_request=(
            "本轮普通 near 池已满，只补挑战探针。"
            "所有候选的 probe_mode 必须从 lateral / bridge / wildcard 中选择，不要输出 near。"
        ),
    )

    user = messages[1]["content"]
    assert "<probe_mode_request>" in user
    assert "只补挑战探针" in user
    assert "不要输出 near" in user


def test_batch_content_evaluation_prompt_orders_profile_before_source_and_batch() -> None:
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["长期偏好"]},
        content_items=[{"title": "本批候选"}],
        source_context="trending",
        source_platform="bilibili",
    )

    user_prompt = messages[1]["content"]

    assert user_prompt.index("<profile_summary>") < user_prompt.index("<source_platform>")
    assert user_prompt.index("<source_platform>") < user_prompt.index("<source_context>")
    assert user_prompt.index("<source_context>") < user_prompt.index("<content_batch>")


def test_batch_content_evaluation_prompt_allows_per_item_platforms() -> None:
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        source_platform="mixed",
        source_context="mixed",
        content_items=[
            {
                "content_id": "BV1",
                "source_platform": "bilibili",
                "source_strategy": "search",
                "content_type": "video",
                "title": "Bili item",
            },
            {
                "content_id": "xhs1",
                "source_platform": "xiaohongshu",
                "source_strategy": "xhs-extension-search",
                "content_type": "note",
                "title": "XHS item",
            },
        ],
    )

    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "<source_platform>\n\nmixed\n\n</source_platform>" in user
    assert '"source_platform": "bilibili"' in user
    assert '"source_platform": "xiaohongshu"' in user
    assert "Do not lower or raise preference score merely because" in system


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
            "build_awareness_prompt",
            dict(
                events=[{"event_type": "view", "title": "A"}],
                preference_summary={"a": 1},
                soul_profile={"x": 1},
            ),
            dict(
                events=[{"event_type": "like", "title": "B"}],
                preference_summary={"a": 2},
                soul_profile={"x": 2},
            ),
        ),
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
        (
            "build_avoidance_generation_prompt",
            dict(
                profile_summary={"likes": ["A"], "disliked_topics": ["X"]},
                existing_avoidances=["old"],
                cooldown_domains=[],
                confirmed_dislikes=["X"],
                confirmed_likes=["A"],
                count=3,
            ),
            dict(
                profile_summary={"likes": ["B"], "disliked_topics": ["Y"]},
                existing_avoidances=["other"],
                cooldown_domains=["cool"],
                confirmed_dislikes=["Y"],
                confirmed_likes=["B"],
                count=5,
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


# ----------------------------------------------------------------------
# v0.3.x batch_content_evaluation negative_examples block.


def test_batch_eval_no_examples_user_message_equals_none_path() -> None:
    """negative_examples=None and =[] both produce a user message
    byte-identical to the pre-feature shape — preserves cache prefix for
    cold-start users with no negative classified events yet."""
    base_kwargs: dict[str, object] = dict(
        profile_summary={"a": 1},
        content_items=[{"x": 1}],
        source_context="trending",
        source_platform="bilibili",
    )
    none_msg = build_batch_content_evaluation_prompt(**base_kwargs)
    empty_msg = build_batch_content_evaluation_prompt(**base_kwargs, negative_examples=[])

    assert none_msg[1]["content"] == empty_msg[1]["content"]
    assert "<negative_examples>" not in none_msg[1]["content"]


def test_batch_eval_negative_examples_block_sits_after_source_context() -> None:
    """When supplied, the block sits strictly between <source_context>
    and <content_batch> — the cache-stable suffix slot in the builder."""
    msg = build_batch_content_evaluation_prompt(
        profile_summary={"a": 1},
        content_items=[{"x": 1}],
        source_context="search",
        source_platform="bilibili",
        negative_examples=[
            {"title": "被微电子男朋友的学识震惊到", "reason": "quick_exit", "age_days": 2}
        ],
    )
    user = msg[1]["content"]
    src_end = user.index("</source_context>")
    neg_start = user.index("<negative_examples>")
    batch_start = user.index("<content_batch>")
    assert src_end < neg_start < batch_start
    assert "被微电子男朋友的学识震惊到" in user


def test_batch_eval_system_message_byte_equal_to_constant_with_negatives() -> None:
    """The system prompt must remain identical to the module constant
    regardless of whether negative_examples is supplied — the two new
    rules (10, 11) are PERMANENT additions, not call-conditional."""
    base_kwargs: dict[str, object] = dict(
        profile_summary={"a": 1},
        content_items=[{"x": 1}],
        source_context="explore",
        source_platform="bilibili",
    )
    none_msg = build_batch_content_evaluation_prompt(**base_kwargs)
    with_neg = build_batch_content_evaluation_prompt(
        **base_kwargs,
        negative_examples=[{"title": "X", "reason": "quick_exit", "age_days": 1}],
    )
    assert none_msg[0]["content"] == _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    assert with_neg[0]["content"] == _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT


def test_batch_eval_system_invariant_across_negative_example_lengths() -> None:
    """Sanity: feeding 0, 1, and 5 examples must yield the same system bytes."""
    base_kwargs: dict[str, object] = dict(
        profile_summary={"a": 1},
        content_items=[{"x": 1}],
        source_context="explore",
        source_platform="bilibili",
    )
    payloads = [
        None,
        [{"title": "X", "reason": "quick_exit", "age_days": 1}],
        [{"title": f"标题{i}", "reason": "quick_exit", "age_days": i} for i in range(5)],
    ]
    systems = {
        build_batch_content_evaluation_prompt(**base_kwargs, negative_examples=p)[0]["content"]
        for p in payloads
    }
    assert len(systems) == 1


def test_batch_eval_negative_examples_json_uses_sort_keys() -> None:
    """The new block must round-trip differently-ordered dict keys to
    byte-identical bytes — same prompt-cache discipline as the rest of
    the builder."""
    examples_a = [
        {"title": "X", "reason": "quick_exit", "age_days": 1},
        {"age_days": 2, "title": "Y", "reason": "explicit_negative"},
    ]
    examples_b = [
        {"age_days": 1, "title": "X", "reason": "quick_exit"},
        {"reason": "explicit_negative", "title": "Y", "age_days": 2},
    ]
    base_kwargs: dict[str, object] = dict(
        profile_summary={"a": 1},
        content_items=[{"x": 1}],
        source_context="explore",
        source_platform="bilibili",
    )
    msg_a = build_batch_content_evaluation_prompt(**base_kwargs, negative_examples=examples_a)
    msg_b = build_batch_content_evaluation_prompt(**base_kwargs, negative_examples=examples_b)
    assert msg_a[1]["content"] == msg_b[1]["content"]


# ----------------------------------------------------------------------
# Task 11 (X / twitter): text-first items carry their full text in
# ``body_text``. Pure-text tweets have low-information titles, so the LLM
# needs ``body_text`` in the USER message of every recommendation /
# evaluation builder. It MUST stay out of the (cached) system prompt.


def _twitter_content_item() -> dict[str, object]:
    return {
        "bvid": "1790000000000000001",
        "content_id": "1790000000000000001",
        "title": "1/ on building resilient systems",
        "up_name": "@handle",
        "content_type": "thread",
        "body_text": "1/ TWEET_BODY_MARKER long-form note_tweet body about systems ...",
    }


def test_batch_eval_prompt_carries_body_text_in_user_message_only() -> None:
    item = _twitter_content_item()
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        content_items=[item],
        source_context="search",
        source_platform="twitter",
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "TWEET_BODY_MARKER" in user
    assert "body_text" in user
    assert "TWEET_BODY_MARKER" not in system
    # System stays the cached constant byte-for-byte.
    assert system == _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT


def test_recommendation_expression_prompt_carries_body_text_in_user_only() -> None:
    item = _twitter_content_item()
    messages = build_recommendation_expression_prompt(
        profile_summary={"a": 1},
        content_summary=item,
        tone_profile=None,
        source_platform="twitter",
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "TWEET_BODY_MARKER" in user
    assert "TWEET_BODY_MARKER" not in system


def test_batch_expression_prompt_carries_body_text_in_user_only() -> None:
    item = _twitter_content_item()
    messages = build_batch_expression_prompt(
        profile_summary={"a": 1},
        content_items=[item],
        tone_profile=None,
        source_platform="twitter",
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "TWEET_BODY_MARKER" in user
    assert "TWEET_BODY_MARKER" not in system
