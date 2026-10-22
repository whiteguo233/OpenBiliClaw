"""Execute native collection readers without launching any browser or host."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["relative", "self", "ordinary", "explicit_scope"])
def test_native_collection_resolves_item_relative_fields(mode: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the native DOM reader fixture")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/fixtures/ego_collect_dom.mjs"),
            str(root / "src/openbiliclaw/sources/ego_dom.mjs"),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mode": mode, "count": 1, "unchanged_plan": True}
