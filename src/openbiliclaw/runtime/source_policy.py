"""Shared discovery source switch and pool-share policy."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from typing import Any

from openbiliclaw.sources.web_recipe import is_compiled_public_web_recipe

logger = logging.getLogger(__name__)

SOURCE_ORDER = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "github",
    "zhihu",
    "reddit",
    "bangumi",
    "linuxdo",
    "v2ex",
    "web",
    "weibo",
)
DEFAULT_SOURCE_ENABLED = {
    "bilibili": True,
    "xiaohongshu": False,
    "douyin": False,
    "youtube": False,
    "twitter": False,
    "github": False,
    "zhihu": False,
    "reddit": False,
    "bangumi": False,
    "linuxdo": False,
    "v2ex": False,
    "weibo": False,
    "web": False,
}
DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 5,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
    "twitter": 1,
    "github": 1,
    "zhihu": 1,
    "reddit": 1,
    "bangumi": 1,
    "linuxdo": 1,
    "v2ex": 1,
    "weibo": 1,
    "web": 1,
}


def enabled_source_recipe_available(database: Any, source: str) -> bool:
    """Return whether storage has an enabled recipe usable by this source."""
    getter = getattr(database, "get_enabled_recipes", None)
    if not callable(getter):
        return False
    try:
        try:
            recipes = getter(source_type=source)
        except TypeError:
            recipes = [
                recipe
                for recipe in getter()
                if str(recipe.get("source_type") or "").strip().lower() == source
            ]
        if source == "web":
            return any(is_compiled_public_web_recipe(recipe) for recipe in recipes)
        return bool(recipes)
    except Exception:
        logger.debug("enabled source-recipe lookup failed: source=%s", source, exc_info=True)
        return False


def source_enabled_map(
    config: Any,
    *,
    enabled_recipe_available: Callable[[str], bool] | None = None,
) -> dict[str, bool]:
    """Return enabled state for pool-accounted discovery sources.

    Generic Web is recipe-driven, so its config switch alone cannot reserve
    candidate-pool capacity.  Callers that can inspect recipe storage provide
    ``enabled_recipe_available``; omitting it keeps Web disabled and therefore
    fail-closed when no enabled recipe has been observed.
    """

    sources_cfg = getattr(config, "sources", None)
    enabled: dict[str, bool] = {}
    for source in SOURCE_ORDER:
        source_cfg = getattr(sources_cfg, source, None) if sources_cfg is not None else None
        default = DEFAULT_SOURCE_ENABLED[source]
        configured = bool(getattr(source_cfg, "enabled", default))
        if source == "web":
            recipe_available = enabled_recipe_available is not None and bool(
                enabled_recipe_available(source)
            )
            enabled[source] = configured and recipe_available
        else:
            enabled[source] = configured
    return {source: enabled.get(source, False) for source in SOURCE_ORDER}


def effective_pool_source_shares(
    config: Any,
    *,
    enabled_recipe_available: Callable[[str], bool] | None = None,
) -> dict[str, int]:
    """Return configured source shares after disabled sources are removed."""

    scheduler = getattr(config, "scheduler", None)
    raw_shares = getattr(scheduler, "pool_source_shares", None)
    shares = _normalize_shares(raw_shares)
    enabled = source_enabled_map(
        config,
        enabled_recipe_available=enabled_recipe_available,
    )
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

    shares: dict[str, int] = dict(DEFAULT_POOL_SOURCE_SHARES)
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
