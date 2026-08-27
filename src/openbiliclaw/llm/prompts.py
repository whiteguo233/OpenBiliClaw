"""Prompt builders for LLM-backed tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.discovery.style_keys import STYLE_KEY_PROMPT_TEXT, normalize_style_key
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.soul.event_prompt_views import (
    build_cognition_event_view_v1,
    normalize_cognition_input_view,
)
from openbiliclaw.soul.profile_views import build_cognition_profile_view_v1

if TYPE_CHECKING:
    from openbiliclaw.soul.tone import ToneProfile


_PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "bilibili": "B 站",
    "xiaohongshu": "小红书",
}


def content_evaluation_clock(*, now: datetime | None = None) -> tuple[str, str]:
    """Return the exact UTC evaluation time and its cache-friendly hour bucket.

    The prompt receives the exact timestamp so current-hour publications never
    look futuristic. The separate hour bucket keeps repeated discovery passes
    cacheable while ensuring a long-lived daemon revisits content as it ages.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    evaluated_at = current.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    evaluation_bucket = (
        current.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    )
    return evaluated_at, evaluation_bucket


def _platform_content_label(source_platform: str) -> str:
    """Return platform-specific content label for prompts."""
    return "B 站内容" if source_platform == "bilibili" else "内容"


def _platform_friend_label(source_platform: str) -> str:
    """Return platform-specific friend label for prompts."""
    return "老B友" if source_platform == "bilibili" else "朋友"


def _platform_display_name(source_platform: str) -> str:
    """Return a human-readable platform name ("B 站" / "小红书")."""
    return _PLATFORM_DISPLAY_NAMES.get(source_platform, "内容")


def _profile_prompt_blocks(
    profile_summary: dict[str, object],
    profile_blocks: list[str] | None = None,
) -> list[str]:
    """Return profile prompt blocks, preferring caller-rendered layers."""

    if profile_blocks:
        return list(profile_blocks)
    return [
        "<profile_summary>",
        json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
        "</profile_summary>",
    ]


def _friend_label_from_mix(source_platform_mix: dict[str, float] | None) -> str:
    """Pick a friend label that fits the user's observed source mix.

    None / empty → bilibili default (back-compat). Single-source uses that
    platform's label. Multi-source collapses to a platform-neutral "熟人"
    so the prompt doesn't lean on one platform's in-group slang.
    """
    if not source_platform_mix:
        return "老B友"
    if len(source_platform_mix) == 1:
        return _platform_friend_label(next(iter(source_platform_mix)))
    return "熟人"


