"""Human takeover frames keep the native host's exact network boundaries."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.sources import ego_executor


@pytest.mark.parametrize("mode", ["interactive", "background"])
def test_embedded_login_uses_owned_sessions_and_shared_request_guards(mode):
    node = shutil.which("node")
    assert node is not None, "Node is required to verify the native host protocol"
    with tempfile.TemporaryDirectory(prefix="ego-frames-", dir="/tmp") as directory:
        result = subprocess.run(
            [
                node,
                str(Path(__file__).parent / "fixtures" / "ego_embedded_login.mjs"),
                str(Path(ego_executor.__file__).with_name("ego_host.mjs")),
                directory,
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert result.returncode == 0, result.stderr
    assert "embedded login boundary passed" in result.stdout
