"""Production agent tools keep the API-owned event ingress through reloads."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


async def test_agent_feedback_ingress_is_bound_at_startup_and_after_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(
                api_key="sk-test-not-a-real-key",
                model="gpt-4o-mini",
            ),
        )
    )
    cfg.scheduler.enabled = False
    cfg.storage.db_path = str(tmp_path / "data" / "openbiliclaw.db")
    save_config(cfg, tmp_path / "config.toml")
    app = create_app()
    ctx = app.state.runtime_context
    initial_context = ctx.agent_tool_context
    for reloaded in (False, True):
        if reloaded:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.put(
                    "/api/config",
                    json={
                        "suppress_background_llm_work": True,
                        "llm": {"openai": {"model": "gpt-4.1-mini"}},
                    },
                )
                assert response.status_code == 202
                for _ in range(200):
                    status = (await client.get("/api/config/apply-status")).json()
                    if status["state"] in {"applied", "failed"}:
                        break
                    await asyncio.sleep(0.01)
                assert status["state"] == "applied"
            assert ctx.agent_tool_context is not initial_context
        result = await ctx.agent_tool_registry.dispatch(
            "submit_feedback",
            {
                "recommendation_id": 999999,
                "feedback_type": "like",
            },
        )
        assert result.ok, result.content
        assert "未找到" in result.content
        assert ctx.agent_tool_context.event_ingress is app.state.event_ingress
