"""Style classification rules for discovered content.

Defines token-based rules mapping content titles/descriptions to style keys.
Style keys are used downstream for diversity control in the candidate pool.
"""

from __future__ import annotations

# Ordered list of (style_key, token_tuple) — first match wins.
STYLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("quick_scan", ("突发", "最新", "局势", "发布", "快讯", "回应", "三分钟看懂")),
    ("decision_support", ("购买前", "盘点", "测评", "对比", "推荐", "排行", "top", "年度")),
    ("hands_on", ("攻略", "教程", "入门", "怎么做", "建议", "指南", "课程", "从零开始", "速刷")),
    (
        "story_immersion",
        ("纪录片", "纪录", "故事", "电影", "小说史", "全过程", "制造过程", "案件", "悬案"),
    ),
    ("opinion_sparring", ("观点", "评论", "立场", "锐评", "看法", "我觉得")),
    ("social_chat", ("闲聊", "杂谈", "聊天", "聊聊", "对话", "访谈", "播客")),
    ("daily_wander", ("日常", "vlog", "生活", "房间", "一天", "routine", "做饭")),
    ("mood_release", ("搞笑", "吐槽", "整活", "挑战", "名场面", "鬼畜", "恶搞", "沙雕", "二创")),
    ("aesthetic_browse", ("空镜", "混剪", "素材", "视觉", "厨向mad", "审美")),
    ("ambient_companion", ("背景音乐", "陪伴", "轻音乐", "学习音乐", "专注", "白噪音")),
    (
        "live_pulse",
        ("直播", "切片", "弹幕", "主播", "演唱会", "live", "现场", "集锦", "高光", "绝杀"),
    ),
    (
        "curiosity_spark",
        ("冷知识", "趣味", "奇怪", "不可思议", "惊人", "你知道吗", "逻辑谜题", "烧脑"),
    ),
    (
        "deep_focus",
        (
            "讲透",
            "底层逻辑",
            "为什么",
            "如何诞生",
            "实验经济学",
            "定理",
            "理论",
            "原理",
            "解析",
            "机制",
            "强度",
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
            "哲学",
            "控制论",
            "混沌",
            "自组织",
            "世界观",
            "设定",
            "悖论",
            "乐理",
            "编曲",
            "和声",
        ),
    ),
]

# Fallback rules when no token matches — keyed by source_strategy.
# Note: explore intentionally has no fallback to avoid collapsing all
# cross-domain results into the same style bucket (hurts diversity).
SOURCE_FALLBACKS: dict[str, str] = {
    "trending": "quick_scan",
}

DEFAULT_STYLE: str = "social_chat"


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
