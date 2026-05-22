"""Shared utilities for parsing LLM-generated structured JSON.

Centralizes three concerns that every analyzer used to re-implement:

1. A unified ``max_tokens`` budget for structured tasks — the provider default
   of 4096 routinely truncates Chinese JSON payloads mid-value. Bumping this
   to 16384 gives enough headroom for preference / profile / awareness /
   insight / layer-delta responses.
2. Markdown code-fence stripping.
3. Best-effort salvage of truncated JSON: walks brace/bracket depth with
   string-awareness, closes any still-open containers at the last safe
   boundary, and returns the largest recoverable prefix.

The salvage helpers used to live in ``soul/preference_analyzer.py`` as
underscored locals; callers now import them from here so the behavior is
consistent across analyzers and a single fix improves them all at once.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TypeAlias

logger = logging.getLogger(__name__)

# Unified token budget for structured (JSON) LLM tasks. Gemini 3 Flash preview
# and Claude both support much larger outputs, and Chinese JSON payloads
# routinely exceed 4096 tokens. Using 16384 leaves plenty of headroom while
# staying well under provider ceilings.
DEFAULT_STRUCTURED_MAX_TOKENS = 16384

JSONPrimitive: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONPrimitive | dict[str, "JSONValue"] | list["JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]
JSONArray: TypeAlias = list[JSONValue]
JSONContainer: TypeAlias = JSONObject | JSONArray
JSONDictPredicate: TypeAlias = Callable[[dict[str, JSONValue]], bool]

_DEFAULT_LIST_WRAPPER_KEYS = (
    "results",
    "items",
    "data",
    "output",
    "scores",
    "evaluations",
    "entries",
    "candidates",
    "delights",
    "observations",
    "insights",
    "hypotheses",
    "notes",
    "list",
    "array",
)
_DEFAULT_OBJECT_WRAPPER_KEYS = ("result", "item", "data", "output")


def strip_json_fences(text: str) -> str:
    """Remove Markdown ``` / ```json fences if present.

    Many LLMs wrap JSON output in a code block even when asked for pure JSON;
    this normalizes the common cases so downstream ``json.loads`` succeeds.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:].lstrip()
    return s


def parse_llm_json_tolerant(text: str) -> JSONContainer | None:
    """Parse LLM JSON output tolerantly.

    Strategy:
        1. Strip Markdown fences.
        2. Try a regular ``json.loads``.
        3. On failure, attempt to salvage a truncated object or array by
           closing unbalanced brackets at the last safe boundary.

    Returns the parsed ``dict`` or ``list`` on success, or ``None`` if the
    response is unrecoverable. Callers that need to distinguish "object"
    from "array" should isinstance-check the result.
    """
    cleaned = strip_json_fences(text)
    try:
        return _coerce_json_container(json.loads(cleaned))
    except json.JSONDecodeError:
        pass

    stripped = cleaned.lstrip()
    if stripped.startswith("{"):
        return _salvage_container(cleaned, open_ch="{")
    if stripped.startswith("["):
        return _salvage_container(cleaned, open_ch="[")

    # Unknown root — try both
    return _salvage_container(cleaned, open_ch="{") or _salvage_container(cleaned, open_ch="[")


def extract_llm_json_list(
    content: str,
    *,
    wrapper_keys: tuple[str, ...] = (),
    allow_singleton: bool = False,
    item_predicate: JSONDictPredicate | None = None,
) -> list[dict[str, JSONValue]] | None:
    """Extract a schema-valid JSON object list from messy LLM output."""
    keys = _merge_wrapper_keys(_DEFAULT_LIST_WRAPPER_KEYS, wrapper_keys)
    parsed = parse_llm_json_tolerant(content)
    direct = _coerce_candidate_list(
        parsed,
        wrapper_keys=keys,
        allow_singleton=allow_singleton,
        item_predicate=item_predicate,
    )
    if direct is not None:
        return direct

    for snippet in reversed(_extract_json_array_snippets(content)):
        candidate = _coerce_candidate_list(
            parse_llm_json_tolerant(snippet),
            wrapper_keys=keys,
            allow_singleton=allow_singleton,
            item_predicate=item_predicate,
        )
        if candidate is not None:
            return candidate

    jsonl_candidate = _coerce_jsonl_objects(content, item_predicate=item_predicate)
    if jsonl_candidate is not None:
        return jsonl_candidate

    if allow_singleton:
        for snippet in reversed(_extract_json_object_snippets(content)):
            candidate = _coerce_candidate_list(
                parse_llm_json_tolerant(snippet),
                wrapper_keys=keys,
                allow_singleton=True,
                item_predicate=item_predicate,
            )
            if candidate is not None:
                return candidate
    return None


