"""Tests for tolerant JSON parsing helpers."""

from openbiliclaw.llm.json_utils import parse_llm_json_tolerant


def test_parse_llm_json_tolerant_salvages_truncated_object() -> None:
    parsed = parse_llm_json_tolerant('{"topic":"系统论","items":["复杂性",')

    assert parsed == {"topic": "系统论", "items": ["复杂性"]}


def test_parse_llm_json_tolerant_rejects_scalar_root() -> None:
    assert parse_llm_json_tolerant('"just a string"') is None
