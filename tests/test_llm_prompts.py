"""Tests for prompt builders and core memory rendering."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import openbiliclaw.llm.prompts as prompt_module
from openbiliclaw.discovery.style_keys import VALID_STYLE_KEYS
from openbiliclaw.llm.prompts import (
    _AWARENESS_SYSTEM_PROMPT,
    _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT,
    _MERGED_KEYWORDS_SYSTEM_PROMPT,
    build_avoidance_generation_prompt,
    build_awareness_prompt,
    build_batch_content_evaluation_prompt,
    build_batch_expression_prompt,
    build_content_evaluation_prompt,
    build_explore_domains_prompt,
    build_merged_keywords_prompt,
    build_profile_consolidation_prompt,
    build_recommendation_expression_prompt,
    build_search_queries_prompt,
    build_socratic_dialogue_prompt,
    build_soul_profile_prompt,
    build_speculation_generation_prompt,
    content_evaluation_clock,
    parse_merged_keywords,
    parse_merged_keywords_with_presence,
    parse_merged_keywords_with_presence_and_explore_domains,
)
from openbiliclaw.memory.manager import MemoryManager

_PROFILE_BLOCKS = [
    '<profile_core>\n\n{"core_traits":["stable"]}\n\n</profile_core>',
    '<profile_interests>\n\n{"interests":[{"name":"AI"}]}\n\n</profile_interests>',
]


def _assert_layered_profile_prefix(user_prompt: str, later_tag: str) -> None:
    assert "<profile_summary>" not in user_prompt
    assert user_prompt.index("<profile_core>") < user_prompt.index("<profile_interests>")
    assert user_prompt.index("<profile_interests>") < user_prompt.index(later_tag)


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
    assert "OpenBiliClaw 本地长期画像" in messages[0]["content"]
    assert "不要声称这些信息只能留在当前聊天上下文" in messages[0]["content"]
    assert "不能修改 B 站或其他内容平台自身的推荐算法" in messages[0]["content"]


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


def test_recommendation_expression_prompt_defaults_to_warm_direct_tone() -> None:
    messages = build_recommendation_expression_prompt(
        profile_summary={"personality_portrait": "尚未建立完整画像"},
        content_summary={"title": "讲透国际局势", "up_name": "某UP"},
        tone_profile=None,
        source_platform="bilibili",
    )

    user_prompt = messages[1]["content"]

    assert "- 信息密度: balanced" in user_prompt
    assert "- 情绪温度: warm" in user_prompt
    assert "- 梗感强度: low" in user_prompt
    assert "- 直给程度: direct" in user_prompt


def test_recommendation_expression_prompt_accepts_profile_blocks_first() -> None:
    messages = build_recommendation_expression_prompt(
        profile_summary={"core_traits": ["fallback"]},
        profile_blocks=_PROFILE_BLOCKS,
        content_summary={"title": "候选"},
        tone_profile=None,
        source_platform="bilibili",
    )

    user_prompt = messages[1]["content"]
    _assert_layered_profile_prefix(user_prompt, "<source_platform>")
    assert user_prompt.index("<source_platform>") < user_prompt.index("<content_summary>")


def test_batch_expression_prompt_accepts_profile_blocks_first() -> None:
    messages = build_batch_expression_prompt(
        profile_summary={"core_traits": ["fallback"]},
        profile_blocks=_PROFILE_BLOCKS,
        content_items=[{"bvid": "BV1", "title": "候选"}],
        tone_profile=None,
        source_platform="bilibili",
    )

    user_prompt = messages[1]["content"]
    _assert_layered_profile_prefix(user_prompt, "<source_platform>")
    assert user_prompt.index("<source_platform>") < user_prompt.index("<content_batch>")


def test_delight_llm_prompt_builders_are_removed() -> None:
    assert not hasattr(prompt_module, "build_delight_score_batch_prompt")
    assert not hasattr(prompt_module, "build_delight_reason_prompt")


def test_merged_keywords_prompt_accepts_profile_blocks_first() -> None:
    messages = build_merged_keywords_prompt(
        profile_summary={"core_traits": ["fallback"]},
        profile_blocks=_PROFILE_BLOCKS,
        platform_blocks=[{"platform": "bilibili", "need": 3}],
    )

    _assert_layered_profile_prefix(messages[1]["content"], "<platforms>")


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


def test_search_prompt_treats_cold_start_hints_as_diversity_budget() -> None:
    messages = build_search_queries_prompt(
        profile_summary={"interests": [{"name": "人工智能", "weight": 0.96}]},
        pool_hints={
            "cold_start": True,
            "avoid_topics": ["人工智能", "机器学习"],
            "prefer_axes": ["篮球战术", "电影拉片", "科技"],
        },
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    assert "cold_start" in user_prompt
    assert "冷启动" in system_prompt
    assert "最多 2 个 query" in system_prompt
    assert "prefer_axes" in system_prompt


def test_merged_keywords_prompt_treats_cold_start_hints_as_diversity_budget() -> None:
    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "人工智能", "weight": 0.96}]},
        platform_blocks=[
            {
                "platform": "bilibili",
                "need": 5,
                "recent_keywords": [],
                "avoid_topics": ["人工智能"],
                "avoid_styles": [],
                "avoid_franchises": [],
                "prefer_axes": ["篮球战术", "电影拉片"],
                "cold_start": True,
            }
        ],
    )

    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    assert "冷启动保护" in system_prompt
    assert "最多 2 个" in system_prompt
    assert '"cold_start": true' in user_prompt
    assert "prefer_axes" in user_prompt
    assert "篮球战术" in user_prompt


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
    assert "只包含 domain、novelty_level、queries 三个字段" in system_prompt


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
    assert "queries" in system_prompt
    assert "长文本字段" in system_prompt


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


def test_awareness_prompts_require_evidence_for_dislike_claims() -> None:
    """Issue #205: notes claimed 点踩 without any dislike event in evidence.
    Both awareness system prompts must forbid dislike claims unless a real
    negative-feedback event exists in recent_events."""
    from openbiliclaw.llm.prompts import _AWARENESS_WITH_CONFUSIONS_SYSTEM_PROMPT

    for system_prompt in (_AWARENESS_SYSTEM_PROMPT, _AWARENESS_WITH_CONFUSIONS_SYSTEM_PROMPT):
        assert "负反馈一致性" in system_prompt
        assert "绝不能在笔记中声称用户点踩" in system_prompt


def test_build_awareness_with_confusions_prompt_includes_existing_confusions() -> None:
    from openbiliclaw.llm.prompts import build_awareness_with_confusions_prompt

    messages = build_awareness_with_confusions_prompt(
        events=[{"event_type": "view", "title": "最新事件"}],
        preference_summary={"interests": ["稳定偏好"]},
        soul_profile={"core_traits": ["稳定画像"]},
        existing_confusions=[
            {
                "id": 1,
                "status": "open",
                "topic": "城市菜市场与社区商业观察",
                "observation": "反复停留但从不购买",
                "interpretation": "可能是社区观察型兴趣",
            },
            {
                "id": 2,
                "status": "resolved",
                "topic": "已经处理过的疑惑",
                "observation": "历史确认过",
                "interpretation": "无需再问",
            },
        ],
    )

    user = messages[1]["content"]
    assert "<existing_confusions>" in user
    assert "</existing_confusions>" in user
    assert "城市菜市场与社区商业观察" in user
    assert "已经处理过的疑惑" in user


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


def test_batch_content_evaluation_compact_json_changes_whitespace_only() -> None:
    kwargs = {
        "profile_summary": {"interests": ["系统 设计"], "values": ["可靠"]},
        "content_items": [
            {
                "content_id": "item-1",
                "title": "保留 字符串 内部 空格",
                "tags": ["架构", "测试"],
            }
        ],
        "source_context": "mixed",
        "source_platform": "mixed",
        "negative_examples": [{"title": "不要 破坏", "reason": "quick_exit"}],
        "evaluated_at": "2026-08-04T09:47:31Z",
    }
    pretty = build_batch_content_evaluation_prompt(**kwargs)
    compact = build_batch_content_evaluation_prompt(**kwargs, compact_json=True)

    assert compact[0]["content"] == pretty[0]["content"]
    assert len(compact[1]["content"]) < len(pretty[1]["content"])

    for tag in (
        "profile_summary",
        "negative_examples",
        "evaluation_context",
        "content_batch",
    ):
        start = f"<{tag}>\n\n"
        end = f"\n\n</{tag}>"
        pretty_json = pretty[1]["content"].split(start, 1)[1].split(end, 1)[0]
        compact_json = compact[1]["content"].split(start, 1)[1].split(end, 1)[0]
        assert json.loads(compact_json) == json.loads(pretty_json)
        assert "\n  " not in compact_json

    assert "保留 字符串 内部 空格" in compact[1]["content"]


def test_batch_content_evaluation_treatment_seam_preserves_production_bytes() -> None:
    kwargs = {
        "profile_summary": {"interests": ["systems"]},
        "content_items": [{"content_id": "global-1", "title": "candidate"}],
        "source_context": "search",
        "source_platform": "bilibili",
    }

    historical_defaults = build_batch_content_evaluation_prompt(**kwargs)
    explicit_production = build_batch_content_evaluation_prompt(
        **kwargs,
        candidate_block=None,
        local_result_ids=False,
    )

    assert explicit_production == historical_defaults


def test_batch_content_evaluation_sparse_contract_is_static_and_transport_neutral() -> None:
    sparse_json = (
        '{"defaults":{"content_type":"video","mode":"normal",'
        '"source_platform":"bilibili"},"items":'
        '[{"author":"u","id":"0","title":"candidate"}]}'
    )
    row_wire = "ROW-WIRE-V1\ndefaults\tmode=normal\ncolumns\tid\nrow\t0"

    sparse_messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        content_items=[],
        candidate_block=sparse_json,
        local_result_ids=True,
    )
    row_messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["different"]},
        content_items=[{"content_id": "must-not-render"}],
        candidate_block=row_wire,
        local_result_ids=True,
    )

    system = sparse_messages[0]["content"]
    assert row_messages[0]["content"] == system
    assert "ROW-WIRE-V1" in system
    assert "defaults/items" in system
    assert "原样带回输入里的 id" in system
    assert "cover:<id>" in system
    assert "bvid" not in system
    assert "content_id" not in system
    assert "严格低于 0.5" in system

    sparse_block = (
        sparse_messages[1]["content"]
        .split("<content_batch>", 1)[1]
        .split(
            "</content_batch>",
            1,
        )[0]
    )
    row_block = (
        row_messages[1]["content"]
        .split("<content_batch>", 1)[1]
        .split(
            "</content_batch>",
            1,
        )[0]
    )
    assert sparse_block.strip() == sparse_json
    assert row_block.strip() == row_wire
    assert "must-not-render" not in row_messages[1]["content"]


@pytest.mark.parametrize(
    ("candidate_block", "local_result_ids"),
    [(None, True), ('{"defaults":{},"items":[]}', False)],
)
def test_batch_content_evaluation_rejects_mixed_identity_contracts(
    candidate_block: str | None,
    local_result_ids: bool,
) -> None:
    with pytest.raises(ValueError, match="must be enabled together"):
        build_batch_content_evaluation_prompt(
            profile_summary={},
            content_items=[],
            candidate_block=candidate_block,
            local_result_ids=local_result_ids,
        )


def test_content_evaluation_prompts_only_allow_explore_scoring_exception() -> None:
    single_system = build_content_evaluation_prompt(
        profile_summary={"interests": ["音乐", "生活方式"]},
        content_summary={"title": "匿名热门游戏视频"},
        source_context="trending",
        source_platform="youtube",
    )[0]["content"]
    batch_system = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["音乐", "生活方式"]},
        content_items=[
            {
                "content_id": "yt-gaming",
                "title": "匿名热门游戏视频",
                "source_strategy": "yt_trending",
            }
        ],
        source_context="mixed",
        source_platform="mixed",
    )[0]["content"]

    for system in (single_system, batch_system):
        assert "除 explore 外，发现路径和平台只提供上下文，不得影响评分标准" in system
        assert "不得因为内容热门、来自推荐流、命中搜索词、沿相关推荐获得" in system
        assert "明显不匹配画像的内容必须允许低于 admission 门槛" in system
        assert "只有 explore 允许主题陌生" in system
        assert "trending 基础分 >= 0.6" not in system
        assert "trending 来源的内容已经过大众验证" not in system
        assert "search 要求高度匹配" not in system
        assert "related_chain 允许适度偏移" not in system


def test_content_evaluation_prompts_define_publication_time_semantics() -> None:
    single_messages = build_content_evaluation_prompt(
        profile_summary={"interests": ["人工智能"]},
        content_summary={"title": "模型更新", "published_at": "2026-08-01T00:00:00Z"},
        source_context="trending",
        source_platform="bilibili",
        evaluated_at="2026-08-04T09:00:00Z",
    )
    batch_messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["人工智能"]},
        content_items=[
            {
                "content_id": "BV1TIME",
                "title": "模型更新",
                "published_at": "2026-08-01T00:00:00Z",
            }
        ],
        source_context="trending",
        source_platform="bilibili",
        evaluated_at="2026-08-04T09:00:00Z",
    )

    for messages in (single_messages, batch_messages):
        system = messages[0]["content"]
        user = messages[1]["content"]
        assert "published_at 是来源提供的权威发布时间" in system
        assert "evaluation_context.evaluated_at 是本次评估的权威时间基准" in system
        assert "模型知识截止时间" in system
        assert "score 只衡量内容与用户画像的相关性及内容本身价值" in system
        assert "与发布时间和时效性完全解耦" in system
        assert "不得因为内容较旧或 published_at 缺失而减分" in system
        assert "时间字段缺失或无效时仍可按内容语义分类" in system
        for temporal_class in (
            "breaking",
            "current",
            "versioned",
            "evergreen",
            "historical",
            "unknown",
        ):
            assert temporal_class in system
        assert "标题里的“今天”“最新”、年份" in system
        assert "trending/search/feed 也不能决定分类" in system
        assert "temporal_confidence" in system
        assert "不是内容质量、相关性或新鲜度" in system
        assert "条件、假设、可能性或未来态句子" in system
        assert "如果支持版本发生变化就重新核验" in system
        assert "Temporal V2 仍是当前受支持版本" in system
        assert '"evaluated_at": "2026-08-04T09:00:00Z"' in user
        assert '"published_at": "2026-08-01T00:00:00Z"' in user


def test_temporal_evaluation_output_contract_is_static_for_pretty_and_sparse_batches() -> None:
    pretty = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        content_items=[{"content_id": "global-1", "title": "candidate"}],
    )
    sparse = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["different"]},
        content_items=[],
        candidate_block=(
            '{"defaults":{"content_type":"video","mode":"normal",'
            '"source_platform":"bilibili"},"items":'
            '[{"author":"u","id":"0","title":"candidate"}]}'
        ),
        local_result_ids=True,
    )

    for system in (pretty[0]["content"], sparse[0]["content"]):
        assert "temporal_class" in system
        assert "temporal_confidence" in system
        assert "temporal_reason" in system
        assert "temporal_validity_mode" in system
        assert "temporal_valid_until" in system
        assert "temporal_scope" in system
        assert "temporal_evidence" in system
        assert "temporal_state" in system
        assert "explicit_deadline" in system
        assert "event_state" in system
        assert "version_state" in system
        assert "freshness_only" in system
        assert "所有非 none mode 都必须给逐字证据" in system
        assert "evergreen/historical" in system
        assert "freshness_only + hook" in system
        assert "限时免费领取" in system
        assert "今晚首播" in system
        assert "unknown、active、expired、superseded" in system
        assert "event_state 只能" in system
        assert "version_state 只能" in system
        assert "temporal_class=unknown 时必须输出 temporal_confidence=0" in system
        assert '"temporal_class": "evergreen"' in system
        assert "分类看核心价值" in system
        assert "score 只衡量内容与用户画像的相关性及内容本身价值" in system

    assert pretty[0]["content"] == _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    assert "原样带回输入里的 id" in sparse[0]["content"]
    assert "bvid" not in sparse[0]["content"]
    assert "content_id" not in sparse[0]["content"]


def test_content_evaluation_clock_keeps_exact_time_and_utc_hour_bucket() -> None:
    assert content_evaluation_clock(now=datetime(2026, 8, 4, 9, 47, 31, 123456, tzinfo=UTC)) == (
        "2026-08-04T09:47:31Z",
        "2026-08-04T09:00:00Z",
    )


def test_content_evaluation_prompts_skip_low_score_reason() -> None:
    """Both eval system prompts bake the 0.5 skip floor + ≤30字 cap (static).

    Reason-diet contract (v0.3.171): ``score`` strictly below the fixed 0.5
    floor writes an empty ``reason`` (pure waste — never admitted); the rest get
    one internal diagnostic capped at 30 Unicode code points. The floor is baked
    constant text, not a per-call value.
    """
    single_system = build_content_evaluation_prompt(
        profile_summary={"interests": ["音乐"]},
        content_summary={"title": "匿名视频"},
    )[0]["content"]
    batch_system = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["音乐"]},
        content_items=[{"content_id": "x", "title": "匿名视频"}],
    )[0]["content"]

    for system in (single_system, batch_system):
        assert "严格低于 0.5" in system
        assert "必须写成空串" in system
        assert "不超过 30 个 Unicode 字符" in system
        assert "内部诊断" in system
        assert "直接展示给用户" not in system


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

    user = messages[1]["content"]

    assert "<source_platform>\n\nmixed\n\n</source_platform>" in user
    assert '"source_platform": "bilibili"' in user


def test_batch_content_evaluation_prompt_explains_engagement_metrics() -> None:
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["systems"]},
        source_platform="mixed",
        source_context="mixed",
        content_items=[
            {
                "content_id": "xhs1",
                "source_platform": "xiaohongshu",
                "title": "XHS item",
                "view_count": 100,
                "like_count": 10,
                "collect_count": 9,
                "tags": ["coffee"],
            }
        ],
    )

    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "互动指标" in system
    assert "不能覆盖内容与画像的真实匹配度" in system
    assert '"collect_count": 9' in user
    assert '"tags": [' in user
    assert '"source_platform": "xiaohongshu"' in user
    assert "Do not lower or raise preference score merely because" in system


def test_batch_content_evaluation_prompt_explains_cover_image_refs() -> None:
    messages = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["visual analysis"]},
        source_platform="mixed",
        source_context="mixed",
        content_items=[
            {
                "content_id": "yt-demo",
                "source_platform": "youtube",
                "title": "Visual item",
                "cover_image_ref": "cover:yt-demo",
            },
            {
                "content_id": "x-text",
                "source_platform": "twitter",
                "content_type": "tweet",
                "title": "Text-only item",
            },
        ],
    )

    system = messages[0]["content"]
    user = messages[1]["content"]

    assert "cover_image_ref" in system
    assert "cover:<content_id>" in system
    assert "没有 cover_image_ref" in system
    assert "只按文本字段判断" in system
    assert '"cover_image_ref": "cover:yt-demo"' in user


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
            "build_awareness_with_confusions_prompt",
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
            "build_posture_gate_prompt",
            dict(
                change={"kind": "value", "content": "追求效率"},
                core_memory={"a": 1},
                ledger_digest=[{"write_point": "values", "outcome": "success"}],
            ),
            dict(
                change={"kind": "goal", "content": "想转行"},
                core_memory={"a": 2},
                ledger_digest=[{"write_point": "core", "outcome": "failed"}],
            ),
        ),
        (
            "build_dialogue_insight_prompt",
            dict(
                user_message="我最近在玩桌游",
                assistant_reply="听起来不错",
                core_memory={"a": 1},
                active_list={"speculations": [{"domain": "桌游"}]},
                anchor={
                    "kind": "hypothesis",
                    "ref": "abcd1234",
                    "text": "用户喜欢桌游",
                    "generation": 1,
                },
            ),
            dict(
                user_message="不想再看带货了",
                assistant_reply="明白",
                core_memory={"a": 2},
                active_list={"insights": [{"hash": "abcd1234", "hypothesis": "H"}]},
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
            "build_probe_sentiment_prompt",
            dict(domain="桌游", user_message="先放着吧"),
            dict(domain="城市摄影", user_message="以后多推这个"),
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
        (
            "build_profile_consolidation_prompt",
            dict(
                likes_clusters=[
                    {"cluster_id": "L1", "members": [{"name": "智能体开发", "weight": 0.97}]}
                ],
                dislikes_clusters=[],
            ),
            dict(
                likes_clusters=[],
                dislikes_clusters=[{"cluster_id": "D1", "members": ["雷点A", "雷点B"]}],
            ),
        ),
        (
            "build_preference_analysis_prompt",
            dict(events=[{"event_type": "view", "title": "A"}], existing_preference={"a": 1}),
            dict(events=[{"event_type": "like", "title": "B"}], existing_preference={"a": 2}),
        ),
        (
            "build_soul_profile_prompt",
            dict(
                history_summary={"recent_topics": ["A"]},
                preference_summary={"interests": ["A"]},
                recent_awareness=[],
                active_insights=[],
                tone_profile=None,
                source_platform_mix={"bilibili": 1.0},
            ),
            dict(
                history_summary={"recent_topics": ["B"]},
                preference_summary={"interests": ["B"]},
                recent_awareness=[{"note": "B"}],
                active_insights=[{"hypothesis": "B"}],
                tone_profile={
                    "density": "dense",
                    "warmth": "warm",
                    "playfulness": "medium",
                    "directness": "balanced",
                },
                source_platform_mix={"xiaohongshu": 1.0},
            ),
        ),
        (
            "build_category_mapping_prompt",
            dict(categories=[{"category": "泛娱乐", "tag_count": 12}]),
            dict(
                categories=[
                    {"category": "内容消费方式", "tag_count": 3},
                    {"category": "宠物", "tag_count": 7},
                ]
            ),
        ),
        (
            "build_merged_keywords_prompt",
            dict(
                profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
                platform_blocks=[
                    {
                        "platform": "bilibili",
                        "need": 8,
                        "recent_keywords": ["AI 编程"],
                        "avoid_topics": ["原神"],
                        "avoid_styles": ["deep_dive"],
                        "avoid_franchises": ["原神"],
                    }
                ],
            ),
            dict(
                profile_summary={"interests": [{"name": "咖啡", "weight": 0.6}]},
                platform_blocks=[
                    {
                        "platform": "xiaohongshu",
                        "need": 5,
                        "recent_keywords": ["手冲咖啡"],
                        "avoid_topics": ["美甲"],
                        "avoid_styles": ["lifestyle"],
                        "avoid_franchises": [],
                    },
                    {
                        "platform": "twitter",
                        "need": 4,
                        "recent_keywords": ["llm"],
                        "avoid_topics": [],
                        "avoid_styles": [],
                        "avoid_franchises": [],
                    },
                ],
            ),
        ),
        (
            "build_inspiration_axis_keyword_prompt",
            dict(
                profile_digest={"interests": ["游戏评价"]},
                platform_guides={"bilibili": {"query_style": ["拆解", "测评"]}},
                selected_interests=[{"label": "游戏评价", "parent": "游戏", "weight": 0.9}],
                existing_axes=[
                    {
                        "axis_id": "axis:mechanics",
                        "interest": "游戏评价",
                        "axis_label": "机制拆解",
                        "axis_kind": "creator_lens",
                    }
                ],
                fresh_evidence=[
                    {
                        "interest": "游戏评价",
                        "title": "忍义手设计理念",
                        "url": "https://example.test/a",
                    }
                ],
                allocation_targets={"游戏评价": {"platforms": ["bilibili"], "min_axes": 2}},
                # E2: with an explore_request block (per-call data in the user
                # message). args2 omits it — the system message must stay
                # byte-identical across both, proving explore adds no system data.
                explore_request={"avoid_covered": ["游戏", "动漫"]},
            ),
            dict(
                profile_digest={"interests": ["咖啡器具"]},
                platform_guides={"youtube": {"query_style": ["review", "explained"]}},
                selected_interests=[{"label": "咖啡器具", "parent": "生活", "weight": 0.7}],
                existing_axes=[
                    {
                        "axis_id": "axis:gear",
                        "interest": "咖啡器具",
                        "axis_label": "器具对比",
                        "axis_kind": "artifact",
                    }
                ],
                fresh_evidence=[
                    {
                        "interest": "咖啡器具",
                        "title": "手磨对比",
                        "url": "https://example.test/b",
                    }
                ],
                allocation_targets={"咖啡器具": {"platforms": ["youtube"], "min_axes": 1}},
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


def test_soul_profile_prompt_orders_stable_context_before_history() -> None:
    """The profile-build call has a huge history block; keep it last.

    Provider prompt caches only match a continuous prefix. Tone/source mix and
    preference summary are more stable than raw history, awareness, and insight
    evidence, so they must appear before the changing history payload.
    """
    messages = build_soul_profile_prompt(
        history_summary={"recent_topics": ["国际新闻"]},
        preference_summary={"interests": ["国际关系"]},
        recent_awareness=[{"note": "最近更偏深度内容"}],
        active_insights=[{"hypothesis": "通过深度内容获得掌控感"}],
        tone_profile={
            "density": "dense",
            "warmth": "warm",
            "playfulness": "medium",
            "directness": "balanced",
        },
        source_platform_mix={"bilibili": 0.5, "xiaohongshu": 0.5},
    )
    user_prompt = messages[1]["content"]

    tone_idx = user_prompt.index("<tone_profile>")
    preference_idx = user_prompt.index("<preference_summary>")
    awareness_idx = user_prompt.index("<recent_awareness>")
    insights_idx = user_prompt.index("<active_insights>")
    history_idx = user_prompt.index("<history_summary>")

    assert tone_idx < preference_idx < awareness_idx < insights_idx < history_idx


def test_soul_profile_prompt_serialization_is_deterministic() -> None:
    messages_a = build_soul_profile_prompt(
        history_summary={"b": 2, "a": 1},
        preference_summary={"style": {"depth_preference": 0.8}, "interests": ["AI"]},
        recent_awareness=[{"z": 2, "a": 1}],
        active_insights=[{"hypothesis": "H", "confidence": 0.6}],
        tone_profile=None,
        source_platform_mix={"xiaohongshu": 0.5, "bilibili": 0.5},
    )
    messages_b = build_soul_profile_prompt(
        history_summary={"a": 1, "b": 2},
        preference_summary={"interests": ["AI"], "style": {"depth_preference": 0.8}},
        recent_awareness=[{"a": 1, "z": 2}],
        active_insights=[{"confidence": 0.6, "hypothesis": "H"}],
        tone_profile=None,
        source_platform_mix={"bilibili": 0.5, "xiaohongshu": 0.5},
    )

    assert messages_a[1]["content"] == messages_b[1]["content"]


def test_profile_consolidation_prompt_prefers_concise_representative_names() -> None:
    messages = build_profile_consolidation_prompt(likes_clusters=[], dislikes_clusters=[])
    system_prompt = messages[0]["content"]

    assert "优先选择能准确" in system_prompt
    assert "覆盖整组的简洁旧 member" in system_prompt
    assert "不得为了看似完整而堆砌近义词" in system_prompt


def test_category_mapping_prompt_user_message_carries_vocab_and_histogram() -> None:
    from openbiliclaw.llm.prompts import build_category_mapping_prompt
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    messages = build_category_mapping_prompt(categories=[{"category": "泛娱乐", "tag_count": 12}])
    system = messages[0]["content"]
    user = messages[1]["content"]

    assert all(term in user for term in CATEGORY_VOCAB)
    assert "泛娱乐" in user
    assert '"tag_count": 12' in user
    assert '"tag_count": 12' not in system
    assert '"mapping"' in system


def test_preference_analysis_system_prompt_contains_full_vocab() -> None:
    from openbiliclaw.llm.prompts import build_preference_analysis_prompt
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    messages = build_preference_analysis_prompt(events=[], existing_preference={})
    system = messages[0]["content"]

    assert all(term in system for term in CATEGORY_VOCAB)
    assert "category 必须" in system


def test_prompt_builders_strip_the_interest_decay_cursor() -> None:
    """``last_decay_at`` is storage bookkeeping, not prompt input.

    It moves on every merge and says nothing about user behaviour, so it must
    not spend prompt budget, shift the preference analyzer's
    ``max_prompt_chars`` chunking decision, or perturb prompt-cache prefixes.
    """
    from openbiliclaw.llm.prompts import (
        build_awareness_prompt,
        build_awareness_with_confusions_prompt,
        build_insight_prompt,
        build_preference_analysis_prompt,
        build_soul_profile_prompt,
        render_preference_summary,
    )

    interest = {
        "name": "滑雪",
        "category": "体育",
        "weight": 0.8,
        "first_seen": "2026-09-01T00:00:00",
        "last_seen": "2026-09-01T00:00:00",
        "source": "browse",
    }
    persisted = {"interests": [{**interest, "last_decay_at": "2026-09-08T00:00:00"}]}
    cleaned = {"interests": [interest]}
    events = [{"event_type": "view", "title": "标题"}]

    for view in ("legacy", "compact-v1"):
        messages = [
            build_preference_analysis_prompt(
                events=events,
                existing_preference=persisted,
                input_view=view,
            ),
            build_awareness_prompt(
                events=events,
                preference_summary=persisted,
                soul_profile={},
                input_view=view,
            ),
            build_awareness_with_confusions_prompt(
                events=events,
                preference_summary=persisted,
                soul_profile={},
                input_view=view,
            ),
            build_insight_prompt(
                awareness_notes=[],
                preference_summary=persisted,
                soul_profile={},
                input_view=view,
            ),
        ]
        baselines = [
            build_preference_analysis_prompt(
                events=events,
                existing_preference=cleaned,
                input_view=view,
            ),
            build_awareness_prompt(
                events=events,
                preference_summary=cleaned,
                soul_profile={},
                input_view=view,
            ),
            build_awareness_with_confusions_prompt(
                events=events,
                preference_summary=cleaned,
                soul_profile={},
                input_view=view,
            ),
            build_insight_prompt(
                awareness_notes=[],
                preference_summary=cleaned,
                soul_profile={},
                input_view=view,
            ),
        ]

        assert messages == baselines
        assert "last_decay_at" not in json.dumps(messages, ensure_ascii=False)

    soul_messages = build_soul_profile_prompt(
        history_summary={},
        preference_summary=persisted,
        tone_profile=None,
    )
    assert soul_messages == build_soul_profile_prompt(
        history_summary={},
        preference_summary=cleaned,
        tone_profile=None,
    )
    assert "last_decay_at" not in json.dumps(soul_messages, ensure_ascii=False)

    assert render_preference_summary(persisted) == render_preference_summary(cleaned)
    assert "last_decay_at" not in render_preference_summary(persisted)


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


def test_batch_eval_system_uses_json_object_results_wrapper() -> None:
    system = _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT

    assert "严格 JSON 对象" in system
    assert '"results" 数组' in system
    assert '"results": [' in system


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


def test_content_evaluation_prompts_use_viewing_mode_style_keys() -> None:
    single = build_content_evaluation_prompt(
        profile_summary={"interests": ["系统设计"]},
        content_summary={"title": "讲透复杂系统"},
        source_context="search",
        source_platform="bilibili",
    )[0]["content"]
    batch = _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT

    for system in (single, batch):
        assert "style_key(13选1)" in system
        for style_key in VALID_STYLE_KEYS:
            assert style_key in system
        assert "deep_dive / fun_variety / lifestyle" not in system
        assert "11 个选项" not in system


def test_prompt_builders_normalize_legacy_style_keys_in_user_payload() -> None:
    single_eval = build_content_evaluation_prompt(
        profile_summary={"interests": ["系统设计"]},
        content_summary={"title": "讲透复杂系统", "style_key": "deep_dive"},
        source_context="search",
        source_platform="bilibili",
    )[1]["content"]
    batch_eval = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["系统设计"]},
        content_items=[{"title": "城市纪录片", "style_key": "story_doc"}],
        source_context="search",
        source_platform="bilibili",
    )[1]["content"]
    single_expression = build_recommendation_expression_prompt(
        profile_summary={"interests": ["生活"]},
        content_summary={"title": "通勤穿搭", "style_key": "lifestyle"},
        tone_profile=None,
        source_platform="xiaohongshu",
    )[1]["content"]
    batch_expression = build_batch_expression_prompt(
        profile_summary={"interests": ["游戏"]},
        content_items=[{"title": "配队攻略", "style_key": "game_strategy"}],
        tone_profile=None,
        source_platform="bilibili",
    )[1]["content"]

    combined = "\n".join(
        [
            single_eval,
            batch_eval,
            single_expression,
            batch_expression,
        ]
    )

    for legacy_key in ("deep_dive", "story_doc", "lifestyle", "game_strategy"):
        assert legacy_key not in combined
    for canonical_key in (
        "deep_focus",
        "story_immersion",
        "daily_wander",
        "hands_on",
    ):
        assert canonical_key in combined


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


def test_search_keyword_prompts_normalize_legacy_avoid_styles() -> None:
    search_user = build_search_queries_prompt(
        profile_summary={"interests": ["AI"]},
        pool_hints={
            "avoid_topics": ["人工智能"],
            "avoid_styles": ["deep_dive", "story_doc", "not_real"],
        },
    )[1]["content"]
    merged_user = build_merged_keywords_prompt(
        profile_summary={"interests": ["AI"]},
        platform_blocks=[
            {
                "platform": "bilibili",
                "need": 2,
                "recent_keywords": [],
                "avoid_topics": ["人工智能"],
                "avoid_styles": ["lifestyle", "fun_variety", "not_real"],
                "avoid_franchises": [],
            }
        ],
    )[1]["content"]

    assert "deep_focus" in search_user
    assert "story_immersion" in search_user
    assert "deep_dive" not in search_user
    assert "story_doc" not in search_user
    assert "daily_wander" in merged_user
    assert "mood_release" in merged_user
    assert "lifestyle" not in merged_user
    assert "fun_variety" not in merged_user
    assert "not_real" not in search_user
    assert "not_real" not in merged_user


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


_TEMPORAL_EXPRESSION_CONTENT = {
    "title": "2024 年的老片重映",
    "up_name": "某UP",
    "published_at": "2024-05-01T12:00:00Z",
    "published_label": "2024-05-01",
    "evaluated_at": "2026-01-02T03:04:05Z",
}


def test_recommendation_expression_prompt_carries_temporal_grounding() -> None:
    messages = build_recommendation_expression_prompt(
        profile_summary={"a": 1},
        content_summary=dict(_TEMPORAL_EXPRESSION_CONTENT),
        tone_profile=None,
        source_platform="bilibili",
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "2024-05-01T12:00:00Z" in user
    assert "2024-05-01" in user
    assert "2026-01-02T03:04:05Z" in user
    # The evaluation clock must never leak into the static cached prefix.
    assert "2024-05-01T12:00:00Z" not in system
    assert "2026-01-02T03:04:05Z" not in system
    # Static rule: only the supplied fields may be used for freshness.
    assert "content_summary.evaluated_at" in system
    assert "评估这条内容时的权威当前时间" in system


def test_batch_expression_prompt_carries_per_item_temporal_grounding() -> None:
    messages = build_batch_expression_prompt(
        profile_summary={"a": 1},
        content_items=[dict(_TEMPORAL_EXPRESSION_CONTENT)],
        tone_profile=None,
        source_platform="bilibili",
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "2024-05-01T12:00:00Z" in user
    assert "2026-01-02T03:04:05Z" in user
    assert "2024-05-01T12:00:00Z" not in system
    assert "2026-01-02T03:04:05Z" not in system
    assert "evaluated_at" in system
    assert "评估该条内容时的权威当前时间" in system


# ----------------------------------------------------------------------
# Discover backpressure P1.4: merged multi-platform keyword builder + parser.


def _merged_platform_blocks() -> list[dict[str, object]]:
    return [
        {
            "platform": "bilibili",
            "need": 8,
            "recent_keywords": ["AI 编程 盘点"],
            "avoid_topics": ["原神"],
            "avoid_styles": ["deep_dive"],
            "avoid_franchises": ["原神"],
        },
        {
            "platform": "xiaohongshu",
            "need": 5,
            "recent_keywords": ["手冲咖啡 入门"],
            "avoid_topics": ["美甲教程"],
            "avoid_styles": ["lifestyle"],
            "avoid_franchises": [],
        },
    ]


def test_merged_keywords_prompt_system_message_equals_constant() -> None:
    """The system message must be the literal module constant — no
    interpolation — so the provider prompt cache fires across calls."""
    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
        platform_blocks=_merged_platform_blocks(),
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == _MERGED_KEYWORDS_SYSTEM_PROMPT


def test_merged_keywords_prompt_user_message_carries_profile_once_and_due_platforms() -> None:
    """User message holds <profile_summary> exactly once plus only the
    platforms passed in — absent platforms must not leak into the prompt."""
    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
        platform_blocks=_merged_platform_blocks(),
    )
    user = messages[1]["content"]

    assert user.count("<profile_summary>") == 1
    assert user.count("</profile_summary>") == 1
    assert "<platforms>" in user
    # Only the two due platforms appear; absent platforms do not.
    assert "bilibili" in user
    assert "xiaohongshu" in user
    for absent in ("douyin", "youtube", "twitter", "zhihu", "reddit"):
        assert absent not in user
    # The avoid_* hints and recent_keywords ride along in the user message.
    assert "AI 编程 盘点" in user
    assert "手冲咖啡 入门" in user
    assert "原神" in user


def test_merged_keywords_prompt_can_request_explore_domains() -> None:
    messages = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
        platform_blocks=_merged_platform_blocks(),
        explore_domains_block={
            "need_domains": 5,
            "queries_per_domain": 3,
            "covered_topic_groups": ["AI 编程", "认知科学"],
        },
    )
    user = messages[1]["content"]

    assert "<explore_domains>" in user
    assert '"covered_topic_groups"' in user
    assert "AI 编程" in user
    assert "探索" in messages[0]["content"]
    assert "explore_domains" in messages[0]["content"]


def test_merged_keywords_prompt_serialization_is_deterministic() -> None:
    """Differently-ordered dict keys with identical semantics must yield a
    byte-identical user message (sort_keys=True discipline)."""
    blocks_a = [
        {
            "platform": "bilibili",
            "need": 8,
            "recent_keywords": ["x"],
            "avoid_topics": ["原神"],
            "avoid_styles": [],
            "avoid_franchises": [],
        }
    ]
    blocks_b = [
        {
            "avoid_franchises": [],
            "avoid_styles": [],
            "avoid_topics": ["原神"],
            "recent_keywords": ["x"],
            "need": 8,
            "platform": "bilibili",
        }
    ]
    msg_a = build_merged_keywords_prompt(
        profile_summary={"interests": ["AI"], "disliked_topics": ["标题党"]},
        platform_blocks=blocks_a,
    )
    msg_b = build_merged_keywords_prompt(
        profile_summary={"disliked_topics": ["标题党"], "interests": ["AI"]},
        platform_blocks=blocks_b,
    )

    assert msg_a[1]["content"] == msg_b[1]["content"]


def test_parse_merged_keywords_parses_per_platform() -> None:
    content = '{"bilibili": ["历史 盘点", "摄影 入门"], "xiaohongshu": ["手冲咖啡 教程"]}'
    parsed = parse_merged_keywords(content, ["bilibili", "xiaohongshu"], per_platform_cap=10)

    assert parsed["bilibili"] == ["历史 盘点", "摄影 入门"]
    assert parsed["xiaohongshu"] == ["手冲咖啡 教程"]


def test_parse_merged_keywords_returns_key_for_every_requested_platform() -> None:
    """Every requested platform gets a key, even ones absent from output."""
    content = '{"bilibili": ["a", "b"]}'
    parsed = parse_merged_keywords(
        content, ["bilibili", "xiaohongshu", "douyin"], per_platform_cap=10
    )

    assert set(parsed) == {"bilibili", "xiaohongshu", "douyin"}
    assert parsed["bilibili"] == ["a", "b"]
    # Missing platform → empty list, not absent key.
    assert parsed["xiaohongshu"] == []
    assert parsed["douyin"] == []


def test_parse_merged_keywords_missing_and_garbage_platform_empty_no_raise() -> None:
    """A platform whose value is non-list garbage yields [] and never raises."""
    content = '{"bilibili": ["ok"], "xiaohongshu": "not a list", "douyin": 42}'
    parsed = parse_merged_keywords(
        content, ["bilibili", "xiaohongshu", "douyin"], per_platform_cap=10
    )

    assert parsed["bilibili"] == ["ok"]
    assert parsed["xiaohongshu"] == []
    assert parsed["douyin"] == []


def test_parse_merged_keywords_total_garbage_does_not_raise() -> None:
    """Non-JSON / non-object content → all-empty result, no exception."""
    for junk in ("not json at all", "", "[1, 2, 3]", "null"):
        parsed = parse_merged_keywords(junk, ["bilibili", "twitter"], per_platform_cap=5)
        assert parsed == {"bilibili": [], "twitter": []}


def test_parse_merged_keywords_partial_truncated_json_salvages() -> None:
    """Tolerant parse recovers a platform from a truncated payload."""
    content = '{"bilibili": ["历史 盘点", "摄影 入门"], "xiaohongshu": ["手冲咖'
    parsed = parse_merged_keywords(content, ["bilibili", "xiaohongshu"], per_platform_cap=10)

    assert parsed["bilibili"] == ["历史 盘点", "摄影 入门"]


def test_parse_merged_keywords_respects_per_platform_cap() -> None:
    content = '{"bilibili": ["a", "b", "c", "d", "e"]}'
    parsed = parse_merged_keywords(content, ["bilibili"], per_platform_cap=3)

    assert parsed["bilibili"] == ["a", "b", "c"]


def test_parse_merged_keywords_dedups_within_platform() -> None:
    content = '{"bilibili": ["a", "a", "b", " b ", "b", "c"]}'
    parsed = parse_merged_keywords(content, ["bilibili"], per_platform_cap=10)

    # "a" deduped; "b" and " b " both strip to "b" so only one kept.
    assert parsed["bilibili"] == ["a", "b", "c"]


def test_parse_merged_keywords_drops_blank_and_non_scalar_items() -> None:
    content = '{"bilibili": ["", "  ", "good", {"x": 1}, ["y"], "另一个"]}'
    parsed = parse_merged_keywords(content, ["bilibili"], per_platform_cap=10)

    assert parsed["bilibili"] == ["good", "另一个"]


def test_parse_merged_keywords_zero_cap_returns_empty_lists() -> None:
    content = '{"bilibili": ["a", "b"]}'
    parsed = parse_merged_keywords(content, ["bilibili"], per_platform_cap=0)

    assert parsed == {"bilibili": []}


# ----------------------------------------------------------------------
# Discover backpressure P2.1: static per-platform supply-advantage table.


def test_merged_keywords_system_prompt_carries_supply_advantage_table() -> None:
    """The static system prompt embeds the per-platform supply-advantage block
    (P2.1) — each platform mapped to where it structurally has good content."""
    sys_prompt = _MERGED_KEYWORDS_SYSTEM_PROMPT

    assert "<supply_advantage>" in sys_prompt
    assert "</supply_advantage>" in sys_prompt
    # Each platform's headline supply advantages from the spec are present.
    assert "学习区" in sys_prompt and "知识科普" in sys_prompt  # bilibili
    assert "生活方式" in sys_prompt and "美妆" in sys_prompt  # xiaohongshu
    assert "热点" in sys_prompt and "搞笑" in sys_prompt  # douyin
    assert "英文长内容" in sys_prompt and "纪录片" in sys_prompt  # youtube
    assert "实时讨论" in sys_prompt and "英文技术" in sys_prompt  # twitter
    assert "公开代码仓库" in sys_prompt and "开源工具" in sys_prompt  # github
    assert "github" in sys_prompt
    assert "知乎" in sys_prompt and "回答" in sys_prompt  # zhihu
    assert "zhihu" in sys_prompt
    assert "subreddit" in sys_prompt and "经验讨论" in sys_prompt  # reddit
    assert "reddit" in sys_prompt
    assert "动画 / 书籍 / 游戏" in sys_prompt  # bangumi catalog
    assert "bangumi" in sys_prompt


def test_merged_keywords_system_prompt_permits_decline() -> None:
    """The static system prompt instructs the model it MAY return fewer / an
    empty list for a platform whose supply advantage doesn't fit the user
    (P2.2 decline) rather than padding."""
    sys_prompt = _MERGED_KEYWORDS_SYSTEM_PROMPT

    assert "弃权" in sys_prompt
    assert "[]" in sys_prompt


def test_merged_keywords_system_prompt_is_fully_static_supply_table() -> None:
    """The supply-advantage table never depends on per-call data — two builds
    with different profiles / platforms keep a byte-identical system message
    (the call-invariance contract holds with the P2 table added)."""
    msg_a = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "AI", "weight": 0.9}]},
        platform_blocks=[
            {
                "platform": "bilibili",
                "need": 8,
                "recent_keywords": [],
                "avoid_topics": [],
                "avoid_styles": [],
                "avoid_franchises": [],
            }
        ],
    )
    msg_b = build_merged_keywords_prompt(
        profile_summary={"interests": [{"name": "美妆", "weight": 0.4}]},
        platform_blocks=[
            {
                "platform": "twitter",
                "need": 4,
                "recent_keywords": ["x"],
                "avoid_topics": ["y"],
                "avoid_styles": [],
                "avoid_franchises": [],
            }
        ],
    )

    assert msg_a[0]["content"] == msg_b[0]["content"] == _MERGED_KEYWORDS_SYSTEM_PROMPT


# ----------------------------------------------------------------------
# Discover backpressure P2.2: parser distinguishes decline (present-empty)
# from omission (absent / non-list).


def test_parse_merged_keywords_with_presence_marks_explicit_empty_as_present() -> None:
    """A platform whose value is an explicit empty list is PRESENT (an
    intentional decline); an omitted platform is NOT present (an omission)."""
    content = '{"bilibili": ["历史 盘点"], "xiaohongshu": []}'
    keywords, present = parse_merged_keywords_with_presence(
        content, ["bilibili", "xiaohongshu", "douyin"], per_platform_cap=10
    )

    assert keywords["bilibili"] == ["历史 盘点"]
    assert keywords["xiaohongshu"] == []
    assert keywords["douyin"] == []
    # bilibili (had words) and xiaohongshu (explicit []) are present; douyin
    # (absent from the JSON object) is NOT.
    assert present == {"bilibili", "xiaohongshu"}


def test_parse_merged_keywords_with_presence_and_explore_domains() -> None:
    content = """
    {
      "bilibili": ["历史 盘点"],
      "explore_domains": [
        {
          "domain": "城市声音采样",
          "novelty_level": 0.83,
          "queries": ["城市 声音 采样 纪录片", "街头 声音 设计 vlog"]
        },
        {
          "domain": "AI",
          "novelty_level": "not-a-number",
          "queries": ["", "  ", "工业 影像 解说", "工业 影像 解说"]
        }
      ]
    }
    """
    keywords, present, explore_domains = parse_merged_keywords_with_presence_and_explore_domains(
        content,
        ["bilibili", "xiaohongshu"],
        per_platform_cap=10,
        max_explore_domains=5,
        queries_per_domain=3,
    )

    assert keywords["bilibili"] == ["历史 盘点"]
    assert present == {"bilibili"}
    assert explore_domains == [
        {
            "domain": "城市声音采样",
            "novelty_level": 0.83,
            "queries": ["城市 声音 采样 纪录片", "街头 声音 设计 vlog"],
        },
        {
            "domain": "AI",
            "novelty_level": 0.65,
            "queries": ["工业 影像 解说"],
        },
    ]


def test_parse_merged_keywords_with_presence_non_list_is_not_present() -> None:
    """A non-list garbage value is treated as an omission (not present), so the
    planner will fall back rather than read it as a decline."""
    content = '{"bilibili": ["ok"], "xiaohongshu": "not a list", "douyin": 42}'
    keywords, present = parse_merged_keywords_with_presence(
        content, ["bilibili", "xiaohongshu", "douyin"], per_platform_cap=10
    )

    assert keywords["bilibili"] == ["ok"]
    assert present == {"bilibili"}


def test_parse_merged_keywords_with_presence_total_garbage_no_present() -> None:
    """Non-JSON / non-object content → no platform present (all fall back)."""
    for junk in ("not json", "", "[1,2]", "null"):
        keywords, present = parse_merged_keywords_with_presence(
            junk, ["bilibili", "twitter"], per_platform_cap=5
        )
        assert keywords == {"bilibili": [], "twitter": []}
        assert present == set()


def test_parse_merged_keywords_with_presence_zero_cap_no_present() -> None:
    """With cap 0 nothing is parsed → no platform is present."""
    keywords, present = parse_merged_keywords_with_presence(
        '{"bilibili": ["a"]}', ["bilibili"], per_platform_cap=0
    )
    assert keywords == {"bilibili": []}
    assert present == set()


def test_parse_merged_keywords_still_collapses_present_and_absent() -> None:
    """The legacy ``parse_merged_keywords`` keeps its presence-agnostic shape:
    present-empty and absent both yield ``[]`` (back-compat for old callers)."""
    content = '{"bilibili": ["a"], "xiaohongshu": []}'
    parsed = parse_merged_keywords(
        content, ["bilibili", "xiaohongshu", "douyin"], per_platform_cap=10
    )
    assert parsed == {"bilibili": ["a"], "xiaohongshu": [], "douyin": []}


def test_inspiration_axis_system_prompt_requires_specific_core_concept() -> None:
    """F1 (Phase 2.1): the inspiration axis-keyword system prompt must carry a
    static rule forcing ``core_concept`` to anchor on a specific
    entity/event/work/person/mechanism from ``fresh_evidence`` — never a mere
    restatement of the interest or axis_label — with a topic-level fallback only
    when no anchor exists, plus at least one explicit bad/good counter-example.
    """
    prompt = prompt_module._INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT

    lowered = prompt.lower()
    # Anchors on a specific evidence entity, not the topic name.
    assert "core_concept" in prompt
    assert "fresh_evidence" in prompt
    assert "anchor" in lowered
    # Must forbid restating the interest / axis_label.
    assert "axis_label" in prompt
    assert "interest" in prompt
    # Explicit counter-examples: bad restatement vs good specific anchor.
    assert "新游推荐" in prompt  # bad: echoes the topic name
    assert "士官长 登陆PS5" in prompt  # good: specific evidence anchor
    # Topic-level fallback escape hatch must exist (no hallucinated proper nouns).
    assert "fall back" in lowered or "fallback" in lowered


def test_inspiration_axis_system_prompt_requires_crossdomain_specific_on_explore() -> None:
    """E2 (Phase 2.3): the inspiration axis-keyword system prompt must carry a
    STATIC rule for cross-domain explore rounds — when the user message includes
    an ``explore_request``, core_concept must anchor on an UNCOVERED-but-relevant
    cross-domain specific entity and avoid the topics in
    ``explore_request.avoid_covered`` — with a bad/good counter-example. The rule
    is always present (static); only the explore_request DATA is per-call.
    """
    prompt = prompt_module._INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT
    lowered = prompt.lower()

    # References the per-call explore_request block + its avoid_covered field.
    assert "explore_request" in prompt
    assert "avoid_covered" in prompt
    # Cross-domain, uncovered-but-relevant intent.
    assert "cross-domain" in lowered
    assert "uncovered" in lowered
    # Bad (covered/same-domain) vs good (uncovered cross-domain) counter-example.
    assert "游戏新作" in prompt  # bad: stays in the covered domain
    assert "詹姆斯韦伯 深空图像" in prompt  # good: uncovered cross-domain anchor


class TestPreferencePromptCognitionContext:
    """第一条线的兴趣更新带认知语境；其它调用方字节不变。"""

    def test_omitting_context_is_byte_identical_to_the_old_builder(self) -> None:
        """init 分片 / 反馈批不传语境，prompt 必须一字不变（回放不变性）。"""
        from openbiliclaw.llm.prompts import build_preference_analysis_prompt

        events = [{"event_type": "view", "title": "标题"}]
        preference = {"interests": []}
        without = build_preference_analysis_prompt(events=events, existing_preference=preference)
        explicit_none = build_preference_analysis_prompt(
            events=events,
            existing_preference=preference,
            awareness_notes=None,
            active_insights=None,
        )

        assert without == explicit_none
        assert "<recent_awareness>" not in without[1]["content"]
        assert "<active_insights>" not in without[1]["content"]

    def test_context_sections_sit_between_preference_and_events(self) -> None:
        """顺序 稳定→易变：偏好、认知语境、事件批，保住 provider 缓存前缀。"""
        from openbiliclaw.llm.prompts import build_preference_analysis_prompt

        messages = build_preference_analysis_prompt(
            events=[{"event_type": "view", "title": "标题"}],
            existing_preference={"interests": []},
            awareness_notes=[{"observation": "最近在深挖 Rust 底层"}],
            active_insights=[{"hypothesis": "可能是系统编程从业者", "confidence": 0.7}],
        )
        body = messages[1]["content"]

        assert body.index("<existing_preference>") < body.index("<recent_awareness>")
        assert body.index("<recent_awareness>") < body.index("<active_insights>")
        assert body.index("<active_insights>") < body.index("<event_batch>")
        assert "最近在深挖 Rust 底层" in body
        assert "可能是系统编程从业者" in body


class TestReplyStyleInjection:
    """issue #255: ``soul.reply_style`` appends one tone-block line; empty stays byte-identical."""

    _TONE = {
        "density": "dense",
        "warmth": "warm",
        "playfulness": "medium",
        "directness": "balanced",
    }

    def test_render_tone_profile_empty_reply_style_is_byte_identical(self) -> None:
        from openbiliclaw.llm.prompts import _render_tone_profile

        baseline = _render_tone_profile(self._TONE)
        assert baseline == _render_tone_profile(self._TONE, reply_style="")
        assert baseline == _render_tone_profile(self._TONE, reply_style="  \n\t ")
        assert "- 回复风格:" not in baseline

    def test_render_tone_profile_appends_single_collapsed_line(self) -> None:
        from openbiliclaw.llm.prompts import _render_tone_profile

        rendered = _render_tone_profile(self._TONE, reply_style="语气温和\n\n少用  梗")

        assert rendered.endswith("\n- 回复风格: 语气温和 少用 梗")
        assert rendered.count("- 回复风格:") == 1

    def test_dialogue_prompt_empty_reply_style_is_byte_identical(self) -> None:
        kwargs = {
            "user_message": "我最近有点迷上纪录片",
            "core_memory_text": "",
            "tone_profile": self._TONE,
            "history": [],
        }

        assert build_socratic_dialogue_prompt(**kwargs) == build_socratic_dialogue_prompt(
            **kwargs, reply_style=""
        )

    def test_dialogue_prompt_injects_reply_style(self) -> None:
        messages = build_socratic_dialogue_prompt(
            user_message="我最近有点迷上纪录片",
            core_memory_text="",
            tone_profile=self._TONE,
            history=[],
            reply_style="像损友一样毒舌",
        )

        assert "- 回复风格: 像损友一样毒舌" in messages[0]["content"]

    def test_soul_profile_prompt_empty_reply_style_is_byte_identical(self) -> None:
        kwargs = {
            "history_summary": {"total": 3},
            "preference_summary": {"interests": []},
            "tone_profile": self._TONE,
        }

        assert build_soul_profile_prompt(**kwargs) == build_soul_profile_prompt(
            **kwargs, reply_style=""
        )

    def test_soul_profile_prompt_injects_reply_style_into_tone_block(self) -> None:
        messages = build_soul_profile_prompt(
            history_summary={"total": 3},
            preference_summary={"interests": []},
            tone_profile=self._TONE,
            reply_style="简洁直接",
        )
        user_prompt = messages[1]["content"]

        tone_block = user_prompt.split("<tone_profile>", 1)[1].split("</tone_profile>", 1)[0]
        assert "- 回复风格: 简洁直接" in tone_block

    def test_recommendation_expression_prompt_empty_reply_style_is_byte_identical(self) -> None:
        kwargs = {
            "profile_summary": {"personality_portrait": "偏好高信息密度内容"},
            "content_summary": {"title": "讲透国际局势", "up_name": "某UP"},
            "tone_profile": self._TONE,
            "source_platform": "bilibili",
        }

        assert build_recommendation_expression_prompt(
            **kwargs
        ) == build_recommendation_expression_prompt(**kwargs, reply_style="")

    def test_recommendation_expression_prompt_injects_reply_style(self) -> None:
        messages = build_recommendation_expression_prompt(
            profile_summary={"personality_portrait": "偏好高信息密度内容"},
            content_summary={"title": "讲透国际局势", "up_name": "某UP"},
            tone_profile=self._TONE,
            source_platform="bilibili",
            reply_style="活泼一点",
        )

        assert "- 回复风格: 活泼一点" in messages[1]["content"]

    def test_batch_expression_prompt_empty_reply_style_is_byte_identical(self) -> None:
        kwargs = {
            "profile_summary": {"core_traits": ["fallback"]},
            "content_items": [{"bvid": "BV1", "title": "候选"}],
            "tone_profile": self._TONE,
            "source_platform": "bilibili",
        }

        assert build_batch_expression_prompt(**kwargs) == build_batch_expression_prompt(
            **kwargs, reply_style=""
        )

    def test_batch_expression_prompt_injects_reply_style(self) -> None:
        messages = build_batch_expression_prompt(
            profile_summary={"core_traits": ["fallback"]},
            content_items=[{"bvid": "BV1", "title": "候选"}],
            tone_profile=self._TONE,
            source_platform="bilibili",
            reply_style="多用短句",
        )

        assert "- 回复风格: 多用短句" in messages[1]["content"]


