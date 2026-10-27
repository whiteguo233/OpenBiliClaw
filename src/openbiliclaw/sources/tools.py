"""Source management tools for agent function calling.

Backward-compatible facade over the JSON Schema tool registry
(``openbiliclaw.agent.tools``). ``SOURCE_TOOLS`` keeps the legacy flat shape
consumed by the prompt-simulation path; ``SourceToolDispatcher`` delegates to
a registry built from the same handlers, so both entry points share behavior.
"""

from __future__ import annotations

from typing import Any

from openbiliclaw.agent.tools.source_tools import build_source_tool_registry

# ── Tool definitions (for LLM, legacy flat shape) ───────────────────
#
# Derived from the registry's ``legacy_schemas()`` so the JSON Schema
# definitions in ``agent/tools/source_tools.py`` remain the single source of
# truth. ``api/runtime_context.py`` injects this list into SocraticDialogue.

SOURCE_TOOLS: list[dict[str, Any]] = build_source_tool_registry(database=None).legacy_schemas()


# ── Tool dispatcher ─────────────────────────────────────────────────


class SourceToolDispatcher:
    """Executes source management tool calls against the database."""

    def __init__(self, database: Any) -> None:
        self._registry = build_source_tool_registry(database)

    def dispatch(self, tool_call: dict[str, Any]) -> str:
        """Execute a tool call and return a human-readable result string.

        Args:
            tool_call: Dict with ``name`` and optional ``arguments`` keys.

        Returns:
            Result message suitable for feeding back to the LLM.
        """
        result = self._registry.dispatch_sync(
            str(tool_call.get("name", "")),
            tool_call.get("arguments", {}),
        )
        return result.content
