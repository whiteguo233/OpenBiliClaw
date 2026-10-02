from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.config import (
    Config,
    LLMConfig,
    LLMProviderConfig,
    LoggingConfig,
    load_config,
    save_config,
)
from openbiliclaw.logging_setup import configure_logging

if TYPE_CHECKING:
    from pathlib import Path


def _valid_config(api_key: str = "sk-valid-openai-key") -> Config:
    return Config(
        llm=LLMConfig(
            default_provider="openai",
            openai=LLMProviderConfig(api_key=api_key, model="gpt-4o-mini"),
        )
    )


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: Config) -> TestClient:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(cfg, tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    return TestClient(app)


async def _wait_for_apply_state(
    client: httpx.AsyncClient,
    expected: str,
    *,
    attempts: int = 200,
) -> dict[str, object]:
    for _ in range(attempts):
        status = (await client.get("/api/config/apply-status")).json()
        if status["state"] == expected:
            return status
        await asyncio.sleep(0.01)
    pytest.fail(f"后台配置状态未进入 {expected}")


def test_put_config_rejects_unbuildable_candidate_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    response = client.put("/api/config", json={"reset_fields": ["llm.openai.api_key"]})

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["reloaded"] is False
    assert body["rollback_applied"] is False
    assert any(
        issue["severity"] == "blocking" and issue["field"] in {"llm", "llm.openai.api_key"}
        for issue in body["config"]["issues"]
    )
    assert config_path.read_bytes() == before
    assert not (tmp_path / "config.toml.bak").exists()


@pytest.mark.asyncio
async def test_put_config_success_saves_snapshot_then_hot_reloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    before = config_path.read_bytes()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        status = await _wait_for_apply_state(client, "applied")

    assert response.status_code == 202
    body = response.json()
    assert body["ok"] is True
    assert body["reloaded"] is False
    assert body["apply_state"] == "queued"
    assert body["rollback_applied"] is False
    assert body["restart_required"] is False
    assert status["applied_revision"] == body["apply_revision"]
    assert load_config(config_path).llm.openai.model == "gpt-4.1-mini"
    assert (tmp_path / "config.toml.bak").read_bytes() == before


@pytest.mark.asyncio
async def test_put_bilibili_date_preference_hot_reloads_curator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={
                "sources": {
                    "bilibili": {
                        "recommendation_date_preset": "custom",
                        "recommendation_date_start": "2023-01-01",
                        "recommendation_date_end": "2023-12-31",
                        "recommendation_date_weight": 0.5,
                    }
                }
            },
        )
        await _wait_for_apply_state(client, "applied")

    assert response.status_code == 202
    curator = app.state.runtime_context.recommendation_engine._curator
    preference = curator._publication_preference
    assert preference.preset == "custom"
    assert preference.start_date.isoformat() == "2023-01-01"
    assert preference.end_date.isoformat() == "2023-12-31"
    assert preference.weight == 0.5


@pytest.mark.asyncio
async def test_put_config_idle_lane_returns_after_persist_before_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_rebuild(self: RuntimeContext, new_config: Config) -> None:
        entered.set()
        await release.wait()
        self.config = new_config

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", blocked_rebuild)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await asyncio.wait_for(
            client.put(
                "/api/config",
                json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
            ),
            timeout=1,
        )
        assert response.status_code == 202
        assert response.json()["apply_state"] == "queued"
        assert load_config(config_path).llm.openai.model == "gpt-4.1-mini"
        await asyncio.wait_for(entered.wait(), timeout=1)

        release.set()
        status = await _wait_for_apply_state(client, "applied")

    assert status["applied_revision"] == response.json()["apply_revision"]


