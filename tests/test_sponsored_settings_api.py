"""The free-model settings API never accepts executable paths or exposes credentials."""

from __future__ import annotations

import shlex
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.config import load_config
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings

from .test_jev_evaluation_config import _client, _configured
from .test_sponsored_user_flow import COMMAND


def configured() -> Any:
    cfg = _configured()
    cfg.llm.sponsored = SponsoredRuntimeSettings(runtime_path=shlex.join(COMMAND))
    return cfg


def test_free_selection_roundtrips_without_mutating_user_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = configured()
    existing_key = cfg.llm.deepseek.api_key
    client = _client(monkeypatch, tmp_path, cfg)
    for model in ("Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B"):
        response = client.put(
            "/api/config", json={"llm": {"sponsored": {"enabled": True, "model": model}}}
        )
        assert response.status_code == 202, response.text
        saved = load_config(tmp_path / "config.toml", consult_environment=False)
        assert saved.llm.sponsored.model == model
        assert saved.llm.deepseek.api_key == existing_key
        read = client.get("/api/config").json()["llm"]["sponsored"]
        assert read["model"] == model and read["runtime_available"]
        assert read["embedding_model"] == "BAAI/bge-m3"
        assert not read["fallback_to_user_provider"]
        assert "runtime_path" not in read
        assert read["context_window"] == (131072 if model.endswith("8B") else 262144)


@pytest.mark.parametrize(
    "update",
    [
        {"runtime_path": "untrusted command"},
        {"model": "unapproved/model"},
        {"enabled": "false"},
        {"fallback_to_user_provider": 1},
        {"request_timeout_seconds": True},
        {"api_key": "must-not-be-accepted"},
    ],
)
def test_invalid_free_settings_are_rejected_before_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, update: dict[str, object]
) -> None:
    client = _client(monkeypatch, tmp_path, configured())
    before = (tmp_path / "config.toml").read_bytes()
    response = client.put("/api/config", json={"llm": {"sponsored": update}})
    assert response.status_code == 400
    assert (tmp_path / "config.toml").read_bytes() == before
    assert "untrusted command" not in response.text and "must-not-be-accepted" not in response.text


@pytest.mark.parametrize("model", ["Qwen/Qwen3-8B", "Qwen/Qwen3.5-4B"])
def test_draft_probe_checks_exact_selected_model_and_bge_without_saving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str
) -> None:
    from openbiliclaw.llm.concurrency import LLMConcurrencyGate

    admissions = []
    original_slot = LLMConcurrencyGate.slot

    @asynccontextmanager
    async def tracked_slot(self: Any, *, caller: str, **kwargs: Any) -> Any:
        async with original_slot(self, caller=caller, **kwargs):
            admissions.append(caller)
            yield

    monkeypatch.setattr(LLMConcurrencyGate, "slot", tracked_slot)
    client = _client(monkeypatch, tmp_path, configured())
    before = (tmp_path / "config.toml").read_bytes()
    result = client.post(
        "/api/config/probe-service",
        json={
            "kind": "sponsored",
            "config": {"llm": {"sponsored": {"enabled": True, "model": model}}},
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["ok"] and result.json()["model"] == model
    assert "BGE-M3" in result.json()["message"]
    assert admissions == ["api.config_probe"]
    assert (tmp_path / "config.toml").read_bytes() == before
