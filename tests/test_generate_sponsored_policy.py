"""Tests for the unsigned sponsored policy generator CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "generate_sponsored_policy.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_generator_writes_signed_ready_policy(tmp_path: Path) -> None:
    output = tmp_path / "sponsored-policy.json"
    result = _run(
        "--output",
        str(output),
        "--policy-version",
        "3",
        "--generated-at",
        "2026-09-22T00:00:00Z",
        "--expires-at",
        "2026-12-21T00:00:00Z",
        "--core-min",
        "0.4.0",
        "--core-max",
        "0.4.*",
    )
    assert result.returncode == 0, result.stderr

    policy = json.loads(output.read_text(encoding="utf-8"))
    assert policy["policy_version"] == 3
    assert policy["keys"] == []
    assert policy["core"] == {"min": "0.4.0", "max": "0.4.*"}
    assert set(policy["tasks"]) == {"soul.consolidation.v1"}
    assert (
        policy["tasks"]["soul.consolidation.v1"]["model"]
        == "deepseek-ai/DeepSeek-V3.2"
    )


def test_generator_check_ignores_timestamps(tmp_path: Path) -> None:
    output = tmp_path / "sponsored-policy.json"
    assert (
        _run(
            "--output",
            str(output),
            "--generated-at",
            "2026-09-22T00:00:00Z",
            "--expires-at",
            "2026-12-21T00:00:00Z",
        ).returncode
        == 0
    )

    result = _run(
        "--check",
        "--output",
        str(output),
        "--generated-at",
        "2027-01-01T00:00:00Z",
        "--expires-at",
        "2027-04-01T00:00:00Z",
    )
    assert result.returncode == 0, result.stderr


def test_generator_check_fails_on_policy_drift(tmp_path: Path) -> None:
    output = tmp_path / "sponsored-policy.json"
    assert (
        _run(
            "--output",
            str(output),
            "--generated-at",
            "2026-09-22T00:00:00Z",
            "--expires-at",
            "2026-12-21T00:00:00Z",
        ).returncode
        == 0
    )

    policy = json.loads(output.read_text(encoding="utf-8"))
    policy["tasks"]["soul.consolidation.v1"]["system_prompt_sha256"] = "sha256:tampered"
    output.write_text(json.dumps(policy), encoding="utf-8")

    result = _run("--check", "--output", str(output))
    assert result.returncode == 2
    assert "out of date" in result.stderr


def test_generator_check_fails_when_missing(tmp_path: Path) -> None:
    result = _run("--check", "--output", str(tmp_path / "missing.json"))
    assert result.returncode == 2
    assert "missing policy" in result.stderr
