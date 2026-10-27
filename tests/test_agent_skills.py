"""Tests for the M4 chat skill system.

Covers SKILL.md parsing (valid / missing fields / malformed frontmatter),
catalog loading with user-directory overrides, builtin skill whitelists,
the ``suggest_skill`` meta tool, tool-subset wiring, and the skill-aware
chat endpoints (``POST /api/chat/agent/stream`` + ``GET /api/chat/skills``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.agent.skill import (
    DEFAULT_SKILL_NAME,
    SkillCatalog,
    SkillDefinition,
    SkillParseError,
    builtin_skills_dir,
    load_skill_catalog,
    parse_skill_md,
)
from openbiliclaw.agent.tasks import START_BACKGROUND_TASK_TOOL_NAME
from openbiliclaw.agent.tools import (
    SUGGEST_SKILL_TOOL_NAME,
    AgentToolContext,
    Tool,
    ToolRegistry,
    build_agent_tool_registry,
    build_suggest_skill_tool,
)
from openbiliclaw.api.app import create_app
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path

VALID_SKILL_MD = """---
name: test-skill
title: 测试角色
description: 一个用于测试的 skill
tools:
  - get_profile
  - write_memory
---

你是测试角色。可用数据入口：get_profile。
"""

BUILTIN_TOOL_WHITELISTS: dict[str, set[str]] = {
    "taste-companion": {
        "search_web",
        "read_webpage",
        "delete_memory",
        "get_profile",
        "read_memory",
        "write_memory",
        "search_history",
        "get_recommendations",
        "query_discovery_pool",
        "get_watch_history",
        "list_sources",
        "get_config",
        "submit_feedback",
        "save_item",
    },
    "taste-explorer": {
        "search_web",
        "read_webpage",
        "delete_memory",
        "get_profile",
        "read_memory",
        "write_memory",
        "search_history",
        "submit_feedback",
    },
    "bangumi-advisor": {
        "search_web",
        "read_webpage",
        "get_profile",
        "read_memory",
        "get_recommendations",
        "get_watch_history",
        "save_item",
        "submit_feedback",
    },
    "system-steward": {
        "search_web",
        "read_webpage",
        "list_sources",
        "get_config",
        "create_source",
        "toggle_source",
        "update_config",
    },
}


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------


def test_parse_valid_skill_md() -> None:
    skill = parse_skill_md(VALID_SKILL_MD, source="custom")
    assert skill.name == "test-skill"
    assert skill.title == "测试角色"
    assert skill.display_name == "测试角色"
    assert skill.description == "一个用于测试的 skill"
    assert skill.tools == ("get_profile", "write_memory")
    assert "你是测试角色" in skill.system_prompt
    assert skill.source == "custom"
    assert skill.builtin is False


def test_parse_inline_tools_list() -> None:
    text = "---\nname: inline\ndescription: d\ntools: [get_profile, 'read_memory']\n---\n正文。\n"
    skill = parse_skill_md(text)
    assert skill.tools == ("get_profile", "read_memory")
    assert skill.title == ""
    assert skill.display_name == "inline"


def test_parse_missing_name_rejected() -> None:
    with pytest.raises(SkillParseError, match="name"):
        parse_skill_md("---\ndescription: d\n---\n正文。\n")


def test_parse_missing_description_rejected() -> None:
    with pytest.raises(SkillParseError, match="description"):
        parse_skill_md("---\nname: x\n---\n正文。\n")


def test_parse_invalid_name_slug_rejected() -> None:
    with pytest.raises(SkillParseError, match="slug"):
        parse_skill_md("---\nname: 口味伙伴\ndescription: d\n---\n正文。\n")


def test_parse_missing_frontmatter_rejected() -> None:
    with pytest.raises(SkillParseError, match="frontmatter"):
        parse_skill_md("name: x\ndescription: d\n正文。\n")


def test_parse_unterminated_frontmatter_rejected() -> None:
    with pytest.raises(SkillParseError, match="---"):
        parse_skill_md("---\nname: x\ndescription: d\n正文。\n")


def test_parse_empty_body_rejected() -> None:
    with pytest.raises(SkillParseError, match="正文"):
        parse_skill_md("---\nname: x\ndescription: d\n---\n   \n")


def test_parse_malformed_frontmatter_line_rejected() -> None:
    with pytest.raises(SkillParseError, match="无法解析"):
        parse_skill_md("---\nname: x\ndescription: d\n???\n---\n正文。\n")


def test_parse_orphan_list_item_rejected() -> None:
    with pytest.raises(SkillParseError, match="列表项"):
        parse_skill_md("---\nname: x\n- stray\ndescription: d\n---\n正文。\n")


# ---------------------------------------------------------------------------
# Catalog loading: builtins, overrides, invalid files
# ---------------------------------------------------------------------------


def test_builtin_skills_load_with_expected_whitelists() -> None:
    catalog = load_skill_catalog()
    assert set(catalog.names) == set(BUILTIN_TOOL_WHITELISTS)
    for name, expected_tools in BUILTIN_TOOL_WHITELISTS.items():
        skill = catalog.get(name)
        assert skill is not None
        assert skill.source == "builtin"
        assert set(skill.tools) == expected_tools
        assert skill.system_prompt.strip()
    default = catalog.default()
    assert default is not None
    assert default.name == DEFAULT_SKILL_NAME


def test_builtin_whitelists_match_registered_tools() -> None:
    """Every whitelisted tool name must exist in the full v1 registry."""
    registry = build_agent_tool_registry(AgentToolContext(database=object()))
    catalog = load_skill_catalog()
    for skill in catalog.definitions:
        missing = set(skill.tools) - set(registry.names)
        assert not missing, f"{skill.name} 白名单引用了未注册的工具: {missing}"
        # System settings remain exclusive to the steward; the two memory
        # roles can submit approval-gated deletion of their chat notes.
        if skill.name != "system-steward":
            for tool_name in skill.tools:
                tool = registry.get(tool_name)
                assert tool is not None
                if tool_name == "delete_memory":
                    assert skill.name in {"taste-companion", "taste-explorer"}
                    assert tool.permission_level == "hard_write"
                else:
                    assert tool.permission_level != "hard_write"


def test_user_skill_overrides_builtin(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    user_dir = tmp_path / "skills"
    override = user_dir / "taste-companion"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text(
        "---\nname: taste-companion\ndescription: 自定义默认伙伴\n"
        "tools: [get_profile]\n---\n自定义人设。\n",
        encoding="utf-8",
    )
    with caplog.at_level("INFO"):
        catalog = load_skill_catalog(user_dir=user_dir)
    skill = catalog.get("taste-companion")
    assert skill is not None
    assert skill.source == "custom"
    assert skill.tools == ("get_profile",)
    assert "自定义人设" in skill.system_prompt
    assert "overrides" in caplog.text
    # The other builtins are untouched.
    assert catalog.get("system-steward") is not None


def test_user_skill_added_and_invalid_file_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    user_dir = tmp_path / "skills"
    good = user_dir / "my-skill"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 自定义\n---\n人设正文。\n", encoding="utf-8"
    )
    bad = user_dir / "broken"
    bad.mkdir()
    (bad / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    with caplog.at_level("WARNING"):
        catalog = load_skill_catalog(user_dir=user_dir)
    assert catalog.get("my-skill") is not None
    assert catalog.get("my-skill").source == "custom"  # type: ignore[union-attr]
    # Broken file skipped, builtins still loaded.
    assert len(catalog) == len(BUILTIN_TOOL_WHITELISTS) + 1
    assert "Skipping invalid skill file" in caplog.text


def test_missing_dirs_yield_empty_catalog(tmp_path: Path) -> None:
    catalog = load_skill_catalog(builtin_dir=tmp_path / "nope", user_dir=tmp_path / "also-nope")
    assert len(catalog) == 0
    assert catalog.default() is None


# ---------------------------------------------------------------------------
# Tool subset + suggest_skill meta tool
# ---------------------------------------------------------------------------


def _sample_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(name="get_profile", description="r", handler=lambda args: "profile"),
            Tool(
                name="write_memory",
                description="w",
                handler=lambda args: "ok",
                permission_level="soft_write",
            ),
            Tool(
                name="update_config",
                description="h",
                handler=lambda args: "ok",
                permission_level="hard_write",
            ),
        ]
    )


def test_subset_filters_to_skill_whitelist() -> None:
    registry = _sample_registry()
    skill = parse_skill_md(VALID_SKILL_MD)
    subset = registry.subset(skill.tools)
    assert subset.names == ["get_profile", "write_memory"]
    assert "update_config" not in subset
    # Unknown whitelist entries are ignored, not errors.
    empty = registry.subset(["does-not-exist"])
    assert len(empty) == 0


def test_suggest_skill_tool_records_suggestion() -> None:
    tool = build_suggest_skill_tool(["taste-companion", "system-steward"])
    registry = ToolRegistry([tool])
    result = registry.dispatch_sync(
        SUGGEST_SKILL_TOOL_NAME, {"skill": "system-steward", "reason": "改配置"}
    )
    assert result.ok
    assert "system-steward" in result.content
    assert "改配置" in result.content


def test_suggest_skill_tool_rejects_unknown_skill() -> None:
    tool = build_suggest_skill_tool(["taste-companion"])
    result = ToolRegistry([tool]).dispatch_sync(SUGGEST_SKILL_TOOL_NAME, {"skill": "nope"})
    # Schema enum validation rejects names outside the catalog.
    assert not result.ok
    assert result.error == "invalid_arguments"


def test_skill_switch_guide_lists_other_skills() -> None:
    catalog = SkillCatalog(
        [
            SkillDefinition(name="a", description="甲", system_prompt="p"),
            SkillDefinition(name="b", description="乙", system_prompt="p"),
        ]
    )
    guide = catalog.render_switch_guide("a")
    assert "b" in guide
    assert "乙" in guide
    assert SUGGEST_SKILL_TOOL_NAME in guide
    assert "甲" not in guide.split("（")[0]
    assert catalog.render_switch_guide("missing").count("\n") >= 1
    single = SkillCatalog([SkillDefinition(name="a", description="甲", system_prompt="p")])
    assert single.render_switch_guide("a") == ""


def test_skill_system_prompt_layering() -> None:
    from openbiliclaw.soul.dialogue import _layer_skill_system_prompt

    skill = SkillDefinition(
        name="system-steward",
        title="系统管家",
        description="d",
        system_prompt="你是系统管家。",
    )
    layered = _layer_skill_system_prompt("BASE", skill, skill_switch_guide="GUIDE")
    assert layered.startswith("BASE")
    assert "系统管家" in layered
    assert "你是系统管家。" in layered
    assert layered.endswith("GUIDE")


# ---------------------------------------------------------------------------
# API integration: skill field on the stream endpoint + skills listing
# ---------------------------------------------------------------------------


class FakeSkillDialogue:
    """Dialogue double recording the skill wiring and finishing immediately."""

    def __init__(self) -> None:
        from openbiliclaw.agent.loop import AgentEvent

        self._event = AgentEvent(type="final", step=1, text="好")
        self.agent_calls: list[dict[str, Any]] = []

    async def stream_agent_reply(
        self,
        agent_loop: Any,
        message: str,
        *,
        session: str = "",
        scope: str = "chat",
        turn_id: str = "",
        session_id: str = "",
        skill: Any = None,
        tools: Any = None,
        skill_switch_guide: str = "",
    ) -> Any:
        self.agent_calls.append(
            {
                "message": message,
                "session_id": session_id,
                "skill": skill,
                "tools": tools,
                "skill_switch_guide": skill_switch_guide,
            }
        )
        yield self._event


def _skill_app(tmp_path: Path, dialogue: FakeSkillDialogue, *, with_registry: bool) -> Any:
    database = Database(tmp_path / "openbiliclaw.db")
    database.initialize()
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        dialogue=dialogue,
    )
    app.state.runtime_context.agent_loop = object()
    if with_registry:
        app.state.runtime_context.agent_tool_registry = build_agent_tool_registry(
            AgentToolContext(database=database)
        )
    return app


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


def test_stream_endpoint_defaults_to_taste_companion(tmp_path: Path) -> None:
    dialogue = FakeSkillDialogue()
    app = _skill_app(tmp_path, dialogue, with_registry=True)

    with TestClient(app) as client:
        response = client.post("/api/chat/agent/stream", json={"message": "你好"})

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [event for event, _ in events] == ["final", "done"]
    assert events[1][1]["skill"] == "taste-companion"
    call = dialogue.agent_calls[0]
    assert call["skill"].name == "taste-companion"
    tools = call["tools"]
    assert tools is not None
    assert set(tools.names) == BUILTIN_TOOL_WHITELISTS["taste-companion"] | {
        SUGGEST_SKILL_TOOL_NAME,
        START_BACKGROUND_TASK_TOOL_NAME,
    }
    # Hard-write tools are excluded from the default skill.
    assert "update_config" not in tools.names
    assert "system-steward" in call["skill_switch_guide"]


def test_stream_endpoint_with_explicit_skill(tmp_path: Path) -> None:
    dialogue = FakeSkillDialogue()
    app = _skill_app(tmp_path, dialogue, with_registry=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"message": "帮我加一个订阅源", "skill": "system-steward"},
        )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1][1]["skill"] == "system-steward"
    call = dialogue.agent_calls[0]
    assert call["skill"].name == "system-steward"
    assert set(call["tools"].names) == BUILTIN_TOOL_WHITELISTS["system-steward"] | {
        SUGGEST_SKILL_TOOL_NAME,
        START_BACKGROUND_TASK_TOOL_NAME,
    }


def test_stream_endpoint_unknown_skill_rejected(tmp_path: Path) -> None:
    dialogue = FakeSkillDialogue()
    app = _skill_app(tmp_path, dialogue, with_registry=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/agent/stream",
            json={"message": "你好", "skill": "no-such-skill"},
        )

    assert response.status_code == 422
    assert "no-such-skill" in response.json()["detail"]
    assert dialogue.agent_calls == []


def test_list_chat_skills_endpoint(tmp_path: Path) -> None:
    dialogue = FakeSkillDialogue()
    app = _skill_app(tmp_path, dialogue, with_registry=False)

    with TestClient(app) as client:
        response = client.get("/api/chat/skills")

    assert response.status_code == 200
    skills = response.json()["skills"]
    by_name = {skill["name"]: skill for skill in skills}
    assert set(by_name) == set(BUILTIN_TOOL_WHITELISTS)
    assert by_name["taste-companion"]["default"] is True
    assert by_name["taste-companion"]["source"] == "builtin"
    assert by_name["taste-companion"]["title"] == "口味伙伴"
    assert by_name["system-steward"]["builtin"] is True
    assert set(by_name["bangumi-advisor"]["tools"]) == BUILTIN_TOOL_WHITELISTS["bangumi-advisor"]


def test_builtin_skills_dir_is_inside_package() -> None:
    assert builtin_skills_dir().is_dir()
    assert (builtin_skills_dir() / "taste-companion" / "SKILL.md").is_file()
