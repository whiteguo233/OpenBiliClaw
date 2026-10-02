"""Recommendation and discovery-pool reading tools (M3).

Both tools are strictly read-only previews over the pool readers on
``Database`` (``get_pool_candidates`` / ``count_pool_readiness`` / …) — they
never call ``RecommendationEngine.serve()``, so nothing is marked shown and
the pool is never consumed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .common import clamp_int, maybe_await, render_content_reference, require_component, short
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)


def build_recommendation_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the recommendation/discovery-domain tools bound to ``ctx``."""
    return [
        Tool(
            name="get_recommendations",
            description=(
                "预览推荐池头部：系统接下来最可能推荐给用户的条目（标题、UP主/作者、"
                "主题、相关度）。这是只读预览——不消耗推荐池、不标记已展示。"
                "定位信息含保存所需的 content_id / source_platform / content_url；"
                "未展示候选没有 recommendation_id，不可猜测 ID 提交推荐反馈。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 5，上限 20",
                    },
                    "source_platform": {
                        "type": "string",
                        "description": "只看某个平台的候选（如 bilibili / xiaohongshu），可空",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda args: _get_recommendations(ctx, args),
        ),
        Tool(
            name="query_discovery_pool",
            description=(
                "查询 discovery 候选池库存：立即可服务数量、待处理数量、有货平台列表，"
                "可选抽样查看候选标题。用于回答“现在池子里有什么/还有多少”。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "sample_size": {
                        "type": "integer",
                        "description": "抽样查看的候选条数，默认 5，0 表示不抽样",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda args: _query_discovery_pool(ctx, args),
        ),
    ]


def _render_pool_row(index: int, row: dict[str, Any]) -> str:
    title = short(row.get("title"), 60) or "(无标题)"
    author = short(row.get("up_name") or row.get("author_name"), 24)
    platform = str(row.get("source_platform") or "bilibili").strip().lower()
    topic = short(row.get("pool_topic_label") or row.get("topic_key"), 30)
    try:
        score = f"{float(row.get('relevance_score') or 0.0):.2f}"
    except (TypeError, ValueError):
        score = "0.00"
    parts = [f"{index}. {title}"]
    if author:
        parts.append(f"— {author}")
    parts.append(f"[{platform}]")
    if topic:
        parts.append(f"主题:{topic}")
    parts.append(f"相关度:{score}")
    return " ".join(parts) + render_content_reference(row)


async def _get_recommendations(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    db = require_component(ctx.database, "database")
    limit = clamp_int(args.get("limit"), default=5, maximum=20)
    platform = str(args.get("source_platform") or "").strip().lower()
    if platform:
        rows = await maybe_await(db.get_pool_candidates_for_platform(platform, limit=limit))
    else:
        rows = await maybe_await(db.get_pool_candidates(limit=limit))
    if not rows:
        scope = f"（平台: {platform}）" if platform else ""
        return f"推荐池当前没有可服务的候选{scope}。"
    lines = [_render_pool_row(index, row) for index, row in enumerate(rows, start=1)]
    header = "推荐池头部预览"
    if platform:
        header += f"（平台: {platform}）"
    return header + "：\n" + "\n".join(lines) + "\n（只读预览，未消耗推荐池）"


async def _query_discovery_pool(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    db = require_component(ctx.database, "database")
    sample_size = clamp_int(args.get("sample_size"), default=5, minimum=0, maximum=20)
    readiness = await maybe_await(db.count_pool_readiness())
    available = await maybe_await(db.count_pool_candidates())
    platforms = await maybe_await(db.list_servable_pool_platforms())

    lines = [
        f"立即可服务候选: {available} 条",
        f"原始素材: {int(readiness.get('raw', 0))} 条，"
        f"待处理: {int(readiness.get('pending', 0))} 条",
        f"有货平台: {', '.join(platforms) if platforms else '（无）'}",
    ]
    if sample_size > 0:
        rows = await maybe_await(db.get_pool_candidates(limit=sample_size))
        if rows:
            lines.append("候选抽样:")
            lines.extend(_render_pool_row(index, row) for index, row in enumerate(rows, start=1))
    return "Discovery 候选池状态：\n" + "\n".join(lines)
