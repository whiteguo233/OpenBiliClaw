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
6. 不要把兴趣 topic 堆成画像主体；题材、UP 主、作品名最多只举 1 到 2 个例子，
   而且只能当证据，不要当正文主干。
7. 可以参考非临床的认知风格、内在驱动力、阶段状态来组织描述，但不要写理论术语，
   不要写成心理报告、咨询记录或说明书，要像熟人总结这个人的气质和状态。
8. mbti 字段必须填写：根据行为数据推断最可能的 MBTI 四字母类型（如 INTJ、ENFP），
   confidence 取 0.5-0.9，四个维度 EI/SN/TF/JP 都要填。如果证据不足可以降低 confidence，
   但不要留空。
9. cognitive_style：如果 preference_summary 中已有 cognitive_style，直接沿用并微调措辞，
   不要推翻或重新推断。如果没有，再从行为模式推断。
10. current_phase 和 life_stage 必须基于具体行为证据：引用具体的观看主题、活动模式来描述，
    不要写"处于探索阶段"之类的空话。参考 history_summary 中的 recent_titles 判断最近的兴趣方向。
    life_stage 必须尽可能推断具体的人口学特征：从内容偏好推断学历层次（如频繁看考研/学术内容→研究生阶段）、
    职业阶段（如看面试/职场/转行内容→求职期）、年龄段（如内容成熟度和话题关注点）。
    用"XX高校研究生在读""工作2-3年的互联网从业者"这样的具体描述，
    不要用"探索与转型期""存在主义实践"等哲学化表述替代可推断的事实。
    current_phase 应聚焦用户当前面临的具体张力或抉择（如"AI浪潮下的职业焦虑与创作冲动并存"），
    而不是抽象的认知状态描述（如"从宏观审美转向消费决策"）。
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
5. 用户画像中包含 interest_domains（一级兴趣域）和 interests（二级具体兴趣）。
   你必须保证 query 主题分布均匀，避免集中在用户最强兴趣上：
   - 约 30% query 使用一级兴趣域名称搜索（如 "科技 深度" "游戏 机制"），
     目的是发现该域中用户尚未接触的新内容。
   - 约 30% query 使用二级兴趣的细分角度（非直接重复现有词条）。
   - 约 40% query 跨域探索（桥接用户认知风格或深层需求到相邻但陌生的领域）。
   跨域 query 不需要完全脱离用户认知范围，但核心主题词必须不在用户任何
   interest_domains / interests 中出现。
6. 所有 query 的核心主题词（第一个实词）必须两两不同，
   禁止同一概念换皮出现多次。
</rules>

<output_schema>
{
  "queries": [
    "纪录片 原理",
    "摄影 构图 深度讲解",
    "历史 长视频 深度",
    "认知科学 决策 机制",
    "城市规划 纪录片"
  ]
}
</output_schema>

<examples>
假设用户 interest_domains 包含 [科技(强化学习, ppo), 历史(纪录片)]，
认知风格偏好"结构化分析、高信息密度"：

一级域 query（~40%）：
- "科技 前沿 深度解读"（用域名搜索，覆盖用户未知的科技子领域）
- "历史 冷知识 讲解"（用域名搜索，发现域内新角度）
- "游戏 机制设计 分析"（如果画像有游戏域）

二级细分 query（~30%）：
- "冷战 外交 深度解析"（历史域内的细分角度，非直接重复）
- "强化学习 应用 案例"（具体兴趣的新切面）

跨域探索 query（~30%）：
- "认知科学 决策 机制"（上游学科，桥接：结构化分析偏好）
- "城市规划 发展史 纪录片"（相邻领域，桥接：纪录片风格+系统视角）

