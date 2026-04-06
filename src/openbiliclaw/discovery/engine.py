"""Content Discovery Engine.

Coordinates multiple discovery strategies to find content
that matches the user's soul profile.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass
class DiscoveryConcurrencyController:
    """Shared bounded concurrency for external discovery dependencies."""

    bilibili_request_concurrency: int = 2
    llm_evaluation_concurrency: int = 2
    _loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)
    _bilibili_semaphore: asyncio.Semaphore | None = field(
        init=False, default=None, repr=False
    )
    _llm_semaphore: asyncio.Semaphore | None = field(init=False, default=None, repr=False)

    def _ensure_loop_bound(self) -> None:
        """Recreate semaphores when the controller is used from a new event loop."""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        self._loop = loop
        self._bilibili_semaphore = asyncio.Semaphore(
            max(1, self.bilibili_request_concurrency)
        )
        self._llm_semaphore = asyncio.Semaphore(max(1, self.llm_evaluation_concurrency))

    async def run_bilibili(self, awaitable: Awaitable[_T]) -> _T:
        """Run one Bilibili-facing awaitable within the request limit."""
        self._ensure_loop_bound()
        assert self._bilibili_semaphore is not None
        async with self._bilibili_semaphore:
            return await awaitable

    async def run_llm(self, awaitable: Awaitable[_T]) -> _T:
        """Run one LLM-facing awaitable within the evaluation limit."""
        self._ensure_loop_bound()
        assert self._llm_semaphore is not None
        async with self._llm_semaphore:
            return await awaitable


class SupportsStructuredTask(Protocol):
    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> object: ...


@dataclass
class DiscoveredContent:
    """A piece of content discovered by the engine."""

    bvid: str = ""  # Bilibili video ID
    title: str = ""
    up_name: str = ""  # UP主 name
    up_mid: int = 0  # UP主 ID
    cover_url: str = ""
    duration: int = 0  # seconds
    view_count: int = 0
    like_count: int = 0
    tags: list[str] = field(default_factory=list)
    topic_key: str = ""
    topic_group: str = ""  # Coarse semantic category (e.g. "强化学习") for diversity
    style_key: str = ""
    description: str = ""
    source_strategy: str = ""  # Which strategy found this
    relevance_score: float = 0.0  # 0.0 - 1.0 (based on user soul)
    relevance_reason: str = ""  # Why this is relevant to the user
    pool_expression: str = ""  # Precomputed recommendation copy for fast popup paths
    pool_topic_label: str = ""  # Precomputed personalized topic label for fast popup paths
    candidate_tier: str = "primary"  # Primary discovery vs backfill supply
    discovered_at: str = ""  # Cache timestamp for recency-aware ranking
    last_scored_at: str = ""  # Last relevance scoring timestamp


class DiscoveryStrategy(ABC):
    """Base class for content discovery strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    @abstractmethod
    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
        """Execute the discovery strategy.

        Args:
            profile: Current user soul profile for relevance guidance.
            limit: Maximum number of items to return.

        Returns:
            List of discovered content items.
        """
        ...

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        """Return an expanded/relaxed variant for supply backfill if supported."""
        return None


