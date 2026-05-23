"""Cross-domain exploration discovery strategy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol, cast

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.strategies._utils import (
    SupportsSearchClient,
    build_profile_summary,
    interest_aliases,
    interest_anchors,
    search_cooldown_remaining,
)
from openbiliclaw.discovery.strategies.search import SearchStrategy
from openbiliclaw.llm.prompts import build_explore_domains_prompt

if TYPE_CHECKING:
    from openbiliclaw.llm.embedding import SupportsEmbeddingService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database


# Minimal contract — explore only needs the topic-group-coverage query
# and shouldn't depend on the full Database surface (keeps unit tests
# light, makes injection simple).
class _SupportsTopicCoverage(Protocol):
    """Minimal protocol the strategy needs from a Database-like object."""

    def get_active_pool_topic_groups(self, *, limit: int = 30, min_count: int = 2) -> list[str]: ...


logger = logging.getLogger(__name__)


@dataclass
class ExploreStrategy(DiscoveryStrategy):
    """Cross-domain surprise discovery -- find the unexpected."""

    llm_service: SupportsStructuredTask
    bilibili_client: SupportsSearchClient
    concurrency: DiscoveryConcurrencyController | None = None
    embedding_service: SupportsEmbeddingService | None = None
    # v0.3.31+: optional database handle so the strategy can query
    # which topic_groups are already saturated in the active pool.
    # The LLM domain generator avoids re-proposing those, which is
    # the main fix for the "explore returned 30 items / 8 distinct
    # topic_groups" pathology — most of the collapse came from the
    # generator suggesting domains that mapped to already-covered
    # topic_groups by the time the eval LLM labeled them.
    database: _SupportsTopicCoverage | None = None
    # Explore deliberately keeps a 0.65 threshold while other strategies
    # use 0.70 (v0.3.31). Reason: explore returns content the user
    # *hasn't* seen — the eval LLM tends to score these conservatively
    # (0.65-0.69) because they're outside familiar topics, and a 0.70
    # bar wipes out 70%+ of the explore pipeline. The whole point of
    # explore is bringing novelty that doesn't perfectly match
    # current interests, so a slightly more permissive threshold
    # preserves the variety it's designed to provide. The blind-spot
    # guidance (covered_topic_groups, v0.3.31) and high
    # ``score_threshold`` already used by other strategies still keep
    # the pool's overall quality high.
    score_threshold: float = 0.65
    queries_per_domain: int = 3
    max_domains: int = 5
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "explore"

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        if self.score_threshold <= 0.58:
            return None
        return replace(
            self,
            score_threshold=max(0.58, round(self.score_threshold - 0.07, 2)),
            queries_per_domain=max(self.queries_per_domain, 3),
            max_domains=max(self.max_domains, 6),
            last_intermediates={},
        )

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Deliberately explore domains the user hasn't tried.

        Uses the soul profile's deep needs and latent interests
        to hypothesize about what new domains might resonate.

        Args:
            profile: User soul profile.
            limit: Maximum results.

        Returns:
            Discovered content list.
        """
        cooldown_remaining = search_cooldown_remaining(self.bilibili_client)
        if cooldown_remaining > 0:
            self.last_intermediates = {
                "domains": [],
                "skipped": "search_cooldown",
                "cooldown_remaining_seconds": int(cooldown_remaining),
            }
            logger.info(
                "Explore: Bilibili search cooldown active (%.0fs left); "
                "skipping domain generation",
                cooldown_remaining,
            )
            return []

        domains = await self._generate_domains(profile)
        self.last_intermediates = {"domains": list(domains)}
        if not domains:
            return []

        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=cast("Database | None", self.database),
            concurrency=self.concurrency,
        )
        search_strategy = SearchStrategy(
            llm_service=self.llm_service,
            bilibili_client=self.bilibili_client,
            concurrency=self.concurrency,
        )
        anchor_list = interest_anchors(profile)
        request_plan: list[tuple[str, float, bool, str]] = []
        for domain in domains:
            novelty_level = self._clamp_novelty(domain.get("novelty_level", 0.5))
            interest_anchored = bool(domain.get("interest_anchored", False))
            domain_name = str(domain.get("domain", "")).strip()
            for query in self._clean_queries(domain.get("queries", [])):
                request_plan.append((query, novelty_level, interest_anchored, domain_name))

        # Respect per-strategy search budget to avoid exhausting IP-level quota.
        if self.concurrency is not None:
            budget = self.concurrency.search_budget_per_strategy
            if len(request_plan) > budget:
                logger.debug(
                    "Explore: trimming request_plan from %d to %d (search budget)",
                    len(request_plan),
                    budget,
                )
                request_plan = request_plan[:budget]

        # Use a dedicated cookie-free client and execute sequentially with
        # delay to avoid triggering IP-level v_voucher rate-limiting.
        search_client = self._create_search_client()
        try:
            search_outcomes = await self._execute_search_sequential(
                search_client,
                request_plan,
            )
        finally:
            if search_client is not self.bilibili_client:
                close = getattr(search_client, "close", None)
                if callable(close):
                    await close()

        # Bucket candidates by domain_label so the downstream eval hard-cap
        # (30) doesn't starve later domains: without bucketing, the first
        # 1-2 domains' query results consume the entire eval window.
        domain_order: list[str] = []
        per_domain: dict[str, list[tuple[DiscoveredContent, float, bool]]] = {}
        seen_bvids: set[str] = set()
        for (query, novelty_level, interest_anchored, domain_label), outcome in zip(
            request_plan, search_outcomes, strict=True
        ):
            if isinstance(outcome, BaseException):
                logger.error(
                    "Explore query failed: %s",
                    query,
                    exc_info=outcome,
                    extra={
                        "strategy": "explore",
                        "query": query,
                        "novelty_level": novelty_level,
                        "error_type": type(outcome).__name__,
                    },
                )
                continue
            if not isinstance(outcome, list):
                continue
            bucket_key = domain_label or query
            if bucket_key not in per_domain:
                per_domain[bucket_key] = []
                domain_order.append(bucket_key)
            for item_index, item in enumerate(outcome):
                content = search_strategy._map_search_result(
                    item,
                    query=query,
                    query_index=0,
                    item_index=item_index,
                    interest_anchors=anchor_list,
                )
                if content is None or content.bvid in seen_bvids:
                    continue
                seen_bvids.add(content.bvid)
                content.source_strategy = self.name
                if domain_label:
                    normalized_domain = re.sub(r"\s+", "", domain_label).lower()[:16]
                    content.topic_group = normalized_domain
                    # Use domain-level granularity for topic_key so content from
                    # the same exploration domain groups together properly
                    content.topic_key = normalized_domain
                per_domain[bucket_key].append((content, novelty_level, interest_anchored))

        # Round-robin interleave across domains so each domain gets fair
        # representation in the 30-item eval window.
        candidates: list[tuple[DiscoveredContent, float, bool]] = []
        max_depth = max((len(per_domain[k]) for k in domain_order), default=0)
        for depth in range(max_depth):
            for key in domain_order:
                bucket = per_domain[key]
                if depth < len(bucket):
                    candidates.append(bucket[depth])
        candidates = trim_candidates_for_llm(
            candidates,
            limit=limit,
            source_context=self.name,
        )

        scores = await evaluator.evaluate_content_batch(
            [content for content, _, _ in candidates],
            profile,
        )
        results: list[DiscoveredContent] = []
        for (
            content,
            novelty_level,
            _interest_anchored,
        ), score in zip(candidates, scores, strict=True):
            bonus = self._exploration_bonus(
                novelty_level=novelty_level,
                openness=profile.preferences.exploration_openness,
            )
            # Explore uses a gentler blending formula than before:
            # - Raw LLM score weighted at 0.60 (was 0.75) to leave room for bonus
            # - Bonus weighted at 0.40 (was 0.25) so novelty/openness matter more
            # - No distance_penalty: non-anchored is the point of explore
            content.relevance_score = max(
                0.0,
                min(1.0, round(score * 0.60 + bonus * 0.40, 4)),
            )
            # Lower threshold for explore: cross-domain content is intentionally
            # less "relevant" in the narrow sense, so we accept more of it
            explore_threshold = (
                self.score_threshold - 0.25 if self.score_threshold > 0.40 else self.score_threshold
            )
            if content.relevance_score < explore_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                return self._sort_results(results)

        return self._sort_results(results)

    def _create_search_client(self) -> SupportsSearchClient:
        """Create a cookie-free API client for explore searches.

        Avoids sharing the authenticated client's session/cookie with other
        strategies, which would cause IP-level v_voucher rate-limiting.
        Falls back to the shared client for non-API clients (e.g. in tests).
        """
        from openbiliclaw.bilibili.api import BilibiliAPIClient

        if not isinstance(self.bilibili_client, BilibiliAPIClient):
            return self.bilibili_client
        try:
            return BilibiliAPIClient(cookie="", min_request_interval=0.8)
        except Exception:
            logger.debug("Could not create dedicated explore search client, using shared")
        return self.bilibili_client

    async def _execute_search_sequential(
        self,
        client: SupportsSearchClient,
        request_plan: list[tuple[str, float, bool, str]],
    ) -> list[object]:
        """Execute search queries sequentially with delay to avoid rate-limiting."""
        results: list[object] = []
        for i, (query, _, _, _) in enumerate(request_plan):
            cooldown_remaining = search_cooldown_remaining(client)
            if cooldown_remaining > 0:
                logger.info(
                    "Explore: Bilibili search cooldown active (%.0fs left); "
                    "skipping remaining %d query(ies)",
                    cooldown_remaining,
                    len(request_plan) - i,
                )
                results.extend([] for _ in range(len(request_plan) - i))
                break
            if i > 0:
                await asyncio.sleep(0.6)
            try:
                result = await client.search(query, page=1, page_size=10)
                results.append(result)
            except Exception as exc:
                results.append(exc)
        return results

    async def _generate_domains(self, profile: SoulProfile) -> list[dict[str, object]]:
        # v0.3.31+: feed already-saturated topic_groups to the LLM as
        # "blind-spot guide" so it doesn't re-propose well-covered
        # areas. Soft-fails to None on any DB error; the prompt's
        # default branch (no covered_topic_groups) is the back-compat
        # path.
        covered_topic_groups: list[str] | None = None
        if self.database is not None:
            try:
                # Match the prompt-side cap (12) — pulling more from DB
                # would just be discarded by the prompt builder. min_count=2
                # avoids stuffing one-off long-tail topics into the avoid list.
                covered_topic_groups = self.database.get_active_pool_topic_groups(
                    limit=12,
                    min_count=2,
                )
            except Exception:
                logger.debug(
                    "explore: failed to load covered_topic_groups, falling back",
                    exc_info=True,
                )
        if covered_topic_groups:
            logger.info(
                "explore: feeding %d covered topic_groups to domain generator (top 5: %s)",
                len(covered_topic_groups),
                ", ".join(covered_topic_groups[:5]),
            )

        messages = build_explore_domains_prompt(
            profile_summary=build_profile_summary(profile)
            | {"exploration_openness": profile.preferences.exploration_openness},
            covered_topic_groups=covered_topic_groups,
        )
        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                # v0.3.31+: bumped 4096 → 8192. With covered_topic_groups
                # added to user msg + 5 domains × 3 queries +
                # why_it_might_resonate text per domain, the JSON output
                # was hitting the 4K cap mid-string (truncation observed
                # at line 20 col 32 / char 736 in production logs),
                # which made json.loads error out and the whole strategy
                # return 0 items. 8K leaves comfortable headroom.
                max_tokens=8192,
                caller="discovery.explore.queries",
            )
            parsed = json.loads(str(getattr(response, "content", "")).strip())
        except Exception:
            logger.exception("Explore domain generation failed.")
            return []

        if not isinstance(parsed, dict) or not isinstance(parsed.get("domains"), list):
            return []

        current_interests = {
            self._normalize_domain_key(interest_item.name)
            for interest_item in profile.preferences.interests[:10]
            if interest_item.name.strip()
        }
        anchor_set = self._interest_anchor_set(profile)
        domains: list[dict[str, object]] = []
        seen_domains: set[str] = set()
        for item in parsed["domains"]:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", "")).strip()
            normalized = self._normalize_domain_key(domain)
            if not domain or normalized in seen_domains:
                continue
            if await self._looks_too_similar_async(normalized, current_interests):
                continue
            seen_domains.add(normalized)
            domains.append(
                {
                    "domain": domain,
                    "why_it_might_resonate": str(item.get("why_it_might_resonate", "")).strip(),
                    "novelty_level": self._clamp_novelty(item.get("novelty_level", 0.5)),
                    "queries": self._clean_queries(item.get("queries", [])),
                }
            )
            if len(domains) >= self.max_domains:
                break
        prioritized = self._prioritize_domains(domains, anchor_set)
        return [domain for domain in prioritized if domain["queries"]]

    async def _looks_too_similar_async(self, domain: str, current_interests: set[str]) -> bool:
        """Check if domain is too similar to existing interests.

        Uses embedding cosine similarity when available, falls back to substring check.
        Threshold for "too similar" is 0.75 (stricter than dedup's 0.82 —
        we want explore to be genuinely novel).
        """
        if not domain:
            return False
        # Fast path: exact or near-exact string match
        for interest_val in current_interests:
            if not interest_val:
                continue
            if domain == interest_val:
                return True
            if interest_val in domain and len(domain) - len(interest_val) < 3:
                return True
            if domain in interest_val and len(interest_val) - len(domain) < 3:
                return True

        # Semantic check: catch near-synonyms like "AI应用" vs "人工智能"
        # Threshold 0.85 = only reject very close synonyms, not loosely related topics
        # (0.75 was too strict — rejected most domains when user has broad interests)
        if self.embedding_service is not None:
            from openbiliclaw.llm.embedding import cosine_similarity

            similarity_reject_threshold = 0.85
            try:
                domain_vec = await self.embedding_service.embed(domain)
                if domain_vec:
                    for interest_val in current_interests:
                        if not interest_val:
                            continue
                        interest_vec = await self.embedding_service.embed(interest_val)
                        if (
                            interest_vec
                            and cosine_similarity(domain_vec, interest_vec)
                            >= similarity_reject_threshold
                        ):
                            logger.debug(
                                "Explore domain rejected (semantic): %r ≈ %r",
                                domain,
                                interest_val,
                            )
                            return True
            except Exception:
                pass  # Fall through to False on embedding failure
        return False

    @staticmethod
    def _normalize_domain_key(value: str) -> str:
        return re.sub(r"\s+", "", value).strip().lower()

    def _interest_anchor_set(self, profile: SoulProfile) -> set[str]:
        anchors: set[str] = set()
        for interest_item in profile.preferences.interests[:5]:
            anchors.update(interest_aliases(str(interest_item.name)))
        return {anchor for anchor in anchors if anchor}

    def _prioritize_domains(
        self,
        domains: list[dict[str, object]],
        anchor_set: set[str],
    ) -> list[dict[str, object]]:
        if not domains:
            return []
        anchored: list[dict[str, object]] = []
        loose: list[dict[str, object]] = []
        for domain in domains:
            anchored_domain = self._is_interest_anchored(domain, anchor_set)
            domain["interest_anchored"] = anchored_domain
            if anchored_domain:
                anchored.append(domain)
            else:
                loose.append(domain)

        if not anchored:
            return domains[: self.max_domains]

        # Prioritize loose (novel) domains to fight echo chamber:
        # At least 3 loose domains when available, interleave with anchored
        loose_cap = max(3, (self.max_domains + 1) // 2)
        anchored_cap = max(1, self.max_domains - min(loose_cap, len(loose)))
        prioritized = [*loose[:loose_cap], *anchored[:anchored_cap]]
        return prioritized[: self.max_domains]

    def _is_interest_anchored(
        self,
        domain: dict[str, object],
        anchor_set: set[str],
    ) -> bool:
        raw_queries = domain.get("queries", [])
        queries = raw_queries if isinstance(raw_queries, list) else []
        haystacks = [
            self._normalize_domain_key(str(domain.get("domain", ""))),
            self._normalize_domain_key(str(domain.get("why_it_might_resonate", ""))),
            *[
                self._normalize_domain_key(str(query))
                for query in queries
                if isinstance(query, str)
            ],
        ]
        for anchor in anchor_set:
            if anchor and any(anchor in haystack for haystack in haystacks):
                return True
        return False

    def _clean_queries(self, raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        queries: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            query = str(item).strip()
            lowered = query.lower()
            if not query or lowered in seen:
                continue
            if any(bad in lowered for bad in ("热门", "推荐", "必看")):
                continue
            seen.add(lowered)
            queries.append(query)
            if len(queries) >= self.queries_per_domain:
                break
        return queries

    @staticmethod
    def _clamp_novelty(raw_value: object) -> float:
        value = ContentDiscoveryEngine._clamp_score(raw_value)
        return min(0.8, max(0.4, value))

    @staticmethod
    def _exploration_bonus(*, novelty_level: float, openness: float) -> float:
        return round(novelty_level * max(0.0, min(1.0, openness)), 4)

    @staticmethod
    def _sort_results(results: list[DiscoveredContent]) -> list[DiscoveredContent]:
        results.sort(key=lambda item: item.relevance_score, reverse=True)
        return results
