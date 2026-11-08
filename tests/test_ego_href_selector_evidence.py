"""Row-scoped evidence for query-free absolute/relative href authoring mistakes."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "mode",
    [
        "relative",
        "mixed",
        "mixed_beyond_budget",
        "ambiguous",
        "cross_origin",
        "query",
        "fragment",
        "escaping",
        "case_flag",
        "selector_list",
        "parent_relative",
        "whitespace",
        "wrong_destination",
        "changed_match",
        "empty",
    ],
)
def test_failed_collection_reports_only_proven_field_repairs_without_changing_plan(
    mode: str,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the native DOM reader fixture")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/fixtures/ego_href_selector_evidence.mjs"),
            str(root / "src/openbiliclaw/sources/ego_dom.mjs"),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["repairs"] == (
        ["link_selector", "title_selector"] if mode == "relative" else []
    )


@pytest.mark.parametrize(
    "mode",
    [
        "absolute",
        "ambiguous",
        "ambiguous_beyond_budget",
        "cross_origin",
        "raw_relative",
        "foreign_base",
        "blank",
        "wrong_field",
        "missing_field",
        "mixed",
        "empty",
        "over_limit",
        "query",
        "fragment",
        "escaping",
        "selector_list",
        "nested",
        "case_flag",
        "parent_relative",
    ],
)
def test_zero_row_item_predicate_requires_unique_same_origin_content_evidence(mode: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the native DOM reader fixture")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            node,
            str(root / "tests/fixtures/ego_item_href_evidence.mjs"),
            str(root / "src/openbiliclaw/sources/ego_dom.mjs"),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["repair"] == (
        {"origin": "https://forum.example"} if mode == "absolute" else None
    )
