"""Tests for the M3 AgentToolContext registry assembly."""

from __future__ import annotations

from openbiliclaw.agent.tools import AgentToolContext, build_agent_tool_registry
from openbiliclaw.storage.database import Database


class TestBuildAgentToolRegistry:
    def _make_db(self, tmp_path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    def test_full_assembly_registers_v1_tool_set(self, tmp_path) -> None:
        registry = build_agent_tool_registry(AgentToolContext(database=self._make_db(tmp_path)))
        expected = {
            # read
            "get_profile",
            "read_memory",
            "search_web",
            "read_webpage",
            "search_history",
            "get_recommendations",
            "get_watch_history",
            "query_discovery_pool",
            "get_config",
            "list_sources",
            # soft_write
            "write_memory",
            "submit_feedback",
            "save_item",
            # hard_write
            "create_source",
            "toggle_source",
            "update_config",
            "delete_memory",
        }
        assert expected.issubset(set(registry.names))

    def test_permission_levels(self, tmp_path) -> None:
        registry = build_agent_tool_registry(AgentToolContext(database=self._make_db(tmp_path)))
        read_only = registry.filter_by_permission("read")
        assert set(read_only.names) == {
            "get_profile",
            "read_memory",
            "search_web",
            "read_webpage",
            "search_history",
            "get_recommendations",
            "get_watch_history",
            "query_discovery_pool",
            "get_config",
            "list_sources",
        }
        soft = registry.filter_by_permission("soft_write")
        assert {"write_memory", "submit_feedback", "save_item"}.issubset(set(soft.names))
        assert "update_config" not in soft.names
        assert "create_source" not in soft.names
        tool = registry.get("update_config")
        assert tool is not None
        assert tool.permission_level == "hard_write"

    def test_subset_for_skill_whitelist(self, tmp_path) -> None:
        registry = build_agent_tool_registry(AgentToolContext(database=self._make_db(tmp_path)))
        subset = registry.subset(["get_profile", "read_memory"])
        assert set(subset.names) == {"get_profile", "read_memory"}

    def test_source_tools_skipped_without_database(self) -> None:
        registry = build_agent_tool_registry(AgentToolContext())
        assert "list_sources" not in registry.names
        assert "create_source" not in registry.names
        # Everything else is still registered and degrades per-call.
        assert "get_profile" in registry.names
        assert "get_config" in registry.names

    def test_llm_schemas_render_for_all_tools(self, tmp_path) -> None:
        registry = build_agent_tool_registry(AgentToolContext(database=self._make_db(tmp_path)))
        schemas = registry.llm_schemas()
        names = {entry["function"]["name"] for entry in schemas}
        assert len(names) == len(registry)
        for entry in schemas:
            assert entry["type"] == "function"
            assert entry["function"]["parameters"]["type"] == "object"
