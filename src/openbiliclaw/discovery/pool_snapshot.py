"""Pool distribution snapshot helpers for discovery planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

_FRANCHISE_SATURATION_THRESHOLD = 10


class PoolStatsDatabase(Protocol):
    def count_pool_candidates(self) -> int: ...

    def count_pool_candidates_by_source(self) -> dict[str, int]: ...

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]: ...


@dataclass(frozen=True)
class PoolDistributionSnapshot:
    pool_target_count: int
    pool_available_count: int
    source_targets: dict[str, int]
    source_counts: dict[str, int]
    source_deficits: dict[str, int]
    saturated_topics: tuple[str, ...] = ()
    saturated_styles: tuple[str, ...] = ()
    saturated_franchises: tuple[str, ...] = ()
    undercovered_axes: tuple[str, ...] = ()

    def to_prompt_hints(self) -> dict[str, object]:
        return {
            "avoid_topics": list(self.saturated_topics[:12]),
            "avoid_styles": list(self.saturated_styles[:8]),
            "avoid_franchises": list(self.saturated_franchises[:8]),
            "prefer_axes": list(self.undercovered_axes[:8]),
            "source_deficits": _top_positive_counts(self.source_deficits, limit=8),
        }


def build_pool_distribution_snapshot(
    db: PoolStatsDatabase,
    *,
    pool_target_count: int,
    source_targets: dict[str, int],
) -> PoolDistributionSnapshot:
    """Build a compact pool coverage summary for later discovery prompts."""
    target_count = max(0, int(pool_target_count))
    clean_source_targets = {
        str(source).strip(): max(0, int(target))
        for source, target in source_targets.items()
        if str(source).strip()
    }
    pool_available_count = db.count_pool_candidates()
    source_counts = db.count_pool_candidates_by_source()
    source_deficits = {
        source: max(0, target - int(source_counts.get(source, 0)))
        for source, target in clean_source_targets.items()
    }
    distribution_counts = db.get_pool_distribution_counts()

    topic_threshold = max(8, target_count // 20)
    style_threshold = max(12, target_count // 8)
    franchise_threshold = _FRANCHISE_SATURATION_THRESHOLD

    saturated_topics = _keys_at_or_above(
        distribution_counts.get("topic_group", {}),
        topic_threshold,
    )
    saturated_styles = _keys_at_or_above(
        distribution_counts.get("style_key", {}),
        style_threshold,
    )
    saturated_franchises = _keys_at_or_above(
        distribution_counts.get("franchise_key", {}),
        franchise_threshold,
    )
    return PoolDistributionSnapshot(
        pool_target_count=target_count,
        pool_available_count=pool_available_count,
        source_targets=clean_source_targets,
        source_counts=dict(source_counts),
        source_deficits=source_deficits,
        saturated_topics=saturated_topics,
        saturated_styles=saturated_styles,
        saturated_franchises=saturated_franchises,
    )


def _keys_at_or_above(counts: dict[str, int], threshold: int) -> tuple[str, ...]:
    if threshold <= 0:
        return tuple(counts)
    return tuple(
        key
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if int(count) >= threshold
    )


def _top_positive_counts(counts: dict[str, int], *, limit: int) -> dict[str, int]:
    if limit <= 0:
        return {}
    positive_counts = (
        (key, count)
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count > 0
    )
    return dict(list(positive_counts)[:limit])
