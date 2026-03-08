"""Search-based content discovery strategy."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryStrategy,
)

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile

from openbiliclaw.llm.prompts import build_explore_domains_prompt, build_search_queries_prompt

logger = logging.getLogger(__name__)


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


class SupportsSearchClient(Protocol):
    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]: ...


class SupportsRankingClient(Protocol):
    async def get_ranking(self, rid: int = 0) -> list[dict[str, object]]: ...


class SupportsMemoryManager(Protocol):
    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: object | None = None,
        end_time: object | None = None,
        keyword: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]: ...


class SupportsSeedStrategy(Protocol):
    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]: ...


class SupportsRelatedClient(Protocol):
    async def get_related_videos(self, bvid: str) -> list[dict[str, object]]: ...

    async def search(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "totalrank",
    ) -> list[dict[str, object]]: ...


@dataclass
class SearchStrategy(DiscoveryStrategy):
    """Discover content by generating search queries from user interests."""

    llm_service: SupportsStructuredTask
    bilibili_client: SupportsSearchClient
    queries_per_run: int = 8
    page_size: int = 10

    @property
    def name(self) -> str:
        return "search"

    async def discover(
        self, profile: SoulProfile, limit: int = 20
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

        Returns:
            Discovered content list.
        """
        queries = await self._generate_queries(profile)
        results: list[DiscoveredContent] = []
        seen_bvids: set[str] = set()

        for query_index, query in enumerate(queries):
            try:
                search_results = await self.bilibili_client.search(
                    query,
                    page=1,
                    page_size=self.page_size,
                )
            except Exception:
                logger.exception("Search query failed: %s", query)
                continue

            for item_index, item in enumerate(search_results):
                content = self._map_search_result(
                    item,
                    query_index=query_index,
                    item_index=item_index,
                )
                if content is None or content.bvid in seen_bvids:
                    continue
                seen_bvids.add(content.bvid)
                results.append(content)
                if len(results) >= limit:
                    return results

        return results

    async def _generate_queries(self, profile: SoulProfile) -> list[str]:
        prompt_messages = build_search_queries_prompt(
            profile_summary=self._profile_summary(profile)
        )
        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=prompt_messages[0]["content"],
                user_input=prompt_messages[1]["content"],
            )
            queries = self._parse_queries(str(getattr(response, "content", "")))
            if queries:
                return queries
        except Exception:
            logger.exception("Search query generation failed; falling back to local queries.")
        return self._fallback_queries(profile)

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

        for interest in profile.preferences.interests:
            query = str(interest.name).strip()
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

    @staticmethod
    def _profile_summary(profile: SoulProfile) -> dict[str, object]:
        return {
            "personality_portrait": profile.personality_portrait,
            "core_traits": profile.core_traits[:5],
            "interests": [
                {
                    "name": interest.name,
                    "category": interest.category,
                    "weight": interest.weight,
                }
                for interest in profile.preferences.interests[:10]
            ],
            "favorite_up_users": profile.preferences.favorite_up_users[:5],
            "deep_needs": profile.deep_needs[:5],
        }

    def _map_search_result(
        self,
        item: dict[str, object],
        *,
        query_index: int,
        item_index: int,
    ) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        return DiscoveredContent(
            bvid=bvid,
            title=self._clean_text(str(item.get("title", ""))),
            up_name=self._clean_text(str(item.get("author", ""))),
            up_mid=self._to_int(item.get("mid", 0)),
            cover_url=str(item.get("pic", "")),
            duration=self._parse_duration(item.get("duration", 0)),
            view_count=self._to_int(item.get("play", 0)),
            description=self._clean_text(str(item.get("description", ""))),
            source_strategy=self.name,
            relevance_score=max(0.0, 0.2 - query_index * 0.02 - item_index * 0.005),
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"<[^>]+>", "", value).strip()

    @staticmethod
    def _parse_duration(raw_value: object) -> int:
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, str) and ":" in raw_value:
            parts = [part for part in raw_value.split(":") if part.isdigit()]
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + int(seconds)
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        return SearchStrategy._to_int(raw_value)

    @staticmethod
    def _to_int(raw_value: object) -> int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            digits = raw_value.replace(",", "").strip()
            if digits.isdigit():
                return int(digits)
        return 0


