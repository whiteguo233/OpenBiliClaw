"""Exercise the shared real-visibility protocol with deterministic browser seams."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_shared_director_client_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/js/director-client.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
