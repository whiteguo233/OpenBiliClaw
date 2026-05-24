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


def test_put_config_success_saves_snapshot_then_hot_reloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reloaded"] is True
    assert body["rollback_applied"] is False
    assert body["restart_required"] is False
    assert load_config(config_path).llm.openai.model == "gpt-4.1-mini"
    assert (tmp_path / "config.toml.bak").read_bytes() == before


def test_put_config_rolls_back_when_hot_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is False
    assert body["rollback_applied"] is True
    assert "simulated" in body["message"]
    assert config_path.read_bytes() == before
    assert (tmp_path / "config.toml.bak").read_bytes() == before


def test_put_config_hot_reload_failure_file_log_keeps_traceback(
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
    client = _make_client(monkeypatch, tmp_path, _valid_config())

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated hot reload crash")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 200
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            handler.flush()
    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "Config hot-reload failed" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: simulated hot reload crash" in text


def test_put_config_returns_500_when_rollback_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    client = _make_client(monkeypatch, tmp_path, _valid_config())
    before = config_path.read_bytes()

    async def fail_rebuild(self: RuntimeContext, new_config: Config) -> None:  # noqa: ARG001
        raise RuntimeError("simulated")

    def fail_restore(*_args: object, **_kwargs: object) -> None:
        raise OSError("restore denied")

    monkeypatch.setattr(RuntimeContext, "rebuild_from_config", fail_rebuild)
    monkeypatch.setattr(
        "openbiliclaw.api.app._restore_config_snapshot",
        fail_restore,
        raising=False,
    )

    response = client.put("/api/config", json={"llm": {"openai": {"model": "gpt-4.1-mini"}}})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "config_persistence_corrupted"
    assert "config.toml.bak" in body["manual_recovery"]
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

    assert first.status_code == 200
    assert second.status_code == 200
    assert load_config(config_path).llm.openai.model == "gpt-5-mini"
    assert load_config(tmp_path / "config.toml.bak").llm.openai.model == "gpt-4.1-mini"