@dataclass
class TrendingStrategy(DiscoveryStrategy):
    """Discover content from trending/ranking pages."""

    bilibili_client: SupportsRankingClient
    llm_service: SupportsStructuredTask
    score_threshold: float = 0.65
    max_related_rids: int = 4
    default_rids: tuple[int, ...] = (36, 188, 181, 119)

    @property
    def name(self) -> str:
        return "trending"

    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
        """Scan trending and ranking content, filter by soul relevance.

        Args:
            profile: User soul profile.
            limit: Maximum results.

        Returns:
            Discovered content list.
        """
        evaluator = ContentDiscoveryEngine(llm_service=self.llm_service)
        rids = await self._select_rids(profile)
        results: list[DiscoveredContent] = []
        seen_bvids: set[str] = set()

        for rid in rids:
            try:
                ranking_items = await self.bilibili_client.get_ranking(rid)
            except Exception:
                logger.exception("Trending ranking request failed: rid=%s", rid)
                continue

            for item in ranking_items:
                content = self._map_ranking_item(item)
                if content is None or content.bvid in seen_bvids:
                    continue
                seen_bvids.add(content.bvid)
                score = await evaluator.evaluate_content(content, profile)
                if score < self.score_threshold:
                    continue
                results.append(content)
                if len(results) >= limit:
                    return results

        return results

    async def _select_rids(self, profile: SoulProfile) -> list[int]:
        from openbiliclaw.llm.prompts import build_trending_rids_prompt

        messages = build_trending_rids_prompt(
            profile_summary=SearchStrategy._profile_summary(profile)
        )
        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
            )
            parsed = json.loads(str(getattr(response, "content", "")).strip())
            if isinstance(parsed, dict) and isinstance(parsed.get("rids"), list):
                selected = [
                    SearchStrategy._to_int(item)
                    for item in parsed["rids"]
                    if SearchStrategy._to_int(item) > 0
                ]
                selected = self._dedupe_ints(selected)[: self.max_related_rids]
                return [0, *selected]
        except Exception:
            logger.exception("Trending rid selection failed; using defaults.")
        return [0, *list(self.default_rids[: self.max_related_rids])]

    def _map_ranking_item(self, item: dict[str, object]) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        owner = item.get("owner")
        up_name = str(item.get("author", "")).strip()
        up_mid = SearchStrategy._to_int(item.get("mid", 0))
        if isinstance(owner, dict):
            up_name = str(owner.get("name", up_name)).strip()
            up_mid = SearchStrategy._to_int(owner.get("mid", up_mid))
        stat = item.get("stat")
        view_count = SearchStrategy._to_int(item.get("play", 0))
        like_count = SearchStrategy._to_int(item.get("like", 0))
        if isinstance(stat, dict):
            view_count = SearchStrategy._to_int(stat.get("view", view_count))
            like_count = SearchStrategy._to_int(stat.get("like", like_count))

        return DiscoveredContent(
            bvid=bvid,
            title=SearchStrategy._clean_text(str(item.get("title", ""))),
            up_name=SearchStrategy._clean_text(up_name),
            up_mid=up_mid,
            cover_url=str(item.get("pic", "")),
            duration=SearchStrategy._parse_duration(item.get("duration", 0)),
            view_count=view_count,
            like_count=like_count,
            description=SearchStrategy._clean_text(
                str(item.get("description", item.get("desc", "")))
            ),
            source_strategy=self.name,
        )

    @staticmethod
    def _dedupe_ints(values: list[int]) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered


