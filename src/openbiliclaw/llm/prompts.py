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
        for platform, _ in sorted(
            source_platform_mix.items(), key=lambda kv: kv[1], reverse=True
        )[:3]
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
    """Build chat messages for Socratic dialogue generation."""
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
你要基于用户历史摘要和偏好摘要，生成一份谨慎、温和、像长期观察后的老朋友所写的人格画像。
</task>

<rules>
1. 只能根据给定材料推断，不要做医学化、病理化、断言式结论。
2. 输出必须是严格 JSON，不要附带解释。
3. 人格描述至少 200 个中文字符。
4. core_traits 控制在 3 到 6 条，deep_needs 和 values 保持简洁。
   deep_needs 必须用具体、可感知的语言描述用户的底层渴望（如"对事物运作原理的深层理解""不受干扰的个人空间与自由"），
   不要写成抽象心理学术语（"掌控感""自我实现"太笼统），也不要写成认知偏好（"逻辑闭环"属于 cognitive_style）。
   core_traits 和 values 数量应与证据匹配（如证据支持 4 条 values 就写 4 条，不要人为缩减）。
5. 先总结这个人怎么处理信息，再总结他在内容里长期在找什么，最后总结他最近更像处于什么阶段。
6. personality_portrait 硬约束（违反即视为失败，必须严格遵守）：

   【禁止清单】— portrait 正文中绝对不得出现以下任一要素：
     - 具体视频题材名、节目类型描述（如"4K修复老番""硬核时政""纪录片""番剧""追番"）
     - UP 主 ID、频道名、主播名
     - 具体作品名、IP 名、游戏名
     - 画质/格式描述（4K / 8K / HDR / 修复版 / 蓝光 等）
     - 具体菜名、食物名、地名、品牌名、产品名（哪怕作为"举例"也禁止）
     - "看了 X""追了 X""浏览 X""沉浸在 X""驻足于 X" 这类直白的观看行为复述
     - 以 recent_titles 中任何视频题目为灵感复述出的场景描写
   兴趣 topic、题材、作品名只能留在你内部推理的思考链里，不得出现在 portrait 最终字面。

   【必含心理维度】— portrait 必须按以下 4 个心理学视角组织，每个视角至少 1 句洞察：
     (a) 信息处理与认知防御机制：用户如何过滤信息、对哪类刺激启动防御、防御的根源是什么
     (b) 核心张力与内在矛盾：写出 2-3 组对立驱动
         （例如：理性控制 vs 情感软落；秩序渴望 vs 好奇扩散；掌控欲 vs 漂流感）
     (c) 情感调节与自我建构策略：用户靠什么获得确定感、如何处理不确定性、
         在焦虑时会退行到什么心理模式
     (d) 当前人格漂移方向：最近人格在往哪个方向迁移、背后的心理动因是什么

   【语气】— 仍然保持老朋友口吻，口语化有温度，但每一句都必须指向心理机制本身，
   而不是"用户看了什么" 的具体事实。

   【反面示例 — 绝对不要写成这样】：
   "你喜欢在4K修复的老番里回味纯粹的审美，会在研究大模型之余为一个正宗鸡煲配方驻足……"
   ← 这段违反禁止清单（出现"4K修复老番""大模型""鸡煲"），必须重写。

   【正面示例 — 应该写成这种风格】：
   "你对'被糊弄'有近乎生理性的排斥——任何带着引流痕迹的信息都会触发你的防御屏障，
    你宁可花三倍的时间自己拆解底层逻辑，也不愿接受被喂进来的结论。这种控制欲的深处，
    其实藏着一种对不确定性的隐性焦虑：当外部叙事越来越模糊，你就越依赖可验证的
    逻辑闭环来给自己一个站得住脚的位置。但你并不只是躲在理性堡垒里——你最近在
    偷偷给自己松绑，开始允许一些'无法完全拆解'的具体生活经验进入视野，
    这是一种从封闭的秩序感向更有弹性的生活实感迁移的尝试，背后是你对'智识洁癖'
    可能把自己活成孤岛的一点点警觉。"
7. 可以参考非临床的认知风格、内在驱动力、阶段状态来组织描述，但不要写理论术语，
   不要写成心理报告、咨询记录或说明书，要像熟人总结这个人的气质和状态。
