"""Aggregate context and registry assembly for the M3 standard tool set.

``AgentToolContext`` is a lightweight handle over the runtime components the
v1 tools need (database, soul engine, memory manager, …). It deliberately
mirrors the field names of ``api/runtime_context.py`` so production wiring in
a later milestone is a plain attribute pass-through — this module never
imports or mutates the runtime context itself.

Every field is optional: ``build_agent_tool_registry`` always registers the
full v1 tool list, and handlers degrade individually to a machine-readable
``handler_error`` when their component is missing (see ``common.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bilibili_tools import build_bilibili_tools
from .config_tools import build_config_tools
from .feedback_tools import build_feedback_tools
from .memory_tools import build_memory_tools
from .profile_tools import build_profile_tools
from .recommendation_tools import build_recommendation_tools
from .registry import ToolRegistry
from .source_tools import build_source_tool_registry
from .web_reading import build_web_reading_tools
from .web_search_tools import build_web_search_tools


@dataclass
class AgentToolContext:
    """Runtime component references available to the chat-agent tools.

    ``config_update_hook(key, value)`` is the production M7 seam: the API
    applies one field through its settings transaction and awaits the outcome.
    ``config_persist_hook`` / ``config_reload_hook`` remain compatibility
    seams used when the unified hook is absent: the persist
    hook saves the mutated ``Config`` (returns the saved path), the reload
    hook triggers the runtime hot-reload (may return an awaitable). Both
    are optional; without them the tool refuses to write.
    """

    database: Any = None
    soul_engine: Any = None
    memory_manager: Any = None
    recommendation_engine: Any = None
    config: Any = None
    event_ingress: Any = None
    saved_sync_service: Any = None
    config_persist_hook: Any = None
    config_reload_hook: Any = None
    config_update_hook: Any = None


def build_agent_tool_registry(ctx: AgentToolContext) -> ToolRegistry:
    """Assemble the full v1 standard tool set bound to ``ctx``.

    Source tools (create/list/toggle_source) are only registered when a
    database is wired — their handlers are bound at construction time. All
    other tools are always registered and degrade per-call.
    """
    registry = ToolRegistry()
    if ctx.database is not None:
        source_registry = build_source_tool_registry(ctx.database)
        for name in source_registry.names:
            tool = source_registry.get(name)
            if tool is not None:
                registry.register(tool)
    for builder in (
        build_profile_tools,
        build_memory_tools,
        build_recommendation_tools,
        build_bilibili_tools,
        build_feedback_tools,
        build_config_tools,
        build_web_search_tools,
        build_web_reading_tools,
    ):
        for tool in builder(ctx):
            registry.register(tool)
    return registry
