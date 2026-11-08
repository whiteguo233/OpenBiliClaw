"""Fresh disliked-topic filtering at recommendation output boundaries.

Discovery may keep fetching broad supply.  This module owns the separate
product invariant: once a dislike is durably visible, cached/history rows must
be rechecked before they are returned to a user-facing recommendation surface.

Scope discipline: a stored dislike is free text emitted by the preference
analyzer, so it arrives either as a specific avoid phrase
("营销包装重的课程") or as a bare category word ("科普").  The two have very
different blast radius once matched against prose.  A phrase is a claim about a
pattern; a bare category word is a label that only makes sense against
structured topic fields.  So only phrase terms keep the substring guard —
otherwise a single item-level dislike silently becomes a site-wide ban
(2026-11-04: "科普" removed 198 cached rows, including 天文科普 items the same
profile still listed as interests).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import TypeVar

_Row = TypeVar("_Row", bound=Mapping[str, object])

# A dislike term counts as a *phrase* (specific enough to substring-match prose)
# when it is long enough to carry qualifiers, or contains a qualifier marker.
# Anything shorter is a bare category label ("科普" / "崩坏" / "综艺") and must
# match structured topic fields by equality only.
#
# The threshold is a specificity proxy, not a sentiment judgement: in Chinese a
# two-to-three-character string is a basic-level noun (a category name), while
# four-plus characters is a compound that can distinguish one pattern from its
# parent category.  Calibrated against the local 2026-11 profile snapshot:
# 125/128 stored terms were phrases, and the 3 bare nouns accounted for 195 of
# the 235 rows the old substring rule removed.  Four characters keeps the
# existing "运动康复"-style topic exclusions working (see
# tests/test_recommendation_exclusion.py).
_PHRASE_MIN_CHARS = 4
_PHRASE_MARKERS = (
    "的",
    "类",
    "向",
    "式",
    "感",
    "版",
    "党",
    "风",
    "教程",
    "内容",
    "视频",
    "讲解",
    "标题",
    "营销",
    "盘点",
    "合集",
    "攻略",
)


def normalize_disliked_topics(topics: Sequence[str]) -> list[str]:
    """Return stable, de-duplicated terms using the existing serve semantics."""

    result: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        term = normalize_dislike_match_text(topic)
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def disliked_topics_digest(topics: Sequence[str]) -> str:
    """Hash a normalized effective-dislike snapshot for cache invalidation."""

    payload = json.dumps(
        sorted(normalize_disliked_topics(topics)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def filter_recommendation_rows(
    rows: Sequence[_Row],
    disliked_topics: Sequence[str],
    *,
    restore_on_total_fuzzy_match: bool = True,
) -> list[_Row]:
    """Filter cached/history recommendation rows with current dislikes.

    This mirrors ``RecommendationEngine``'s existing serve-time policy.  Exact
    structured-topic matches are always excluded.  Fuzzy title/body/tag
    matches are also excluded, except that the existing starvation safeguard
    may restore exact-safe rows when a broad phrase fuzzily matches the entire
    window.  Single-item push surfaces disable that safeguard.

    Scope rule: structured topic fields exclude on equality for every term;
    prose fields (title / description / author / tags / body) only exclude for
    *phrase* terms.  A bare category label ("科普") never substring-matches
    prose — see ``dislike_term_scope`` for the calibration.
    """

    items = list(rows)
    terms = normalize_disliked_topics(disliked_topics)
    if not items or not terms:
        return items
    filtered = [row for row in items if not recommendation_row_matches(row, terms)]
    if filtered or not restore_on_total_fuzzy_match:
        return filtered
    return [row for row in items if not recommendation_row_matches_exact(row, terms)]


def dislike_term_scope(term: str) -> str:
    """Return ``"phrase"`` for specific avoid patterns, ``"category"`` for labels."""

    text = normalize_dislike_match_text(term)
    if len(text) >= _PHRASE_MIN_CHARS or any(marker in text for marker in _PHRASE_MARKERS):
        return "phrase"
    return "category"


def phrase_dislike_terms(terms: Sequence[str]) -> list[str]:
    """Return the normalized terms specific enough to substring-match prose."""

    result: list[str] = []
    for term in terms:
        normalized = normalize_dislike_match_text(term)
        if normalized and dislike_term_scope(normalized) == "phrase":
            result.append(normalized)
    return result


def recommendation_row_matches(row: Mapping[str, object], disliked_terms: Sequence[str]) -> bool:
    """Return whether a recommendation row matches a dislike by scope.

    Structured topic equality applies to every term; prose substring matching
    applies only to phrase terms, so a bare category label cannot erase every
    title that happens to contain it.
    """

    if recommendation_row_matches_exact(row, disliked_terms):
        return True
    prose_terms = phrase_dislike_terms(disliked_terms)
    if not prose_terms:
        return False
    fields = [
        normalize_dislike_match_text(row.get("title")),
        normalize_dislike_match_text(row.get("topic")),
        normalize_dislike_match_text(row.get("topic_label")),
        normalize_dislike_match_text(row.get("description")),
        normalize_dislike_match_text(row.get("body_text")),
        normalize_dislike_match_text(row.get("up_name")),
        normalize_dislike_match_text(row.get("tags")),
    ]
    return any(term in field for term in prose_terms for field in fields if field)


def recommendation_row_matches_exact(
    row: Mapping[str, object],
    disliked_terms: Sequence[str],
) -> bool:
    """Return whether a structured topic field exactly matches a dislike."""

    exact_fields = {
        normalize_dislike_match_text(row.get("topic")),
        normalize_dislike_match_text(row.get("topic_label")),
        normalize_dislike_match_text(row.get("topic_key")),
        normalize_dislike_match_text(row.get("topic_group")),
        normalize_dislike_match_text(row.get("pool_topic_label")),
    }
    exact_fields.discard("")
    return any(term in exact_fields for term in disliked_terms)


def normalize_dislike_match_text(value: object) -> str:
    """Match the established serve-time lowercase/whitespace normalization."""

    text = str(value or "").strip().lower()
    return re.sub(r"\s+", "", text) if text else ""
