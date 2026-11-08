"""Conversational authoring with approval only when a source becomes active."""

from __future__ import annotations

import json
from typing import Any

from openbiliclaw.api.custom_sources import public_draft
from openbiliclaw.sources.workflow_service import (
    WorkflowService,
    discovery_scope_limits,
    workflow_presentation,
)

from .registry import Tool, tool_user_message


def build_custom_source_tools(ctx: Any) -> list[Tool]:
    """Keep browser input and credentials out of the model's tool surface."""

    def service() -> WorkflowService:
        return WorkflowService(ctx.database, config=ctx.config, llm_service=ctx.llm_service)

    async def prepare(args: dict[str, Any]) -> str:
        result = public_draft(
            await service().prepare(
                **args, request_limits=discovery_scope_limits(tool_user_message.get())
            )
        )
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
                "interval_minutes": source["interval_minutes"],
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
        existing_workflow = (
            current.store.get_workflow(str(draft["workflow_id"]))
            if draft.get("workflow_id")
            else None
        )
        if draft.get("published_workflow_id"):
            applied = current.store.get_workflow(draft["published_workflow_id"])
            return (
                f"来源「{source['name'] if source else draft['name']}」的草稿已应用。\n"
                f"流程：{applied['workflow_id']}，当前版本 v{applied['version']}。\n"
                "重复执行保留当前来源和流程，不新增流程或覆盖后续版本。"
            )
        share = source["target_share"] if source else args.get("target_share", 1)
        interval = source["interval_minutes"] if source else args.get("interval_minutes", 30)
        _validate_policy(share=share, interval=interval)
        name = str(source["name"] if source else draft["name"])
        action = "添加发现流程到" if source else "启用发现来源"
        update_note = ""
        if existing_workflow is not None:
            action = "更新发现流程于"
            update_note = (
                f"\n流程：{existing_workflow['workflow_id']}，"
                f"版本 v{existing_workflow['version']} → v{existing_workflow['version'] + 1}"
                f"\n原发现目标：{existing_workflow['intent']}"
                "\n替换该流程并保留已处理内容记录；来源的其他流程不变。"
            )
        state = "（来源当前停用，添加流程后仍保持停用）" if source and not source["enabled"] else ""
        merge_note = (
            "\n同站来源若已存在，将追加流程并保留该来源的比例、间隔和启停状态。"
            if not source and not draft.get("separate_source")
            else ""
        )
        presentation = workflow_presentation(draft)
        kind = {
            "feed": "订阅源（RSS / Atom / JSON Feed）",
            "sitemap": "站点地图",
            "listing": "网页列表",
            "browser": "浏览器网页",
        }.get(presentation["kind"], "网站")
        scope = ""
        if presentation["kind"] == "feed":
            scope = (
                f"\n订阅请求范围：{'、'.join(presentation['request_origins'])}"
                f"\n文章链接范围：{'、'.join(presentation['content_origins'])}"
                "（仅接收订阅中给出的内容与链接，不自动读取文章页）"
            )
        if existing_workflow is not None:
            previous = workflow_presentation(existing_workflow)
            scope += (
                f"\n原请求范围：{'、'.join(previous['request_origins'])}"
                f"\n更新后请求范围：{'、'.join(presentation['request_origins'])}"
                f"\n原内容链接范围：{'、'.join(previous['content_origins'])}"
                f"\n更新后内容链接范围：{'、'.join(presentation['content_origins'])}"
            )
        return (
            f"{action}「{name}」{state}{update_note}\n"
            f"网址：{draft['entry_url']}\n"
            f"类型：{kind}{scope}\n"
            f"发现目标：{draft['intent']}\n"
            f"每轮采集上限：{presentation['max_items']} 条，{presentation['max_pages']} 页\n"
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
            description="根据用户的网站或 RSS/Atom/JSON Feed 网址和自然语言目标建立草稿并试跑；"
            "不会启用来源。用户只要求订阅整个 Feed 时，intent 可为‘跟踪该订阅的新内容’。"
            "intent 必须保留用户的条数、页数及不翻页等限制，不得省略。"
            "网页通过 Ego Lite 采集；自动识别成功的 Feed 使用 HTTP。Ego 不可用时报告原因，"
            "不会替换浏览器。需要登录时把 takeover_url 给用户操作，"
            "绝不索取密码/Cookie。"
            "同站默认一个来源多个流程；separate_source=true 才拆为独立配比来源。"
            "更新既有流程或订阅范围时先 list_sources 获取ID，再传 source_id+workflow_id；"
            "试跑后仍需 activate 审批，只有 source_id 时会追加新流程。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "intent": {"type": "string"},
                    "name": {"type": "string"},
                    "source_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "executor": {"type": "string", "enum": ["auto", "http", "ego"]},
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
                "将试跑成功的草稿启用为 Discover 来源或更新指定既有流程；"
                "调用生成真实审批卡，批准后生效。"
            ),
            impact_hint="启用或更新网站、订阅源流程，按已有 Discover 比例与评估规则补充内容。",
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
            "workflow_id",
            "base_version",
            "status",
            "takeover_url",
            "name",
            "kind",
            "max_items",
            "max_pages",
            "detail",
            "sample_items",
        )
        if key in result
    }
    value["sample_items"] = value.get("sample_items", [])[:2]
    for optional in ("sample_items", "detail", "name", "kind"):
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) <= budget:
            return encoded
        value.pop(optional, None)
    return json.dumps(value, ensure_ascii=False)
