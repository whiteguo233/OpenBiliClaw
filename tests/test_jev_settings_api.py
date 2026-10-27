"""Settings API persistence and secret handling; no external Jev requests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from scripts.jev_fake_backend import FakeJevBackend

from openbiliclaw.config import load_config
from openbiliclaw.decision.registry import build_decision_provider
from openbiliclaw.discovery.jev_evaluator import JevContentEvaluator

from .test_jev_evaluation_config import _client, _configured
from .test_jev_full_evaluator import ITEM

if TYPE_CHECKING:
    from pathlib import Path


def test_can_save_jev_connection_and_switch_full_evaluation_in_one_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _configured()
    monkeypatch.delenv(cfg.decision.providers.jev.api_key_env, raising=False)
    client = _client(monkeypatch, tmp_path, cfg)
    secret = "synthetic-ui-token-must-not-return"
    response = client.put(
        "/api/config",
        json={
            "decision": {
                "providers": {
                    "jev": {
                        "enabled": True,
                        "endpoint_url": "https://jev.example.test/v1/systemone",
                        "api_key": secret,
                        "model": "jev-1.14.0",
                        "context_limits": {
                            "jev-1.14.0": {
                                "max_request_tokens": 40_000,
                                "max_state_question_tokens": 20_000,
                            }
                        },
                    }
                }
            },
            "discovery": {"eval_scorer": "jev"},
        },
    )
    assert response.status_code == 202, response.text
    assert secret not in response.text
    saved = load_config(tmp_path / "config.toml", consult_environment=False)
    assert saved.discovery.eval_scorer == "jev"
    assert saved.decision.providers.jev.api_key == secret
    assert saved.decision.providers.jev.endpoint_url == "https://jev.example.test/v1/systemone"
    assert secret not in repr(saved.decision)
    for query in ("", "?reveal_keys=true"):
        read = client.get("/api/config" + query)
        assert secret not in read.text
        status = read.json()["discovery"]["jev_provider"]
        assert status["ready"] is True
        assert status["api_key_configured"] is True
        assert status["credential_source"] == "config"
        assert status["endpoint_url"] == saved.decision.providers.jev.endpoint_url
        assert status["context_budget"]["max_state_question_tokens"] == 20_000


def test_blank_token_preserves_and_explicit_clear_is_not_a_mask_echo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _configured()
    cfg.decision.providers.jev.api_key = "synthetic-saved-token"
    cfg.discovery.eval_scorer = "jev"
    monkeypatch.delenv(cfg.decision.providers.jev.api_key_env, raising=False)
    client = _client(monkeypatch, tmp_path, cfg)
    response = client.put(
        "/api/config",
        json={
            "decision": {"providers": {"jev": {"api_key": "", "timeout_seconds": 5}}},
        },
    )
    assert response.status_code == 202
    path = tmp_path / "config.toml"
    assert load_config(path).decision.providers.jev.api_key == "synthetic-saved-token"
    assert path.stat().st_mode & 0o777 == 0o600
    # Clearing a required credential must fail atomically until the route is off.
    before = path.read_bytes()
    response = client.put(
        "/api/config",
        json={
            "decision": {"providers": {"jev": {"clear_api_key": True}}},
        },
    )
    assert response.status_code == 400
    assert path.read_bytes() == before
    response = client.put(
        "/api/config",
        json={
            "decision": {"providers": {"jev": {"clear_api_key": True}}},
            "discovery": {"eval_scorer": "llm"},
        },
    )
    assert response.status_code == 202
    assert load_config(path).decision.providers.jev.api_key == ""
    assert "synthetic-saved-token" not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint_url": "http://external.example/v1/systemone", "api_key": "synthetic-new-key"},
        {
            "endpoint_url": "https://user:password@example.test/v1/systemone",
            "api_key": "synthetic-new-key",
        },
        {
            "endpoint_url": "https://example.test/v1/systemone?token=secret",
            "api_key": "synthetic-new-key",
        },
        {"endpoint_url": "https://changed.example.test/v1/systemone"},
        {"api_key": "synthetic secret"},
        {"api_key": "synthetic****mask"},
        {"api_key": 123},
        {"enabled": "true"},
        {"clear_api_key": "true"},
        {"api_key": "synthetic-new-key", "clear_api_key": True},
        {"api_keey": "synthetic-new-key"},
        {
            "context_limits": {
                "jev-1.13.0": {"max_request_tokens": 128000, "max_state_question_tokens": 64000}
            }
        },
    ],
)
def test_invalid_jev_updates_never_persist_or_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    client = _client(monkeypatch, tmp_path, _configured())
    path = tmp_path / "config.toml"
    before = path.read_bytes()
    response = client.put("/api/config", json={"decision": {"providers": {"jev": changes}}})
    assert response.status_code == 400
    assert path.read_bytes() == before
    assert "synthetic-new-key" not in response.text
    assert "user:password" not in response.text


def test_capacity_table_can_reset_to_auto_without_touching_selected_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _configured()
    cfg.decision.providers.jev.api_key = "synthetic-saved-token"
    client = _client(monkeypatch, tmp_path, cfg)
    for table in (
        {"jev-1.13.0": {"max_request_tokens": 40000, "max_state_question_tokens": 20000}},
        {},
    ):
        response = client.put(
            "/api/config",
            json={
                "decision": {"providers": {"jev": {"context_limits": table}}},
            },
        )
        assert response.status_code == 202
        provider = response.json()["config"]["discovery"]["jev_provider"]
        assert provider["model"] == "jev-1.13.0"
        assert provider["context_limits"] == table
        assert provider["context_budget"]["max_request_tokens"] == (40000 if table else 64000)


def test_saved_connection_drives_real_native_http_without_proxy_or_env_key_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = _configured()
    monkeypatch.setenv(cfg.decision.providers.jev.api_key_env, "wrong-environment-key")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    client = _client(monkeypatch, tmp_path, cfg)
    with FakeJevBackend(model="jev-1.14.0") as backend:
        response = client.put(
            "/api/config",
            json={
                "decision": {
                    "providers": {
                        "jev": {
                            "enabled": True,
                            "endpoint_url": backend.url,
                            "api_key": "synthetic-test-key",
                            "model": "jev-1.14.0",
                            "max_attempts": 1,
                        }
                    }
                },
                "discovery": {"eval_scorer": "jev"},
            },
        )
        assert response.status_code == 202
        saved = load_config(tmp_path / "config.toml", consult_environment=False)

        async def evaluate() -> None:
            provider = build_decision_provider(saved.decision)
            assert provider is not None
            try:
                evaluator = JevContentEvaluator(
                    provider,
                    model=saved.decision.providers.jev.model,
                    config=saved.discovery.jev_evaluation,
                )
                results = await evaluator.evaluate(
                    profile_summary={"disliked_topics": []}, content_items=[ITEM]
                )
                assert results[0] is not None and results[0]["score"] == 0.75
            finally:
                await provider.aclose()

        asyncio.run(evaluate())
        assert len(backend.requests) == 1
        assert backend.requests[0]["model"] == "jev-1.14.0"
        assert len(backend.requests[0]["questions"]) == 10
