"""Local content-history reading tools (M3).

``get_watch_history`` reads the local 30-day content-history projection and
the local saved lists (收藏 / 稍后再看). It never touches the live B站 API —
all data comes from the storage layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .common import clamp_int, maybe_await, render_content_reference, require_component, short
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)

_HISTORY_KIND_LABELS = {
    "clicked": "已点击",
    "shown": "已展示未点击",
    "removed": "已移除/点踩",
}
_SAVED_KIND_LABELS = {
    "favorite": "收藏清单",
    "watch_later": "稍后再看",
}


def build_bilibili_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the local history-reading tools bound to ``ctx``."""
    return [
        Tool(
            name="get_watch_history",
            description=(
                "查询本地记录的内容历史：clicked 已点击 / shown 已展示未点 / "
                "removed 已移除或点踩 / favorite 收藏清单 / watch_later 稍后再看。"
                "只读本地数据层，不触发真实抓取。"
                "定位信息含内容 ID、平台和链接；推荐历史还含可用于 submit_feedback 的"
                " recommendation_id（如存在），不要把列表序号当成 ID。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["clicked", "shown", "removed", "favorite", "watch_later"],
                        "description": "历史类别，默认 clicked",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 10，上限 50",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda args: _get_watch_history(ctx, args),
        ),
    ]


async def _get_watch_history(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    db = require_component(ctx.database, "database")
    kind = str(args.get("kind") or "clicked").strip().lower()
    limit = clamp_int(args.get("limit"), default=10, maximum=50)

    if kind in _HISTORY_KIND_LABELS:
        items, total = await maybe_await(db.list_content_history(kind, limit=limit))
        label = _HISTORY_KIND_LABELS[kind]
        if not items:
            return f"最近 30 天没有{label}的内容记录。"
        lines = [
            f"[{short(item.get('occurred_at'), 19)}] {short(item.get('title'), 50) or '(无标题)'}"
            + (
                f" — {short(item.get('author_name'), 24)}"
                if str(item.get("author_name") or "").strip()
                else ""
            )
            + f" [{str(item.get('source_platform') or 'bilibili').strip().lower()}]"
            + render_content_reference(item)
            for item in items
        ]
        return f"{label}的内容（最近 {len(items)} 条，共 {total} 条）：\n" + "\n".join(lines)

    if kind in _SAVED_KIND_LABELS:
        rows = await maybe_await(db.list_saved_memberships(kind, limit=limit))
        label = _SAVED_KIND_LABELS[kind]
        if not rows:
            return f"{label}当前为空。"
        lines = [
            f"[{short(row.get('added_at') or row.get('created_at'), 19)}] "
            f"{short(row.get('title'), 50) or '(无标题)'}"
            + (
                f" — {short(row.get('author_name'), 24)}"
                if str(row.get("author_name") or "").strip()
                else ""
            )
            + (f" 备注:{short(row.get('note'), 30)}" if str(row.get("note") or "").strip() else "")
            + render_content_reference(row)
            for row in rows
        ]
        return f"{label}（{len(rows)} 条）：\n" + "\n".join(lines)

    return f"未知历史类别: {kind}。"
