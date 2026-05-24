"""LLM-based content extraction from raw page text.

Converts unstructured web page text into structured DiscoveredContent
objects using an LLM to identify titles, authors, summaries, and URLs.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM_PROMPT = """\
<task>
你是一个内容提取助手。给定一段网页文本，从中提取所有独立的内容条目。
</task>

<rules>
1. 输出必须是严格 JSON 数组。
2. 每个条目包含以下字段：
   - title: 内容标题（必填）
   - author: 作者名（如有）
   - summary: 内容摘要，50-200字（如有）
   - url: 内容链接（如有，必须是完整URL）
   - content_id: 内容的唯一标识（从URL提取，如笔记ID、帖子ID等）
3. 只提取真正的内容条目（文章、帖子、笔记、视频等），忽略导航、广告、页脚。
4. 如果页面文本中没有可提取的内容条目，返回空数组 []。
5. 最多提取 20 条。
</rules>

<output_schema>
[
  {
    "title": "标题",
    "author": "作者",
    "summary": "摘要",
    "url": "https://example.com/post/123",
    "content_id": "123"
  }
]
</output_schema>
"""


async def extract_content_from_page(
    page_text: str,
    *,
    source_platform: str,
    llm_service: Any,
    base_url: str = "",
) -> list[DiscoveredContent]:
    """Use an LLM to extract structured content items from raw page text.

    Args:
        page_text: Raw visible text from a web page.
        source_platform: Platform identifier (e.g. "xiaohongshu", "web").
        llm_service: An LLM service with ``complete_structured_task()``.
        base_url: Base URL for resolving relative links.

    Returns:
        List of DiscoveredContent items extracted from the page.
    """
    from openbiliclaw.discovery.engine import DiscoveredContent

    if not page_text or len(page_text.strip()) < 50:
        logger.debug("Page text too short for extraction (%d chars)", len(page_text))
        return []

    # Truncate very long pages to stay within LLM context limits
    truncated = page_text[:8000] if len(page_text) > 8000 else page_text

    user_prompt = (
        f"<platform>{source_platform}</platform>\n\n<page_text>\n{truncated}\n</page_text>"
    )

    try:
        response = await llm_service.complete_structured_task(
            system_instruction=_EXTRACTION_SYSTEM_PROMPT,
            user_input=user_prompt,
            temperature=0.3,
            max_tokens=4096,
            caller=f"sources.{source_platform}.extract",
        )
    except Exception:
        logger.exception("LLM extraction failed for %s page", source_platform)
        return []

    try:
        items_raw = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM extraction returned invalid JSON: %.200s", response.content)
        return []

    if not isinstance(items_raw, list):
        logger.warning("LLM extraction returned non-list: %s", type(items_raw))
        return []

    results: list[DiscoveredContent] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        # LLMs often return JSON nulls for missing fields — ``item.get(key, "")``
        # then yields ``None`` (the value), not the default, and ``str(None)``
        # produces the string ``"None"`` which looks populated to every
        # downstream truthiness check. Coerce to "" before stripping.
        title = str(item.get("title") or "").strip()
        if not title:
            continue

        content_id = str(item.get("content_id") or "").strip()
        content_url = str(item.get("url") or "").strip()

        # Generate a content_id from URL if not provided
        if not content_id and content_url:
            content_id = content_url.rstrip("/").rsplit("/", 1)[-1]
        if not content_id:
            content_id = title[:32]

        results.append(
            DiscoveredContent(
                content_id=content_id,
                content_url=content_url,
                source_platform=source_platform,
                title=title,
                author_name=str(item.get("author") or "").strip(),
                description=str(item.get("summary") or "").strip(),
                source_strategy="web_extract",
            )
        )

    logger.info(
        "Extracted %d content items from %s page (%d chars)",
        len(results),
        source_platform,
        len(page_text),
    )
    return results
