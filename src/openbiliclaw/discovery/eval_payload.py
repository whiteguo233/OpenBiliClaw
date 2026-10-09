"""Canonical sparse candidate payloads for production and replay evaluator wires."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openbiliclaw.llm.evaluation_wire import (
    EvaluationWireError,
    validate_canonical_evaluation_envelope,
)

_OPTIONAL_STRING_FIELDS = (
    "body_text",
    "description",
    "published_at",
)
_OPTIONAL_POSITIVE_INTEGER_FIELDS = (
    "duration",
    "view_count",
    "like_count",
    "favorite_count",
    "collect_count",
    "comment_count",
    "share_count",
    "danmaku_count",
    "rating_count",
    "source_rank",
)
_OPTIONAL_POSITIVE_NUMBER_FIELDS = ("rating_score",)
_OPTIONAL_LIST_FIELDS = ("tags", "related_interests")


class CanonicalEvaluationPayloadError(ValueError):
    """Raised when a sparse evaluator payload is not canonical."""


@dataclass(frozen=True)
class CanonicalEvaluationBatch:
    """One request-scoped canonical sparse evaluator batch.

    ``local_ids`` and ``local_id_to_index`` are deliberately request-local;
    neither exposes a global content identifier on the LLM wire.
    """

    defaults: dict[str, object]
    items: tuple[dict[str, object], ...]
    local_ids: tuple[str, ...]

    @property
    def local_id_to_index(self) -> dict[str, int]:
        """Return the local-ID to request-member index map."""

        return {local_id: index for index, local_id in enumerate(self.local_ids)}

    def as_payload(self) -> dict[str, object]:
        """Return the JSON-like canonical envelope consumed by transports."""

        return {
            "defaults": dict(self.defaults),
            "items": [_copy_item(item) for item in self.items],
        }


def _copy_item(item: Mapping[str, object]) -> dict[str, object]:
    return {key: list(value) if isinstance(value, list) else value for key, value in item.items()}


def _text(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _sanitize_wire_text(value: str) -> str:
    """Strip exactly the characters the evaluation wire validator rejects.

    One upstream-sourced control character (e.g. a NUL inside a Bilibili
    description) used to raise in ``_validate_text`` and fail the *whole*
    evaluation batch (#284). Source rows keep their raw text; only the LLM
    wire copy is cleaned here, so a dirty item can no longer kill its batch.
    The strip set mirrors the validator: C0 controls except ``\\t \\r \\n``,
    plus DEL. Lone surrogates are replaced so the result always encodes as
    valid UTF-8.
    """

    cleaned = "".join(
        character
        for character in value
        if character in {"\t", "\r", "\n"} or not (ord(character) < 0x20 or ord(character) == 0x7F)
    )
    try:
        cleaned.encode("utf-8")
    except UnicodeEncodeError:
        cleaned = cleaned.encode("utf-8", "replace").decode("utf-8")
    return cleaned


def _nonempty_list(value: object) -> list[object] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    items = list(value)
    return items or None


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, float) and math.isfinite(value) and value > 0


def _is_explore(item: Mapping[str, object]) -> bool:
    effective_context = item.get("source_context") or item.get("source_strategy")
    return _text(effective_context).strip().lower() == "explore"


def _homogeneous_default(values: Sequence[str]) -> str | None:
    if not values:
        return None
    first = values[0]
    return first if all(value == first for value in values[1:]) else None


def build_canonical_evaluation_batch(
    content_items: Sequence[Mapping[str, object]],
) -> CanonicalEvaluationBatch:
    """Build the sole canonical sparse representation for all transports."""

    source_items = [dict(item) for item in content_items]
    platforms = [_sanitize_wire_text(_text(item.get("source_platform"))) for item in source_items]
    content_types = [_sanitize_wire_text(_text(item.get("content_type"))) for item in source_items]
    default_platform = _homogeneous_default(platforms)
    default_content_type = _homogeneous_default(content_types)

    defaults: dict[str, object] = {"mode": "normal"}
    if default_platform is not None:
        defaults["source_platform"] = default_platform
    if default_content_type is not None:
        defaults["content_type"] = default_content_type

    canonical_items: list[dict[str, object]] = []
    local_ids: list[str] = []
    for index, source in enumerate(source_items):
        local_id = str(index)
        local_ids.append(local_id)
        item: dict[str, object] = {
            "id": local_id,
            "title": _sanitize_wire_text(_text(source.get("title"))),
            "author": _sanitize_wire_text(
                _text(source.get("author_name") or source.get("up_name"))
            ),
        }
        if default_platform is None:
            item["source_platform"] = platforms[index]
        if default_content_type is None:
            item["content_type"] = content_types[index]
        if _is_explore(source):
            item["mode"] = "explore"

        for field in _OPTIONAL_STRING_FIELDS:
            value = source.get(field)
            if isinstance(value, str) and value:
                item[field] = _sanitize_wire_text(value)
        for field in _OPTIONAL_POSITIVE_INTEGER_FIELDS:
            value = source.get(field)
            if _is_positive_integer(value):
                item[field] = value
        for field in _OPTIONAL_POSITIVE_NUMBER_FIELDS:
            value = source.get(field)
            if _is_positive_number(value):
                item[field] = value
        for field in _OPTIONAL_LIST_FIELDS:
            value = _nonempty_list(source.get(field))
            if value is not None:
                entries = [
                    sanitized for entry in value if (sanitized := _sanitize_wire_text(_text(entry)))
                ]
                if entries:
                    item[field] = entries
        if _text(source.get("cover_image_ref")):
            item["cover_image_ref"] = f"cover:{local_id}"
        canonical_items.append(item)

    batch = CanonicalEvaluationBatch(
        defaults=defaults,
        items=tuple(canonical_items),
        local_ids=tuple(local_ids),
    )
    _validate_canonical_batch(batch)
    return batch


def render_sparse_evaluation_json(batch: CanonicalEvaluationBatch) -> str:
    """Render canonical sparse JSON deterministically without ASCII escaping."""

    _validate_canonical_batch(batch)
    return json.dumps(
        batch.as_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_sparse_evaluation_json(payload: str) -> CanonicalEvaluationBatch:
    """Decode and strictly validate a canonical sparse JSON payload."""

    try:
        decoded = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CanonicalEvaluationPayloadError("sparse evaluator payload is not valid JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"defaults", "items"}:
        raise CanonicalEvaluationPayloadError(
            "sparse evaluator payload must contain only defaults and items"
        )
    defaults = decoded.get("defaults")
    raw_items = decoded.get("items")
    if not isinstance(defaults, dict) or not isinstance(raw_items, list):
        raise CanonicalEvaluationPayloadError(
            "defaults must be an object and items must be an array"
        )
    if not all(isinstance(item, dict) for item in raw_items):
        raise CanonicalEvaluationPayloadError("every sparse evaluator item must be an object")
    items = tuple(dict(item) for item in raw_items)
    local_ids: list[str] = []
    for item in items:
        raw_id = item.get("id")
        local_ids.append(raw_id if isinstance(raw_id, str) else "")
    batch = CanonicalEvaluationBatch(
        defaults=dict(defaults),
        items=items,
        local_ids=tuple(local_ids),
    )
    _validate_canonical_batch(batch)
    return batch


def _validate_canonical_batch(batch: CanonicalEvaluationBatch) -> None:
    expected_ids = tuple(str(index) for index in range(len(batch.items)))
    if batch.local_ids != expected_ids:
        raise CanonicalEvaluationPayloadError(
            "canonical local IDs must be sequential decimal strings"
        )
    try:
        validated = validate_canonical_evaluation_envelope(batch.as_payload())
    except EvaluationWireError as exc:
        raise CanonicalEvaluationPayloadError(str(exc)) from exc
    if validated != batch.as_payload():
        raise CanonicalEvaluationPayloadError("canonical envelope changed during validation")


def resolve_local_evaluation_results(
    payload: Sequence[Mapping[str, Any]],
    expected_ids: Sequence[str],
) -> list[dict[str, Any] | None]:
    """Resolve result members by strict request-local ID.

    Multi-member requests never use positional binding. A singleton retains
    the existing tolerant fallback only when the sole response has no usable
    explicit ``id`` at all.
    """

    ids = tuple(expected_ids)
    if not all(isinstance(local_id, str) and local_id for local_id in ids):
        raise ValueError("expected local IDs must be non-empty strings")
    if len(set(ids)) != len(ids):
        raise ValueError("expected local IDs must be unique")

    expected = set(ids)
    matched: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    saw_explicit_id = False
    for raw_item in payload:
        item = dict(raw_item)
        raw_id = item.get("id")
        if raw_id is None or raw_id == "":
            continue
        saw_explicit_id = True
        if not isinstance(raw_id, str) or raw_id not in expected:
            continue
        if raw_id in duplicates:
            continue
        if raw_id in matched:
            matched.pop(raw_id, None)
            duplicates.add(raw_id)
            continue
        matched[raw_id] = item

    if len(ids) == 1 and len(payload) == 1 and not saw_explicit_id:
        return [dict(payload[0])]
    return [matched.get(local_id) for local_id in ids]
