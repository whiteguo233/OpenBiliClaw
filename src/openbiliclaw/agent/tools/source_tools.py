"""Source management tools as JSON Schema tool definitions.

These are the M1-migrated versions of the legacy ``sources/tools.py``
``SOURCE_TOOLS``: same behavior and user-facing result strings, but with
JSON Schema parameters and permission levels for the agent loop.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from .registry import Tool, ToolRegistry

logger = logging.getLogger(__name__)


def build_source_tool_registry(database: Any) -> ToolRegistry:
    """Build a registry with the three source management tools bound to ``database``."""
    return ToolRegistry(
        [
            Tool(
                name="create_source",
                description="创建新的内容源订阅。当用户说想关注某个平台的某类内容时调用。",
                permission_level="hard_write",
                impact_hint="新增一个内容源订阅，会参与后续的内容发现与推荐。",
                parameters={
                    "type": "object",
                    "properties": {
                        "source_type": {
                            "type": "string",
                            "description": "平台类型，如 xiaohongshu / web / v2ex / zhihu",
                        },
                        "name": {
                            "type": "string",
                            "description": "人类可读的订阅名，如 '小红书-机械键盘'",
                        },
                        "strategy": {
                            "type": "string",
                            "enum": ["search", "feed"],
                            "description": "search 或 feed",
                        },
                        "query": {
                            "type": "string",
                            "description": "搜索关键词（strategy=search 时必填）",
                        },
                        "url": {
                            "type": "string",
                            "description": "直接 URL（strategy=feed 时必填）",
                        },
                    },
                },
                handler=lambda args: _create_source(database, args),
            ),
            Tool(
                name="list_sources",
                description="列出用户当前的所有内容源订阅。",
                permission_level="read",
                parameters={"type": "object", "properties": {}},
                handler=lambda args: _list_sources(database, args),
            ),
            Tool(
                name="toggle_source",
                description="启用或禁用某个内容源订阅。",
                permission_level="hard_write",
                impact_hint="改变该订阅的启用状态，会影响后续内容发现与推荐。",
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "订阅 ID"},
                        "enabled": {
                            "type": ["boolean", "string"],
                            "description": "true 或 false",
                        },
                    },
                    "required": ["id"],
                },
                handler=lambda args: _toggle_source(database, args),
            ),
        ]
    )


def _create_source(db: Any, args: dict[str, Any]) -> str:
    source_type = str(args.get("source_type", "web"))
    name = str(args.get("name", ""))
    strategy = str(args.get("strategy", "search"))
    query = str(args.get("query", ""))
    url = str(args.get("url", ""))

    if not name:
        name = f"{source_type}-{query or url or '未命名'}"

    config: dict[str, str] = {}
    if query:
        config["query"] = query
    if url:
        config["url"] = url

    recipe = {
        "id": str(uuid.uuid4()),
        "source_type": source_type,
        "name": name,
        "strategy": strategy,
        "config": config,
        "target_share": 4,
        "enabled": True,
        "created_by": "agent",
        "created_at": datetime.now(UTC).isoformat(),
    }
    db.save_source_recipe(recipe)

    logger.info("Agent created source recipe: %s (%s)", name, recipe["id"])
    return f"已创建内容源订阅「{name}」(类型: {source_type}, 策略: {strategy}, ID: {recipe['id']})"


def _list_sources(db: Any, _args: dict[str, Any]) -> str:
    recipes = db.get_all_recipes()
    if not recipes:
        return "当前没有任何内容源订阅。"

    lines = []
    for r in recipes:
        status = "✅" if r["enabled"] else "⏸️"
        config = json.dumps(r.get("config") or {}, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"{status} {r['name']} ({r['source_type']}/{r['strategy']})"
            f" ID: {r['id']} 配置: {config}"
        )
    return "当前内容源订阅：\n" + "\n".join(lines)


def _toggle_source(db: Any, args: dict[str, Any]) -> str:
    recipe_id = str(args.get("id", ""))
    enabled = args.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.lower() in ("true", "1", "yes")

    if not recipe_id:
        return "缺少订阅 ID。"

    updated = db.update_recipe(recipe_id, enabled=bool(enabled))
    if not updated:
        return f"未找到 ID 为 {recipe_id} 的订阅。"

    action = "启用" if enabled else "禁用"
    return f"已{action}订阅 {recipe_id}。"