8. mbti 字段必须填写：根据行为数据推断最可能的 MBTI 四字母类型（如 INTJ、ENFP），
   confidence 取 0.5-0.9，四个维度 EI/SN/TF/JP 都要填。如果证据不足可以降低 confidence，
   但不要留空。
9. cognitive_style：如果 preference_summary 中已有 cognitive_style，直接沿用并微调措辞，
   不要推翻或重新推断。如果没有，再从行为模式推断。
10. life_stage 应从行为证据抽象出用户所处的人生阶段全貌：推断人口学特征（学历、职业阶段、年龄段），
    并刻画该阶段的核心心理状态和发展方向（如"工作2-3年的互联网从业者，正处于技能深化与职业方向确认的关键期"）。
    不要堆砌具体事件（如"喜欢粤语文化、学做鸡煲"），而要提炼这些行为背后反映的阶段性特征。
    current_phase 应聚焦用户当前面临的核心张力或心理主题（如"职业焦虑与创作冲动并存"），
    概括当前的心理动力方向，而不是罗列最近看了什么具体内容。
    具体事件只能作为推理依据，不能成为描述本身。
11. 警惕内向/分析型偏见：不要默认将用户描绘为"内省、理性、追求掌控感"的人格。
    如果用户频繁观看搞笑、娱乐、社交互动、派对游戏、生活分享、追番类内容，
    core_traits 应体现外向、社交驱动、刺激寻求、兴趣易转移等特征；
    motivational_drivers 应反映分享表达、对抗无聊、群体归属等驱动力；
    deep_needs 应包含新鲜刺激渴求、被群体接纳等需求。
    根据实际行为证据判断，而不是套用"深度思考者"模板。
12. 警惕纯理性偏见：即使用户确实偏好知识类/深度内容，也不要只输出智识维度的特质。
    观察用户是否表现出以下感性信号：关注人文/情感/艺术/理想主义类内容、
    对创作者的情感表达有持续互动、追番或追剧中表现出高共情投入、
    关注社会议题或弱势群体话题、对"完美"或"极致"有反复追求。
    如果存在这些信号，core_traits 必须包含感性维度（如深度共情、理想主义、
    完美主义倾向、审美敏感等），不要全部用"好奇""批判""分析"等冷色调词汇覆盖。
    values 也要相应体现人文关怀、质量信仰等非功利价值观。
</rules>

