"""Prompt builders for LLM-backed tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.soul.tone import ToneProfile


def _render_tone_profile(tone_profile: ToneProfile | None) -> str:
    """Render tone profile guidance for prompt builders."""
    tone = tone_profile or {
        "density": "balanced",
        "warmth": "warm",
        "playfulness": "medium",
        "directness": "balanced",
    }
    return (
        "请保持“老B友”基调：懂 B 站语境，像熟人聊天，不像客服。\n"
        f"- 信息密度: {tone['density']}\n"
        f"- 情绪温度: {tone['warmth']}\n"
        f"- 梗感强度: {tone['playfulness']}\n"
        f"- 直给程度: {tone['directness']}"
    )


def build_socratic_dialogue_prompt(
    *,
    user_message: str,
    core_memory_text: str,
    tone_profile: ToneProfile | None,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build chat messages for Socratic dialogue generation."""
    system_prompt = "\n\n".join(
        [
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            "请使用苏格拉底式对话风格：温和、追问动机、确认理解，但整体更像会接话的老B友，不像客服，也不要像咨询师。",
            _render_tone_profile(tone_profile),
            "以下是当前用户的 core memory，请把它作为理解用户的背景，而不是机械复述：",
            core_memory_text,
        ]
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def render_preference_summary(preference_summary: dict[str, object]) -> str:
    """Render preference summary into stable text."""
    if not preference_summary:
        return "（暂无偏好摘要）"
    return json.dumps(preference_summary, ensure_ascii=False, indent=2)


def build_preference_analysis_prompt(
    *,
    events: list[dict[str, object]],
    existing_preference: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting user preferences from events."""
    system_prompt = """
<task>
你要从一批用户行为事件中提取稳定偏好画像。
</task>

<rules>
1. 只能根据提供的事件推断，不要猜测没有证据的结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 如果证据不足，返回空数组、默认值或较低权重。
4. 兴趣标签控制在 5~15 个以内，weight 在 0~1 之间。
</rules>

<output_schema>
{
  "interests": [{"name": "历史", "category": "知识", "weight": 0.8, "source": "watch history"}],
  "style": {
    "preferred_duration": "long",
    "preferred_pace": "moderate",
    "quality_sensitivity": 0.5,
    "humor_preference": 0.3,
    "depth_preference": 0.9
  },
  "context": {
    "weekday_patterns": "",
    "weekend_patterns": "",
    "time_of_day_patterns": "",
    "session_type": "deep_dive"
  },
  "exploration_openness": 0.6,
  "disliked_topics": ["低质标题党"],
  "favorite_up_users": ["某个UP主"]
}
</output_schema>

<examples>
输入事件里如果多次出现长视频、纪录片、深度讲解，
可以提高 “历史/纪录片/知识” 相关标签和 depth_preference。
</examples>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<existing_preference>",
            json.dumps(existing_preference, ensure_ascii=False, indent=2),
            "</existing_preference>",
            "<event_batch>",
            json.dumps(events, ensure_ascii=False, indent=2),
            "</event_batch>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_soul_profile_prompt(
    *,
    history_summary: dict[str, object],
    preference_summary: dict[str, object],
    recent_awareness: list[dict[str, object]] | None = None,
    active_insights: list[dict[str, object]] | None = None,
    tone_profile: ToneProfile | None,
) -> list[dict[str, str]]:
    """Build a structured prompt for initial soul-profile generation."""
    system_prompt = """
<task>
你要基于用户历史摘要和偏好摘要，生成一份谨慎、温和、像长期观察后的老朋友所写的人格画像。
</task>

<rules>
1. 只能根据给定材料推断，不要做医学化、病理化、断言式结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 人格描述至少 200 个中文字符。
4. core_traits 控制在 3 到 6 条，deep_needs 和 values 保持简洁。
5. 先总结这个人怎么处理信息，再总结他在内容里长期在找什么，最后总结他最近更像处于什么阶段。
6. 不要把兴趣 topic 堆成画像主体；题材、UP 主、作品名最多只举 1 到 2 个例子，
   而且只能当证据，不要当正文主干。
7. 可以参考非临床的认知风格、内在驱动力、阶段状态来组织描述，但不要写理论术语，
   不要写成心理报告、咨询记录或说明书，要像熟人总结这个人的气质和状态。
</rules>

<output_schema>
{
  "personality_portrait": "至少 200 字的自然语言人格描述",
  "core_traits": ["理性", "好奇", "谨慎"],
  "cognitive_style": ["会先看结构", "对证据比较敏感", "偏好把问题讲透"],
  "motivational_drivers": ["建立判断确定性", "持续扩展理解边界"],
  "current_phase": "最近更像在一边吸收高密度信息，一边整理自己的判断框架。",
  "values": ["真实", "成长"],
  "life_stage": "处于探索与积累阶段",
  "deep_needs": ["被理解", "持续成长"]
}
</output_schema>
""".strip()
    system_prompt = "\n\n".join([system_prompt, _render_tone_profile(tone_profile)])
    normalized_awareness = recent_awareness or []
    normalized_insights = active_insights or []
    user_prompt = "\n\n".join(
        [
            "<history_summary>",
            json.dumps(history_summary, ensure_ascii=False, indent=2),
            "</history_summary>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<recent_awareness>",
            json.dumps(normalized_awareness, ensure_ascii=False, indent=2),
            "</recent_awareness>",
            "<active_insights>",
            json.dumps(normalized_insights, ensure_ascii=False, indent=2),
            "</active_insights>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_awareness_prompt(
    *,
    events: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for recent awareness-note generation."""
    system_prompt = """
<task>
你要基于近期用户行为，生成少量谨慎的近期观察笔记。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. observation 只能描述观察到的行为倾向，不要下人格定论。
3. trend 和 emotion_guess 必须使用保守表述。
4. 如果证据不足，可以返回空数组。
</rules>

<output_schema>
[
  {
    "date": "2026-03-08",
    "observation": "最近连续浏览高信息密度内容。",
    "trend": "更偏向深度解释而非轻量消遣。",
    "emotion_guess": "可能处于主动吸收和整理信息的阶段。"
  }
]
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<recent_events>",
            json.dumps(events, ensure_ascii=False, indent=2),
            "</recent_events>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2),
            "</soul_profile>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_insight_prompt(
    *,
    awareness_notes: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for insight-hypothesis generation."""
    system_prompt = """
<task>
你要基于近期觉察、偏好摘要和用户画像，生成谨慎的解释性假设。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. hypothesis 是假设，不是结论，措辞必须保守。
3. 每条必须附 1~3 条 evidence。
4. confidence 保持在 0~1，且不要过高。
</rules>

<output_schema>
[
  {
    "hypothesis": "用户可能通过深度内容获得掌控感。",
    "evidence": ["最近连续浏览高信息密度内容。"],
    "confidence": 0.62
  }
]
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<awareness_notes>",
            json.dumps(awareness_notes, ensure_ascii=False, indent=2),
            "</awareness_notes>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2),
            "</soul_profile>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_search_queries_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for search query generation."""
    system_prompt = """
<task>
你要为 B 站内容发现生成一组可搜索的关键词组合。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. query 必须是适合 B 站搜索的短词或短组合，不要写成长句。
3. 优先组合“兴趣主题 + 内容风格/需求”，避免过泛的词。
4. queries 数量控制在 5 到 10 个。
</rules>

<output_schema>
{
  "queries": ["纪录片 原理", "摄影 构图", "历史 长视频 深度"]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_dialogue_insight_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    core_memory: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting candidate insights from dialogue."""
    system_prompt = """
<task>
你要从一轮用户对话中提取少量高价值的候选理解，用于后续长期画像更新。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. 只提取用户明确表达或高度暗示的稳定信号，不要记录瞬时情绪碎片。
3. kind 只允许: interest, dislike, goal, value, state。
4. confidence 保持保守，0~1。
5. 最多返回 3 条 candidates。
</rules>

<output_schema>
{
  "candidates": [
    {
      "kind": "goal",
      "content": "想更系统地理解国际局势",
      "confidence": 0.84,
      "evidence": "用户明确说想把国际新闻看得更透。"
    }
  ]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<core_memory>",
            json.dumps(core_memory, ensure_ascii=False, indent=2),
            "</core_memory>",
            "<dialogue_turn>",
            json.dumps(
                {
                    "user_message": user_message,
                    "assistant_reply": assistant_reply,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "</dialogue_turn>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_trending_rids_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for selecting relevant Bilibili ranking rids."""
    system_prompt = """
<task>
你要从用户画像中推断最值得关注的 B 站排行榜分区 rid。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. 只返回 3 到 5 个最相关的分区 rid，不包含 0。
3. 如果不确定，优先选择知识、科技、影视、纪录片相关分区。
</rules>

<output_schema>
{
  "rids": [36, 188, 181, 119]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for content relevance evaluation."""
    system_prompt = """
<task>
你要评估一个 B 站内容与这个用户画像的匹配度。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. score 范围必须在 0 到 1 之间。
3. reason 只写一句中文，解释为什么这个人会喜欢或不喜欢这个内容。
4. 不要只说“因为热门”或“因为看过类似的”，要结合用户画像。
</rules>

<output_schema>
{
  "score": 0.78,
  "reason": "这个视频的讲解深度和表达方式更贴近你长期偏好的高信息密度内容。"
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2),
            "</content_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_recommendation_expression_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    tone_profile: ToneProfile | None,
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression."""
    system_prompt = """
<task>
你要像一个真正懂这个人的老B友一样，给出一段推荐这条 B 站内容的话。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. expression 必须是 50 到 150 字的中文口语表达，像朋友私聊，不像算法推荐。
3. expression 要解释“为什么这条内容会对上这个人的胃口”，不要说空话。
4. topic_label 需要是轻度个性化的主题标签，不要只写泛分类词。
5. 避免机械解释腔、广告腔和“根据你的兴趣”“你可能会喜欢”这类算法套话。
</rules>

<output_schema>
{
  "expression": "这条会对上你最近那种想把问题想透的劲头，"
    "它不是热闹型内容，而是会慢慢把结构给你铺开。",
  "topic_label": "你最近那股想把问题想透的劲头"
}
</output_schema>
""".strip()
    system_prompt = "\n\n".join([system_prompt, _render_tone_profile(tone_profile)])
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2),
            "</content_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_explore_domains_prompt(
    *,
    profile_summary: dict[str, object],
) -> list[dict[str, str]]:
    """Build a structured prompt for cross-domain exploration ideas."""
    system_prompt = """
<task>
你要为这个用户设计 3 到 5 个“高相关但有陌生感”的跨领域探索方向。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. domain 不能直接重复用户现有高权重兴趣词。
3. domains 至少覆盖 3 类不同内容方向，
   例如知识解释、现实观察、审美体验、人物叙事、技术机制、社会文化；
   不要都落在同一个抽象轴上。
4. 同一母题的换皮变体最多只能保留 1 个，
   例如“博弈论 / 桌游机制 / 纳什均衡 / 策略模型”这类本质相同的方向不能同时出现。
5. why_it_might_resonate 必须先说明它对应用户的哪种认知需求、
   信息处理偏好或内在驱动力，再解释这种陌生内容为什么仍然可能打动这个人。
6. novelty_level 范围必须在 0.4 到 0.8 之间。
7. 每个 domain 生成 1 到 2 个适合 B 站搜索的 query，不能写抽象句子。
</rules>

<output_schema>
{
  "domains": [
    {
      "domain": "城市空间与建筑叙事",
      "why_it_might_resonate": "你偏好结构清晰、能从具体对象看见更大系统的内容。",
      "novelty_level": 0.62,
      "queries": ["城市 建筑 纪录片", "空间 设计 深度讲解"]
    }
  ]
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
