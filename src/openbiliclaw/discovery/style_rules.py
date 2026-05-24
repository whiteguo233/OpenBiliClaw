"""Style classification rules for discovered content.

Defines token-based rules mapping content titles/descriptions to style keys.
Style keys are used downstream for diversity control in the candidate pool.
"""

from __future__ import annotations

# Ordered list of (style_key, token_tuple) — first match wins.
STYLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "game_strategy",
        (
            "攻略",
            "机制",
            "强度",
            "实机",
            "联机",
            "mod",
            "杀戮尖塔",
            "爬塔",
        ),
    ),
    (
        "news_brief",
        (
            "突发",
            "最新",
            "局势",
            "锐评",
            "发布",
            "快讯",
            "回应",
            "自焚",
        ),
    ),
    (
        "practical_guide",
        (
            "教程",
            "入门",
            "购买前",
            "怎么做",
            "建议",
            "指南",
            "统计",
            "课程",
            "导论",
            "从零开始",
            "原理图解",
            "数学原理",
            "透彻理解",
            "一小时从",
        ),
    ),
    (
        "story_doc",
        (
            "纪录片",
            "纪录",
            "故事",
            "电影",
            "小说史",
            "讲了一个怎样",
            "短片",
            "全过程",
            "制造过程",
            "工艺难度",
            "设计面面观",
        ),
    ),
    (
        "visual_showcase",
        (
            "空镜",
            "混剪",
            "素材",
            "视觉",
            "厨向mad",
        ),
    ),
    (
        "tech_analysis",
        (
            "大模型",
            "人工智能",
            "芯片",
            "显微镜",
            "纳米",
            "编译器",
            "算法",
            "架构",
            "gpu",
            "cpu",
            "内核",
        ),
    ),
    (
        "fun_variety",
        (
            "搞笑",
            "吐槽",
            "整活",
            "挑战",
            "名场面",
            "鬼畜",
            "恶搞",
            "沙雕",
        ),
    ),
    (
        "lifestyle",
        (
            "日常",
            "vlog",
            "生活",
            "开箱",
            "房间",
            "一天",
            "routine",
        ),
    ),
    (
        "review_roundup",
        (
            "盘点",
            "测评",
            "推荐",
            "合集",
            "排行",
            "top",
            "年度",
        ),
    ),
    (
        "deep_dive",
        (
            "讲透",
            "底层逻辑",
            "为什么",
            "如何诞生",
            "实验经济学",
            "科幻",
            "定理",
            "理论",
            "原理",
            "解析",
            "原型",
            "战力系统",
            "哲学",
            "控制论",
            "混沌",
            "自组织",
            "世界观",
            "设定",
            "悖论",
            "逻辑谜题",
            "谜题",
            "存在主义",
            "形而上",
        ),
    ),
]

# Fallback rules when no token matches — keyed by source_strategy.
# Note: explore intentionally has no fallback to avoid collapsing all
# cross-domain results into the same style bucket (hurts diversity).
SOURCE_FALLBACKS: dict[str, str] = {
    "trending": "news_brief",
}

DEFAULT_STYLE: str = "light_chat"


def infer_style_key(
    *,
    title: str,
    description: str = "",
    reason: str = "",
    source_strategy: str = "",
) -> str:
    """Infer a style_key from content text using rule-based token matching."""
    text = " ".join([title, description, reason]).lower()

    for style_key, tokens in STYLE_RULES:
        if any(token in text for token in tokens):
            return style_key

    fallback = SOURCE_FALLBACKS.get(source_strategy)
    if fallback:
        return fallback

    return DEFAULT_STYLE
