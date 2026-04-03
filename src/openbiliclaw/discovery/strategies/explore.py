"""Cross-domain exploration discovery strategy."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
)
from openbiliclaw.discovery.strategies._utils import (
    SupportsSearchClient,
    _gather_bounded,
    build_profile_summary,
    interest_aliases,
    interest_anchors,
)
from openbiliclaw.discovery.strategies.search import SearchStrategy
from openbiliclaw.llm.prompts import build_explore_domains_prompt

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)


@dataclass
class ExploreStrategy(DiscoveryStrategy):
    """Cross-domain surprise discovery -- find the unexpected."""

    llm_service: SupportsStructuredTask
    bilibili_client: SupportsSearchClient
    concurrency: DiscoveryConcurrencyController | None = None
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
        domains = await self._generate_domains(profile)
        self.last_intermediates = {"domains": list(domains)}
        if not domains:
            return []

        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            concurrency=self.concurrency,
        )
        search_strategy = SearchStrategy(
            llm_service=self.llm_service,
            bilibili_client=self.bilibili_client,
            concurrency=self.concurrency,
        )
        anchor_list = interest_anchors(profile)
        runner = self.concurrency.run_bilibili if self.concurrency is not None else None
        request_plan: list[tuple[str, float, bool, str]] = []
        for domain in domains:
            novelty_level = self._clamp_novelty(domain.get("novelty_level", 0.5))
            interest_anchored = bool(domain.get("interest_anchored", False))
            domain_name = str(domain.get("domain", "")).strip()
            for query in self._clean_queries(domain.get("queries", [])):
                request_plan.append((query, novelty_level, interest_anchored, domain_name))

        search_outcomes = await _gather_bounded(
            [
                self.bilibili_client.search(
                    query,
                    page=1,
                    page_size=10,
                )
                for query, _, _, _ in request_plan
            ],
            runner=runner,
        )

        candidates: list[tuple[DiscoveredContent, float, bool]] = []
        seen_bvids: set[str] = set()
        for (query, novelty_level, interest_anchored, domain_label), outcome in zip(
            request_plan, search_outcomes, strict=True
        ):
            if isinstance(outcome, BaseException):
                logger.exception("Explore query failed: %s", query, exc_info=outcome)
                continue
            if not isinstance(outcome, list):
                continue
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
                    normalized_domain = re.sub(r"\s+", "", domain_label).lower()[:8]
                    content.topic_group = normalized_domain
                    # Use query-level granularity for topic_key so both diversity
                    # and no_echo_chamber scoring see distinct entries per query
                    normalized_query = re.sub(r"\s+", "", query).lower()[:16]
                    content.topic_key = f"explore:{normalized_domain}:{normalized_query}"
                candidates.append((content, novelty_level, interest_anchored))

        scores = await asyncio.gather(
            *(evaluator.evaluate_content(content, profile) for content, _, _ in candidates)
        )
        results: list[DiscoveredContent] = []
        for (
            content,
            novelty_level,
            interest_anchored,
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
            explore_threshold = self.score_threshold - 0.15 if self.score_threshold > 0.45 else self.score_threshold
            if content.relevance_score < explore_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                return self._sort_results(results)

        return self._sort_results(results)

    async def _generate_domains(self, profile: SoulProfile) -> list[dict[str, object]]:
        messages = build_explore_domains_prompt(
            profile_summary=build_profile_summary(profile)
            | {"exploration_openness": profile.preferences.exploration_openness}
        )
        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
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
            if self._looks_too_similar(normalized, current_interests):
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

    @staticmethod
    def _looks_too_similar(domain: str, current_interests: set[str]) -> bool:
        if not domain:
            return False
        for interest_val in current_interests:
            if not interest_val:
                continue
            # Exact match: definitely too similar
            if domain == interest_val:
                return True
            # Domain is a trivial extension of interest (<3 chars added): too similar
            # e.g. "纪录片" vs "纪录片类" -- too close
            # But "纪录片" vs "纪录片幕后工艺" -- different enough to explore
            if interest_val in domain and len(domain) - len(interest_val) < 3:
                return True
            if domain in interest_val and len(interest_val) - len(domain) < 3:
                return True
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
