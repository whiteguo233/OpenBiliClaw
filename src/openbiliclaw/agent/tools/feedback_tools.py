"""Recommendation feedback and local save tools (M3, soft_write).

- ``submit_feedback`` mirrors the durable ingest flow of
  ``integrations/openclaw/operations.py::submit_feedback`` and the
  ``POST /api/feedback`` endpoint: build a canonical feedback event, accept it
  through ``EventIngressService`` (idempotent via ``ingest_key``), project it
  onto the recommendation row, then fire the non-blocking cognition hook.
- ``save_item`` mirrors ``operations.py::save_local``: local-only membership
  through ``SavedSyncService.save_local(..., auto_sync=False)`` — nothing is
  written to any platform account (L3 对外动作 stays out of v1).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from .common import maybe_await, require_component, short
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)

_FEEDBACK_TYPE_LABELS = {
    "like": "点赞",
    "dislike": "点踩",
    "dismiss": "屏蔽",
    "comment": "评论",
}
_SAVED_KIND_LABELS = {
    "favorite": "收藏清单",
    "watch_later": "稍后再看",
}


def build_feedback_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the soft-write feedback/save tools bound to ``ctx``."""
    return [
        Tool(
            name="submit_feedback",
            description=(
                "对一条推荐提交反馈：like 点赞 / dislike 点踩 / dismiss 屏蔽 / comment 评论"
                "（comment 必须带 note）。recommendation_id 可通过推荐历史或对话上下文获得。"
                "反馈会进入统一兴趣线，影响后续推荐。"
            ),
            permission_level="soft_write",
            parameters={
                "type": "object",
                "properties": {
                    "recommendation_id": {
                        "type": "integer",
                        "description": "推荐记录 ID",
                    },
                    "feedback_type": {
                        "type": "string",
                        "enum": ["like", "dislike", "dismiss", "comment"],
                        "description": "反馈类型",
                    },
                    "note": {"type": "string", "description": "备注/评论内容，可空"},
                    "request_id": {
                        "type": "string",
                        "description": "幂等键（同一请求重试时去重），可空，缺省自动生成",
                    },
                },
                "required": ["recommendation_id", "feedback_type"],
                "additionalProperties": False,
            },
            handler=lambda args: _submit_feedback(ctx, args),
        ),
        Tool(
            name="save_item",
            description=(
                "把一条内容保存到本地清单：favorite 收藏 / watch_later 稍后再看。"
                "只在本地记录，不同步到 B 站等平台账号。"
            ),
            permission_level="soft_write",
            parameters={
                "type": "object",
                "properties": {
                    "content_id": {
                        "type": "string",
                        "description": "内容 ID（如 BV 号）",
                    },
                    "source_platform": {
                        "type": "string",
                        "description": "来源平台，默认 bilibili",
                    },
                    "list_kind": {
                        "type": "string",
                        "enum": ["favorite", "watch_later"],
                        "description": "目标清单，默认 favorite",
                    },
                    "title": {"type": "string", "description": "标题，可空"},
                    "content_url": {"type": "string", "description": "链接，可空"},
                    "content_type": {
                        "type": "string",
                        "description": "内容形态（video/note/tweet 等），默认 video",
                    },
                    "author_name": {"type": "string", "description": "作者，可空"},
                    "note": {"type": "string", "description": "备注，可空"},
                },
                "required": ["content_id"],
                "additionalProperties": False,
            },
            handler=lambda args: _save_item(ctx, args),
        ),
    ]


async def _submit_feedback(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    db = require_component(ctx.database, "database")
    ingress = require_component(ctx.event_ingress, "event_ingress")

    raw_id = args.get("recommendation_id")
    try:
        recommendation_id = int(raw_id) if raw_id is not None else 0
    except (TypeError, ValueError):
        return f"recommendation_id 无效: {raw_id}。"
    feedback_type = str(args.get("feedback_type") or "").strip().lower()
    note = str(args.get("note") or "").strip()
    request_id = str(args.get("request_id") or "").strip() or f"chat-agent-{uuid.uuid4()}"

    if feedback_type not in _FEEDBACK_TYPE_LABELS:
        return f"不支持的反馈类型: {feedback_type}。"
    if feedback_type == "comment" and not note:
        return "评论反馈必须带 note。"

    recommendation = await maybe_await(db.get_recommendation_by_id(recommendation_id))
    if recommendation is None:
        return f"未找到 ID 为 {recommendation_id} 的推荐记录。"
    title = str(recommendation.get("title") or "")

    from openbiliclaw.sources.event_format import build_event

    event = build_event(
        event_type="feedback",
        source_platform=str(recommendation.get("source_platform") or "bilibili"),
        title=title,
        metadata={
            "recommendation_id": recommendation_id,
            "bvid": recommendation.get("bvid", ""),
            "feedback_type": feedback_type,
            "feedback_note": note,
            "event_namespace": "recommendation",
            "profile_update_owner": "content_feedback",
        },
    )
    event["ingest_key"] = request_id
    receipt = await maybe_await(ingress.accept(event, producer="chat_agent"))
    if receipt.accepted != 1 or receipt.rejected or not receipt.items:
        reason = receipt.items[0].error if receipt.items else "rejected"
        return f"反馈事件被拒绝: {reason}"
    item_receipt = receipt.items[0]

    label = _FEEDBACK_TYPE_LABELS[feedback_type]
    if item_receipt.duplicate:
        return f"该反馈已提交过（幂等去重），未重复记录：「{short(title, 40)}」的{label}。"

    # Durable first write is the commit boundary; the projection is a
    # retryable repair, same shape as the OpenClaw adapter path.
    await maybe_await(
        db.update_recommendation_feedback(
            recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
    )

    hook = getattr(ctx.soul_engine, "record_immediate_feedback_cognition", None)
    if item_receipt.inserted and callable(hook):
        try:
            await maybe_await(hook(feedback_type=feedback_type, title=title, note=note))
        except Exception:
            logger.warning("feedback cognition follow-up deferred", exc_info=True)

    return f"已记录对「{short(title, 40)}」的{label}反馈（事件 #{item_receipt.event_id}）。"


async def _save_item(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    service = require_component(ctx.saved_sync_service, "saved_sync_service")

    content_id = str(args.get("content_id") or "").strip()
    if not content_id:
        return "content_id 不能为空。"
    list_kind = str(args.get("list_kind") or "favorite").strip().lower()
    if list_kind not in _SAVED_KIND_LABELS:
        return f"未知清单类型: {list_kind}（可选: favorite / watch_later）"

    from openbiliclaw.saved_sync.models import SavedItemInput

    item = SavedItemInput(
        source_platform=str(args.get("source_platform") or "bilibili").strip().lower(),
        content_id=content_id,
        content_url=str(args.get("content_url") or ""),
        content_type=str(args.get("content_type") or "video"),
        title=str(args.get("title") or ""),
        author_name=str(args.get("author_name") or ""),
    )
    result = await maybe_await(
        service.save_local(
            list_kind, item, note=str(args.get("note") or "").strip(), auto_sync=False
        )
    )
    label = _SAVED_KIND_LABELS[list_kind]
    display = short(args.get("title") or content_id, 40)
    if getattr(result, "saved", False):
        return f"已保存到{label}：{display}（仅本地记录，未同步平台账号）。"
    return f"保存失败：{display} 未写入{label}。"