class ContentDiscoveryEngine:
    """Orchestrates multiple discovery strategies.

    Available strategies:
    - Search: keyword-based search from user interests
    - Related: follow related recommendation chains
    - Trending: scan trending/ranking content
    - Comments: mine recommendations from comment sections
    - UPTrack: track followed/discovered UP主
    - Explore: cross-domain surprise discovery
    """

    def __init__(
        self,
        llm_service: SupportsStructuredTask | None = None,
        database: Database | None = None,
        *,
        concurrency: DiscoveryConcurrencyController | None = None,
        embedding_service: Any | None = None,
        target_primary_count: int = 20,
        backfill_target_count: int = 40,
    ) -> None:
        self._strategies: list[DiscoveryStrategy] = []
        self._llm_service = llm_service
        self._database = database
        self._concurrency = concurrency
        self._embedding_service = embedding_service
        self._target_primary_count = max(1, target_primary_count)
        self._backfill_target_count = max(self._target_primary_count, backfill_target_count)
        self._eval_cache: dict[str, tuple[float, str, str, str]] = {}

    def register_strategy(self, strategy: DiscoveryStrategy) -> None:
        """Register a discovery strategy."""
        self._strategies.append(strategy)
        logger.info("Registered discovery strategy: %s", strategy.name)

    async def discover(
        self,
        profile: SoulProfile,
        strategies: list[str] | None = None,
        limit: int = 30,
    ) -> list[DiscoveredContent]:
        """Run discovery with selected (or all) strategies.

        Args:
            profile: User soul profile for relevance evaluation.
            strategies: Optional list of strategy names to run.
                       If None, runs all registered strategies.

        Returns:
            Combined, deduplicated, and scored list of discovered content.
        """
        active = self._strategies
        if strategies:
            active = [s for s in self._strategies if s.name in strategies]

        if not active:
            return []

        effective_limit = max(1, min(limit, self._backfill_target_count))
        primary_results = await self._run_strategies(
            active,
            profile=profile,
            limit=effective_limit,
        )
        # Normalize topic_group using embeddings before dedup
        merged_primary = self._merge_and_rank(primary_results)
        await self._normalize_topic_groups(merged_primary)
        await self._normalize_topic_keys(merged_primary)
        final_results = self._compress_topic_repeats(
            merged_primary,
            limit=effective_limit,
        )

        primary_target = min(self._target_primary_count, effective_limit)
        if len(final_results) < primary_target:
            backfill_results = await self._run_backfill(
                active,
                profile=profile,
                limit=effective_limit,
                existing=final_results,
            )
            all_results = self._merge_and_rank([*final_results, *backfill_results])
            await self._normalize_topic_groups(all_results)
            await self._normalize_topic_keys(all_results)
            final_results = self._compress_topic_repeats(
                all_results,
                limit=effective_limit,
            )

        self._cache_results(final_results)
        return final_results

    async def _normalize_topic_groups(
        self,
        results: list[DiscoveredContent],
    ) -> None:
        """Assign topic_group to items that lack one via embedding similarity.

        Items that already have a topic_group are trusted as-is — they were
        set by LLM evaluation or strategy-level inference and are already
        coarse labels.  Re-merging short Chinese labels via embedding produces
        false positives (e.g. "国际史实" → "人工智能" at threshold 0.82)
        because short text embeddings are deceptively close in cosine space.

        This method only operates on items WITHOUT a topic_group, attempting
        to assign them to an existing cluster from items that do have one.
        """
        if self._embedding_service is None or not results:
            return

        from openbiliclaw.llm.embedding import cosine_similarity

        # Build cluster centroids from items that already have a topic_group
        clusters: dict[str, list[float]] = {}
        for item in results:
            group = (item.topic_group or "").strip().lower()
            if not group or group in clusters:
                continue
            vec = await self._embedding_service.embed(group)
            if vec:
                clusters[group] = vec

        if not clusters:
            return

        # Only try to assign topic_group to items that don't have one
        # Use a stricter threshold for short-label merging
        threshold = min(0.92, self._embedding_service.similarity_threshold + 0.10)
        for item in results:
            if (item.topic_group or "").strip():
                continue
            topic = (item.topic_key or "").strip().lower()
            if not topic:
                continue
            vec = await self._embedding_service.embed(topic)
            if not vec:
                continue

            best_label: str | None = None
            best_sim = 0.0
            for label, centroid in clusters.items():
                sim = cosine_similarity(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_label = label

            if best_label is not None and best_sim >= threshold:
                item.topic_group = best_label
                logger.debug(
                    "Topic assigned: %r → %r (sim=%.3f)", topic, best_label, best_sim,
                )

    async def _normalize_topic_keys(
        self,
        results: list[DiscoveredContent],
    ) -> None:
        """Normalize topic_keys across strategies via embedding-based clustering.

        Different strategies produce topic_keys at different granularities:
        - search: fine-grained LLM phrases ("moba经济曲线动态博弈")
        - trending/related_chain: B站 tname categories ("网络游戏")
        - explore: domain labels ("精密机械钟表修复与微观结构")

        This method clusters semantically similar keys and reassigns them
        to a canonical representative, so downstream diversity logic in
        _compress_topic_repeats correctly recognizes same-topic items.
        """
        if self._embedding_service is None or not results:
            return

        from openbiliclaw.llm.embedding import cosine_similarity

        # Step 1: Collect unique topic_keys and embed them
        unique_keys: list[str] = []
        seen: set[str] = set()
        for item in results:
            key = (item.topic_key or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique_keys.append(key)

        if len(unique_keys) <= 1:
            return

        # Embed all unique keys
        key_vectors: dict[str, list[float]] = {}
        for key in unique_keys:
            vec = await self._embedding_service.embed(key)
            if vec:
                key_vectors[key] = vec

        if len(key_vectors) <= 1:
            return

        # Step 2: Greedy agglomerative clustering
        threshold = self._embedding_service.similarity_threshold  # ~0.82
        clusters: list[tuple[str, list[str]]] = []

        for key, vec in key_vectors.items():
            best_cluster_idx: int | None = None
            best_sim = 0.0
            for idx, (canonical, _members) in enumerate(clusters):
                centroid = key_vectors.get(canonical)
                if centroid is None:
                    continue
                sim = cosine_similarity(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster_idx = idx

            if best_cluster_idx is not None and best_sim >= threshold:
                clusters[best_cluster_idx][1].append(key)
            else:
                clusters.append((key, [key]))

        # Step 3: For each cluster, pick canonical label (medium-length preferred)
        canonical_map: dict[str, str] = {}  # original_key → canonical_key
        for _canonical, members in clusters:
            if len(members) <= 1:
                continue
            best_label = members[0]
            best_score = self._label_quality_score(members[0])
            for member in members[1:]:
                score = self._label_quality_score(member)
                if score > best_score:
                    best_score = score
                    best_label = member
            for member in members:
                if member != best_label:
                    canonical_map[member] = best_label

        if not canonical_map:
            return

        # Step 4: Reassign topic_key on items
        for item in results:
            key = (item.topic_key or "").strip().lower()
            canonical = canonical_map.get(key)
            if canonical:
                logger.debug(
                    "Topic key normalized: %r → %r (strategy=%s)",
                    item.topic_key, canonical, item.source_strategy,
                )
                item.topic_key = canonical

    @staticmethod
    def _label_quality_score(label: str) -> float:
        """Score a topic label for use as canonical representative.

        Prefers medium-length labels (4-8 chars) that are descriptive
        but not overly specific.
        """
        length = len(label)
        if length <= 2:
            return 0.2
        if length <= 4:
            return 0.6
        if length <= 8:
            return 1.0
        if length <= 12:
            return 0.7
        return 0.4

    async def evaluate_content(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        source_context: str = "",
    ) -> float:
        """Evaluate how relevant a piece of content is for the user.

        The core evaluation is based on the user's Soul — their deep personality
        and interests — not just surface-level metrics.

        Args:
            content: Content to evaluate.
            profile: User's soul profile.
            source_context: Discovery context hint for calibrating evaluation,
                e.g. "search_query: 纪录片 原理" or "explore_domain: 城市建筑叙事".

        Returns:
            Relevance score (0.0 - 1.0).
        """
        if self._llm_service is None:
            return 0.0

        # Check eval cache (same bvid in same profile → same score)
        cache_key = f"{content.bvid}:{id(profile)}"
        cached = self._eval_cache.get(cache_key)
        if cached is not None:
            score, reason, topic_group, style_key = cached
            content.relevance_score = score
            content.relevance_reason = reason
            if topic_group:
                content.topic_group = topic_group
            if style_key:
                content.style_key = style_key
            return score

        # Embedding pre-filter: skip LLM call for content with very low
        # similarity to any user interest (saves API cost)
        if self._embedding_service is not None and profile.preferences.interests:
            from openbiliclaw.llm.embedding import cosine_similarity

            content_text = f"{content.title} {content.description or ''}"
            content_vec = await self._embedding_service.embed(content_text)
            if content_vec:
                max_sim = 0.0
                for interest_item in profile.preferences.interests[:10]:
                    interest_vec = await self._embedding_service.embed(interest_item.name)
                    if interest_vec:
                        sim = cosine_similarity(content_vec, interest_vec)
                        if sim > max_sim:
                            max_sim = sim
                # Very low similarity to all interests AND not from explore strategy
                # (explore is intentionally cross-domain, so don't pre-filter it)
                if max_sim < 0.3 and content.source_strategy != "explore":
                    content.relevance_score = round(max_sim * 0.5, 4)
                    content.relevance_reason = "embedding 预过滤: 与所有兴趣相似度极低"
                    self._eval_cache[cache_key] = (
                        content.relevance_score, content.relevance_reason, "", "",
                    )
                    return content.relevance_score

        from openbiliclaw.llm.prompts import build_content_evaluation_prompt

        messages = build_content_evaluation_prompt(
            profile_summary={
                "personality_portrait": profile.personality_portrait,
                "core_traits": profile.core_traits[:5],
                "deep_needs": profile.deep_needs[:5],
                "interests": [
                    {
                        "name": item.name,
                        "category": item.category,
                        "weight": item.weight,
                    }
                    for item in profile.preferences.interests[:10]
                ],
            },
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "duration": content.duration,
                "view_count": content.view_count,
                "source_strategy": content.source_strategy,
            },
            source_context=source_context or content.source_strategy,
        )
        try:
            llm_call = self._llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
            )
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
            payload = json.loads(str(getattr(response, "content", "")).strip())
            if not isinstance(payload, dict):
                return 0.0
            score = self._clamp_score(payload.get("score", 0.0))
            reason = str(payload.get("reason", "")).strip()
            topic_group = str(payload.get("topic_group", "")).strip()
            style_key = str(payload.get("style_key", "")).strip().lower()
        except Exception:
            logger.exception("Failed to evaluate discovered content: %s", content.bvid)
            return 0.0

        # Validate LLM-returned style_key against allowed values
        _VALID_STYLES = {
            "game_strategy", "news_brief", "practical_guide", "story_doc",
            "visual_showcase", "tech_analysis", "philosophy_culture",
            "deep_dive", "light_chat",
        }

        content.relevance_score = score
        content.relevance_reason = reason
        if topic_group:
            content.topic_group = topic_group
        if style_key in _VALID_STYLES:
            content.style_key = style_key
        self._eval_cache[cache_key] = (score, reason, topic_group, style_key)
        return score

    async def evaluate_content_batch(
        self,
        contents: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
        batch_size: int = 10,
    ) -> list[float]:
        """Evaluate multiple content items with batched LLM calls.

        Groups items into batches of ``batch_size`` and sends one LLM
        call per batch instead of one per item.  Falls back to single
        evaluation for items that fail in a batch.

        Returns scores in the same order as ``contents``.
        """
        if self._llm_service is None or not contents:
            return [0.0] * len(contents)

        # Split into cached vs uncached
        uncached_indices: list[int] = []
        scores: list[float] = [0.0] * len(contents)
        for i, content in enumerate(contents):
            cache_key = f"{content.bvid}:{id(profile)}"
            cached = self._eval_cache.get(cache_key)
            if cached is not None:
                score, reason, topic_group, style_key = cached
                content.relevance_score = score
                content.relevance_reason = reason
                if topic_group:
                    content.topic_group = topic_group
                if style_key:
                    content.style_key = style_key
                scores[i] = score
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            return scores

        # Process uncached items in batches
        for batch_start in range(0, len(uncached_indices), batch_size):
            batch_indices = uncached_indices[batch_start:batch_start + batch_size]
            batch_contents = [contents[i] for i in batch_indices]
            batch_scores = await self._evaluate_batch(
                batch_contents, profile, source_context=source_context,
            )
            for idx, batch_score in zip(batch_indices, batch_scores):
                scores[idx] = batch_score

        return scores

    async def _evaluate_batch(
        self,
        batch: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
    ) -> list[float]:
        """Send one LLM call for a batch of items."""
        from openbiliclaw.llm.prompts import build_batch_content_evaluation_prompt

        profile_data = {
            "personality_portrait": profile.personality_portrait,
            "core_traits": profile.core_traits[:5],
            "deep_needs": profile.deep_needs[:5],
            "interests": [
                {"name": item.name, "category": item.category, "weight": item.weight}
                for item in profile.preferences.interests[:10]
            ],
        }
        content_items = [
            {
                "title": c.title,
                "up_name": c.up_name,
                "description": (c.description or "")[:200],
                "duration": c.duration,
                "view_count": c.view_count,
                "source_strategy": c.source_strategy,
            }
            for c in batch
        ]
        messages = build_batch_content_evaluation_prompt(
            profile_summary=profile_data,
            content_items=content_items,
            source_context=source_context or (batch[0].source_strategy if batch else ""),
        )

        _VALID_STYLES = {
            "game_strategy", "news_brief", "practical_guide", "story_doc",
            "visual_showcase", "tech_analysis", "philosophy_culture",
            "deep_dive", "light_chat",
        }

        try:
            llm_call = self._llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                max_tokens=8192,
            )
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
            raw = str(getattr(response, "content", "")).strip()
            payload = json.loads(raw)
            # LLM may return a single dict instead of array for 1-item batches
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise ValueError(f"Expected JSON array, got {type(payload).__name__}")
        except Exception:
            logger.warning(
                "Batch evaluation failed for %d items, falling back to single eval",
                len(batch),
            )
            # Fallback: evaluate individually
            return [
                await self.evaluate_content(c, profile, source_context=source_context)
                for c in batch
            ]

        results: list[float] = []
        for i, content in enumerate(batch):
            if i >= len(payload) or not isinstance(payload[i], dict):
                results.append(0.0)
                continue
            item_result = payload[i]
            score = self._clamp_score(item_result.get("score", 0.0))
            reason = str(item_result.get("reason", "")).strip()
            topic_group = str(item_result.get("topic_group", "")).strip()
            style_key = str(item_result.get("style_key", "")).strip().lower()

            content.relevance_score = score
            content.relevance_reason = reason
            if topic_group:
                content.topic_group = topic_group
            if style_key in _VALID_STYLES:
                content.style_key = style_key

            cache_key = f"{content.bvid}:{id(profile)}"
            self._eval_cache[cache_key] = (score, reason, topic_group, style_key)
            results.append(score)

        return results

    @staticmethod
    def _clamp_score(raw_value: object) -> float:
        if isinstance(raw_value, bool | int | float):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            try:
                value = float(raw_value)
            except ValueError:
                value = 0.0
        else:
            value = 0.0
        return max(0.0, min(1.0, round(value, 4)))

    @staticmethod
    def _merge_duplicates(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        by_bvid: dict[str, DiscoveredContent] = {}
        for item in results:
            existing = by_bvid.get(item.bvid)
            if existing is None or item.relevance_score > existing.relevance_score:
                by_bvid[item.bvid] = item
        return list(by_bvid.values())

    async def _run_strategies(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
    ) -> list[DiscoveredContent]:
        # Split strategies into two phases to avoid B站 search rate-limiting.
        # Search uses WBI-signed requests that get v_voucher challenges when
        # run concurrently with heavy API traffic from other strategies.
        search_strategies = [s for s in strategies if s.name == "search"]
        other_strategies = [s for s in strategies if s.name != "search"]

        results: list[DiscoveredContent] = []

        # Phase 1: run non-search strategies concurrently
        if other_strategies:
            tasks = [s.discover(profile, limit=limit) for s in other_strategies]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend(self._collect_strategy_results(other_strategies, gathered))

        # Phase 2: run search strategies after other API traffic settles
        if search_strategies:
            tasks = [s.discover(profile, limit=limit) for s in search_strategies]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend(self._collect_strategy_results(search_strategies, gathered))

        logger.info(
            "Discovery gather returned %d results for %d strategies: %s",
            len(results),
            len(strategies),
            [s.name for s in strategies],
        )
        return results

    @staticmethod
    def _collect_strategy_results(
        strategies: list[DiscoveryStrategy],
        gathered: list[object],
    ) -> list[DiscoveredContent]:
        results: list[DiscoveredContent] = []
        for strategy, outcome in zip(strategies, gathered, strict=True):
            if isinstance(outcome, BaseException):
                logger.exception(
                    "Strategy '%s' failed: %s: %s",
                    strategy.name,
                    type(outcome).__name__,
                    outcome,
                    exc_info=outcome,
                )
                continue
            if not isinstance(outcome, list):
                logger.error(
                    "Strategy '%s' returned unexpected outcome type: %s",
                    strategy.name,
                    type(outcome).__name__,
                )
                continue
            items: list[DiscoveredContent] = outcome
            results.extend(items)
            logger.info(
                "Strategy '%s' found %d items.%s",
                strategy.name,
                len(items),
                "" if items else " (empty — all candidates filtered or generation failed)",
            )
        return results

    async def _run_backfill(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        existing: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        remaining = limit - len(existing)
        if remaining <= 0:
            return []

        backfill_strategies: list[DiscoveryStrategy | None] = []
        for strategy in strategies:
            factory = getattr(strategy, "create_backfill_strategy", None)
            if not callable(factory):
                backfill_strategies.append(None)
                continue
            backfill_strategies.append(factory())
        active_backfill = [strategy for strategy in backfill_strategies if strategy is not None]
        results: list[DiscoveredContent] = []
        if active_backfill:
            results.extend(
                await self._run_strategies(
                    active_backfill,
                    profile=profile,
                    limit=remaining,
                )
            )

        merged = self._merge_and_rank([*existing, *results])[:limit]
        if len(merged) >= limit:
            return results

        results.extend(
            self._load_cached_backfill(
                limit=limit,
                exclude_bvids={item.bvid for item in merged},
            )
        )
        return results

    def _load_cached_backfill(
        self,
        *,
        limit: int,
        exclude_bvids: set[str],
    ) -> list[DiscoveredContent]:
        if self._database is None:
            return []

        rows = self._database.get_unrecommended_content(limit=limit)
        candidates: list[DiscoveredContent] = []
        for row in rows:
            bvid = str(row.get("bvid", "")).strip()
            if not bvid or bvid in exclude_bvids:
                continue
            candidates.append(
                DiscoveredContent(
                    bvid=bvid,
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    up_mid=int(row.get("up_mid", 0) or 0),
                    duration=int(row.get("duration", 0) or 0),
                    tags=[],
                    topic_key=str(row.get("topic_key", "")),
                    topic_group=str(row.get("topic_group", "")),
                    style_key=str(row.get("style_key", "")),
                    description=str(row.get("description", "")),
                    cover_url=str(row.get("cover_url", "")),
                    view_count=int(row.get("view_count", 0) or 0),
                    like_count=int(row.get("like_count", 0) or 0),
                    source_strategy=str(row.get("source", "")),
                    relevance_score=self._clamp_score(row.get("relevance_score", 0.0)),
                    relevance_reason=str(row.get("relevance_reason", "")),
                    candidate_tier="backfill",
                    discovered_at=str(row.get("discovered_at", "")),
                    last_scored_at=str(row.get("last_scored_at", "")),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    @staticmethod
    def _merge_and_rank(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        merged = ContentDiscoveryEngine._merge_duplicates(results)
        merged.sort(
            key=lambda item: (
                item.candidate_tier != "primary",
                -item.relevance_score,
                -item.view_count,
                item.bvid,
            )
        )
        return merged

    @staticmethod
    def _compress_topic_repeats(
        results: list[DiscoveredContent],
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        if limit <= 1 or len(results) <= 1:
            return results[:limit]

        per_style_cap = ContentDiscoveryEngine._style_cap(limit)
        per_source_cap = ContentDiscoveryEngine._source_cap(limit)
        unique_source_target = min(
            limit,
            len(
                {
                    ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
                    for item in results
                    if ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
                }
            ),
        )

        # Step 1: select diverse subset — prioritize unique topics, balanced styles/sources
        selected, overflow = ContentDiscoveryEngine._select_diverse(
            results,
            limit=limit,
            per_style_cap=per_style_cap,
            per_source_cap=per_source_cap,
            unique_source_target=unique_source_target,
        )
        if len(selected) >= limit:
            return selected[:limit]

        # Step 2: backfill from overflow with relaxed constraints
        selected = ContentDiscoveryEngine._backfill_from_overflow(
            selected, overflow,
            limit=limit,
            per_style_cap=per_style_cap,
            per_source_cap=per_source_cap,
        )
        return selected[:limit]

    @staticmethod
    def _select_diverse(
        results: list[DiscoveredContent],
        *,
        limit: int,
        per_style_cap: int,
        per_source_cap: int,
        unique_source_target: int,
    ) -> tuple[list[DiscoveredContent], list[DiscoveredContent]]:
        """Select a diverse subset, deferring duplicates to overflow."""
        selected: list[DiscoveredContent] = []
        overflow: list[DiscoveredContent] = []
        seen_topics: set[str] = set()
        seen_sources: set[str] = set()
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for item in results:
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            is_new_source = (
                bool(source) and source not in seen_sources
                and len(seen_sources) < unique_source_target
            )

            if topic and topic in seen_topics:
                overflow.append(item)
                continue
            if not is_new_source and style and style_counts.get(style, 0) >= per_style_cap:
                overflow.append(item)
                continue
            if source and source_counts.get(source, 0) >= per_source_cap:
                overflow.append(item)
                continue
            if not is_new_source and source and source in seen_sources:
                overflow.append(item)
                continue

            selected.append(item)
            if topic:
                seen_topics.add(topic)
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                seen_sources.add(source)
                source_counts[source] = source_counts.get(source, 0) + 1
            if len(selected) >= limit:
                break

        return selected, overflow

    @staticmethod
    def _backfill_from_overflow(
        selected: list[DiscoveredContent],
        overflow: list[DiscoveredContent],
        *,
        limit: int,
        per_style_cap: int,
        per_source_cap: int,
    ) -> list[DiscoveredContent]:
        """Fill remaining slots from overflow with relaxed topic constraint."""
        seen_topics = {ContentDiscoveryEngine._topic_bucket(i) for i in selected} - {""}
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for item in selected:
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 1: allow new topics from overflow
        remaining: list[DiscoveredContent] = []
        for item in overflow:
            if len(selected) >= limit:
                break
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if topic and topic in seen_topics:
                remaining.append(item)
                continue
            if style and style_counts.get(style, 0) >= per_style_cap:
                remaining.append(item)
                continue
            if source and source_counts.get(source, 0) >= per_source_cap:
                remaining.append(item)
                continue
            selected.append(item)
            if topic:
                seen_topics.add(topic)
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 2: fill remaining slots with soft source cap (no single source > 40%)
        max_per_source = max(per_source_cap + 1, limit * 2 // 5)
        leftover: list[DiscoveredContent] = []
        for item in remaining:
            if len(selected) >= limit:
                break
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source and source_counts.get(source, 0) >= max_per_source:
                leftover.append(item)
                continue
            selected.append(item)
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 3: truly unconditional fill if still short
        for item in leftover:
            if len(selected) >= limit:
                break
            selected.append(item)

        return selected

    @staticmethod
    def _topic_bucket(item: DiscoveredContent) -> str:
        """Use topic_group (coarse) for diversity bucketing, fall back to topic_key."""
        if item.topic_group.strip():
            return ContentDiscoveryEngine._normalize_topic_token(item.topic_group)
        if item.topic_key.strip():
            return ContentDiscoveryEngine._normalize_topic_token(item.topic_key)
        for tag in item.tags:
            token = ContentDiscoveryEngine._normalize_topic_token(tag)
            if token:
                return token
        return ""

    @staticmethod
    def _style_bucket(item: DiscoveredContent) -> str:
        return ContentDiscoveryEngine._normalize_topic_token(item.style_key)

    @staticmethod
    def _normalize_topic_token(value: str) -> str:
        compact = re.sub(r"\s+", "", value.strip().lower())
        return compact[:32]

    @staticmethod
    def _style_cap(limit: int) -> int:
        return max(1, min(3, (limit + 1) // 3))

    @staticmethod
    def _source_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    @staticmethod
    def infer_style_key(
        *,
        title: str,
        description: str = "",
        reason: str = "",
        source_strategy: str = "",
    ) -> str:
        from openbiliclaw.discovery.style_rules import infer_style_key as _infer

        return _infer(
            title=title,
            description=description,
            reason=reason,
            source_strategy=source_strategy,
        )

    def _cache_results(self, results: list[DiscoveredContent]) -> None:
        if self._database is None or not results:
            return
        for item in results:
            try:
                self._database.cache_content(
                    item.bvid,
                    title=item.title,
                    up_name=item.up_name,
                    up_mid=item.up_mid,
                    duration=item.duration,
                    tags=item.tags,
                    topic_key=item.topic_key,
                    topic_group=item.topic_group,
                    style_key=item.style_key,
                    description=item.description,
                    cover_url=item.cover_url,
                    view_count=item.view_count,
                    like_count=item.like_count,
                    relevance_score=item.relevance_score,
                    relevance_reason=item.relevance_reason,
                    candidate_tier=item.candidate_tier,
                    source=item.source_strategy,
                )
            except Exception:
                logger.exception("Failed to cache discovered content: %s", item.bvid)
