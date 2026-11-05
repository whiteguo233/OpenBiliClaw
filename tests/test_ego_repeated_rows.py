"""Authoring evidence must describe a list beyond a single item's identity."""

import shutil
import subprocess
from pathlib import Path

from openbiliclaw.sources import ego_executor


def test_repeated_rows_keep_scope_without_pinning_one_topic() -> None:
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [
            node,
            str(Path(__file__).parent / "fixtures" / "ego_repeated_rows.mjs"),
            str(Path(ego_executor.__file__).with_name("ego_dom.mjs")),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