def _tone_context_line(source_platform_mix: dict[str, float] | None) -> str:
    """First line of the tone block — describes which platforms to sound native on."""
    if not source_platform_mix:
        return "请保持“老B友”基调：懂 B 站语境，像熟人聊天，不像客服。"
    if len(source_platform_mix) == 1:
        platform = next(iter(source_platform_mix))
        friend = _platform_friend_label(platform)
        display = _platform_display_name(platform)
        return f"请保持“{friend}”基调：懂 {display} 语境，像熟人聊天，不像客服。"
    top = [
        platform
        for platform, _ in sorted(source_platform_mix.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]
    display_list = " / ".join(_platform_display_name(p) for p in top)
    return (
        f"请保持朋友感基调：这个用户横跨 {display_list}，不同平台的梗都接得住，"
        "但不要把一个站的黑话硬塞进另一个站的语境。像熟人聊天，不像客服。"
    )


def _render_tone_profile(
    tone_profile: ToneProfile | None,
    source_platform_mix: dict[str, float] | None = None,
) -> str:
    """Render tone profile guidance for prompt builders."""
    tone = tone_profile or {
        "density": "balanced",
        "warmth": "warm",
        "playfulness": "low",
        "directness": "direct",
    }
    return (
        _tone_context_line(source_platform_mix) + "\n"
        f"- 信息密度: {tone['density']}\n"
        f"- 情绪温度: {tone['warmth']}\n"
        f"- 梗感强度: {tone['playfulness']}\n"
        f"- 直给程度: {tone['directness']}"
    )


def _normalize_prompt_style_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        style_key = normalize_style_key(item)
        if style_key and style_key not in seen:
            result.append(style_key)
            seen.add(style_key)
    return result


def _normalize_content_style_fields(content: dict[str, object]) -> dict[str, object]:
    normalized = dict(content)
    if "style_key" in normalized:
        normalized["style_key"] = normalize_style_key(normalized.get("style_key"))
    return normalized


def _normalize_pool_hints(pool_hints: dict[str, object] | None) -> dict[str, object]:
    normalized = dict(pool_hints or {})
    if "avoid_styles" in normalized:
        normalized["avoid_styles"] = _normalize_prompt_style_list(normalized.get("avoid_styles"))
    return normalized


def _normalize_platform_blocks(platform_blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized_blocks: list[dict[str, object]] = []
    for block in platform_blocks:
        normalized = dict(block)
        if "avoid_styles" in normalized:
            normalized["avoid_styles"] = _normalize_prompt_style_list(
                normalized.get("avoid_styles")
            )
        normalized_blocks.append(normalized)
    return normalized_blocks


def _normalize_explore_domains_block(block: dict[str, object]) -> dict[str, object]:
    normalized = dict(block)
    try:
        need_domains = int(cast("Any", normalized.get("need_domains", 5)) or 5)
    except (TypeError, ValueError):
        need_domains = 5
    try:
        queries_per_domain = int(cast("Any", normalized.get("queries_per_domain", 3)) or 3)
    except (TypeError, ValueError):
        queries_per_domain = 3
    normalized["need_domains"] = max(1, need_domains)
    normalized["queries_per_domain"] = max(
        1,
        min(3, queries_per_domain),
    )
    covered = normalized.get("covered_topic_groups", [])
    if not isinstance(covered, (list, tuple)):
        covered = []
    seen: set[str] = set()
    unique_covered: list[str] = []
    for item in covered:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique_covered.append(text)
        if len(unique_covered) >= 12:
            break
    normalized["covered_topic_groups"] = unique_covered
    normalized["intent"] = "exploratory_bilibili_queries"
    return normalized


def build_socratic_dialogue_prompt(
    *,
    user_message: str,
    core_memory_text: str,
    tone_profile: ToneProfile | None,
    history: list[dict[str, str]],
    source_platform_mix: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """Build chat messages for Socratic dialogue generation.

    Note (v0.3.28+ cache analysis): unlike content-evaluation builders,
    this one's system prompt does include per-user state (friend label,
    tone, core memory). That looks like cache poisoning at first glance,
    but OpenBiliClaw is single-user — per-user state is stable across
    calls for the same install, so the cache still fires on repeated
    dialogue turns. Multi-user deployments would want to refactor this
    further, but for the current single-user model leaving the system
    prompt user-specific is the simpler and equally-effective approach.

    ``core_memory_text`` is a documented injection seam: it lets tests feed
    a core-memory block directly, but in production the dialogue caller
    passes ``""`` and the real core-memory injection happens downstream in
    ``LLMService.complete_with_core_memory`` (and its ``complete_with_tools``
    sibling), not here. Do not resurrect any per-service core-memory-block
    getattr probe at the dialogue call site.
    """
    friend_label = _friend_label_from_mix(source_platform_mix)
    system_prompt = "\n\n".join(
        [
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            (
                "请使用苏格拉底式对话风格：温和、追问动机、确认理解，"
                f"但整体更像会接话的{friend_label}，不像客服，也不要像咨询师。"
            ),
            (
                "能力边界：系统会在回复后尝试把用户明确、稳定的兴趣和避雷写入"
                "OpenBiliClaw 本地长期画像，并用于后续 OpenBiliClaw 候选过滤；"
                "不要声称这些信息只能留在当前聊天上下文。你不能修改 B 站或其他"
                "内容平台自身的推荐算法，必须把本地推荐与平台推荐区分清楚。"
            ),
            _render_tone_profile(tone_profile, source_platform_mix),
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


def _category_vocab_line() -> str:
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    return "、".join(CATEGORY_VOCAB)


_PREFERENCE_ANALYSIS_SYSTEM_PROMPT = """
<task>
你要从一批用户行为事件中提取稳定偏好画像。
</task>

<rules>
1. 只能根据提供的事件推断，不要猜测没有证据的结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 如果证据不足，返回空数组、默认值或较低权重。
4. 兴趣标签控制在 5~25 个以内，weight 在 0~1 之间。证据充分时可多提，证据不足时宁可少提、给低权重，不要为了凑数编造标签。
5. 所有文本字段（name、context 下的 patterns/session_type、disliked_topics）必须用中文。
   category 必须从以下固定词表中逐字选择，不得发明新分类、不得使用同义变体：
   __CATEGORY_VOCAB__。拿不准归属时用「其他」。
6. favorite_up_users 必须从事件的 up_name 字段原样复制，一个字都不能改。先逐条扫描所有事件收集 up_name 值，再与 existing_preference.favorite_up_users 合并去重。严禁根据话题推测可能的UP主名称。如果本批事件中无 up_name 字段，保留 existing_preference 中的原有列表不变。
7. cognitive_style 描述用户的信息处理偏好（如思维方式、阅读习惯、理解路径），3~5 条，基于观看行为模式推断，不要照搬兴趣标签。
8. 每条事件都自带一个 `context` 字段（v0.3.22+ 起所有源都统一填充），它是该事件的中文自然语言摘要（如"在 B 站收藏了《讲透历史叙事》,作者:历史实验室"或"小红书点赞:手冲咖啡入门 作者:豆子老师"）。**优先把 context 作为人类可读的事件描述**来理解用户行为；同时用 metadata 里的结构化字段（up_name、bvid、folder、source_platform 等）做精确匹配 / 复制。
9. 用户的兴趣信号可能跨平台（B 站 / 小红书 / 等）；通过 metadata.source_platform 区分来源，但兴趣分析本身要把所有平台的信号一视同仁，不要因为来自小红书就降权。
10. 如果事件的 inferred_satisfaction 是 negative，或 metadata.feedback_type 是 dislike / metadata.reaction 是 thumbs_down，表示负向证据。不要把负向事件提取为 interests / favorite_up_users；只能用于 disliked_topics、风格避让或降低相关偏好置信度。
11. metadata.signal_strength 表示该事件作为偏好证据的强度，不是最终 interest.weight。如果存在该字段，优先用它判断证据强弱；最终 weight 仍要结合重复次数、内容一致性、最近性、负向反馈和跨来源一致性。没有 signal_strength 时按事件类型粗略理解：favorite / bookmark / save / collect 是强正向；coin / share 是强正向；like 是明确正向；comment 是主动参与但要看语义；follow / subscription 是长期兴趣信号但偏创作者/频道维度，不能直接等同于每个题材都喜欢；view / history 是弱到中等信号，单条不能推出高权重兴趣，重复出现或与强信号同向时才提高；click 只有足够停留、完播或 positive inferred_satisfaction 时才增强；search 是意图信号不是喜欢信号；hover / scroll / snapshot 只作被动上下文辅助；dialogue 是用户主动聊到，按表达强度判断。负向反馈、dislike、thumbs_down 或 inferred_satisfaction=negative 优先级最高，不能被 signal_strength 抵消。
12. 如果 metadata.feedback_type 是 comment，它是用户对推荐内容的直接反馈和中性反馈容器，不预设正向或负向。必须根据备注、feedback_note、context 中的具体内容判断用户是喜欢、不喜欢，还是仅补充说明：正向才可强化 interests / style；负向只能用于 disliked_topics、风格避让或降低相关偏好置信度；不明确时不要强行改偏好。
12b. 如果事件的 context 结尾带「(已撤销)」标记，或 metadata.retracted 为真，表示用户后来撤销了这次正向行为（取消赞 / 取消收藏 / 取消关注等）。这类证据已被降级（signal_strength 通常为 0.2），只能当作很弱的兴趣线索：不要据此提高任何兴趣权重，也不要把它计入 favorite_up_users；有明确反向证据时可用于降低相关偏好置信度。
13. 初始化分片时，可顺手输出少量 awareness_candidates / insight_candidates：
    - awareness_candidates 是对本批事件的直接观察，不是人格结论，最多 3 条；
    - insight_candidates 是有证据支撑的轻量假设，最多 2 条，confidence 0~1；
    - 它们只用于下一步初始画像生成的临时上下文，不要为了完整而编造。
14. style / exploration_openness 字段有严格取值约束，违反会被系统丢弃：
    - style.preferred_duration 只能是 short | medium | long 之一；
    - style.preferred_pace 只能是 fast | moderate | slow 之一；
    - style.quality_sensitivity / humor_preference / depth_preference 以及
      exploration_openness 都是 0~1 之间的浮点数。
    当证据不足以判断时，直接省略该字段或填 0.5（openness/数值型）；
    严禁用 "unknown"、"未知"、"none" 之类占位符或用 0 当"没数据"的替身
    （0 会被当作"该维度确实极低"的真实取值）。
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
    "weekday_patterns": "工作日集中看 AI 技术资讯和国际时事深度",
    "weekend_patterns": "周末沉浸追番和游戏社区内容",
    "time_of_day_patterns": "深夜到凌晨（2-4点）活跃度最高",
    "session_type": "深度钻研型"
  },
  "exploration_openness": 0.6,
  "disliked_topics": ["低质标题党"],
  "cognitive_style": ["偏好类比与隐喻式理解而非纯逻辑推演", "直觉优先、自上而下的全局把握"],
  "favorite_up_users": ["某个UP主"],
  "awareness_candidates": [
    {
      "observation": "最近连续停留在高信息密度的工具链内容上",
      "trend": "从泛泛探索转向验证具体工作流",
      "emotion_guess": "带着掌控感需求的好奇"
    }
  ],
  "insight_candidates": [
    {
      "hypothesis": "用户可能不只追新工具，更在意工具能否支撑长期推进",
      "evidence": ["多条工具链和长期项目事件同向出现"],
      "confidence": 0.68
    }
  ]
}
</output_schema>

<examples>
输入事件里如果多次出现长视频、纪录片、深度讲解，
可以提高 “历史/纪录片/知识” 相关标签和 depth_preference。
</examples>
""".strip().replace("__CATEGORY_VOCAB__", _category_vocab_line())


def build_preference_analysis_prompt(
    *,
    events: list[dict[str, object]],
    existing_preference: dict[str, object],
    awareness_notes: list[dict[str, object]] | None = None,
    active_insights: list[dict[str, object]] | None = None,
    input_view: str = "legacy",
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting user preferences from events.

    ``awareness_notes`` / ``active_insights`` are optional cognition context —
    the incremental interest line passes its recent tail so a batch of events
    is interpreted against what the system already believes about the user,
    not in a vacuum. ``None`` keeps the prompt byte-identical to the
    pre-context builder, which is what every other caller (init chunks,
    feedback batch) still sends: their replay invariance is preserved by
    construction. Sections are ordered stable → variable so the provider-side
    prompt cache keeps its prefix.
    """
    from openbiliclaw.sources.event_format import render_retraction_marked_events

    system_prompt = _PREFERENCE_ANALYSIS_SYSTEM_PROMPT
    selected_view = normalize_cognition_input_view(input_view)
    rendered_events = render_retraction_marked_events(events)
    if selected_view == "compact-v1":
        profile_view = build_cognition_profile_view_v1(
            preference_summary=existing_preference,
            recent_awareness=awareness_notes,
            active_insights=active_insights,
        )
        sections = [
            "<existing_preference>",
            json.dumps(
                profile_view.stable_preference,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</existing_preference>",
        ]
        if profile_view.recent_awareness:
            sections += [
                "<recent_awareness>",
                json.dumps(
                    profile_view.recent_awareness,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</recent_awareness>",
            ]
        if profile_view.active_insights:
            sections += [
                "<active_insights>",
                json.dumps(
                    profile_view.active_insights,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</active_insights>",
            ]
        sections += [
            "<event_batch>",
            json.dumps(
                build_cognition_event_view_v1(rendered_events).as_list(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</event_batch>",
        ]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(sections)},
        ]

    sections = [
        "<existing_preference>",
        json.dumps(existing_preference, ensure_ascii=False, indent=2),
        "</existing_preference>",
    ]
    if awareness_notes:
        sections += [
            "<recent_awareness>",
            json.dumps(awareness_notes, ensure_ascii=False, indent=2, sort_keys=True),
            "</recent_awareness>",
        ]
    if active_insights:
        sections += [
            "<active_insights>",
            json.dumps(active_insights, ensure_ascii=False, indent=2, sort_keys=True),
            "</active_insights>",
        ]
    sections += [
        "<event_batch>",
        json.dumps(
            rendered_events,
            ensure_ascii=False,
            indent=2,
        ),
        "</event_batch>",
    ]
    user_prompt = "\n\n".join(sections)
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
    source_platform_mix: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """Build a cache-friendly prompt for initial soul-profile generation."""
    system_prompt = """
<task>
你要生成一份人格画像。你是用户的老朋友,正坐在 ta 对面,直接跟 ta 说"你是这样一个人"。
画像会被原样展示给用户本人 —— 写法必须是**第二人称**直接对话
("你这人……"、"你身上……"、"你最近……"),
绝对不能写成"ta……"、"他……"、"这人……"或类似的第三人称叙述。

你不是在列对方平时看什么、玩什么 ——
那些事看 ta 自己的关注列表和兴趣标签就能知道,不用你写。
你要说的是 ta 这个人**内在是什么样、需要什么、怎么活着**,
让 ta 看完觉得"这个朋友是真懂我"。
</task>

<inner_step>
写之前在心里走完三步(不要输出这一段):

【第一步】看 ta 的兴趣分布,估出"生活模式占比":
   玩耍模式 / 钻研模式 / 审美模式 / 行动模式 / 倾听模式 / 闲逛模式
   合计 ~100%。

【第二步 — 关键】把每种模式翻译成它对应的**内在需求**。
   portrait 写的是这些"内在需求",不是模式本身,更不是具体兴趣。

   翻译表(参考):
   - 玩耍 → 对趣味/情绪能量/松弛的底层需求 / 对"生活得有意思"的执着
   - 钻研 → 对结构/原理/掌控感的需求 / 对"想明白"的执拗
   - 审美 → 对感官质量/调性的敏感 / 对"对不对劲"的不妥协
   - 行动 → 把抽象转成具体的实现欲 / 对"光想不做"的不安
   - 倾听 → 对人物状态和情感纹理的兴趣
   - 闲逛 → 对自由度的需要 / 不愿被目标锁死的反弹

【第三步】心理张力(防御 / 焦虑 / 内在矛盾)只在行为里**真有证据**时才写。
   没有就不写,不要硬编 — 没有冲突的人也是合法的。
</inner_step>

<rules>
1. 输出严格 JSON,不要附带解释。
2. portrait 是一段连续的话(不分段、不分点),150-260 字。
3. **绝对不出现具体兴趣词** —— portrait 必须停留在"ta 这个人是什么样"这一层,
   兴趣具象层是 likes 字段的责任,不在 portrait 里复读。禁止出现:
   - 游戏类型(自走棋、MOBA、塔防、自走棋玩法、等)
   - 内容载体(番剧、综艺、虚拟主播、直播、纪录片、4K 修复等)
   - 领域名(AI、人工智能、编程、新能源、机器学习、哲学、历史等)
   - 作品名 / IP 名 / UP 主名 / 频道名 / 主播名 / 品牌名 / 食物名 / 地名
   - "看了 X""追了 X""沉浸在 X""驻足于 X" 这类直白行为复述
   兴趣 topic、题材、作品名只能留在内部推理里,不得出现在 portrait 最终字面。
4. **必须用第二人称"你"**直接对用户讲话,不要用 "ta / 他 / 她 / 这人" 等
   第三人称叙述。写法用**内在需求**和**为人方式**说话,不是行为列表:
   - ✅ "你既要乐子也要门道,两边都不肯偏废"
   - ✅ "对世界怎么运转的好奇,在你身上是一种长期不退烧的状态"
   - ❌ "ta 沉迷自走棋"(第三人称 + 具体兴趣词,双重违规)
   - ❌ "这人对 AI 编程感兴趣"(第三人称 + 领域名,双重违规)
   - ❌ "玩耍模式占比 50%"(规则术语不应出现在最终输出)
5. 调性:**老朋友坐在你对面跟你说"你是这样一个人"**,
   口语、有温度,可以带轻微调侃。
   绝不写成心理报告 / 咨询记录 / 说明书 / 理论术语堆砌。
6. 模式占比决定语气配比 — 占比高的内在需求多写,占比 < 5% 的不写。
7. **不预设性格类型**。
   - 如果用户玩耍权重高,开头就写"乐子"和"情绪能量"那一类内在需求,
     不要默认从"理性 / 防御"开始。
   - 如果用户钻研权重真的高,portrait 也要老实写得克制严肃 ——
     不要为了"显得轻松"硬塞玩心。
   - 如果用户审美权重高,portrait 该敏感诗意就敏感诗意。
   - 没有冲突的人就不写冲突,没有焦虑就不写焦虑。
8. core_traits 用**为人特征词**(3 到 6 条),不写兴趣类别:
   - ✅ 爱玩 / 较真 / 杂食 / 信息敏感 / 自我节奏感强 / 不上纲上线 / 沉得住气 / 慢热
   - ❌ 游戏玩家 / 技术钻研者 / AI 爱好者
9. deep_needs 用具体可感知的语言描述底层渴望
   (如"在玩乐与正经之间自由切换的空间""被美与真实触动""不被打扰的深度专注时间"),
   不要写抽象心理学术语("掌控感""自我实现"太笼统),
   也不要写认知偏好("逻辑闭环"属于 cognitive_style)。
10. cognitive_style:如果 preference_summary 中已有 cognitive_style,
    直接沿用并微调措辞,不要推翻或重新推断。如果没有,再从行为模式推断。
11. life_stage 推断人口学和阶段特征(学历 / 职业阶段 / 年龄段 + 该阶段的核心心理状态),
    不要堆砌具体事件。
    current_phase 聚焦当前心理动力方向,不罗列最近内容。
12. mbti 字段必须填写,confidence 0.5-0.9,
    四个维度 EI/SN/TF/JP 都要给 pole + strength。
    **不要默认 INTP/INTJ** — 根据行为证据如实判断:
    爱玩、外向、社交驱动可能是 ESFP/ENFP;
    审美驱动可能是 INFP/ISFP;
    行动驱动可能是 ESTP/ESTJ 等。
13. history_summary 里的 `contexts` / `recent_contexts` / `older_contexts`
    (v0.3.22+ 跨源统一)是用户行为的中文自然语言摘要,每行形如
    "在 B 站收藏了《...》,作者:..." 或 "小红书点赞:... 作者:..."。
    **优先把 contexts 当作行为图景**来感受用户在做什么、跨哪些平台,
    再结合 titles / authors / favorites_summary / following_summary
    做更细的标签匹配。跨平台信号要一视同仁,不要因为某条来自小红书
    就降权——portrait 写的是"内在需求和为人方式",和平台来源无关。
</rules>

<positive_examples>
全部使用第二人称"你",像老朋友坐在你对面跟你说话:

示例 A(玩耍 50% + 钻研 30% + 行动 10%):
"你这人身上同时挂着两根弦:一根是'生活得有意思',另一根是'想明白'。
乐子那根是底色 — 你对趣味、情绪能量、松弛感有底层需求,没意思的东西
你一秒都坐不住。但你也不是只追着乐跑,遇到不懂的就会想从底层拆开看,
而且拆得有耐心。两边切换得挺自然,玩了不觉得没干正事,认真起来也
放得下玩。最近你那股'想明白'的劲有点不满足于纸上谈兵,开始想真
往现实里落一落了。"

示例 B(钻研 70% + 审美 20%):
"你骨子里偏认真型 — 不是紧绷的那种,而是好奇心很长。'想明白'
这件事的需要在你身上是常驻的,不是一阵一阵的。你对'对不对劲'
也敏感,质感不到位的东西会下意识让你皱眉头,但你不至于挑剔到
不近人情。整体节奏是慢工出细活,不容易被推着走,你有你自己的
节拍。跟你认真聊一件事你会很投入,但闲扯并不是你的舒适区。"

示例 C(玩耍 80% 单一主轴):
"你的主调其实挺简单 — 生活就是要有意思。你对乐子的吸收力很强,
新东西一来你会想去尝尝,不喜欢的也不会硬撑。不是不会认真,但
认真不是你的主调。你信息杂食,什么都看一点,不强求深度。跟你
待着舒服,因为你不会把简单的事搞复杂。"

示例 D(审美 70% + 倾听 20%):
"你是个高敏感审美者 — 对质感、调性、氛围有刻在骨子里的敏感。
你判断'对不对劲'比多数人快得多,不是刻意,是本能。你也愿意
感知作品和人背后的情感纹理,但更看重当下的直觉感受对不对。
你整体节奏慢,挑剔但不刻薄,这种对感官质量的坚持让你活得挺纯粹。"
</positive_examples>

<output_schema>
{
  "personality_portrait": "150-260 字的一段连续介绍(描述内在需求和为人方式,不出现具体兴趣)",
  "core_traits": ["..."],
  "cognitive_style": ["..."],
  "motivational_drivers": ["..."],
  "current_phase": "...",
  "values": ["..."],
  "life_stage": "...",
  "deep_needs": ["..."],
  "mbti": {
    "type": "....",
    "confidence": 0.7,
    "dimensions": {
      "EI": {"pole": "I", "strength": 0.7},
      "SN": {"pole": "N", "strength": 0.7},
      "TF": {"pole": "T", "strength": 0.6},
      "JP": {"pole": "P", "strength": 0.6}
    }
  }
}
</output_schema>
""".strip()
    normalized_awareness = recent_awareness or []
    normalized_insights = active_insights or []
    user_prompt = "\n\n".join(
        [
            "<tone_profile>",
            _render_tone_profile(tone_profile, source_platform_mix),
            "</tone_profile>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</preference_summary>",
            "<recent_awareness>",
            json.dumps(normalized_awareness, ensure_ascii=False, indent=2, sort_keys=True),
            "</recent_awareness>",
            "<active_insights>",
            json.dumps(normalized_insights, ensure_ascii=False, indent=2, sort_keys=True),
            "</active_insights>",
            "<history_summary>",
            json.dumps(history_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</history_summary>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_role_delta_prompt(
    *,
    current_life_stage: str,
    current_phase: str,
    evidence: list[str],
) -> list[dict[str, str]]:
    """Build a delta prompt for updating the role layer."""
    system_prompt = """
<task>
你要判断用户最近的行为证据是否表明其生活阶段或当前状态发生了变化。
这是一个保守更新：只有当证据明确表明变化时才修改，否则保持原样。
</task>

<rules>
1. 输出必须是严格 JSON。
2. 如果证据不足以判断变化，返回 changed=false 并保持原值不变。
3. life_stage 和 current_phase 必须基于具体行为证据描述，不要写抽象空话。
4. current_phase 应引用具体的活动模式（如"最近密集观看XX类内容"、"开始关注XX领域"）。
5. 每次最多修改一个字段（life_stage 或 current_phase），优先修改 current_phase。
</rules>

<output_schema>
{
  "changed": true,
  "life_stage": "当前生活阶段描述",
  "current_phase": "当前状态描述，引用具体行为证据",
  "reason": "简要说明为什么需要更新"
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<current_state>",
            json.dumps(
                {
                    "life_stage": current_life_stage,
                    "current_phase": current_phase,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "</current_state>",
            "<recent_evidence>",
            json.dumps(evidence[:20], ensure_ascii=False, indent=2),
            "</recent_evidence>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_values_delta_prompt(
    *,
    current_values: list[str],
    current_drivers: list[str],
    evidence: list[str],
) -> list[dict[str, str]]:
    """Build a delta prompt for updating the values layer."""
    system_prompt = """
<task>
你要判断用户最近的行为证据是否表明其价值观或动机驱动发生了变化。
这是一个保守更新：每次最多增删 1 条，不要大规模重写。
</task>

<rules>
1. 输出必须是严格 JSON。
2. 如果证据不足，返回 changed=false。
3. 添加的价值观/驱动力必须有明确的行为证据支撑。
4. 移除的条目必须说明为什么不再适用。
5. values 控制在 3-6 条，motivational_drivers 控制在 2-4 条。
</rules>

<output_schema>
{
  "changed": true,
  "values": ["更新后的价值观列表"],
  "motivational_drivers": ["更新后的动机驱动列表"],
  "reason": "简要说明变更理由"
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<current_state>",
            json.dumps(
                {
                    "values": current_values,
                    "motivational_drivers": current_drivers,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "</current_state>",
            "<recent_evidence>",
            json.dumps(evidence[:20], ensure_ascii=False, indent=2),
            "</recent_evidence>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_core_delta_prompt(
    *,
    current_traits: list[str],
    current_needs: list[str],
    current_mbti: dict[str, object],
    evidence: list[str],
) -> list[dict[str, str]]:
    """Build a delta prompt for updating the core layer."""
    system_prompt = """
<task>
你要判断用户最近的行为证据是否表明其核心人格特质、深层需求或 MBTI 需要微调。
这是最保守的更新层：核心人格极少变化，只有大量长期一致的证据才应修改。
</task>

<rules>
1. 输出必须是严格 JSON。
2. 如果证据不足（通常如此），返回 changed=false。
3. core_traits 每次最多增删 1 条，deep_needs 同理。
4. MBTI 类型几乎不变，只有当大量证据明确矛盾时才调整维度 strength。
5. 不要因为单次行为就改变核心层，需要看到跨多次的一致性模式。
6. deep_needs 必须写心理动力层面的需求（如掌控感、身份认同、自主性、归属感），
   不要写认知偏好（如"逻辑闭环""价值确认"）——认知偏好属于 cognitive_style，不属于 deep_needs。
7. core_traits 只保留有直接行为证据的特质，不要从已有特质外推衍生维度
   （如从"务实"衍生出"极致精度追求""结构审美驱动"），也不要遗漏"独立自主"等有证据支撑的特质。
</rules>

<output_schema>
{
  "changed": false,
  "core_traits": ["保持不变的特质列表"],
  "deep_needs": ["保持不变的需求列表"],
  "mbti": {"type": "INTP", "confidence": 0.7, "dimensions": {}},
  "reason": "说明为什么保持不变/为什么需要微调"
}
</output_schema>
""".strip()
    user_prompt = "\n\n".join(
        [
            "<current_state>",
            json.dumps(
                {
                    "core_traits": current_traits,
                    "deep_needs": current_needs,
                    "mbti": current_mbti,
                },
                ensure_ascii=False,
                indent=2,
            ),
            "</current_state>",
            "<recent_evidence>",
            json.dumps(evidence[:20], ensure_ascii=False, indent=2),
            "</recent_evidence>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


_AWARENESS_SYSTEM_PROMPT = """
<task>
你要基于近期用户行为，生成少量谨慎的近期观察笔记。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. observation 只能描述观察到的行为倾向，不要下人格定论。
3. trend 和 emotion_guess 必须使用保守表述。
4. 如果证据不足，可以返回空数组。
5. 每条事件自带 `context` 字段（v0.3.22+ 跨源统一），是中文自然语言摘要——优先以 context 来理解事件本身，配合 metadata.source_platform 区分平台。所有平台信号都参与觉察推断,不区别对待。
6. 如果 recent_events 出现 `feedback_type=dislike`、`reaction=thumbs_down` 或 `inferred_satisfaction=negative`，把它当作用户最近开始避开某类内容的信号；可以生成“最近开始避开 X”这类保守观察，但不要把单次 dislike 上升成人格结论。
7. 负反馈一致性：笔记中描述「点踩 / dislike / 不感兴趣」等明确负反馈行为时，recent_events 里必须真的存在这类事件（`feedback_type=dislike` 或 `inferred_satisfaction=negative`）；没有就绝不能在笔记中声称用户点踩了——只能描述实际观察到的浏览行为。
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


def build_awareness_prompt(
    *,
    events: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
    input_view: str = "legacy",
) -> list[dict[str, str]]:
    """Build a structured prompt for recent awareness-note generation."""
    from openbiliclaw.sources.event_format import render_retraction_marked_events

    selected_view = normalize_cognition_input_view(input_view)
    rendered_events = render_retraction_marked_events(events)
    if selected_view == "compact-v1":
        profile_view = build_cognition_profile_view_v1(
            soul_profile=soul_profile,
            preference_summary=preference_summary,
        )
        sections = [
            "<soul_profile>",
            json.dumps(
                profile_view.stable_soul,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(
                profile_view.stable_preference,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</preference_summary>",
        ]
        if profile_view.recent_awareness:
            sections += [
                "<recent_awareness>",
                json.dumps(
                    profile_view.recent_awareness,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</recent_awareness>",
            ]
        if profile_view.active_insights:
            sections += [
                "<active_insights>",
                json.dumps(
                    profile_view.active_insights,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</active_insights>",
            ]
        sections += [
            "<recent_events>",
            json.dumps(
                build_cognition_event_view_v1(rendered_events).as_list(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</recent_events>",
        ]
        return [
            {"role": "system", "content": _AWARENESS_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(sections)},
        ]

    user_prompt = "\n\n".join(
        [
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2, sort_keys=True),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</preference_summary>",
            "<recent_events>",
            json.dumps(
                rendered_events,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</recent_events>",
        ]
    )
    return [
        {"role": "system", "content": _AWARENESS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


_AWARENESS_WITH_CONFUSIONS_SYSTEM_PROMPT = """
<task>
你要基于近期用户行为，做两件事：
1. 生成少量谨慎的近期观察笔记（notes）。
2. 标记出你「看不懂」的行为——那些证据不足、无法干净解读、可能存在多种矛盾解释的观察，作为疑惑（confusions）。
</task>

<rules>
1. 输出必须是严格 JSON 对象：{"notes": [...], "confusions": [...]}，不要附带解释。
2. notes 的每条字段：date / observation / trend / emotion_guess / source_event_ids；措辞保守，不下人格定论。
   - source_event_ids：**这条观察实际依据的事件 id 数组**，只能从 recent_events 里各事件的 `id` 中选，
     不要编造、不要把整批都塞进去；确实说不清是哪几条时给空数组（系统会退回整批归属并标记为近似）。
3. confusions 只在你「真的不确定该怎么解读」时才产出，宁缺毋滥，最多 2 条。每条包含：
   - topic：这个疑惑关联的话题/领域（简短，可为空）。
   - observation：看到的、说不清的行为现象。
   - interpretation：你此刻最可能但不确定的解读。
   - interpretation_confidence：0~1，对上面解读的把握（低置信才该成为疑惑）。
   - evidence_refs：相关的事件线索（可为空数组）。
4. 每条事件自带 `context` 字段（跨源统一中文摘要），优先据此理解事件，配合 metadata.source_platform 区分平台；所有平台信号一视同仁。
5. 如果没有真正看不懂的地方，confusions 返回空数组——不要为凑数制造疑惑。
6. 如果 recent_events 出现 `feedback_type=dislike`、`reaction=thumbs_down` 或 `inferred_satisfaction=negative`，把它当作用户最近开始避开某类内容的信号；可以生成“最近开始避开 X”这类保守观察，但不要把单次 dislike 上升成人格结论。
7. 负反馈一致性：笔记中描述「点踩 / dislike / 不感兴趣」等明确负反馈行为时，该条 source_event_ids 必须至少包含一条对应的事件（`feedback_type=dislike` 或 `inferred_satisfaction=negative`）；recent_events 里没有这类事件，就绝不能在笔记中声称用户点踩了——只能描述实际观察到的浏览行为。
8. 详细输入（画像 / 偏好摘要 / 近期事件）见 user message 的 X / Y / Z 各段。
</rules>

<output_schema>
{
  "notes": [
    {
      "date": "2026-03-08",
      "observation": "最近连续浏览高信息密度内容。",
      "trend": "更偏向深度解释而非轻量消遣。",
      "emotion_guess": "可能处于主动吸收和整理信息的阶段。",
      "source_event_ids": [12, 15, 17]
    }
  ],
  "confusions": [
    {
      "topic": "解压视频",
      "observation": "连续点开解压视频但每条停留都很短。",
      "interpretation": "可能只是当背景音，而不是真的对这个题材感兴趣。",
      "interpretation_confidence": 0.35,
      "evidence_refs": []
    }
  ]
}
</output_schema>
""".strip()


def build_awareness_with_confusions_prompt(
    *,
    events: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
    input_view: str = "legacy",
) -> list[dict[str, str]]:
    """Build the awareness+confusions prompt (Phase 2).

    A NEW, independent builder — the legacy :func:`build_awareness_prompt`
    stays byte-identical (its replay path is the one guarded by the invariance
    test). ``cognition_cycle`` switches to this builder as an intentional
    behaviour change (A/B recorded in the PR). System is a module-level
    constant; per-call variables live in the user message with deterministic
    ``sort_keys=True`` JSON so prompt-cache prefixes stay stable.
    """
    from openbiliclaw.sources.event_format import render_retraction_marked_events

    selected_view = normalize_cognition_input_view(input_view)
    rendered_events = render_retraction_marked_events(events)
    if selected_view == "compact-v1":
        profile_view = build_cognition_profile_view_v1(
            soul_profile=soul_profile,
            preference_summary=preference_summary,
        )
        sections = [
            "<soul_profile>",
            json.dumps(
                profile_view.stable_soul,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(
                profile_view.stable_preference,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</preference_summary>",
        ]
        if profile_view.recent_awareness:
            sections += [
                "<recent_awareness>",
                json.dumps(
                    profile_view.recent_awareness,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</recent_awareness>",
            ]
        if profile_view.active_insights:
            sections += [
                "<active_insights>",
                json.dumps(
                    profile_view.active_insights,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</active_insights>",
            ]
        sections += [
            "<recent_events>",
            json.dumps(
                build_cognition_event_view_v1(rendered_events).as_list(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</recent_events>",
        ]
        return [
            {"role": "system", "content": _AWARENESS_WITH_CONFUSIONS_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(sections)},
        ]

    user_prompt = "\n\n".join(
        [
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2, sort_keys=True),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</preference_summary>",
            "<recent_events>",
            json.dumps(
                rendered_events,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</recent_events>",
        ]
    )
    return [
        {"role": "system", "content": _AWARENESS_WITH_CONFUSIONS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


_INSIGHT_SYSTEM_PROMPT = """
<task>
你要基于近期觉察、偏好摘要和用户画像，生成谨慎的解释性假设。
</task>

<rules>
1. 输出必须是严格 JSON 数组，不要附带解释。
2. hypothesis 是假设，不是结论，措辞必须保守。
3. 每条必须附 1~3 条 evidence。
4. confidence 保持在 0~1，且不要过高。
5. existing_hypotheses（如有）是当前已有的活跃假设，仅作上下文参考。本次新的觉察笔记若印证某条已有假设，可重述同一 hypothesis 文本以累积其证据/置信；若指向新方向，再生成新假设。不要为凑数而重复已有假设。
6. 只依据本次觉察笔记里的新信号下结论；existing_hypotheses 本身不是新证据。
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


def build_insight_prompt(
    *,
    awareness_notes: list[dict[str, object]],
    preference_summary: dict[str, object],
    soul_profile: dict[str, object],
    existing_hypotheses: list[dict[str, object]] | None = None,
    input_view: str = "legacy",
) -> list[dict[str, str]]:
    """Build a structured prompt for insight-hypothesis generation.

    ``existing_hypotheses`` (optional) is the set of currently-active
    hypotheses passed as read-only context so an incremental run — which
    only sees *new* awareness notes — can refine or avoid restating them
    instead of regenerating from the full awareness history every time.
    See rules 5 / 6 below.
    """
    selected_view = normalize_cognition_input_view(input_view)
    if selected_view == "compact-v1":
        profile_view = build_cognition_profile_view_v1(
            soul_profile=soul_profile,
            preference_summary=preference_summary,
            recent_awareness=awareness_notes,
            active_insights=existing_hypotheses,
        )
        sections = [
            "<soul_profile>",
            json.dumps(
                profile_view.stable_soul,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(
                profile_view.stable_preference,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</preference_summary>",
            "<existing_hypotheses>",
            json.dumps(
                profile_view.active_insights,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</existing_hypotheses>",
            "<awareness_notes>",
            json.dumps(
                profile_view.recent_awareness,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</awareness_notes>",
        ]
        return [
            {"role": "system", "content": _INSIGHT_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(sections)},
        ]

    user_prompt = "\n\n".join(
        [
            "<awareness_notes>",
            json.dumps(awareness_notes, ensure_ascii=False, indent=2),
            "</awareness_notes>",
            "<existing_hypotheses>",
            json.dumps(existing_hypotheses or [], ensure_ascii=False, indent=2),
            "</existing_hypotheses>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2),
            "</preference_summary>",
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2),
            "</soul_profile>",
        ]
    )
    return [
        {"role": "system", "content": _INSIGHT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_search_queries_prompt(
    *,
    profile_summary: dict[str, object],
    pool_hints: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    """Build a structured prompt for search query generation."""
    system_prompt = """
<task>
你要为 B 站内容发现生成一组可搜索的关键词组合。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. query 必须是适合 B 站搜索的短词或短组合，不要写成长句。
3. 优先组合"兴趣主题 + 内容风格/需求"，避免过泛的词。
4. queries 数量控制在 5 到 10 个。
5. 用户画像中包含 interest_domains（一级兴趣域）、interests（二级具体兴趣）
   以及可选的 speculative_interests（猜测兴趣——系统推测用户可能感兴趣但尚未确认的方向）。
   你必须保证 query 主题分布均匀，避免集中在用户最强兴趣上：
   - 约 25% query 使用一级兴趣域名称搜索（如 "科技 盘点" "游戏 推荐"），
     目的是发现该域中用户尚未接触的新内容。
   - 约 25% query 使用二级兴趣的细分角度（非直接重复现有词条）。
   - 约 25% query 基于 speculative_interests 生成（如果画像中存在），
     直接用猜测兴趣的 domain 作为核心主题词组合搜索。
     若不存在 speculative_interests 则将此配额分配给跨域探索。
   - 约 25% query 跨域探索（桥接用户认知风格或深层需求到相邻但陌生的领域）。
   跨域 query 不需要完全脱离用户认知范围，但核心主题词必须不在用户任何
   interest_domains / interests 中出现。
6. query 的内容风格必须多样化，不要全部偏向"深度/学术/原理"。
   应该混合使用不同风格词，如 盘点/推荐/日常/吐槽/测评/入门/体验/挑战/合集 等，
   整组 query 中带"深度/原理/解析/机制"等学术向关键词的不得超过 2 个。
7. 多样性双向保护：
   - 如果 depth_preference 偏低、preferred_duration 偏短，或 humor_preference 偏高，
     就进一步减少"原理/解析/机制"这类硬入口，优先使用更轻、更好点开的形式词；
     不要把"理解力强"误翻译成"必须更学术"。
   - 反过来，如果 depth_preference 偏高、preferred_duration 偏长，
     但 humor_preference >= 0.4、exploration_openness >= 0.6，
     或 cognitive_style 里有"兼顾/调节/穿插轻松"这类描述，
     仍要至少保证 30% query 用 "盘点/合集/吐槽/日常/挑战/体验/vlog" 这类放松形式词，
     不能因为画像深就只发硬 query；用户硬不代表 24 小时都想看硬内容。
8. 所有 query 的核心主题词（第一个实词）必须两两不同，
   禁止同一概念换皮出现多次。
9. 如果 user 消息包含 <pool_distribution_hints>，这些是当前推荐池已经拥挤或欠覆盖的方向。
   avoid_topics / avoid_styles / avoid_franchises 是软避让信号；prefer_axes 是优先补货方向。
   avoid_styles 是封闭 style_key 观看模式，不是题材标签。
   source_deficits 是平台/来源缺口信号，不是内容轴；不要把平台名当成 query 主题。
   不要为了避让而生成与用户画像无关的 query。
10. 冷启动保护：如果 <pool_distribution_hints> 里 cold_start=true，
   表示当前还没有足够历史 discovery / pool 分布可参考，avoid_topics 不是用户讨厌的内容，
   而是画像里权重最高、最容易让首批内容过度集中的主题。此时：
   - avoid_topics 中的主题整组最多 2 个 query 可以直接使用，不能占满搜索词；
   - 至少一半 query 必须来自 prefer_axes、较低权重兴趣、一级兴趣域的其它切面或跨域探索；
   - prefer_axes 是冷启动时优先补广度的内容轴，应该优先覆盖，但不要生造无关主题；
   - 仍要保留少量高权重兴趣入口，让首批内容有命中感，不要完全避开用户最喜欢的方向。
</rules>

<output_schema>
{
  "queries": [
    "摄影 入门 推荐",
    "历史 冷知识 盘点",
    "科技 新品 测评",
    "城市规划 纪录片",
    "认知科学 科普"
  ]
}
</output_schema>

<examples>
假设用户 interest_domains 包含 [科技(强化学习, ppo), 历史(纪录片)]，
认知风格偏好"结构化分析、高信息密度"：

一级域 query（~40%）：
- "科技 新品 盘点"（用域名搜索，覆盖用户未知的科技子领域）
- "历史 冷知识 讲解"（用域名搜索，发现域内新角度）
- "游戏 推荐 合集"（如果画像有游戏域）

二级细分 query（~30%）：
- "冷战 外交 故事"（历史域内的细分角度，非直接重复）
- "强化学习 应用 案例"（具体兴趣的新切面）

跨域探索 query（~30%）：
- "心理学 日常 科普"（相邻学科，桥接：对人行为的好奇）
- "城市探索 vlog"（相邻领域，桥接：纪录片风格+系统视角）

坏的 query：
- "强化学习 ppo"（和已有二级兴趣完全重合，无新意）
- "美食"（与用户认知风格无桥接关系，随机发散）
- "博弈论 纳什均衡 策略模型"（三个 query 本质相同，浪费多样性配额）
- "科技 深度 解析" + "历史 深度 解读" + "哲学 深度 讨论"（全部偏学术，风格单一）
</examples>
""".strip()
    user_sections = [
        "<profile_summary>",
        json.dumps(profile_summary, ensure_ascii=False, indent=2),
        "</profile_summary>",
    ]
    compact_pool_hints = {
        key: value
        for key, value in _normalize_pool_hints(pool_hints).items()
        if value not in (None, "", [], {}, ())
    }
    if compact_pool_hints:
        user_sections.extend(
            [
                "<pool_distribution_hints>",
                json.dumps(compact_pool_hints, ensure_ascii=False, indent=2),
                "</pool_distribution_hints>",
            ]
        )
    user_prompt = "\n\n".join(user_sections)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for dialogue-insight extraction (v0.3.174+
# prompt-cache compliance). All per-call data — core memory, the dialogue
# turn, and the active-list to settle against — lives in the user message.
_DIALOGUE_INSIGHT_SYSTEM_PROMPT = """
<task>
你要从一轮用户对话中做两件事:
1) 提取少量高价值的候选理解 (candidates),用于后续长期画像更新;
2) 依据 user 消息里的 <active_list>,判断这轮对话是否结算 (settles) 了其中某些
   活跃对象 (系统当前的推测兴趣 / 洞察假设 / 疑惑)。
core_memory、这轮对话、以及 active_list 都在 user 消息里给出。
</task>

<rules>
1. 输出必须是严格 JSON,不要附带解释。
2. 只提取用户明确表达或高度暗示的稳定信号,不要记录瞬时情绪碎片。
3. candidates 的 kind 只允许: interest, dislike, goal, value, state。
4. confidence 保持保守,0~1。
5. 最多返回 3 条 candidates。
6. settles 只允许引用 <active_list> 中真实出现过的对象:
   - kind 为 speculation 时,ref 必须是 active_list.speculations[].domain;
   - kind 为 insight 时,ref 必须是 active_list.insights[].hash;
   - kind 为 confusion 时,ref 必须是 active_list.confusions[].id。
   不要凭空编造 ref;不确定就不要写进 settles。
7. settles[].verdict 只允许: confirm (这轮明确印证), reject (这轮明确否定)。
   只有用户明确表态才结算,含糊不清时不结算。
8. 若没有可结算对象,settles 返回空数组。
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
  ],
  "settles": [
    {
      "kind": "speculation",
      "ref": "桌游",
      "verdict": "confirm",
      "note": "用户说最近确实在玩。"
    }
  ]
}
</output_schema>
""".strip()


_DIALOGUE_ANCHOR_USER_CONTRACT = """
<anchor_contract>
这轮处于单一话题锚中。除原有 candidates / settles 外，还要返回 anchor 判断：
- relation 只允许 support / contradict / revise / answer / ambiguous / unrelated；
- hypothesis 锚只允许 support / contradict / revise / ambiguous / unrelated；
- confusion 锚只允许 answer / ambiguous / unrelated；
- confusion 的 answer 必须把 interpretation 写成 real_interest / proxy_behavior / dismissed 之一；
- revise 可在 derived 中给出修正后的假设；其他 relation 的 derived 返回空数组；
- 归锚内容禁止重复写进 candidates，也不要再用 settles 结算锚对象；
- 不确定归属时返回 ambiguous，明确岔题才返回 unrelated。

anchor 字段格式：
{
  "relation": "support",
  "interpretation": "",
  "derived": [
    {
      "content": "修正后的假设",
      "confidence": 0.82,
      "evidence": "用户本轮的明确修正"
    }
  ]
}
</anchor_contract>
""".strip()


def build_dialogue_insight_prompt(
    *,
    user_message: str,
    assistant_reply: str,
    core_memory: dict[str, object],
    active_list: dict[str, object] | None = None,
    anchor: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    """Build a structured prompt for extracting candidate insights from dialogue.

    ``active_list`` (v0.3.174+) carries the round's injected settle targets
    (speculations by ``domain`` / insights by content ``hash`` / confusions by
    ``id``). The system prompt is a module-level constant; all per-call data is
    ordered most-stable-first in the user message (prompt-cache convention).
    """
    user_sections = [
        "<core_memory>",
        json.dumps(core_memory, ensure_ascii=False, indent=2, sort_keys=True),
        "</core_memory>",
        "<active_list>",
        json.dumps(active_list or {}, ensure_ascii=False, indent=2, sort_keys=True),
        "</active_list>",
    ]
    if anchor:
        user_sections.extend(
            [
                "<current_anchor>",
                json.dumps(anchor, ensure_ascii=False, indent=2, sort_keys=True),
                "</current_anchor>",
                _DIALOGUE_ANCHOR_USER_CONTRACT,
            ]
        )
    user_sections.extend(
        [
            "<dialogue_turn>",
            json.dumps(
                {
                    "user_message": user_message,
                    "assistant_reply": assistant_reply,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</dialogue_turn>",
        ]
    )
    user_prompt = "\n\n".join(user_sections)
    return [
        {"role": "system", "content": _DIALOGUE_INSIGHT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for the posture gate (Phase 3). All per-call data
# (the proposed deep-write change, core memory, and the 30-day ledger digest)
# lives in the user message — see ``build_posture_gate_prompt``.
_POSTURE_GATE_SYSTEM_PROMPT = """
<task>
你是画像深层写入的「态势门控」。系统准备把一条深层理解（目标 / 价值观 / 核心状态，
或一次整份画像重建）写进长期画像。你要判断：以当前对这个人的既有理解为基准，这条改动
是否站得住脚。深层理解稳定、代价高，不该被一两条噪声行为轻易改写。
待判定的改动、core_memory、以及最近 30 天的画像写入台账摘要都在 user 消息里。
</task>

<judgement>
只能返回三种判定之一：
- accept：改动与既有理解一致、或有足够证据支撑，放行。
- downgrade：改动方向可能成立但证据不足、或与既有理解有张力——不直接写深层，降级为
  一个「待验证的假设」。这不是拒绝，而是把它放进假设池等更多证据。
- reject：改动明显是噪声、自相矛盾、或与大量既有证据冲突，且没有新意，丢弃。
</judgement>

<principles>
1. 冲突不是错误，而是一个新假设。当改动与既有画像冲突时，默认倾向 downgrade（生成假设）
   而非 accept 或 reject——除非冲突方已有压倒性证据。
2. 保守优先：把握不足时选 downgrade，而不是 accept。
3. 参考台账：若最近同一方向已反复写入并稳定，可以更放心 accept；若台账显示这个方向近期
   反复横跳，倾向 downgrade。
4. 详细输入见 user 消息的 <proposed_change> / <core_memory> / <ledger_digest> 各段。
</principles>

<output_schema>
{"verdict": "downgrade", "reason": "与既有的稳定价值观有张力，证据仅一轮对话，先作为假设观察。"}
</output_schema>
""".strip()


def build_posture_gate_prompt(
    *,
    change: dict[str, object],
    core_memory: dict[str, object],
    ledger_digest: list[dict[str, object]] | None = None,
) -> list[dict[str, str]]:
    """Build the posture-gate judgement prompt (Phase 3).

    The system prompt is a module-level constant (prompt-cache convention); all
    per-call variables live in the user message ordered most-stable-first
    (persona/core memory → this change → the recent ledger digest), each
    serialized with deterministic ``sort_keys=True`` JSON.
    """
    user_prompt = "\n\n".join(
        [
            "<core_memory>",
            json.dumps(core_memory, ensure_ascii=False, indent=2, sort_keys=True),
            "</core_memory>",
            "<proposed_change>",
            json.dumps(change, ensure_ascii=False, indent=2, sort_keys=True),
            "</proposed_change>",
            "<ledger_digest>",
            json.dumps(ledger_digest or [], ensure_ascii=False, indent=2, sort_keys=True),
            "</ledger_digest>",
        ]
    )
    return [
        {"role": "system", "content": _POSTURE_GATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for single-item content evaluation.
# All variables (source_context, source_platform, profile, content)
# go in user_prompt — see ``build_content_evaluation_prompt``.
# Reason-diet floor (v0.3.171): the "< 0.5 → empty reason" threshold below is a
# fixed 0.5 baked into the static prompt text, NOT the runtime
# ``admission_min_score``. It must stay a literal constant for two reasons:
# (1) cache convention — the system prompt has to be byte-identical across
# calls, so it cannot interpolate a per-call/config value; (2) safety margin —
# 0.5 is strictly below every admission path (0.60 default, 0.58 explore per
# ``discovery/admission.py``), so omitting low-score diagnostic text cannot
# affect admission. Reopen this only if an admission path ever drops at/below
# 0.5.
_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你要评估一个候选内容与一个用户画像的匹配度。下面 user 消息会给出 "
    "<source_context>(发现路径)、<source_platform>(平台)、"
    "<profile_summary>(画像)、<evaluation_context>(评估时间)、"
    "<content_summary>(候选),你按下面规则打分。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON,不要附带解释。\n"
    "2. score 范围必须在 0 到 1 之间。\n"
    "3. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
    "score 严格低于 0.5 的条目,reason 必须写成空串 "
    '""(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);'
    "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
    "不超过 30 个 Unicode 字符,说明内容与画像匹配或不匹配的依据。\n"
    '4. 不要只说"因为热门"或"因为看过类似的",要结合用户画像。\n'
    "5. 除 explore 外，发现路径和平台只提供上下文，不得影响评分标准:"
    "search、trending、hot、feed、related_chain、channel、creator 等所有非 explore 候选"
    "都必须按内容与用户画像的真实匹配度统一评分;"
    "不得因为内容热门、来自推荐流、命中搜索词、沿相关推荐获得、来自订阅频道或得到平台算法背书，"
    "就设置基础分、自动加分、降低门槛或事后编造画像关联。"
    "明显不匹配画像的内容必须允许低于 admission 门槛。"
    "只有 explore 允许主题陌生，但内容仍需具备具体、可信的可看性和吸引力，"
    "不能仅因为心理需求抽象匹配就给高分，过于学术、艰深、小众的内容应适当降分。\n"
    "6. topic_group 是该内容所属的粗粒度主题分类,用于推荐去重。"
    "要求:2-4 个中文词,抽象到能覆盖同类内容,"
    '例如"强化学习"而非"强化学习ppo算法源码级讲解",'
    '"城市建筑"而非"上海外滩建筑群纪录片"。'
    "同一主题的不同切面必须归为同一个 topic_group。"
    '语义相同的主题必须用同一个词——"AI" "人工智能" "机器学习" 统一写成 "人工智能",'
    '"RL" "强化学习" 统一写成 "强化学习"。\n'
    "7. style_key(13选1) 描述用户消费这条内容时的观看状态,不是题材分类。"
    "必须从以下观看模式中选一个:\n"
    f"{STYLE_KEY_PROMPT_TEXT}\n"
    "8. franchise_key(可空):内容如果明确属于某个具体 IP / 系列 / 作品 / 品牌,"
    "填它的规范名(中文优先),用于跨 topic_group 的同 IP 去重。例:\n"
    '   - 「AI 重绘原神地图」「提瓦特摄影」「蒙德角色真实化」 → "原神"\n'
    '   - 「星穹铁道 1.6 实战」「崩铁 角色养成」 → "崩坏:星穹铁道"\n'
    '   - 「ChatGPT 工作流」「OpenAI 新模型」 → "ChatGPT"\n'
    '   - 「黑神话悟空 二周目」 → "黑神话:悟空"\n'
    '   - 「番茄炒蛋 5 分钟教程」「读书博主 推荐书单」 → ""'
    "(一般科普 / 美食 / 通用资讯都填空字符串,不要硬凑)\n"
    "   - 同一 IP 必须用相同写法,不要在「原神」「Genshin」「米哈游 原神」之间切换。\n"
    "9. 不同 source_platform(bilibili / xiaohongshu / 其他)的内容标签同 schema,"
    "不要因为来源不同特殊处理评分逻辑。\n"
    "10. score 只衡量内容与用户画像的相关性及内容本身价值，与发布时间和时效性完全解耦。"
    "不得因为内容较新而加分，也不得因为内容较旧或 published_at 缺失而减分；时效语义只写入下面"
    "八个 temporal 字段，交给后续确定性推荐资格、复审与排序策略处理。\n"
    "11. temporal_class 判断内容的核心价值为何会随观看时间过期，必须六选一。判断先问:内容指涉的对象(产品/技术/设备/事件/工具)是否仍在演进或迭代?\n"
    "   - breaking:价值依赖小时到数日内的即时状态，如突发新闻、实时赛果、即时行情或正在发生的事件;\n"
    "   - current:价值依赖近期语境，如政策变化、新品发布、热点讨论或近期评测;\n"
    "   - versioned:内容指涉可识别的具体对象(软件、产品、模型、工具、框架、游戏或设备版本)，这些对象本身在持续迭代，对象更新后内容价值随之衰减;不要求标题出现版本号。AI 工具/模型教程、硬件装机盘点、年度新品合集等指涉快速迭代对象的内容都归此类(若语境很新则归 current);\n"
    "   - evergreen:原理、通用知识、食谱、故事、纪录片、通用教程等，价值不显著依赖当前时间;但若教程/科普具体指涉仍在快速迭代的工具或产品，应归 versioned 而非 evergreen;\n"
    "   - historical:核心价值正是对已闭合、不再演进对象的回顾、考据、档案、经典作品或过去事件的历史语境。若指涉对象仍在演进或迭代，即使采用'盘点/回顾'框架，也不得判 historical;\n"
    "   - unknown:现有内容证据不足以可靠判断。\n"
    "12. 分类看核心价值，不按内容格式、平台或发现路径贴标签。标题里的“今天”“最新”、年份、"
    "日期词不能单独决定分类，trending/search/feed 也不能决定分类。例如 Python 概念讲解是 evergreen，"
    "Python 3.8 安装教程是 versioned，新手机发布评测是 current，刚结束赛事的赛果是 breaking，"
    "多年后赛事复盘是 historical。'盘点/回顾/年度总结/装机'框架本身不等于 historical:"
    "「CES 2025 年度必看科技精品」「中国硬件都有啥?装机盘点」指涉仍在迭代的硬件产品，归 current 或 versioned;"
    "「2020 疫情全纪录」指涉已闭合事件，才是 historical。\n"
    "13. temporal_confidence 是对 temporal_class 判断的置信度(0-1)，不是内容质量、相关性或新鲜度。"
    "当 temporal_class=unknown 时必须输出 temporal_confidence=0 且 temporal_reason=空字符串；其余五类的 "
    "temporal_reason 用一句精炼中文说明核心价值为何会或不会过期。published_at 是来源提供的权威"
    "发布时间，evaluation_context.evaluated_at 是本次评估的权威时间基准；不得根据模型知识截止时间"
    "或标题年份推测发布时间。时间字段缺失或无效时仍可按内容语义分类，但不得猜测具体年龄。\n"
    "14. temporal_validity_mode 必须五选一:none、explicit_deadline、event_state、version_state、"
    "freshness_only。只有输入 title、description、body_text 或 published_label 明确写出截止日期、"
    "具体时刻和时区，才可使用 explicit_deadline，并把规范化的带时区 RFC3339 时刻写入 "
    "temporal_valid_until；日期-only、缺具体时刻或缺时区时不得使用 explicit_deadline；不得把"
    "发布时间、评估时间、常识或模型知识当截止时间。其它 mode 的 temporal_valid_until 必须为空串。\n"
    "15. temporal_scope 必须三选一:none、core、hook。core 表示过期会使内容核心价值失效，hook 表示"
    "只让标题钩子/限时入口失效而正文仍有价值；没有具体时效主张时用 none。temporal_evidence 必须是"
    "上述输入文本中的一段连续原文，不得改写或生成；所有非 none mode 都必须提供逐字证据，找不到时"
    "只能输出 none。即使核心正文是 evergreen/historical，若标题中的‘今天/最新/限时’钩子会过期，"
    "也可输出 freshness_only + hook 并引用该钩子；hook 永远不代表核心正文失效。\n"
    "例如 evergreen 教程标题中的‘限时免费领取’，或 historical 纪录片标题中的‘今晚首播’，都可"
    "输出 freshness_only + hook，逐字引用该标题钩子，并把 temporal_state 设为 unknown。\n"
    "16. temporal_state 必须四选一:unknown、active、expired、superseded。none、"
    "freshness_only、explicit_deadline 必须输出 unknown；event_state 只能输出 active 或 expired；"
    "version_state 只能输出 active 或 superseded。expired/superseded 必须由 temporal_evidence 的"
    "逐字原文明示事件已经结束或版本已被替代，不能根据发布时间、年龄、evaluation_context、常识或"
    "模型知识推断；active 同样必须有逐字、无条件的当前状态证据。条件、假设、可能性或未来态句子"
    "不能证明任何 state，例如‘如果支持版本发生变化就重新核验’不能作为 active 证据；应引用"
    "‘Temporal V2 仍是当前受支持版本’这类直接陈述。\n"
    "17. temporal_class=unknown 时，五个新字段必须严格为 temporal_validity_mode=none、"
    "temporal_valid_until=空串、temporal_scope=none、temporal_evidence=空串、"
    "temporal_state=unknown。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "{\n"
    '  "score": 0.78,\n'
    '  "reason": "主题契合画像中的长期兴趣,内容角度有增量",\n'
    '  "topic_group": "生活方式",\n'
    '  "style_key": "social_chat",\n'
    '  "franchise_key": "",\n'
    '  "temporal_class": "evergreen",\n'
    '  "temporal_confidence": 0.91,\n'
    '  "temporal_reason": "核心价值不依赖当前时间",\n'
    '  "temporal_validity_mode": "none",\n'
    '  "temporal_valid_until": "",\n'
    '  "temporal_scope": "none",\n'
    '  "temporal_evidence": "",\n'
    '  "temporal_state": "unknown"\n'
    "}\n"
    "</output_schema>"
)


def build_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    source_context: str = "",
    source_platform: str = "bilibili",
    evaluated_at: str = "",
) -> list[dict[str, str]]:
    """Build a structured prompt for single-item content relevance evaluation.

    Args:
        profile_summary: User profile summary.
        content_summary: Content metadata.
        source_context: Discovery context hint (e.g. search / trending / explore).
        source_platform: Platform identifier for dynamic prompt wording.
        evaluated_at: Authoritative UTC reference time for temporal classification.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT`` (100% static).
    All variables live in ``user_prompt``.
    """
    user_prompt = "\n\n".join(
        [
            "<source_context>",
            source_context or "(unspecified)",
            "</source_context>",
            "<source_platform>",
            source_platform or "bilibili",
            "</source_platform>",
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<evaluation_context>",
            json.dumps(
                {"evaluated_at": evaluated_at or "(unspecified)"},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</evaluation_context>",
            "<content_summary>",
            json.dumps(
                _normalize_content_style_fields(content_summary),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</content_summary>",
        ]
    )
    return [
        {"role": "system", "content": _SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# Module-level constant: 100% static system prompt for batch content
# evaluation. This is what gets cached across all calls, so it MUST NOT
# include any per-call variables (source platform, discovery context,
# profile data — all of those go in user_prompt). Provider-side prompt
# cache (DeepSeek 90% / OpenAI 50% / Claude 90% / Gemini 75% off) only
# fires when the prefix is byte-identical across calls.
#
# Reason-diet floor (v0.3.171): rule 3a bakes a fixed 0.5 skip threshold (see
# the single-eval constant above for the full rationale) — a literal constant,
# never the runtime ``admission_min_score``, so the prefix stays byte-stable and
# stays strictly below every admission path (0.60 default, 0.58 explore).
_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你要批量评估多个候选内容与一个用户画像的匹配度。"
    "下面 user 消息会按稳定性顺序给出画像层(<profile_core>、<profile_life_context>、"
    "<profile_interests>、<profile_style_context>、<profile_recent_context>)、"
    "<source_platform>(平台)、"
    "<source_context>(发现路径)、<evaluation_context>(评估时间)、"
    "<content_batch>(本批候选),你按下面规则打分。\n"
    "</task>\n\n"
    "<rules>\n"
    '1. 输出必须是严格 JSON 对象,不要附带解释。顶层只包含 "results" 数组。\n'
    "2. results 数组长度必须与输入内容数量一致,顺序一一对应。\n"
    "3. 每项必须原样带回输入里的 bvid 或 content_id,并包含 score(0-1)、"
    "reason、topic_group(2-4词粗分类)、style_key(13选1)、"
    "franchise_key(可空)、temporal_class、temporal_confidence、temporal_reason、"
    "temporal_validity_mode、temporal_valid_until、temporal_scope、temporal_evidence、"
    "temporal_state。\n"
    "3a. reason 仅供内部诊断,不是面向用户的推荐文案。写法(省 token):"
    "score 严格低于 0.5 的条目,reason 必须写成空串 "
    '""(这些条目达不到准入门槛、会被直接丢弃,写理由是纯浪费);'
    "score 大于等于 0.5 的条目,reason 写一句精炼中文,"
    "不超过 30 个 Unicode 字符,说明内容与画像匹配的依据。\n"
    "4. 除 explore 外，发现路径和平台只提供上下文，不得影响评分标准:"
    "search、trending、hot、feed、related_chain、channel、creator 等所有非 explore 候选"
    "都必须按内容与用户画像的真实匹配度统一评分;"
    "不得因为内容热门、来自推荐流、命中搜索词、沿相关推荐获得、来自订阅频道或得到平台算法背书，"
    "就设置基础分、自动加分、降低门槛或事后编造画像关联。"
    "明显不匹配画像的内容必须允许低于 admission 门槛。"
    "只有 explore 允许主题陌生，但内容仍需具备具体、可信的可看性和吸引力，"
    "不能仅因为心理需求抽象匹配就给高分，过于学术、艰深、小众的内容应适当降分。\n"
    "5. topic_group 规则:2-4 个中文词的粗分类,同主题不同切面统一。"
    "语义相同必须用同一词(AI/人工智能/机器学习 统一为 人工智能)。\n"
    "6. style_key(13选1) 描述用户消费这条内容时的观看状态,不是题材分类。"
    "必须从以下观看模式中选一个:\n"
    f"{STYLE_KEY_PROMPT_TEXT}\n"
    "7. franchise_key 规则:内容如果明确属于某个具体 IP / 系列 / 作品 / 品牌,"
    "填它的规范名(中文优先),用于跨 topic_group 的同 IP 去重。例:\n"
    "   - 「AI 重绘原神地图」「提瓦特摄影」「蒙德角色真实化」"
    '→ franchise_key = "原神"\n'
    "   - 「星穹铁道 1.6 实战」「崩铁 角色养成」"
    '→ franchise_key = "崩坏:星穹铁道"\n'
    '   - 「ChatGPT 工作流」「OpenAI 新模型」 → franchise_key = "ChatGPT"\n'
    '   - 「黑神话悟空 二周目」 → franchise_key = "黑神话:悟空"\n'
    '   - 「番茄炒蛋 5 分钟教程」「读书博主 推荐书单」 → franchise_key = ""'
    "(一般科普 / 美食 / 通用资讯都填空字符串,不要硬凑)\n"
    "   - 同一 IP 必须用相同写法,不要在「原神」「Genshin」「米哈游 原神」之间切换。\n"
    "   - **batch 一致性强约束 (v0.3.31+)**:在为整个 batch 标 franchise_key 之前,"
    "先扫一遍 batch 里所有 title,识别出现 ≥ 2 次的中文 IP / 剧名 / 作品名 / 系列名 / "
    "游戏名 / UP 主名 / 频道名(含集数后缀变体,例如「风犬少年的天空 01」「风犬少年的天空 07」"
    "应识别为同 IP「风犬少年的天空」)。**所有命中同一 IP 的 item 必须填同一个 franchise_key**,"
    "不允许部分填部分留空。这条规则比规则 7 前面的「明确属于」判定更强:只要在本 batch 内"
    "已经出现了 2 次同名 IP,后续命中的 item 即便单看不那么「明确」,也必须填上。\n"
    "8. 评分要尊重画像里的多样性诉求,双向保护:\n"
    "   - 如果 depth_preference 不高、preferred_duration 偏短,"
    "或 humor_preference 偏高,不要把学术艰深、入口很高的内容误判成高匹配;"
    "讲法轻松但不空的内容同样可以高分。\n"
    "   - 反过来,如果 depth_preference 偏高、preferred_duration 偏长,"
    "但 humor_preference >= 0.4、exploration_openness >= 0.6,"
    '或 cognitive_style 里写明 "兼顾/调节/穿插轻松" 这类双轨倾向,'
    "说明用户也需要轻内容做心理调节、喘气。这时 mood_release / social_chat / "
    "daily_wander / story_immersion / aesthetic_browse 观看模式的内容只要本身可看(话题清晰、"
    'UP 主观察角度有意思),不要因为"不够深"就一律压到 0.5 以下,'
    "应当给到 0.6-0.75,与画像中的娱乐/二次元/生活类兴趣标签保持权重一致。\n"
    "9. 不同 source_platform(bilibili / xiaohongshu / 其他)的内容标签同 schema,"
    "不要因为来源不同特殊处理评分逻辑。\n"
    "10. When content_batch items include source_platform/source_strategy/content_type, "
    "use those per-item fields as the authoritative platform context. "
    "Do not lower or raise preference score merely because content comes from a "
    "different platform; score every item against the same Soul-profile rubric. "
    "对 content_type 为 tweet / thread 的纯文本条目(标题往往只是正文首行),"
    "请以该条目的 body_text 字段为内容主体来判断匹配度,而不是只看 title。\n"
    "10a. content_batch 里的互动指标(view_count / like_count / collect_count / "
    "comment_count / share_count / favorite_count 等)只能作为辅助上下文,用于判断"
    "内容是否有平台牵引力、收藏意图或讨论强度,不能覆盖内容与画像的真实匹配度。"
    "高热度不能拯救明显不匹配的内容;低热度也不能惩罚高度契合的小众内容。"
    "小红书 collect_count 比被动浏览更接近真实兴趣;X/Twitter 的 body_text 仍是主信号。\n"
    "10b. 多模态封面图规则:当 content_batch item 含有 cover_image_ref 时,"
    "它的值形如 cover:<content_id>,对应同一 user 消息中紧随文字锚点"
    " `Cover image cover:<content_id> ...` 后面的图片。评分时必须结合该头图 / 封面图"
    "与 title、body_text、description、tags、互动指标一起判断主题、风格、视觉质感和点击诱因;"
    "如果图片与文本存在冲突,把可见图像证据作为辅助修正,但不要仅凭封面热闹给高分。"
    "没有 cover_image_ref 的条目表示没有可用图片,只按文本字段判断,不要猜测缺失图片。\n"
    "10c. score 只衡量内容与用户画像的相关性及内容本身价值，与发布时间和时效性完全解耦。"
    "不得因为内容较新而加分，也不得因为内容较旧或 published_at 缺失而减分；时效语义只写入"
    "八个 temporal 字段，交给后续确定性推荐资格、复审与排序策略处理。\n"
    "11. 当 user 消息携带 `<negative_examples>` 时,把这些标题视为用户最近"
    "**明确不喜欢**的样本——理由可能是快速划走 (`quick_exit`) 或显式负反馈"
    " (`explicit_negative`)。\n"
    "12. 对每个候选项,先与 `<negative_examples>` 中的标题做**结构 / 话术 / "
    "商业意图**层面的比较;若高度相似(同款震惊体、同款保姆级全攻略、同款月入过万"
    "钓贴),`integration_fit` 与 `interest_overlap` 必须显著降低,不要被表面话题词"
    "吸引而错给高分。比较的是**话术模式**,不是关键词重叠。\n"
    "13. profile_interests.disliked_topics 是长期避雷项;候选命中这些主题或话术模式时,"
    "score 必须下调,不要把它们当成 interests 的反向补充来加分。\n"
    "14. temporal_class 判断内容的核心价值为何会随观看时间过期，必须六选一。判断先问:内容指涉的对象(产品/技术/设备/事件/工具)是否仍在演进或迭代?"
    "breaking(突发新闻、实时赛果、即时行情或正在发生事件，价值通常以小时到数日计)、"
    "current(政策变化、新品发布、热点讨论或近期评测)、"
    "versioned(内容指涉可识别的具体对象——软件、产品、模型、工具、框架、游戏或设备版本——这些对象本身在持续迭代，对象更新后内容价值随之衰减;不要求标题出现版本号。AI 工具/模型教程、硬件装机盘点、年度新品合集等指涉快速迭代对象的内容都归此类，若语境很新则归 current)、"
    "evergreen(原理、通用知识、食谱、故事、纪录片或通用教程;但若教程/科普具体指涉仍在快速迭代的工具或产品，应归 versioned 而非 evergreen)、"
    "historical(核心价值是对已闭合、不再演进对象的回顾、考据、档案、经典作品或过去事件的历史语境;若指涉对象仍在演进或迭代，即使采用'盘点/回顾'框架，也不得判 historical)、"
    "unknown(证据不足)。\n"
    "15. 分类看核心价值，不按格式、平台或发现路径贴标签。标题里的“今天”“最新”、年份、日期词"
    "不能单独决定分类，trending/search/feed 也不能决定分类。例如 Python 概念讲解是 evergreen，"
    "Python 3.8 安装教程是 versioned，新手机发布评测是 current，刚结束赛事的赛果是 breaking，"
    "多年后赛事复盘是 historical。'盘点/回顾/年度总结/装机'框架本身不等于 historical:"
    "「CES 2025 年度必看科技精品」「中国硬件都有啥?装机盘点」指涉仍在迭代的硬件产品，归 current 或 versioned;"
    "「2020 疫情全纪录」指涉已闭合事件，才是 historical。\n"
    "16. temporal_confidence 是对 temporal_class 判断的置信度(0-1)，不是内容质量、相关性或新鲜度。"
    "当 temporal_class=unknown 时必须输出 temporal_confidence=0 且 temporal_reason=空字符串；其余五类的 "
    "temporal_reason 用一句精炼中文说明核心价值为何会或不会过期。published_at 是来源提供的权威"
    "发布时间，evaluation_context.evaluated_at 是本次评估的权威时间基准；不得根据模型知识截止时间"
    "或标题年份推测发布时间。时间字段缺失或无效时仍可按内容语义分类，但不得猜测具体年龄。\n"
    "17. temporal_validity_mode 必须五选一:none、explicit_deadline、event_state、version_state、"
    "freshness_only。只有当前 item 的 title、description、body_text 或 published_label 明确写出截止"
    "日期、具体时刻和时区，才可用 explicit_deadline，并把规范化的带时区 RFC3339 时刻写入"
    "temporal_valid_until；不得从发布时间、评估时间、其它 item、常识或模型知识推断截止时间。"
    "日期-only、缺具体时刻或缺时区时不得使用 explicit_deadline；其它 mode 的 "
    "temporal_valid_until 必须为空串。\n"
    "18. temporal_scope 必须三选一:none、core、hook。core 表示核心价值失效，hook 表示只有标题钩子/"
    "限时入口失效而正文仍有价值。temporal_evidence 必须是当前 item 输入文本中的连续原文，不得改写、"
    "跨 item 拼接或生成；所有非 none mode 都必须给逐字证据，找不到时只能输出 none。即使核心正文是"
    "evergreen/historical，若标题里的‘今天/最新/限时’钩子会过期，也可输出 freshness_only + hook 并"
    "引用该钩子；hook 永远不表示核心正文失效。\n"
    "例如 evergreen 教程标题中的‘限时免费领取’，或 historical 纪录片标题中的‘今晚首播’，都可"
    "输出 freshness_only + hook，逐字引用该标题钩子，并把 temporal_state 设为 unknown。\n"
    "19. temporal_state 必须四选一:unknown、active、expired、superseded。none、freshness_only、"
    "explicit_deadline 必须为 unknown；event_state 只能为 active/expired；version_state 只能为"
    "active/superseded。expired/superseded 必须由当前 item 的 temporal_evidence 逐字明示事件已结束或"
    "版本已替代，不能根据发布时间、年龄、evaluated_at、其它 item、常识或模型知识推断；active 也必须"
    "有逐字、无条件的当前状态证据。条件、假设、可能性或未来态句子不能证明任何 state，例如"
    "‘如果支持版本发生变化就重新核验’不能作为 active 证据；应引用‘Temporal V2 仍是当前受支持"
    "版本’这类直接陈述。temporal_class=unknown 时五个新字段必须依次为 none、空串、none、空串、"
    "unknown。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "{\n"
    '  "results": [\n'
    '    {"bvid": "BV1xxx", "score": 0.78, "reason": "...", "topic_group": "认知科学", '
    '"style_key": "deep_focus", "franchise_key": "", "temporal_class": "evergreen", '
    '"temporal_confidence": 0.91, "temporal_reason": "核心价值不依赖当前时间", '
    '"temporal_validity_mode": "none", "temporal_valid_until": "", '
    '"temporal_scope": "none", "temporal_evidence": "", "temporal_state": "unknown"},\n'
    '    {"bvid": "BV2xxx", "score": 0.72, "reason": "...", "topic_group": "游戏摄影", '
    '"style_key": "aesthetic_browse", "franchise_key": "原神", '
    '"temporal_class": "versioned", "temporal_confidence": 0.82, '
    '"temporal_reason": "内容依赖游戏版本", "temporal_validity_mode": "version_state", '
    '"temporal_valid_until": "", "temporal_scope": "core", '
    '"temporal_evidence": "当前游戏版本", "temporal_state": "active"},\n'
    '    {"bvid": "BV3xxx", "score": 0.45, "reason": "", "topic_group": "美食", '
    '"style_key": "social_chat", "franchise_key": "", "temporal_class": "current", '
    '"temporal_confidence": 0.74, "temporal_reason": "讨论依赖近期语境", '
    '"temporal_validity_mode": "freshness_only", "temporal_valid_until": "", '
    '"temporal_scope": "core", "temporal_evidence": "近期讨论", '
    '"temporal_state": "unknown"}\n'
    "  ]\n"
    "}\n"
    "</output_schema>"
)


def _build_sparse_batch_evaluation_system_prompt() -> str:
    """Return the static local-ID contract shared by sparse transports."""

    replacements = (
        (
            "3. 每项必须原样带回输入里的 bvid 或 content_id,并包含 score(0-1)、"
            "reason、topic_group(2-4词粗分类)、style_key(13选1)、"
            "franchise_key(可空)、temporal_class、temporal_confidence、temporal_reason、"
            "temporal_validity_mode、temporal_valid_until、temporal_scope、temporal_evidence、"
            "temporal_state。\n",
            "3. content_batch 编码一个 canonical batch,可能是含 defaults/items 的 JSON,"
            "也可能是 ROW-WIRE-V1 表；表中的 defaults、columns、row 与同名 canonical "
            "字段完全等价。defaults 是所有 items/rows 共享的默认值,每项同名字段优先。"
            "每项包含请求内局部 id、title、author,以及非空的内容/互动字段。"
            "每项必须原样带回输入里的 id,并包含 score(0-1)、reason、"
            "topic_group(2-4词粗分类)、style_key(13选1)、franchise_key(可空)、"
            "temporal_class、temporal_confidence、temporal_reason、temporal_validity_mode、"
            "temporal_valid_until、temporal_scope、temporal_evidence、temporal_state。\n",
        ),
        (
            "10. When content_batch items include source_platform/source_strategy/content_type, "
            "use those per-item fields as the authoritative platform context. "
            "Do not lower or raise preference score merely because content comes from a "
            "different platform; score every item against the same Soul-profile rubric. ",
            "10. Resolve source_platform, content_type and mode from each canonical item, "
            "falling back to content_batch.defaults when the item omits that field. "
            "Only mode=explore receives the explore exception; mode=normal covers every "
            "other discovery path. Do not lower or raise preference score merely because "
            "content comes from a different platform; score every item against the same "
            "Soul-profile rubric. ",
        ),
        (
            "它的值形如 cover:<content_id>,对应同一 user 消息中紧随文字锚点"
            " `Cover image cover:<content_id> ...` 后面的图片。评分时必须结合该头图 / 封面图",
            "它的值形如 cover:<id>,对应同一 user 消息中紧随文字锚点"
            " `Cover image cover:<id> ...` 后面的图片。评分时必须结合该头图 / 封面图",
        ),
        ('{"bvid": "BV1xxx"', '{"id": "0"'),
        ('{"bvid": "BV2xxx"', '{"id": "1"'),
        ('{"bvid": "BV3xxx"', '{"id": "2"'),
    )
    prompt = _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    for production_text, sparse_text in replacements:
        if prompt.count(production_text) != 1:
            raise RuntimeError("sparse evaluator system prompt is stale")
        prompt = prompt.replace(production_text, sparse_text, 1)
    return prompt


_SPARSE_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT = _build_sparse_batch_evaluation_system_prompt()


def build_batch_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    profile_blocks: list[str] | None = None,
    content_items: list[dict[str, object]],
    source_context: str = "",
    source_platform: str = "bilibili",
    negative_examples: list[dict[str, object]] | None = None,
    evaluated_at: str = "",
    compact_json: bool = False,
    candidate_block: str | None = None,
    local_result_ids: bool = False,
) -> list[dict[str, str]]:
    """Build a prompt that evaluates multiple content items in one LLM call.

    Same rules as single evaluation, but processes a batch and returns
    a JSON array of results keyed by item index.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT`` — 100% static
    across all calls, so the entire instruction block is cache-eligible.
    v0.3.x+ eval callers may pass pre-rendered ``profile_blocks`` ordered
    from stable core profile to volatile recent context; unchanged layers are
    reused by the caller's render cache and keep the provider-visible prefix
    byte-stable. The fallback path still serializes ``profile_summary`` as one
    block for older call sites.

    v0.3.x: optional ``negative_examples`` block sits between
    ``<source_context>`` and ``<content_batch>``, carrying recent
    quick-exit / explicit-negative titles for the model to pattern-match
    against. When ``None`` or empty the block is omitted entirely so the
    user-message bytes are identical to the no-examples path (cache
    prefix unchanged for cold-start users). System prompt picks up two
    permanent rules about how to consume the block (rules 10 + 11) and
    stays call-invariant after that one-time template change.

    v0.3.x: discovery evaluation may include item-level ``related_interests``
    entries inside ``content_items``. They are per-candidate name-string recall
    hints from the tail interest pool (ranks beyond the compact block's top
    64), intentionally kept out of the stable profile blocks so provider
    prompt-cache prefixes remain byte-stable.

    ``compact_json`` is an experiment seam for deterministic JSON whitespace
    removal. It never changes field names or values, and defaults to the
    historical indented rollback bytes.

    ``candidate_block`` and ``local_result_ids`` carry the production sparse
    candidate wire and request-local result identity. The block is already
    rendered by the shared transport layer; the static local-ID system contract
    is shared by production sparse JSON and replay-only row wire. Leaving both
    arguments disabled preserves the historical explicit-``production``
    rollback prompt bytes.
    """

    if (candidate_block is not None) != local_result_ids:
        raise ValueError("candidate_block and local_result_ids must be enabled together")

    def render_json(value: object) -> str:
        if compact_json:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    user_blocks: list[str] = (
        list(profile_blocks)
        if profile_blocks
        else [
            "<profile_summary>",
            render_json(profile_summary),
            "</profile_summary>",
        ]
    )
    user_blocks.extend(
        [
            "<source_platform>",
            source_platform or "bilibili",
            "</source_platform>",
            "<source_context>",
            source_context or "(unspecified)",
            "</source_context>",
        ]
    )
    if negative_examples:
        user_blocks.extend(
            [
                "<negative_examples>",
                render_json(negative_examples),
                "</negative_examples>",
            ]
        )
    user_blocks.extend(
        [
            "<evaluation_context>",
            render_json({"evaluated_at": evaluated_at or "(unspecified)"}),
            "</evaluation_context>",
        ]
    )
    user_blocks.extend(
        [
            "<content_batch>",
            (
                candidate_block
                if candidate_block is not None
                else render_json([_normalize_content_style_fields(item) for item in content_items])
            ),
            "</content_batch>",
        ]
    )
    user_prompt = "\n\n".join(user_blocks)
    return [
        {
            "role": "system",
            "content": (
                _SPARSE_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
                if local_result_ids
                else _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for single-item recommendation expression.
# Platform / tone / persona variables live in user_prompt prefix.
_RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT = """
<task>
你要像一个真正懂这个人的朋友一样,给出一段推荐这条候选内容的话。下面 user 消息会给出
<source_platform>(平台,决定友谊基调)、<tone_profile>(语气参数)、
<profile_summary>(画像)、<content_summary>(候选)。
</task>

<rules>
1. 输出必须是严格 JSON,不要附带解释。
2. expression 必须是 50 到 150 字的中文口语表达,像朋友私聊,不像算法推荐。
   如果 source_platform 是 bilibili,可以用"老 B 友"基调和 B 站语境;
   xiaohongshu 用更生活化的姐妹/朋友语气;其他平台保持中性朋友感。
3. expression 要解释"为什么这条内容会对上这个人的胃口",必须引用至少一个具体内容细节
   (如视频/笔记标题中的关键词、作者特点、或内容的独特切入角度),不要说空话。
   如果 content_summary.content_type 是 tweet / thread,标题只是正文首行,
   请以 content_summary.body_text 为内容主体来引用具体细节。
4. topic_label 需要是轻度个性化的主题标签,不要只写泛分类词。
5. 避免机械解释腔、广告腔和"根据你的兴趣""你可能会喜欢"这类算法套话。
6. 禁止使用以下模板词:信息密度、高质量、深度好文、值得一看、强烈推荐、不容错过。
   用具体描述代替泛泛评价。
7. 如果内容来自 explore (跨域发现),expression 要解释这个陌生领域和用户的哪种
   认知偏好/深层需求产生了关联,让用户觉得"虽然没想过但确实想看"。
8. 如果 profile_summary.style 里 depth_preference 不高、preferred_duration 偏短,
   或 humor_preference 偏高,expression 要更轻、更顺口,少用"认知偏好 / 底层结构 /
   深层需求"这类抽象词,不要把推荐说得比内容本身还硬。
9. 如果 content_summary.style_key 是 daily_wander / social_chat / mood_release /
   decision_support / story_immersion / aesthetic_browse / ambient_companion / live_pulse,
   优先从人物、场景、信息点、情绪或使用场景切口来推荐,
   不要硬写成"系统闭环 / 底层逻辑 / 认知防御"。
10. 严格遵循 <tone_profile> 里给的密度 / 温度 / 梗感 / 直给度 4 个参数。
11. 避开 profile_summary.disliked_topics 中的主题或话术模式；如果候选明显命中这些避雷点,
    不要热情背书,只能保守说明差异化理由,且不得把 disliked topic 包装成用户偏好。
</rules>

<output_schema>
{
  "expression": "这个 UP 主拿液压机去压各种日用品,看着无厘头,"
    "但你仔细看他每次都会慢放形变过程——其实暗合材料力学那套东西,"
    "你搞机械的应该会觉得有点意思。",
  "topic_label": "藏在整活视频里的材料力学"
}
</output_schema>
""".strip()


def build_recommendation_expression_prompt(
    *,
    profile_summary: dict[str, object],
    profile_blocks: list[str] | None = None,
    content_summary: dict[str, object],
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT`` (100% static).
    Platform label / tone profile / profile / content all live in
    ``user_prompt``. Callers may pass pre-rendered layered profile blocks,
    which are placed before platform / tone / content so the provider cache
    can reuse the stable profile prefix across platform and copy changes.
    """
    user_blocks = [
        *_profile_prompt_blocks(profile_summary, profile_blocks),
        "<source_platform>",
        source_platform or "bilibili",
        "</source_platform>",
        "<tone_profile>",
        _render_tone_profile(tone_profile, {source_platform: 1.0}),
        "</tone_profile>",
        "<content_summary>",
        json.dumps(
            _normalize_content_style_fields(content_summary),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "</content_summary>",
    ]
    user_prompt = "\n\n".join(user_blocks)
    return [
        {"role": "system", "content": _RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for batch recommendation expression.
_BATCH_EXPRESSION_SYSTEM_PROMPT = (
    "<task>\n"
    "你要像一个真正懂这个人的朋友一样,为多条候选内容各写一段推荐话。"
    "下面 user 消息会给出 <source_platform>(平台)、<tone_profile>(语气)、"
    "<profile_summary>(画像)、<content_batch>(本批候选)。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON 数组,数组长度与输入内容数量一致,顺序一一对应。\n"
    "2. 每项必须原样带回输入里的 bvid 或 content_id,并包含 "
    "expression(50-150字中文口语) 和 topic_label(个性化主题标签)。\n"
    "3. expression 像朋友私聊。bilibili 用'老 B 友'语境,xiaohongshu 用更生活化的姐妹/朋友语气,"
    "其他平台保持中性朋友感。必须引用至少一个具体内容细节(标题关键词、作者特点、独特切入角度),"
    "不要说空话。content_type 为 tweet / thread 的纯文本条目,以 body_text 字段为内容主体来引用。\n"
    "4. 避免:算法套话、信息密度、高质量、深度好文、值得一看、强烈推荐。\n"
    "5. explore 来源的内容要解释陌生领域和用户认知偏好的关联。\n"
    "6. 每条 expression 的开头措辞必须不同,禁止重复同一句式。\n"
    "7. 如果 profile_summary.style 显示 depth_preference 不高、preferred_duration 偏短,"
    "或 humor_preference 偏高,整体措辞要更轻、更顺口,不要把轻内容硬写成分析报告。\n"
    "8. 如果某条 content.style_key 是 daily_wander / social_chat / mood_release / "
    "decision_support / story_immersion / aesthetic_browse / ambient_companion / live_pulse,"
    "就优先从人物、场景、信息点、情绪或使用场景切口下笔,"
    "不要把它写成心理机制拆解。\n"
    "9. 严格遵循 <tone_profile> 里给的密度 / 温度 / 梗感 / 直给度 4 个参数。\n"
    "10. 避开 profile_summary.disliked_topics 中的主题或话术模式;如果候选明显命中这些避雷点,"
    "不要热情背书,只能保守说明差异化理由,且不得把 disliked topic 包装成用户偏好。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "[\n"
    '  {"bvid": "BV1xxx", "expression": "这条...", "topic_label": "xxx"},\n'
    '  {"bvid": "BV2xxx", "expression": "这个UP主...", "topic_label": "yyy"}\n'
    "]\n"
    "</output_schema>"
)


def build_batch_expression_prompt(
    *,
    profile_summary: dict[str, object],
    profile_blocks: list[str] | None = None,
    content_items: list[dict[str, object]],
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a prompt that generates expressions for multiple items in one call.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_BATCH_EXPRESSION_SYSTEM_PROMPT`` (100% static).
    """
    user_blocks = [
        *_profile_prompt_blocks(profile_summary, profile_blocks),
        "<source_platform>",
        source_platform or "bilibili",
        "</source_platform>",
        "<tone_profile>",
        _render_tone_profile(tone_profile, {source_platform: 1.0}),
        "</tone_profile>",
        "<content_batch>",
        json.dumps(
            [_normalize_content_style_fields(item) for item in content_items],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "</content_batch>",
    ]
    user_prompt = "\n\n".join(user_blocks)
    return [
        {"role": "system", "content": _BATCH_EXPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for probe-chat sentiment classification.
# Per-call variables (方向 / 用户发言) live in the user message — see
# ``build_probe_sentiment_prompt``. ``neutral_deferred`` = user actively asks to
# shelve the direction (routes to the defer state machine); plain ``neutral`` =
# undecided (no state change). ``neutral_ambiguous`` is intentionally NOT a label.
_PROBE_SENTIMENT_SYSTEM_PROMPT = (
    "任务：判断用户对一个兴趣方向的态度。\n\n"
    "规则：\n"
    "1. 只输出一个英文标签："
    "strong_positive、weak_positive、neutral_deferred、neutral、negative\n"
    "2. 不要输出任何其他内容\n\n"
    "判断标准：\n"
    "- strong_positive = 用户明确要加入画像、以后多推、这就是想看的\n"
    "- weak_positive = 用户表达轻微兴趣、可以看看、偶尔看看，但未直接确认\n"
    "- negative = 用户表达了不喜欢、不感兴趣、太难、太无聊\n"
    "- neutral_deferred = 用户主动要求先放一放：明确说「暂时忽略」「先放着」「稍后再看」「以后再说」\n"
    "- neutral = 态度不明确、还在犹豫、没想好（如「不确定」「再看看」「不好说」）\n\n"
    "方向与用户发言见 user 消息。\n"
)


def build_probe_sentiment_prompt(
    *,
    domain: str,
    user_message: str,
) -> list[dict[str, str]]:
    """Build the probe-chat sentiment classification prompt.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level constant
    ``_PROBE_SENTIMENT_SYSTEM_PROMPT`` (100% static). The direction and the
    user's message live in ``user_prompt``.
    """
    user_prompt = f"方向：{domain}\n用户：{user_message}"
    return [
        {"role": "system", "content": _PROBE_SENTIMENT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_explore_domains_prompt(
    *,
    profile_summary: dict[str, object],
    covered_topic_groups: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build a structured prompt for cross-domain exploration ideas.

    ``covered_topic_groups`` (v0.3.31+) lists topic_group labels that
    are already well-represented in the user's active recommendation
    pool. The LLM uses this as a "blind-spot guide" — it MUST avoid
    proposing domains whose evaluator-visible topic_group would land
    on any of these. Without this, explore tended to keep re-proposing
    well-covered areas (e.g. "AI 编程"、"认知科学"), and 30 candidate
    items would collapse into 8 distinct topic_groups instead of
    ~25-30. Passing the empty list / None falls back to the original
    open-ended exploration prompt.
    """
    system_prompt = """
<task>
你要为这个用户设计 3 到 5 个“高相关但有陌生感”的跨领域探索方向。
</task>

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. domain 不能直接重复用户现有高权重兴趣词。
3. 如果画像中存在 speculative_interests（猜测兴趣），至少 1 个 domain 应基于
   猜测兴趣的 domain 展开（可以细化或拓展，但核心方向要对应）。
   这些是系统推测用户可能喜欢但尚未确认的方向，优先用于探索。
4. domains 至少覆盖 3 类不同内容方向，
   例如知识解释、现实观察、审美体验、人物叙事、技术机制、社会文化；
   不要都落在同一个抽象轴上。
5. 同一母题的换皮变体最多只能保留 1 个，
   例如”博弈论 / 桌游机制 / 纳什均衡 / 策略模型”这类本质相同的方向不能同时出现。
6. 输出保持短 JSON：每个 domain 只包含 domain、novelty_level、queries 三个字段，
   不要输出解释、分类、原因或其它长文本字段。
7. novelty_level 范围必须在 0.65 到 0.95 之间；至少 3 个 domain 的 novelty_level ≥ 0.75。
8. 每个 domain 生成 2 到 3 个适合 B 站搜索的 query，query 必须具体到可直接搜索的细分话题，禁止只写宽泛大词。
9. 不同 domain 的 query 之间词汇重叠率要低；每个 query 必须包含一个内容形式词
   （如 盘点/推荐/测评/vlog/日常/吐槽/科普/体验/挑战/合集/纪录片/解说/手书/混剪），
   不同 domain 必须使用不同的形式词，以保证搜索结果在风格维度上有差异。
   整组 query 中"深度讲解/深度解析/原理"等学术向形式词最多只能出现 1 次，
   优先使用轻松、大众化的形式词。
10. 反信息茧房：不同 domain 的 query 第一个实词（核心主题词）必须两两不同，
   禁止仅替换修饰词而保留相同核心名词；至少 4 个 domain 必须来自用户
   已有兴趣领域之外的全新方向（即用户画像中未出现的领域）。
   不同 domain 之间不得共享同一个上位概念（如"城市空间"与"城市规划"共享"城市"）。
11. 心理诉求轴多样性（核心规则，违反即视为失败）：
   每个 domain 必须对应**不同**的心理诉求轴，每个轴最多只能出现一次。
   定义清单：
     - 拆解·系统·结构  ：精密机械、数学、算法、博弈、底层原理、工艺拆解
     - 感官·沉浸·审美    ：视觉/听觉/材质/光影/空间体验、ASMR、风景、艺术
     - 情绪·叙事·人物    ：纪录片人物、剧情、日常 vlog、生活故事、情感讨论
     - 文化·社会·议题    ：社会观察、亚文化、地域文化、历史人文
     - 实操·生活·烟火    ：美食、生活技能、家居、旅行、宠物、亲子
     - 运动·身体·动手    ：体育、健身、户外、动手实验
     - 幽默·吐槽·消遣    ：搞笑、鬼畜、整活、轻松吐槽
   例：5 个 domain 不许全在"拆解·系统·结构"轴里换皮（钟表/榫卯/开发板/电路/模型
   都属于同一个轴——拆解结构——这种安排是错的）；必须把 5 个槽位分散到至少 4 个不同的轴。
12. 重要：判断用户兴趣方向时**只能依赖 `interests` / `interest_domains` 字段中的明确标签**，
   不要从 core_traits、deep_needs 等人格/心理描述里的比喻或例子反推出兴趣目标
   （例如看到"钻研""精密"这类字眼就臆造出"机械结构""精密拆解"之类 domain）。
   应该看 interests 实际有什么、并在心理诉求轴清单里挑一个**还没被占用**的轴去拓展。
13. **盲区优先 (v0.3.31+)**: 如果 user 消息里给了 `<covered_topic_groups>` 块，
   表示这些 topic_group 在用户推荐池里已经堆积，本轮探索**尽量绕开**这些方向，
   优先去探索没被覆盖的领域。如果实在某条 domain 跟 covered 列表里的方向有重合，
   仍要尽量挑边缘切入点（例：covered 含"认知科学" → 不要出"思维模型/元认知"这种正中靶心的
   domain，改去"声音设计 / 城市民俗 / 工业纪录"等其它轴）。这是软规则，
   不要因此放弃生成 domain — 至少给出 5 个 domain，宁可有一个落在 covered 边缘也别返回空。
</rules>

<output_schema>
{
  "domains": [
    {
      "domain": "城市空间与建筑叙事",
      "novelty_level": 0.72,
      "queries": ["上海 里弄 改造 纪录片", "创意 建筑 盘点", "废墟 探险 vlog"]
    }
  ]
}
</output_schema>
""".strip()
    user_prompt_parts = [
        "<profile_summary>",
        json.dumps(profile_summary, ensure_ascii=False, indent=2),
        "</profile_summary>",
    ]
    # v0.3.31+: covered_topic_groups tells the LLM which topic_group
    # labels are already over-represented in the active pool. Combined
    # with the system-side rule "avoid covered_topic_groups", this
    # forces explore to actually explore — not re-propose 认知科学 /
    # AI编程 / 体育预测 each cycle when they're already in the pool.
    if covered_topic_groups:
        # Deduplicate + cap to top 12. Initially tried 30 + a hard
        # "禁止" tone in the system rule; observed DeepSeek returning
        # empty content on ~50% of explore cycles when the constraint
        # set got that tight. Top 12 is enough avoidance signal for
        # the highest-saturation topics while leaving the model room
        # to maneuver.
        seen: set[str] = set()
        unique_covered: list[str] = []
        for label in covered_topic_groups:
            normalized = (label or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_covered.append(normalized)
            if len(unique_covered) >= 12:
                break
        if unique_covered:
            user_prompt_parts.extend(
                [
                    "<covered_topic_groups>",
                    "下面这些 topic_group 在用户当前推荐池里已经堆积，本轮 explore 尽量绕开 ——"
                    "如果某条 domain 不可避免地会跟其中之一相关，挑边缘切入点（例：covered 含"
                    "「认知科学」→ 不出「思维模型/元认知」这种正中的，改去「声音设计/工业纪录」等"
                    "其它轴）。这是软提示，不要因此返回空 domain。",
                    json.dumps(unique_covered, ensure_ascii=False, indent=2),
                    "</covered_topic_groups>",
                ]
            )
    user_prompt = "\n\n".join(user_prompt_parts)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_speculation_generation_prompt(
    *,
    profile_summary: str,
    existing_speculations: list[str],
    cooldown_domains: list[str],
    confirmed_domains: list[str],
    count: int = 5,
    probe_mode_request: str | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for generating speculative interest directions."""
    system_prompt = (
        "<task>\n"
        "你像一个懂 ta 的朋友。看 ta 平时在看什么、玩什么，\n"
        "猜 ta 还可能喜欢的相似 / 相邻方向。\n"
        "目标是给出 ta 真的会点开看的内容方向，\n"
        "不是把 ta 的爱好『分析化 / 学术化』成另一个领域。\n"
        "</task>\n\n"
        "<signal_weights>\n"
        "综合用户信号时按以下权重决策：\n"
        "  ≈50%  用户的 likes 分布（直接反映 ta 实际在看什么、占比多少）\n"
        "  ≈30%  deep_needs + motivational_drivers（内在动力）\n"
        "  ≈15%  core_traits + cognitive_style（处理信息的风格）\n"
        "   ≤5%  MBTI（**仅作弱参考**）\n"
        "\n"
        "MBTI 标签本身带显著语料偏置（网上写 INTP/INTJ 的人远多于 ESFP/ESTP），\n"
        '看到"拆解 / 原理 / 审慎"这类词不要反射性套"INTP 该看什么"模板。\n'
        "当 likes 分布与 MBTI 暗示方向冲突时，**永远优先 likes**。\n"
        "</signal_weights>\n\n"
        "<rules>\n"
        "1. 每个猜测必须有 reason，说清楚『为什么 ta 也会喜欢这个』——\n"
        "   写得像朋友给朋友推荐时的『你也试试，跟你之前看的那些是一路的』那种语感。\n"
        "   不要写成『ta 喜欢 X，因为 X 反映了对 Y 的深层心理需求』这种学术分析。\n"
        "2. 不能重复已有兴趣、已在探索中的方向、或冷却期的方向。\n"
        "3. 方向应具体到可以搜索到内容（不要太抽象）。\n"
        "4. confidence 范围 0.3-0.6，越有把握越高。\n"
        "5. 多数猜测应该是『跟 ta 现在看的同一类、再往下走一点』的近距离方向，\n"
        "   少数可以远一点。近距离方向更容易被实际点击。\n"
        "6. 人格共振检验：对每个猜测自问『ta 下次打开 B 站，\n"
        "   真的会点这类内容吗？』如果不确定，降低 confidence 或换方向。\n"
        "7. 输出严格 JSON，不要附带解释。\n"
        "8. 分散性：\n"
        "   - domain 核心主题词必须无重叠（禁止同概念换皮）。\n"
        "   - 鼓励 category 多样，但**不强制两两不同** ——\n"
        "     如果用户在某 category（例如『娱乐』）是绝对主轴（权重远高于其他），\n"
        "     允许该 category 占多条不同 domain 的探针；\n"
        "     这反而比强行换 category 更贴合 ta 真实行为。\n"
        "   - experience_mode 必须从\n"
        "     knowledge / aesthetic / hands_on / people_story / wander_observe 中选择。\n"
        "   - entry_load 必须从 light / heavy 中选择。\n"
        "   - 不要让所有猜测都落在同一种观看体感上。\n"
        "9. **不要把娱乐爱好都翻译成它的『学术 / 解析 / 设计学 / 科学』版本**——\n"
        "   ta 在看番不一定是为了『考据动画产业』，可能就是想看好看的番。\n"
        "   ta 喝咖啡不一定是为了『研究萃取曲线』，可能就是喜欢咖啡馆氛围。\n"
        "   reason 和 specifics 都要尊重 ta 的实际消费姿态，\n"
        "   而不是你（LLM）作为分析师默认的『更有内容』的版本。\n"
        "10. **每条探针必须输出 probe_mode 距离带**，四选一：\n"
        "    - near：贴着用户已经明确喜欢的主题往下钻，几乎是同类内容的更具体版本。\n"
        "    - lateral：从已有 like 横向跳到相邻主题，消费体感相近，但主题不是同一个词的换皮。\n"
        "    - bridge：用某个 like 加上一条 deep_need / cognitive_style 自然桥接到较陌生方向。\n"
        "    - wildcard：证据较弱但可能打破信息茧房的挑战方向，必须保持可搜索、可点击。\n"
        "    probe_mode 只用于系统理解距离，不要把 near / lateral / bridge / wildcard 写进用户文案。\n"
        "    默认多给 near，少量给 lateral / bridge / wildcard；不要让所有探针都停在 near。\n"
        "</rules>\n\n"
        "<bridge_examples>\n"
        "（只描述结构性的延伸路径，不写具体 topic 关键词——\n"
        "具体内容由你根据用户实际 likes 自行判断填入。）\n"
        "\n"
        "合法的延伸路径模式：\n"
        "- 大类 → 小类（drill-down）：\n"
        "  用户某 category 权重很高 → 钻到该 category 下更具体的子方向。\n"
        "- 小类 → 兄弟小类（同大类内 lateral）：\n"
        "  用户某具体 like 旁边 → 同大类下另一个小类。\n"
        "- 小类 → 兄弟小类（跨大类 lateral）：\n"
        "  不同大类但消费体感接近的小类互相延伸。\n"
        "- 大类 + 小类 → 复合方向：\n"
        "  综合用户大类整体特征和某个具体小类，找一个新方向。\n"
        "\n"
        "各路径都是合法延伸。**不要默认某种路径『更深刻 / 更值得推荐』** ——\n"
        "选哪条由用户实际行为决定，不由 LLM 的『含金量』直觉决定。\n"
        "\n"
        "❌ 反面模式（每条都违反 signal_weights 或忽略 ta 实际消费姿态）：\n"
        "- 把娱乐爱好翻译成它的『学术 / 解析 / 设计学 / 科学』版本\n"
        "  （ta 看番不是为了考据动画产业，喝咖啡不是为了研究萃取曲线）\n"
        "- 用户在某 category 上权重 0.95+，结果生成 5/5 都是其他 category\n"
        "  （漏掉用户主轴，违反 signal_weights）\n"
        "- 强行 blend：每条都套『因为 ta 有 deep_need X』的同一个心理学模板\n"
        '- domain 抽象到"经济学 / 心理学 / 社会学 / 科学"层级\n'
        "  （ta 实际不会在 B 站搜这种学术词）\n"
        "</bridge_examples>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "speculations": [\n'
        "    {\n"
        '      "domain": "一级方向名称（宽泛领域）",\n'
        '      "category": "所属大类（必须两两不同）",\n'
        '      "probe_mode": "near|lateral|bridge|wildcard",\n'
        '      "reason": "朋友式说明为什么这个距离带的方向值得试试（不要露出 probe_mode）",\n'
        '      "experience_mode": "knowledge|aesthetic|hands_on|people_story|wander_observe",\n'
        '      "entry_load": "light|heavy",\n'
        '      "confidence": 0.45,\n'
        '      "specifics": [\n'
        '        "可搜索的具体话题1",\n'
        '        "可搜索的具体话题2"\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "</output_schema>\n\n"
        "<specifics_rules>\n"
        "每个 domain 必须附带 2-4 个 specifics，代表该方向下可搜索到内容的具体话题。\n"
        "specifics 不是 domain 的同义词，而是更窄的切入点。\n"
        "specifics 应该贴近 ta 实际会搜索的关键词，\n"
        "而不是该领域的『学术化命题』。\n"
        "例如：\n"
        '  ✅ domain="独立咖啡馆" → specifics=["上海独立咖啡馆探店", "手冲咖啡师 vlog", "咖啡赛事剪辑"]\n'
        '  ❌ domain="独立咖啡馆" → specifics=["萃取曲线分析", "烘焙度风味化学"]（过于学术）\n'
        "</specifics_rules>"
    )

    # Two semantically different exclude lists:
    # - existing_speculations + cooldown_domains: hard exclude (don't dive in)
    # - confirmed_domains (user's actual likes): the user's MAIN AXES.
    #   These should NOT block the LLM from drilling into them; instead
    #   they're the most relevant exploration territory.  We tell the LLM
    #   these are core axes to drill INTO, not to avoid.
    hard_exclude_list = sorted(set(existing_speculations + cooldown_domains))
    main_axes_list = sorted(set(confirmed_domains))
    hard_exclude_text = (
        "以下 domain 字符串完全相同的方向不要重复（这些是冷却期/已在探索中的方向）：\n"
        + "、".join(hard_exclude_list)
        if hard_exclude_list
        else "无"
    )
    main_axes_text = (
        "以下是用户的主轴 likes（用户已经在这些大类上花最多时间）：\n"
        + "、".join(main_axes_list)
        + "\n\n"
        "**重要**：这些不是排除项 —— 它们是用户最喜欢的轴。\n"
        "你应该**钻进这些大类**，按 rule 10 lateral 模式的几条路径\n"
        "（大类→小类 / 小类↔小类 / 大类+小类）生成具体的子方向探针，\n"
        "而不是绕开它们去找 ta 不太看的小众类。\n"
        "只是不要把 domain 字段直接写成这些大类名本身（例如不要让 domain 字段\n"
        "等于 likes 里出现的某个大类字符串）—— domain 应该是该大类下\n"
        "你自己根据用户实际行为判断出的具体子方向。"
        if main_axes_list
        else "（用户尚无明确主轴）"
    )
    user_sections = [
        "<user_profile>",
        profile_summary,
        "</user_profile>",
        "<main_axes>",
        main_axes_text,
        "</main_axes>",
        "<hard_exclude>",
        hard_exclude_text,
        "</hard_exclude>",
    ]
    if probe_mode_request:
        user_sections.extend(["<probe_mode_request>", probe_mode_request, "</probe_mode_request>"])
    user_sections.append(f"请生成 {count} 个猜测兴趣方向。")
    user_prompt = "\n\n".join(user_sections)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


_AVOIDANCE_GENERATION_SYSTEM_PROMPT = """
<task>
你要为用户生成“可能不喜欢 / 想避开”的内容方向探针。
这些探针不是推荐过滤本身，而是需要用户确认的避雷假设。
</task>

<source_modes>
每条候选必须选择一个 source_mode：
- negative_signal：从显式 dislike、thumbs_down、负向聊天或已确认 disliked_topics 延展。
- positive_boundary：从用户喜欢的领域推断其可能不喜欢的低质形态或边界。
- style_boundary：从节奏、质量、表达方式、信息密度等风格偏好推断避雷边界。
</source_modes>

<rules>
1. 输出严格 JSON，不要附带解释。
2. 每条必须是内容形态、质量、节奏、表达方式或信息增量层面的边界。
3. 不能生成敏感人格判断，不能把用户本人贴负面标签。
4. 不能重复已有 dislike、已在探测中的 avoidance、冷却期 avoidance。
5. 不能直接把正向兴趣本身当成讨厌对象；如果来自 positive_boundary，只能问具体低质形态。
6. domain 必须具体，specifics 必须列 2-4 个更窄的避雷形态。
7. experience_mode 必须从 knowledge / aesthetic / hands_on / people_story / wander_observe 中选择。
8. entry_load 必须从 light / heavy 中选择。
9. confidence 范围 0.3-0.75，越有证据越高。
10. active set 要保持多样性：同一 source_mode + 同一粗主题 / 证据源只生成一个候选；如果已有 AI positive_boundary，不要再输出 AI 教程 / 测评 / 趋势的换皮候选。
11. 每批候选要尽量覆盖不同 source_mode、experience_mode、entry_load，不要只围绕 confirmed_likes 中最强的领域扩写。
</rules>

<output_schema>
{
  "avoidances": [
    {
      "domain": "浅层热点复读",
      "reason": "用户可能不喜欢无信息增量、只复读热梗和立场的热点内容。",
      "source_mode": "negative_signal",
      "source_signal": "thumbs_down: 热点复读",
      "experience_mode": "knowledge",
      "entry_load": "light",
      "confidence": 0.62,
      "specifics": ["标题党热点解读", "无信息增量复读", "情绪化站队剪辑"]
    }
  ]
}
</output_schema>
""".strip()


def build_avoidance_generation_prompt(
    *,
    profile_summary: str | dict[str, object],
    existing_avoidances: list[str],
    existing_avoidance_details: list[dict[str, object]] | None = None,
    cooldown_domains: list[str],
    confirmed_dislikes: list[str],
    confirmed_likes: list[str],
    count: int = 5,
    source_mode_quota: dict[str, int] | None = None,
) -> list[dict[str, str]]:
    """Build a prompt for generating speculative avoidance directions."""
    payload: dict[str, object] = {
        "profile_summary": profile_summary,
        "existing_avoidances": existing_avoidances,
        "existing_avoidance_details": existing_avoidance_details or [],
        "cooldown_domains": cooldown_domains,
        "confirmed_dislikes": confirmed_dislikes,
        "confirmed_likes": confirmed_likes,
        "count": count,
    }
    if source_mode_quota:
        payload["source_mode_quota"] = source_mode_quota
    user_prompt_parts = [
        "<avoidance_generation_context>",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        "</avoidance_generation_context>",
    ]
    if source_mode_quota:
        quota_lines = [f"  - {mode}: {n} 条" for mode, n in source_mode_quota.items() if n > 0]
        user_prompt_parts.extend(
            [
                "",
                "<source_mode_distribution>",
                "本轮请按以下配额分配 source_mode（硬约束，违反即失败）：",
                *quota_lines,
                "配额为 0 的 mode 不要生成。",
                "</source_mode_distribution>",
            ]
        )
    user_prompt = "\n\n".join(user_prompt_parts)
    return [
        {"role": "system", "content": _AVOIDANCE_GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


_PROFILE_CONSOLIDATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你是用户画像的整理器。输入是若干「嫌疑重复」的主题簇（cluster），\n"
    "分为 likes（兴趣主题）和 dislikes（避雷主题）两组。\n"
    "你要对每个簇内的主题做出裁决：哪些在用户画像里表达相同的推荐意图、\n"
    "继续并存只会重复占位（应合并），哪些能带来不同推荐结果（应保留）。\n"
    "</task>\n"
    "\n"
    "<rules>\n"
    "1. 只能输出操作（op），不能输出整理后的列表。每个操作是 merge 或 keep。\n"
    "2. merge 的 members 必须从该簇的 members 中【逐字原样复制】；普通成员可用字符串，\n"
    '   同名异类成员必须用 {"name": 原名, "category": 原分类} 精确引用。\n'
    "3. 每个簇内的每个主题，必须被 merge 或 keep 恰好覆盖一次，不能遗漏、不能重复。\n"
    "4. merge 至少 2 个 members。canonical 是合并后的代表性 item 名。优先选择能准确\n"
    "   覆盖整组的简洁旧 member；只有旧 member 都只覆盖一部分时才起具体组合名。\n"
    "   新名必须与 members 同等具体或更精确，不得为了看似完整而堆砌近义词。\n"
    "5. likes 的目标是减少画像槽位重复，不限于字典意义上的严格同义词。措辞变体、\n"
    "   同粒度且推荐/搜索结果高度重叠的标签、以及加了空泛前后缀但没有新增选择价值的\n"
    "   标签都应 merge（如「搞笑」vs「娱乐搞笑」）。\n"
    "   真正的父子兴趣若会带来不同推荐结果仍须 keep（如「篮球」vs「NBA」、\n"
    "   「AI技术」vs「AI视频技术」、「游戏」vs「手机游戏」）。\n"
    "6. dislikes 组的标准更严：只合并语义几乎相同的真同义项；【严禁向上泛化】——\n"
    "   canonical 绝不能比 members 更宽泛（如把「一个案例反复切悬念拖时长」归并成\n"
    "   「低质内容」是严重错误，会误伤大量正常内容）。拿不准时一律 keep。\n"
    "7. likes 可以按「是否重复占用同一推荐意图」从宽合并，但同样不允许把具体兴趣\n"
    "   向上合并成会改变召回范围的大类。拿不准两项能否产生不同结果时才 keep。\n"
    "8. likes 成员带 category（一级分类）。同名/近名但 category 不同且语义不同的条目\n"
    "   是【同名异义】（如 苹果(科技) vs 苹果(美食)），必须分别 keep，严禁合并。\n"
    "   只有确认它们是同一概念被误标了不同分类时才 merge；此时 merge.members 和\n"
    "   keep.member 都必须使用 {name, category}，使每个同名条目可被逐一追踪。\n"
    "9. 每个簇可带 known_distinct_pairs；这些 pair 是用户回滚或当前策略下已确认要分开\n"
    "   的成员，严禁在任何 merge 中放到一起，也无需重新裁决它们之间的关系。\n"
    "10. 输出严格 JSON，不要附带解释文本。\n"
    "11. 各变量见 user 消息：likes_clusters / dislikes_clusters（各簇带 cluster_id、\n"
    "   members、known_distinct_pairs 及权重 / category 元数据）。\n"
    "</rules>\n"
    "\n"
    "<output_schema>\n"
    "{\n"
    '  "likes": [\n'
    '    {"cluster_id": "L1", "op": "merge", "members": ["AI工具与技术", "AI工具与工程实践"],\n'
    '     "canonical": "AI工程工具链", "reason": "新 item 能同时代表工具和工程实践"},\n'
    '    {"cluster_id": "L2", "op": "keep", "name": "篮球", "reason": "NBA 是其子集而非同义"},\n'
    '    {"cluster_id": "H1", "op": "merge",\n'
    '     "members": [{"name": "苹果", "category": "科技"}, {"name": "苹果", "category": "资讯"}],\n'
    '     "canonical": "苹果公司"},\n'
    '    {"cluster_id": "H1", "op": "keep", "member": {"name": "苹果", "category": "美食"}}\n'
    "  ],\n"
    '  "dislikes": [\n'
    '    {"cluster_id": "D1", "op": "merge", "members": ["偶像团体练习室内容", "偶像练习室物料"],\n'
    '     "canonical": "偶像练习室物料"}\n'
    "  ]\n"
    "}\n"
    "</output_schema>"
)


def build_profile_consolidation_prompt(
    *,
    likes_clusters: list[dict[str, object]],
    dislikes_clusters: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Build the prompt for LLM-judged consolidation of like/dislike topics.

    Each cluster dict carries ``cluster_id``, ``known_distinct_pairs`` and
    ``members`` (list of dicts with name + weight + category metadata for
    likes, plain strings for dislikes).
    System prompt is fully static (cache-friendly per CLAUDE.md convention);
    all per-call data lives in the user message with deterministic
    serialization.
    """
    user_prompt = "\n\n".join(
        [
            "<likes_clusters>",
            json.dumps(likes_clusters, ensure_ascii=False, indent=2, sort_keys=True),
            "</likes_clusters>",
            "<dislikes_clusters>",
            json.dumps(dislikes_clusters, ensure_ascii=False, indent=2, sort_keys=True),
            "</dislikes_clusters>",
        ]
    )
    return [
        {"role": "system", "content": _PROFILE_CONSOLIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


_CATEGORY_MAPPING_SYSTEM_PROMPT = (
    "<task>\n"
    "你是用户画像分类体系的迁移器。user 消息提供：vocab（固定一级分类词表）和\n"
    "categories（现存分类及各自的标签数 tag_count）。\n"
    "你要为【每一个】现存分类选择词表中【恰好一个】目标分类。\n"
    "</task>\n"
    "\n"
    "<rules>\n"
    "1. mapping 必须覆盖 categories 里的每一个分类，一个都不能漏，也不能多出输入里没有的分类。\n"
    "2. 映射目标必须逐字来自 vocab，不得发明新分类、不得返回 vocab 之外的写法。\n"
    "3. 优先语义归属（如 泛娱乐/文娱→娱乐；宠物/动物→萌宠；技术/数码/人工智能→科技；\n"
    "   二次元→动漫；商业→财经）。\n"
    "4. 现存分类本身已在 vocab 中的，映射到它自己。\n"
    "5. 实在无法归属的才映射到「其他」，不要偷懒批量扔「其他」。\n"
    "6. 输出严格 JSON，不要附带解释文本。\n"
    "</rules>\n"
    "\n"
    "<output_schema>\n"
    "{\n"
    '  "mapping": {"泛娱乐": "娱乐", "宠物": "萌宠", "内容消费方式": "其他"}\n'
    "}\n"
    "</output_schema>"
)


# Module-level constant: 100% static system prompt for the MERGED, multi-
# platform search-keyword generator (Discover backpressure refactor P1.4).
# This single call subsumes per-platform keyword builders (B站 search /
# 小红书 / 抖音 / YouTube / X / 知乎 / Reddit 等) so the profile is sent ONCE and the
# provider-side prompt cache fires on the byte-identical prefix. Per
# CLAUDE.md "LLM Prompt-Cache Convention": NOTHING per-call lives here —
# the profile, the due-platform set, each platform's need count, recent
# keywords, and avoid_* hints ALL live in the user message (<profile_summary>
# + <platforms>). The <supply_advantage> block below (P2) is STATIC per
# platform (it describes where each platform structurally has good content,
# never anything about *this* user), so it belongs in the system constant and
# the call-invariance test still holds.
PLATFORM_SUPPLY_ADVANTAGES: dict[str, str] = {
    "bilibili": (
        "学习区 / 知识科普 / 深度长视频 / 梗文化 / 技术。把兴趣做成"
        "主题 + 风格词(盘点 / 入门 / 测评 / 教程 / 整活)。"
    ),
    "xiaohongshu": (
        "生活方式 / 好物种草 / 教程攻略 / 美妆 / 体验分享。具象、带场景的"
        "长尾(教程 / 攻略 / vlog / 踩坑 / 真实体验),避免裸类目词。"
    ),
    "douyin": "短视频 / 娱乐 / 热点 / 搞笑 / 才艺。短平快、口语、跟得上当下热度。",
    "youtube": ("英文长内容 / 纪录片 / 讲座 / 国际视角。2-4 词,中英文按话题选最常见的搜索语言。"),
    "twitter": (
        "实时讨论 / 英文技术 / 观点 / 资讯。1-4 词,技术 / 小众话题尤其优先英文,华语圈话题可用中文。"
    ),
    "zhihu": (
        "知乎中文问答 / 深度回答 / 经验复盘 / 专业解释 / 观点辨析。适合"
        "问题式、场景式或概念 + 经验词的中文关键词。"
    ),
    "reddit": (
        "subreddit 经验讨论 / 技术问答 / 开源项目 / 长帖复盘 / 社区观点。"
        "优先英文关键词,1-5 词,可带 subreddit 或社区语境词。"
    ),
    "bangumi": (
        "动画 / 书籍 / 游戏 / 音乐 / 三次元作品目录。优先作品题材、IP、原作、"
        "作者、监督、制作公司、游戏平台等可检索实体,避免社媒热词。"
    ),
    "linuxdo": (
        "中文技术社区 / Linux / 开源软件 / AI 工具 / 自托管 / 开发运维 / 数码折腾。"
        "优先具体技术实体 + 教程、踩坑、部署、经验、讨论等论坛原生表达。"
    ),
    "v2ex": (
        "真实技术与生活讨论 / 经验复盘 / 折腾记录 / 求助 / 开源项目。关键词应保留"
        "问题或主题语义，优先具体 Node 语境，避免泛泛的论坛首页词。"
    ),
    "weibo": (
        "中文实时公共讨论 / 社会热点 / 娱乐文化 / 当事人回应 / 现场进展。"
        "优先具体人、事件、作品或话题实体,可搭配热议 / 回应 / 进展等微博原生语境词。"
    ),
}


def platform_supply_advantage(platform: str) -> str:
    """Return the static supply advantage text for one discovery platform."""

    return PLATFORM_SUPPLY_ADVANTAGES.get(str(platform or "").strip().lower(), "")


def render_platform_supply_advantages(platforms: list[str] | tuple[str, ...] | None = None) -> str:
    """Render static platform supply advantages for prompts."""

    selected = tuple(platforms) if platforms is not None else tuple(PLATFORM_SUPPLY_ADVANTAGES)
    lines: list[str] = []
    for platform in selected:
        guide = platform_supply_advantage(platform)
        if guide:
            lines.append(f"  - {platform}:{guide}")
    return "\n".join(lines)


_MERGED_KEYWORDS_SYSTEM_PROMPT = (
    "<task>\n"
    "你要为多个平台的内容发现一次性生成搜索关键词。\n"
    "见 user 消息里的 <profile_summary>(用户画像,只发一次)和 <platforms>"
    "(本轮需要补词的平台数组)。<platforms> 里每个平台块给出 platform、need"
    "(要生成多少个该平台关键词)、recent_keywords(最近已经搜过、不要再出的词)、"
    "avoid_topics / avoid_styles / avoid_franchises(当前推荐池已饱和、要避开的方向)、"
    "prefer_axes(冷启动或手动传入的优先补广度方向)、cold_start(是否空池冷启动)、"
    "supply_hint(数据观察:该平台近来实际产出较多、用户没有反感的方向,是下面 "
    "<supply_advantage> 静态表的数据化补充,可能为空)。\n"
    "如果 user 消息额外包含 <explore_domains>,说明 B 站 explore refresh plan 已到期"
    "或即将到期,且 B 站仍有补货空间。此时除了常规平台关键词,还要额外输出"
    "可选 key `explore_domains`:它不是常规兴趣命中,而是专门给 B 站搜索缓存池的"
    "探索性查询方向,用于跳出信息茧房和测试新的心理诉求轴。\n"
    "</task>\n\n"
    "<supply_advantage>\n"
    "每个平台结构性擅长的内容方向不同(下面是平台的固有供给优势,与具体用户无关)。"
    "请把用户画像里的兴趣,映射到该平台真正有好内容的形态上:\n"
    f"{render_platform_supply_advantages()}\n"
    "</supply_advantage>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON 对象,不要附带解释。\n"
    "2. JSON 的 key 必须是 <platforms> 里出现的 platform 标识符"
    "(bilibili / xiaohongshu / douyin / youtube / twitter / zhihu / reddit / bangumi / linuxdo),"
    "(bilibili / xiaohongshu / douyin / youtube / twitter / zhihu / reddit / bangumi / v2ex / weibo),"
    "每个 key 的值是一个"
    "字符串数组。**只输出本轮 <platforms> 里给到的平台**,不要凭空加平台。"
    "唯一例外:只有 user 消息含 <explore_domains> 时,才可以额外输出"
    "`explore_domains` 数组。\n"
    "3. 每个平台生成恰好该平台 need 个搜索关键词;凑不满时宁缺毋滥,数组可短于 need,"
    "但不要为了凑数编造与画像无关的词。\n"
    "4. 每个关键词都要是适合在该平台搜索框直接输入的短词 / 短组合,不要写成长句。\n"
    "5. **不要重复**该平台 recent_keywords 里已有的词(换皮、加无意义尾词也算重复)。\n"
    "6. 避开该平台的 avoid_topics / avoid_styles / avoid_franchises;这些是软避让信号。"
    "avoid_styles 是封闭 style_key 观看模式,不是题材标签。不要为了避让而生成与用户画像无关的词。\n"
    "7. 把同一个兴趣映射到该平台 <supply_advantage> 里描述的强项形态上,保持该平台的"
    "原生搜索风格。若该平台块带非空 supply_hint,优先往这些已被实际验证有产出的方向上"
    "映射(它和 avoid_* 不会重叠);supply_hint 为空时只依据 <supply_advantage> 静态表。\n"
    "8. **弃权(可少出 / 不出)**:如果用户的兴趣和某个平台的 <supply_advantage> 基本不"
    "匹配(在该平台搜不到对用户有价值的内容),就为该平台返回更少、甚至空数组 []——"
    "不要硬凑、不要为了填满 need 而编造不契合该平台的词。该平台留空是允许且正确的。\n"
    "9. 同一平台内各关键词的核心主题词要两两不同,不要同一概念换皮出现多次。\n"
    "10. 冷启动保护:如果某平台块 cold_start=true,avoid_topics 不是用户讨厌的内容,"
    "而是画像里权重最高、最容易让首批关键词过度集中的主题。该平台的关键词中,"
    "avoid_topics 整组最多 2 个可以直接使用;至少一半关键词应覆盖 prefer_axes、"
    "较低权重兴趣、一级兴趣域的其它切面或适合该平台的跨域映射。仍要保留少量"
    "高权重兴趣入口,不要完全避开用户最喜欢的方向。\n"
    "11. explore_domains 规则:只有收到 <explore_domains> 才生成。每个 domain 必须"
    "明显带探索性:优先选择用户画像之外、但可能被其 deep_needs / interest_domains "
    "间接吸引的跨域方向;不要把已有高权重兴趣换皮成探索。每个 domain 输出 domain、"
    "novelty_level、queries;queries 是适合 B 站直接搜索的具体短词,每条都要含内容形式词"
    "(纪录片 / 盘点 / vlog / 科普 / 测评 / 解说 / 体验等),并尽量避开"
    "covered_topic_groups。探索 query 宁可少而新,不要补成常规关键词。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "{\n"
    '  "bilibili": ["历史 冷知识 盘点", "摄影 入门 推荐"],\n'
    '  "xiaohongshu": ["手冲咖啡 入门 教程", "通勤 穿搭 真实体验"],\n'
    '  "douyin": ["AI 绘画 整活", "城市 夜骑 热门"],\n'
    '  "youtube": ["machine learning explained", "城市规划 纪录片"],\n'
    '  "twitter": ["rust async runtime", "llm agents discussion"],\n'
    '  "zhihu": ["AI 工具 经验", "城市规划 问答"],\n'
    '  "reddit": ["local LLM agents", "open source AI tooling"],\n'
    '  "bangumi": ["赛博朋克 动画", "时间循环 独立游戏"],\n'
    '  "linuxdo": ["本地大模型 部署 踩坑", "开源自托管 工具 分享"],\n'
    '  "v2ex": ["本地运行 Agent 讨论", "家庭网络折腾 经验"],\n'
    '  "weibo": ["AI Agent 热议", "动画制作 业内回应"],\n'
    '  "explore_domains": [\n'
    '    {"domain": "城市声音采样", "novelty_level": 0.84, '
    '"queries": ["城市 声音 采样 纪录片", "街头 声音 设计 vlog"]}\n'
    "  ]\n"
    "}\n"
    "</output_schema>"
)


_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT = """
You are the keyword-inspiration axis planner for OpenBiliClaw discovery.

Return ONLY a strict JSON object with exactly this shape:
{
  "axes": [
    {
      "interest": "string",
      "axis_label": "string",
      "axis_kind": "subgenre|creator_lens|method|artifact|community_language|debate|other",
      "example_terms": ["string"],
      "evidence_refs": ["url or evidence id"],
      "time_sensitive": false
    }
  ],
  "keywords": [
    {
      "interest": "string",
      "axis_id_or_label": "existing axis_id or exact axis_label",
      "platform": "bilibili|xiaohongshu|douyin|youtube|twitter|zhihu|reddit|bangumi|linuxdo",
      "platform": "bilibili|xiaohongshu|douyin|youtube|twitter|zhihu|reddit|bangumi|v2ex|weibo",
      "core_concept": "short searchable concept",
      "decoration": "optional style marker",
      "recency_sensitivity": "low|medium|high"
    }
  ]
}

Rules:
1. Generate axes and platform-native keyword candidates in one response.
2. For each interest, keywords must span at least allocation_targets.min_axes different axes.
3. Over-generate: produce at least two keyword candidates for every interest-platform allocation
   slot when the evidence supports it.
4. If an output axis is semantically the same as an existing axis, reuse the existing axis_id or
   axis_label verbatim in keywords. Do not rename or paraphrase same-meaning existing axes.
5. Output core_concept, decoration, and recency_sensitivity as separate fields. Do not merge
   decoration words into core_concept unless they are part of the actual search concept.
6. core_concept MUST anchor on a specific entity, event, work, person, or mechanism taken from
   fresh_evidence (a proper noun, title, named controversy, or concrete mechanic). Do NOT restate
   the interest or the axis_label as the core_concept. Example: when interest is "游戏资讯与推荐",
   a core_concept like "新游推荐" or "游戏资讯" that just echoes the topic name is UNACCEPTABLE;
   anchor on something concrete instead, e.g. "士官长 登陆PS5" or "腾讯网易 新游发布". Only when a
   slot's fresh_evidence truly has no specific anchor may you fall back to a topic-level
   core_concept — treat that as the exception, and never invent proper nouns that are not in the
   evidence.
7. When the user message includes an explore_request block, this is a cross-domain exploration
   round: every core_concept MUST anchor on a specific entity/event/work/person/mechanism from a
   DIFFERENT domain than the user's usual interests — something currently uncovered but plausibly
   relevant — and MUST avoid every topic listed in explore_request.avoid_covered. Example: for a
   user already saturated on 游戏, a core_concept like 游戏新作 (still the covered domain) is
   UNACCEPTABLE; anchor on an uncovered but adjacent concrete thing such as 詹姆斯韦伯 深空图像
   (from 天文, uncovered) instead. When there is no explore_request block, ignore this rule.
8. Never put literal years such as 2025 or 2026 in core_concept. Use recency_sensitivity=high
   for time-sensitive topics instead.
9. Use platform_guides as platform style guidance, not as hard gates. Only output platforms that
   appear in allocation_targets.
10. Keep axes grounded in fresh_evidence. evidence_refs should point to the provided URL or compact
   evidence identifier when available.
11. A keyword's ``interest`` MUST exactly name one item from selected_interests. Its core_concept
   must be supported by fresh_evidence or an existing axis for that SAME interest. Never copy a
   work, person, event, controversy, or mechanism from another interest's evidence and relabel it.
   If the same-interest evidence cannot support a concrete query, omit that keyword instead.
12. Treat recent_keywords as query families, not exact strings: adding only a generic suffix such
   as 复盘 / 解析 / 分析 / 教程 / 盘点 / review / explained is still a duplicate and must be omitted.
13. Keep JSON compact and valid. No markdown, no commentary, no trailing prose.
""".strip()


def build_inspiration_axis_keyword_prompt(
    *,
    profile_digest: object,
    platform_guides: object,
    selected_interests: object,
    existing_axes: object,
    fresh_evidence: object,
    allocation_targets: object,
    explore_request: object | None = None,
) -> list[dict[str, str]]:
    """Build the merged axis-plus-keyword inspiration prompt.

    Cache-friendly per CLAUDE.md: ``system_prompt`` is the module-level
    ``_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT`` constant (100% static). All
    per-call data lives in ``user_prompt`` blocks ordered most-stable
    (profile_digest) → most-variable (allocation_targets), serialized with
    ``ensure_ascii=False, indent=2, sort_keys=True``.

    ``explore_request`` is an optional per-call block (Phase 2.3, E2): when
    provided it flags a cross-domain exploration round and carries
    ``avoid_covered``. It lives ONLY in the user message — the static explore
    rule is always present in the system prompt — so the system prefix stays
    byte-identical with or without it (prompt-cache invariant).
    """

    user_blocks = [
        "<profile_digest>",
        json.dumps(profile_digest, ensure_ascii=False, indent=2, sort_keys=True),
        "</profile_digest>",
        "<platform_guides>",
        json.dumps(platform_guides, ensure_ascii=False, indent=2, sort_keys=True),
        "</platform_guides>",
        "<selected_interests>",
        json.dumps(selected_interests, ensure_ascii=False, indent=2, sort_keys=True),
        "</selected_interests>",
        "<existing_axes>",
        json.dumps(existing_axes, ensure_ascii=False, indent=2, sort_keys=True),
        "</existing_axes>",
        "<fresh_evidence>",
        json.dumps(fresh_evidence, ensure_ascii=False, indent=2, sort_keys=True),
        "</fresh_evidence>",
        "<allocation_targets>",
        json.dumps(allocation_targets, ensure_ascii=False, indent=2, sort_keys=True),
        "</allocation_targets>",
    ]
    if explore_request is not None:
        user_blocks.extend(
            [
                "<explore_request>",
                json.dumps(explore_request, ensure_ascii=False, indent=2, sort_keys=True),
                "</explore_request>",
            ]
        )
    return [
        {"role": "system", "content": _INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_blocks)},
    ]


def _category_tag_count(category: dict[str, object]) -> int:
    raw = category.get("tag_count", 0)
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def build_category_mapping_prompt(
    *,
    categories: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Build a cache-friendly prompt for mapping categories to the fixed vocab."""
    from openbiliclaw.soul.taxonomy import CATEGORY_VOCAB

    payload = {
        "categories": sorted(
            categories,
            key=lambda c: (
                -_category_tag_count(c),
                str(c.get("category", "")),
            ),
        ),
        "vocab": list(CATEGORY_VOCAB),
    }
    user_prompt = "\n\n".join(
        [
            "<category_mapping_context>",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            "</category_mapping_context>",
        ]
    )
    return [
        {"role": "system", "content": _CATEGORY_MAPPING_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_merged_keywords_prompt(
    *,
    profile_summary: dict[str, object],
    profile_blocks: list[str] | None = None,
    platform_blocks: list[dict[str, object]],
    explore_domains_block: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    """Build the merged, multi-platform search-keyword generation prompt.

    Subsumes the five legacy per-platform keyword builders into ONE LLM call
    (Discover backpressure refactor, design spec §7.1 / §7.2): the profile is
    serialized once and every due platform rides along in a single ``<platforms>``
    block, so the provider prompt cache fires on the stable prefix.

    Args:
        profile_summary: The canonical ``build_profile_summary`` dict, sent once.
        platform_blocks: One dict per due platform, each carrying
            ``platform`` plus ``need`` / ``recent_keywords`` /
            ``avoid_topics`` / ``avoid_styles`` / ``avoid_franchises`` /
            ``prefer_axes`` / ``cold_start``. Only the platforms passed in
            appear in the prompt (and may appear in the output). The ``avoid_*``
            and ``prefer_axes`` fields come from
            ``PoolDistributionSnapshot.to_prompt_hints()``.
        explore_domains_block: Optional Bilibili explore-refresh request. When
            present, the model may append an ``explore_domains`` array whose
            queries are written into the Bilibili query cache as exploratory
            searches.

    Cache-friendly per CLAUDE.md: ``system_prompt`` is the module-level constant
    ``_MERGED_KEYWORDS_SYSTEM_PROMPT`` (100% static). All per-call data lives in
    ``user_prompt``, ordered most-stable (profile) → most-variable (this batch's
    due platforms), each serialized with ``ensure_ascii=False, indent=2,
    sort_keys=True``.
    """
    user_blocks = [
        *_profile_prompt_blocks(profile_summary, profile_blocks),
        "<platforms>",
        json.dumps(
            _normalize_platform_blocks(platform_blocks),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "</platforms>",
    ]
    if explore_domains_block is not None:
        user_blocks.extend(
            [
                "<explore_domains>",
                json.dumps(
                    _normalize_explore_domains_block(explore_domains_block),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</explore_domains>",
            ]
        )
    user_prompt = "\n\n".join(user_blocks)
    return [
        {"role": "system", "content": _MERGED_KEYWORDS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def parse_merged_keywords(
    content: str,
    platforms: list[str],
    *,
    per_platform_cap: int,
) -> dict[str, list[str]]:
    """Parse the merged keyword-generation response into per-platform lists.

    Tolerant counterpart to :func:`build_merged_keywords_prompt`. Reuses
    ``parse_llm_json_tolerant`` so truncated / fenced JSON still salvages.
    Never raises — a missing, non-list, or garbage value for any requested
    platform yields an empty list for that platform.

    Args:
        content: The raw LLM response text.
        platforms: The platforms to extract (typically the same set passed to
            the builder). The returned dict has exactly these keys.
        per_platform_cap: Maximum keywords kept per platform after dedup.

    Returns:
        ``{platform: [keyword, ...]}`` for every platform in ``platforms``,
        each list deduped (order-preserving) and capped at ``per_platform_cap``.
    """
    keywords, _present = parse_merged_keywords_with_presence(
        content, platforms, per_platform_cap=per_platform_cap
    )
    return keywords


def parse_merged_keywords_with_presence(
    content: str,
    platforms: list[str],
    *,
    per_platform_cap: int,
) -> tuple[dict[str, list[str]], set[str]]:
    """Like :func:`parse_merged_keywords` but also report decline vs omission.

    The planner (P2.2) must tell an **intentional decline** — the model
    addressed the platform and returned an explicit empty list ``[]`` (the
    user's interests don't fit that platform's supply advantage, see system
    prompt rule 8) — apart from an **omission** (the platform key is absent /
    non-list garbage, the model never answered for it). The first must NOT
    trigger the interest-name fallback (skip the platform this cycle); the
    second still falls back.

    A platform counts as "present" when the parsed payload is a dict and that
    platform's value is a JSON list (``[]`` included). A non-list value
    (``"x"`` / ``42`` / missing) is NOT present → omission → fallback. With
    ``per_platform_cap <= 0`` nothing is parsed and no platform is present.

    Returns:
        ``(keywords_by_platform, present_platforms)`` where ``keywords`` has a
        key for every requested platform (deduped, capped) and
        ``present_platforms`` is the subset whose value was an explicit list.
    """
    result: dict[str, list[str]] = {platform: [] for platform in platforms}
    present: set[str] = set()
    if per_platform_cap <= 0:
        return result, present

    payload = parse_llm_json_tolerant(content)
    if not isinstance(payload, dict):
        return result, present

    for platform in platforms:
        raw = payload.get(platform)
        if not isinstance(raw, list):
            continue
        # An explicit list (even empty) means the model addressed this platform.
        present.add(platform)
        seen: set[str] = set()
        keywords: list[str] = []
        for item in raw:
            if not isinstance(item, (str, int, float)):
                continue
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            keywords.append(text)
            if len(keywords) >= per_platform_cap:
                break
        result[platform] = keywords
    return result, present


def parse_merged_keywords_with_presence_and_explore_domains(
    content: str,
    platforms: list[str],
    *,
    per_platform_cap: int,
    max_explore_domains: int = 5,
    queries_per_domain: int = 3,
) -> tuple[dict[str, list[str]], set[str], list[dict[str, object]]]:
    """Parse platform keywords plus optional ``explore_domains``.

    This keeps the legacy platform-keyword parser contract intact while giving
    the unified planner a way to consume the optional exploratory Bilibili query
    block requested by ``build_merged_keywords_prompt(..., explore_domains_block=...)``.
    """
    keywords, present = parse_merged_keywords_with_presence(
        content,
        platforms,
        per_platform_cap=per_platform_cap,
    )
    payload = parse_llm_json_tolerant(content)
    if not isinstance(payload, dict):
        return keywords, present, []
    raw_domains = payload.get("explore_domains")
    if not isinstance(raw_domains, list):
        return keywords, present, []

    domains: list[dict[str, object]] = []
    seen_domains: set[str] = set()
    max_domains = max(0, int(max_explore_domains))
    query_cap = max(1, int(queries_per_domain))
    for raw_item in raw_domains:
        if not isinstance(raw_item, dict):
            continue
        domain = str(raw_item.get("domain", "")).strip()
        normalized_domain = "".join(domain.split()).lower()
        if not domain or normalized_domain in seen_domains:
            continue
        queries = _clean_explore_domain_queries(raw_item.get("queries"), query_cap)
        if not queries:
            continue
        seen_domains.add(normalized_domain)
        domains.append(
            {
                "domain": domain,
                "novelty_level": _clamp_explore_novelty(raw_item.get("novelty_level")),
                "queries": queries,
            }
        )
        if len(domains) >= max_domains:
            break
    return keywords, present, domains


def _clean_explore_domain_queries(value: object, cap: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    seen: set[str] = set()
    queries: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        queries.append(text)
        if len(queries) >= cap:
            break
    return queries


def _clamp_explore_novelty(value: object) -> float:
    try:
        novelty = float(cast("Any", value))
    except (TypeError, ValueError):
        novelty = 0.65
    return max(0.65, min(0.95, novelty))
