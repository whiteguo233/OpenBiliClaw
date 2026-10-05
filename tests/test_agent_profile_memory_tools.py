"""Tests for the M3 profile and memory tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
from openbiliclaw.agent.tools.memory_tools import build_memory_tools
from openbiliclaw.agent.tools.profile_tools import build_profile_tools
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.soul.engine import SoulProfileNotInitializedError
from openbiliclaw.soul.profile import OnionProfile
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _registry(*builders: Any, ctx: AgentToolContext) -> ToolRegistry:
    registry = ToolRegistry()
    for builder in builders:
        for tool in builder(ctx):
            registry.register(tool)
    return registry


class _FakeSoulEngine:
    def __init__(self, profile: Any = None, error: Exception | None = None) -> None:
        self._profile = profile
        self._error = error

    def get_profile(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._profile


class _FakeLayer:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}
        self.saved = False

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def update(self, key: str, value: Any) -> None:
        self._data[key] = value

    def save(self) -> None:
        self.saved = True


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.layers = {
            name: _FakeLayer() for name in ("event", "preference", "awareness", "insight", "soul")
        }

    def get_layer(self, name: str) -> _FakeLayer:
        return self.layers[name]

    def render_core_memory_prompt(self) -> str:
        return "核心记忆摘要"


class TestGetProfile:
    async def test_happy_path(self) -> None:
        profile = OnionProfile(personality_portrait="测试画像：好奇的探索者")
        ctx = AgentToolContext(soul_engine=_FakeSoulEngine(profile=profile))
        result = await _registry(build_profile_tools, ctx=ctx).dispatch("get_profile", {})
        assert result.ok
        assert "测试画像" in result.content

    async def test_profile_not_initialized(self) -> None:
        ctx = AgentToolContext(
            soul_engine=_FakeSoulEngine(error=SoulProfileNotInitializedError("nope"))
        )
        result = await _registry(build_profile_tools, ctx=ctx).dispatch("get_profile", {})
        assert result.ok
        assert "尚未初始化" in result.content

    async def test_missing_component(self) -> None:
        result = await _registry(build_profile_tools, ctx=AgentToolContext()).dispatch(
            "get_profile", {}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert "组件不可用" in result.content


class TestReadMemory:
    async def test_core_layer(self) -> None:
        ctx = AgentToolContext(memory_manager=_FakeMemoryManager())
        result = await _registry(build_memory_tools, ctx=ctx).dispatch("read_memory", {})
        assert result.ok
        assert "核心记忆摘要" in result.content

    async def test_raw_layer_json(self) -> None:
        memory = _FakeMemoryManager()
        memory.layers["preference"].update("interests", [{"name": "机械键盘"}])
        ctx = AgentToolContext(memory_manager=memory)
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "read_memory", {"layer": "preference"}
        )
        assert result.ok
        assert "机械键盘" in result.content

    async def test_invalid_layer_rejected_by_schema(self) -> None:
        ctx = AgentToolContext(memory_manager=_FakeMemoryManager())
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "read_memory", {"layer": "bogus"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        result = await _registry(build_memory_tools, ctx=AgentToolContext()).dispatch(
            "read_memory", {}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert "组件不可用" in result.content


class TestWriteMemory:
    async def test_happy_path_namespaced_write(self, tmp_path: Path) -> None:
        memory = MemoryManager(tmp_path)
        memory.initialize()
        ctx = AgentToolContext(memory_manager=memory)
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "write_memory",
            {"layer": "insight", "key": "chat_observation", "value": "用户最近对露营感兴趣"},
        )
        assert result.ok
        notes = memory.get_layer("insight").data["agent_notes"]
        assert notes["chat_observation"]["value"] == "用户最近对露营感兴趣"
        assert memory.get_layer("insight").storage_path.exists()

    async def test_soul_layer_rejected_by_schema(self) -> None:
        memory = _FakeMemoryManager()
        ctx = AgentToolContext(memory_manager=memory)
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "write_memory", {"layer": "soul", "key": "core", "value": "x"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"
        assert memory.layers["soul"].data == {}

    async def test_missing_required_fields(self) -> None:
        ctx = AgentToolContext(memory_manager=_FakeMemoryManager())
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "write_memory", {"layer": "event"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_invalid_key_and_long_value(self) -> None:
        memory = _FakeMemoryManager()
        ctx = AgentToolContext(memory_manager=memory)
        registry = _registry(build_memory_tools, ctx=ctx)
        bad_key = await registry.dispatch(
            "write_memory", {"layer": "event", "key": "bad key!", "value": "x"}
        )
        assert not bad_key.ok
        assert "键名无效" in bad_key.content
        long_value = await registry.dispatch(
            "write_memory", {"layer": "event", "key": "ok", "value": "x" * 2001}
        )
        assert "过长" in long_value.content
        assert memory.layers["event"].data == {}

    async def test_missing_component(self) -> None:
        result = await _registry(build_memory_tools, ctx=AgentToolContext()).dispatch(
            "write_memory", {"layer": "event", "key": "k", "value": "v"}
        )
        assert not result.ok
        assert result.error == "handler_error"


class TestSearchHistory:
    def _make_db(self, tmp_path: Path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    async def test_search_chat_and_events(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.create_chat_turn(turn_id="t1", message="给我推荐机械键盘视频")
        db.complete_chat_turn("t1", reply="好的，这是一批键盘推荐")
        db.insert_event("click", title="机械键盘组装教程", source_platform="bilibili")
        ctx = AgentToolContext(database=db)
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "search_history", {"keyword": "键盘"}
        )
        assert result.ok
        assert "历史对话" in result.content
        assert "机械键盘视频" in result.content
        assert "行为事件" in result.content
        assert "机械键盘组装教程" in result.content

    async def test_chat_only_scope_and_no_match(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.create_chat_turn(turn_id="t1", message="今天天气怎么样")
        ctx = AgentToolContext(database=db)
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "search_history", {"keyword": "不存在的关键词", "source": "chat"}
        )
        assert result.ok
        assert "无匹配" in result.content
        assert "行为事件" not in result.content

    async def test_invalid_time_range(self, tmp_path: Path) -> None:
        ctx = AgentToolContext(database=self._make_db(tmp_path))
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "search_history", {"start_time": "昨天"}
        )
        assert result.ok
        assert "格式无效" in result.content

    async def test_invalid_source_rejected_by_schema(self, tmp_path: Path) -> None:
        ctx = AgentToolContext(database=self._make_db(tmp_path))
        result = await _registry(build_memory_tools, ctx=ctx).dispatch(
            "search_history", {"source": "bogus"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        result = await _registry(build_memory_tools, ctx=AgentToolContext()).dispatch(
            "search_history", {}
        )
        assert not result.ok
        assert result.error == "handler_error"
