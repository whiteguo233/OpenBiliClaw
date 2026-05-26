"""Prompt builders for LLM-backed tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.soul.tone import ToneProfile


_PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "bilibili": "B 站",
    "xiaohongshu": "小红书",
}


def _platform_content_label(source_platform: str) -> str:
    """Return platform-specific content label for prompts."""
    return "B 站内容" if source_platform == "bilibili" else "内容"


def _platform_friend_label(source_platform: str) -> str:
    """Return platform-specific friend label for prompts."""
    return "老B友" if source_platform == "bilibili" else "朋友"


def _platform_display_name(source_platform: str) -> str:
    """Return a human-readable platform name ("B 站" / "小红书")."""
    return _PLATFORM_DISPLAY_NAMES.get(source_platform, "内容")


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
        for platform, _ in sorted(source_platform_mix.items(), key=lambda kv: kv[1], reverse=True)[
            :3
        ]
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
        "playfulness": "medium",
        "directness": "balanced",
    }
    return (
        _tone_context_line(source_platform_mix) + "\n"
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
    """
    friend_label = _friend_label_from_mix(source_platform_mix)
    system_prompt = "\n\n".join(
        [
            "你是 OpenBiliClaw，一个像朋友一样理解用户的 AI 伙伴。",
            (
                "请使用苏格拉底式对话风格：温和、追问动机、确认理解，"
                f"但整体更像会接话的{friend_label}，不像客服，也不要像咨询师。"
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
5. 所有文本字段（name、category、context 下的 patterns/session_type、disliked_topics）必须用中文。
6. favorite_up_users 必须从事件的 up_name 字段原样复制，一个字都不能改。先逐条扫描所有事件收集 up_name 值，再与 existing_preference.favorite_up_users 合并去重。严禁根据话题推测可能的UP主名称。如果本批事件中无 up_name 字段，保留 existing_preference 中的原有列表不变。
7. cognitive_style 描述用户的信息处理偏好（如思维方式、阅读习惯、理解路径），3~5 条，基于观看行为模式推断，不要照搬兴趣标签。
8. 每条事件都自带一个 `context` 字段（v0.3.22+ 起所有源都统一填充），它是该事件的中文自然语言摘要（如"在 B 站收藏了《讲透历史叙事》,作者:历史实验室"或"小红书点赞:手冲咖啡入门 作者:豆子老师"）。**优先把 context 作为人类可读的事件描述**来理解用户行为；同时用 metadata 里的结构化字段（up_name、bvid、folder、source_platform 等）做精确匹配 / 复制。
9. 用户的兴趣信号可能跨平台（B 站 / 小红书 / 等）；通过 metadata.source_platform 区分来源，但兴趣分析本身要把所有平台的信号一视同仁，不要因为来自小红书就降权。
10. 如果事件的 inferred_satisfaction 是 negative，或 metadata.feedback_type 是 dislike / metadata.reaction 是 thumbs_down，表示负向证据。不要把负向事件提取为 interests / favorite_up_users；只能用于 disliked_topics、风格避让或降低相关偏好置信度。
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
    source_platform_mix: dict[str, float] | None = None,
) -> list[dict[str, str]]:
    """Build a structured prompt for initial soul-profile generation."""
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
    system_prompt = "\n\n".join(
        [system_prompt, _render_tone_profile(tone_profile, source_platform_mix)]
    )
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
) -> list[dict[str, str]]:
    """Build a structured prompt for recent awareness-note generation."""
    user_prompt = "\n\n".join(
        [
            "<soul_profile>",
            json.dumps(soul_profile, ensure_ascii=False, indent=2, sort_keys=True),
            "</soul_profile>",
            "<preference_summary>",
            json.dumps(preference_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</preference_summary>",
            "<recent_events>",
            json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True),
            "</recent_events>",
        ]
    )
    return [
        {"role": "system", "content": _AWARENESS_SYSTEM_PROMPT},
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
   source_deficits 是平台/来源缺口信号，不是内容轴；不要把平台名当成 query 主题。
   不要为了避让而生成与用户画像无关的 query。
10. favorite_up_users 是用户常看的 B 站 UP 主名单，仅供背景参考。
    严禁从创作者名字推断其内容类型或专长领域，进而生成该类型的 query。
    "关注/常看某创作者" ≠ "对该创作者的内容类型感兴趣"。
    用户的内容兴趣已完整体现在 interest_domains / interests / speculative_interests 中，
    不要从 favorite_up_users 另行推导兴趣方向。
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
        for key, value in (pool_hints or {}).items()
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
3. 选出的 rid 必须横跨至少 3 个不同的一级分区大类（如知识、科技、影视、生活、游戏等），
   避免全部落在同一大类下，以保证热门内容来源的多样性。
4. 至少 1 个 rid 必须来自用户画像中未出现的兴趣领域（即用户没有直接关注但可能因热度而感兴趣的分区），
   以引入新鲜感。
5. 如果不确定，优先选择知识、科技、影视、纪录片相关分区。
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


# 100% static system prompt for single-item content evaluation.
# All variables (source_context, source_platform, profile, content)
# go in user_prompt — see ``build_content_evaluation_prompt``.
_SINGLE_CONTENT_EVALUATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你要评估一个候选内容与一个用户画像的匹配度。下面 user 消息会给出 "
    "<source_context>(发现路径)、<source_platform>(平台)、"
    "<profile_summary>(画像)、<content_summary>(候选),你按下面规则打分。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON,不要附带解释。\n"
    "2. score 范围必须在 0 到 1 之间。\n"
    "3. reason 只写一句中文,解释为什么这个人会喜欢或不喜欢这个内容。\n"
    '4. 不要只说"因为热门"或"因为看过类似的",要结合用户画像。\n'
    "5. 根据 <source_context> 调整评判宽容度:search 要求高度匹配;"
    "trending 来源的内容已经过大众验证,只要不在用户讨厌列表中且内容质量过关,基础分应 ≥ 0.6,若还能和画像产生关联则给更高分;"
    "related_chain 允许适度偏移;explore 允许主题陌生,但内容仍需具备可看性和吸引力,"
    "不能仅因为心理需求抽象匹配就给高分,过于学术、艰深、小众的内容应适当降分。\n"
    "6. topic_group 是该内容所属的粗粒度主题分类,用于推荐去重。"
    "要求:2-4 个中文词,抽象到能覆盖同类内容,"
    '例如"强化学习"而非"强化学习ppo算法源码级讲解",'
    '"城市建筑"而非"上海外滩建筑群纪录片"。'
    "同一主题的不同切面必须归为同一个 topic_group。"
    '语义相同的主题必须用同一个词——"AI" "人工智能" "机器学习" 统一写成 "人工智能",'
    '"RL" "强化学习" 统一写成 "强化学习"。\n'
    "7. style_key 从以下 11 个选项中选一个,描述该内容的呈现风格:\n"
    "   game_strategy(游戏攻略/机制解析)/ news_brief(新闻资讯/时事快评)/ "
    "practical_guide(教程/入门/实操指南)/ story_doc(纪录片/故事/人物传记)/ "
    "visual_showcase(视觉向/混剪/空镜)/ tech_analysis(技术分析/硬件评测)/ "
    "deep_dive(原理讲解/学术解析)/ "
    "fun_variety(搞笑/吐槽/整活/挑战)/ lifestyle(日常/vlog/生活分享)/ "
    "review_roundup(盘点/测评/推荐/合集)/ "
    "light_chat(闲聊/杂谈/其他)\n"
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
    "</rules>\n\n"
    "<output_schema>\n"
    "{\n"
    '  "score": 0.78,\n'
    '  "reason": "这个视频的选题角度新颖,节奏轻快,契合你对该领域的好奇心。",\n'
    '  "topic_group": "生活方式",\n'
    '  "style_key": "light_chat",\n'
    '  "franchise_key": ""\n'
    "}\n"
    "</output_schema>"
)


def build_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    source_context: str = "",
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a structured prompt for single-item content relevance evaluation.

    Args:
        profile_summary: User profile summary.
        content_summary: Content metadata.
        source_context: Discovery context hint (e.g. search / trending / explore).
        source_platform: Platform identifier for dynamic prompt wording.

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
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2, sort_keys=True),
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
_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT = (
    "<task>\n"
    "你要批量评估多个候选内容与一个用户画像的匹配度。"
    "下面 user 消息会给出 <profile_summary>(画像)、<source_platform>(平台)、"
    "<source_context>(发现路径)、<content_batch>(本批候选),你按下面规则打分。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON 数组,不要附带解释。\n"
    "2. 数组长度必须与输入内容数量一致,顺序一一对应。\n"
    "3. 每项必须原样带回输入里的 bvid 或 content_id,并包含 score(0-1)、"
    "reason(一句中文)、topic_group(2-4词粗分类)、style_key(11选1)、"
    "franchise_key(可空)。\n"
    "4. 根据 <source_context> 调整评判宽容度:search 要求高度匹配;"
    "trending 基础分 >= 0.6;related_chain 允许适度偏移;"
    "explore 允许主题陌生,但内容仍需具备可看性,过于学术艰深的应适当降分。\n"
    "5. topic_group 规则:2-4 个中文词的粗分类,同主题不同切面统一。"
    "语义相同必须用同一词(AI/人工智能/机器学习 统一为 人工智能)。\n"
    "6. style_key 从 11 个选项中选:game_strategy / news_brief / "
    "practical_guide / story_doc / visual_showcase / tech_analysis / "
    "deep_dive / fun_variety / lifestyle / review_roundup / light_chat\n"
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
    "说明用户也需要轻内容做心理调节、喘气。这时 fun_variety / light_chat / "
    "lifestyle / story_doc / visual_showcase 风格的内容只要本身可看(话题清晰、"
    'UP 主观察角度有意思),不要因为"不够深"就一律压到 0.5 以下,'
    "应当给到 0.6-0.75,与画像中的娱乐/二次元/生活类兴趣标签保持权重一致。\n"
    "9. 不同 source_platform(bilibili / xiaohongshu / 其他)的内容标签同 schema,"
    "不要因为来源不同特殊处理评分逻辑。\n"
    "10. 当 user 消息携带 `<negative_examples>` 时,把这些标题视为用户最近"
    "**明确不喜欢**的样本——理由可能是快速划走 (`quick_exit`) 或显式负反馈"
    " (`explicit_negative`)。\n"
    "11. 对每个候选项,先与 `<negative_examples>` 中的标题做**结构 / 话术 / "
    "商业意图**层面的比较;若高度相似(同款震惊体、同款保姆级全攻略、同款月入过万"
    "钓贴),`integration_fit` 与 `interest_overlap` 必须显著降低,不要被表面话题词"
    "吸引而错给高分。比较的是**话术模式**,不是关键词重叠。\n"
    "12. profile_summary.disliked_topics 是长期避雷项;候选命中这些主题或话术模式时,"
    "score 必须下调,不要把它们当成 interests 的反向补充来加分。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "[\n"
    '  {"bvid": "BV1xxx", "score": 0.78, "reason": "...", "topic_group": "认知科学", '
    '"style_key": "deep_dive", "franchise_key": ""},\n'
    '  {"bvid": "BV2xxx", "score": 0.72, "reason": "...", "topic_group": "游戏摄影", '
    '"style_key": "visual_showcase", "franchise_key": "原神"},\n'
    '  {"bvid": "BV3xxx", "score": 0.45, "reason": "...", "topic_group": "美食", '
    '"style_key": "light_chat", "franchise_key": ""}\n'
    "]\n"
    "</output_schema>"
)


def build_batch_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_items: list[dict[str, object]],
    source_context: str = "",
    source_platform: str = "bilibili",
    negative_examples: list[dict[str, object]] | None = None,
) -> list[dict[str, str]]:
    """Build a prompt that evaluates multiple content items in one LLM call.

    Same rules as single evaluation, but processes a batch and returns
    a JSON array of results keyed by item index.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT`` — 100% static
    across all calls, so the entire ~3500-token instruction block is
    cache-eligible. All variables (profile, source_platform,
    source_context, content_items) live in ``user_prompt``, ordered from most
    stable (profile, changes once per profile rebuild) to most variable
    (content_batch, changes every call). DeepSeek's auto-cache hits the
    system prefix every call after the first; explicit-cache providers
    can mark the system block with cache_control.

    v0.3.x: optional ``negative_examples`` block sits between
    ``<source_context>`` and ``<content_batch>``, carrying recent
    quick-exit / explicit-negative titles for the model to pattern-match
    against. When ``None`` or empty the block is omitted entirely so the
    user-message bytes are identical to the no-examples path (cache
    prefix unchanged for cold-start users). System prompt picks up two
    permanent rules about how to consume the block (rules 10 + 11) and
    stays call-invariant after that one-time template change.
    """
    user_blocks: list[str] = [
        "<profile_summary>",
        json.dumps(
            profile_summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        "</profile_summary>",
        "<source_platform>",
        source_platform or "bilibili",
        "</source_platform>",
        "<source_context>",
        source_context or "(unspecified)",
        "</source_context>",
    ]
    if negative_examples:
        user_blocks.extend(
            [
                "<negative_examples>",
                json.dumps(
                    negative_examples,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                "</negative_examples>",
            ]
        )
    user_blocks.extend(
        [
            "<content_batch>",
            json.dumps(
                content_items,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "</content_batch>",
        ]
    )
    user_prompt = "\n\n".join(user_blocks)
    return [
        {"role": "system", "content": _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT},
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
4. topic_label 需要是轻度个性化的主题标签,不要只写泛分类词。
5. 避免机械解释腔、广告腔和"根据你的兴趣""你可能会喜欢"这类算法套话。
6. 禁止使用以下模板词:信息密度、高质量、深度好文、值得一看、强烈推荐、不容错过。
   用具体描述代替泛泛评价。
7. 如果内容来自 explore (跨域发现),expression 要解释这个陌生领域和用户的哪种
   认知偏好/深层需求产生了关联,让用户觉得"虽然没想过但确实想看"。
8. 如果 profile_summary.style 里 depth_preference 不高、preferred_duration 偏短,
   或 humor_preference 偏高,expression 要更轻、更顺口,少用"认知偏好 / 底层结构 /
   深层需求"这类抽象词,不要把推荐说得比内容本身还硬。
9. 如果 content_summary.style_key 是 lifestyle / light_chat / fun_variety /
   review_roundup / story_doc / visual_showcase,优先从人物、场景、信息点或情绪切口来推荐,
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
    content_summary: dict[str, object],
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT`` (100% static).
    Platform label / tone profile / profile / content all live in
    ``user_prompt``, ordered so that platform + tone (semi-stable per
    user) come before content (changes every call) — extends the
    prefix-cache match as far as the recommendation cycle reuses the
    same persona.
    """
    user_prompt = "\n\n".join(
        [
            "<source_platform>",
            source_platform or "bilibili",
            "</source_platform>",
            "<tone_profile>",
            _render_tone_profile(tone_profile, {source_platform: 1.0}),
            "</tone_profile>",
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_summary>",
        ]
    )
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
    "不要说空话。\n"
    "4. 避免:算法套话、信息密度、高质量、深度好文、值得一看、强烈推荐。\n"
    "5. explore 来源的内容要解释陌生领域和用户认知偏好的关联。\n"
    "6. 每条 expression 的开头措辞必须不同,禁止重复同一句式。\n"
    "7. 如果 profile_summary.style 显示 depth_preference 不高、preferred_duration 偏短,"
    "或 humor_preference 偏高,整体措辞要更轻、更顺口,不要把轻内容硬写成分析报告。\n"
    "8. 如果某条 content.style_key 是 lifestyle / light_chat / fun_variety / "
    "review_roundup / story_doc / visual_showcase,就优先从人物、场景、信息点或情绪切口下笔,"
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
    content_items: list[dict[str, object]],
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a prompt that generates expressions for multiple items in one call.

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_BATCH_EXPRESSION_SYSTEM_PROMPT`` (100% static).
    """
    user_prompt = "\n\n".join(
        [
            "<source_platform>",
            source_platform or "bilibili",
            "</source_platform>",
            "<tone_profile>",
            _render_tone_profile(tone_profile, {source_platform: 1.0}),
            "</tone_profile>",
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_items, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_batch>",
        ]
    )
    return [
        {"role": "system", "content": _BATCH_EXPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# 100% static system prompt for delight-reason generation.
_DELIGHT_REASON_SYSTEM_PROMPT = (
    "<task>\n"
    "你要为一条「主动惊喜推荐」写一段解释,说明为什么这条内容可能会让这个人意外地喜欢。\n"
    "这不是普通推荐——这是你作为一个真正懂他的朋友,主动跑来说「这条你一定要看」。\n"
    "下面 user 消息会给出 <source_platform>、<tone_profile>、<profile_summary>、"
    "<content_summary>、<reason_stub>。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出必须是严格 JSON,包含 delight_reason 和 delight_hook。\n"
    "2. delight_reason(80-200字中文口语)要解释:\n"
    "   - 这条内容为什么会让这个人产生「意外的共鸣」或「惊喜的发现」\n"
    "   - 必须引用用户画像中的至少一个深层需求、洞察假说或认知偏好\n"
    "   - 语气比普通推荐更亲密、更有把握,像「我知道你不常看这类,但这条真的会戳到你」\n"
    "3. delight_hook(2-4个中文字)是一个短标签,用于UI徽章展示。\n"
    "   例如:深层共鸣、跨域惊喜、灵感碰撞、意外契合、隐藏需求\n"
    "4. 不要用:强烈推荐、值得一看、高质量、信息密度等套话。\n"
    "5. reason_stub 提供了打分信号的线索,用它来组织 delight_reason 的叙事方向。\n"
    "6. 严格遵循 <tone_profile> 里给的密度 / 温度 / 梗感 / 直给度 4 个参数。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "{\n"
    '  "delight_reason": "你之前聊到过想搞明白...",\n'
    '  "delight_hook": "深层共鸣"\n'
    "}\n"
    "</output_schema>"
)


_DELIGHT_BATCH_SCORE_SYSTEM_PROMPT = (
    "<task>\n"
    "你要为一批候选内容评估「惊喜推荐分」。这是用户在常规推荐流之外、特别值得"
    "主动 surface 的「意外契合」内容。\n"
    "下面 user 消息会给出 <profile_summary>(画像)、<content_batch>(候选列表)。\n"
    "</task>\n\n"
    "<rules>\n"
    "1. 输出严格 JSON 数组,顺序与输入 <content_batch> 一一对应,长度相同。\n"
    '2. 每项: {"bvid": "...", "score": 0.0-1.0, "rationale": "...", "hook": "..."}\n'
    "3. 「惊喜」≠「相似度高」。判分核心:\n"
    "   - 内容跟用户已有兴趣有概念上的连接,但不是直接重复(避免「又一条 X 测评」)。\n"
    "   - 内容能呼应 deep_needs / active_insights — 用户没明说但实际渴望的方向。\n"
    "   - 内容质量本身要好(标题/描述能透出做工)。\n"
    "   - 优先 explore / related_chain 来源:search 天然 fitting,惊喜需要离开舒适区。\n"
    "4. score 标尺:\n"
    "   - 0.85+: 极少数真正「哇这个意外好对胃口」的 item。\n"
    "   - 0.70-0.85: 跨域呼应,用户大概率会感兴趣但自己不会主动找。\n"
    "   - 0.55-0.70: 有惊喜潜力但相对常规。\n"
    "   - 0.40-0.55: 跟用户兴趣有些关联,但太普通。\n"
    "   - <0.40: 跟用户兴趣无关或纯重复已有关注。\n"
    "5. rationale (80-180 字中文口语) 用第二人称「你」,直接当 delight_reason 用:\n"
    "   - 解释「为什么这条对你是惊喜」,引用画像中至少一个 deep_need / insight / 兴趣特征。\n"
    "   - 语气像懂你的朋友主动说「这条你一定要看」,有把握、有连接、不空泛。\n"
    "   - 不用套话: 强烈推荐 / 值得一看 / 高质量 / 信息密度。\n"
    "6. hook (2-4 个中文字) 短标签,如: 深层共鸣 / 跨域惊喜 / 灵感碰撞 / 意外契合 / 隐藏需求。\n"
    "7. 不要省略任何 item。即使是低分(<0.40)的也要返回完整结构,score + rationale 写明为什么不算惊喜。\n"
    "</rules>\n\n"
    "<output_schema>\n"
    "[\n"
    "  {\n"
    '    "bvid": "BV1xxx",\n'
    '    "score": 0.78,\n'
    '    "rationale": "你之前 likes 里有 X 和 Y，这条把它俩用 Z 视角串起来了，正好戳你「想要更深一层」的那个点...",\n'
    '    "hook": "跨域惊喜"\n'
    "  }\n"
    "]\n"
    "</output_schema>"
)


def build_delight_score_batch_prompt(
    *,
    profile_summary: dict[str, object],
    content_batch: list[dict[str, object]],
) -> list[dict[str, str]]:
    """Build a prompt for batch-scoring delight candidates via LLM.

    Replaces the embedding-cosine pipeline (likes_alignment / deep_need /
    insight / dislike) which biased toward "similar" rather than
    "surprising". A single batched call returns score + rationale + hook
    per candidate, eliminating the secondary delight_reason call.

    System prompt is fully static (cache-friendly per CLAUDE.md
    convention). User payload contains the per-call profile summary and
    the candidate batch, both serialized with sort_keys for deterministic
    cache prefixes.
    """
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_batch, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_batch>",
        ]
    )
    return [
        {"role": "system", "content": _DELIGHT_BATCH_SCORE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def build_delight_reason_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    reason_stub: str,
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a prompt for generating a delight reason explanation.

    The output should feel like a friend saying "I know you don't usually
    watch this kind of thing, but I genuinely think this one would hit
    different for you because..."

    v0.3.28+ cache-friendly: ``system_prompt`` is the module-level
    constant ``_DELIGHT_REASON_SYSTEM_PROMPT`` (100% static).
    """
    user_prompt = "\n\n".join(
        [
            "<source_platform>",
            source_platform or "bilibili",
            "</source_platform>",
            "<tone_profile>",
            _render_tone_profile(tone_profile, {source_platform: 1.0}),
            "</tone_profile>",
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2, sort_keys=True),
            "</content_summary>",
            "<reason_stub>",
            reason_stub,
            "</reason_stub>",
        ]
    )
    return [
        {"role": "system", "content": _DELIGHT_REASON_SYSTEM_PROMPT},
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
6. why_it_might_resonate 必须先说明它对应用户的哪种认知需求、
   信息处理偏好或内在驱动力，再解释这种陌生内容为什么仍然可能打动这个人。
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
   定义清单（每个 domain 在 why_it_might_resonate 里**显式写出对应哪个轴**）：
     - 拆解·系统·结构  ：精密机械、数学、算法、博弈、底层原理、工艺拆解
     - 感官·沉浸·审美    ：视觉/听觉/材质/光影/空间体验、ASMR、风景、艺术
     - 情绪·叙事·人物    ：纪录片人物、剧情、日常 vlog、生活故事、情感讨论
     - 文化·社会·议题    ：社会观察、亚文化、地域文化、历史人文
     - 实操·生活·烟火    ：美食、生活技能、家居、旅行、宠物、亲子
     - 运动·身体·动手    ：体育、健身、户外、动手实验
     - 幽默·吐槽·消遣    ：搞笑、鬼畜、整活、轻松吐槽
   例：5 个 domain 不许全在"拆解·系统·结构"轴里换皮（钟表/榫卯/开发板/电路/模型
   都属于同一个轴——拆解结构——这种安排是错的）；必须把 5 个槽位分散到至少 4 个不同的轴。
12. 重要：personality_portrait 里出现的具体名词（如"机械结构""手工技艺""琢磨某物"
   "钻研某活"等）只是写作时的文风装饰，**不是真实的兴趣信号**。
   你判断用户兴趣方向时**只能依赖 `interests` 字段中的明确标签**，
   绝对不要把 portrait 里的比喻或例子当成探索目标。
   如果 portrait 提到"机械结构"，你不应该把"机械"或"精密拆解"当成 domain；
   而应该看 interests 实际有什么、并在心理诉求轴清单里挑一个**还没被占用**的轴去拓展。
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
      "category": "审美体验",
      "why_it_might_resonate": "你偏好结构清晰、能从具体对象看见更大系统的内容。",
      "novelty_level": 0.72,
      "queries": ["上海 里弄 改造 纪录片", "创意 建筑 盘点", "废墟 探险 vlog"]
    }
  ]
}
category 必须从以下选项中选取且每个 domain 的 category 必须不同：
知识解释 / 现实观察 / 审美体验 / 人物叙事 / 技术机制 / 社会文化 / 自然科学 / 生活方式
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
        "  ≈30%  portrait + deep_needs + motivational_drivers（内在动力）\n"
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
    profile_summary: dict[str, object],
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
        quota_lines = [
            f"  - {mode}: {n} 条" for mode, n in source_mode_quota.items() if n > 0
        ]
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
