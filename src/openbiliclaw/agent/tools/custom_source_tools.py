"""Conversational authoring with approval only when a source becomes active."""

from __future__ import annotations

import json
from typing import Any

from openbiliclaw.api.custom_sources import public_draft
from openbiliclaw.sources.workflow_service import WorkflowService

from .registry import Tool


def build_custom_source_tools(ctx: Any) -> list[Tool]:
    """Keep browser input and credentials out of the model's tool surface."""

    def service() -> WorkflowService:
        return WorkflowService(ctx.database, config=ctx.config, llm_service=ctx.llm_service)

    async def prepare(args: dict[str, Any]) -> str:
        result = public_draft(await service().prepare(**args))
        return _bounded_summary(result, ctx.config)

    async def trial(args: dict[str, Any]) -> str:
        result = public_draft(await service().trial(args["draft_id"]))
        return _bounded_summary(result, ctx.config)

    def activate(args: dict[str, Any]) -> str:
        source = service().activate(**args)
        return json.dumps(
            {
                "saved": True,
                "source_id": source["source_id"],
                "name": source["name"],
                "enabled": source["enabled"],
                "target_share": source["target_share"],
                "detail": "来源已保存；后续抓取仍需经过现有 Discover 评估才能入池。",
            },
            ensure_ascii=False,
        )

    def activation_summary(args: dict[str, Any]) -> str:
        current = service()
        draft = current.store.get_draft(args["draft_id"])
        if not draft.get("trial_success") or draft.get("status") != "ready":
            raise ValueError("草稿尚未试跑成功，不能提交启用审批")
        source = current.resolve_draft_source(draft)
        share = source["target_share"] if source else args.get("target_share", 1)
        interval = source["interval_minutes"] if source else args.get("interval_minutes", 30)
        _validate_policy(share=share, interval=interval)
        name = str(source["name"] if source else draft["name"])
        action = "添加发现流程到" if source else "启用发现来源"
        state = "（来源当前停用，添加流程后仍保持停用）" if source and not source["enabled"] else ""
        merge_note = (
            "\n同站来源若已存在，将追加流程并保留该来源的比例、间隔和启停状态。"
            if not source and not draft.get("separate_source")
            else ""
        )
        return (
            f"{action}「{name}」{state}\n"
            f"网址：{draft['entry_url']}\n"
            f"发现目标：{draft['intent']}\n"
            f"推荐比例：{share}；抓取间隔：{interval} 分钟{merge_note}"
        )

    def management_summary(args: dict[str, Any]) -> str:
        source = service().sources.get_source(args["source_id"])
        if source is None or source.get("archived"):
            raise ValueError("来源不存在或已归档，不能提交修改审批")
        _validate_policy(
            share=args.get("target_share", source["target_share"]),
            interval=args.get("interval_minutes", source["interval_minutes"]),
        )
        labels = {"name": "名称", "target_share": "推荐比例", "interval_minutes": "抓取间隔"}
        changes = []
        for field, label in labels.items():
            if field not in args:
                continue
            value = str(args[field]).strip() if field == "name" else args[field]
            if field == "name" and (not value or len(str(value)) > 200):
                raise ValueError("来源名称必须为 1–200 个字符")
            suffix = " 分钟" if field == "interval_minutes" else ""
            changes.append(f"{label}：{source[field]} → {value}{suffix}")
        if "enabled" in args:
            changes.append("状态：" + ("启用" if args["enabled"] else "停用"))
        if not changes:
            raise ValueError("请指定要修改的来源配置")
        return f"修改发现来源「{source['name']}」\n网址：{source['entry_url']}\n" + "；".join(
            changes
        )

    def manage(args: dict[str, Any]) -> str:
        values = dict(args)
        source_id = values.pop("source_id")
        source = service().sources.update_source(source_id, **values)
        return json.dumps(
            {
                "source_id": source_id,
                "name": source["name"],
                "enabled": source["enabled"],
                "target_share": source["target_share"],
                "interval_minutes": source["interval_minutes"],
            },
            ensure_ascii=False,
        )

    return [
        Tool(
            name="prepare_discovery_source",
            permission_level="soft_write",
            handler=prepare,
            description="根据用户网址和自然语言发现目标建立持久草稿并试跑；不会启用来源。"
            "自动选 HTTP 或浏览器。需要登录时把 takeover_url 给用户操作，绝不索取密码/Cookie。"
            "同站默认一个来源多个流程；separate_source=true 才拆为独立配比来源。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "intent": {"type": "string"},
                    "name": {"type": "string"},
                    "source_id": {"type": "string"},
                    "executor": {"type": "string", "enum": ["auto", "http", "playwright", "ego"]},
                    "separate_source": {"type": "boolean"},
                },
                "required": ["url", "intent"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="trial_discovery_source",
            permission_level="soft_write",
            handler=trial,
            description="登录完成或继续未完成的来源草稿时重新试跑。返回 ready 才可提交启用审批。",
            parameters={
                "type": "object",
                "properties": {"draft_id": {"type": "string"}},
                "required": ["draft_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="activate_discovery_source",
            permission_level="hard_write",
            approval_summary=activation_summary,
            handler=activate,
            description=(
                "将试跑成功的草稿启用为独立 Discover 来源；调用生成真实审批卡，批准后生效。"
            ),
            impact_hint="启用网站发现流程，按已有 Discover 比例与评估规则定期补充内容。",
            parameters={
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string"},
                    "target_share": {"type": "integer", "minimum": 1, "maximum": 20},
                    "interval_minutes": {"type": "integer", "minimum": 5, "maximum": 10080},
                },
                "required": ["draft_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="manage_discovery_source",
            permission_level="hard_write",
            approval_summary=management_summary,
            handler=manage,
            description="修改自定义来源名称、独立比例、抓取间隔或启停；需要审批。",
            impact_hint="修改这个来源的 Discover 配置。",
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "name": {"type": "string"},
                    "target_share": {"type": "integer", "minimum": 1, "maximum": 20},
                    "interval_minutes": {"type": "integer", "minimum": 5, "maximum": 10080},
                    "enabled": {"type": "boolean"},
                },
                "required": ["source_id"],
                "additionalProperties": False,
            },
        ),
    ]


def _validate_policy(*, share: Any, interval: Any) -> None:
    if isinstance(share, bool) or not isinstance(share, int) or not 1 <= share <= 20:
        raise ValueError("推荐比例必须为 1–20 的整数")
    if isinstance(interval, bool) or not isinstance(interval, int) or not 5 <= interval <= 10080:
        raise ValueError("抓取间隔必须为 5–10080 分钟")


def _bounded_summary(result: dict[str, Any], config: Any) -> str:
    """Keep resumable IDs and takeover links intact inside the agent result budget."""
    budget = max(
        200, min(3500, int(getattr(getattr(config, "agent", None), "tool_result_max_chars", 4000)))
    )
    value = {
        key: result[key]
        for key in (
            "draft_id",
            "status",
            "takeover_url",
            "name",
            "executor",
            "detail",
            "sample_items",
        )
        if key in result
    }
    value["sample_items"] = value.get("sample_items", [])[:2]
    for optional in ("sample_items", "detail", "name", "executor"):
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) <= budget:
            return encoded
        value.pop(optional, None)
    return json.dumps(value, ensure_ascii=False)