<output_schema>
{
  "personality_portrait": "至少 200 字的自然语言人格描述",
  "core_traits": ["理性", "好奇", "谨慎"],
  "cognitive_style": ["具象思维优先", "边做边想的迭代模式", "问题导向型学习"],
  "motivational_drivers": ["掌握可迁移的实用技能", "持续扩展能力边界"],
  "current_phase": "最近更像在一边动手实践，一边积累经验和判断力。",
  "values": ["实用主义", "工匠精神", "个人自由"],
  "life_stage": "处于探索与积累阶段",
  "deep_needs": ["被理解", "持续成长"],
  "mbti": {
    "type": "INTP",
    "confidence": 0.7,
    "dimensions": {
      "EI": {"pole": "I", "strength": 0.8},
      "SN": {"pole": "N", "strength": 0.75},
      "TF": {"pole": "T", "strength": 0.7},
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
    user_prompt = "\n\n".join([
        "<current_state>",
        json.dumps({
            "life_stage": current_life_stage,
            "current_phase": current_phase,
        }, ensure_ascii=False, indent=2),
        "</current_state>",
        "<recent_evidence>",
        json.dumps(evidence[:20], ensure_ascii=False, indent=2),
        "</recent_evidence>",
    ])
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
    user_prompt = "\n\n".join([
        "<current_state>",
        json.dumps({
            "values": current_values,
            "motivational_drivers": current_drivers,
        }, ensure_ascii=False, indent=2),
        "</current_state>",
        "<recent_evidence>",
        json.dumps(evidence[:20], ensure_ascii=False, indent=2),
        "</recent_evidence>",
    ])
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
    user_prompt = "\n\n".join([
        "<current_state>",
        json.dumps({
            "core_traits": current_traits,
            "deep_needs": current_needs,
            "mbti": current_mbti,
        }, ensure_ascii=False, indent=2),
        "</current_state>",
        "<recent_evidence>",
        json.dumps(evidence[:20], ensure_ascii=False, indent=2),
        "</recent_evidence>",
    ])
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
7. query 的内容风格必须多样化，不要全部偏向"深度/学术/原理"。
   应该混合使用不同风格词，如 盘点/推荐/日常/吐槽/测评/入门/体验/挑战/合集 等，
   整组 query 中带"深度/原理/解析/机制"等学术向关键词的不得超过 2 个。
8. 如果 profile_summary.style 里 depth_preference 偏低、preferred_duration 偏短，
   或 humor_preference 偏高，就进一步减少"原理/解析/机制"这类硬入口，
   优先使用更轻、更好点开的形式词；不要把"理解力强"误翻译成"必须更学术"。
6. 所有 query 的核心主题词（第一个实词）必须两两不同，
   禁止同一概念换皮出现多次。
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


def build_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_summary: dict[str, object],
    source_context: str = "",
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a structured prompt for content relevance evaluation.

    Args:
        profile_summary: User profile summary.
        content_summary: Content metadata.
        source_context: Discovery context hint (e.g. search / trending / explore).
        source_platform: Platform identifier for dynamic prompt wording.
    """
    source_hint = ""
    if source_context:
        source_hint = (
            "\n<discovery_context>\n"
            f"{source_context}\n"
            "</discovery_context>\n\n"
        )

    system_prompt = (
        "<task>\n"
        + source_hint
        + "你要评估一个 " + _platform_content_label(source_platform) + "与这个用户画像的匹配度。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON，不要附带解释。\n"
        "2. score 范围必须在 0 到 1 之间。\n"
        "3. reason 只写一句中文，解释为什么这个人会喜欢或不喜欢这个内容。\n"
        "4. 不要只说\"因为热门\"或\"因为看过类似的\"，要结合用户画像。\n"
        "5. 根据发现路径调整评判宽容度：search 要求高度匹配；"
        "trending 来源的内容已经过大众验证，只要不在用户讨厌列表中且内容质量过关，基础分应 ≥ 0.6，若还能和画像产生关联则给更高分；"
        "related_chain 允许适度偏移；explore 允许主题陌生，但内容仍需具备可看性和吸引力，"
        "不能仅因为心理需求抽象匹配就给高分，过于学术、艰深、小众的内容应适当降分。\n"
        "6. topic_group 是该内容所属的粗粒度主题分类，用于推荐去重。"
        "要求：2-4 个中文词，抽象到能覆盖同类内容，"
        "例如\"强化学习\"而非\"强化学习ppo算法源码级讲解\"，"
        "\"城市建筑\"而非\"上海外滩建筑群纪录片\"。"
        "同一主题的不同切面必须归为同一个 topic_group。"
        "语义相同的主题必须用同一个词——\"AI\" \"人工智能\" \"机器学习\" 统一写成 \"人工智能\"，"
        "\"RL\" \"强化学习\" 统一写成 \"强化学习\"。\n"
        "7. style_key 从以下 11 个选项中选一个，描述该内容的呈现风格：\n"
        "   game_strategy（游戏攻略/机制解析）/ news_brief（新闻资讯/时事快评）/ "
        "practical_guide（教程/入门/实操指南）/ story_doc（纪录片/故事/人物传记）/ "
        "visual_showcase（视觉向/混剪/空镜）/ tech_analysis（技术分析/硬件评测）/ "
        "deep_dive（原理讲解/学术解析）/ "
        "fun_variety（搞笑/吐槽/整活/挑战）/ lifestyle（日常/vlog/生活分享）/ "
        "review_roundup（盘点/测评/推荐/合集）/ "
        "light_chat（闲聊/杂谈/其他）\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "score": 0.78,\n'
        '  "reason": "这个视频的选题角度新颖，节奏轻快，契合你对该领域的好奇心。",\n'
        '  "topic_group": "生活方式",\n'
        '  "style_key": "light_chat"\n'
        "}\n"
        "</output_schema>"
    )
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


def build_batch_content_evaluation_prompt(
    *,
    profile_summary: dict[str, object],
    content_items: list[dict[str, object]],
    source_context: str = "",
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a prompt that evaluates multiple content items in one LLM call.

    Same rules as single evaluation, but processes a batch and returns
    a JSON array of results keyed by item index.
    """
    source_hint = ""
    if source_context:
        source_hint = (
            "\n<discovery_context>\n"
            f"{source_context}\n"
            "</discovery_context>\n\n"
        )

    system_prompt = (
        "<task>\n"
        + source_hint
        + "你要批量评估多个 " + _platform_content_label(source_platform) + "与这个用户画像的匹配度。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON 数组，不要附带解释。\n"
        "2. 数组长度必须与输入内容数量一致，顺序一一对应。\n"
        "3. 每项包含 score(0-1)、reason(一句中文)、topic_group(2-4词粗分类)、"
        "style_key(11选1)。\n"
        "4. 根据发现路径调整评判宽容度：search 要求高度匹配；"
        "trending 基础分 >= 0.6；related_chain 允许适度偏移；"
        "explore 允许主题陌生，但内容仍需具备可看性，过于学术艰深的应适当降分。\n"
        "5. topic_group 规则：2-4 个中文词的粗分类，同主题不同切面统一。"
        "语义相同必须用同一词（AI/人工智能/机器学习 统一为 人工智能）。\n"
        "6. style_key 从 11 个选项中选：game_strategy / news_brief / "
        "practical_guide / story_doc / visual_showcase / tech_analysis / "
        "deep_dive / fun_variety / lifestyle / review_roundup / light_chat\n"
        "7. 如果 profile_summary.style 显示 depth_preference 不高、preferred_duration 偏短，"
        "或 humor_preference 偏高，不要把学术艰深、入口很高的内容误判成高匹配；"
        "讲法轻松但不空的内容同样可以高分。\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "[\n"
        '  {"score": 0.78, "reason": "...", "topic_group": "认知科学", '
        '"style_key": "deep_dive"},\n'
        '  {"score": 0.45, "reason": "...", "topic_group": "美食", '
        '"style_key": "light_chat"}\n'
        "]\n"
        "</output_schema>"
    )
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_items, ensure_ascii=False, indent=2),
            "</content_batch>",
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
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression."""
    _friend = _platform_friend_label(source_platform)
    _content = _platform_content_label(source_platform)
    system_prompt = """
<task>
你要像一个真正懂这个人的{friend}一样，给出一段推荐这条 {content}的话。
</task>""".replace("{friend}", _friend).replace("{content}", _content) + """

<rules>
1. 输出必须是严格 JSON，不要附带解释。
2. expression 必须是 50 到 150 字的中文口语表达，像朋友私聊，不像算法推荐。
3. expression 要解释”为什么这条内容会对上这个人的胃口”，必须引用至少一个具体内容细节
   （如视频标题中的关键词、UP主特点、或内容的独特切入角度），不要说空话。
4. topic_label 需要是轻度个性化的主题标签，不要只写泛分类词。
5. 避免机械解释腔、广告腔和”根据你的兴趣””你可能会喜欢”这类算法套话。
6. 禁止使用以下模板词：信息密度、高质量、深度好文、值得一看、强烈推荐、不容错过。
   用具体描述代替泛泛评价。
7. 如果内容来自 explore（跨域发现），expression 要解释这个陌生领域和用户的哪种
   认知偏好/深层需求产生了关联，让用户觉得”虽然没想过但确实想看”。
8. 如果 profile_summary.style 里 depth_preference 不高、preferred_duration 偏短，
   或 humor_preference 偏高，expression 要更轻、更顺口，少用“认知偏好 / 底层结构 /
   深层需求”这类抽象词，不要把推荐说得比内容本身还硬。
9. 如果 content_summary.style_key 是 lifestyle / light_chat / fun_variety /
   review_roundup / story_doc / visual_showcase，优先从人物、场景、信息点或情绪切口来推荐，
   不要硬写成“系统闭环 / 底层逻辑 / 认知防御”。
</rules>

<output_schema>
{
  "expression": "这个 UP 主拿液压机去压各种日用品，看着无厘头，"
    "但你仔细看他每次都会慢放形变过程——其实暗合材料力学那套东西，"
    "你搞机械的应该会觉得有点意思。",
  "topic_label": "藏在整活视频里的材料力学"
}
</output_schema>
""".strip()
    system_prompt = "\n\n".join(
        [system_prompt, _render_tone_profile(tone_profile, {source_platform: 1.0})]
    )
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


def build_batch_expression_prompt(
    *,
    profile_summary: dict[str, object],
    content_items: list[dict[str, object]],
    tone_profile: ToneProfile | None,
    source_platform: str = "bilibili",
) -> list[dict[str, str]]:
    """Build a prompt that generates expressions for multiple items in one call."""
    _friend = _platform_friend_label(source_platform)
    _content = _platform_content_label(source_platform)
    system_prompt = (
        "<task>\n"
        "你要像一个真正懂这个人的" + _friend + "一样，为多条 " + _content + "各写一段推荐话。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON 数组，数组长度与输入内容数量一致，顺序一一对应。\n"
        "2. 每项包含 expression(50-150字中文口语) 和 topic_label(个性化主题标签)。\n"
        "3. expression 像朋友私聊，必须引用至少一个具体内容细节"
        "（标题关键词、UP主特点、独特切入角度），不要说空话。\n"
        "4. 避免：算法套话、信息密度、高质量、深度好文、值得一看、强烈推荐。\n"
        "5. explore 来源的内容要解释陌生领域和用户认知偏好的关联。\n"
        "6. 每条 expression 的开头措辞必须不同，禁止重复同一句式。\n"
        "7. 如果 profile_summary.style 显示 depth_preference 不高、preferred_duration 偏短，"
        "或 humor_preference 偏高，整体措辞要更轻、更顺口，不要把轻内容硬写成分析报告。\n"
        "8. 如果某条 content.style_key 是 lifestyle / light_chat / fun_variety / "
        "review_roundup / story_doc / visual_showcase，就优先从人物、场景、信息点或情绪切口下笔，"
        "不要把它写成心理机制拆解。\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "[\n"
        '  {"expression": "这条...", "topic_label": "xxx"},\n'
        '  {"expression": "这个UP主...", "topic_label": "yyy"}\n'
        "]\n"
        "</output_schema>"
    )
    system_prompt = "\n\n".join(
        [system_prompt, _render_tone_profile(tone_profile, {source_platform: 1.0})]
    )
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_batch>",
            json.dumps(content_items, ensure_ascii=False, indent=2),
            "</content_batch>",
        ]
    )
    return [
        {"role": "system", "content": system_prompt},
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
    """
    system_prompt = (
        "<task>\n"
        "你要为一条「主动惊喜推荐」写一段解释，说明为什么这条内容可能会让这个人意外地喜欢。\n"
        "这不是普通推荐——这是你作为一个真正懂他的朋友，主动跑来说「这条你一定要看」。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON，包含 delight_reason 和 delight_hook。\n"
        "2. delight_reason（80-200字中文口语）要解释：\n"
        "   - 这条内容为什么会让这个人产生「意外的共鸣」或「惊喜的发现」\n"
        "   - 必须引用用户画像中的至少一个深层需求、洞察假说或认知偏好\n"
        "   - 语气比普通推荐更亲密、更有把握，像「我知道你不常看这类，但这条真的会戳到你」\n"
        "3. delight_hook（2-4个中文字）是一个短标签，用于UI徽章展示。\n"
        "   例如：深层共鸣、跨域惊喜、灵感碰撞、意外契合、隐藏需求\n"
        "4. 不要用：强烈推荐、值得一看、高质量、信息密度等套话。\n"
        "5. reason_stub 提供了打分信号的线索，用它来组织 delight_reason 的叙事方向。\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "delight_reason": "你之前聊到过想搞明白...",\n'
        '  "delight_hook": "深层共鸣"\n'
        "}\n"
        "</output_schema>"
    )
    system_prompt = "\n\n".join(
        [system_prompt, _render_tone_profile(tone_profile, {source_platform: 1.0})]
    )
    user_prompt = "\n\n".join(
        [
            "<profile_summary>",
            json.dumps(profile_summary, ensure_ascii=False, indent=2),
            "</profile_summary>",
            "<content_summary>",
            json.dumps(content_summary, ensure_ascii=False, indent=2),
            "</content_summary>",
            "<reason_stub>",
            reason_stub,
            "</reason_stub>",
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


def build_speculation_generation_prompt(
    *,
    profile_summary: str,
    existing_speculations: list[str],
    cooldown_domains: list[str],
    confirmed_domains: list[str],
    count: int = 5,
) -> list[dict[str, str]]:
    """Build a prompt for generating speculative interest directions."""
    system_prompt = (
        "<task>\n"
        "你是一个用户兴趣探索引擎。根据用户的已确认画像，推测用户可能感兴趣但尚未接触的领域。\n"
        "你需要找到心理学上的桥接关系——从已有兴趣模式中推断出合理的新方向。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 每个猜测必须有 reason 说明心理学桥接逻辑（为什么从已有兴趣能推出这个新方向）\n"
        "2. 不能重复已有兴趣、已在探索中的方向、或冷却期的方向\n"
        "3. 方向应具体到可以搜索到内容（不要太抽象）\n"
        "4. confidence 范围 0.3-0.6，越有把握越高\n"
        "5. 平衡近距离延伸与跨领域探索——近距离方向更容易被用户实际点击，\n"
        "   不要一味追求跨领域而忽略用户真正会看的内容\n"
        "6. 人格共振检验：对每个猜测自问『这个人下次打开B站，\n"
        "   真的会点击这类内容吗？』如果答案不确定，降低 confidence 或换方向\n"
        "7. 输出严格 JSON，不要附带解释\n"
        "8. 分散性强制要求：\n"
        "   - 所有猜测的 category 必须两两不同，不允许任何两个猜测属于同一大类\n"
        "   - 不同猜测的 domain 核心主题词必须无重叠（禁止同概念换皮）\n"
        "   - 猜测必须横跨至少 3 种不同的认知维度，例如：\n"
        "     知识理解型（科普/历史/哲学）、技能实践型（手工/编程/烹饪）、\n"
        "     审美体验型（音乐/摄影/建筑）、社会观察型（纪录片/人物/社会议题）、\n"
        "     身体感知型（运动/旅行/自然）\n"
        "   - 如果用户兴趣集中在某一维度（如全是知识型），\n"
        "     至少 1 个猜测必须来自其他维度\n"
        "9. 桥接距离要求：\n"
        "   - 至少 2 个猜测是近距离桥接（与已有兴趣共享明确属性，\n"
        "     在B站上容易搜到且用户大概率会点击）\n"
        "   - 至少 1 个猜测是远距离桥接（与已有兴趣仅共享深层心理需求，\n"
        "     表面看不出明显关联）\n"
        "   - 至少 1 个猜测是纯新奇方向（从用户人格特质出发，\n"
        "     而非从现有兴趣出发推理）\n"
        "</rules>\n\n"
        "<bridge_examples>\n"
        "近距离桥接：\n"
        "- 策略游戏 + 数据分析 -> 博弈论科普（共通：系统性思维+决策优化）\n"
        "远距离桥接：\n"
        "- 深度时事解读 + 对因果链的执念 -> 法医学纪录片（共通：追溯真相的思维模式）\n"
        "纯新奇方向：\n"
        "- 用户特质「对精密结构的审美偏好」 -> 机械表拆解/钟表工艺\n"
        "  （不从兴趣出发，而从人格出发：精密结构审美→微观工艺世界）\n\n"
        "坏的示例（太集中）：\n"
        "- 博弈论科普 + 纳什均衡 + 策略模型（本质同一主题）\n"
        "- 认知科学 + 神经科学 + 心理学实验（同一维度的三个变体）\n"
        "</bridge_examples>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "speculations": [\n'
        "    {\n"
        '      "domain": "一级方向名称（宽泛领域）",\n'
        '      "category": "所属大类（必须两两不同）",\n'
        '      "reason": "心理学桥接推理：从X兴趣+Y特质->可能喜欢此方向",\n'
        '      "bridge_type": "near|far|novel",\n'
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
        "例如 domain=\"建筑美学\" → specifics=[\"现代主义建筑纪录片\", \"中式园林设计\", \"包豪斯风格解读\"]\n"
        "</specifics_rules>"
    )

    exclude_list = sorted(set(existing_speculations + cooldown_domains + confirmed_domains))
    exclude_text = "以下方向不要重复：" + "、".join(exclude_list) if exclude_list else "无排除项"
    user_prompt = "\n\n".join([
        "<user_profile>",
        profile_summary,
        "</user_profile>",
        "<exclude_domains>",
        exclude_text,
        "</exclude_domains>",
        f"请生成 {count} 个猜测兴趣方向。",
    ])
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
