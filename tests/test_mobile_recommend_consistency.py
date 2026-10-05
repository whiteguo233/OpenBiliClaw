"""Execute mobile recommendation event ordering and transport regressions."""

import shutil
import subprocess

import pytest


def test_mobile_recommendation_consistency_runtime() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    result = subprocess.run(
        [node, "--test", "tests/js/mobile-recommend-consistency.test.mjs"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
