"""Closed viewing-mode taxonomy for discovery/recommendation style keys."""

from __future__ import annotations

import re

STYLE_KEY_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("deep_focus", "深度专注:原理、结构、系统分析、长线思考"),
    ("quick_scan", "快速扫信息:热点、更新、短知识、资讯变化"),
    ("hands_on", "跟做学习:教程、攻略、实操步骤、解决问题"),
    ("decision_support", "辅助决策:测评、盘点、对比、购买前参考"),
    ("story_immersion", "叙事沉浸:纪录片、人物故事、事件复盘"),
    ("opinion_sparring", "观点碰撞:评论、立场、辩论、锐评"),
    ("social_chat", "陪聊/对谈:闲聊、访谈、播客式内容"),
    ("daily_wander", "日常漫游:vlog、生活流、低目标浏览"),
    ("mood_release", "情绪释放:搞笑、整活、吐槽、二创"),
    ("aesthetic_browse", "审美浏览:视觉、混剪、空镜、作品展示"),
    ("ambient_companion", "背景陪伴:背景音乐、白噪音、长时间陪伴"),
    ("live_pulse", "现场脉冲:直播切片、演出现场、赛事高光"),
    ("curiosity_spark", "新鲜猎奇:奇怪事实、冷门切口、意外发现"),
)

VALID_STYLE_KEYS: frozenset[str] = frozenset(key for key, _ in STYLE_KEY_DEFINITIONS)

STYLE_KEY_PROMPT_TEXT: str = "\n".join(
    f"   - {key}: {description}" for key, description in STYLE_KEY_DEFINITIONS
)

# Legacy content may already be cached with old content-form labels. Keep
# consuming it by mapping each old label onto the new viewing-mode taxonomy.
LEGACY_STYLE_KEY_MAP: dict[str, str] = {
    "deep_dive": "deep_focus",
    "tech_analysis": "deep_focus",
    "music_analysis": "deep_focus",
    "news_brief": "quick_scan",
    "practical_guide": "hands_on",
    "tutorial_short": "hands_on",
    "game_strategy": "hands_on",
    "review_roundup": "decision_support",
    "unboxing_experience": "decision_support",
    "story_doc": "story_immersion",
    "emotional_narrative": "story_immersion",
    "true_crime": "story_immersion",
    "opinion_stand": "opinion_sparring",
    "light_chat": "social_chat",
    "lifestyle": "daily_wander",
    "fun_variety": "mood_release",
    "parody_remix": "mood_release",
    "visual_showcase": "aesthetic_browse",
    "audio_background": "ambient_companion",
    "music_live": "live_pulse",
    "live_moment": "live_pulse",
    "sports_highlight": "live_pulse",
    "sci_fact": "curiosity_spark",
}


def normalize_style_key(value: object) -> str:
    """Return a canonical viewing-mode style key, or ``""`` if unknown."""
    if value is None:
        return ""
    token = re.sub(r"[\s-]+", "_", str(value).strip().lower())
    if token in VALID_STYLE_KEYS:
        return token
    return LEGACY_STYLE_KEY_MAP.get(token, "")
