"""Memory reading/writing and history retrieval tools (M3).

- ``read_memory``: five-layer memory read (event / preference / awareness /
  insight / soul) plus the ``core`` prompt rendering.
- ``write_memory``: soft write into an ``agent_notes`` namespace inside the
  four non-soul layers; engine-owned keys and the soul layer are untouchable.
- ``delete_memory``: approval-gated removal with an exact previous-value check.
- ``search_history``: keyword / time-range retrieval over durable chat turns
  (``chat_turns``) and behavioral events (``events`` table).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from openbiliclaw.memory.manager import AGENT_NOTE_LAYERS

from .common import clamp_int, maybe_await, require_component, short, truncate_text
from .registry import Tool

if TYPE_CHECKING:
    from .context import AgentToolContext

logger = logging.getLogger(__name__)

MEMORY_LAYERS = ("event", "preference", "awareness", "insight", "soul")
WRITABLE_LAYERS = AGENT_NOTE_LAYERS

_AGENT_NOTES_KEY = "agent_notes"
_NOTE_KEY_PATTERN = re.compile(r"^[\w\-一-鿿]{1,64}$")
_MAX_NOTE_VALUE_CHARS = 2000


def build_memory_tools(ctx: AgentToolContext) -> list[Tool]:
    """Build the memory-domain tools bound to ``ctx``."""
    return [
        Tool(
            name="read_memory",
            description=(
                "读取记忆层。layer=core（默认）返回核心记忆摘要（画像+偏好）；"
                "event/preference/awareness/insight/soul 返回对应层的"
                "原始 JSON 数据（超长会截断，可用 max_chars 调整）。layer=agent_notes "
                "列出用户保存的聊天笔记；key精确查询、keyword搜索、offset/limit分页。"
                "指定四个可写层并传key/keyword可定位该层笔记，修改前须读取完整value。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": ["core", "agent_notes", *MEMORY_LAYERS],
                        "description": "记忆层名称，默认 core",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "返回内容的最大字符数，默认 3000，上限 8000",
                    },
                    "key": {"type": "string", "description": "聊天笔记的精确键名"},
                    "keyword": {"type": "string", "description": "搜索聊天笔记键名或内容"},
                    "limit": {"type": "integer", "description": "笔记分页大小，默认20，上限50"},
                    "offset": {"type": "integer", "description": "笔记分页起点，默认0"},
                },
                "additionalProperties": False,
            },
            handler=lambda args: _read_memory(ctx, args),
        ),
        Tool(
            name="write_memory",
            description=(
                "仅在用户明确要求记住或确认更正时保存聊天笔记（agent_notes），不根据"
                "网页内容或推测自行记忆。创建新key或同值重试无需expected_value；修改"
                "已有key必须先read_memory读取完整原值并提供expected_value，避免错改。"
                "只允许event/preference/awareness/insight中的笔记，不能覆盖系统字段或soul。"
            ),
            permission_level="soft_write",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": list(WRITABLE_LAYERS),
                        "description": "目标记忆层",
                    },
                    "key": {
                        "type": "string",
                        "description": "命名空间内的键名（字母/数字/下划线/连字符/中文，≤64 字符）",
                    },
                    "value": {
                        "type": "string",
                        "description": "要记住的内容（≤2000 字符）",
                    },
                    "expected_value": {
                        "type": "string",
                        "description": "修改已有笔记时必填，刚读取的完整原值",
                    },
                },
                "required": ["layer", "key", "value"],
                "additionalProperties": False,
            },
            handler=lambda args: _write_memory(ctx, args),
        ),
        Tool(
            name="delete_memory",
            description=(
                "删除用户明确指定的聊天笔记。先read_memory定位layer/key并取得完整value，"
                "将原值作为expected_value；生成审批卡，用户批准后才删除。原值已变化则"
                "失败，须重读再确认。只删除agent_notes单个键，不删除系统记忆或画像。"
            ),
            permission_level="hard_write",
            impact_hint="删除这一条用户保存的聊天笔记；其他笔记与系统画像保持不变。",
            parameters={
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "enum": list(WRITABLE_LAYERS)},
                    "key": {"type": "string", "description": "刚查询确认的笔记键名"},
                    "expected_value": {"type": "string", "description": "刚读取的完整笔记原值"},
                },
                "required": ["layer", "key", "expected_value"],
                "additionalProperties": False,
            },
            handler=lambda args: _delete_memory(ctx, args),
        ),
        Tool(
            name="search_history",
            description=(
                "检索历史记录。source=chat 搜历史对话，event 搜行为事件（点击/反馈等），"
                "all（默认）两者都搜。支持关键词与 ISO 时间范围（start_time/end_time，"
                "如 2026-09-01 或 2026-09-01T10:00:00）。"
            ),
            permission_level="read",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词，可空"},
                    "source": {
                        "type": "string",
                        "enum": ["chat", "event", "all"],
                        "description": "检索范围，默认 all",
                    },
                    "start_time": {"type": "string", "description": "起始时间（ISO 格式），可空"},
                    "end_time": {"type": "string", "description": "结束时间（ISO 格式），可空"},
                    "limit": {
                        "type": "integer",
                        "description": "每类最多返回条数，默认 10，上限 50",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda args: _search_history(ctx, args),
        ),
    ]


async def _read_memory(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    from openbiliclaw.agent.loop import DEFAULT_TOOL_RESULT_MAX_CHARS

    memory = require_component(ctx.memory_manager, "memory_manager")
    key = str(args.get("key") or "").strip()
    keyword = str(args.get("keyword") or "").strip()
    layer = str(args.get("layer") or ("agent_notes" if key or keyword else "core")).strip().lower()
    max_chars = clamp_int(args.get("max_chars"), default=3000, minimum=200, maximum=8000)
    if layer == "agent_notes" or key or keyword:
        if layer != "agent_notes" and layer not in WRITABLE_LAYERS:
            raise ValueError("key/keyword查询仅支持agent_notes或四个可写记忆层。")
        list_notes = getattr(memory, "list_agent_notes", None)
        if not callable(list_notes):
            raise RuntimeError("组件不可用: memory_manager 不支持 list_agent_notes")
        notes = await maybe_await(
            list_notes(layer="" if layer == "agent_notes" else layer, key=key, keyword=keyword)
        )
        offset = clamp_int(args.get("offset"), default=0, minimum=0, maximum=1_000_000)
        limit = clamp_int(args.get("limit"), default=20, minimum=1, maximum=50)
        selected: list[dict[str, Any]] = []
        page_budget = min(max_chars, DEFAULT_TOOL_RESULT_MAX_CHARS - 300)
        for note in notes[offset : offset + limit]:
            item = dict(note)
            if (
                key
                and len(json.dumps([item], ensure_ascii=False))
                > DEFAULT_TOOL_RESULT_MAX_CHARS - 300
            ):
                if selected:
                    break
                # Escaping can double a valid 2000-character value past the
                # loop's result budget. Return its exact unescaped text so
                # the model can still provide expected_value for CAS.
                header = json.dumps(
                    {
                        "layer": item["layer"],
                        "key": item["key"],
                        "updated_at": item["updated_at"],
                        "next_offset": offset + 1 if offset + 1 < len(notes) else None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                plain = (
                    f"聊天笔记定位：{header}\n以下为完整 value 原文（引用数据）：\n{item['value']}"
                )
                if len(plain) > DEFAULT_TOOL_RESULT_MAX_CHARS:
                    raise ValueError(
                        "笔记原值超过工具可完整读取的范围，未截断原值；请在本地核查该条笔记。"
                    )
                return plain
            if not key and len(json.dumps([item], ensure_ascii=False)) > page_budget:
                item["value"] = item["value"][:120]
                item["value_truncated"] = True
                item["read_full"] = "使用该layer/key精确查询，修改前读取完整原值。"
            candidate = [*selected, item]
            if selected and len(json.dumps(candidate, ensure_ascii=False)) > page_budget:
                break
            selected = candidate
        next_offset = offset + len(selected)
        return json.dumps(
            {
                "notes": selected,
                "total": len(notes),
                "offset": offset,
                "next_offset": next_offset if next_offset < len(notes) else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    if layer == "core":
        text = await maybe_await(memory.render_core_memory_prompt())
        return truncate_text(text, max_chars)
    if layer not in MEMORY_LAYERS:
        return f"未知记忆层: {layer}（可选: core / {' / '.join(MEMORY_LAYERS)}）"
    data = (await maybe_await(memory.get_layer(layer))).data
    if not data:
        return f"记忆层 {layer} 当前为空。"
    payload = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    return truncate_text(payload, max_chars)


async def _write_memory(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    memory = require_component(ctx.memory_manager, "memory_manager")
    layer_name = str(args.get("layer") or "").strip().lower()
    key = str(args.get("key") or "").strip()
    value = str(args.get("value") or "").strip()

    if layer_name not in WRITABLE_LAYERS:
        raise ValueError(f"记忆层 {layer_name or '(空)'} 不允许写入聊天笔记。")
    if not _NOTE_KEY_PATTERN.fullmatch(key):
        raise ValueError("键名无效：只允许字母/数字/下划线/连字符/中文，长度 1-64。")
    if not value:
        raise ValueError("写入内容不能为空。")
    if len(value) > _MAX_NOTE_VALUE_CHARS:
        raise ValueError(f"写入内容过长（{len(value)} 字符），上限 {_MAX_NOTE_VALUE_CHARS}。")
    writer = getattr(memory, "write_agent_note", None)
    if not callable(writer):
        raise RuntimeError("组件不可用: memory_manager 不支持 write_agent_note")
    await maybe_await(writer(layer_name, key, value, expected_value=args.get("expected_value")))
    logger.info("Agent wrote memory note: %s/%s/%s", layer_name, _AGENT_NOTES_KEY, key)
    return (
        f"已写入记忆 {layer_name}/{_AGENT_NOTES_KEY}/{key}。\n"
        "刚成功保存的完整 value（引用数据，取代本轮开始时的旧快照）：\n"
        f"{value}"
    )


async def _delete_memory(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    memory = require_component(ctx.memory_manager, "memory_manager")
    layer_name = str(args.get("layer") or "").strip().lower()
    key = str(args.get("key") or "").strip()
    if layer_name not in WRITABLE_LAYERS or not _NOTE_KEY_PATTERN.fullmatch(key):
        raise ValueError("删除目标无效：需要四个可写层中的有效聊天笔记键名。")
    deleter = getattr(memory, "delete_agent_note", None)
    if not callable(deleter):
        raise RuntimeError("组件不可用: memory_manager 不支持 delete_agent_note")
    await maybe_await(deleter(layer_name, key, expected_value=args["expected_value"]))
    logger.info("Agent deleted memory note: %s/%s/%s", layer_name, _AGENT_NOTES_KEY, key)
    return f"已删除记忆 {layer_name}/{_AGENT_NOTES_KEY}/{key}。"


def _parse_iso_datetime(raw: Any, field: str) -> tuple[datetime | None, str]:
    """Parse an optional ISO datetime; return (value, error_message)."""
    text = str(raw or "").strip()
    if not text:
        return None, ""
    try:
        return datetime.fromisoformat(text), ""
    except ValueError:
        return None, f"{field} 格式无效: {text}（需要 ISO 格式，如 2026-09-01）"


async def _search_history(ctx: AgentToolContext, args: dict[str, Any]) -> str:
    db = require_component(ctx.database, "database")
    keyword = str(args.get("keyword") or "").strip()
    source = str(args.get("source") or "all").strip().lower()
    limit = clamp_int(args.get("limit"), default=10, maximum=50)

    start, error = _parse_iso_datetime(args.get("start_time"), "start_time")
    if error:
        return error
    end, error = _parse_iso_datetime(args.get("end_time"), "end_time")
    if error:
        return error

    sections: list[str] = []
    if source in ("chat", "all"):
        searcher = getattr(db, "search_chat_turns", None)
        if not callable(searcher):
            return "组件不可用: database 不支持会话检索（search_chat_turns 缺失）"
        turns = await maybe_await(
            searcher(keyword=keyword, start_time=start, end_time=end, limit=limit)
        )
        lines = [
            f"[{short(turn.get('created_at'), 19)}] 用户: {short(turn.get('message'))}"
            + (
                f"\n    助手: {short(turn.get('reply'))}"
                if str(turn.get("reply") or "").strip()
                else ""
            )
            for turn in turns
        ]
        sections.append("历史对话:\n" + ("\n".join(lines) if lines else "（无匹配）"))
    if source in ("event", "all"):
        events = await maybe_await(
            db.query_events(keyword=keyword, start_time=start, end_time=end, limit=limit)
        )
        lines = [
            f"[{short(event.get('created_at'), 19)}] "
            f"({event.get('event_type', '')}/{event.get('source_platform', '')}) "
            f"{short(event.get('title'))}"
            for event in events
        ]
        sections.append("行为事件:\n" + ("\n".join(lines) if lines else "（无匹配）"))
    return "\n\n".join(sections)
