"""Run the mobile source-login navigation regressions under pytest/CI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_mobile_source_login_navigation_js() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for mobile source-login JS tests")
    result = subprocess.run(
        [
            node,
            "--test",
            "tests/js/mobile-custom-sources.test.mjs",
            "tests/js/source-login.test.mjs",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
