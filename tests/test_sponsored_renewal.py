"""Revoked built-in credentials remain actionable; local vectors remain independent."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from openbiliclaw.config import Config, load_config, save_config
from openbiliclaw.diagnostics_alerts import (
    get_diagnostics_alert_buffer,
    reset_diagnostics_alert_buffer,
)
from openbiliclaw.llm.base import LLMRegistry
from openbiliclaw.llm.registry import build_embedding_service
from openbiliclaw.llm.sponsored_embedding import select_local_embedding
from openbiliclaw.llm.sponsored_errors import SponsoredError
from openbiliclaw.llm.sponsored_profiles import approved_registry
from openbiliclaw.llm.sponsored_provider import SponsoredProvider
from openbiliclaw.runtime import sponsored_bundle as bundle
from openbiliclaw.runtime.ollama_supervisor import effective_ollama_endpoint, ollama_required
from openbiliclaw.sponsored_runtime_state import SponsoredRuntimeSettings, reset_sponsored_settings
from openbiliclaw.sponsored_status import (
    configure_status_directory,
    record_sponsored_outcome,
    sponsored_status,
)

from .test_jev_evaluation_config import _client
from .test_sponsored_bundle import signed_bundle
from .test_sponsored_settings_api import configured
from .test_sponsored_user_flow import COMMAND, MESSAGES

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_status() -> Any:
    reset_sponsored_settings()
    reset_diagnostics_alert_buffer()
    yield
    reset_sponsored_settings()
    reset_diagnostics_alert_buffer()


def config_at(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.data_dir = str(tmp_path / "data")
    cfg.llm.sponsored = SponsoredRuntimeSettings(enabled=True, runtime_path=shlex.join(COMMAND))
    configure_status_directory(cfg.data_path)
    return cfg


@pytest.mark.parametrize("operation", ["complete", "stream", "embedding"])
async def test_revocation_is_observed_at_real_ipc_boundary(tmp_path: Path, operation: str) -> None:
    cfg = config_at(tmp_path)
    async with SponsoredProvider(
        COMMAND,
        registry=approved_registry(),
        fallback_to_other_free_model=False,
        env={"SPONSORED_MOCK_ERROR_CODE": "AUTH_FAILED"},
    ) as sdk:
        with pytest.raises(SponsoredError, match="最新的 OpenBiliClaw"):
            if operation == "stream":
                _ = [chunk async for chunk in sdk.stream_complete(MESSAGES)]
            elif operation == "embedding":
                await sdk.embedding("测试向量")
            else:
                await sdk.complete(MESSAGES)
    notice = sponsored_status(cfg)["notices"][0]
    assert notice["code"] == "AUTH_FAILED" and notice["requires_update"]
    assert notice["capabilities"] == (["embedding"] if operation == "embedding" else ["chat"])
    assert "自己的 API Key" in notice["message"]
    # Reloading/binding the installation again must not erase the notice.
    configure_status_directory(None)
    configure_status_directory(cfg.data_path)
    assert sponsored_status(cfg)["notices"] == [notice]


@pytest.mark.parametrize(
    "code", ["RATE_LIMITED", "UPSTREAM_ERROR", "UNAVAILABLE", "PROTOCOL_ERROR"]
)
def test_transient_failures_neither_claim_expiry_nor_erase_revocation(
    tmp_path: Path, code: str
) -> None:
    cfg = config_at(tmp_path)
    record_sponsored_outcome(COMMAND, capability="chat", code=code)
    assert not sponsored_status(cfg)["notices"]
    record_sponsored_outcome(COMMAND, capability="chat", code="AUTH_FAILED")
    record_sponsored_outcome(COMMAND, capability="chat", code=code)
    assert sponsored_status(cfg)["notices"][0]["code"] == "AUTH_FAILED"


def test_notices_coalesce_and_recovery_is_scoped_to_the_actual_route(tmp_path: Path) -> None:
    cfg = config_at(tmp_path)
    for _ in range(5):
        record_sponsored_outcome(COMMAND, capability="chat", code="AUTH_FAILED")
    assert len(get_diagnostics_alert_buffer().snapshot()["alerts"]) == 1
    record_sponsored_outcome(COMMAND, capability="embedding", code="AUTH_FAILED")
    record_sponsored_outcome(COMMAND, capability="embedding")
    assert sponsored_status(cfg)["notices"][0]["capabilities"] == ["chat"]
    record_sponsored_outcome(COMMAND, capability="chat")
    assert not sponsored_status(cfg)["notices"]
    record_sponsored_outcome(COMMAND, capability="embedding", code="AUTH_FAILED")
    cfg.llm.sponsored = replace(cfg.llm.sponsored, embedding_mode="local")
    assert not sponsored_status(cfg)["notices"]
    cfg.llm.sponsored = replace(cfg.llm.sponsored, embedding_mode="cloud", enabled=False)
    assert not sponsored_status(cfg)["notices"]
    cfg.llm.sponsored = replace(cfg.llm.sponsored, enabled=True, runtime_path="/new/runtime")
    assert not sponsored_status(cfg)["notices"]


def test_quota_notice_does_not_prescribe_an_update(tmp_path: Path) -> None:
    cfg = config_at(tmp_path)
    record_sponsored_outcome(COMMAND, capability="chat", code="QUOTA_EXCEEDED")
    notice = sponsored_status(cfg)["notices"][0]
    assert not notice["requires_update"]
    assert "不是凭据过期" in notice["message"]


async def test_expired_policy_before_handshake_remains_an_update_error(tmp_path: Path) -> None:
    cfg = config_at(tmp_path)
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write("
        "'obc-sponsored-runtime: POLICY_INVALID: policy expired\\n'); sys.exit(1)",
    ]
    cfg.llm.sponsored = replace(cfg.llm.sponsored, runtime_path=shlex.join(command))
    sdk = SponsoredProvider(command, registry=approved_registry())
    try:
        with pytest.raises(SponsoredError) as error:
            _ = [chunk async for chunk in sdk.stream_complete(MESSAGES)]
        assert error.value.code == "POLICY_INVALID"
        assert sponsored_status(cfg)["notices"][0]["requires_update"]
    finally:
        await sdk.aclose()


def test_signed_expired_installation_is_visible_without_executing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "bundle"
    catalog, key = signed_bundle(directory)
    policy = json.loads((directory / "sponsored-policy.json").read_text())
    policy["expires_at"] = "2020-01-01T00:00:00Z"
    raw = json.dumps(policy).encode()
    (directory / "sponsored-policy.json").write_bytes(raw)
    (directory / "sponsored-policy.sig").write_bytes(key.sign(raw))
    manifest = json.loads((directory / "manifest.json").read_text())
    for filename in manifest["files"]:
        manifest["files"][filename] = hashlib.sha256(
            (directory / filename).read_bytes()
        ).hexdigest()
    raw = json.dumps(manifest).encode()
    (directory / "manifest.json").write_bytes(raw)
    (directory / "manifest.sig").write_bytes(key.sign(raw))
    monkeypatch.setattr(bundle, "_catalog", lambda: catalog)
    monkeypatch.setattr(bundle, "_installed_candidates", lambda: [directory])
    cfg = config_at(tmp_path)
    cfg.llm.sponsored = replace(cfg.llm.sponsored, runtime_path="")
    assert not bundle.installed_runtime_command()
    assert bundle.bundle_status()["code"] == "POLICY_INVALID"
    assert sponsored_status(cfg)["notices"][0]["code"] == "POLICY_INVALID"
    # A corrupted policy must not be called a verified expired policy.
    (directory / "sponsored-policy.json").write_text("corrupted")
    assert not sponsored_status(cfg)["notices"]


def test_local_embedding_is_independent_and_uses_a_distinct_cache_space(tmp_path: Path) -> None:
    cfg = config_at(tmp_path)
    cloud = build_embedding_service(cfg, LLMRegistry())
    assert cloud is not None and cloud.embedding_provider.endswith(".SponsoredEmbeddingAdapter")
    select_local_embedding(cfg)
    cfg.llm.sponsored = replace(cfg.llm.sponsored, embedding_mode="local")
    local = build_embedding_service(cfg, LLMRegistry())
    assert local is not None and local.embedding_provider.endswith(".OllamaEmbeddingProvider")
    assert local.embedding_model == "bge-m3"
    assert local.embedding_fingerprint != cloud.embedding_fingerprint
    assert ollama_required(cfg)
    target = save_config(cfg, tmp_path / "config.toml")
    loaded = load_config(target, consult_environment=False)
    assert loaded.llm.sponsored.embedding_mode == "local"
    cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
    cfg.llm.embedding.model = "my-local-model"
    select_local_embedding(cfg)
    assert cfg.llm.embedding.base_url.endswith(":11435/v1")
    assert cfg.llm.embedding.model == "my-local-model"
    cfg.llm.ollama.model = "old-chat-model"
    assert effective_ollama_endpoint(cfg) == "http://127.0.0.1:11435"


def test_api_saves_local_choice_and_preserves_existing_private_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = configured()
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.model = "bge-m3"
    cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.put(
        "/api/config", json={"llm": {"sponsored": {"enabled": True, "embedding_mode": "local"}}}
    )
    assert response.status_code == 202, response.text
    saved = load_config(tmp_path / "config.toml", consult_environment=False)
    assert saved.llm.sponsored.embedding_mode == "local"
    assert saved.llm.embedding.base_url.endswith(":11435/v1")
    assert client.get("/api/config").json()["llm"]["sponsored"]["embedding_model"] == "bge-m3"


@pytest.mark.parametrize("mode", ["invalid", [], True])
def test_api_rejects_invalid_embedding_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: Any
) -> None:
    client = _client(monkeypatch, tmp_path, configured())
    assert (
        client.put("/api/config", json={"llm": {"sponsored": {"embedding_mode": mode}}}).status_code
        == 400
    )


def test_disabling_free_plan_allows_the_users_own_embedding_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = configured()
    select_local_embedding(cfg)
    cfg.llm.sponsored = replace(cfg.llm.sponsored, enabled=True, embedding_mode="local")
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.put(
        "/api/config",
        json={
            "llm": {
                "sponsored": {"enabled": False, "embedding_mode": "local"},
                "embedding": {
                    "provider": "openai_compatible",
                    "model": "user-embedding",
                    "base_url": "http://127.0.0.1:12345/v1",
                    "api_key": "synthetic-test-key",
                },
            }
        },
    )
    assert response.status_code == 202, response.text
    saved = load_config(tmp_path / "config.toml", consult_environment=False)
    assert not saved.llm.sponsored.enabled
    assert saved.llm.embedding.provider == "openai_compatible"
    assert saved.llm.embedding.model == "user-embedding"


def test_probe_auth_failure_is_visible_in_settings_and_runtime_notices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = configured()
    cfg.llm.sponsored = replace(cfg.llm.sponsored, enabled=True)
    monkeypatch.setenv("SPONSORED_MOCK_ERROR_CODE", "AUTH_FAILED")
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.post("/api/config/probe-service", json={"kind": "sponsored"})
    assert response.status_code == 200
    assert not response.json()["ok"]
    assert response.json()["service_status"]["notices"][0]["requires_update"]
    assert "自己的 API Key" in response.json()["error"]
    assert client.get("/api/config").json()["llm"]["sponsored"]["service_status"]["notices"]
    # Injected placeholder graphs deliberately have no active Config.
    client.app.state.runtime_context.config = load_config(tmp_path / "config.toml")
    assert client.get("/api/runtime-status").json()["sponsored_status"]["notices"]


def test_setup_can_verify_free_chat_before_preparing_local_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def unwanted_cloud_vector(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("local embedding selection must not probe cloud BGE")

    monkeypatch.setattr(SponsoredProvider, "embedding", unwanted_cloud_vector)
    client = _client(monkeypatch, tmp_path, configured())
    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "sponsored",
            "check_embedding": False,
            "config": {"llm": {"sponsored": {"enabled": True, "embedding_mode": "local"}}},
        },
    )
    assert response.json()["ok"], response.text
    assert "初始化时准备" in response.json()["message"]


async def test_local_vectors_still_work_after_free_key_revocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = config_at(tmp_path)
    select_local_embedding(cfg)
    cfg.llm.sponsored = replace(cfg.llm.sponsored, embedding_mode="local")
    record_sponsored_outcome(COMMAND, capability="chat", code="AUTH_FAILED")
    local_embed = AsyncMock(return_value=[0.1] * 1024)
    monkeypatch.setattr("openbiliclaw.llm.ollama_provider.embed_via_native_api", local_embed)
    monkeypatch.setattr(
        SponsoredProvider, "embedding", AsyncMock(side_effect=AssertionError("cloud route used"))
    )
    service = build_embedding_service(cfg, LLMRegistry())
    assert service is not None and await service.probe()
    assert local_embed.await_args.args[:2] == ("http://127.0.0.1:11434/v1", "bge-m3")
    assert sponsored_status(cfg)["notices"][0]["capabilities"] == ["chat"]


def test_free_chat_probe_checks_selected_local_vector_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local_embed = AsyncMock(return_value=[0.1] * 1024)
    monkeypatch.setattr("openbiliclaw.llm.ollama_provider.embed_via_native_api", local_embed)
    monkeypatch.setattr(
        SponsoredProvider, "embedding", AsyncMock(side_effect=AssertionError("cloud route used"))
    )
    client = _client(monkeypatch, tmp_path, configured())
    response = client.post(
        "/api/config/probe-service",
        json={
            "kind": "sponsored",
            "config": {"llm": {"sponsored": {"enabled": True, "embedding_mode": "local"}}},
        },
    )
    assert response.json()["ok"], response.text
    assert "本地 bge-m3" in response.json()["message"]
    local_embed.assert_awaited_once()


@pytest.mark.parametrize("mode", ["cloud", "local"])
def test_cli_free_setup_saves_embedding_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    from openbiliclaw import cli

    cfg = config_at(tmp_path)
    cfg.llm.sponsored = replace(cfg.llm.sponsored, embedding_mode="local")
    cfg.llm.embedding.provider = "ollama"
    cfg.llm.embedding.model = "my-existing-local-model"
    cfg.llm.embedding.base_url = "http://127.0.0.1:11435/v1"
    save_config(cfg, tmp_path / "config.toml")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    answers = iter(["free", "2", "1" if mode == "cloud" else "2"])
    monkeypatch.setattr(cli.typer, "prompt", lambda *_a, **_kw: next(answers))
    probed = []

    def has_model(model: str, *, host: str) -> bool:
        probed.append((model, host))
        return True

    monkeypatch.setattr(cli, "_ollama_has_model", has_model)
    cli._interactive_runtime_config_setup()
    saved = load_config(tmp_path / "config.toml", consult_environment=False)
    selected = saved.llm.instances[saved.llm.default_chain[0]]
    assert selected.model == "Qwen/Qwen3.5-4B"
    assert not saved.llm.sponsored.enabled
    assert saved.llm.embedding.provider == ("siliconflow_free" if mode == "cloud" else "ollama")
    assert probed == (
        [] if mode == "cloud" else [("my-existing-local-model", "http://127.0.0.1:11435")]
    )