def extract_llm_json_object(
    content: str,
    *,
    wrapper_keys: tuple[str, ...] = (),
    item_predicate: JSONDictPredicate | None = None,
) -> dict[str, JSONValue] | None:
    """Extract a schema-valid JSON object from messy LLM output."""
    keys = _merge_wrapper_keys(_DEFAULT_OBJECT_WRAPPER_KEYS, wrapper_keys)
    parsed = parse_llm_json_tolerant(content)
    direct = _coerce_candidate_object(
        parsed,
        wrapper_keys=keys,
        item_predicate=item_predicate,
    )
    if direct is not None:
        return direct

    for snippet in reversed(_extract_json_object_snippets(content)):
        candidate = _coerce_candidate_object(
            parse_llm_json_tolerant(snippet),
            wrapper_keys=keys,
            item_predicate=item_predicate,
        )
        if candidate is not None:
            return candidate
    return None


def format_parse_failure(content: str, exc: Exception, *, label: str) -> str:
    """Format a compact diagnostic entry for a failed parse.

    Intentionally includes both the head and tail of the raw response: the
    tail is usually where a truncation manifests, while the head reveals
    whether the LLM obeyed the schema.
    """
    snippet = content.strip()
    head = snippet[:400]
    tail = snippet[-400:]
    return (
        f"{label} JSON parse failed at {exc}; "
        f"total_chars={len(snippet)} head={head!r} tail={tail!r}"
    )


def _salvage_container(text: str, *, open_ch: str) -> JSONContainer | None:
    """Best-effort recovery of a JSON object or array cut off mid-value.

    Walks ``text`` tracking brace/bracket depth and string state; records
    the last "safe" truncation point (matching top-level close or a comma
    at depth ≥1). Then tries progressively longer candidates by either
    cutting at the safe point or repairing the tail with missing closers.
    """
    start = text.find(open_ch)
    if start < 0:
        return None

    depth_stack: list[str] = []
    in_string = False
    escape = False
    last_safe: int | None = None

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            depth_stack.append(ch)
            continue
        if ch in "}]":
            if not depth_stack:
                continue
            depth_stack.pop()
            if not depth_stack:
                last_safe = i + 1
            continue
        if ch == "," and depth_stack:
            last_safe = i

    candidates: list[str] = []
    if last_safe is not None:
        candidates.append(text[start:last_safe])

    trimmed = text[start:]
    for cut_char in (",", "{", "["):
        idx = trimmed.rfind(cut_char)
        if idx >= 0:
            candidate_tail = trimmed[: idx + (0 if cut_char == "," else 1)]
            closers = _remaining_closers(candidate_tail)
            if closers is not None:
                candidates.append(candidate_tail + closers)

    for candidate in candidates:
        candidate = candidate.strip().rstrip(",")
        if not candidate:
            continue
        try:
            parsed = _coerce_json_container(json.loads(candidate))
        except json.JSONDecodeError:
            continue
        if open_ch == "{" and isinstance(parsed, dict):
            return parsed
        if open_ch == "[" and isinstance(parsed, list):
            return parsed
    return None


def _coerce_json_container(value: object) -> JSONContainer | None:
    coerced = _coerce_json_value(value)
    if isinstance(coerced, (dict, list)):
        return coerced
    return None


def _coerce_json_value(value: object) -> JSONValue | None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        coerced_dict: JSONObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                return None
            coerced_item = _coerce_json_value(item)
            if coerced_item is None and item is not None:
                return None
            coerced_dict[key] = coerced_item
        return coerced_dict
    if isinstance(value, list):
        coerced_list: JSONArray = []
        for item in value:
            coerced_item = _coerce_json_value(item)
            if coerced_item is None and item is not None:
                return None
            coerced_list.append(coerced_item)
        return coerced_list
    return None


