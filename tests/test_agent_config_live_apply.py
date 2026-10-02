"""Chat-only config edits preserve active runtime work and transaction semantics."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import httpx
import pytest

from openbiliclaw.agent.approvals import ApprovalStore
from openbiliclaw.agent.loop import AgentLoop
from openbiliclaw.agent.tools import AgentToolContext, Tool, ToolRegistry
from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.api.source_auth.write import CredentialVerdict
from openbiliclaw.config import (
    AgentConfig,
    Config,
    LLMConfig,
    LLMProviderConfig,
    load_config,
    save_config,
)
from openbiliclaw.llm.base import LLMResponse
from openbiliclaw.sources.x_auth import XCookieManager

if TYPE_CHECKING:
    from pathlib import Path


def _app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, twitter: bool = False) -> Any:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    config = Config(
        data_dir=str(tmp_path / "data"),
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-test-not-a-real-key", model="gpt-4o-mini"),
        ),
    )
    config.scheduler.enabled = False
    config.sources.twitter.enabled = twitter
    if twitter:
        monkeypatch.delenv(config.sources.twitter.cookie_env, raising=False)
        XCookieManager(config.data_path).set_cookie("auth_token=old-test; ct0=old-test")
    config.storage.db_path = str(tmp_path / "data" / "openbiliclaw.db")
    save_config(config, tmp_path / "config.toml")
    return create_app()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("loop_enabled", False),
        ("loop_max_steps", 63),
        ("tool_result_max_chars", 2000),
        ("session_title_enabled", False),
        ("task_max_steps", 16),
    ],
)
async def test_agent_approval_applies_without_draining_or_cancelling_runtime_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object
) -> None:
    app = _app(monkeypatch, tmp_path)
    ctx = app.state.runtime_context
    preserved = {
        name: getattr(ctx, name)
        for name in (
            "llm_service",
            "agent_tool_registry",
            "agent_tool_context",
            "chat_approval_store",
            "soul_engine",
            "dialogue",
            "dialogue_settlement_queue",
            "runtime_controller",
        )
    }
    old_loop = ctx.agent_loop
    drain = AsyncMock(side_effect=AssertionError("chat budget must not drain feedback"))
    monkeypatch.setattr(app.state.feedback_batch_scheduler, "pause_and_drain", drain)
    publish = AsyncMock()
    monkeypatch.setattr(ctx.event_hub, "publish", publish)
    work_started, finish_work = asyncio.Event(), asyncio.Event()

    async def background_work() -> None:
        work_started.set()
        await finish_work.wait()

    work = ctx.task_registry.track("active_background_learning", background_work())
    await work_started.wait()
    approval = ctx.chat_approval_store.submit(
        tool_name="update_config",
        arguments={"key": f"agent.{field}", "value": str(value).lower()},
        summary="Adjust chat behavior",
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
            assert response.status_code == 200
            for _ in range(100):
                if approval.status in {"executed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert approval.status == "executed", approval.error
            assert (await client.get("/api/config/apply-status")).json()["state"] == "applied"
            repeat = await client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
            assert repeat.json()["already_executed"] is True
            assert repeat.json()["queued"] is False
        drain.assert_not_called()
        assert not work.done()
        assert all(getattr(ctx, name) is original for name, original in preserved.items())
        assert ctx.agent_loop is not old_loop
        assert old_loop.max_steps == 64
        assert getattr(ctx.config.agent, field) == value
        assert getattr(load_config(tmp_path / "config.toml").agent, field) == value
        result = await ctx.agent_tool_registry.dispatch("get_config", {"section": "agent"})
        assert json.loads(result.content)["agent"][field] == value
        assert any(call.args[0]["type"] == "config_reloaded" for call in publish.call_args_list)
    finally:
        finish_work.set()
        await work


async def test_repeating_current_agent_value_preserves_busy_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _app(monkeypatch, tmp_path)
    ctx = app.state.runtime_context
    original_loop = ctx.agent_loop
    drain = AsyncMock(side_effect=AssertionError("unchanged config must not drain feedback"))
    monkeypatch.setattr(app.state.feedback_batch_scheduler, "pause_and_drain", drain)
    finish_work = asyncio.Event()
    work = ctx.task_registry.track("active_background_learning", finish_work.wait())
    try:
        await app.state._apply_agent_config_update("agent.loop_max_steps", 64)
        drain.assert_not_called()
        assert ctx.agent_loop is original_loop
        assert not work.done()
    finally:
        finish_work.set()
        await work


@pytest.mark.parametrize("max_steps", [63, 64])
async def test_agent_edit_with_other_unapplied_fields_still_waits_for_full_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, max_steps: int
) -> None:
    app = _app(monkeypatch, tmp_path)
    ctx = app.state.runtime_context
    old_dialogue = ctx.dialogue
    candidate = load_config(tmp_path / "config.toml")
    candidate.language = "en-US"
    save_config(candidate, tmp_path / "config.toml")
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause_feedback() -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(app.state.feedback_batch_scheduler, "pause_and_drain", pause_feedback)
    update = asyncio.create_task(
        app.state._apply_agent_config_update("agent.loop_max_steps", max_steps)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not update.done()
        assert ctx.config.language != "en-US"
        assert ctx.agent_loop.max_steps == 64
    finally:
        release.set()
        await update
    assert ctx.dialogue is not old_dialogue
    assert ctx.config.language == "en-US"
    assert ctx.agent_loop.max_steps == max_steps


async def test_later_failed_full_reload_retains_successful_agent_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _app(monkeypatch, tmp_path)
    ctx = app.state.runtime_context
    old_dialogue = ctx.dialogue
    await app.state._apply_agent_config_update("agent.loop_max_steps", 63)
    assert ctx.dialogue is old_dialogue
    real_rebuild = RuntimeContext.rebuild_from_config

    async def rebuild(self: RuntimeContext, candidate: Config) -> None:
        if candidate.language == "en-US":
            raise RuntimeError("injected ordinary config failure")
        await real_rebuild(self, candidate)

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", rebuild)
    with pytest.raises(RuntimeError, match="injected ordinary config failure"):
        await app.state._apply_agent_config_update("language", "en-US")
    assert ctx.config.agent.loop_max_steps == 63
    assert ctx.agent_loop.max_steps == 63
    assert load_config(tmp_path / "config.toml").agent.loop_max_steps == 63


def _small_runtime(llm: Any, config: Config) -> RuntimeContext:
    registry = ToolRegistry([Tool(name="read", description="Read", handler=lambda _: "x" * 300)])
    store = ApprovalStore()
    return RuntimeContext(
        config=config,
        llm_service=llm,
        agent_tool_registry=registry,
        agent_tool_context=AgentToolContext(config=config),
        chat_approval_store=store,
        agent_loop=AgentLoop.from_config(llm, registry, config, approval_gate=store),
    )


async def test_live_agent_edit_does_not_change_inflight_turn_budgets() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class LLM:
        async def complete_with_native_tools(self, **kwargs: Any) -> LLMResponse:
            messages = kwargs["messages"]
            if len(messages) == 2:
                if messages[-1]["content"] == "old":
                    entered.set()
                    await release.wait()
                return LLMResponse(tool_calls=[{"id": "one", "name": "read", "arguments": {}}])
            return LLMResponse(content="done")

    config = Config(agent=AgentConfig(loop_max_steps=2, tool_result_max_chars=4000))
    ctx = _small_runtime(LLM(), config)

    async def collect(loop: AgentLoop, message: str) -> list[Any]:
        return [event async for event in loop.run(system_instruction="test", user_message=message)]

    old_loop = ctx.agent_loop
    running = asyncio.create_task(collect(old_loop, "old"))
    await entered.wait()
    candidate = copy.deepcopy(config)
    candidate.agent.loop_max_steps = 1
    candidate.agent.tool_result_max_chars = 200
    try:
        assert ctx.try_apply_agent_config(candidate)
    finally:
        release.set()
    old_events = await running
    new_events = await collect(ctx.agent_loop, "new")
    assert not any(event.type == "step_limit_reached" for event in old_events)
    assert any(event.type == "step_limit_reached" for event in new_events)
    assert next(event for event in old_events if event.type == "tool_result").truncated is False
    assert next(event for event in new_events if event.type == "tool_result").truncated is True


def test_future_agent_fields_are_not_implicitly_safe_to_apply() -> None:
    @dataclass
    class FutureAgentConfig(AgentConfig):
        learning_backend: str = "old"

    config = Config(agent=FutureAgentConfig())
    ctx = _small_runtime(object(), config)
    candidate = copy.deepcopy(config)
    candidate.agent = FutureAgentConfig(learning_backend="new", loop_max_steps=63)
    assert not ctx.try_apply_agent_config(candidate)
    assert ctx.config is config


@pytest.mark.parametrize("mode", ["direct", "pending", "failed-active"])
async def test_cookie_settings_keep_full_rebuild_even_when_coalesced_with_agent_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    app = _app(monkeypatch, tmp_path, twitter=True)
    ctx = app.state.runtime_context
    old_client = ctx.account_sync_service.x_client
    new_cookie = "auth_token=new-test; ct0=new-test"
    monkeypatch.setattr(
        "openbiliclaw.api.source_auth.write.validate_credential",
        AsyncMock(return_value=CredentialVerdict(ok=True)),
    )
    # Keep the ordinary settings revision pending until an agent edit replaces
    # it: cookie jar updates are not represented by Config equality.
    release_worker = asyncio.Event()
    first_drain_entered, fail_first_drain = asyncio.Event(), asyncio.Event()
    original_create_task = asyncio.create_task
    if mode == "failed-active":
        real_drain = app.state.feedback_batch_scheduler.pause_and_drain

        async def drain() -> None:
            if not first_drain_entered.is_set():
                first_drain_entered.set()
                await fail_first_drain.wait()
                raise RuntimeError("injected first settings handoff failure")
            await real_drain()

        monkeypatch.setattr(app.state.feedback_batch_scheduler, "pause_and_drain", drain)

    def create_task(coro: Any, *, name: str | None = None, **kwargs: Any) -> Any:
        if name == "config-apply":

            async def delayed() -> None:
                await release_worker.wait()
                await coro

            return original_create_task(delayed(), name=name, **kwargs)
        return original_create_task(coro, name=name, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", create_task)
    pending_agent = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.put(
            "/api/config", json={"sources": {"twitter": {"cookie": new_cookie}}}
        )
        assert response.status_code == 202
        try:
            if mode == "failed-active":
                release_worker.set()
                await asyncio.wait_for(first_drain_entered.wait(), 1)
            if mode != "direct":
                pending_agent = original_create_task(
                    app.state._apply_agent_config_update("agent.loop_max_steps", 63)
                )
                for _ in range(100):
                    status = (await client.get("/api/config/apply-status")).json()
                    if status["requested_revision"] == 2:
                        break
                    await asyncio.sleep(0.01)
                assert status["requested_revision"] == 2
        finally:
            release_worker.set()
            fail_first_drain.set()
            if pending_agent is not None:
                await pending_agent
            if app.state.config_apply_task is not None:
                await app.state.config_apply_task
        assert (await client.get("/api/config/apply-status")).json()["state"] == "applied"
    assert ctx.account_sync_service.x_client is not old_client
    assert ctx.account_sync_service.x_client._cookie == new_cookie
    if mode != "direct":
        assert ctx.agent_loop.max_steps == 63
