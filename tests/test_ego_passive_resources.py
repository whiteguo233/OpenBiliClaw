"""Background extraction avoids passive assets; interactive policy stays intact."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from openbiliclaw.sources import ego_executor


@pytest.mark.parametrize("mode", ["background", "interactive"])
def test_native_passive_resources_preserve_text_loading_and_network_limits(mode):
    node = shutil.which("node")
    assert node is not None, "Node is required to verify the native host protocol"
    with tempfile.TemporaryDirectory(prefix="ego-assets-", dir="/tmp") as directory:
        result = subprocess.run(
            [
                node,
                str(Path(__file__).parent / "fixtures" / "ego_passive_resources.mjs"),
                str(Path(ego_executor.__file__).with_name("ego_host.mjs")),
                directory,
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr
    assert "passive resource policy passed" in result.stdout