def _merge_wrapper_keys(
    default_keys: tuple[str, ...],
    caller_keys: tuple[str, ...],
) -> tuple[str, ...]:
    merged: list[str] = []
    for key in (*caller_keys, *default_keys):
        if key and key not in merged:
            merged.append(key)
    return tuple(merged)


def _coerce_candidate_list(
    value: object,
    *,
    wrapper_keys: tuple[str, ...],
    allow_singleton: bool,
    item_predicate: JSONDictPredicate | None,
) -> list[dict[str, JSONValue]] | None:
    for candidate in _iter_list_candidates(
        value,
        wrapper_keys=wrapper_keys,
        allow_singleton=allow_singleton,
    ):
        coerced = _coerce_json_object_list(candidate)
        if coerced is None:
            continue
        if item_predicate is not None and not any(item_predicate(item) for item in coerced):
            continue
        return coerced
    return None


def _iter_list_candidates(
    value: object,
    *,
    wrapper_keys: tuple[str, ...],
    allow_singleton: bool,
) -> list[object]:
    candidates: list[object] = []
    if isinstance(value, list):
        candidates.append(value)
    if isinstance(value, dict):
        for key in wrapper_keys:
            if key in value:
                nested = value[key]
                candidates.append(nested)
                if isinstance(nested, dict):
                    for nested_key in wrapper_keys:
                        if nested_key in nested:
                            candidates.append(nested[nested_key])
        if allow_singleton:
            candidates.append(value)
    return candidates


def _coerce_json_object_list(value: object) -> list[dict[str, JSONValue]] | None:
    if not isinstance(value, list) or not value:
        return None
    results: list[dict[str, JSONValue]] = []
    for item in value:
        coerced_item = _coerce_json_value(item)
        if not isinstance(coerced_item, dict):
            return None
        results.append(coerced_item)
    return results


def _coerce_candidate_object(
    value: object,
    *,
    wrapper_keys: tuple[str, ...],
    item_predicate: JSONDictPredicate | None,
) -> dict[str, JSONValue] | None:
    for candidate in _iter_object_candidates(value, wrapper_keys=wrapper_keys):
        coerced = _coerce_json_value(candidate)
        if not isinstance(coerced, dict):
            continue
        if item_predicate is not None and not item_predicate(coerced):
            continue
        return coerced
    return None


def _iter_object_candidates(
    value: object,
    *,
    wrapper_keys: tuple[str, ...],
) -> list[object]:
    candidates: list[object] = []
    if isinstance(value, dict):
        candidates.append(value)
        for key in wrapper_keys:
            if key in value:
                nested = value[key]
                candidates.append(nested)
                if isinstance(nested, dict):
                    for nested_key in wrapper_keys:
                        if nested_key in nested:
                            candidates.append(nested[nested_key])
    return candidates


def _coerce_jsonl_objects(
    content: str,
    *,
    item_predicate: JSONDictPredicate | None,
) -> list[dict[str, JSONValue]] | None:
    rows: list[dict[str, JSONValue]] = []
    for line in content.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        coerced = _coerce_json_value(parsed)
        if isinstance(coerced, dict):
            rows.append(coerced)
    if not rows:
        return None
    if item_predicate is not None and not any(item_predicate(item) for item in rows):
        return None
    return rows


def _extract_json_array_snippets(text: str) -> list[str]:
    return _extract_balanced_json_snippets(text, open_char="[", close_char="]")


def _extract_json_object_snippets(text: str) -> list[str]:
    return _extract_balanced_json_snippets(text, open_char="{", close_char="}")


def _extract_balanced_json_snippets(
    text: str,
    *,
    open_char: str,
    close_char: str,
) -> list[str]:
    snippets: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == open_char:
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == close_char and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                snippets.append(text[start : index + 1])
                start = None
    return snippets


def _remaining_closers(partial: str) -> str | None:
    """Return the string of closing brackets needed to balance ``partial``.

    Returns ``None`` if the partial ends inside a string literal that cannot
    be safely closed (we refuse to guess where a string should terminate).
    """
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in partial:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None
            stack.pop()
    if in_string:
        return None
    return "".join("}" if opener == "{" else "]" for opener in reversed(stack))