@dataclass
class RelatedChainStrategy(DiscoveryStrategy):
    """Discover content by following related recommendation chains."""

    bilibili_client: SupportsRelatedClient
    llm_service: SupportsStructuredTask
    memory_manager: SupportsMemoryManager
    search_strategy: SupportsSeedStrategy | None = None
    trending_strategy: SupportsSeedStrategy | None = None
    score_threshold: float = 0.65
    max_seeds: int = 5
    related_per_seed: int = 8
    max_depth: int = 2

    @property
    def name(self) -> str:
        return "related_chain"

    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
        """Start from known good content and explore related chains.

        Args:
            profile: User soul profile.
            limit: Maximum results.

        Returns:
            Discovered content list.
        """
        evaluator = ContentDiscoveryEngine(llm_service=self.llm_service)
        seed_bvids = await self._select_seed_bvids(profile)
        if not seed_bvids:
            return []

        results: list[DiscoveredContent] = []
        seen_bvids = set(seed_bvids)
        visited_source_bvids: set[str] = set()
        frontier: list[tuple[str, int, int]] = [
            (seed_bvid, 1, seed_index) for seed_index, seed_bvid in enumerate(seed_bvids)
        ]

        while frontier:
            seed_bvid, depth, seed_index = frontier.pop(0)
            if seed_bvid in visited_source_bvids:
                continue
            visited_source_bvids.add(seed_bvid)
            try:
                related_items = await self.bilibili_client.get_related_videos(seed_bvid)
            except Exception:
                logger.exception("Related videos request failed: %s", seed_bvid)
                continue

            for item in related_items[: self.related_per_seed]:
                content = self._map_related_item(item)
                if content is None or content.bvid in seen_bvids:
                    continue
                seen_bvids.add(content.bvid)
                score = await evaluator.evaluate_content(content, profile)
                bonus = self._seed_bonus(seed_index) + self._depth_bonus(depth)
                content.relevance_score = min(1.0, round(score + bonus, 4))
                if content.relevance_score < self.score_threshold:
                    continue
                results.append(content)
                if depth < self.max_depth:
                    frontier.append((content.bvid, depth + 1, seed_index))
                if len(results) >= limit:
                    return results

        results.sort(key=lambda item: item.relevance_score, reverse=True)
        return results

    async def _select_seed_bvids(self, profile: SoulProfile) -> list[str]:
        seeds: list[str] = []
        seen: set[str] = set()

        for bvid in self._event_seed_bvids():
            if bvid in seen:
                continue
            seen.add(bvid)
            seeds.append(bvid)
            if len(seeds) >= self.max_seeds:
                return seeds

        for bvid in await self._preference_seed_bvids(profile):
            if bvid in seen:
                continue
            seen.add(bvid)
            seeds.append(bvid)
            if len(seeds) >= self.max_seeds:
                return seeds

        for strategy in (self.search_strategy, self.trending_strategy):
            if strategy is None:
                continue
            remaining = self.max_seeds - len(seeds)
            if remaining <= 0:
                break
            try:
                items = await strategy.discover(profile, limit=remaining)
            except Exception:
                logger.exception(
                    "Fallback seed strategy failed: %s",
                    getattr(strategy, "name", "unknown"),
                )
                continue
            for item in items:
                if item.bvid in seen or not item.bvid:
                    continue
                seen.add(item.bvid)
                seeds.append(item.bvid)
                if len(seeds) >= self.max_seeds:
                    return seeds

        return seeds

    def _event_seed_bvids(self) -> list[str]:
        events = self.memory_manager.query_events(
            event_types=["view", "favorite", "like"],
            limit=max(self.max_seeds * 3, 12),
        )
        seed_bvids: list[str] = []
        for event in events:
            bvid = self._extract_bvid_from_event(event)
            if bvid:
                seed_bvids.append(bvid)
        return seed_bvids

    async def _preference_seed_bvids(self, profile: SoulProfile) -> list[str]:
        queries: list[str] = []
        queries.extend(
            interest.name.strip()
            for interest in profile.preferences.interests[:2]
            if interest.name.strip()
        )
        queries.extend(
            up_name.strip()
            for up_name in profile.preferences.favorite_up_users[:1]
            if up_name.strip()
        )

        seeds: list[str] = []
        seen: set[str] = set()
        for query in queries:
            try:
                items = await self.bilibili_client.search(query, page=1, page_size=2)
            except Exception:
                logger.exception("Preference seed search failed: %s", query)
                continue
            for item in items:
                bvid = str(item.get("bvid", "")).strip()
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                seeds.append(bvid)
                if len(seeds) >= self.max_seeds:
                    return seeds
        return seeds

    @staticmethod
    def _extract_bvid_from_event(event: dict[str, object]) -> str:
        metadata = event.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if isinstance(metadata, dict):
            bvid = str(metadata.get("bvid", "")).strip()
            if bvid:
                return bvid

        url = str(event.get("url", "")).strip()
        match = re.search(r"/video/(BV[\w]+)", url)
        return match.group(1) if match else ""

    def _map_related_item(self, item: dict[str, object]) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        owner = item.get("owner")
        up_name = ""
        up_mid = 0
        if isinstance(owner, dict):
            up_name = SearchStrategy._clean_text(str(owner.get("name", "")))
            up_mid = SearchStrategy._to_int(owner.get("mid", 0))

        stat = item.get("stat")
        view_count = 0
        like_count = 0
        if isinstance(stat, dict):
            view_count = SearchStrategy._to_int(stat.get("view", 0))
            like_count = SearchStrategy._to_int(stat.get("like", 0))

        return DiscoveredContent(
            bvid=bvid,
            title=SearchStrategy._clean_text(str(item.get("title", ""))),
            up_name=up_name,
            up_mid=up_mid,
            cover_url=str(item.get("pic", "")),
            duration=SearchStrategy._parse_duration(item.get("duration", 0)),
            view_count=view_count,
            like_count=like_count,
            description=SearchStrategy._clean_text(
                str(item.get("desc", item.get("description", "")))
            ),
            source_strategy=self.name,
        )

    @staticmethod
    def _seed_bonus(seed_index: int) -> float:
        return max(0.0, 0.03 - seed_index * 0.01)

    @staticmethod
    def _depth_bonus(depth: int) -> float:
        return max(0.0, 0.02 - max(0, depth - 1) * 0.01)


