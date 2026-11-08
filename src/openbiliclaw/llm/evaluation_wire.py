"""Deterministic row-wire transport for canonical batch-evaluation inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy

ROW_WIRE_V1_HEADER = "ROW-WIRE-V1"

# This order is the protocol. New fields require a new wire version rather than
# silently changing the width or meaning of existing rows.
ROW_WIRE_V1_COLUMNS: tuple[str, ...] = (
    "id",
    "source_platform",
    "content_type",
    "mode",
    "title",
    "author",
    "body_text",
    "description",
    "duration",
    "view_count",
    "like_count",
    "favorite_count",
    "collect_count",
    "comment_count",
    "share_count",
    "danmaku_count",
    "tags",
    "rating_score",
    "rating_count",
    "source_rank",
    "related_interests",
    "cover_image_ref",
)
_SPARSE_ONLY_FIELDS = (
    "published_at",
    "published_label",
    "reply_count",
    "retweet_count",
    "bookmark_count",
    "source_metadata",
)
_CANONICAL_ITEM_FIELDS = frozenset((*ROW_WIRE_V1_COLUMNS, *_SPARSE_ONLY_FIELDS))

_ENVELOPE_FIELDS = frozenset({"defaults", "items"})
_DEFAULT_FIELDS = frozenset({"source_platform", "content_type", "mode"})
_REQUIRED_ITEM_FIELDS = frozenset({"id", "title", "author"})
_STRING_ITEM_FIELDS = frozenset(
    {
        "id",
        "source_platform",
        "content_type",
        "mode",
        "title",
        "author",
        "body_text",
        "description",
        "published_at",
        "published_label",
        "cover_image_ref",
    }
)
_POSITIVE_INTEGER_ITEM_FIELDS = frozenset(
    {
        "duration",
        "view_count",
        "like_count",
        "favorite_count",
        "collect_count",
        "comment_count",
        "share_count",
        "danmaku_count",
        "reply_count",
        "retweet_count",
        "bookmark_count",
        "rating_count",
        "source_rank",
    }
)
_POSITIVE_NUMBER_ITEM_FIELDS = frozenset({"rating_score"})
_LIST_ITEM_FIELDS = frozenset({"tags", "related_interests"})


class EvaluationWireError(ValueError):
    """Raised when a canonical envelope or row-wire payload is invalid."""


def project_evaluation_source_metadata(
    source_platform: str,
    metadata: object,
) -> dict[str, object]:
    """Copy only bounded, semantic source fields; exclude IDs and raw responses.

    Explicit false flags and observed zero counts carry meaning. Missing fields
    remain missing; repository push/update time never replaces publication time.
    """
    if source_platform != "github" or not isinstance(metadata, Mapping):
        return {}

    def clean(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return "".join(
            char
            for char in value[:256]
            if ord(char) >= 0x20 and ord(char) != 0x7F and not 0xD800 <= ord(char) <= 0xDFFF
        ).strip()

    result: dict[str, object] = {}
    for field in ("created_at", "updated_at", "pushed_at", "language", "visibility"):
        if value := clean(metadata.get(field)):
            result[field] = value
    for field in ("forks_count", "open_issues_count", "watchers_count"):
        count = metadata.get(field)
        if type(count) is int and 0 <= count <= 2**63 - 1:
            result[field] = count
    for field in ("fork", "archived", "disabled"):
        flag = metadata.get(field)
        if isinstance(flag, bool):
            result[field] = flag
    license_data = metadata.get("license")
    if isinstance(license_data, Mapping):
        license_fields = {
            field: value
            for field in ("key", "name", "spdx_id")
            if (value := clean(license_data.get(field)))
        }
        if license_fields:
            result["license"] = license_fields
    return result


def _is_finite_positive_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if isinstance(value, int):
        return value > 0
    return math.isfinite(value) and value > 0


def _validate_text(value: str, *, location: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvaluationWireError(f"{location} is not valid UTF-8 text") from exc
    for character in value:
        codepoint = ord(character)
        if (codepoint < 0x20 and character not in {"\t", "\r", "\n"}) or codepoint == 0x7F:
            raise EvaluationWireError(f"{location} contains an unsupported ASCII control")


def _escape_cell(value: str, *, location: str) -> str:
    _validate_text(value, location=location)
    return (
        value.replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")
    )


def _unescape_cell(value: str, *, location: str) -> str:
    decoded: list[str] = []
    index = 0
    escapes = {"\\": "\\", "t": "\t", "r": "\r", "n": "\n"}
    while index < len(value):
        character = value[index]
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            raise EvaluationWireError(f"{location} contains a dangling escape")
        escape = value[index + 1]
        replacement = escapes.get(escape)
        if replacement is None:
            raise EvaluationWireError(f"{location} contains an unknown escape")
        decoded.append(replacement)
        index += 2
    text = "".join(decoded)
    _validate_text(text, location=location)
    return text


def _validated_envelope(
    envelope: Mapping[str, object],
) -> tuple[dict[str, str], list[dict[str, object]]]:
    if not isinstance(envelope, Mapping):
        raise EvaluationWireError("evaluation envelope must be a mapping")
    raw_keys = list(envelope)
    if any(not isinstance(key, str) for key in raw_keys) or set(raw_keys) != _ENVELOPE_FIELDS:
        raise EvaluationWireError("evaluation envelope must contain only defaults and items")

    raw_defaults = envelope["defaults"]
    if not isinstance(raw_defaults, Mapping):
        raise EvaluationWireError("evaluation envelope defaults must be a mapping")
    defaults: dict[str, str] = {}
    for raw_key, raw_value in raw_defaults.items():
        if not isinstance(raw_key, str) or raw_key not in _DEFAULT_FIELDS:
            raise EvaluationWireError("evaluation defaults contain an unknown field")
        if not isinstance(raw_value, str):
            raise EvaluationWireError(f"evaluation default {raw_key} must be a string")
        if raw_key in {"source_platform", "content_type"} and not raw_value:
            raise EvaluationWireError(f"evaluation default {raw_key} must be non-empty")
        _validate_text(raw_value, location=f"evaluation default {raw_key}")
        defaults[raw_key] = raw_value
    if defaults.get("mode") != "normal":
        raise EvaluationWireError("evaluation envelope mode default must be normal")

    raw_items = envelope["items"]
    if not isinstance(raw_items, list):
        raise EvaluationWireError("evaluation envelope items must be a list")

    items: list[dict[str, object]] = []
    allowed_fields = _CANONICAL_ITEM_FIELDS
    per_item_defaults: dict[str, list[str]] = {
        "source_platform": [],
        "content_type": [],
    }
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise EvaluationWireError(f"evaluation item {item_index} must be a mapping")
        raw_item_keys = list(raw_item)
        if any(not isinstance(key, str) for key in raw_item_keys):
            raise EvaluationWireError(f"evaluation item {item_index} has a non-string field")
        item_keys = set(raw_item_keys)
        if not item_keys <= allowed_fields:
            raise EvaluationWireError(f"evaluation item {item_index} has an unknown field")
        if not item_keys >= _REQUIRED_ITEM_FIELDS:
            raise EvaluationWireError(f"evaluation item {item_index} lacks a required field")

        item: dict[str, object] = {}
        for field in (*ROW_WIRE_V1_COLUMNS, *_SPARSE_ONLY_FIELDS):
            if field not in raw_item:
                continue
            value = raw_item[field]
            if field in _STRING_ITEM_FIELDS:
                if not isinstance(value, str):
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} must be a string"
                    )
                if field not in _REQUIRED_ITEM_FIELDS and not value:
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} must be omitted when empty"
                    )
                _validate_text(value, location=f"evaluation item {item_index} field {field}")
            elif field in _POSITIVE_INTEGER_ITEM_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} must be a positive integer"
                    )
            elif field in _POSITIVE_NUMBER_ITEM_FIELDS:
                if not isinstance(value, int | float) or isinstance(value, bool):
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} must be numeric"
                    )
                if not _is_finite_positive_number(value):
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} must be finite and positive"
                    )
            elif field in _LIST_ITEM_FIELDS:
                invalid_list = not isinstance(value, list) or any(
                    not isinstance(entry, str) for entry in value
                )
                if invalid_list or not value:
                    raise EvaluationWireError(
                        f"evaluation item {item_index} field {field} "
                        "must be a non-empty string list"
                    )
                for entry_index, entry in enumerate(value):
                    _validate_text(
                        entry,
                        location=(
                            f"evaluation item {item_index} field {field} entry {entry_index}"
                        ),
                    )
            elif field == "source_metadata":
                platform = raw_item.get("source_platform", defaults.get("source_platform", ""))
                projected = project_evaluation_source_metadata(str(platform), value)
                if not projected or projected != value:
                    raise EvaluationWireError(
                        "source_metadata must contain only canonical source fields"
                    )
                value = projected
            else:  # pragma: no cover - guarded by the complete protocol field sets above
                raise EvaluationWireError(f"unsupported row-wire field {field}")
            item[field] = value

        if item["id"] != str(item_index):
            raise EvaluationWireError(
                "evaluation item ids must be contiguous decimal strings in row order"
            )
        if "mode" in item and item["mode"] != "explore":
            raise EvaluationWireError("per-item evaluation mode may only be explore")
        if "cover_image_ref" in item and item["cover_image_ref"] != f"cover:{item_index}":
            raise EvaluationWireError("cover_image_ref must use the request-local item id")
        for field in ("source_platform", "content_type"):
            if field in defaults:
                if field in item:
                    raise EvaluationWireError(
                        f"evaluation item {item_index} duplicates the {field} default"
                    )
            else:
                value = item.get(field)
                if not isinstance(value, str) or not value:
                    raise EvaluationWireError(
                        f"evaluation item {item_index} is missing its {field} value"
                    )
                per_item_defaults[field].append(value)
        items.append(item)
    for field, values in per_item_defaults.items():
        if values and len(set(values)) < 2:
            raise EvaluationWireError(
                f"homogeneous evaluation {field} values must use a batch default"
            )
    return defaults, items


def validate_canonical_evaluation_envelope(
    envelope: Mapping[str, object],
) -> dict[str, object]:
    """Validate and return a detached canonical evaluator envelope."""

    defaults, items = _validated_envelope(envelope)
    return {
        "defaults": dict(defaults),
        "items": deepcopy(items),
    }


def _encoded_item_cell(item: Mapping[str, object], field: str, *, item_index: int) -> str:
    if field not in item:
        return ""
    value = item[field]
    try:
        if field in _LIST_ITEM_FIELDS:
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        elif field in _POSITIVE_INTEGER_ITEM_FIELDS | _POSITIVE_NUMBER_ITEM_FIELDS:
            serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        else:
            serialized = str(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise EvaluationWireError(
            f"evaluation item {item_index} field {field} cannot be serialized"
        ) from exc
    return _escape_cell(serialized, location=f"evaluation item {item_index} field {field}")


def encode_evaluation_row_wire(envelope: Mapping[str, object]) -> str:
    """Encode a canonical ``{defaults, items}`` envelope as row-wire-v1."""

    defaults, items = _validated_envelope(envelope)
    for field in _SPARSE_ONLY_FIELDS:
        if any(field in item for item in items):
            raise EvaluationWireError(f"row-wire-v1 does not support {field}")
    default_cells = ["defaults"]
    for field in sorted(defaults):
        value = _escape_cell(defaults[field], location=f"evaluation default {field}")
        default_cells.append(f"{field}={value}")

    lines = [
        ROW_WIRE_V1_HEADER,
        "\t".join(default_cells),
        "\t".join(("columns", *ROW_WIRE_V1_COLUMNS)),
    ]
    for item_index, item in enumerate(items):
        cells = [
            _encoded_item_cell(item, field, item_index=item_index) for field in ROW_WIRE_V1_COLUMNS
        ]
        lines.append("\t".join(("row", *cells)))
    wire = "\n".join(lines)
    try:
        wire.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - all cells were already validated
        raise EvaluationWireError("row-wire output is not valid UTF-8 text") from exc
    return wire


def _decode_list_cell(value: str, *, location: str) -> list[str]:
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise EvaluationWireError(f"{location} is not valid compact JSON") from exc
    if not isinstance(decoded, list) or any(not isinstance(entry, str) for entry in decoded):
        raise EvaluationWireError(f"{location} must decode to a string list")
    return decoded


def _decode_number_cell(value: str, *, field: str, location: str) -> int | float:
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise EvaluationWireError(f"{location} is not a valid JSON number") from exc
    if field in _POSITIVE_INTEGER_ITEM_FIELDS:
        if isinstance(decoded, bool) or not isinstance(decoded, int) or decoded <= 0:
            raise EvaluationWireError(f"{location} must decode to a positive integer")
        positive_integer: int = decoded
        return positive_integer
    if isinstance(decoded, bool) or not isinstance(decoded, int | float):
        raise EvaluationWireError(f"{location} must decode to a number")
    if not _is_finite_positive_number(decoded):
        raise EvaluationWireError(f"{location} must decode to a finite positive number")
    positive_number: int | float = decoded
    return positive_number


def decode_evaluation_row_wire(payload: str) -> dict[str, object]:
    """Strictly decode row-wire-v1 into its canonical sparse envelope."""

    if not isinstance(payload, str):
        raise EvaluationWireError("row-wire payload must be text")
    try:
        payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EvaluationWireError("row-wire payload is not valid UTF-8 text") from exc
    if "\r" in payload:
        raise EvaluationWireError("row-wire framing must use LF; cell CR must be escaped")

    lines = payload.split("\n")
    if len(lines) < 3 or lines[0] != ROW_WIRE_V1_HEADER:
        raise EvaluationWireError("row-wire payload has an invalid version header")

    default_cells = lines[1].split("\t")
    if not default_cells or default_cells[0] != "defaults":
        raise EvaluationWireError("row-wire payload has an invalid defaults row")
    defaults: dict[str, str] = {}
    default_order: list[str] = []
    for cell_index, cell in enumerate(default_cells[1:], start=1):
        field, separator, raw_value = cell.partition("=")
        if not separator or field not in _DEFAULT_FIELDS:
            raise EvaluationWireError("row-wire defaults contain an invalid field")
        if field in defaults:
            raise EvaluationWireError("row-wire defaults contain a duplicate field")
        default_order.append(field)
        defaults[field] = _unescape_cell(
            raw_value,
            location=f"row-wire default cell {cell_index}",
        )
    if default_order != sorted(default_order):
        raise EvaluationWireError("row-wire defaults are not in canonical order")

    expected_columns = "\t".join(("columns", *ROW_WIRE_V1_COLUMNS))
    if lines[2] != expected_columns:
        raise EvaluationWireError("row-wire columns do not match row-wire-v1")

    items: list[dict[str, object]] = []
    expected_width = len(ROW_WIRE_V1_COLUMNS) + 1
    for row_index, line in enumerate(lines[3:]):
        raw_cells = line.split("\t")
        if len(raw_cells) != expected_width or raw_cells[0] != "row":
            raise EvaluationWireError(f"row-wire row {row_index} has an invalid width or prefix")
        item: dict[str, object] = {}
        for field, raw_cell in zip(ROW_WIRE_V1_COLUMNS, raw_cells[1:], strict=True):
            location = f"row-wire row {row_index} field {field}"
            decoded_cell = _unescape_cell(raw_cell, location=location)
            if not decoded_cell and field not in _REQUIRED_ITEM_FIELDS:
                continue
            if field in _LIST_ITEM_FIELDS:
                item[field] = _decode_list_cell(decoded_cell, location=location)
            elif field in _POSITIVE_INTEGER_ITEM_FIELDS | _POSITIVE_NUMBER_ITEM_FIELDS:
                item[field] = _decode_number_cell(
                    decoded_cell,
                    field=field,
                    location=location,
                )
            else:
                item[field] = decoded_cell
        items.append(item)

    envelope: dict[str, object] = {"defaults": defaults, "items": items}
    # Revalidation catches missing/non-contiguous ids and field-level semantic
    # violations. Re-encoding then rejects alternate spellings, JSON whitespace,
    # numeric forms, trailing lines, or non-canonical escaping.
    _validated_envelope(envelope)
    if encode_evaluation_row_wire(envelope) != payload:
        raise EvaluationWireError("row-wire payload is not in canonical row-wire-v1 form")
    return envelope
