"""Shared discovery source switch and pool-share policy."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube")
DEFAULT_SOURCE_ENABLED = {
    "bilibili": True,
    "xiaohongshu": False,
    "douyin": False,
    "youtube": False,
}
DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 8,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
}


def source_enabled_map(config: Any) -> dict[str, bool]:
    """Return enabled state for pool-accounted discovery sources."""

    sources_cfg = getattr(config, "sources", None)
    enabled: dict[str, bool] = {}
    for source in SOURCE_ORDER:
        source_cfg = getattr(sources_cfg, source, None) if sources_cfg is not None else None
        default = DEFAULT_SOURCE_ENABLED[source]
        enabled[source] = bool(getattr(source_cfg, "enabled", default))
    return {source: enabled.get(source, False) for source in SOURCE_ORDER}


def effective_pool_source_shares(config: Any) -> dict[str, int]:
    """Return configured source shares after disabled sources are removed."""

    scheduler = getattr(config, "scheduler", None)
    raw_shares = getattr(scheduler, "pool_source_shares", None)
    shares = _normalize_shares(raw_shares)
    enabled = source_enabled_map(config)
    return {source: share for source, share in shares.items() if enabled.get(source, False)}


def suggest_pool_source_shares(
    event_counts: Mapping[str, int] | None,
    *,
    enabled_sources: Mapping[str, bool] | None = None,
    configured_shares: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Suggest integer pool shares from observed platform event counts.

    Counts are square-root damped and scaled around the core Bilibili
    default. This keeps large imports from dominating while still giving
    active optional platforms visible quota.
    """

    enabled = _normalize_enabled_sources(enabled_sources)
    fallback = {
        source: share
        for source, share in _normalize_shares(configured_shares).items()
        if enabled.get(source, False)
    }
    counts = {
        source: max(0, int(event_counts.get(source, 0))) if event_counts else 0
        for source in SOURCE_ORDER
        if enabled.get(source, False)
    }
    if not counts or not any(counts.values()):
        return fallback

    bilibili_count = counts.get("bilibili", 0)
    if bilibili_count <= 0:
        max_count = max(counts.values())
        bilibili_count = max_count if max_count > 0 else 1
    bilibili_weight = max(math.sqrt(bilibili_count), 1.0)
    suggested: dict[str, int] = {"bilibili": fallback.get("bilibili", 8)}

    for source in SOURCE_ORDER:
        if source == "bilibili" or not enabled.get(source, False):
            continue
        count = counts.get(source, 0)
        if count <= 0:
            suggested[source] = fallback.get(source, 1)
            continue
        scaled = round((math.sqrt(count) / bilibili_weight) * suggested["bilibili"])
        suggested[source] = min(8, max(1, int(scaled)))

    return {source: suggested[source] for source in SOURCE_ORDER if source in suggested}


def _normalize_enabled_sources(enabled_sources: Mapping[str, bool] | None) -> dict[str, bool]:
    if enabled_sources is None:
        return dict(DEFAULT_SOURCE_ENABLED)
    enabled: dict[str, bool] = {}
    for source in SOURCE_ORDER:
        enabled[source] = bool(enabled_sources.get(source, DEFAULT_SOURCE_ENABLED[source]))
    return {source: enabled.get(source, False) for source in SOURCE_ORDER}


def _normalize_shares(value: Mapping[str, int] | Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return dict(DEFAULT_POOL_SOURCE_SHARES)

    shares: dict[str, int] = {}
    for raw_source, raw_share in value.items():
        source = str(raw_source).strip().lower()
        if source not in SOURCE_ORDER:
            continue
        try:
            share = int(raw_share)
        except (TypeError, ValueError):
            continue
        if share > 0:
            shares[source] = share
    return shares or dict(DEFAULT_POOL_SOURCE_SHARES)
