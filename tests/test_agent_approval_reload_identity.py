"""Config reload must preserve the live approval authority for path aliases."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, LLMConfig, LLMProviderConfig, save_config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("path_style", ["symlink", "relative", "relative-other-cwd"])
async def test_config_approval_stays_executed_across_data_path_normalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_style: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "project-alias"
    alias.symlink_to(project, target_is_directory=True)
    monkeypatch.chdir(tmp_path if path_style == "relative-other-cwd" else project)
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(project))
    cfg = Config(
        data_dir=str(alias / "data") if path_style == "symlink" else "data",
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key="sk-test-not-a-real-key", model="gpt-4o-mini"),
        ),
    )
    cfg.scheduler.enabled = False
    cfg.storage.db_path = str(project / "data" / "openbiliclaw.db")
    save_config(cfg, project / "config.toml")
    app = create_app()
    ctx = app.state.runtime_context
    original_store = ctx.chat_approval_store
    assert original_store.path.resolve() == project / "data" / "chat_approvals.json"
    approval = original_store.submit(
        tool_name="update_config",
        arguments={"key": "language", "value": "en-US"},
        summary="Update language through a complete runtime rebuild",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
        assert response.status_code == 200
        assert response.json()["approval"]["status"] == "executing"
        for _ in range(500):
            if original_store.get(approval.approval_id).status in {"executed", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert original_store.get(approval.approval_id).status == "executed", original_store.get(
            approval.approval_id
        ).error
        assert (await client.get("/api/config/apply-status")).json()["state"] == "applied"
        current = (await client.get("/api/chat/approvals")).json()["items"]
        record = next(item for item in current if item["approval_id"] == approval.approval_id)
        assert record["status"] == "executed"
        assert ctx.chat_approval_store is original_store
        repeat = await client.post(f"/api/chat/approvals/{approval.approval_id}/approve")
        assert repeat.json()["already_executed"] is True
        assert repeat.json()["queued"] is False
