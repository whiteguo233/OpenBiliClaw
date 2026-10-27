"""JSON Schema tool registry for the agent loop.

Tools are defined with MCP-style JSON Schema ``parameters`` and dispatched
in-process. The registry renders OpenAI-compatible function-calling schemas
for providers with native tool support, legacy flat descriptions for the
prompt-simulation fallback, and per-skill whitelisted subsets.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

PermissionLevel = Literal["read", "soft_write", "hard_write"]

# read < soft_write < hard_write, used for permission-gated filtering.
_PERMISSION_ORDER: dict[PermissionLevel, int] = {
    "read": 0,
    "soft_write": 1,
    "hard_write": 2,
}


@dataclass(frozen=True)
class Tool:
    """One callable tool exposed to the agent loop.

    ``parameters`` is a JSON Schema object describing the arguments dict.
    ``handler`` receives the validated arguments and returns a human-readable
    result string; it may be sync or async. ``permission_level`` follows the
    chat-agent-loop design: ``read`` (L0), ``soft_write`` (L1), ``hard_write``
    (L2, approval-gated upstream). ``impact_hint`` is a short user-facing
    note of what a hard_write call will change, surfaced on the approval
    card (M7).
    """

    name: str
    description: str
    handler: Callable[[dict[str, Any]], Any]
    parameters: dict[str, Any] = field(default_factory=dict)
    permission_level: PermissionLevel = "read"
    impact_hint: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool dispatch.

    ``content`` is always a human-readable string suitable for feeding back
    to the LLM, whether the call succeeded or not. ``error`` carries a
    machine-readable failure kind: ``""`` (ok), ``unknown_tool``,
    ``invalid_arguments``, ``handler_error`` or ``async_handler``.
    """

    ok: bool
    content: str
    error: str = ""


def validate_tool_arguments(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Validate ``arguments`` against a (subset of) JSON Schema object schema.

    Supported keywords: ``type`` (string or list of strings), ``properties``,
    ``required``, ``enum``, ``items``, ``additionalProperties: false``.
    Returns a list of human-readable error strings; empty means valid.
    Unknown keywords are ignored on purpose — this is a guard rail for tool
    dispatch, not a full JSON Schema implementation.
    """
    if not schema:
        return [] if isinstance(arguments, dict) else ["参数必须是对象"]
    return _validate_value(schema, arguments, path="参数")


def _validate_value(schema: dict[str, Any], value: Any, *, path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None and not _type_matches(expected, value):
        errors.append(f"{path} 类型应为 {_format_expected_types(expected)}")
        return errors
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        errors.append(f"{path} 取值必须是 {enum} 之一")
    if isinstance(value, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key in schema.get("required") or []:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path} 缺少必需字段 {key}")
            for key, sub_value in value.items():
                sub_schema = properties.get(key)
                if isinstance(sub_schema, dict):
                    errors.extend(_validate_value(sub_schema, sub_value, path=f"{path}.{key}"))
                elif schema.get("additionalProperties") is False:
                    errors.append(f"{path} 包含未定义的字段 {key}")
    if isinstance(value, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_value(items_schema, item, path=f"{path}[{index}]"))
    return errors


def _type_matches(expected: str | list[str], value: Any) -> bool:
    types = [expected] if isinstance(expected, str) else list(expected)
    return any(_single_type_matches(type_name, value) for type_name in types)


def _single_type_matches(type_name: str, value: Any) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "null":
        return value is None
    # Unknown type names are treated as "no constraint".
    return True


def _format_expected_types(expected: str | list[str]) -> str:
    if isinstance(expected, str):
        return expected
    return " 或 ".join(str(item) for item in expected)


class ToolRegistry:
    """Registry of agent tools with schema rendering and dispatch."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """Register a tool. Duplicate names raise ``ValueError``."""
        name = tool.name.strip()
        if not name:
            raise ValueError("Tool name must not be empty.")
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name.strip())

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def subset(self, allowed: Iterable[str]) -> ToolRegistry:
        """Return a registry limited to the given tool names (skill whitelist)."""
        allowed_names = {str(name).strip() for name in allowed if str(name).strip()}
        return ToolRegistry(tool for name, tool in self._tools.items() if name in allowed_names)

    def filter_by_permission(self, max_level: PermissionLevel) -> ToolRegistry:
        """Return a registry with tools at or below ``max_level``."""
        ceiling = _PERMISSION_ORDER[max_level]
        return ToolRegistry(
            tool
            for tool in self._tools.values()
            if _PERMISSION_ORDER[tool.permission_level] <= ceiling
        )

    def llm_schemas(self) -> list[dict[str, Any]]:
        """Render OpenAI-compatible function-calling tool schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                },
            }
            for tool in self._tools.values()
        ]

    def legacy_schemas(self) -> list[dict[str, Any]]:
        """Render the legacy flat shape (``SOURCE_TOOLS``-compatible).

        The legacy prompt-simulation path only consumes ``name`` /
        ``description``; ``parameters`` degrades to ``{name: description}``.
        """
        rendered: list[dict[str, Any]] = []
        for tool in self._tools.values():
            entry: dict[str, Any] = {"name": tool.name, "description": tool.description}
            properties = tool.parameters.get("properties") if tool.parameters else None
            if isinstance(properties, dict) and properties:
                entry["parameters"] = {
                    key: str(sub.get("description") or sub.get("type") or "")
                    for key, sub in properties.items()
                    if isinstance(sub, dict)
                }
            rendered.append(entry)
        return rendered

    async def dispatch(self, name: str, arguments: Any = None) -> ToolResult:
        """Validate and execute a tool call, awaiting async handlers."""
        tool, args, failure = self._prepare(name, arguments)
        if failure is not None:
            return failure
        assert tool is not None
        try:
            value = tool.handler(args)
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            logger.exception("Tool dispatch error: %s", tool.name)
            return ToolResult(ok=False, content=f"工具执行出错: {exc}", error="handler_error")
        return ToolResult(ok=True, content=str(value))

    def dispatch_sync(self, name: str, arguments: Any = None) -> ToolResult:
        """Synchronous dispatch for legacy callers.

        Async handlers cannot be awaited here; they return an
        ``async_handler`` error result instead of blocking the event loop.
        """
        tool, args, failure = self._prepare(name, arguments)
        if failure is not None:
            return failure
        assert tool is not None
        try:
            value = tool.handler(args)
        except Exception as exc:
            logger.exception("Tool dispatch error: %s", tool.name)
            return ToolResult(ok=False, content=f"工具执行出错: {exc}", error="handler_error")
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            return ToolResult(
                ok=False,
                content="工具执行出错: 该工具需要异步调度",
                error="async_handler",
            )
        return ToolResult(ok=True, content=str(value))

    def _prepare(
        self, name: str, arguments: Any
    ) -> tuple[Tool | None, dict[str, Any], ToolResult | None]:
        tool = self._tools.get(str(name).strip())
        if tool is None:
            return None, {}, ToolResult(ok=False, content=f"未知工具: {name}", error="unknown_tool")
        args = arguments if isinstance(arguments, dict) else {}
        errors = validate_tool_arguments(tool.parameters, args)
        if errors:
            return (
                None,
                {},
                ToolResult(
                    ok=False,
                    content="参数校验失败: " + "；".join(errors),
                    error="invalid_arguments",
                ),
            )
        return tool, args, None
