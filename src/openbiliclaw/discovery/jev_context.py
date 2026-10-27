"""Local, traceable candidate evidence for one complete Jev evaluation.

Original text stays with the caller. The wire stores each distinct excerpt once;
Choice descriptions point at its explicit ID instead of repeating the text.
This module performs no I/O and never creates a semantic summary.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass

TEXT_FIELDS = ("title", "description", "body_text", "published_label")
CONTEXT_POLICY = "jev-source-spans-v1"
# Conservative lexical anchors, not a complete semantic detector. Their adjacent
# source spans are indivisible; this does not prove omitted material irrelevant.
_CONDITION = re.compile(
    r"不|没|无|非|仅|只|除|但|然而|如果|假如|否则|截止|截至|过期|失效|撤回|条件|"
    r"\b(?:not|no|never|unless|except|only|however|but|if|until|expired|deadline)\b|\d{4}[-年/]",
    re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    result = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(run[i : i + 2] for i in range(len(run) - 1))
    return result


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class CandidateTextView:
    """Detached wire data and the exact original excerpts it can return."""

    content: dict[str, object]
    excerpts: dict[str, str]
    evidence_options: dict[str, str]


def candidate_text_view(
    item: dict[str, object], *, max_bytes: int | None = None, query_texts: tuple[str, ...] = ()
) -> CandidateTextView | None:
    """Use complete source spans within a wire budget; never slice a long span.

    With no budget, all source spans must fit the native choice limit. Budgeted
    extraction preserves field openings/endings, explicit conditions and their
    neighbors before selecting other spans by local lexical relevance.
    """
    spans: dict[str, dict[str, object]] = {}
    by_text: dict[str, str] = {}
    for field in TEXT_FIELDS:
        text = item.get(field)
        if text is not None and not isinstance(text, str):
            raise ValueError("candidate source fields must be text")
        if not isinstance(text, str):
            continue
        start = 0
        for part in re.split(r"(?<=[。！？!?])|\n", text):
            stripped = part.strip()
            if stripped:
                offset = text.find(stripped, start)
                origin = {"field": field, "start": offset, "end": offset + len(stripped)}
                if stripped in by_text:
                    origins = spans[by_text[stripped]]["origins"]
                    assert isinstance(origins, list)
                    origins.append(origin)
                else:
                    ref = f"e{len(spans)}"
                    by_text[stripped] = ref
                    spans[ref] = {"text": stripped, "origins": [origin]}
                start = offset + len(stripped)
    source = {key: item[key] for key in TEXT_FIELDS if key in item}
    source_digest = hashlib.sha256(_json(source).encode("utf-8")).hexdigest()
    metadata = deepcopy({key: value for key, value in item.items() if key not in TEXT_FIELDS})

    def render(selected: set[str]) -> dict[str, object]:
        return {
            **metadata,
            "text_spans": {key: value for key, value in spans.items() if key in selected},
            "text_coverage": {
                "policy": CONTEXT_POLICY,
                "source_digest": source_digest,
                "is_partial": len(selected) < len(spans),
                "source_spans": len(spans),
                "selected_spans": len(selected),
            },
        }

    selected = set(spans)
    content = render(selected)
    if len(spans) > 254 or (
        max_bytes is not None and len(_json(content).encode("utf-8")) > max_bytes
    ):
        if max_bytes is None:
            return None
        positions: dict[str, list[tuple[int, str]]] = {key: [] for key in TEXT_FIELDS}
        for key, span in spans.items():
            origins = span["origins"]
            assert isinstance(origins, list)
            for origin in origins:
                positions[origin["field"]].append((origin["start"], key))
        # A deduplicated span can occur at several distant positions. Sorting
        # original offsets, rather than first-seen IDs, preserves real neighbors.
        field_refs = {
            field: [key for _, key in sorted(values)] for field, values in positions.items()
        }
        neighbors: dict[str, set[str]] = {key: {key} for key in spans}
        mandatory: set[str] = set()
        for field, refs in field_refs.items():
            if refs:
                mandatory.update((refs[0], refs[-1]))
            if field in {"title", "published_label"}:
                mandatory.update(refs)
            for i, key in enumerate(refs):
                neighbors[key].update(refs[max(0, i - 1) : i + 2])
        for key, span in spans.items():
            if _CONDITION.search(str(span["text"])):
                mandatory.update(neighbors[key])
        selected = mandatory
        content = render(selected)
        if len(selected) > 254 or len(_json(content).encode("utf-8")) > max_bytes:
            return None
        query = _tokens(" ".join(query_texts))
        ranked = sorted(
            spans,
            key=lambda key: (-len(_tokens(str(spans[key]["text"])) & query), int(key[1:])),
        )
        for key in ranked:
            proposed = selected | neighbors[key]
            if len(proposed) > 254:
                continue
            proposed_content = render(proposed)
            if len(_json(proposed_content).encode("utf-8")) <= max_bytes:
                selected, content = proposed, proposed_content
    return CandidateTextView(
        content=content,
        excerpts={key: str(value["text"]) for key, value in spans.items() if key in selected},
        evidence_options={
            key: f"This candidate's `content.text_spans.{key}.text`."
            for key in spans
            if key in selected
        },
    )
