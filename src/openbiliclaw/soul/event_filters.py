"""Filter helpers for the inferred_satisfaction signal.

The classifier in ``openbiliclaw.sources.event_format`` tags every event
at ingest time. Downstream consumers (preference analyzer, awareness
analyzer, ...) call ``filter_events_by_satisfaction`` to drop rows the
classifier marked as quick-exit or explicit-negative before feeding the
LLM.

The aliasing rule: requesting ``"unknown"`` also matches rows whose
``inferred_satisfaction`` is missing or ``None``. This lets callers opt
in to unclassified legacy events (pre-migration rows) without writing
the NULL branch by hand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def filter_events_by_satisfaction(
    events: Iterable[dict[str, Any]],
    *,
    modes: frozenset[str],
) -> list[dict[str, Any]]:
    """Return only the events whose ``inferred_satisfaction`` is in ``modes``.

    Order is preserved. An empty ``modes`` set returns ``[]`` (caller
    explicitly asked for nothing — do not silently fall back to "all").

    A row whose ``inferred_satisfaction`` is ``None`` or missing is
    treated as ``"unknown"`` so legacy rows survive when the caller
    opts into them with ``modes={"positive", "unknown"}``.
    """
    if not modes:
        return []
    keep: list[dict[str, Any]] = []
    for row in events:
        raw = row.get("inferred_satisfaction")
        category = raw if isinstance(raw, str) and raw else "unknown"
        if category in modes:
            keep.append(row)
    return keep
