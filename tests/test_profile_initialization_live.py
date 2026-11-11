"""The opt-in real acceptance must distinguish bounded sampling from failure."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

validate = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/profile_initialization_live.py")
)["validate_completed_stages"]


def _status() -> dict:
    return {
        "stages": [{"status": "warning", "reason": "github_partial"}] + [{"status": "ok"}] * 3,
        "partial_success": True,
        "reason": "github_partial",
    }


def test_live_acceptance_keeps_expected_sample_cap_warning() -> None:
    validate(_status(), platform="github", collected=40, sample_limit=40)


@pytest.mark.parametrize("kind", ["discovery", "model", "short_collection", "unexpected_warning"])
def test_live_acceptance_rejects_unfulfilled_stages(kind: str) -> None:
    status = _status()
    count = 40
    if kind == "discovery":
        status["stages"][3] = {"status": "warning", "reason": "discovery_partial"}
    elif kind == "model":
        status["stages"][2] = {"status": "failed"}
    elif kind == "short_collection":
        count = 39
    else:
        status["reason"] = "discovery_timeout"
    with pytest.raises(AssertionError):
        validate(status, platform="github", collected=count, sample_limit=40)
