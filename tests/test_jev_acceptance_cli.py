"""The acceptance CLI reads saved Jev settings without touching production data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.jev_fake_backend import FakeJevBackend

from openbiliclaw.config import Config, JevContextLimits, load_config, save_config

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    variables = dict(os.environ if env is None else env)
    variables["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_jev_single_pass.py"), *args],
        cwd=ROOT,
        env=variables,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def configured(path: Path, endpoint: str, model: str) -> None:
    cfg = Config()
    cfg.data_dir = str(path.parent / "must-not-be-used")
    cfg.decision.providers.jev.enabled = True
    cfg.decision.providers.jev.api_key = "synthetic-test-key"
    cfg.decision.providers.jev.endpoint_url = endpoint
    cfg.decision.providers.jev.model = model
    cfg.decision.providers.jev.context_limits = {model: JevContextLimits(40_000, 20_000)}
    cfg.discovery.eval_scorer = "jev"
    save_config(cfg, path)


def test_live_cli_uses_saved_endpoint_token_exact_model_and_capacity(tmp_path: Path) -> None:
    model = "synthetic-jev-small-v1"
    with FakeJevBackend(
        model=model, max_request_bytes=30_000, max_state_question_bytes=15_000
    ) as backend:
        path = tmp_path / "connection.toml"
        configured(path, backend.url, model)
        original = path.read_bytes()
        env = dict(os.environ, TYPESAFE_API_KEY="wrong-environment-token")
        result = run_cli("--live", "--confirm-free-credits", "--config", str(path), env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["status"] == "passed"
        assert report["backend"] == "configured"
        assert report["usage_is_synthetic"] is None  # Configured services are not attested.
        assert report["model"] == model
        assert report["context_budget"]["max_state_question_tokens"] == 20_000
        assert all(case["pipeline"]["cached"] == 3 for case in report["cases"])
        assert 0 < len(backend.requests) <= 6
        assert all(request["model"] == model for request in backend.requests)
        assert all(row["max_state_question_bytes"] <= 15_000 for row in backend.records)
        assert "synthetic-test-key" not in result.stdout + result.stderr
        assert "wrong-environment-token" not in result.stdout + result.stderr
        assert path.read_bytes() == original
        assert not (tmp_path / "must-not-be-used").exists()


@pytest.mark.parametrize(
    "scenario",
    ["confirmation", "disabled", "missing_key", "model_mismatch", "unknown_capacity", "fake"],
)
def test_invalid_preconditions_do_not_send_requests_or_disclose_settings(
    tmp_path: Path, scenario: str
) -> None:
    with FakeJevBackend() as backend:
        path = tmp_path / "connection.toml"
        configured(path, backend.url, "jev-1.13.0")
        cfg = load_config(path, consult_environment=False)
        cfg.discovery.eval_scorer = "llm"
        args = ["--live", "--confirm-free-credits", "--config", str(path)]
        env = {key: value for key, value in os.environ.items() if key != "TYPESAFE_API_KEY"}
        if scenario == "confirmation":
            args.remove("--confirm-free-credits")
        elif scenario == "disabled":
            cfg.decision.providers.jev.enabled = False
        elif scenario == "missing_key":
            cfg.decision.providers.jev.api_key = ""
        elif scenario == "model_mismatch":
            args.extend(["--model", "jev-1.14.0"])
        elif scenario == "unknown_capacity":
            cfg.decision.providers.jev.model = "synthetic-unknown-context"
            cfg.decision.providers.jev.context_limits = {}
        elif scenario == "fake":
            args[0] = "--fake"
        save_config(cfg, path)
        original = path.read_bytes()
        result = run_cli(*args, env=env)
        assert result.returncode == 2, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "not_attempted"
        assert backend.requests == []
        assert "synthetic-test-key" not in result.stdout + result.stderr
        assert path.read_bytes() == original
        assert not (tmp_path / "must-not-be-used").exists()


def test_preview_reads_model_capacity_but_never_contacts_saved_endpoint(tmp_path: Path) -> None:
    with FakeJevBackend(model="synthetic-preview-v1") as backend:
        path = tmp_path / "connection.toml"
        configured(path, backend.url, backend.model)
        result = run_cli("--config", str(path))
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["status"] == "preview" and report["live"] is False
        assert report["model"] == backend.model
        assert report["context_budget"]["max_state_question_bytes"] == 15_000
        assert backend.requests == []


def test_saved_named_environment_key_is_used_when_no_token_is_stored(tmp_path: Path) -> None:
    with FakeJevBackend() as backend:
        path = tmp_path / "connection.toml"
        configured(path, backend.url, backend.model)
        cfg = load_config(path, consult_environment=False)
        cfg.decision.providers.jev.api_key = ""
        cfg.decision.providers.jev.api_key_env = "TEST_ACCEPTANCE_JEV_TOKEN"
        save_config(cfg, path)
        result = run_cli(
            "--live",
            "--confirm-free-credits",
            "--config",
            str(path),
            env=dict(
                os.environ,
                TYPESAFE_API_KEY="wrong-default-token",
                TEST_ACCEPTANCE_JEV_TOKEN="synthetic-test-key",
            ),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "passed"
        assert len(backend.requests) == 4
        assert load_config(path, consult_environment=False).decision.providers.jev.api_key == ""


def test_live_validation_stops_on_first_failure_and_disables_configured_retries(
    tmp_path: Path,
) -> None:
    with FakeJevBackend(mode="server_error") as backend:
        path = tmp_path / "connection.toml"
        configured(path, backend.url, backend.model)
        cfg = load_config(path, consult_environment=False)
        cfg.decision.providers.jev.max_attempts = 3
        save_config(cfg, path)
        result = run_cli("--live", "--confirm-free-credits", "--config", str(path))
        assert result.returncode == 1, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["status"] == "incomplete"
        assert len(backend.requests) == 1
        assert len(report["cases"]) == 1
        assert report["cases"][0]["pipeline"]["cached"] == 0
        assert report["cases"][0]["pending"] == 3
        assert load_config(path, consult_environment=False).decision.providers.jev.max_attempts == 3
