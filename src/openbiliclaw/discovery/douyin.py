"""Reusable Douyin discovery orchestration.

This module keeps Douyin direct-cookie discovery usable from CLI,
runtime producers, tests, and future API endpoints without duplicating
strategy construction in each caller.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.discovery.strategies.douyin_direct import (
    DouyinDirectStrategy,
    SupportsDouyinDirectClient,
)

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile


@dataclass(frozen=True)
class DouyinDiscoveryOptions:
    """Options for one Douyin direct discovery run."""

    limit: int = 30
    sources: tuple[str, ...] = ("search", "hot", "feed")
    keywords: tuple[str, ...] = ()
    creator_sec_uids: tuple[str, ...] = ()
    cache: bool = True
    evaluate: bool = True
    per_source_limit: int = 20
    keywords_per_run: int = 5


@dataclass(frozen=True)
class DouyinDiscoveryResult:
    """Result summary for a Douyin discovery run."""

    items: list[DiscoveredContent]
    cached: bool
    source_counts: dict[str, int]


class DouyinDiscoveryService:
    """Run Douyin direct discovery through a reusable service boundary."""

    def __init__(
        self,
        *,
        client: SupportsDouyinDirectClient,
        discovery_engine: Any | None = None,
        llm_service: Any | None = None,
        concurrency: Any | None = None,
        database: Any | None = None,
    ) -> None:
        self._client = client
        self._discovery_engine = discovery_engine
        self._llm_service = llm_service
        self._concurrency = concurrency
        self._database = database

    async def discover(
        self,
        profile: SoulProfile,
        options: DouyinDiscoveryOptions | None = None,
    ) -> DouyinDiscoveryResult:
        """Run one Douyin discovery cycle."""
        opts = options or DouyinDiscoveryOptions()
        limit = max(1, opts.limit)
        strategy = self._build_strategy(opts)

        if opts.cache and self._discovery_engine is not None:
            self._discovery_engine.register_strategy(strategy)
            raw_items = await self._discovery_engine.discover(
                profile,
                strategies=[strategy.name],
                limit=limit,
            )
            items = _douyin_items(raw_items)
            return DouyinDiscoveryResult(
                items=items,
                cached=True,
                source_counts=_source_counts(items),
            )

        items = await strategy.discover(profile, limit=limit)
        return DouyinDiscoveryResult(
            items=items,
            cached=False,
            source_counts=_source_counts(items),
        )

    def _build_strategy(self, opts: DouyinDiscoveryOptions) -> DouyinDirectStrategy:
        llm_service = self._llm_service
        concurrency = self._concurrency
        database = self._database
        if self._discovery_engine is not None:
            llm_service = llm_service or getattr(self._discovery_engine, "_llm_service", None)
            concurrency = concurrency or getattr(self._discovery_engine, "_concurrency", None)
            database = database or getattr(self._discovery_engine, "_database", None)

        return DouyinDirectStrategy(
            client=self._client,
            llm_service=llm_service,
            concurrency=concurrency,
            database=database,
            sources=opts.sources,
            seed_keywords=opts.keywords,
            creator_sec_uids=opts.creator_sec_uids,
            keywords_per_run=max(1, opts.keywords_per_run),
            per_source_limit=max(1, opts.per_source_limit),
            llm_evaluation=opts.evaluate,
        )


def split_csv_values(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Split repeated CLI/env values that may contain comma-separated entries."""
    if not values:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        for part in str(value).split(","):
            token = part.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
    return tuple(result)


def _source_counts(items: list[DiscoveredContent]) -> dict[str, int]:
    counts = Counter(str(item.source_strategy or "douyin_direct") for item in items)
    return dict(counts)


def _douyin_items(raw_items: list[Any]) -> list[DiscoveredContent]:
    return [
        item
        for item in raw_items
        if isinstance(item, DiscoveredContent) and item.source_platform == "douyin"
    ]
