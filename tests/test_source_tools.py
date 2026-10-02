"""Tests for source management tools and dialogue tool calling (Phase 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from openbiliclaw.agent.tools.source_tools import build_source_tool_registry
from openbiliclaw.sources.tools import SOURCE_TOOLS, SourceToolDispatcher
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class TestSourceToolDispatcher:
    """Unit tests for the tool dispatcher."""

    def _make_db(self, tmp_path: Path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    def test_create_source(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch(
            {
                "name": "create_source",
                "arguments": {
                    "source_type": "xiaohongshu",
                    "name": "小红书-机械键盘",
                    "strategy": "search",
                    "query": "机械键盘",
                },
            }
        )

        assert "小红书-机械键盘" in result
        recipes = db.get_all_recipes()
        assert len(recipes) == 1
        assert recipes[0]["source_type"] == "xiaohongshu"
        assert recipes[0]["config"]["query"] == "机械键盘"
        assert recipes[0]["created_by"] == "agent"

    @pytest.mark.parametrize("source_type", ["web", " WEB "])
    def test_legacy_dialogue_refuses_web_writes(self, tmp_path: Path, source_type: str) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)
        result = dispatcher.dispatch(
            {
                "name": "create_source",
                "arguments": {
                    "source_type": source_type,
                    "name": "Example",
                    "strategy": "feed",
                    "url": "https://news.example.com/feed.xml",
                },
            }
        )
        assert "确认卡" in result
        assert db.get_all_recipes() == []

    def test_legacy_dialogue_refuses_web_toggle_and_does_not_offer_async_probe(
        self, tmp_path: Path
    ) -> None:
        db = self._make_db(tmp_path)
        build_source_tool_registry(db).dispatch_sync(
            "create_source",
            {
                "source_type": "web",
                "url": "https://news.example.com/feed.xml",
                "strategy": "feed",
            },
        )
        recipe = db.get_all_recipes()[0]
        result = SourceToolDispatcher(db).dispatch(
            {
                "name": "toggle_source",
                "arguments": {
                    "id": recipe["id"],
                    "enabled": False,
                },
            }
        )
        assert "确认卡" in result
        assert db.get_all_recipes()[0]["enabled"]
        assert "probe_web_source" not in {tool["name"] for tool in SOURCE_TOOLS}

    def test_list_sources_empty(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch({"name": "list_sources"})
        assert "没有" in result

    def test_list_sources_with_recipes(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "bilibili",
                "name": "B站搜索",
                "strategy": "search",
            }
        )
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch({"name": "list_sources"})
        assert "B站搜索" in result

    def test_toggle_source(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.save_source_recipe(
            {
                "id": "r1",
                "source_type": "xiaohongshu",
                "name": "测试",
                "strategy": "feed",
            }
        )
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch(
            {
                "name": "toggle_source",
                "arguments": {"id": "r1", "enabled": False},
            }
        )
        assert "禁用" in result
        assert db.get_enabled_recipes() == []

    def test_toggle_nonexistent(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch(
            {
                "name": "toggle_source",
                "arguments": {"id": "nope", "enabled": True},
            }
        )
        assert "未找到" in result

    def test_unknown_tool(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        dispatcher = SourceToolDispatcher(db)

        result = dispatcher.dispatch({"name": "delete_everything"})
        assert "未知" in result


class TestSourceToolDefinitions:
    """Verify tool schema definitions are well-formed."""

    def test_all_tools_have_name(self) -> None:
        for tool in SOURCE_TOOLS:
            assert "name" in tool
            assert isinstance(tool["name"], str)

    def test_all_tools_have_description(self) -> None:
        for tool in SOURCE_TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 0

    def test_tool_names(self) -> None:
        names = [t["name"] for t in SOURCE_TOOLS]
        assert "create_source" in names
        assert "list_sources" in names
        assert "toggle_source" in names


class TestDialogueToolCalling:
    """Integration test: dialogue with tool dispatcher."""

    def test_dialogue_init_with_tools(self) -> None:
        """Dialogue can be constructed with tools and dispatcher."""
        from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

        class FakeSoulEngine:
            pass

        dialogue = SocraticDialogue(
            llm=None,
            soul_engine=FakeSoulEngine(),
            tools=SOURCE_TOOLS,
            tool_dispatcher=object(),
            learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
        )
        assert dialogue._tools == SOURCE_TOOLS
        assert dialogue._tool_dispatcher is not None

    def test_dialogue_init_without_tools(self) -> None:
        """Dialogue works without tools (backward compatible)."""
        from openbiliclaw.soul.dialogue import DialogueLearningMode, SocraticDialogue

        class FakeSoulEngine:
            pass

        dialogue = SocraticDialogue(
            llm=None,
            soul_engine=FakeSoulEngine(),
            learning_mode=DialogueLearningMode.REPLY_ONLY_TEST,
        )
        assert dialogue._tools == []
        assert dialogue._tool_dispatcher is None
