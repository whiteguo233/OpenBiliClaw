"""Tests for the Sponsored pilot evaluation harness."""

from __future__ import annotations

import json
import shlex
import sys
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.llm.sponsored_pilot_eval import (
    default_fixtures,
    load_fixtures,
    run_pilot_eval,
    validate_consolidation_output,
)
from openbiliclaw.llm.sponsored_provider import SponsoredProvider

if TYPE_CHECKING:
    from pathlib import Path

MOCK_COMMAND = f"{sys.executable} -m openbiliclaw.llm.sponsored_mock_runtime"


async def test_pilot_eval_runs_against_mock_runtime() -> None:
    provider = SponsoredProvider(shlex.split(MOCK_COMMAND), request_timeout=15.0)
    try:
        report = await run_pilot_eval(provider)
    finally:
        await provider.aclose()

    assert report["task"] == "soul.consolidation"
    assert report["fixtures"] == len(default_fixtures())
    assert report["failed"] == 0
    assert all(item["parsed"] is not None for item in report["results"])


def test_validate_consolidation_output_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        validate_consolidation_output("not json")
    with pytest.raises(ValueError):
        validate_consolidation_output('{"likes": [{"score": 1}]}')
    with pytest.raises(ValueError):
        validate_consolidation_output('{"likes": "nope"}')


def test_load_fixtures_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "fixtures.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "smoke",
                    "payload": {"likes_clusters": [], "dislikes_clusters": []},
                }
            ]
        ),
        encoding="utf-8",
    )
    fixtures = load_fixtures(str(path))
    assert len(fixtures) == 1
    assert fixtures[0].name == "smoke"
    assert fixtures[0].payload == {"likes_clusters": [], "dislikes_clusters": []}