class TestDialogueTonePromptReplacement:
    """``soul.dialogue_tone_prompt`` replaces the dialogue tone block; empty is byte-identical."""

    _TONE = {
        "density": "dense",
        "warmth": "warm",
        "playfulness": "medium",
        "directness": "balanced",
    }

    def _dialogue_kwargs(self) -> dict[str, object]:
        return {
            "user_message": "我最近有点迷上纪录片",
            "core_memory_text": "",
            "tone_profile": self._TONE,
            "history": [],
        }

    def test_empty_dialogue_tone_prompt_is_byte_identical(self) -> None:
        kwargs = self._dialogue_kwargs()

        baseline = build_socratic_dialogue_prompt(**kwargs)
        assert baseline == build_socratic_dialogue_prompt(**kwargs, dialogue_tone_prompt="")
        assert baseline == build_socratic_dialogue_prompt(**kwargs, dialogue_tone_prompt="  \n\t ")

    def test_non_empty_replaces_tone_block_and_keeps_other_segments(self) -> None:
        custom = "说话像一个爱看纪录片的老朋友：\n- 多用短句\n- 先问动机再给建议"
        baseline = build_socratic_dialogue_prompt(
            **self._dialogue_kwargs(), reply_style="像损友一样毒舌"
        )
        replaced = build_socratic_dialogue_prompt(
            **self._dialogue_kwargs(),
            reply_style="像损友一样毒舌",
            dialogue_tone_prompt=custom,
        )

        baseline_system = baseline[0]["content"]
        replaced_system = replaced[0]["content"]

        # The whole rendered tone block (including the reply_style line) is gone.
        assert "- 信息密度" in baseline_system
        assert "- 回复风格: 像损友一样毒舌" in baseline_system
        assert "- 信息密度" not in replaced_system
        assert "- 情绪温度" not in replaced_system
        assert "- 回复风格:" not in replaced_system
        # The user text appears verbatim (multi-line preserved).
        assert custom in replaced_system
        # Identity / Socratic behaviour / capability boundary / core-memory
        # lead-in segments stay byte-identical.
        for segment in (
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            "请使用苏格拉底式对话风格",
            "能力边界：",
            "以下是当前用户的 core memory，请把它作为理解用户的背景，而不是机械复述：",
        ):
            assert segment in replaced_system

    def test_dialogue_tone_prompt_is_stripped_but_not_collapsed(self) -> None:
        messages = build_socratic_dialogue_prompt(
            **self._dialogue_kwargs(),
            dialogue_tone_prompt="\n  第一行\n  第二行  \n",
        )

        assert "第一行\n  第二行" in messages[0]["content"]

    def test_other_builders_have_no_dialogue_tone_prompt_param(self) -> None:
        import inspect

        for builder in (
            build_soul_profile_prompt,
            build_recommendation_expression_prompt,
            build_batch_expression_prompt,
        ):
            assert "dialogue_tone_prompt" not in inspect.signature(builder).parameters
