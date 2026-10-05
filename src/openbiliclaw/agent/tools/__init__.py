"""Agent tool registry package (M1: JSON Schema tools + dispatch; M3: v1 tool set)."""

from .common import ToolComponentUnavailableError
from .context import AgentToolContext, build_agent_tool_registry
from .registry import PermissionLevel, Tool, ToolRegistry, ToolResult, validate_tool_arguments
from .skill_tools import SUGGEST_SKILL_TOOL_NAME, build_suggest_skill_tool
from .source_tools import build_source_tool_registry

__all__ = [
    "SUGGEST_SKILL_TOOL_NAME",
    "AgentToolContext",
    "PermissionLevel",
    "Tool",
    "ToolComponentUnavailableError",
    "ToolRegistry",
    "ToolResult",
    "build_agent_tool_registry",
    "build_source_tool_registry",
    "build_suggest_skill_tool",
    "validate_tool_arguments",
]
