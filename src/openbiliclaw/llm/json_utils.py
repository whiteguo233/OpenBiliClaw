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
