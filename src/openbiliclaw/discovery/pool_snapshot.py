"""Pool distribution snapshot helpers for discovery planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

from openbiliclaw.discovery.style_keys import normalize_style_key

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
    cold_start: bool = False

    def to_prompt_hints(self) -> dict[str, object]:
        return {
            "cold_start": self.cold_start,
            "avoid_topics": list(self.saturated_topics[:12]),
            "avoid_styles": list(_normalized_style_values(self.saturated_styles)[:8]),
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
        _normalized_style_counts(distribution_counts.get("style_key", {})),
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


def build_cold_start_pool_snapshot(
    profile: object,
    *,
    pool_target_count: int,
    source_targets: dict[str, int] | None = None,
) -> PoolDistributionSnapshot | None:
    """Build synthetic diversity hints for the first discovery wave.

    With an empty pool there is no real saturation history yet, but the top
    profile interests can still over-dominate every strategy. Treat the
    strongest interests as soft avoid topics and the next interests/domains as
    preferred axes so prompts and reranking start broad.
    """

    from openbiliclaw.discovery.strategies._utils import build_profile_summary

    try:
        summary = build_profile_summary(profile)  # type: ignore[arg-type]
    except Exception:
        return None

    interests = _summary_interest_items(summary.get("interests"))
    domains = _summary_domain_items(summary.get("interest_domains"))
    if not interests and not domains:
        return None

    ranked_interests = sorted(interests, key=lambda item: _item_weight(item), reverse=True)
    high_weight_interests = [
        item for item in ranked_interests if _item_weight(item) >= 0.88
    ] or ranked_interests[:1]
    dominant = _unique_nonempty(
        str(item.get("name", "")).strip() for item in high_weight_interests[:2]
    )
    domain_names = _unique_nonempty(str(item.get("domain", "")).strip() for item in domains)
    secondary = _unique_nonempty(
        [
            *(
                str(item.get("name", "")).strip()
                for item in ranked_interests
                if str(item.get("name", "")).strip() not in dominant
            ),
            *domain_names,
        ]
    )
    secondary = [axis for axis in secondary if axis not in dominant]

    clean_targets = {
        str(source).strip(): max(0, int(target))
        for source, target in (source_targets or {}).items()
        if str(source).strip()
    }
    return PoolDistributionSnapshot(
        pool_target_count=max(0, int(pool_target_count)),
        pool_available_count=0,
        source_targets=clean_targets,
        source_counts={source: 0 for source in clean_targets},
        source_deficits=dict(clean_targets),
        saturated_topics=tuple(dominant),
        undercovered_axes=tuple(secondary[:12]),
        cold_start=True,
    )


def _keys_at_or_above(counts: dict[str, int], threshold: int) -> tuple[str, ...]:
    if threshold <= 0:
        return tuple(counts)
    return tuple(
        key
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if int(count) >= threshold
    )


def _normalized_style_counts(counts: dict[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for key, count in counts.items():
        style_key = normalize_style_key(key)
        if style_key:
            normalized[style_key] = normalized.get(style_key, 0) + int(count)
    return normalized


def _normalized_style_values(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        style_key = normalize_style_key(value)
        if style_key and style_key not in seen:
            normalized.append(style_key)
            seen.add(style_key)
    return tuple(normalized)


def _summary_interest_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and str(item.get("name", "")).strip()]


def _summary_domain_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        item for item in value if isinstance(item, dict) and str(item.get("domain", "")).strip()
    ]


def _item_weight(item: dict[str, object]) -> float:
    value = item.get("weight", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _unique_nonempty(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _top_positive_counts(counts: dict[str, int], *, limit: int) -> dict[str, int]:
    if limit <= 0:
        return {}
    positive_counts = (
        (key, count)
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count > 0
    )
    return dict(list(positive_counts)[:limit])
