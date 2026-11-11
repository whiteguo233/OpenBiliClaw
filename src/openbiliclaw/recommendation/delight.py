"""Relative delight selection adapters and legacy threshold compatibility.

Production selects the Top 10% of the eligible unread pool in storage. The
absolute thresholds below are fallbacks for older adapters and visual-bonus
eligibility; they do not veto a nonempty relative selection. This module does not compute
``delight_score`` itself: the runtime score is produced by
``RecommendationEngine.precompute_delight_scores()``, which reuses the
``relevance_score`` Evo already wrote into ``content_cache`` (a deliberate
decision to avoid a second LLM call per candidate). Catalogue signals such as
``rating_score`` / ``rating_count`` / ``source_rank`` influence that score by
entering the shared evaluator prompt via
``discovery.engine._prompt_visible_content_fields`` when non-zero, so the LLM
weighs them in context rather than through a separate weighted formula here.

A standalone embedding-based multi-signal ``DelightScorer`` lived here until
v0.3.174; it had no production call sites and was removed. If delight ever
needs its own scorer again, wire it into ``precompute_delight_scores`` rather
than reviving a parallel scoring path.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Legacy score-query and visual-bonus defaults, retained for adapter
# compatibility. Production queue membership is an identity-based Top-K of
# admitted content, not an extra 0.75/0.80 quality gate.
DEFAULT_DELIGHT_THRESHOLD: float = 0.75
CONSERVATIVE_DELIGHT_THRESHOLD: float = 0.80
_LOW_EXPLORATION_OPENNESS: float = 0.3


def effective_delight_threshold(
    exploration_openness: float,
    *,
    threshold: float = DEFAULT_DELIGHT_THRESHOLD,
) -> float:
    """Return the delight threshold adjusted for conservative users."""
    if exploration_openness < _LOW_EXPLORATION_OPENNESS:
        return max(threshold, CONSERVATIVE_DELIGHT_THRESHOLD)
    return threshold


def select_delight_candidates(
    database: Any,
    *,
    fallback_threshold: float,
    limit: int = 20,
    include_liked: bool = False,
    include_delivered: bool = False,
) -> list[dict[str, Any]]:
    """Use atomic relative selection; keep legacy storage adapters compatible."""
    select = getattr(database, "get_ranked_delight_candidates", None)
    if callable(select):
        return list(
            select(limit=limit, include_liked=include_liked, include_delivered=include_delivered)
        )
    kwargs: dict[str, Any] = {"min_delight_score": fallback_threshold, "limit": limit}
    if include_liked:
        kwargs["include_liked"] = True
    if include_delivered:
        kwargs["include_delivered"] = True
    return list(database.get_delight_candidates(**kwargs))


def count_selected_delights(database: Any, *, fallback_threshold: float) -> int:
    """Count the same undelivered selection used by proactive delivery."""
    if callable(getattr(database, "get_ranked_delight_candidates", None)):
        return len(
            select_delight_candidates(
                database, fallback_threshold=fallback_threshold, limit=2**31 - 1
            )
        )
    return int(database.count_delight_candidates(min_delight_score=fallback_threshold))
