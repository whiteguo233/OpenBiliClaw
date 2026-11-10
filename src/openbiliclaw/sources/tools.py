"""Source management tools for agent function calling.

Backward-compatible facade over the JSON Schema tool registry
(``openbiliclaw.agent.tools``). ``SOURCE_TOOLS`` keeps the legacy flat shape
consumed by the prompt-simulation path; ``SourceToolDispatcher`` delegates to
a registry built from the same handlers. Public-Web writes require the agent
approval UI and are refused on this synchronous legacy surface.
"""

from __future__ import annotations

from typing import Any

from openbiliclaw.agent.tools.source_tools import build_source_tool_registry

# ── Tool definitions (for LLM, legacy flat shape) ───────────────────
#
# Derived from the registry's ``legacy_schemas()`` so the JSON Schema
# definitions in ``agent/tools/source_tools.py`` remain the single source of
# truth. ``api/runtime_context.py`` injects this list into SocraticDialogue.

_LEGACY_TOOL_NAMES = ("create_source", "list_sources", "toggle_source")
SOURCE_TOOLS: list[dict[str, Any]] = (
    build_source_tool_registry(database=None).subset(_LEGACY_TOOL_NAMES).legacy_schemas()
)
for _tool in SOURCE_TOOLS:
    if _tool["name"] == "create_source":
        _tool["description"] = "创建平台内容订阅；公开 Web 订阅请使用支持确认卡的新版聊一聊。"

_WEB_APPROVAL_REQUIRED = (
    "此聊天入口不支持 Web 订阅确认卡。请在新版聊一聊中添加或停用网站，"
    "也可使用设置中的公开 Web 页面。"
)


# ── Tool dispatcher ─────────────────────────────────────────────────


class SourceToolDispatcher:
    """Executes source management tool calls against the database."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._registry = build_source_tool_registry(database).subset(_LEGACY_TOOL_NAMES)

    def dispatch(self, tool_call: dict[str, Any]) -> str:
        """Execute a tool call and return a human-readable result string.

        Args:
            tool_call: Dict with ``name`` and optional ``arguments`` keys.

        Returns:
            Result message suitable for feeding back to the LLM.
        """
        name = str(tool_call.get("name", ""))
        args = tool_call.get("arguments", {})
        if isinstance(args, dict):
            if (
                name == "create_source"
                and str(args.get("source_type", "web")).strip().lower() == "web"
            ):
                return _WEB_APPROVAL_REQUIRED
            if name == "toggle_source":
                recipe_id = str(args.get("id", ""))
                if any(
                    row["id"] == recipe_id and row["source_type"] == "web"
                    for row in self._database.get_all_recipes()
                ):
                    return _WEB_APPROVAL_REQUIRED
        result = self._registry.dispatch_sync(
            str(tool_call.get("name", "")),
            tool_call.get("arguments", {}),
        )
        return result.content
