"""Content Discovery Engine.

Coordinates multiple discovery strategies to find content
that matches the user's soul profile.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.json_utils import extract_llm_json_list, parse_llm_json_tolerant
from openbiliclaw.llm.service import is_llm_rate_limit_error

if TYPE_CHECKING:
    from collections.abc import Awaitable, Sequence

    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
_EVALUATE_BATCH_HARD_CAP_DEFAULT: int = 90
_LLM_EVAL_OVERSAMPLE_FACTOR: int = 2
_LLM_EVAL_MIN_WINDOW: int = 6


@dataclass
class DiscoveryConcurrencyController:
    """Shared bounded concurrency for external discovery dependencies."""

    bilibili_request_concurrency: int = 2
    # Cap on simultaneous discovery LLM calls. Sized so a typical init
    # discover (4 strategies × ~8 batches each = ~32 batches) fans out
    # in a single wave rather than queueing behind the cap. Each batch
    # is a max-thinking deepseek call (~60-100s); without enough
    # concurrency we'd spend the full P4 budget waiting on the
    # semaphore (observed 17 min wall on 40 batches at concurrency=8,
    # of which only ~100s was actual LLM compute per batch).
    # deepseek has no effective RPM cap at our request sizes, so the
    # only practical limits are the local event loop overhead and the
    # ``chat_active`` yield (which still works to give interactive
    # dialogue priority).
    llm_evaluation_concurrency: int = 32
    search_budget_total: int = 30
    """Total bilibili search API calls allowed per discovery run.

    The budget is split evenly among strategies that use search
    (search, explore, related_chain) to prevent any single strategy
    from exhausting the IP-level rate limit.
    """
    _search_strategy_count: int = field(init=False, default=3, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(init=False, default=None, repr=False)
    _bilibili_semaphore: asyncio.Semaphore | None = field(init=False, default=None, repr=False)
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
        self._bilibili_semaphore = asyncio.Semaphore(max(1, self.bilibili_request_concurrency))
        self._llm_semaphore = asyncio.Semaphore(max(1, self.llm_evaluation_concurrency))

    async def run_bilibili(self, awaitable: Awaitable[_T]) -> _T:
        """Run one Bilibili-facing awaitable within the request limit."""
        self._ensure_loop_bound()
        assert self._bilibili_semaphore is not None
        async with self._bilibili_semaphore:
            return await awaitable

    chat_active: bool = False
    llm_throttle_seconds: float = 0.0
    """Minimum delay between consecutive discovery LLM calls.

    Kept at 0 for deepseek, which has no effective RPM cap at our
    request sizes. Raise above 0 when fronting a provider with a
    strict RPM ceiling (e.g. Gemini free tier at 15 RPM). The
    ``chat_active`` flag already yields the lane when a dialogue is
    in progress, so the throttle is no longer needed for chat
    protection on deepseek.
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
        caller: str = "",
        reasoning_effort: str | None = None,
    ) -> object: ...


