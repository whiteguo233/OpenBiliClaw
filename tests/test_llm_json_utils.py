"""Tests for tolerant JSON parsing helpers."""

from openbiliclaw.llm.json_utils import (
    extract_llm_json_list,
    extract_llm_json_object,
    parse_llm_json_tolerant,
)


def test_parse_llm_json_tolerant_salvages_truncated_object() -> None:
    parsed = parse_llm_json_tolerant('{"topic":"系统论","items":["复杂性",')

    assert parsed == {"topic": "系统论", "items": ["复杂性"]}


def test_parse_llm_json_tolerant_rejects_scalar_root() -> None:
    assert parse_llm_json_tolerant('"just a string"') is None


def _has_score(item: dict[str, object]) -> bool:
    return "score" in item


def _has_hypothesis(item: dict[str, object]) -> bool:
    return "hypothesis" in item


def test_extract_llm_json_list_accepts_root_array() -> None:
    assert extract_llm_json_list('[{"score":0.8}]', item_predicate=_has_score) == [
        {"score": 0.8}
    ]


def test_extract_llm_json_list_accepts_common_wrappers() -> None:
    for key in ("results", "items", "data", "output", "scores"):
        assert extract_llm_json_list(
            f'{{"{key}":[{{"score":0.8}}]}}',
            item_predicate=_has_score,
        ) == [{"score": 0.8}]


def test_extract_llm_json_list_accepts_caller_wrapper_aliases() -> None:
    assert extract_llm_json_list(
        '{"hypotheses":[{"hypothesis":"h","confidence":0.6}]}',
        wrapper_keys=("hypotheses",),
        item_predicate=_has_hypothesis,
    ) == [{"hypothesis": "h", "confidence": 0.6}]


def test_extract_llm_json_list_accepts_singleton_when_enabled() -> None:
    assert extract_llm_json_list(
        '{"score":0.8}',
        allow_singleton=True,
        item_predicate=_has_score,
    ) == [{"score": 0.8}]


def test_extract_llm_json_list_accepts_markdown_fenced_json() -> None:
    assert extract_llm_json_list(
        '```json\n[{"score":0.8}]\n```',
        item_predicate=_has_score,
    ) == [{"score": 0.8}]


def test_extract_llm_json_list_accepts_jsonl_objects() -> None:
    assert extract_llm_json_list(
        '{"score":0.8}\n{"score":0.7}',
        item_predicate=_has_score,
    ) == [{"score": 0.8}, {"score": 0.7}]


def test_extract_llm_json_list_prefers_final_schema_valid_array_after_echo() -> None:
    content = """
    {"schema":{"items":[{"not_score":true}]}}
    ```json
    [{"score":0.9}]
    ```
    """

    assert extract_llm_json_list(content, item_predicate=_has_score) == [{"score": 0.9}]


def test_extract_llm_json_list_accepts_malformed_mimo_object_wrapped_array() -> None:
    content = """{
      [
        {"hypothesis":"h","evidence":["e"],"confidence":0.6}
      ]
    }"""

    assert extract_llm_json_list(content, item_predicate=_has_hypothesis) == [
        {"hypothesis": "h", "evidence": ["e"], "confidence": 0.6}
    ]


def test_extract_llm_json_list_rejects_arrays_without_predicate_match() -> None:
    assert extract_llm_json_list('[{"title":"echo"}]', item_predicate=_has_score) is None


def test_extract_llm_json_object_accepts_root_object() -> None:
    assert extract_llm_json_object(
        '{"expression":"x"}',
        item_predicate=lambda item: "expression" in item,
    ) == {"expression": "x"}


def test_extract_llm_json_object_accepts_wrapped_object() -> None:
    assert extract_llm_json_object(
        '{"result":{"delight_reason":"r"}}',
        item_predicate=lambda item: "delight_reason" in item,
    ) == {"delight_reason": "r"}


def test_extract_llm_json_object_prefers_final_predicate_match_after_echo() -> None:
    content = '{"schema":{"expression":"string"}}\n{"expression":"real"}'

    assert extract_llm_json_object(
        content,
        item_predicate=lambda item: item.get("expression") == "real",
    ) == {"expression": "real"}