@pytest.mark.asyncio
async def test_put_config_skips_feedback_cutover_for_incomplete_memory_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A real rebuilt SoulEngine must respect its stable adapter capability."""
    from openbiliclaw.soul.engine import SoulEngine

    calls = 0

    async def unexpected_cutover(self: SoulEngine) -> dict[str, object]:  # noqa: ARG001
        nonlocal calls
        calls += 1
        raise AssertionError("incomplete memory adapter cannot own a durable cursor")

    monkeypatch.setattr(
        SoulEngine,
        "prepare_feedback_owner_cutover",
        unexpected_cutover,
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        await _wait_for_apply_state(client, "applied")

    assert response.status_code == 202
    assert response.json()["reloaded"] is False
    assert calls == 0


@pytest.mark.asyncio
async def test_put_config_runs_feedback_cutover_for_capable_memory_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The capability guard must not suppress the production MemoryManager."""
    from openbiliclaw.memory.manager import MemoryManager
    from openbiliclaw.soul.engine import SoulEngine

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), tmp_path / "config.toml")
    memory = MemoryManager(tmp_path / "data")
    memory.initialize()
    calls = 0

    async def record_cutover(self: SoulEngine) -> dict[str, object]:  # noqa: ARG001
        nonlocal calls
        calls += 1
        return {"prepared": False, "feedback_owner_version": 2}

    monkeypatch.setattr(
        SoulEngine,
        "prepare_feedback_owner_cutover",
        record_cutover,
    )
    app = create_app(memory_manager=memory, database=object(), soul_engine=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        await _wait_for_apply_state(client, "applied")

    assert response.status_code == 202
    assert response.json()["reloaded"] is False
    assert calls == 1


@pytest.mark.asyncio
async def test_put_config_rolls_back_when_hot_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    before = config_path.read_bytes()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        status = await _wait_for_apply_state(client, "failed")

    assert response.status_code == 202
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is False
    assert "simulated" in status["message"]
    assert config_path.read_bytes() == before
    assert (tmp_path / "config.toml.bak").read_bytes() == before


@pytest.mark.asyncio
async def test_put_config_restores_in_memory_runtime_after_restart_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A post-rebuild task failure must not leave the rejected config live."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async def fail_background_restart(
        app_arg: object,  # noqa: ARG001
        *,
        run_post_reload_llm_work: bool = True,  # noqa: ARG001
    ) -> None:
        raise RuntimeError("simulated background restart failure")

    monkeypatch.setattr(
        app.state.runtime_context,
        "restart_background_tasks",
        fail_background_restart,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        status = await _wait_for_apply_state(client, "failed")

    assert response.status_code == 202
    assert "background restart failure" in status["error"]
    assert load_config(config_path).llm.openai.model == "gpt-4o-mini"
    assert app.state.runtime_context.config.llm.openai.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_put_config_explains_blank_hot_reload_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async def timeout_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise TimeoutError

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", timeout_rebuild)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        status = await _wait_for_apply_state(client, "failed")

    assert response.status_code == 202
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is False
    assert "后台对话在 25 分钟内仍未整理完成" in status["message"]
    assert "热重载失败（）" not in status["message"]


@pytest.mark.asyncio
async def test_put_config_hot_reload_failure_file_log_keeps_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    configure_logging(
        Config(
            logging=LoggingConfig(
                level="INFO",
                file_level="DEBUG",
                directory=str(log_dir),
                filename="app.log",
                max_file_size_mb=0,
                backup_count=1,
            )
        )
    )
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated hot reload crash")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        await _wait_for_apply_state(client, "failed")

    assert response.status_code == 202
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()
    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "Queued config hot-reload failed" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: simulated hot reload crash" in text


@pytest.mark.asyncio
async def test_put_config_reports_failed_when_background_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    before = config_path.read_bytes()
    save_calls = 0

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    def fail_second_save(config: Config, path: Path | None = None) -> Path:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("restore denied")
        return save_config(config, path)

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)
    monkeypatch.setattr(
        "openbiliclaw.config.save_config",
        fail_second_save,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        status = await _wait_for_apply_state(client, "failed")

    assert response.status_code == 202
    assert "无法恢复最后一次已生效配置" in status["message"]
    assert status["error"] == "restore denied"
    assert config_path.read_bytes() != before


@pytest.mark.asyncio
async def test_put_config_serializes_concurrent_saves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    first_cfg = _valid_config()
    first_cfg.llm.openai.model = "gpt-4o-mini"
    save_config(first_cfg, config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first, second = await asyncio.gather(
            client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}}),
            client.put("/api/config", json={"llm": {"openai": {"model": "gpt-5-mini"}}}),
        )
        status = await _wait_for_apply_state(client, "applied")

    assert first.status_code == 202
    assert second.status_code == 202
    assert status["applied_revision"] == second.json()["apply_revision"]
    assert load_config(config_path).llm.openai.model == "gpt-5-mini"
    assert load_config(tmp_path / "config.toml.bak").llm.openai.model == "gpt-4.1-mini"


