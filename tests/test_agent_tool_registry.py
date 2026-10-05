"""Tests for the JSON Schema agent tool registry (M1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.agent.tools import (
    Tool,
    ToolRegistry,
    build_source_tool_registry,
    validate_tool_arguments,
)
from openbiliclaw.sources.tools import SOURCE_TOOLS, SourceToolDispatcher
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _echo_handler(args: dict[str, Any]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(args.items()))


def _make_tool(name: str = "echo", **overrides: Any) -> Tool:
    kwargs: dict[str, Any] = {
        "name": name,
        "description": f"{name} tool",
        "handler": _echo_handler,
    }
    kwargs.update(overrides)
    return Tool(**kwargs)


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry([_make_tool("alpha"), _make_tool("beta")])
        assert registry.names == ["alpha", "beta"]
        assert registry.get("alpha") is not None
        assert "beta" in registry
        assert len(registry) == 2
        assert registry.get("missing") is None

    def test_duplicate_name_raises(self) -> None:
        registry = ToolRegistry([_make_tool("alpha")])
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_tool("alpha"))

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ToolRegistry([_make_tool("  ")])

    def test_subset_filters_by_whitelist(self) -> None:
        registry = ToolRegistry([_make_tool("alpha"), _make_tool("beta"), _make_tool("gamma")])
        subset = registry.subset(["alpha", "missing", " gamma "])
        assert subset.names == ["alpha", "gamma"]
        # The parent registry is untouched.
        assert len(registry) == 3

    def test_filter_by_permission(self) -> None:
        registry = ToolRegistry(
            [
                _make_tool("reader", permission_level="read"),
                _make_tool("soft", permission_level="soft_write"),
                _make_tool("hard", permission_level="hard_write"),
            ]
        )
        assert registry.filter_by_permission("read").names == ["reader"]
        assert registry.filter_by_permission("soft_write").names == ["reader", "soft"]
        assert registry.filter_by_permission("hard_write").names == ["reader", "soft", "hard"]

    def test_llm_schemas_openai_format(self) -> None:
        registry = ToolRegistry(
            [
                _make_tool(
                    "echo",
                    parameters={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
                _make_tool("ping"),
            ]
        )
        schemas = registry.llm_schemas()
        assert schemas[0] == {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "echo tool",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
        # Schema-less tools still render a valid empty object schema.
        assert schemas[1]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_legacy_schemas_flat_shape(self) -> None:
        registry = ToolRegistry(
            [
                _make_tool(
                    "echo",
                    parameters={
                        "type": "object",
                        "properties": {"text": {"type": "string", "description": "文本"}},
                    },
                ),
                _make_tool("ping"),
            ]
        )
        legacy = registry.legacy_schemas()
        assert legacy[0] == {
            "name": "echo",
            "description": "echo tool",
            "parameters": {"text": "文本"},
        }
        assert legacy[1] == {"name": "ping", "description": "ping tool"}


class TestValidateToolArguments:
    def test_valid_object(self) -> None:
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        assert validate_tool_arguments(schema, {"text": "hi"}) == []

    def test_missing_required(self) -> None:
        schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
        errors = validate_tool_arguments(schema, {})
        assert errors and "id" in errors[0]

    def test_wrong_type(self) -> None:
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        assert validate_tool_arguments(schema, {"count": "three"})
        # bool must not pass as integer (bool is an int subclass).
        assert validate_tool_arguments(schema, {"count": True})
        assert validate_tool_arguments(schema, {"count": 3}) == []

    def test_enum_violation(self) -> None:
        schema = {
            "type": "object",
            "properties": {"strategy": {"type": "string", "enum": ["search", "feed"]}},
        }
        assert validate_tool_arguments(schema, {"strategy": "search"}) == []
        assert validate_tool_arguments(schema, {"strategy": "SEARCH"})

    def test_union_type(self) -> None:
        schema = {"type": "object", "properties": {"enabled": {"type": ["boolean", "string"]}}}
        assert validate_tool_arguments(schema, {"enabled": True}) == []
        assert validate_tool_arguments(schema, {"enabled": "true"}) == []
        assert validate_tool_arguments(schema, {"enabled": 1})

    def test_additional_properties_false(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        assert validate_tool_arguments(schema, {"a": "x"}) == []
        assert validate_tool_arguments(schema, {"b": "x"})

    def test_array_items(self) -> None:
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        }
        assert validate_tool_arguments(schema, {"tags": ["a", "b"]}) == []
        assert validate_tool_arguments(schema, {"tags": ["a", 2]})

    def test_non_dict_arguments(self) -> None:
        schema = {"type": "object", "properties": {}}
        assert validate_tool_arguments(schema, "nope")


class TestToolDispatch:
    async def test_dispatch_sync_handler(self) -> None:
        registry = ToolRegistry([_make_tool("echo")])
        result = await registry.dispatch("echo", {"a": 1})
        assert result.ok
        assert result.content == "a=1"

    async def test_dispatch_async_handler(self) -> None:
        async def async_handler(args: dict[str, Any]) -> str:
            return f"async:{args.get('x', '')}"

        registry = ToolRegistry([_make_tool("async_tool", handler=async_handler)])
        result = await registry.dispatch("async_tool", {"x": "y"})
        assert result.ok
        assert result.content == "async:y"

    async def test_dispatch_unknown_tool(self) -> None:
        registry = ToolRegistry([_make_tool("echo")])
        result = await registry.dispatch("nope", {})
        assert not result.ok
        assert result.error == "unknown_tool"
        assert "未知工具" in result.content

    async def test_dispatch_invalid_arguments(self) -> None:
        registry = ToolRegistry(
            [
                _make_tool(
                    "strict",
                    parameters={"type": "object", "required": ["id"], "properties": {}},
                )
            ]
        )
        result = await registry.dispatch("strict", {})
        assert not result.ok
        assert result.error == "invalid_arguments"
        assert "参数校验失败" in result.content

    async def test_dispatch_handler_exception(self) -> None:
        def boom(_args: dict[str, Any]) -> str:
            raise RuntimeError("kaboom")

        registry = ToolRegistry([_make_tool("boom", handler=boom)])
        result = await registry.dispatch("boom", {})
        assert not result.ok
        assert result.error == "handler_error"
        assert "kaboom" in result.content

    def test_dispatch_sync(self) -> None:
        registry = ToolRegistry([_make_tool("echo")])
        result = registry.dispatch_sync("echo", {"k": "v"})
        assert result.ok
        assert result.content == "k=v"

    def test_dispatch_sync_rejects_async_handler(self) -> None:
        async def async_handler(_args: dict[str, Any]) -> str:
            return "unreachable"

        registry = ToolRegistry([_make_tool("async_tool", handler=async_handler)])
        result = registry.dispatch_sync("async_tool", {})
        assert not result.ok
        assert result.error == "async_handler"


class TestSourceToolsMigration:
    """The legacy ``sources/tools.py`` surface now delegates to the registry."""

    def _make_db(self, tmp_path: Path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    def test_source_tools_legacy_shape_unchanged(self) -> None:
        assert SOURCE_TOOLS == [
            {
                "name": "create_source",
                "description": "创建新的内容源订阅。当用户说想关注某个平台的某类内容时调用。",
                "parameters": {
                    "source_type": "平台类型，如 xiaohongshu / web / v2ex / zhihu",
                    "name": "人类可读的订阅名，如 '小红书-机械键盘'",
                    "strategy": "search 或 feed",
                    "query": "搜索关键词（strategy=search 时必填）",
                    "url": "直接 URL（strategy=feed 时必填）",
                },
            },
            {"name": "list_sources", "description": "列出用户当前的所有内容源订阅。"},
            {
                "name": "toggle_source",
                "description": "启用或禁用某个内容源订阅。",
                "parameters": {"id": "订阅 ID", "enabled": "true 或 false"},
            },
        ]

    def test_registry_llm_schemas(self, tmp_path: Path) -> None:
        registry = build_source_tool_registry(self._make_db(tmp_path))
        schemas = {entry["function"]["name"]: entry for entry in registry.llm_schemas()}
        assert set(schemas) == {"create_source", "list_sources", "toggle_source"}
        create_params = schemas["create_source"]["function"]["parameters"]
        assert create_params["type"] == "object"
        assert create_params["properties"]["strategy"]["enum"] == ["search", "feed"]
        assert schemas["toggle_source"]["function"]["parameters"]["required"] == ["id"]

    def test_permission_levels(self, tmp_path: Path) -> None:
        registry = build_source_tool_registry(self._make_db(tmp_path))
        assert registry.get("list_sources").permission_level == "read"  # type: ignore[union-attr]
        assert registry.get("create_source").permission_level == "hard_write"  # type: ignore[union-attr]
        assert registry.filter_by_permission("read").names == ["list_sources"]

    async def test_registry_dispatch_roundtrip(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        registry = build_source_tool_registry(db)
        created = await registry.dispatch(
            "create_source",
            {
                "source_type": "xiaohongshu",
                "name": "小红书-键盘",
                "strategy": "search",
                "query": "键盘",
            },
        )
        assert created.ok
        assert "小红书-键盘" in created.content
        listed = await registry.dispatch("list_sources")
        assert "小红书-键盘" in listed.content

    def test_dispatcher_delegates(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)
        result = dispatcher.dispatch({"name": "list_sources"})
        assert "没有" in result
        unknown = dispatcher.dispatch({"name": "delete_everything"})
        assert "未知" in unknown

    def test_dispatcher_validation_error(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)
        # strategy must be one of the enum values now.
        result = dispatcher.dispatch(
            {"name": "create_source", "arguments": {"strategy": "bogus", "name": "x"}}
        )
        assert "参数校验失败" in result
        assert db.get_all_recipes() == []
