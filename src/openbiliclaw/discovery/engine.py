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
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from openbiliclaw.discovery.strategies._utils import build_profile_summary

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
    search_budget_total: int = 30
    """Total bilibili search API calls allowed per discovery run.

    The budget is split evenly among strategies that use search
    (search, explore, related_chain) to prevent any single strategy
    from exhausting the IP-level rate limit.
    """
    _search_strategy_count: int = field(init=False, default=3, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)
    _bilibili_semaphore: asyncio.Semaphore | None = field(
        init=False, default=None, repr=False
    )
    _llm_semaphore: asyncio.Semaphore | None = field(init=False, default=None, repr=False)

    @property
    def search_budget_per_strategy(self) -> int:
        """Per-strategy share of the search API budget."""
        return max(1, self.search_budget_total // max(1, self._search_strategy_count))

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

    chat_active: bool = False
    llm_throttle_seconds: float = 2.0
    """Minimum delay between consecutive discovery LLM calls.

    Prevents discovery from saturating the LLM provider's RPM quota
    (e.g. Gemini Flash free tier = 15 RPM).  With throttle=2s, discovery
    uses at most 30 RPM of its own, leaving headroom for interactive
    chat requests.
    """

    async def run_llm(self, awaitable: Awaitable[_T]) -> _T:
        """Run one LLM-facing awaitable within the evaluation limit.

        When ``chat_active`` is True (a user dialogue is in progress),
        discovery LLM calls yield until the dialogue finishes.  This
        prevents discovery from saturating the LLM API's RPM quota and
        starving interactive chat requests.
        """
        while self.chat_active:
            await asyncio.sleep(0.5)
        self._ensure_loop_bound()
        assert self._llm_semaphore is not None
        async with self._llm_semaphore:
            result = await awaitable
            # Throttle: space out discovery LLM calls to avoid RPM exhaustion
            if self.llm_throttle_seconds > 0:
                await asyncio.sleep(self.llm_throttle_seconds)
            return result


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

    bvid: str = ""  # Bilibili video ID (legacy; prefer content_id for new code)
    title: str = ""
    up_name: str = ""  # UP主 name (legacy; prefer author_name for new code)
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

    # ── Multi-source fields (Phase 0) ───────────────────────────────
    content_id: str = ""  # Universal content ID; equals bvid for Bilibili content
    content_url: str = ""  # Direct clickable URL
    source_platform: str = ""  # "bilibili" | "xiaohongshu" | "web" | ...
    author_name: str = ""  # Universal author name; equals up_name for Bilibili

    def __post_init__(self) -> None:
        if not self.content_id and self.bvid:
            self.content_id = self.bvid
        if not self.source_platform and self.bvid:
            self.source_platform = "bilibili"
        if not self.author_name and self.up_name:
            self.author_name = self.up_name
        if not self.content_url and self.bvid:
            self.content_url = f"https://www.bilibili.com/video/{self.bvid}"

    def to_cache_kwargs(self) -> dict[str, object]:
        """Build the kwargs dict for ``Database.cache_content()``.

        Single source of truth for the DiscoveredContent → content_cache
        field mapping.  Used by discovery's ``_cache_results`` and the
        recommendation engine's ``classify_pool_backlog`` persist loop.
        """
        return {
            "title": self.title,
            "up_name": self.up_name,
            "up_mid": self.up_mid,
            "duration": self.duration,
            "tags": self.tags,
            "topic_key": self.topic_key,
            "topic_group": self.topic_group,
            "style_key": self.style_key,
            "description": self.description,
            "cover_url": self.cover_url,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
            "candidate_tier": self.candidate_tier,
            "source": self.source_strategy,
            "source_platform": self.source_platform or "bilibili",
            "content_id": self.content_id or self.bvid,
            "content_url": self.content_url,
            "author_name": self.author_name or self.up_name,
        }


# Canonical set of LLM-returned style_key values accepted by evaluation.
# Shared across discovery and recommendation — must stay in sync.
VALID_STYLE_KEYS: frozenset[str] = frozenset({
    "game_strategy", "news_brief", "practical_guide", "story_doc",
    "visual_showcase", "tech_analysis",
    "deep_dive", "fun_variety", "lifestyle", "review_roundup",
    "light_chat",
})


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

    def register_adapter(self, adapter: Any) -> None:
        """Register a :class:`SourceAdapter` for multi-source discovery.

        The adapter is stored in ``_adapter_registry`` keyed by its
        ``source_type``.  Phase 2+ will use this during recipe-driven
        discovery cycles.
        """
        if not hasattr(self, "_adapter_registry"):
            from openbiliclaw.sources.registry import AdapterRegistry

            self._adapter_registry = AdapterRegistry()
        self._adapter_registry.register(adapter)

    @property
    def adapter_registry(self) -> Any:
        """Return the adapter registry, creating it lazily if needed."""
        if not hasattr(self, "_adapter_registry"):
            from openbiliclaw.sources.registry import AdapterRegistry

            self._adapter_registry = AdapterRegistry()
        return self._adapter_registry

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
            profile_summary=build_profile_summary(profile),
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "duration": content.duration,
                "view_count": content.view_count,
                "source_strategy": content.source_strategy,
            },
            source_context=source_context or content.source_strategy,
            source_platform=content.source_platform or "bilibili",
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
        valid_styles = VALID_STYLE_KEYS

        content.relevance_score = score
        content.relevance_reason = reason
        if topic_group:
            content.topic_group = topic_group
        if style_key in valid_styles:
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
            for idx, batch_score in zip(batch_indices, batch_scores, strict=True):
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

        profile_data = build_profile_summary(profile)
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
            source_platform=(batch[0].source_platform or "bilibili") if batch else "bilibili",
        )

        valid_styles = {
            "game_strategy", "news_brief", "practical_guide", "story_doc",
            "visual_showcase", "tech_analysis",
            "deep_dive", "fun_variety", "lifestyle", "review_roundup",
            "light_chat",
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
            if style_key in valid_styles:
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
        # Split strategies into two phases to avoid B站 IP-level search
        # rate-limiting.  Search strategy runs first (Phase 1) with a
        # dedicated cookie-free client so it gets clean quota.  Other
        # strategies (explore, related_chain) also call the search API,
        # so each strategy's calls are capped by the per-strategy search
        # budget in DiscoveryConcurrencyController.
        search_strategies = [s for s in strategies if s.name == "search"]
        other_strategies = [s for s in strategies if s.name != "search"]

        results: list[DiscoveredContent] = []

        # Phase 1: run search strategy first to get clean IP quota
        if search_strategies:
            tasks = [s.discover(profile, limit=limit) for s in search_strategies]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend(self._collect_strategy_results(search_strategies, gathered))

        # Brief cooldown between phases to let IP-level rate limit recover
        if search_strategies and other_strategies:
            await asyncio.sleep(2.0)

        # Phase 2: run remaining strategies concurrently
        if other_strategies:
            tasks = [s.discover(profile, limit=limit) for s in other_strategies]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            results.extend(self._collect_strategy_results(other_strategies, gathered))

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
                    content_id=str(row.get("content_id", "") or bvid),
                    content_url=str(row.get("content_url", "")),
                    source_platform=str(row.get("source_platform", "") or "bilibili"),
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
        unique_sources = {
            ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            for item in results
            if ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
        }
        unique_source_target = min(limit, len(unique_sources))

        # Step 0: reserve minimum slots per source strategy.
        # Without a floor, high-scoring sources (related_chain) monopolize all
        # slots via the score-sorted selection, leaving low-scoring but novel
        # sources (search, explore) with zero representation.
        n_sources = max(1, len(unique_sources))
        per_source_floor = max(1, limit // n_sources) if unique_sources else 0
        # Hard ceiling: no single source takes more than ~35% of results,
        # even if it has unlimited topic diversity (e.g. trending).
        per_source_ceiling = max(per_source_floor + 1, limit * 35 // 100)
        reserved, unreserved = ContentDiscoveryEngine._reserve_per_source(
            results,
            per_source_floor=per_source_floor,
            unique_sources=unique_sources,
        )

        # Step 1: select diverse subset from unreserved pool.
        # Pass reserved items' topics/sources so _select_diverse knows what
        # has already been committed.
        remaining_limit = limit - len(reserved)
        reserved_topics = {
            ContentDiscoveryEngine._topic_bucket(i) for i in reserved
        } - {""}
        reserved_sources = {
            ContentDiscoveryEngine._normalize_topic_token(i.source_strategy)
            for i in reserved
        } - {""}
        selected, overflow = ContentDiscoveryEngine._select_diverse(
            unreserved,
            limit=remaining_limit,
            per_style_cap=per_style_cap,
            per_source_cap=max(1, per_source_cap - per_source_floor),
            unique_source_target=unique_source_target,
            initial_seen_topics=reserved_topics,
            initial_seen_sources=reserved_sources,
        )

        # Combine reserved + selected
        combined = list(reserved)
        reserved_bvids = {item.bvid for item in reserved}
        for item in selected:
            if item.bvid not in reserved_bvids:
                combined.append(item)
        if len(combined) >= limit:
            return combined[:limit]

        # Step 2: backfill from overflow with relaxed constraints
        combined = ContentDiscoveryEngine._backfill_from_overflow(
            combined, overflow,
            limit=limit,
            per_style_cap=per_style_cap,
            per_source_cap=per_source_cap,
            per_source_ceiling=per_source_ceiling,
        )
        return combined[:limit]

    @staticmethod
    def _reserve_per_source(
        results: list[DiscoveredContent],
        *,
        per_source_floor: int,
        unique_sources: set[str],
    ) -> tuple[list[DiscoveredContent], list[DiscoveredContent]]:
        """Reserve the best items from each source to guarantee representation.

        Returns (reserved, unreserved) where reserved contains at most
        *per_source_floor* items per source (the highest-scored ones),
        and unreserved contains everything else.
        """
        if per_source_floor <= 0:
            return [], list(results)

        source_buckets: dict[str, list[DiscoveredContent]] = {s: [] for s in unique_sources}
        for item in results:
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source in source_buckets:
                source_buckets[source].append(item)

        reserved: list[DiscoveredContent] = []
        reserved_bvids: set[str] = set()
        # Track topics across ALL sources to avoid reserving duplicate topics
        global_seen_topics: set[str] = set()
        source_counts: dict[str, int] = {s: 0 for s in unique_sources}

        # Round-robin: iterate by score across all sources, reserving items
        # until each source reaches its floor.  Skip items whose topic is
        # already reserved (from any source) to maximise topic diversity.
        for item in results:
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source not in source_counts or source_counts[source] >= per_source_floor:
                continue
            topic = ContentDiscoveryEngine._topic_bucket(item)
            if topic and topic in global_seen_topics:
                continue
            reserved.append(item)
            reserved_bvids.add(item.bvid)
            source_counts[source] += 1
            if topic:
                global_seen_topics.add(topic)

        unreserved = [item for item in results if item.bvid not in reserved_bvids]
        return reserved, unreserved

    @staticmethod
    def _select_diverse(
        results: list[DiscoveredContent],
        *,
        limit: int,
        per_style_cap: int,
        per_source_cap: int,
        unique_source_target: int,
        initial_seen_topics: set[str] | None = None,
        initial_seen_sources: set[str] | None = None,
    ) -> tuple[list[DiscoveredContent], list[DiscoveredContent]]:
        """Select a diverse subset, deferring duplicates to overflow."""
        selected: list[DiscoveredContent] = []
        overflow: list[DiscoveredContent] = []
        seen_topics: set[str] = set(initial_seen_topics or ())
        seen_sources: set[str] = set(initial_seen_sources or ())
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
            # Prioritize source representation: defer items from already-seen
            # sources until all unique sources have at least one entry.
            if (
                not is_new_source
                and source
                and source in seen_sources
                and len(seen_sources) < unique_source_target
            ):
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
        per_source_ceiling: int = 0,
    ) -> list[DiscoveredContent]:
        """Fill remaining slots from overflow with relaxed topic constraint.

        Enforces a per-topic-group cap so that no single topic_group
        dominates the final result set (max ~20% of limit), and a
        per-source ceiling so that no single source exceeds ~35%.
        """
        # Per-topic cap: no single topic_group takes more than ~20% of results.
        # For small limits (≤5) this is 1, preserving strict topic dedup.
        per_topic_cap = max(1, limit // 5)
        # Hard source ceiling: even with infinite topic diversity, a single
        # source cannot take more than this many slots in total.
        source_ceiling = (
            per_source_ceiling
            if per_source_ceiling > 0
            else max(per_source_cap + 1, limit * 35 // 100)
        )

        topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for item in selected:
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 1: allow new or under-cap topics from overflow
        remaining: list[DiscoveredContent] = []
        for item in overflow:
            if len(selected) >= limit:
                break
            topic = ContentDiscoveryEngine._topic_bucket(item)
            style = ContentDiscoveryEngine._style_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if topic and topic_counts.get(topic, 0) >= per_topic_cap:
                remaining.append(item)
                continue
            if style and style_counts.get(style, 0) >= per_style_cap:
                remaining.append(item)
                continue
            if source and source_counts.get(source, 0) >= source_ceiling:
                remaining.append(item)
                continue
            selected.append(item)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
            if style:
                style_counts[style] = style_counts.get(style, 0) + 1
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1

        # Pass 2: fill remaining with soft caps (topic ≤30%, source ≤ ceiling)
        max_per_topic = max(per_topic_cap + 1, limit * 3 // 10)
        leftover: list[DiscoveredContent] = []
        for item in remaining:
            if len(selected) >= limit:
                break
            topic = ContentDiscoveryEngine._topic_bucket(item)
            source = ContentDiscoveryEngine._normalize_topic_token(item.source_strategy)
            if source and source_counts.get(source, 0) >= source_ceiling:
                leftover.append(item)
                continue
            if topic and topic_counts.get(topic, 0) >= max_per_topic:
                leftover.append(item)
                continue
            selected.append(item)
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
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
                self._database.cache_content(item.bvid, **item.to_cache_kwargs())
            except Exception:
                logger.exception("Failed to cache discovered content: %s", item.bvid)
