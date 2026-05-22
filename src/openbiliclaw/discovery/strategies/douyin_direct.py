"""Douyin direct-cookie discovery strategy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    trim_candidates_for_llm,
)
from openbiliclaw.sources.douyin_direct import normalize_aweme_item

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


class SupportsDouyinDirectClient(Protocol):
    async def search_aweme(self, keyword: str, *, limit: int = 30) -> list[dict[str, object]]: ...
    async def get_hot_board(self, *, limit: int = 30) -> list[dict[str, object]]: ...

    async def get_creator_posts(
        self,
        sec_uid: str,
        *,
        limit: int = 30,
    ) -> list[dict[str, object]]: ...

    async def get_recommend_feed(self, *, limit: int = 30) -> list[dict[str, object]]: ...


@dataclass
class DouyinDirectStrategy(DiscoveryStrategy):
    """Discover Douyin candidates using backend direct-cookie Web requests."""

    client: SupportsDouyinDirectClient
    llm_service: SupportsStructuredTask | None = None
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    sources: tuple[str, ...] = ("search", "hot", "feed")
    seed_keywords: tuple[str, ...] = ()
    creator_sec_uids: tuple[str, ...] = ()
    keywords_per_run: int = 5
    per_source_limit: int = 20
    llm_evaluation: bool = True
    score_threshold: float = 0.65
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "douyin_direct"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        raw_items: list[tuple[str, dict[str, object]]] = []
        keywords = self._keywords(profile)
        self.last_intermediates = {
            "sources": list(self.sources),
            "keywords": list(keywords),
            "creator_sec_uids": list(self.creator_sec_uids),
        }

        if "search" in self.sources:
            search_source_strategy = str(
                getattr(self.client, "search_source_strategy", "dy-direct-search")
                or "dy-direct-search"
            )
            for keyword in keywords:
                for item in await self.client.search_aweme(
                    keyword,
                    limit=min(self.per_source_limit, max(1, limit)),
                ):
                    raw_items.append((search_source_strategy, item))

        if "hot" in self.sources:
            hot_limit = min(self.per_source_limit, max(1, limit))
            hot_source_strategy = str(
                getattr(self.client, "hot_source_strategy", "dy-direct-hot") or "dy-direct-hot"
            )
            for item in await self.client.get_hot_board(limit=hot_limit):
                raw_items.append((hot_source_strategy, item))

        if "feed" in self.sources:
            feed_limit = min(self.per_source_limit, max(1, limit))
            feed_source_strategy = str(
                getattr(self.client, "feed_source_strategy", "dy-direct-feed") or "dy-direct-feed"
            )
            for item in await self.client.get_recommend_feed(limit=feed_limit):
                raw_items.append((feed_source_strategy, item))

        if "creator" in self.sources:
            for sec_uid in self.creator_sec_uids:
                for item in await self.client.get_creator_posts(
                    sec_uid,
                    limit=min(self.per_source_limit, max(1, limit)),
                ):
                    raw_items.append(("dy-direct-creator", item))

        candidates = self._normalize_and_dedupe(raw_items)
        if not candidates:
            return []

        if not self.llm_evaluation or self.llm_service is None:
            return candidates[:limit]

        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=self.database,
            concurrency=self.concurrency,
        )
        eval_candidates = trim_candidates_for_llm(
            candidates,
            limit=limit,
            source_context=self.name,
        )
        scores = await evaluator.evaluate_content_batch(eval_candidates, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(eval_candidates, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                break
        return results

    def _keywords(self, profile: SoulProfile) -> list[str]:
        candidates = [str(k).strip() for k in self.seed_keywords if str(k).strip()]
        if not candidates:
            candidates = [
                str(interest.name).strip()
                for interest in profile.preferences.interests
                if str(interest.name).strip()
            ]
        seen: set[str] = set()
        deduped: list[str] = []
        for keyword in candidates:
            if keyword in seen:
                continue
            seen.add(keyword)
            deduped.append(keyword)
            if len(deduped) >= self.keywords_per_run:
                break
        return deduped

    @staticmethod
    def _normalize_and_dedupe(
        raw_items: list[tuple[str, dict[str, object]]],
    ) -> list[DiscoveredContent]:
        seen: set[str] = set()
        normalized: list[DiscoveredContent] = []
        for source_strategy, item in raw_items:
            content = normalize_aweme_item(item, source_strategy=source_strategy)
            if content is None:
                continue
            key = content.content_id or content.bvid
            if key in seen:
                continue
            seen.add(key)
            normalized.append(content)
        return normalized
