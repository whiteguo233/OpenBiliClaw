"""Tests for the M3 watch-history and config tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
from openbiliclaw.agent.tools.bilibili_tools import build_bilibili_tools
from openbiliclaw.agent.tools.config_tools import build_config_tools
from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _registry(*builders: Any, ctx: AgentToolContext) -> ToolRegistry:
    registry = ToolRegistry()
    for builder in builders:
        for tool in builder(ctx):
            registry.register(tool)
    return registry


class _FakeHistoryDatabase:
    def list_content_history(
        self, category: str, *, limit: int = 12, **_: Any
    ) -> tuple[list[dict[str, Any]], int]:
        if category == "clicked":
            return (
                [
                    {
                        "occurred_at": "2026-09-22 21:00:00",
                        "title": "机械键盘组装教程",
                        "author_name": "外设小站",
                        "source_platform": "bilibili",
                    }
                ],
                1,
            )
        return ([], 0)

    def list_saved_memberships(
        self, list_kind: str, limit: int = 50, **_: Any
    ) -> list[dict[str, Any]]:
        if list_kind == "favorite":
            return [
                {
                    "added_at": "2026-09-20 10:00:00",
                    "title": "露营装备清单",
                    "author_name": "户外玩家",
                    "note": "国庆出发前看",
                }
            ]
        return []


class TestGetWatchHistory:
    async def test_clicked_history(self) -> None:
        ctx = AgentToolContext(database=_FakeHistoryDatabase())
        result = await _registry(build_bilibili_tools, ctx=ctx).dispatch(
            "get_watch_history", {"kind": "clicked"}
        )
        assert result.ok
        assert "机械键盘组装教程" in result.content
        assert "外设小站" in result.content

    async def test_saved_list_with_note(self) -> None:
        ctx = AgentToolContext(database=_FakeHistoryDatabase())
        result = await _registry(build_bilibili_tools, ctx=ctx).dispatch(
            "get_watch_history", {"kind": "favorite"}
        )
        assert result.ok
        assert "收藏清单" in result.content
        assert "国庆出发前看" in result.content

    async def test_empty_category(self) -> None:
        ctx = AgentToolContext(database=_FakeHistoryDatabase())
        result = await _registry(build_bilibili_tools, ctx=ctx).dispatch(
            "get_watch_history", {"kind": "removed"}
        )
        assert result.ok
        assert "没有" in result.content

    async def test_invalid_kind_rejected_by_schema(self) -> None:
        ctx = AgentToolContext(database=_FakeHistoryDatabase())
        result = await _registry(build_bilibili_tools, ctx=ctx).dispatch(
            "get_watch_history", {"kind": "everything"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"

    async def test_missing_component(self) -> None:
        result = await _registry(build_bilibili_tools, ctx=AgentToolContext()).dispatch(
            "get_watch_history", {}
        )
        assert not result.ok
        assert result.error == "handler_error"


class TestGetConfig:
    def _make_config(self) -> Config:
        config = Config()
        config.llm.deepseek.api_key = "sk-super-secret-key"
        config.llm.deepseek.model = "deepseek-chat"
        config.bilibili.cookie = "SESSDATA=topsecret"
        return config

    async def test_sensitive_fields_are_redacted(self) -> None:
        ctx = AgentToolContext(config=self._make_config())
        result = await _registry(build_config_tools, ctx=ctx).dispatch("get_config", {})
        assert result.ok
        assert "sk-super-secret-key" not in result.content
        assert "SESSDATA=topsecret" not in result.content
        assert "已脱敏" in result.content
        # Non-sensitive fields stay readable.
        assert "deepseek-chat" in result.content

    async def test_section_filter(self) -> None:
        ctx = AgentToolContext(config=self._make_config())
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "get_config", {"section": "bilibili"}
        )
        assert result.ok
        assert "bilibili" in result.content
        assert "SESSDATA" not in result.content
        assert "deepseek" not in result.content

    async def test_unknown_section(self) -> None:
        ctx = AgentToolContext(config=self._make_config())
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "get_config", {"section": "nonsense"}
        )
        assert result.ok
        assert "没有「nonsense」段" in result.content
        assert "llm" in result.content

    async def test_missing_component(self) -> None:
        result = await _registry(build_config_tools, ctx=AgentToolContext()).dispatch(
            "get_config", {}
        )
        assert not result.ok
        assert result.error == "handler_error"


class TestUpdateConfig:
    def _ctx(self, **hooks: Any) -> AgentToolContext:
        config = Config()
        config.llm.default_provider = "ollama"
        config.llm.ollama.model = "test-model"
        return AgentToolContext(config=config, **hooks)

    async def test_invalid_runtime_config_never_reaches_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        reloaded: list[Any] = []
        ctx = self._ctx(
            config_persist_hook=lambda cfg: save_config(cfg, path),
            config_reload_hook=lambda cfg: reloaded.append(cfg),
        )
        save_config(ctx.config, path)
        original = path.read_text()
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "llm.default_provider", "value": "nonexistent-provider"}
        )
        assert not result.ok
        assert "llm.default_provider" in result.content
        assert ctx.config.llm.default_provider == "ollama"
        assert path.read_text() == original
        assert reloaded == []

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
    async def test_nonfinite_numeric_config_is_rejected(self, value: str) -> None:
        persisted: list[Any] = []
        ctx = self._ctx(config_persist_hook=lambda cfg: persisted.append(cfg))
        original = ctx.config.discovery.admission_min_score
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "discovery.admission_min_score", "value": value}
        )
        assert not result.ok
        assert ctx.config.discovery.admission_min_score == original
        assert not persisted

    async def test_real_write_applies_persists_and_reloads(self) -> None:
        persisted: list[str] = []
        reloaded: list[str] = []
        ctx = self._ctx(
            config_persist_hook=lambda cfg: persisted.append(cfg.language) or "/tmp/config.toml",
            config_reload_hook=lambda cfg: reloaded.append(cfg.language),
        )
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "language", "value": "en-US", "reason": "用户要求"}
        )
        assert result.ok
        assert ctx.config.language == "en-US"
        assert persisted == ["en-US"]
        assert reloaded == ["en-US"]
        assert "language" in result.content
        assert "热重载" in result.content

    async def test_secret_key_refused(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "llm.deepseek.api_key", "value": "sk-x"}
        )
        assert not result.ok
        assert "敏感" in result.content
        assert ctx.config.llm.deepseek.api_key != "sk-x"

    async def test_path_key_refused(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "data_dir", "value": "/elsewhere"}
        )
        assert not result.ok
        assert "不允许" in result.content

    async def test_unknown_key_refused(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "llm.no_such_field", "value": "x"}
        )
        assert not result.ok
        assert "不存在" in result.content

    async def test_bool_coercion(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        current = ctx.config.scheduler.auto_update_enabled
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config",
            {"key": "scheduler.auto_update_enabled", "value": "false" if current else "true"},
        )
        assert result.ok, result.content
        assert ctx.config.scheduler.auto_update_enabled is (not current)

    async def test_bool_coercion_failure_is_readable(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "scheduler.auto_update_enabled", "value": "maybe"}
        )
        assert not result.ok
        assert "布尔" in result.content

    async def test_persist_failure_rolls_back(self) -> None:
        def _failing_hook(cfg: Any) -> str:
            raise OSError("disk full")

        ctx = self._ctx(config_persist_hook=_failing_hook)
        before = ctx.config.language
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "language", "value": "en-US"}
        )
        assert not result.ok
        assert result.error == "handler_error"
        assert ctx.config.language == before

    @pytest.mark.parametrize("rollback_failure", ["", "persist", "reload"])
    async def test_reload_failure_compensates_disk_and_runtime(
        self, tmp_path: Path, rollback_failure: str
    ) -> None:
        path = tmp_path / "config.toml"
        reloaded: list[str] = []
        ctx = self._ctx()
        before = ctx.config.language
        save_config(ctx.config, path)

        def persist(candidate: Config) -> Path:
            if candidate.language == before and rollback_failure == "persist":
                raise OSError("rollback disk unavailable")
            return save_config(candidate, path)

        async def reload(candidate: Config) -> None:
            reloaded.append(candidate.language)
            if candidate.language == before and rollback_failure == "reload":
                raise RuntimeError("rollback runtime unavailable")
            # A runtime can publish the candidate before a later restart step
            # fails, so rollback must restore both components and the file.
            ctx.config = candidate
            if candidate.language == "en-US":
                raise RuntimeError("candidate runtime restart failed")

        ctx.config_persist_hook = persist
        ctx.config_reload_hook = reload
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "language", "value": "en-US"}
        )

        assert not result.ok
        assert "candidate runtime restart failed" in result.content
        assert reloaded == ["en-US", before]
        assert load_config(path).language == ("en-US" if rollback_failure == "persist" else before)
        assert ctx.config.language == ("en-US" if rollback_failure == "reload" else before)
        if rollback_failure:
            assert (
                f"rollback {'disk' if rollback_failure == 'persist' else 'runtime'} unavailable"
                in result.content
            )

    async def test_missing_persist_hook_is_unavailable(self) -> None:
        result = await _registry(build_config_tools, ctx=self._ctx()).dispatch(
            "update_config", {"key": "language", "value": "en-US"}
        )
        assert not result.ok
        assert result.error == "handler_error"

    async def test_same_value_is_a_noop(self) -> None:
        ctx = self._ctx(config_persist_hook=lambda cfg: "/tmp/config.toml")
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "language", "value": ctx.config.language}
        )
        assert result.ok
        assert "无需修改" in result.content

    async def test_permission_level_is_hard_write(self) -> None:
        registry = _registry(build_config_tools, ctx=AgentToolContext(config=Config()))
        tool = registry.get("update_config")
        assert tool is not None
        assert tool.permission_level == "hard_write"
        assert tool.impact_hint

    async def test_missing_required_fields(self) -> None:
        ctx = AgentToolContext(config=Config())
        result = await _registry(build_config_tools, ctx=ctx).dispatch(
            "update_config", {"key": "llm.default_provider"}
        )
        assert not result.ok
        assert result.error == "invalid_arguments"


class TestSearchChatTurnsDatabase:
    """The storage method backing the search_history tool."""

    def _make_db(self, tmp_path: Path) -> Database:
        db = Database(tmp_path / "test.db")
        db.initialize()
        return db

    def test_keyword_matches_message_and_reply(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.create_chat_turn(turn_id="t1", message="聊聊机械键盘")
        db.complete_chat_turn("t1", reply="键盘话题不错")
        db.create_chat_turn(turn_id="t2", message=" unrelated ")
        db.complete_chat_turn("t2", reply="也聊聊键盘轴体")

        by_message = db.search_chat_turns(keyword="机械键盘")
        assert [row["turn_id"] for row in by_message] == ["t1"]
        by_reply = db.search_chat_turns(keyword="轴体")
        assert [row["turn_id"] for row in by_reply] == ["t2"]
        assert db.search_chat_turns(keyword="不存在") == []

    def test_session_and_time_filters(self, tmp_path: Path) -> None:
        db = self._make_db(tmp_path)
        db.create_chat_turn(turn_id="t1", message="你好", session="popup")
        db.create_chat_turn(turn_id="t2", message="你好", session="desktop")

        popup_only = db.search_chat_turns(keyword="你好", session="popup")
        assert [row["turn_id"] for row in popup_only] == ["t1"]

        from datetime import datetime

        future = db.search_chat_turns(keyword="你好", start_time=datetime(2999, 1, 1))
        assert future == []
