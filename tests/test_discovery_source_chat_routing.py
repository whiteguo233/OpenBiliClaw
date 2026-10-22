"""New chat subscriptions use independent sources; approved legacy actions remain valid."""

from __future__ import annotations

import pytest

from openbiliclaw.agent.approvals import ApprovalStore
from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tools import AgentToolContext, build_agent_tool_registry
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.storage.database import Database
from tests.test_agent_loop import FakeAgentLLM, _collect, _tool_call


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "routing.db")
    db.initialize()
    yield db
    db.close()


@pytest.mark.parametrize("source_type", [None, "web", "rss", "atom", "json_feed"])
async def test_new_chat_web_creation_redirects_before_any_approval(database, source_type):
    arguments = {"name": "News", "url": "https://example.com/feed", "strategy": "feed"}
    if source_type is not None:
        arguments["source_type"] = source_type
    store = ApprovalStore()
    registry = build_agent_tool_registry(AgentToolContext(database=database))
    llm = FakeAgentLLM(
        [
            LLMResponse(content="", tool_calls=[_tool_call("create_source", arguments)]),
            LLMResponse(content="改用自定义来源试跑。"),
        ]
    )
    events = await _collect(
        AgentLoop(llm, registry, approval_gate=store),
        system_instruction="Manage sources",
        user_message="订阅这个 Feed",
    )
    result = next(event for event in events if event.type == "tool_result")
    assert not result.ok
    assert "prepare_discovery_source" in result.text
    assert "activate_discovery_source" in result.text
    assert store.list() == []
    assert database.get_all_recipes() == []
    assert not any(event.type == "approval_request" for event in events)


async def test_native_platform_keyword_subscription_keeps_approval(database):
    args = {
        "source_type": "bilibili",
        "name": "Python教程",
        "strategy": "search",
        "query": "Python",
    }
    registry = build_agent_tool_registry(AgentToolContext(database=database))
    store = ApprovalStore()
    llm = FakeAgentLLM(
        [
            LLMResponse(content="", tool_calls=[_tool_call("create_source", args)]),
            LLMResponse(content="请查看订阅卡片。"),
        ]
    )
    events = await _collect(
        AgentLoop(llm, registry, approval_gate=store),
        system_instruction="Manage sources",
        user_message="订阅B站Python教程",
    )
    request = next(event for event in events if event.type == "approval_request")
    assert "Python教程" in request.summary
    assert len(store.list()) == 1
    assert database.get_all_recipes() == []
    result = await registry.dispatch("create_source", args)
    assert result.ok, result.content
    assert database.get_all_recipes()[0]["config"]["query"] == "Python"


async def test_persisted_legacy_web_approval_can_still_execute(database):
    # Approval execution dispatches the already-reviewed payload; it never invokes
    # the summary callback that guards newly submitted chat actions.
    registry = build_agent_tool_registry(AgentToolContext(database=database))
    result = await registry.dispatch(
        "create_source",
        {
            "source_type": "web",
            "name": "Previously approved",
            "strategy": "feed",
            "url": "https://example.com/feed",
        },
    )
    assert result.ok, result.content
    assert database.get_all_recipes()[0]["source_type"] == "web"


async def test_platform_label_cannot_route_feed_into_legacy_platform_recipes(database):
    registry = build_agent_tool_registry(AgentToolContext(database=database))
    store = ApprovalStore()
    llm = FakeAgentLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    _tool_call(
                        "create_source",
                        {
                            "source_type": "github",
                            "strategy": "feed",
                            "url": "https://github.com/psf/requests/releases.atom",
                        },
                    )
                ],
            ),
            LLMResponse(content="改用动态订阅试跑。"),
        ]
    )
    events = await _collect(
        AgentLoop(llm, registry, approval_gate=store),
        system_instruction="Manage sources",
        user_message="增加这个 Atom 订阅",
    )
    failure = next(event for event in events if event.type == "tool_result")
    assert not failure.ok and "prepare_discovery_source" in failure.text
    assert store.list() == []
    assert database.get_all_recipes() == []