@pytest.mark.asyncio
async def test_busy_dialogue_queues_config_and_latest_revision_wins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    monkeypatch.setattr(app.state.auth_gate, "is_trusted_local", lambda _request: True)
    coordinator = app.state.dialogue_execution_coordinator
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_dialogue_lane() -> None:
        async with coordinator.lease():
            entered.set()
            await release.wait()

    owner = asyncio.create_task(hold_dialogue_lane())
    await asyncio.wait_for(entered.wait(), timeout=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url="http://testserver",
    ) as client:
        first = await asyncio.wait_for(
            client.put(
                "/api/config",
                json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
            ),
            timeout=1,
        )
        second = await asyncio.wait_for(
            client.put(
                "/api/config",
                json={"llm": {"openai": {"model": "gpt-5-mini"}}},
            ),
            timeout=1,
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["apply_state"] == "queued"
        assert second.json()["apply_revision"] > first.json()["apply_revision"]
        assert load_config(config_path).llm.openai.model == "gpt-5-mini"
        init_response = await client.post("/api/init", json={"sources": ["bilibili"]})
        assert init_response.status_code == 409
        assert init_response.json()["error"] == "config_applying"

        release.set()
        await owner
        for _ in range(200):
            status = (await client.get("/api/config/apply-status")).json()
            if status["state"] == "applied":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("queued config revision did not apply")

    assert status["requested_revision"] == second.json()["apply_revision"]
    assert status["applied_revision"] == second.json()["apply_revision"]
    assert app.state.runtime_context.config.llm.openai.model == "gpt-5-mini"


@pytest.mark.asyncio
async def test_queued_config_failure_restores_last_applied_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    coordinator = app.state.dialogue_execution_coordinator
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_dialogue_lane() -> None:
        async with coordinator.lease():
            entered.set()
            await release.wait()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("queued rebuild failed")

    owner = asyncio.create_task(hold_dialogue_lane())
    await asyncio.wait_for(entered.wait(), timeout=1)
    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        assert response.status_code == 202
        release.set()
        await owner
        for _ in range(200):
            status = (await client.get("/api/config/apply-status")).json()
            if status["state"] == "failed":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("queued config failure did not settle")

    assert "queued rebuild failed" in status["error"]
    assert load_config(config_path).llm.openai.model == "gpt-4o-mini"
    assert load_config(tmp_path / "config.toml.bak").llm.openai.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_hot_reload_rollback_preserves_persisted_restart_only_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    initial = _valid_config()
    initial.data_dir = str(tmp_path / "active-data")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(initial, config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    next_data = tmp_path / "next-data"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first = await client.put("/api/config", json={"data_dir": str(next_data)})
        assert first.status_code == 202
        assert first.json()["restart_required"] is True
        await _wait_for_apply_state(client, "applied")

        async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
            raise RuntimeError("later hot reload failed")

        monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)
        second = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        assert second.status_code == 202
        await _wait_for_apply_state(client, "failed")

    restored = load_config(config_path)
    assert restored.data_dir == str(next_data)
    assert restored.llm.openai.model == "gpt-4o-mini"


async def test_agent_config_success_is_retained_by_later_failed_settings_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
    from openbiliclaw.agent.tools.config_tools import build_config_tools

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    config_path = tmp_path / "config.toml"
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    ctx = app.state.runtime_context
    ctx.config = load_config(config_path)

    async def rebuild(self: RuntimeContext, candidate: Config) -> None:
        if candidate.llm.openai.model == "gpt-4.1-mini":
            raise RuntimeError("later settings rebuild failed")
        self.config = candidate

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", rebuild)
    tool_context = AgentToolContext(
        config=ctx.config,
        config_persist_hook=lambda cfg: save_config(cfg, config_path),
        config_reload_hook=ctx._request_config_reload,
        config_update_hook=getattr(app.state, "_apply_agent_config_update", None),
    )
    registry = ToolRegistry(build_config_tools(tool_context))
    changed = await registry.dispatch("update_config", {"key": "language", "value": "en-US"})
    assert changed.ok, changed.content
    assert load_config(config_path).language == "en-US"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put(
            "/api/config",
            json={"llm": {"openai": {"model": "gpt-4.1-mini"}}},
        )
        assert response.status_code == 202
        await _wait_for_apply_state(client, "failed")
    assert load_config(config_path).language == "en-US"
    assert ctx.config.language == "en-US"


async def test_agent_config_reload_failure_rolls_back_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
    from openbiliclaw.agent.tools.config_tools import build_config_tools

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    config_path = tmp_path / "config.toml"
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    ctx = app.state.runtime_context
    ctx.config = load_config(config_path)
    original_language = ctx.config.language

    async def rebuild(self: RuntimeContext, candidate: Config) -> None:
        self.config = candidate
        if candidate.language == "en-US":
            raise RuntimeError("agent reload failed after publication")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", rebuild)
    tool_context = AgentToolContext(
        config=ctx.config,
        config_persist_hook=lambda cfg: save_config(cfg, config_path),
        config_reload_hook=ctx._request_config_reload,
        config_update_hook=getattr(app.state, "_apply_agent_config_update", None),
    )
    changed = await ToolRegistry(build_config_tools(tool_context)).dispatch(
        "update_config",
        {"key": "language", "value": "en-US"},
    )
    assert not changed.ok
    assert load_config(config_path).language == original_language
    assert ctx.config.language == original_language
    assert tool_context.config.language == original_language


async def test_agent_config_waits_for_handoff_without_mutating_live_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.agent.tools import AgentToolContext, ToolRegistry
    from openbiliclaw.agent.tools.config_tools import build_config_tools

    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    config_path = tmp_path / "config.toml"
    save_config(_valid_config(), config_path)
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    ctx = app.state.runtime_context
    ctx.config = load_config(config_path)
    initial_language = ctx.config.language
    entered = asyncio.Event()
    release = asyncio.Event()

    async def rebuild(self: RuntimeContext, candidate: Config) -> None:
        entered.set()
        await release.wait()
        self.config = candidate

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", rebuild)
    tool_context = AgentToolContext(
        config=ctx.config,
        config_update_hook=app.state._apply_agent_config_update,
    )
    task = asyncio.create_task(
        ToolRegistry(build_config_tools(tool_context)).dispatch(
            "update_config",
            {"key": "language", "value": "en-US"},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert ctx.config.language == initial_language
        assert tool_context.config.language == initial_language
        assert load_config(config_path).language == "en-US"
        assert not task.done()
    finally:
        release.set()
    result = await task
    assert result.ok, result.content
    assert ctx.config.language == "en-US"


async def test_config_apply_reports_which_execution_lane_is_draining(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(_valid_config(), tmp_path / "config.toml")
    app = create_app(memory_manager=object(), database=object(), soul_engine=object())
    feedback_entered = asyncio.Event()
    release_feedback = asyncio.Event()
    dialogue_entered = asyncio.Event()
    release_dialogue = asyncio.Event()
    rebuild_entered = asyncio.Event()
    release_rebuild = asyncio.Event()

    async def pause_feedback() -> None:
        feedback_entered.set()
        await release_feedback.wait()

    async def hold_dialogue() -> None:
        async with app.state.dialogue_execution_coordinator.lease():
            dialogue_entered.set()
            await release_dialogue.wait()

    async def rebuild(self: RuntimeContext, candidate: Config) -> None:
        rebuild_entered.set()
        await release_rebuild.wait()
        self.config = candidate

    monkeypatch.setattr(app.state.feedback_batch_scheduler, "pause_and_drain", pause_feedback)
    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", rebuild)
    dialogue_task = asyncio.create_task(hold_dialogue())
    await dialogue_entered.wait()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.put("/api/config", json={"language": "en-US"})
        assert response.status_code == 202
        try:
            await asyncio.wait_for(feedback_entered.wait(), timeout=1)
            status = (await client.get("/api/config/apply-status")).json()
            assert status["state"] == "applying"
            assert "画像/反馈" in status["message"]
            assert "仍可继续聊天" in status["message"]
            assert not app.state.dialogue_execution_coordinator.paused

            release_feedback.set()
            for _ in range(100):
                status = (await client.get("/api/config/apply-status")).json()
                if "当前对话" in status["message"]:
                    break
                await asyncio.sleep(0.01)
            assert "当前对话" in status["message"]
            assert not rebuild_entered.is_set()

            release_dialogue.set()
            await asyncio.wait_for(rebuild_entered.wait(), timeout=1)
            status = (await client.get("/api/config/apply-status")).json()
            assert "对话学习任务" in status["message"]
            assert status["state"] == "applying"
        finally:
            release_feedback.set()
            release_dialogue.set()
            release_rebuild.set()
            await dialogue_task
        await _wait_for_apply_state(client, "applied")
