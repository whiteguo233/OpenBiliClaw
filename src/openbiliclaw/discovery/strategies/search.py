"""Search-based content discovery strategy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    discovery_raw_candidate_mode_enabled,
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.keyword_digest import profile_kw_digest
from openbiliclaw.discovery.strategies._utils import (
    SupportsSearchClient,
    build_query_generation_profile_summary,
    cached_embedding_lookup,
    clean_text,
    interest_anchors,
    normalize_match_text,
    parse_duration,
    search_cooldown_remaining,
    to_int,
)
from openbiliclaw.llm.prompts import build_search_queries_prompt
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.published_time import normalize_published_time
from openbiliclaw.recommendation.publication_preference import (
    PRESET_ALL,
    PublicationDatePreference,
    resolve_publication_window,
)

if TYPE_CHECKING:
    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

# The 2026-08 historical discovery replay found that ordinary Bilibili search
# supplied very little <=7-day content.  Production compositions therefore
# reserve one bounded ``pubdate`` request and at most five rows per refresh. The
# dataclass default stays disabled so alternate/test compositions remain
# backwards compatible unless they deliberately opt into the lane.
RECENT_SUPPLY_LANE_QUERIES = 1
RECENT_SUPPLY_LANE_PAGE_SIZE = 5


@dataclass(frozen=True)
class _SearchRequest:
    """One bounded Bilibili search request with its supply-lane provenance."""

    query_index: int
    query: str
    page: int
    page_size: int
    order: str = "totalrank"
    discovery_lane: str = ""
    pubtime_begin: int | None = None
    pubtime_end: int | None = None


@dataclass
class SearchStrategy(DiscoveryStrategy):
    """Discover content by generating search queries from user interests."""

    llm_service: SupportsStructuredTask
    bilibili_client: SupportsSearchClient
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    embedding_service: SupportsEmbeddingService | None = None
    queries_per_run: int = 8
    page_size: int = 10
    max_pages: int = 1
    recent_lane_queries_per_run: int = 0
    recent_lane_page_size: int = RECENT_SUPPLY_LANE_PAGE_SIZE
    publication_preference: PublicationDatePreference | None = None
    llm_evaluation: bool = True
    score_threshold: float = 0.60
    last_intermediates: dict[str, object] = field(default_factory=dict)
    query_cache_ttl_seconds: float = 6 * 60 * 60
    _query_cache: dict[str, tuple[float, list[str]]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @property
    def name(self) -> str:
        return "search"

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        pool_snapshot: object | None = None,
        queries: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        """Generate search queries based on user soul and execute them.

        Strategy:
        1. Extract key interests from the soul profile
        2. Generate creative search keyword combinations
        3. Execute searches via Bilibili API
        4. Score results against the soul profile

        Args:
            profile: User soul profile.
            limit: Maximum results.
            pool_snapshot: Optional current pool distribution summary.
            queries: Optional caller-supplied search queries. When provided
                (non-None), they are used verbatim and the internal LLM
                query-generation call is skipped (the unified keyword planner
                injection point). When ``None``, behavior is unchanged.
            keyword_ids: Optional ``query → discovery_keywords.id`` map (P1.8
                yield provenance). When provided, each produced candidate is
                stamped with the id of the query that produced it so admission
                can credit the originating keyword. ``None`` → no stamping.

        Returns:
            Discovered content list.
        """
        cooldown_remaining = search_cooldown_remaining(self.bilibili_client)
        if cooldown_remaining > 0:
            self.last_intermediates = {
                "queries": [],
                "skipped": "search_cooldown",
                "cooldown_remaining_seconds": int(cooldown_remaining),
            }
            logger.info(
                "Search: Bilibili search cooldown active (%.0fs left); skipping query generation",
                cooldown_remaining,
            )
            return []

        if queries is None:
            resolved_queries = await self._generate_queries(profile, pool_snapshot=pool_snapshot)
        else:
            resolved_queries = self._dedupe_queries(queries)
        queries = resolved_queries
        self.last_intermediates = {"queries": list(queries)}
        anchor_list = interest_anchors(profile)
        candidates: list[DiscoveredContent] = []
        candidates_by_query: dict[int, list[DiscoveredContent]] = {}
        recent_candidates_by_query: dict[int, list[DiscoveredContent]] = {}
        seen_bvids: set[str] = set()
        effective_queries, request_plan = self._build_request_plan(queries)
        recent_request_count = sum(bool(request.discovery_lane) for request in request_plan)
        if recent_request_count:
            self.last_intermediates["recent_lane"] = {
                "order": "pubdate",
                "request_count": recent_request_count,
                "page_size": max(1, int(self.recent_lane_page_size)),
            }
        # Use a dedicated API client for search to avoid session-level
        # rate-limiting from B站.  The shared client accumulates request
        # history from other strategies (trending, related_chain, explore)
        # which triggers v_voucher challenges on the search endpoint.
        search_client = self._create_search_client()
        try:
            gathered = await self._execute_search_queries(
                search_client,
                request_plan,
            )
        finally:
            if search_client is not self.bilibili_client:
                close = getattr(search_client, "close", None)
                if callable(close):
                    await close()

        api_result_count = 0
        recent_api_result_count = 0
        recent_unique_count = 0
        for request, outcome in zip(request_plan, gathered, strict=True):
            query_index = request.query_index
            query = request.query
            page = request.page
            if isinstance(outcome, BaseException):
                logger.error(
                    "Search query failed: %s",
                    query,
                    exc_info=outcome,
                    extra={
                        "strategy": "search",
                        "query": query,
                        "page": page,
                        "error_type": type(outcome).__name__,
                    },
                )
                continue
            if not isinstance(outcome, list):
                logger.warning(
                    "Search query '%s' returned non-list: %s",
                    query,
                    type(outcome).__name__,
                )
                continue
            api_result_count += len(outcome)
            if request.discovery_lane:
                recent_api_result_count += len(outcome)
            search_results = outcome
            for item_index, item in enumerate(search_results):
                content = self._map_search_result(
                    item,
                    query=query,
                    query_index=query_index,
                    item_index=item_index + (page - 1) * request.page_size,
                    interest_anchors=anchor_list,
                    discovery_lane=request.discovery_lane,
                )
                if content is None or content.bvid in seen_bvids:
                    continue
                # P1.8 yield provenance: stamp the producing query's keyword id
                # (unified planner injection). No-op when unmapped / not injected.
                if keyword_ids:
                    content.source_keyword_id = keyword_ids.get(query)
                seen_bvids.add(content.bvid)
                candidates.append(content)
                if request.discovery_lane:
                    recent_candidates_by_query.setdefault(query_index, []).append(content)
                    recent_unique_count += 1
                else:
                    candidates_by_query.setdefault(query_index, []).append(content)

        logger.info(
            "Search: %d queries, %d API results, %d unique candidates",
            len(queries),
            api_result_count,
            len(candidates),
        )
        if recent_request_count:
            recent_stats = self.last_intermediates.get("recent_lane")
            if isinstance(recent_stats, dict):
                recent_stats.update(
                    {
                        "api_results": recent_api_result_count,
                        "unique_candidates": recent_unique_count,
                    }
                )

        ordered_candidates = self._interleave_search_lanes(
            candidates_by_query,
            recent_candidates_by_query,
        )

        if not self.llm_evaluation or discovery_raw_candidate_mode_enabled():
            # Preserve the legacy request-order result when the lane is disabled;
            # when enabled, round-robin is what guarantees a bounded recent row
            # can reach a small raw-candidate window instead of sitting at tail.
            raw_candidates = ordered_candidates if recent_request_count else candidates
            return raw_candidates[:limit]

        evaluator = self.content_evaluator()
        eval_candidates = self.filter_candidates_for_eval(ordered_candidates)
        eval_candidates = trim_candidates_for_llm(
            eval_candidates,
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

        if not results and candidates:
            score_vals = sorted(scores, reverse=True)
            logger.warning(
                "Search: %d candidates all below threshold %.2f. Top-5 scores: %s",
                len(candidates),
                self.score_threshold,
                score_vals[:5],
            )
        return results

    def _build_request_plan(
        self,
        queries: list[str],
    ) -> tuple[list[str], list[_SearchRequest]]:
        """Build a base + recent request plan within the existing API budget."""

        max_pages = max(1, int(self.max_pages))
        desired_recent = min(max(0, int(self.recent_lane_queries_per_run)), len(queries))
        effective_queries = list(queries)
        budget: int | None = None
        if self.concurrency is not None:
            budget = max(1, int(self.concurrency.search_budget_per_strategy))
            # Reserve recent capacity only when at least one complete normal
            # query still fits.  The lane therefore cannot silently exceed the
            # strategy's existing request budget.
            recent_reservation = min(desired_recent, max(0, budget - max_pages))
            primary_budget = max(1, budget - recent_reservation)
            max_queries = primary_budget // max_pages
            if len(effective_queries) > max_queries:
                logger.debug(
                    "Search: trimming queries from %d to %d (search budget + recent lane)",
                    len(effective_queries),
                    max_queries,
                )
                effective_queries = effective_queries[:max_queries]

        pubtime_begin, pubtime_end = self._strict_pubtime_bounds()
        request_plan = [
            _SearchRequest(
                query_index=query_index,
                query=query,
                page=page,
                page_size=max(1, int(self.page_size)),
                pubtime_begin=pubtime_begin,
                pubtime_end=pubtime_end,
            )
            for query_index, query in enumerate(effective_queries)
            for page in range(1, max_pages + 1)
        ]
        remaining_budget = desired_recent if budget is None else max(0, budget - len(request_plan))
        recent_count = min(desired_recent, len(effective_queries), remaining_budget)
        request_plan.extend(
            _SearchRequest(
                query_index=query_index,
                query=query,
                page=1,
                page_size=max(1, int(self.recent_lane_page_size)),
                order="pubdate",
                discovery_lane="recent",
                pubtime_begin=pubtime_begin,
                pubtime_end=pubtime_end,
            )
            for query_index, query in enumerate(effective_queries[:recent_count])
        )
        return effective_queries, request_plan

    def _strict_pubtime_bounds(self) -> tuple[int | None, int | None]:
        """Return Bilibili search timestamps only for strict date filtering."""
        preference = self.publication_preference
        if preference is None or preference.preset == PRESET_ALL or preference.weight < 1.0:
            return None, None
        window = resolve_publication_window(preference)
        if window is None:
            return None, None
        start = int(window.start_utc.timestamp()) if window.start_utc is not None else None
        end = int(window.end_utc.timestamp()) if window.end_utc is not None else None
        return start, end

    @staticmethod
    def _interleave_query_candidates(
        candidates_by_query: dict[int, list[DiscoveredContent]],
    ) -> list[DiscoveredContent]:
        """Round-robin candidates so small LLM windows still cover each query."""
        ordered_query_indices = sorted(candidates_by_query)
        max_depth = max(
            (len(candidates_by_query[index]) for index in ordered_query_indices),
            default=0,
        )
        interleaved: list[DiscoveredContent] = []
        for depth in range(max_depth):
            for index in ordered_query_indices:
                bucket = candidates_by_query[index]
                if depth < len(bucket):
                    interleaved.append(bucket[depth])
        return interleaved

    @classmethod
    def _interleave_search_lanes(
        cls,
        candidates_by_query: dict[int, list[DiscoveredContent]],
        recent_candidates_by_query: dict[int, list[DiscoveredContent]],
    ) -> list[DiscoveredContent]:
        """Round-robin normal queries plus the bounded recent supply lane."""

        if not recent_candidates_by_query:
            return cls._interleave_query_candidates(candidates_by_query)
        ordered_buckets: list[list[DiscoveredContent]] = []
        query_indices = sorted(set(candidates_by_query) | set(recent_candidates_by_query))
        for query_index in query_indices:
            normal = candidates_by_query.get(query_index)
            if normal:
                ordered_buckets.append(normal)
            recent = recent_candidates_by_query.get(query_index)
            if recent:
                ordered_buckets.append(recent)
        max_depth = max((len(bucket) for bucket in ordered_buckets), default=0)
        return [
            bucket[depth]
            for depth in range(max_depth)
            for bucket in ordered_buckets
            if depth < len(bucket)
        ]

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        return replace(
            self,
            queries_per_run=min(max(self.queries_per_run + 4, self.queries_per_run), 12),
            page_size=min(max(self.page_size, 12), 20),
            max_pages=max(self.max_pages, 2),
            score_threshold=max(0.60, round(self.score_threshold - 0.07, 2)),
            last_intermediates={},
        )

    def _create_search_client(self) -> SupportsSearchClient:
        """Create a fresh API client for search while preserving auth.

        B站 rate-limits search per session. Other strategies
        (especially explore) exhaust the shared client's search quota,
        so we use a dedicated client. Search currently returns
        ``v_voucher`` for anonymous WBI requests, so the dedicated client
        must carry over the runtime cookie when one exists. Falls back to
        the shared client if creation fails or if the bilibili_client is
        not the real API client (e.g. in tests).
        """
        from openbiliclaw.bilibili.api import BilibiliAPIClient

        if not isinstance(self.bilibili_client, BilibiliAPIClient):
            return self.bilibili_client
        try:
            return BilibiliAPIClient(
                cookie=str(getattr(self.bilibili_client, "_cookie", "")),
                min_request_interval=0.8,
                proxy=getattr(self.bilibili_client, "_proxy", None),
            )
        except Exception:
            logger.debug("Could not create dedicated search client, using shared")
        return self.bilibili_client

    async def _execute_search_queries(
        self,
        client: SupportsSearchClient,
        request_plan: list[_SearchRequest],
    ) -> list[object]:
        """Execute search queries sequentially with delay + storm backoff.

        v0.3.61+: per-query delay now jitter-randomised in 0.5–1.0s to
        avoid synchronised waves of WBI requests landing in the same
        Bilibili rate-limit bucket. ``client.search`` already retries
        v_voucher challenges 3× internally, so an empty list at this
        layer means the keyword exhausted retries and the IP is being
        challenged. Three consecutive empty results = "storm mode" —
        we abort the rest of the plan rather than burn LLM-generated
        queries against an IP that's currently being denied. The
        remaining queries get filled with empty results so the strategy
        can still gracefully return what it has, and the next refresh
        tick (60s later) gets a fresh shot.
        """
        import random

        storm_trigger = 3
        gathered: list[object] = []
        consecutive_empty = 0
        storm_aborted = False
        for i, request in enumerate(request_plan):
            query = request.query
            page = request.page
            if storm_aborted:
                gathered.append([])
                continue
            cooldown_remaining = search_cooldown_remaining(client)
            if cooldown_remaining > 0:
                logger.info(
                    "Search: Bilibili search cooldown active (%.0fs left); "
                    "skipping remaining %d query(ies)",
                    cooldown_remaining,
                    len(request_plan) - i,
                )
                gathered.extend([] for _ in range(len(request_plan) - i))
                break
            if i > 0:
                # Jitter 0.5–1.0s. Steady-state cost: ~0.75s/query;
                # under storm: backoff already happens inside client.search,
                # so this is purely a desync between queries.
                await asyncio.sleep(0.5 + random.uniform(0.0, 0.5))
            try:
                if request.pubtime_begin is not None or request.pubtime_end is not None:
                    result = await self._search_with_publication_bounds(client, request)
                elif request.discovery_lane:
                    result = await client.search(
                        query,
                        page=page,
                        page_size=request.page_size,
                        order=request.order,
                    )
                else:
                    # Keep the legacy call shape for alternate clients that
                    # implement the old protocol without an ``order`` kwarg.
                    result = await client.search(
                        query,
                        page=page,
                        page_size=request.page_size,
                    )
            except Exception as exc:
                gathered.append(exc)
                # An exception path doesn't count as v_voucher storm
                # evidence (could be 412, network blip, etc.); reset.
                consecutive_empty = 0
                continue
            gathered.append(result)
            # Storm detection: empty result after retries already
            # consumed = IP is being rate-limited *now*. Burning the
            # remaining queries just deepens the hole.
            if isinstance(result, list) and not result:
                cooldown_remaining = search_cooldown_remaining(client)
                if cooldown_remaining > 0:
                    logger.warning(
                        "v_voucher search cooldown detected (%.0fs left) — "
                        "aborting remaining %d query(ies) this round",
                        cooldown_remaining,
                        len(request_plan) - (i + 1),
                    )
                    storm_aborted = True
                    continue
                consecutive_empty += 1
                if consecutive_empty >= storm_trigger:
                    logger.warning(
                        "v_voucher storm detected (%d consecutive empty queries)"
                        " — aborting remaining %d query(ies) this round; "
                        "next refresh tick (60s) gets a fresh attempt",
                        consecutive_empty,
                        len(request_plan) - (i + 1),
                    )
                    storm_aborted = True
            else:
                consecutive_empty = 0
        return gathered

    @staticmethod
    async def _search_with_publication_bounds(
        client: SupportsSearchClient,
        request: _SearchRequest,
    ) -> list[dict[str, object]]:
        """Search with only the publication boundaries that actually exist."""
        if request.discovery_lane:
            if request.pubtime_begin is not None and request.pubtime_end is not None:
                return await client.search(
                    request.query,
                    page=request.page,
                    page_size=request.page_size,
                    order=request.order,
                    pubtime_begin=request.pubtime_begin,
                    pubtime_end=request.pubtime_end,
                )
            if request.pubtime_begin is not None:
                return await client.search(
                    request.query,
                    page=request.page,
                    page_size=request.page_size,
                    order=request.order,
                    pubtime_begin=request.pubtime_begin,
                )
            return await client.search(
                request.query,
                page=request.page,
                page_size=request.page_size,
                order=request.order,
                pubtime_end=request.pubtime_end,
            )

        if request.pubtime_begin is not None and request.pubtime_end is not None:
            return await client.search(
                request.query,
                page=request.page,
                page_size=request.page_size,
                pubtime_begin=request.pubtime_begin,
                pubtime_end=request.pubtime_end,
            )
        if request.pubtime_begin is not None:
            return await client.search(
                request.query,
                page=request.page,
                page_size=request.page_size,
                pubtime_begin=request.pubtime_begin,
            )
        return await client.search(
            request.query,
            page=request.page,
            page_size=request.page_size,
            pubtime_end=request.pubtime_end,
        )

    async def _generate_queries(
        self,
        profile: SoulProfile,
        *,
        pool_snapshot: object | None = None,
    ) -> list[str]:
        pool_hints: dict[str, object] | None = None
        to_prompt_hints = getattr(pool_snapshot, "to_prompt_hints", None)
        if callable(to_prompt_hints):
            try:
                raw_hints = to_prompt_hints()
            except Exception:
                logger.warning(
                    "Search query generation: ignoring invalid pool snapshot hints",
                    exc_info=True,
                )
            else:
                if isinstance(raw_hints, dict):
                    pool_hints = raw_hints
                else:
                    logger.warning(
                        "Search query generation: ignoring non-dict pool snapshot hints: %s",
                        type(raw_hints).__name__,
                    )

        cache_key = self._query_cache_key(profile, pool_hints)
        if cache_key is not None:
            cached = self._cached_queries(cache_key)
            if cached is not None:
                return cached

        try:
            try:
                prompt_messages = build_search_queries_prompt(
                    profile_summary=self._query_profile_summary(profile),
                    pool_hints=pool_hints,
                )
            except (TypeError, ValueError) as exc:
                if pool_hints is None:
                    raise
                logger.warning(
                    "Search query generation: dropping unserializable pool hints: %s",
                    exc,
                )
                prompt_messages = build_search_queries_prompt(
                    profile_summary=self._query_profile_summary(profile),
                    pool_hints=None,
                )
            complete_structured = self.llm_service.complete_structured_task
            response = await complete_structured(
                system_instruction=prompt_messages[0]["content"],
                user_input=prompt_messages[1]["content"],
                max_tokens=1024,
                caller="discovery.search.queries",
                reasoning_effort="",
                **without_core_memory_kwargs(complete_structured),
            )
            queries = self._parse_queries(str(getattr(response, "content", "")))
            if queries:
                if cache_key is not None:
                    self._store_queries(cache_key, queries)
                return queries
        except Exception:
            logger.exception("Search query generation failed; falling back to local queries.")
        return self._fallback_queries(profile)

    def _query_cache_key(
        self,
        profile: SoulProfile,
        pool_hints: dict[str, object] | None,
    ) -> str | None:
        try:
            hints_blob = json.dumps(
                pool_hints or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except TypeError:
            return None
        hints_digest = hashlib.sha256(hints_blob.encode("utf-8")).hexdigest()[:12]
        return f"{profile_kw_digest(profile)}:{hints_digest}"

    def _cached_queries(self, cache_key: str) -> list[str] | None:
        cached = self._query_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, queries = cached
        if time.monotonic() >= expires_at:
            self._query_cache.pop(cache_key, None)
            return None
        return list(queries)

    def _store_queries(self, cache_key: str, queries: list[str]) -> None:
        ttl = max(0.0, float(self.query_cache_ttl_seconds))
        if ttl <= 0:
            return
        self._query_cache[cache_key] = (time.monotonic() + ttl, list(queries))

    @staticmethod
    def _dedupe_queries(queries: list[str]) -> list[str]:
        """Strip + dedupe caller-injected queries (no per-run cap)."""
        deduped: list[str] = []
        seen: set[str] = set()
        for item in queries:
            query = str(item).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            deduped.append(query)
        return deduped

    def _parse_queries(self, content: str) -> list[str]:
        text = content.strip()
        if not text:
            return []
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return []
        raw_queries = parsed.get("queries", [])
        if not isinstance(raw_queries, list):
            return []
        queries: list[str] = []
        seen: set[str] = set()
        for item in raw_queries:
            query = str(item).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            queries.append(query)
            if len(queries) >= min(self.queries_per_run, 10):
                break
        return queries

    def _fallback_queries(self, profile: SoulProfile) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()

        for interest_item in profile.preferences.interests:
            query = str(interest_item.name).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            queries.append(query)
            if len(queries) >= min(self.queries_per_run, 5):
                return queries

        for trait in profile.core_traits:
            query = str(trait).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            queries.append(query)
            if len(queries) >= min(self.queries_per_run, 5):
                break

        return queries

    # ------------------------------------------------------------------
    # Delegating static helpers — keep backwards-compatible class API
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_summary(profile: SoulProfile) -> dict[str, object]:
        return build_query_generation_profile_summary(profile)

    def _query_profile_summary(self, profile: SoulProfile) -> dict[str, object]:
        return build_query_generation_profile_summary(
            profile,
            embedding_lookup=cached_embedding_lookup(self.embedding_service),
        )

    @staticmethod
    def _interest_anchors(profile: SoulProfile) -> list[tuple[str, float]]:
        return interest_anchors(profile)

    @staticmethod
    def _interest_aliases(name: str) -> set[str]:
        from openbiliclaw.discovery.strategies._utils import interest_aliases

        return interest_aliases(name)

    @staticmethod
    def _clean_text(value: str) -> str:
        return clean_text(value)

    @staticmethod
    def _to_int(raw_value: object) -> int:
        return to_int(raw_value)

    @staticmethod
    def _parse_duration(raw_value: object) -> int:
        return parse_duration(raw_value)

    @staticmethod
    def _normalize_match_text(value: str) -> str:
        return normalize_match_text(value)

    # ------------------------------------------------------------------
    # Instance helpers
    # ------------------------------------------------------------------

    def _map_search_result(
        self,
        item: dict[str, object],
        *,
        query: str,
        query_index: int,
        item_index: int,
        interest_anchors: list[tuple[str, float]],
        discovery_lane: str = "",
    ) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        title = clean_text(str(item.get("title", "")))
        description = clean_text(str(item.get("description", "")))
        # Pre-filter score: interest anchor bonus only (LLM eval overwrites later)
        anchor_bonus = self._interest_anchor_bonus(
            query=query,
            title=title,
            description=description,
            interest_anchors=interest_anchors,
        )
        pre_score = round(0.1 + anchor_bonus, 4)
        published = normalize_published_time(item.get("pubdate") or item.get("publish_time"))
        return DiscoveredContent(
            bvid=bvid,
            title=title,
            up_name=clean_text(str(item.get("author", ""))),
            up_mid=to_int(item.get("mid", 0)),
            cover_url=str(item.get("pic", "")),
            duration=parse_duration(item.get("duration", 0)),
            view_count=to_int(item.get("play", 0)),
            like_count=to_int(item.get("like", 0)),
            favorite_count=to_int(item.get("favorites", item.get("favorite", 0))),
            danmaku_count=to_int(item.get("video_review", item.get("danmaku", 0))),
            comment_count=to_int(item.get("review", item.get("reply", 0))),
            share_count=to_int(item.get("share", 0)),
            topic_key=self._topic_key_from_query(query),
            topic_group=self._topic_group_from_query(query),
            description=description,
            style_key=ContentDiscoveryEngine.infer_style_key(
                title=title,
                description=description,
                source_strategy=self.name,
            ),
            source_strategy=self.name,
            discovery_lane=discovery_lane,
            relevance_score=min(1.0, pre_score),
            published_at=published.published_at,
            published_label=published.published_label,
        )

    @staticmethod
    def _interest_anchor_bonus(
        *,
        query: str,
        title: str,
        description: str,
        interest_anchors: list[tuple[str, float]],
    ) -> float:
        query_text = normalize_match_text(query)
        title_text = normalize_match_text(title)
        description_text = normalize_match_text(description)
        best_bonus = 0.0
        for anchor, weight in interest_anchors:
            if not anchor:
                continue
            bonus = 0.0
            if anchor in query_text:
                bonus += 0.18 + max(0.0, weight - 0.6) * 0.35
            if anchor in title_text:
                bonus += 0.08
            if anchor in description_text:
                bonus += 0.05
            best_bonus = max(best_bonus, bonus)
        return min(0.42, round(best_bonus, 4))

    @staticmethod
    def _topic_key_from_query(query: str) -> str:
        return re.sub(r"\s+", "", query).strip().lower()

    @staticmethod
    def _topic_group_from_query(query: str) -> str:
        """Extract the core topic word from a search query.

        "强化学习 游戏ai 决策模型" → "强化学习"
        "纪录片 原理" → "纪录片"
        """
        parts = query.strip().split()
        if parts:
            return re.sub(r"\s+", "", parts[0]).lower()[:8]
        return ""