@dataclass
class ExploreStrategy(DiscoveryStrategy):
    """Cross-domain surprise discovery — find the unexpected."""

    llm_service: SupportsStructuredTask
    bilibili_client: SupportsSearchClient
    score_threshold: float = 0.65
    queries_per_domain: int = 2
    max_domains: int = 5

    @property
    def name(self) -> str:
        return "explore"

    async def discover(
        self, profile: SoulProfile, limit: int = 20
    ) -> list[DiscoveredContent]:
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
        if not domains:
            return []

        evaluator = ContentDiscoveryEngine(llm_service=self.llm_service)
        results: list[DiscoveredContent] = []
        seen_bvids: set[str] = set()

        for domain in domains:
            novelty_level = self._clamp_novelty(domain.get("novelty_level", 0.5))
            for query in self._clean_queries(domain.get("queries", [])):
                try:
                    search_results = await self.bilibili_client.search(
                        query,
                        page=1,
                        page_size=10,
                    )
                except Exception:
                    logger.exception("Explore query failed: %s", query)
                    continue

                for item_index, item in enumerate(search_results):
                    content = SearchStrategy(
                        llm_service=self.llm_service,
                        bilibili_client=self.bilibili_client,
                    )._map_search_result(
                        item,
                        query_index=0,
                        item_index=item_index,
                    )
                    if content is None or content.bvid in seen_bvids:
                        continue
                    seen_bvids.add(content.bvid)
                    content.source_strategy = self.name
                    score = await evaluator.evaluate_content(content, profile)
                    bonus = self._exploration_bonus(
                        novelty_level=novelty_level,
                        openness=profile.preferences.exploration_openness,
                    )
                    content.relevance_score = min(1.0, round(score * 0.75 + bonus * 0.25, 4))
                    if content.relevance_score < self.score_threshold:
                        continue
                    results.append(content)
                    if len(results) >= limit:
                        return self._sort_results(results)

        return self._sort_results(results)

    async def _generate_domains(self, profile: SoulProfile) -> list[dict[str, object]]:
        messages = build_explore_domains_prompt(
            profile_summary=SearchStrategy._profile_summary(profile)
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
            interest.name.strip().lower()
            for interest in profile.preferences.interests[:10]
            if interest.name.strip()
        }
        domains: list[dict[str, object]] = []
        seen_domains: set[str] = set()
        for item in parsed["domains"]:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain", "")).strip()
            normalized = domain.lower()
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
        return [domain for domain in domains if domain["queries"]]

    @staticmethod
    def _looks_too_similar(domain: str, current_interests: set[str]) -> bool:
        return any(
            domain == interest or domain in interest or interest in domain
            for interest in current_interests
        )

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
