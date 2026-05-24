"""Tests for filter_events_by_satisfaction.

Pure helper used by PreferenceAnalyzer (and potentially other consumers)
to drop events the classifier marked as quick-exit or explicit-negative.
The aliasing rule is documented inline: requesting ``"unknown"`` also
matches rows whose ``inferred_satisfaction`` is ``None`` so unclassified
legacy events can be opt-in retained.
"""

from __future__ import annotations

import pytest

from openbiliclaw.soul.event_filters import filter_events_by_satisfaction


def _row(idx: int, satisfaction: str | None) -> dict[str, object]:
    return {
        "id": idx,
        "event_type": "click",
        "title": f"row-{idx}",
        "inferred_satisfaction": satisfaction,
    }


def test_filter_keeps_only_positive_rows() -> None:
    events = [
        _row(1, "positive"),
        _row(2, "negative"),
        _row(3, "positive"),
        _row(4, "neutral"),
    ]
    out = filter_events_by_satisfaction(events, modes=frozenset({"positive"}))
    assert [row["id"] for row in out] == [1, 3]


def test_filter_modes_positive_plus_unknown_keeps_null_and_unknown() -> None:
    """Plan contract: `"unknown"` matches both `"unknown"` and `None`."""
    events = [
        _row(1, "positive"),
        _row(2, "unknown"),
        _row(3, None),
        _row(4, "negative"),
    ]
    out = filter_events_by_satisfaction(events, modes=frozenset({"positive", "unknown"}))
    assert [row["id"] for row in out] == [1, 2, 3]


def test_filter_empty_modes_returns_empty_list() -> None:
    events = [_row(1, "positive"), _row(2, "negative")]
    out = filter_events_by_satisfaction(events, modes=frozenset())
    assert out == []


def test_filter_preserves_order() -> None:
    """Caller may depend on chronological / DB order; filter must not reorder."""
    events = [
        _row(1, "positive"),
        _row(2, "negative"),
        _row(3, "positive"),
        _row(4, "positive"),
        _row(5, "negative"),
    ]
    out = filter_events_by_satisfaction(events, modes=frozenset({"positive"}))
    assert [row["id"] for row in out] == [1, 3, 4]


def test_filter_rows_without_field_treated_as_none() -> None:
    """Defensive: a row that somehow lacks the field is treated as None
    (i.e. requesting `unknown` keeps it, requesting `positive` drops it)."""
    events = [{"id": 99, "event_type": "view"}]
    keeps_with_unknown = filter_events_by_satisfaction(events, modes=frozenset({"unknown"}))
    assert keeps_with_unknown == events
    drops_with_positive = filter_events_by_satisfaction(events, modes=frozenset({"positive"}))
    assert drops_with_positive == []


@pytest.mark.parametrize("modes", [{"negative"}, {"neutral"}])
def test_filter_other_modes(modes: set[str]) -> None:
    events = [
        _row(1, "positive"),
        _row(2, "negative"),
        _row(3, "neutral"),
        _row(4, None),
    ]
    out = filter_events_by_satisfaction(events, modes=frozenset(modes))
    expected_mode = next(iter(modes))
    assert [row["id"] for row in out] == [
        row["id"] for row in events if row["inferred_satisfaction"] == expected_mode
    ]
