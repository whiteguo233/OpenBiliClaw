"""Tests for the canonical JSON-mode system prompt contract."""

from __future__ import annotations

import pytest

from openbiliclaw.llm.prompt_contracts import ensure_json_mode_contract


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Output JSON.", "Output json."),
        ("Output json.", "Output json."),
        ("  Return a JSON object.  ", "Return a json object."),
        ("no marker here", "no marker here\n\njson"),
        ("", "json"),
        ("   ", "json"),
        ("JSON", "json"),
        ("JSON and json", "JSON and json"),
        ("JSON and JSON", "json and json"),
    ],
)
def test_ensure_json_mode_contract_is_canonical(raw: str, expected: str) -> None:
    assert ensure_json_mode_contract(raw) == expected


def test_ensure_json_mode_contract_is_idempotent() -> None:
    for raw in ("plain", "Output JSON.", "Output json.", ""):
        once = ensure_json_mode_contract(raw)
        assert ensure_json_mode_contract(once) == once


def test_ensure_json_mode_contract_strips_surrounding_whitespace_only() -> None:
    assert ensure_json_mode_contract("\nkeep  inner  spacing\n") == "keep  inner  spacing\n\njson"
