"""News headings after a long sidebar must reach bounded Ego observations."""

import shutil
import subprocess
from pathlib import Path

from openbiliclaw.sources import ego_executor


def test_news_headings_and_table_rows_share_the_observation_budget() -> None:
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [
            node,
            str(Path(__file__).parent / "fixtures" / "ego_heading_sampling.mjs"),
            str(Path(ego_executor.__file__).with_name("ego_dom.mjs")),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