class SupportsNegativeExemplarStore(Protocol):
    """Storage surface needed by negative-anchor cache invalidation."""

    def get_latest_event_id(self) -> int | None: ...

    def query_events(
        self,
        *,
        satisfaction_modes: frozenset[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


def llm_eval_candidate_limit(limit: int) -> int:
    """Return the pre-LLM candidate window for a requested result limit."""
    safe_limit = max(1, int(limit))
    return min(
        _EVALUATE_BATCH_HARD_CAP_DEFAULT,
        max(_LLM_EVAL_MIN_WINDOW, safe_limit * _LLM_EVAL_OVERSAMPLE_FACTOR),
    )


def trim_candidates_for_llm(
    candidates: Sequence[_T],
    *,
    limit: int,
    source_context: str,
) -> list[_T]:
    """Keep a bounded pre-LLM candidate window while preserving upstream order."""
    eval_limit = llm_eval_candidate_limit(limit)
    if len(candidates) <= eval_limit:
        return list(candidates)
    logger.info(
        "%s: trimming LLM eval candidates from %d to %d (result_limit=%d)",
        source_context,
        len(candidates),
        eval_limit,
        limit,
    )
    return list(candidates[:eval_limit])


def _parse_batch_evaluation_payload(raw: str) -> list[dict[str, Any]] | None:
    """Extract the scored result array from a provider response."""
    payload = extract_llm_json_list(
        raw,
        wrapper_keys=("results", "items", "evaluations", "scores", "data"),
        allow_singleton=True,
        item_predicate=lambda item: "score" in item,
    )
    if payload is None:
        return None
    return [dict(item) for item in payload]


def _content_result_keys(content: DiscoveredContent) -> set[str]:
    """Stable keys that may identify a content item in batched LLM results."""
    return {
        key
        for key in {
            str(getattr(content, "bvid", "") or "").strip(),
            str(getattr(content, "content_id", "") or "").strip(),
        }
        if key
    }


def _batch_results_by_content_key(
    payload: list[dict[str, Any]],
    batch: list[DiscoveredContent],
) -> dict[str, dict[str, Any]] | None:
    """Return payload entries keyed by content ID when the LLM supplied IDs."""
    valid_keys: set[str] = set()
    for content in batch:
        valid_keys.update(_content_result_keys(content))

    matched: dict[str, dict[str, Any]] = {}
    saw_identifier = False
    for item in payload:
        raw_key = str(item.get("bvid") or item.get("content_id") or "").strip()
        if not raw_key:
            continue
        saw_identifier = True
        if raw_key not in valid_keys:
            continue
        matched[raw_key] = item

    return matched if saw_identifier else None


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
    # Franchise / IP / series key tagged by the LLM at evaluation time
    # (e.g. "原神", "崩坏:星穹铁道", "ChatGPT", "塞尔达传说"). Empty
    # for general-interest content. Lets the curator down-rank items
    # in the same IP after a single dislike, and lets the
    # ``/api/recommendations`` endpoint cap how many same-franchise
    # items appear in a single response window. Better than the
    # heuristic title-substring approach (which v0.3.17 briefly tried)
    # because the LLM already saw title + description + topic and can
    # infer the IP correctly even when the title is bilingual or coded
    # ("提瓦特摄影" → 原神, "宝可梦" → 精灵宝可梦, etc.).
    franchise_key: str = ""
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
            "franchise_key": self.franchise_key,
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
VALID_STYLE_KEYS: frozenset[str] = frozenset(
    {
        "game_strategy",
        "news_brief",
        "practical_guide",
        "story_doc",
        "visual_showcase",
        "tech_analysis",
        "deep_dive",
        "fun_variety",
        "lifestyle",
        "review_roundup",
        "light_chat",
    }
)

# v0.3.50+: per-batch franchise cap for ``_evaluate_batch``. The LLM
# correctly identifies when a batch has many same-IP items (the prompt
# mandates batch-wide franchise consistency), but pre-v0.3.50 we kept
# them all and let serve()'s diversifier sort it out — by which point
# the pool was already franchise-skewed. Cap=4 lets a series have a
# small foothold in each refresh round but stops a single ``related_chain``
# excursion from dumping 13 items of the same UP into one batch.
_BATCH_FRANCHISE_CAP: int = 4

# v0.3.51+: per-batch style cap. Mirrors the franchise cap above —
# without it, a single eval_batch easily had 9-12 items of the same
# style (fun_variety / story_doc / light_chat / practical_guide all
# observed at 30-40% concentration in production). 8/30 = ~27% which
# still lets a dominant style breathe but blocks single-style
# domination of the pool.
_BATCH_STYLE_CAP: int = 8

# v0.3.50+: pool-wide franchise quota for ``_cache_results``. Once a
# franchise has this many items in the pool, new same-franchise items
# are skipped before they can compete for serve() slots. Sized at ~1.5%
# of the default pool target (600), so 9-10 items is enough breathing
# room for a series the user actively follows but not enough to skew
# the whole pool's tone.
_POOL_FRANCHISE_QUOTA: int = 10

# v0.3.50+: per-UP cap inside a single related_chain depth round.
# Without this, related_chain following a single seed could fan out
# into 13+ items of the same UP (张雪机车 was the production trigger).
_RELATED_CHAIN_PER_UP_CAP: int = 3


class DiscoveryStrategy(ABC):
    """Base class for content discovery strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    @abstractmethod
    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
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


def _strategy_accepts_pool_snapshot(fn: Any) -> bool:
    """Return whether a strategy discover callable accepts ``pool_snapshot=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "pool_snapshot" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


async def _call_strategy_discover(
    strategy: DiscoveryStrategy,
    profile: SoulProfile,
    *,
    limit: int,
    pool_snapshot: Any | None,
) -> list[DiscoveredContent]:
    discover_fn: Any = strategy.discover
    if _strategy_accepts_pool_snapshot(discover_fn):
        return cast(
            "list[DiscoveredContent]",
            await discover_fn(profile, limit=limit, pool_snapshot=pool_snapshot),
        )
    return cast("list[DiscoveredContent]", await discover_fn(profile, limit=limit))


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
        embedding_service: SupportsEmbeddingService | None = None,
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
        self._eval_cache: dict[str, tuple[float, str, str, str, str]] = {}
        # v0.3.x negative-anchors cache: (timestamp, latest_event_id,
        # exemplars). Refreshes when either the latest event id changes
        # (new negative classified) or 5 minutes have elapsed.
        self._negative_exemplars_cache: tuple[float, int | None, list[dict[str, object]]] | None = (
            None
        )

    def register_strategy(self, strategy: DiscoveryStrategy) -> None:
        """Register a discovery strategy."""
        self._strategies = [item for item in self._strategies if item.name != strategy.name]
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
        *,
        fully_parallel: bool = False,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
    ) -> list[DiscoveredContent]:
        """Run discovery with selected (or all) strategies.

        Args:
            profile: User soul profile for relevance evaluation.
            strategies: Optional list of strategy names to run.
                       If None, runs all registered strategies.
            fully_parallel: When True, skip the default two-phase split
                (search-first then others) and run every strategy in a
                single ``asyncio.gather``. Rate limiting still holds —
                ``bilibili_request_concurrency`` caps simultaneous HTTP
                requests and ``search_budget_total`` caps total search
                calls — so this only sacrifices the 2s cool-down between
                phases. Use for latency-critical flows (init bootstrap).
            strategy_limits: Optional per-strategy run limits. The final
                ``limit`` still caps returned/cached results; this only
                prevents a grouped refresh from giving every strategy the
                full platform deficit.
            pool_snapshot: Optional current pool distribution summary for
                strategies that can use pool-aware discovery guidance.

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
            fully_parallel=fully_parallel,
            strategy_limits=strategy_limits,
            pool_snapshot=pool_snapshot,
        )
        # Normalize topic_group using embeddings before dedup
        merged_primary = self._merge_and_rank(primary_results)
        await self._normalize_topic_groups(merged_primary)
        await self._normalize_topic_keys(merged_primary)
        merged_primary = self._apply_pool_snapshot_rerank(merged_primary, pool_snapshot)
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
                pool_snapshot=pool_snapshot,
            )
            all_results = self._merge_and_rank([*final_results, *backfill_results])
            await self._normalize_topic_groups(all_results)
            await self._normalize_topic_keys(all_results)
            all_results = self._apply_pool_snapshot_rerank(all_results, pool_snapshot)
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
                    "Topic assigned: %r → %r (sim=%.3f)",
                    topic,
                    best_label,
                    best_sim,
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
            canonical_key = canonical_map.get(key)
            if canonical_key:
                logger.debug(
                    "Topic key normalized: %r → %r (strategy=%s)",
                    item.topic_key,
                    canonical_key,
                    item.source_strategy,
                )
                item.topic_key = canonical_key

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

        # Check eval cache (same content identity in same profile → same score)
        cache_key = f"{self._content_identity(content)}:{id(profile)}"
        cached = self._eval_cache.get(cache_key)
        if cached is not None:
            score, reason, topic_group, style_key, franchise_key = cached
            content.relevance_score = score
            content.relevance_reason = reason
            if topic_group:
                content.topic_group = topic_group
            if style_key:
                content.style_key = style_key
            if franchise_key:
                content.franchise_key = franchise_key
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
                        content.relevance_score,
                        content.relevance_reason,
                        "",
                        "",
                        "",
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
                caller="discovery.evaluate_single",
            )
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
            payload = parse_llm_json_tolerant(str(getattr(response, "content", "")).strip())
            if not isinstance(payload, dict):
                raise ValueError("Expected JSON object from content evaluation")
            if not isinstance(payload, dict):
                return 0.0
            score = self._clamp_score(payload.get("score", 0.0))
            reason = str(payload.get("reason", "")).strip()
            topic_group = str(payload.get("topic_group", "")).strip()
            style_key = str(payload.get("style_key", "")).strip().lower()
            franchise_key = str(payload.get("franchise_key", "")).strip()
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
        if franchise_key:
            content.franchise_key = franchise_key
        self._eval_cache[cache_key] = (
            score,
            reason,
            topic_group,
            style_key,
            franchise_key,
        )
        return score

    # Safety cap applied at the evaluator level regardless of caller.
    # Strategies that over-fetch (related_chain depth-2 fanout, explore
    # with expanded budget, etc.) would otherwise dump 400+ items into a
    # single discover run. 30 keeps each strategy's evaluation bounded
    # to a single LLM call when ``batch_size`` matches the cap (v0.3.25+
    # default — see below). Truncation is top-of-list (natural ranking
    # from strategies), and a WARNING is emitted so we see when
    # strategies hit the cap.
    #
    # v0.3.52+: cap raised 30 → 90 to evaluate ~3× more candidates per
    # discovery round. Production logs (2026-05-05) routinely truncated
    # 300-480 candidates down to 30 — 90% data wasted. The 30/batch
    # constant stays so each individual LLM call is the same size,
    # but ``_run_batch`` already gathers multiple batches in parallel
    # via ``asyncio.gather``, so the new cap means 3 parallel LLM
    # batches of 30 items each. Concurrency is bounded by
    # ``llm_evaluation_concurrency`` so we don't blow up provider
    # rate limits. Combined with v0.3.51's reasoning-disabled batches
    # (~30s each), three parallel batches finish in roughly the same
    # wall time as one used to take.
    _EVALUATE_BATCH_HARD_CAP = _EVALUATE_BATCH_HARD_CAP_DEFAULT

    async def evaluate_content_batch(
        self,
        contents: list[DiscoveredContent],
        profile: SoulProfile,
        *,
        source_context: str = "",
        batch_size: int = 30,
    ) -> list[float]:
        """Evaluate multiple content items with batched LLM calls.

        Groups items into batches of ``batch_size`` and sends one LLM
        call per batch instead of one per item.  Falls back to single
        evaluation for items that fail in a batch.

        v0.3.25+ default raised from 10 → 30 to amortize the ~3500-token
        fixed prompt overhead (system rules + profile_summary) across
        more candidates: 3 calls × (3500 + 800) input ≈ 12,900 input
        tokens vs 1 call × (3500 + 2400) ≈ 5,900 — a ~54% reduction in
        evaluation cost. The matching ``max_tokens`` boost (8192 → 16384
        in the actual call below) gives the larger JSON output array
        comfortable headroom (~50 tokens × 30 items ≈ 1500 output, well
        under the new ceiling).

        Returns scores in the same order as ``contents``.
        """
        if self._llm_service is None or not contents:
            return [0.0] * len(contents)

        original_len = len(contents)
        if original_len > self._EVALUATE_BATCH_HARD_CAP:
            logger.warning(
                "evaluate_content_batch: truncating %d -> %d items (source=%s)",
                original_len,
                self._EVALUATE_BATCH_HARD_CAP,
                source_context or "mixed",
            )
            contents = contents[: self._EVALUATE_BATCH_HARD_CAP]

        scores: list[float] = [0.0] * len(contents)
        viewed_content_keys = self._recent_viewed_content_keys()
        if viewed_content_keys:
            eval_pairs = [
                (index, content)
                for index, content in enumerate(contents)
                if self._candidate_view_keys(content).isdisjoint(viewed_content_keys)
            ]
            skipped_viewed = len(contents) - len(eval_pairs)
            if skipped_viewed > 0:
                logger.info(
                    "eval_batch skipped %d recently viewed candidate(s) before LLM "
                    "(source=%s)",
                    skipped_viewed,
                    source_context or "mixed",
                )
        else:
            eval_pairs = list(enumerate(contents))

        if not eval_pairs:
            if len(scores) < original_len:
                scores = scores + [0.0] * (original_len - len(scores))
            return scores

        eval_indices = [index for index, _content in eval_pairs]
        eval_contents = [content for _index, content in eval_pairs]

        # Split into cached vs uncached. Batch eval consumes recent negative
        # exemplars, so the in-memory score cache is versioned by latest event
        # id. Otherwise a newly recorded quick-exit cannot affect candidates
        # scored earlier in the same process.
        negative_revision = self._negative_exemplar_revision()
        uncached_indices: list[int] = []
        for i, content in enumerate(eval_contents):
            cache_key = self._batch_eval_cache_key(
                content,
                profile,
                negative_revision=negative_revision,
            )
            cached = self._eval_cache.get(cache_key)
            if cached is not None:
                # Cache tuple grew in v0.3.18 to carry franchise_key.
                # Tolerate the legacy 4-tuple shape so an in-flight
                # process holding pre-upgrade entries doesn't crash on
                # the next eval call.
                if len(cached) == 5:
                    score, reason, topic_group, style_key, franchise_key = cached
                else:
                    score, reason, topic_group, style_key = cached
                    franchise_key = ""
                content.relevance_score = score
                content.relevance_reason = reason
                if topic_group:
                    content.topic_group = topic_group
                if style_key:
                    content.style_key = style_key
                if franchise_key:
                    content.franchise_key = franchise_key
                scores[eval_indices[i]] = score
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            if len(scores) < original_len:
                scores = scores + [0.0] * (original_len - len(scores))
            return scores

        total_batches = (len(uncached_indices) + batch_size - 1) // batch_size
        logger.info(
            "eval_batch start: source=%s items=%d batches=%d (cached=%d)",
            source_context or "mixed",
            len(uncached_indices),
            total_batches,
            len(eval_contents) - len(uncached_indices),
        )

        # Fan every batch out concurrently. The ``run_llm`` wrapper
        # already caps actual parallelism to
        # ``llm_evaluation_concurrency``, so this just lets the
        # semaphore do its job without the sequential for-loop
        # throttling us to 1 active batch per strategy.
        async def _run_batch(
            batch_idx: int,
            batch_indices: list[int],
        ) -> tuple[list[int], list[float]]:
            batch_contents = [eval_contents[i] for i in batch_indices]
            t0 = time.monotonic()
            batch_scores = await self._evaluate_batch(
                batch_contents,
                profile,
                source_context=source_context,
            )
            elapsed = time.monotonic() - t0
            kept = sum(1 for s in batch_scores if s > 0)
            # v0.3.31+: diversity snapshot of the kept items so we can
            # see whether discovery is feeding the pool with variety or
            # 30 candidates that all collapse to the same topic_group.
            kept_items = [batch_contents[i] for i, s in enumerate(batch_scores) if s > 0]
            topics: Counter[str] = Counter(
                (getattr(c, "topic_group", "") or "untagged").strip().lower() for c in kept_items
            )
            styles: Counter[str] = Counter(
                (getattr(c, "style_key", "") or "untagged").strip().lower() for c in kept_items
            )
            franchises: Counter[str] = Counter(
                (getattr(c, "franchise_key", "") or "").strip().lower() for c in kept_items
            )
            del franchises[""]  # don't count non-franchise items
            top_topic = topics.most_common(1)[0] if topics else ("", 0)
            top_franchise = franchises.most_common(1)[0] if franchises else ("", 0)
            logger.info(
                "eval_batch %d/%d done: source=%s size=%d elapsed=%.1fs kept=%d "
                "diversity={topics: %d uniq, top=%s×%d (%.0f%%); styles: %d uniq, "
                "top=%s×%d; franchises: %d uniq%s}",
                batch_idx,
                total_batches,
                source_context or "mixed",
                len(batch_indices),
                elapsed,
                kept,
                len(topics),
                top_topic[0] or "—",
                top_topic[1],
                (top_topic[1] / kept * 100) if kept else 0,
                len(styles),
                styles.most_common(1)[0][0] if styles else "—",
                styles.most_common(1)[0][1] if styles else 0,
                len(franchises),
                f", top_franchise={top_franchise[0]}×{top_franchise[1]}"
                if top_franchise[1] > 1
                else "",
            )
            return batch_indices, batch_scores

        tasks = []
        for batch_idx, batch_start in enumerate(
            range(0, len(uncached_indices), batch_size), start=1
        ):
            batch_indices = uncached_indices[batch_start : batch_start + batch_size]
            tasks.append(_run_batch(batch_idx, batch_indices))

        for batch_indices, batch_scores in await asyncio.gather(*tasks):
            for idx, batch_score in zip(batch_indices, batch_scores, strict=True):
                scores[eval_indices[idx]] = batch_score

        # Pad for any items dropped by the hard cap above so callers
        # that ``zip(candidates, scores, strict=True)`` still line up.
        if len(scores) < original_len:
            scores = scores + [0.0] * (original_len - len(scores))

        return scores

    def _recent_viewed_content_keys(self) -> set[str]:
        database = getattr(self, "_database", None)
        get_recent = getattr(database, "get_recent_viewed_content_keys", None)
        log_name = "get_recent_viewed_content_keys"
        if not callable(get_recent):
            get_recent = getattr(database, "get_recent_viewed_bvids", None)
            log_name = "get_recent_viewed_bvids"
        if not callable(get_recent):
            return set()
        try:
            raw = get_recent()
        except Exception:
            logger.debug("%s failed", log_name, exc_info=True)
            return set()
        return {str(item).strip() for item in raw if str(item).strip()}

    @staticmethod
    def _candidate_view_keys(content: DiscoveredContent) -> set[str]:
        platform = (content.source_platform or ("bilibili" if content.bvid else "")).strip().lower()
        if platform == "xhs":
            platform = "xiaohongshu"
        elif platform == "dy":
            platform = "douyin"
        elif platform == "yt":
            platform = "youtube"
        elif platform == "bili":
            platform = "bilibili"

        keys: set[str] = set()
        for value in {content.bvid, content.content_id}:
            content_id = str(value or "").strip()
            if not content_id:
                continue
            keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    def _negative_exemplar_revision(self) -> int | None:
        """Return the event-log revision used for negative-aware eval cache keys."""
        database = cast(
            "SupportsNegativeExemplarStore | None",
            getattr(self, "_database", None),
        )
        if database is None:
            return None
        try:
            latest_id = database.get_latest_event_id()
        except Exception:
            logger.debug("negative_exemplars: get_latest_event_id failed", exc_info=True)
            return None
        if latest_id is None:
            return None
        return int(latest_id)

    def _get_negative_exemplars(self) -> list[dict[str, object]] | None:
        """Return recent negative exemplars, refreshing the cache when stale.

        Cache key: (latest_event_id, time bucket). 5-minute TTL keeps the
        I/O flat across batches; latest-event-id invalidation picks up
        fresh negatives as soon as the user records one. Storage failures
        return None so the eval-batch always runs.
        """
        # Defensive getattr: some test fixtures construct the engine via
        # ``__new__`` and skip ``__init__`` entirely, so `_database` and
        # `_negative_exemplars_cache` may not exist as attributes.
        database = cast(
            "SupportsNegativeExemplarStore | None",
            getattr(self, "_database", None),
        )
        if database is None:
            return None

        from openbiliclaw.soul.negative_exemplars import recent_negative_exemplars

        latest_id = self._negative_exemplar_revision()

        cached = cast(
            "tuple[float, int | None, list[dict[str, object]]] | None",
            getattr(self, "_negative_exemplars_cache", None),
        )
        if cached is not None:
            cached_ts, cached_latest_id, cached_exemplars = cached
            if cached_latest_id == latest_id and (time.monotonic() - cached_ts) < 300:
                return cached_exemplars

        try:
            exemplars = recent_negative_exemplars(database)
        except Exception:
            logger.debug("negative_exemplars: refresh failed", exc_info=True)
            return None

        self._negative_exemplars_cache = (time.monotonic(), latest_id, exemplars)
        return exemplars

    def _batch_eval_cache_key(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
        *,
        negative_revision: int | None,
    ) -> str:
        revision = "none" if negative_revision is None else str(negative_revision)
        return f"{self._content_identity(content)}:{id(profile)}:neg:{revision}"

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
                "bvid": c.bvid,
                "content_id": c.content_id or c.bvid,
                "title": c.title,
                "up_name": c.up_name,
                "description": (c.description or "")[:200],
                "duration": c.duration,
                "view_count": c.view_count,
                "source_strategy": c.source_strategy,
            }
            for c in batch
        ]
        negative_examples = self._get_negative_exemplars()
        # Treat empty list as "no examples" so the user-message stays
        # byte-identical to the no-examples shape on cold-start users.
        if not negative_examples:
            negative_examples = None
        messages = build_batch_content_evaluation_prompt(
            profile_summary=profile_data,
            content_items=content_items,
            source_context=source_context or (batch[0].source_strategy if batch else ""),
            source_platform=(batch[0].source_platform or "bilibili") if batch else "bilibili",
            negative_examples=negative_examples,
        )

        valid_styles = {
            "game_strategy",
            "news_brief",
            "practical_guide",
            "story_doc",
            "visual_showcase",
            "tech_analysis",
            "deep_dive",
            "fun_variety",
            "lifestyle",
            "review_roundup",
            "light_chat",
        }

        assert self._llm_service is not None
        try:
            llm_call = self._llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                # v0.3.51+: explicitly disable provider thinking. This
                # task is structured scoring (return JSON array), not
                # reasoning — production logs showed 8-16 min/batch
                # with reasoning enabled, dropping to ~30s without.
                # 16384 max_tokens is plenty for the 1500-3000 token
                # output a 30-item JSON array now needs.
                max_tokens=16384,
                reasoning_effort="",
                caller="discovery.evaluate_batch",
            )
            if self._concurrency is not None:
                response = await self._concurrency.run_llm(llm_call)
            else:
                response = await llm_call
            raw = str(getattr(response, "content", "")).strip()
            payload = _parse_batch_evaluation_payload(raw)
            if payload is None:
                raise ValueError("Expected scored JSON array from batch evaluation")
        except Exception as exc:
            if is_llm_rate_limit_error(exc):
                logger.warning(
                    "Batch evaluation skipped single-item fallback for %d items because "
                    "the LLM provider is rate-limited or cooling down: %s",
                    len(batch),
                    exc,
                )
                return [0.0 for _ in batch]
            logger.warning(
                "Batch evaluation failed for %d items, falling back to single eval",
                len(batch),
            )
            # Fallback: evaluate individually
            return [
                await self.evaluate_content(c, profile, source_context=source_context)
                for c in batch
            ]

        payload_by_id = _batch_results_by_content_key(payload, batch)
        if payload_by_id is None and len(payload) != len(batch):
            logger.warning(
                "Batch evaluation result count mismatch without IDs (%d results for %d items), "
                "falling back to single eval",
                len(payload),
                len(batch),
            )
            return [
                await self.evaluate_content(c, profile, source_context=source_context)
                for c in batch
            ]

        results: list[float] = []
        for i, content in enumerate(batch):
            if payload_by_id is None:
                raw_item = payload[i] if i < len(payload) else None
            else:
                raw_item = next(
                    (
                        payload_by_id[key]
                        for key in _content_result_keys(content)
                        if key in payload_by_id
                    ),
                    None,
                )
            if raw_item is None:
                results.append(0.0)
                continue
            if not isinstance(raw_item, dict):
                results.append(0.0)
                continue
            item_result: dict[str, Any] = raw_item
            score = self._clamp_score(item_result.get("score", 0.0))
            reason = str(item_result.get("reason", "")).strip()
            topic_group = str(item_result.get("topic_group", "")).strip()
            style_key = str(item_result.get("style_key", "")).strip().lower()
            franchise_key = str(item_result.get("franchise_key", "")).strip()

            content.relevance_score = score
            content.relevance_reason = reason
            if topic_group:
                content.topic_group = topic_group
            if style_key in valid_styles:
                content.style_key = style_key
            if franchise_key:
                content.franchise_key = franchise_key

            cache_key = self._batch_eval_cache_key(
                content,
                profile,
                negative_revision=self._negative_exemplar_revision(),
            )
            self._eval_cache[cache_key] = (
                score,
                reason,
                topic_group,
                style_key,
                franchise_key,
            )
            results.append(score)

        # v0.3.50+: intra-batch franchise cap. The LLM dutifully fills
        # franchise_key for IP/series content (per the prompt's batch-
        # consistency rule), but we used to keep all 30 items even when
        # ≥10 of them shared a franchise — observed in production:
        # 张雪机车×13 / 风犬少年的天空×7 / 咲间妮娜×7 in single batches.
        # Cap at ``_BATCH_FRANCHISE_CAP`` per batch: keep the highest-
        # scoring N items per franchise, zero the rest. Empty franchise
        # is exempt (most generic content has no IP signal).
        cap = _BATCH_FRANCHISE_CAP
        if cap > 0 and batch:
            buckets: dict[str, list[int]] = {}
            for i, content in enumerate(batch):
                if i >= len(results) or results[i] <= 0:
                    continue
                key = (content.franchise_key or "").strip().lower()
                if not key:
                    continue
                buckets.setdefault(key, []).append(i)
            dropped = 0
            for _key, indices in buckets.items():
                if len(indices) <= cap:
                    continue
                # Keep top ``cap`` by score, drop the rest.
                indices.sort(key=lambda idx: results[idx], reverse=True)
                for idx in indices[cap:]:
                    results[idx] = 0.0
                    batch[idx].relevance_score = 0.0
                    dropped += 1
            if dropped:
                logger.info(
                    "eval_batch franchise cap: dropped %d item(s) (cap=%d/franchise; offenders=%s)",
                    dropped,
                    cap,
                    ", ".join(f"{k}×{len(v)}" for k, v in buckets.items() if len(v) > cap),
                )

        # v0.3.51+: same-style cap (mirrors v0.3.50 franchise cap).
        # Production logs (2026-05-05) showed single-style concentration
        # 7-12/30 in many eval batches (fun_variety×10, story_doc×11,
        # light_chat×11, practical_guide×10). Pool inherits this skew
        # because eval_batch keeps all 30 — diversifier at serve time
        # can't unbias a pool that's already 30%+ same-style.
        # Cap=8 (27% of a 30-batch) lets a style have a small foothold
        # but stops single-style domination of the round.
        style_cap = _BATCH_STYLE_CAP
        if style_cap > 0 and batch:
            style_buckets: dict[str, list[int]] = {}
            for i, content in enumerate(batch):
                if i >= len(results) or results[i] <= 0:
                    continue
                style_key = (content.style_key or "").strip().lower()
                if not style_key:
                    continue
                style_buckets.setdefault(style_key, []).append(i)
            style_dropped = 0
            for _style_key, indices in style_buckets.items():
                if len(indices) <= style_cap:
                    continue
                indices.sort(key=lambda idx: results[idx], reverse=True)
                for idx in indices[style_cap:]:
                    results[idx] = 0.0
                    batch[idx].relevance_score = 0.0
                    style_dropped += 1
            if style_dropped:
                logger.info(
                    "eval_batch style cap: dropped %d item(s) (cap=%d/style; offenders=%s)",
                    style_dropped,
                    style_cap,
                    ", ".join(
                        f"{k}×{len(v)}" for k, v in style_buckets.items() if len(v) > style_cap
                    ),
                )

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
        by_identity: dict[str, DiscoveredContent] = {}
        for item in results:
            identity = ContentDiscoveryEngine._content_identity(item)
            existing = by_identity.get(identity)
            if existing is None or item.relevance_score > existing.relevance_score:
                by_identity[identity] = item
        return list(by_identity.values())

    @staticmethod
    def _content_identity(item: DiscoveredContent) -> str:
        platform = (item.source_platform or "bilibili").strip() or "bilibili"
        content_id = (item.content_id or item.bvid or item.content_url).strip()
        if content_id:
            return f"{platform}:{content_id}"
        return f"{platform}:title:{item.title}:{item.author_name or item.up_name}"

    async def _run_strategies(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        fully_parallel: bool = False,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
    ) -> list[DiscoveredContent]:
        results: list[DiscoveredContent] = []
        run_entries = [
            (strategy, self._strategy_run_limit(strategy, limit, strategy_limits))
            for strategy in strategies
        ]
        run_entries = [
            (strategy, run_limit) for strategy, run_limit in run_entries if run_limit > 0
        ]
        if not run_entries:
            return []

        if fully_parallel:
            # One shot: every strategy runs in a single gather. We rely
            # on ``bilibili_request_concurrency`` + ``search_budget_total``
            # to bound IP-level pressure; the default phase split is
            # safer but adds ~search_wall_time before others start.
            names = [s.name for s, _ in run_entries]
            logger.info("discover start (fully_parallel): strategies=%s limit=%d", names, limit)
            t0 = time.monotonic()

            async def _timed(
                strategy: DiscoveryStrategy,
                run_limit: int,
            ) -> list[DiscoveredContent]:
                s_t0 = time.monotonic()
                logger.info("strategy %s: dispatch limit=%d", strategy.name, run_limit)
                try:
                    result = await _call_strategy_discover(
                        strategy,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                    )
                finally:
                    logger.info(
                        "strategy %s: done in %.1fs",
                        strategy.name,
                        time.monotonic() - s_t0,
                    )
                return result

            gathered = await asyncio.gather(
                *(_timed(s, run_limit) for s, run_limit in run_entries),
                return_exceptions=True,
            )
            results.extend(self._collect_strategy_results([s for s, _ in run_entries], gathered))
            logger.info(
                "discover done (fully_parallel): strategies=%s total_elapsed=%.1fs results=%d",
                names,
                time.monotonic() - t0,
                len(results),
            )
        else:
            # Split strategies into two phases to avoid B站 IP-level
            # search rate-limiting. Search runs first (Phase 1) with a
            # dedicated cookie-free client so it gets clean quota.
            # Other strategies (explore, related_chain) also call the
            # search API, so each strategy's calls are capped by the
            # per-strategy search budget in
            # ``DiscoveryConcurrencyController``.
            search_entries = [(s, run_limit) for s, run_limit in run_entries if s.name == "search"]
            other_entries = [(s, run_limit) for s, run_limit in run_entries if s.name != "search"]

            # Phase 1: run search strategy first to get clean IP quota
            if search_entries:
                tasks = [
                    _call_strategy_discover(
                        s,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                    )
                    for s, run_limit in search_entries
                ]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(
                    self._collect_strategy_results([s for s, _ in search_entries], gathered)
                )

            # Brief cooldown between phases to let IP-level rate limit recover
            if search_entries and other_entries:
                await asyncio.sleep(2.0)

            # Phase 2: run remaining strategies concurrently
            if other_entries:
                tasks = [
                    _call_strategy_discover(
                        s,
                        profile,
                        limit=run_limit,
                        pool_snapshot=pool_snapshot,
                    )
                    for s, run_limit in other_entries
                ]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                results.extend(
                    self._collect_strategy_results([s for s, _ in other_entries], gathered)
                )

        logger.info(
            "Discovery gather returned %d results for %d strategies: %s",
            len(results),
            len(run_entries),
            [s.name for s, _ in run_entries],
        )
        return results

    @staticmethod
    def _strategy_run_limit(
        strategy: DiscoveryStrategy,
        default_limit: int,
        strategy_limits: dict[str, int] | None,
    ) -> int:
        if not strategy_limits:
            return max(1, int(default_limit))
        raw_limit = strategy_limits.get(strategy.name, default_limit)
        try:
            run_limit = int(raw_limit)
        except (TypeError, ValueError):
            run_limit = default_limit
        return max(0, min(max(1, int(default_limit)), run_limit))

    @staticmethod
    def _collect_strategy_results(
        strategies: list[DiscoveryStrategy],
        gathered: Sequence[list[DiscoveredContent] | BaseException],
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
            # v0.3.31+: per-strategy raw diversity snapshot. Items at
            # this point are pre-LLM-evaluation (topic_group / style_key
            # not set yet), so we report what's observable: title-level
            # uniqueness, up_name spread, and platform mix. Catches
            # "search returned 13 items but they're all from the same UP
            # / all same title prefix" pathologies.
            ups: Counter[str] = Counter((c.up_name or "").strip().lower() for c in items)
            del ups[""]
            unique_titles = len({c.title.strip() for c in items if c.title})
            platforms: Counter[str] = Counter((c.source_platform or "bilibili") for c in items)
            top_up = ups.most_common(1)[0] if ups else ("", 0)
            logger.info(
                "Strategy '%s' found %d items.%s "
                "diversity={unique_titles=%d/%d, unique_ups=%d, top_up=%s×%d, platforms=%s}",
                strategy.name,
                len(items),
                "" if items else " (empty — all candidates filtered or generation failed)",
                unique_titles,
                len(items) or 1,
                len(ups),
                top_up[0] or "—",
                top_up[1],
                dict(platforms.most_common()),
            )
        return results

    async def _run_backfill(
        self,
        strategies: list[DiscoveryStrategy],
        *,
        profile: SoulProfile,
        limit: int,
        existing: list[DiscoveredContent],
        pool_snapshot: Any | None = None,
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
                    pool_snapshot=pool_snapshot,
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
    def _apply_pool_snapshot_rerank(
        results: list[DiscoveredContent],
        pool_snapshot: Any | None,
    ) -> list[DiscoveredContent]:
        if pool_snapshot is None or len(results) <= 1:
            return list(results)

        saturated_topics = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_topics",
        )
        saturated_styles = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_styles",
        )
        saturated_franchises = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "saturated_franchises",
        )
        undercovered_axes = ContentDiscoveryEngine._normalized_snapshot_values(
            pool_snapshot,
            "undercovered_axes",
        )
        if not (saturated_topics or saturated_styles or saturated_franchises or undercovered_axes):
            return list(results)

        indexed_results = list(enumerate(results))
        indexed_results.sort(
            key=lambda indexed: ContentDiscoveryEngine._pool_rerank_key(
                indexed[1],
                original_index=indexed[0],
                saturated_topics=saturated_topics,
                saturated_styles=saturated_styles,
                saturated_franchises=saturated_franchises,
                undercovered_axes=undercovered_axes,
            )
        )
        return [item for _, item in indexed_results]

    @staticmethod
    def _pool_rerank_key(
        item: DiscoveredContent,
        *,
        original_index: int,
        saturated_topics: set[str],
        saturated_styles: set[str],
        saturated_franchises: set[str],
        undercovered_axes: set[str],
    ) -> tuple[bool, bool, float, float, int]:
        raw_score = item.relevance_score
        adjusted_score = raw_score
        topic = ContentDiscoveryEngine._topic_bucket(item)
        style = ContentDiscoveryEngine._style_bucket(item)
        franchise = ContentDiscoveryEngine._normalize_topic_token(item.franchise_key)

        if topic in saturated_topics:
            adjusted_score -= 0.08
        if style in saturated_styles:
            adjusted_score -= 0.04
        if franchise in saturated_franchises:
            adjusted_score -= 0.10
        if topic in undercovered_axes:
            adjusted_score += 0.04

        return (
            item.candidate_tier != "primary",
            raw_score < 0.92,
            -adjusted_score,
            -raw_score,
            original_index,
        )

    @staticmethod
    def _normalized_snapshot_values(pool_snapshot: Any, attribute: str) -> set[str]:
        values = getattr(pool_snapshot, attribute, ()) or ()
        if not isinstance(values, (list, tuple, set, frozenset)):
            return set()
        return {
            token
            for value in values
            if isinstance(value, str)
            if (token := ContentDiscoveryEngine._normalize_topic_token(value))
        }

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
        reserved_topics = {ContentDiscoveryEngine._topic_bucket(i) for i in reserved} - {""}
        reserved_sources = {
            ContentDiscoveryEngine._normalize_topic_token(i.source_strategy) for i in reserved
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
        reserved_keys = {ContentDiscoveryEngine._content_identity(item) for item in reserved}
        for item in selected:
            if ContentDiscoveryEngine._content_identity(item) not in reserved_keys:
                combined.append(item)
        if len(combined) >= limit:
            return combined[:limit]

        # Step 2: backfill from overflow with relaxed constraints
        combined = ContentDiscoveryEngine._backfill_from_overflow(
            combined,
            overflow,
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
        reserved_keys: set[str] = set()
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
            reserved_keys.add(ContentDiscoveryEngine._content_identity(item))
            source_counts[source] += 1
            if topic:
                global_seen_topics.add(topic)

        unreserved = [
            item
            for item in results
            if ContentDiscoveryEngine._content_identity(item) not in reserved_keys
        ]
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
                bool(source)
                and source not in seen_sources
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

        # v0.3.50+: pool-wide franchise quota. Without this, multiple
        # discovery rounds can each pass the per-batch cap (4 张雪机车
        # in batch 1, 4 in batch 2, ...) and the pool ends up with 30+
        # items of the same franchise — diversifier at serve time
        # cannot rescue a pool that's already franchise-skewed.
        existing_franchise_counts: dict[str, int] = {}
        if _POOL_FRANCHISE_QUOTA > 0:
            try:
                existing_franchise_counts = self._database.count_pool_by_franchise()
            except Exception:
                # Old DB or test stub without the helper — skip the
                # quota check rather than fail caching entirely.
                logger.debug("count_pool_by_franchise unavailable", exc_info=True)
                existing_franchise_counts = {}

        persisted: list[DiscoveredContent] = []
        skipped_franchise: dict[str, int] = {}
        skipped_viewed = 0
        round_franchise_counts: dict[str, int] = {}
        viewed_content_keys = self._recent_viewed_content_keys()
        for item in results:
            if (
                viewed_content_keys
                and not self._candidate_view_keys(item).isdisjoint(viewed_content_keys)
            ):
                skipped_viewed += 1
                continue
            franchise_key = (item.franchise_key or "").strip().lower()
            if franchise_key and _POOL_FRANCHISE_QUOTA > 0:
                pool_existing = existing_franchise_counts.get(franchise_key, 0)
                round_existing = round_franchise_counts.get(franchise_key, 0)
                if pool_existing + round_existing >= _POOL_FRANCHISE_QUOTA:
                    skipped_franchise[franchise_key] = skipped_franchise.get(franchise_key, 0) + 1
                    continue
            try:
                self._database.cache_content(item.bvid or item.content_id, **item.to_cache_kwargs())
                persisted.append(item)
                if franchise_key:
                    round_franchise_counts[franchise_key] = (
                        round_franchise_counts.get(franchise_key, 0) + 1
                    )
            except Exception:
                logger.exception("Failed to cache discovered content: %s", item.bvid)

        if skipped_viewed:
            logger.info(
                "pool cache skipped %d recently viewed item(s) before writing content_cache",
                skipped_viewed,
            )

        if skipped_franchise:
            logger.info(
                "pool franchise quota: skipped %d item(s) (cap=%d/franchise; %s)",
                sum(skipped_franchise.values()),
                _POOL_FRANCHISE_QUOTA,
                ", ".join(f"{k}×{v}" for k, v in skipped_franchise.items()),
            )

        # v0.3.45+: warm the recommendation MMR embedding cache while we
        # still hold these items in memory. Without this hook, the first
        # ``serve()`` after a discovery run pays ~150ms × N for serial
        # API calls — the warm path is L2 SQLite so subsequent reshuffles
        # are <1s. Fired in a detached task so we don't block discovery
        # finalization on a slow embedding provider.
        if persisted and self._embedding_service is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # _cache_results is sometimes called from sync test paths;
                # fall through silently rather than raise.
                return
            loop.create_task(self._warm_mmr_embeddings(persisted))

    async def _warm_mmr_embeddings(
        self,
        items: list[DiscoveredContent],
    ) -> None:
        """Pre-warm the MMR embedding cache for newly-cached items.

        Mirrors ``RecommendationEngine._mmr_embedding_text`` so the cache
        keys line up byte-for-byte. Best-effort — never raises.
        """
        if self._embedding_service is None or not items:
            return
        embedding_service = self._embedding_service

        async def _warm(item: DiscoveredContent) -> None:
            text = (f"{item.title or ''} {(item.description or '')[:160]}").strip()[:200]
            if not text:
                return
            try:
                await embedding_service.embed(text)
            except Exception:
                logger.debug(
                    "discovery._warm_mmr_embeddings: embed failed for %s",
                    item.bvid,
                    exc_info=True,
                )

        await asyncio.gather(*(_warm(item) for item in items))
