"""Tests for recent_negative_exemplars.

Builds the downstream consumer of the inferred_satisfaction signal:
a small recency-weighted list of recent quick-exit / explicit-negative
titles, used by the eval-batch evaluator as concrete anchors to
pattern-match clickbait look-alikes against.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars


class _StubEventStore:
    """Minimal duck-typed event store for the helper."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.calls: list[dict[str, Any]] = []

    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append({"satisfaction_modes": satisfaction_modes, "limit": limit})
        # Always return everything we have; let the helper do its own
        # filtering / weighting. Real Database.query_events filters by
        # satisfaction_modes itself; doing it here would prevent the
        # tests from checking that the helper asks for the right slice.
        return list(self._events[:limit])


def _row(
    *,
    idx: int,
    title: str,
    reason: str = "quick_exit",
    age_days: float = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime(2026, 5, 16, 12, 0, 0)
    created = now - timedelta(days=age_days)
    return {
        "id": idx,
        "title": title,
        "inferred_satisfaction": "negative",
        "satisfaction_reason": reason,
        "created_at": created.isoformat(sep=" "),
    }


def test_empty_store_returns_empty_list() -> None:
    out = recent_negative_exemplars(_StubEventStore([]))
    assert out == []


def test_returns_records_with_expected_shape() -> None:
    now = datetime(2026, 5, 16, 12, 0, 0)
    store = _StubEventStore(
        [
            _row(idx=1, title="震惊！这把键盘竟然", age_days=2, now=now),
            _row(
                idx=2, title="保姆级教程一期通关", reason="explicit_negative", age_days=5, now=now
            ),
            _row(idx=3, title="无效信息流", age_days=10, now=now),
        ]
    )
    out = recent_negative_exemplars(store, now=now)
    assert len(out) == 3
    for record in out:
        assert set(record.keys()) == {"title", "reason", "age_days"}
        assert isinstance(record["age_days"], int)


def test_caps_to_limit_with_recency_priority() -> None:
    """20 negatives — only the 8 most-recent (highest weight) are kept."""
    now = datetime(2026, 5, 16, 12, 0, 0)
    events = [_row(idx=i, title=f"标题{i}", age_days=i, now=now) for i in range(1, 21)]
    out = recent_negative_exemplars(_StubEventStore(events), now=now)
    assert len(out) == 8
    # The 8 newest titles should be "标题1" through "标题8"
    kept_titles = [r["title"] for r in out]
    assert kept_titles == [f"标题{i}" for i in range(1, 9)]


def test_dedupe_by_normalized_prefix() -> None:
    """Two near-identical clickbait variants collapse to one slot."""
    now = datetime(2026, 5, 16, 12, 0, 0)
    events = [
        _row(idx=1, title="#震惊！这一把键盘竟然能", age_days=1, now=now),
        _row(idx=2, title="震惊！这一把键盘竟然能！！", age_days=3, now=now),
        _row(idx=3, title="纯粹不一样的话题完全不同的内容", age_days=2, now=now),
    ]
    out = recent_negative_exemplars(_StubEventStore(events), now=now)
    titles = [r["title"] for r in out]
    # First two collapse to one (newer wins)
    assert len(out) == 2
    assert "纯粹不一样的话题完全不同的内容" in titles
    # The newer of the two near-duplicates is the age_days=1 row.
    near_dup = next(t for t in titles if "键盘" in t)
    assert near_dup.startswith("#震惊")


def test_titles_truncated_to_80_chars() -> None:
    long_title = "震惊" * 50  # 100 chars
    out = recent_negative_exemplars(
        _StubEventStore([_row(idx=1, title=long_title)]),
        now=datetime(2026, 5, 16, 12, 0, 0),
    )
    assert len(out) == 1
    assert len(out[0]["title"]) <= 80
    assert out[0]["title"].endswith("…")


def test_storage_failure_returns_empty_without_raising() -> None:
    class BrokenStore:
        def query_events(self, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("database is locked")

    out = recent_negative_exemplars(BrokenStore())
    assert out == []


def test_recency_weight_uses_half_life_default() -> None:
    """exp(-age_days / 14) — age_days=0 → weight=1; age_days=14 → ~0.37."""
    now = datetime(2026, 5, 16, 12, 0, 0)
    events = [
        _row(idx=1, title="最新", age_days=0, now=now),
        _row(idx=2, title="老一点", age_days=14, now=now),
        _row(idx=3, title="最老", age_days=30, now=now),
    ]
    out = recent_negative_exemplars(_StubEventStore(events), now=now, limit=2)
    # Should keep the two highest-weight (newest) rows.
    titles = [r["title"] for r in out]
    assert "最新" in titles
    assert "老一点" in titles
    assert "最老" not in titles


def test_helper_requests_negative_modes_from_event_store() -> None:
    """The helper must explicitly ask for negative-classified events."""
    store = _StubEventStore([])
    recent_negative_exemplars(store)
    assert store.calls, "helper should call query_events"
    assert store.calls[0]["satisfaction_modes"] == frozenset({"negative"})
