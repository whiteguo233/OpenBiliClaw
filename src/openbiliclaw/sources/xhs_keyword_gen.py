"""LLM-based xiaohongshu-style keyword generator.

Rewrites SoulProfile interest tags into xhs-flavored search queries —
concrete, lifestyle-oriented, long-tail — so the extension's background
dispatcher can search xhs in a way that matches how real users browse.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast

from openbiliclaw.llm.json_utils import parse_llm_json_tolerant

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.soul.profile import OnionProfile, SoulProfile

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """你是小红书内容策略师。给你一个用户的兴趣画像（B 站等平台归纳的），\
请把它改写成 N 个"小红书风格"的搜索关键词。

小红书风格的关键词特征：
- 生活化、具象、带场景（而不是宽泛的学科/品类词）
- 偏长尾、偏体验分享（"教程/攻略/vlog/踩坑/真实体验"等尾词常见）
- 口语化，2~8 个字为主，必要时可稍长
- 避免只给单字类目词（"科技"、"游戏"），要加限定
- 避免和 bilibili 完全相同的写法

只返回 JSON，不要任何解释文字。格式：
{"keywords": ["...", "..."]}"""


def _build_user_prompt(profile: SoulProfile | OnionProfile, count: int) -> str:
    # Same canonical structured profile every other discovery prompt sees
    # (B站 / YouTube / X query-gen, all-platform evaluation) — no divergent
    # representation. Lazy import keeps sources/ off discovery/ at module load.
    # Deterministic dump keeps the prompt-cache prefix stable.
    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    # build_profile_summary is annotated for SoulProfile but supports OnionProfile
    # too (back-compat properties); the producer hands us either.
    summary = build_profile_summary(cast("SoulProfile", profile))
    return (
        "<profile_summary>\n"
        + json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n</profile_summary>\n\n"
        + "请基于上面画像里的兴趣（interests / interest_domains），避开 disliked_topics，"
        + f"输出 {count} 个小红书风格关键词。"
    )


async def generate_xhs_keywords(
    llm_service: LLMService,
    profile: SoulProfile | OnionProfile,
    *,
    count: int = 5,
) -> list[str]:
    """Generate up to ``count`` xhs-style search keywords from *profile*.

    Falls back to the profile's interest names (deterministic) when the LLM is
    unavailable / fails / returns nothing usable, so the unified keyword planner
    (and the legacy path) never loses xhs to a transient LLM failure. Returns an
    empty list only when the profile has no usable interests.
    """
    if not profile.preferences.interests:
        return []
    keywords = await _llm_xhs_keywords(llm_service, profile, count)
    return keywords or _interest_name_fallback(profile, count)


async def _llm_xhs_keywords(
    llm_service: LLMService,
    profile: SoulProfile | OnionProfile,
    count: int,
) -> list[str]:
    """The LLM attempt; returns ``[]`` on any failure so the caller can fall back."""
    try:
        response = await llm_service.complete_structured_task(
            system_instruction=_SYSTEM_PROMPT,
            user_input=_build_user_prompt(profile, count),
            temperature=0.8,
            max_tokens=512,
            caller="sources.xhs.keyword_gen",
        )
    except Exception as exc:
        logger.warning("xhs keyword LLM call failed: %s", exc)
        return []

    content = response.content.strip()
    payload = parse_llm_json_tolerant(content)
    if payload is None:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("xhs keyword LLM returned non-JSON: %r", content[:200])
            return []
    if not isinstance(payload, dict):
        return []
    raw_keywords = payload.get("keywords", [])
    if not isinstance(raw_keywords, list):
        return []

    seen: set[str] = set()
    keywords: list[str] = []
    for item in raw_keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        keywords.append(text)
        if len(keywords) >= count:
            break
    return keywords


def _interest_name_fallback(profile: SoulProfile | OnionProfile, count: int) -> list[str]:
    """Deterministic interest-name keywords (mirrors B站/YouTube/抖音 fallback)."""
    ranked = sorted(
        profile.preferences.interests, key=lambda tag: float(tag.weight or 0.0), reverse=True
    )
    seen: set[str] = set()
    out: list[str] = []
    for tag in ranked:
        name = str(tag.name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= count:
            break
    return out