坏的 query：
- "强化学习 ppo"（和已有二级兴趣完全重合，无新意）
- "美食"（与用户认知风格无桥接关系，随机发散）
- "博弈论 纳什均衡 策略模型"（三个 query 本质相同，浪费多样性配额）
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
) -> list[dict[str, str]]:
    """Build a structured prompt for content relevance evaluation.

    Args:
        profile_summary: User profile summary.
        content_summary: Content metadata.
        source_context: Discovery context hint (e.g. search / trending / explore).
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
        + "你要评估一个 B 站内容与这个用户画像的匹配度。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON，不要附带解释。\n"
        "2. score 范围必须在 0 到 1 之间。\n"
        "3. reason 只写一句中文，解释为什么这个人会喜欢或不喜欢这个内容。\n"
        "4. 不要只说\"因为热门\"或\"因为看过类似的\"，要结合用户画像。\n"
        "5. 根据发现路径调整评判宽容度：search 要求高度匹配；"
        "trending 来源的内容已经过大众验证，只要不在用户讨厌列表中且内容质量过关，基础分应 ≥ 0.6，若还能和画像产生关联则给更高分；"
        "related_chain 允许适度偏移；explore 只要心理需求层面说得通就应该给较高分，即使主题完全陌生也不应因此大幅扣分。\n"
        "6. topic_group 是该内容所属的粗粒度主题分类，用于推荐去重。"
        "要求：2-4 个中文词，抽象到能覆盖同类内容，"
        "例如\"强化学习\"而非\"强化学习ppo算法源码级讲解\"，"
        "\"城市建筑\"而非\"上海外滩建筑群纪录片\"。"
        "同一主题的不同切面必须归为同一个 topic_group。"
        "语义相同的主题必须用同一个词——\"AI\" \"人工智能\" \"机器学习\" 统一写成 \"人工智能\"，"
        "\"RL\" \"强化学习\" 统一写成 \"强化学习\"。\n"
        "7. style_key 从以下 9 个选项中选一个，描述该内容的呈现风格：\n"
        "   game_strategy（游戏攻略/机制解析）/ news_brief（新闻资讯/时事快评）/ "
        "practical_guide（教程/入门/实操指南）/ story_doc（纪录片/故事/人物传记）/ "
        "visual_showcase（视觉向/混剪/空镜）/ tech_analysis（技术深度分析/硬件评测）/ "
        "philosophy_culture（哲学/文化/思想讨论）/ deep_dive（原理讲解/学术解析）/ "
        "light_chat（日常/闲聊/娱乐/其他）\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "{\n"
        '  "score": 0.78,\n'
        '  "reason": "这个视频的讲解深度和表达方式更贴近你长期偏好的高信息密度内容。",\n'
        '  "topic_group": "认知科学",\n'
        '  "style_key": "deep_dive"\n'
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
        + "你要批量评估多个 B 站内容与这个用户画像的匹配度。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON 数组，不要附带解释。\n"
        "2. 数组长度必须与输入内容数量一致，顺序一一对应。\n"
        "3. 每项包含 score(0-1)、reason(一句中文)、topic_group(2-4词粗分类)、"
        "style_key(9选1)。\n"
        "4. 根据发现路径调整评判宽容度：search 要求高度匹配；"
        "trending 基础分 >= 0.6；related_chain 允许适度偏移；"
        "explore 只要心理需求说得通就给较高分。\n"
        "5. topic_group 规则：2-4 个中文词的粗分类，同主题不同切面统一。"
        "语义相同必须用同一词（AI/人工智能/机器学习 统一为 人工智能）。\n"
        "6. style_key 从 9 个选项中选：game_strategy / news_brief / "
        "practical_guide / story_doc / visual_showcase / tech_analysis / "
        "philosophy_culture / deep_dive / light_chat\n"
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
) -> list[dict[str, str]]:
    """Build a structured prompt for friend-style recommendation expression."""
    system_prompt = """
<task>
你要像一个真正懂这个人的老B友一样，给出一段推荐这条 B 站内容的话。
</task>

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


def build_batch_expression_prompt(
    *,
    profile_summary: dict[str, object],
    content_items: list[dict[str, object]],
    tone_profile: ToneProfile | None,
) -> list[dict[str, str]]:
    """Build a prompt that generates expressions for multiple items in one call."""
    system_prompt = (
        "<task>\n"
        "你要像一个真正懂这个人的老B友一样，为多条 B 站内容各写一段推荐话。\n"
        "</task>\n\n"
        "<rules>\n"
        "1. 输出必须是严格 JSON 数组，数组长度与输入内容数量一致，顺序一一对应。\n"
        "2. 每项包含 expression(50-150字中文口语) 和 topic_label(个性化主题标签)。\n"
        "3. expression 像朋友私聊，必须引用至少一个具体内容细节"
        "（标题关键词、UP主特点、独特切入角度），不要说空话。\n"
        "4. 避免：算法套话、信息密度、高质量、深度好文、值得一看、强烈推荐。\n"
        "5. explore 来源的内容要解释陌生领域和用户认知偏好的关联。\n"
        "6. 每条 expression 的开头措辞必须不同，禁止重复同一句式。\n"
        "</rules>\n\n"
        "<output_schema>\n"
        "[\n"
        '  {"expression": "这条...", "topic_label": "xxx"},\n'
        '  {"expression": "这个UP主...", "topic_label": "yyy"}\n'
        "]\n"
        "</output_schema>"
    )
    system_prompt = "\n\n".join([system_prompt, _render_tone_profile(tone_profile)])
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
6. novelty_level 范围必须在 0.65 到 0.95 之间；至少 3 个 domain 的 novelty_level ≥ 0.75。
7. 每个 domain 生成 2 到 3 个适合 B 站搜索的 query，query 必须具体到可直接搜索的细分话题，禁止只写宽泛大词。
8. 不同 domain 的 query 之间词汇重叠率要低；每个 query 必须包含一个内容形式词
   （如 纪录片/深度讲解/科普/测评/vlog/解说/手书/混剪），
   不同 domain 必须使用不同的形式词，以保证搜索结果在风格维度上有差异。
9. 反信息茧房：不同 domain 的 query 第一个实词（核心主题词）必须两两不同，
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
      "queries": ["上海 里弄 改造 纪录片", "参数化 建筑 深度讲解", "废墟 探险 vlog"]
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
        "5. 优先选择跨领域的交叉方向，而非已有兴趣的简单延伸\n"
        "6. 输出严格 JSON，不要附带解释\n"
        "7. 分散性强制要求：\n"
        "   - 所有猜测的 category 必须两两不同，不允许任何两个猜测属于同一大类\n"
        "   - 不同猜测的 domain 核心主题词必须无重叠（禁止同概念换皮）\n"
        "   - 猜测必须横跨至少 3 种不同的认知维度，例如：\n"
        "     知识理解型（科普/历史/哲学）、技能实践型（手工/编程/烹饪）、\n"
        "     审美体验型（音乐/摄影/建筑）、社会观察型（纪录片/人物/社会议题）、\n"
        "     身体感知型（运动/旅行/自然）\n"
        "   - 如果用户兴趣集中在某一维度（如全是知识型），\n"
        "     至少 2 个猜测必须来自其他维度\n"
        "8. 桥接距离要求：\n"
        "   - 至少 1 个猜测是近距离桥接（与已有兴趣共享 1 个属性）\n"
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
