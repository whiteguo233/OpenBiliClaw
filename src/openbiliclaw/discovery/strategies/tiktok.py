"""TikTok discovery strategies (experimental).

Three strategies over a TikTok client (yt-dlp backend, web API backend, or
the mode-dispatching ``TiktokRouterClient`` — all expose the same async
surface):

TiktokFeedStrategy
    Anonymous For-You feed via the web API backend
    (``/api/recommend/item_list/``) → LLM evaluates. No configuration
    dependency; yields nothing under ``mode = "ytdlp"`` (yt-dlp has no
    feed surface).

TiktokTagStrategy
    LLM generates TikTok hashtags from the soul profile (or the unified
    keyword planner injects words, which are mapped onto hashtags) →
    hashtag listing per hashtag → LLM evaluates candidates. yt-dlp ships
    no TikTok search extractor and guest search is gated upstream, so this
    hashtag mapping stays the keyword-driven path.

TiktokUserStrategy
    Reads configured creator handles → fetches recent uploads → LLM
    evaluates.

TiktokSearchStrategy
    Keyword search via the web API backend (``/api/search/item/full/``).
    Unlike the tag path, planner-injected words are used **verbatim** (no
    hashtag compaction) — that is search's core value over hashtags. Only
    mounted when a login cookie is configured and the mode allows the web
    backend: guest identities are gated upstream, and logged-in scraping
    carries account risk (TikTok ToS), so it is off by default.

Client methods may return raw yt-dlp entry dicts (yt-dlp backend) or
ready-made ``DiscoveredContent`` (web backend); ``_coerce_candidate``
normalizes both.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.discovery.engine import (
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    discovery_raw_candidate_mode_enabled,
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.strategies._utils import build_query_generation_profile_summary
from openbiliclaw.llm.task_options import without_core_memory_kwargs
from openbiliclaw.sources.tiktok import (
    normalize_tiktok_handle,
    normalize_tiktok_tag,
    normalize_tiktok_video,
    tag_from_query,
)

if TYPE_CHECKING:
    from openbiliclaw.recommendation.publication_preference import PublicationDatePreference
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


class SupportsTiktokDiscovery(Protocol):
    """The async client surface the TikTok strategies consume.

    Implemented by the yt-dlp ``TiktokClient`` (raw entry dicts), the web
    ``TiktokWebClient`` (``DiscoveredContent`` | ``None``), and the
    mode-dispatching ``TiktokRouterClient``.
    """

    async def get_feed(self, *, limit: int = 12) -> Any: ...

    async def get_tag_videos(self, tag: str, *, limit: int = 15) -> list[Any]: ...

    async def get_user_videos(self, handle: str, *, limit: int = 10) -> list[Any]: ...

    async def search_videos(self, keyword: str, *, limit: int = 20) -> list[Any]: ...


_TAGS_SYSTEM_PROMPT = """\
你要为 TikTok 内容发现生成一组适合 TikTok 话题标签（hashtag）的词。

规则：
1. 输出必须是严格 JSON，不要附带解释。
2. tag 是不带 # 的话题标签词，优先英文单词或短组合（不带空格），
   例如 "satisfying"、"booktok"、"historyfacts"；中文话题可用中文标签词。
3. 数量 4 到 8 个，覆盖用户画像中不同兴趣领域。
4. 避免与已有很多内容的领域过度集中。

输出格式：
{"tags": ["booktok", "historyfacts", ...]}
"""

_SEARCH_SYSTEM_PROMPT = """\
你要为 TikTok 内容发现生成一组搜索关键词。

规则：
1. 输出必须是严格 JSON，不要附带解释。
2. keyword 是直接用于 TikTok 搜索框的词或短语，可以带空格，
   例如 "machine learning"、"cooking tips"；中文关键词可用中文。
3. 数量 4 到 8 个，覆盖用户画像中不同兴趣领域。
4. 避免与已有很多内容的领域过度集中。

输出格式：
{"keywords": ["machine learning", "cooking tips", ...]}
"""


def _extract_llm_json_payload(raw: object) -> object:
    """Return a JSON-like payload from either raw provider JSON or LLMResponse."""
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        raw = content
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _record_batch_failures(
    intermediates: dict[str, object], keys: list[str], batches: list[Any]
) -> None:
    errors = {
        key: str(getattr(batch, "reason", "unavailable"))
        for key, batch in zip(keys, batches, strict=True)
        if batch is None or isinstance(batch, BaseException)
    }
    intermediates["errors"] = errors


def _coerce_candidate(raw: object, source_strategy: str) -> DiscoveredContent | None:
    """Accept both backend shapes: yt-dlp entry dict or web DiscoveredContent."""
    if isinstance(raw, DiscoveredContent):
        if not raw.source_strategy:
            raw.source_strategy = source_strategy
        return raw
    if isinstance(raw, dict):
        return normalize_tiktok_video(raw, source_strategy=source_strategy)
    return None


# ---------------------------------------------------------------------------
# TiktokTagStrategy
# ---------------------------------------------------------------------------


@dataclass
class TiktokTagStrategy(DiscoveryStrategy):
    """Discover TikTok content by hashtag listing (tiktok:tag)."""

    client: SupportsTiktokDiscovery
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    tags: tuple[str, ...] = ()
    tags_per_run: int = 6
    results_per_tag: int = 15
    score_threshold: float = 0.60
    date_preference: PublicationDatePreference | None = None
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "tiktok_tag"

    @property
    def source_platform(self) -> str:
        return "tiktok"

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        queries: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        baseline = [tag for tag in (normalize_tiktok_tag(t) for t in self.tags) if tag]
        if queries is None:
            resolved_tags = await self._generate_tags(profile)
            # Configured baseline tags are sticky and lead the LLM path.
            tags = self._dedupe_tags([*baseline, *resolved_tags])[: max(1, self.tags_per_run)]
        else:
            # Unified keyword planner injection: the planner emits search
            # phrases, which TikTok can only consume as hashtags. Claimed words
            # come first and are never truncated away: the producer marks every
            # claimed word ``used`` after handoff, so dropping one here would
            # burn it unsearched. Baseline tags only fill remaining capacity.
            resolved_tags = self._dedupe_tags([tag_from_query(q) for q in queries])
            tags = self._dedupe_tags([*resolved_tags, *baseline])[
                : max(self.tags_per_run, len(resolved_tags), 1)
            ]
        self.last_intermediates = {"tags": list(tags)}
        if not tags:
            return []

        raw_batches = await asyncio.gather(
            *[self.client.get_tag_videos(tag, limit=self.results_per_tag) for tag in tags],
            return_exceptions=True,
        )

        _record_batch_failures(self.last_intermediates, tags, raw_batches)
        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        # ``raw_batches[i]`` corresponds to ``tags[i]`` (gather preserves order).
        for tag, batch in zip(tags, raw_batches, strict=True):
            if batch is None or isinstance(batch, BaseException):
                logger.warning("tiktok_tag batch failed: %s", batch)
                continue
            keyword_id = keyword_ids.get(tag) if keyword_ids else None
            if keyword_id is None and keyword_ids:
                # The planner word was compacted into a hashtag; recover the id
                # through the same mapping the producer used to inject it.
                for word, word_id in keyword_ids.items():
                    if tag_from_query(word) == tag:
                        keyword_id = word_id
                        break
            for raw in batch:
                content = _coerce_candidate(raw, self.name)
                if content is None or content.content_id in seen:
                    continue
                content.source_keyword_id = keyword_id
                seen.add(content.content_id)
                candidates.append(content)

        logger.info("tiktok_tag: %d tags → %d candidates", len(tags), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation or discovery_raw_candidate_mode_enabled():
            return candidates[:limit]

        evaluator = self.content_evaluator()
        candidates = self.filter_candidates_for_eval(candidates)
        trimmed = trim_candidates_for_llm(candidates, limit=limit, source_context=self.name)
        scores = await evaluator.evaluate_content_batch(trimmed, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(trimmed, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _dedupe_tags(tags: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in tags:
            tag = str(item).strip().lstrip("#")
            if not tag or tag.casefold() in seen:
                continue
            seen.add(tag.casefold())
            deduped.append(tag)
        return deduped

    async def _generate_tags(self, profile: SoulProfile) -> list[str]:
        profile_summary = build_query_generation_profile_summary(profile)
        user_input = json.dumps(
            {"profile": profile_summary, "max_tags": self.tags_per_run},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            complete_structured = self.llm_service.complete_structured_task
            raw = await complete_structured(
                system_instruction=_TAGS_SYSTEM_PROMPT,
                user_input=user_input,
                temperature=0.8,
                max_tokens=512,
                caller="tiktok_tag.generate_tags",
                **without_core_memory_kwargs(complete_structured),
            )
            parsed = _extract_llm_json_payload(raw)
            if isinstance(parsed, dict):
                tags = parsed.get("tags") or []
                return [normalized for item in tags if (normalized := normalize_tiktok_tag(item))][
                    : self.tags_per_run
                ]
        except Exception as exc:
            logger.warning("tiktok_tag: tag generation failed, falling back to interests: %s", exc)

        # Fallback: compact interest names into hashtag candidates.
        return [
            tag
            for interest in profile.preferences.interests
            if (tag := tag_from_query(interest.name))
        ][: self.tags_per_run]


# ---------------------------------------------------------------------------
# TiktokUserStrategy
# ---------------------------------------------------------------------------


@dataclass
class TiktokUserStrategy(DiscoveryStrategy):
    """Discover recent uploads from configured TikTok creators (tiktok:user).

    Handles come from ``[sources.tiktok].creators``; there is no TikTok
    follow-event bootstrap (the source has no login path), so the list is
    config-owned.
    """

    client: SupportsTiktokDiscovery
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    creators: tuple[str, ...] = ()
    max_creators: int = 10
    videos_per_creator: int = 5
    score_threshold: float = 0.60
    date_preference: PublicationDatePreference | None = None
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "tiktok_user"

    @property
    def source_platform(self) -> str:
        return "tiktok"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        handles = [h for h in (normalize_tiktok_handle(c) for c in self.creators) if h][
            : self.max_creators
        ]
        self.last_intermediates = {"creators": list(handles)}
        if not handles:
            logger.debug("tiktok_user: no creators configured")
            return []

        batches = await asyncio.gather(
            *[
                self.client.get_user_videos(handle, limit=self.videos_per_creator)
                for handle in handles
            ],
            return_exceptions=True,
        )

        _record_batch_failures(self.last_intermediates, handles, batches)
        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for batch in batches:
            if batch is None or isinstance(batch, BaseException):
                logger.warning("tiktok_user batch failed: %s", batch)
                continue
            for raw in batch:
                content = _coerce_candidate(raw, self.name)
                if content is None or content.content_id in seen:
                    continue
                seen.add(content.content_id)
                candidates.append(content)

        logger.info("tiktok_user: %d creators → %d candidates", len(handles), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation or discovery_raw_candidate_mode_enabled():
            return candidates[:limit]

        evaluator = self.content_evaluator()
        candidates = self.filter_candidates_for_eval(candidates)
        trimmed = trim_candidates_for_llm(candidates, limit=limit, source_context=self.name)
        scores = await evaluator.evaluate_content_batch(trimmed, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(trimmed, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                break
        return results


# ---------------------------------------------------------------------------
# TiktokFeedStrategy
# ---------------------------------------------------------------------------


@dataclass
class TiktokFeedStrategy(DiscoveryStrategy):
    """Discover from the anonymous TikTok For-You feed (web API backend).

    ``/api/recommend/item_list/`` needs no configuration and no login: the
    same guest identity bootstrap that mints ``msToken`` also serves the
    feed. The feed is a high-visibility surface, so the producer keeps its
    daily budget small by default. Under ``mode = "ytdlp"`` the router
    returns ``None`` (yt-dlp has no feed surface) and the strategy yields
    nothing.
    """

    client: SupportsTiktokDiscovery
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    results_per_run: int = 12
    score_threshold: float = 0.60
    date_preference: PublicationDatePreference | None = None
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "tiktok_feed"

    @property
    def source_platform(self) -> str:
        return "tiktok"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        try:
            batch = await self.client.get_feed(limit=max(1, self.results_per_run))
        except Exception as exc:
            self.last_intermediates = {
                "fetched": 0,
                "errors": {"feed": str(getattr(exc, "reason", "unavailable"))},
            }
            return []
        if batch is None:
            self.last_intermediates = {"fetched": 0, "errors": {"feed": "unavailable"}}
            return []
        if not batch:
            # ``None`` (backend unavailable / ytdlp mode) and ``[]`` both mean
            # "nothing this run"; the router already logged the reason.
            self.last_intermediates = {"fetched": 0}
            return []

        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for raw in batch:
            content = _coerce_candidate(raw, self.name)
            if content is None or content.content_id in seen:
                continue
            seen.add(content.content_id)
            candidates.append(content)

        self.last_intermediates = {"fetched": len(candidates)}
        logger.info("tiktok_feed: %d candidates", len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation or discovery_raw_candidate_mode_enabled():
            return candidates[:limit]

        evaluator = self.content_evaluator()
        candidates = self.filter_candidates_for_eval(candidates)
        trimmed = trim_candidates_for_llm(candidates, limit=limit, source_context=self.name)
        scores = await evaluator.evaluate_content_batch(trimmed, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(trimmed, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                break
        return results


# ---------------------------------------------------------------------------
# TiktokSearchStrategy
# ---------------------------------------------------------------------------


@dataclass
class TiktokSearchStrategy(DiscoveryStrategy):
    """Discover TikTok content by keyword search (web API backend).

    Only mounted when the router reports ``search_available`` (web backend +
    login cookie): guest identities are gated on the search endpoint
    upstream, and logged-in scraping carries account risk (TikTok ToS), so
    this strategy never runs by default.

    Planner-injected words are used **verbatim** — unlike the tag strategy
    there is no hashtag compaction, which is exactly search's value over the
    hashtag path (multi-word phrases, natural language). Search responses
    mix card types; the client already filters down to video items
    (``parse_tiktok_item(..., require_video=True)``).
    """

    client: SupportsTiktokDiscovery
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    keywords_per_run: int = 4
    results_per_keyword: int = 10
    score_threshold: float = 0.60
    date_preference: PublicationDatePreference | None = None
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "tiktok_search"

    @property
    def source_platform(self) -> str:
        return "tiktok"

    async def discover(
        self,
        profile: SoulProfile,
        limit: int = 20,
        *,
        queries: list[str] | None = None,
        keyword_ids: dict[str, int] | None = None,
    ) -> list[DiscoveredContent]:
        if queries is None:
            # Self-generated keywords are capped inside ``_generate_keywords``.
            keywords = await self._generate_keywords(profile)
        else:
            # Verbatim planner words — no hashtag compaction here, and never
            # truncated: the producer marks every injected word used after
            # handoff, so dropping one would burn it unsearched.
            keywords = self._dedupe_keywords(queries)
        self.last_intermediates = {"queries": list(keywords)}
        if not keywords:
            return []

        raw_batches = await asyncio.gather(
            *[
                self.client.search_videos(keyword, limit=self.results_per_keyword)
                for keyword in keywords
            ],
            return_exceptions=True,
        )

        _record_batch_failures(self.last_intermediates, keywords, raw_batches)
        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for keyword, batch in zip(keywords, raw_batches, strict=True):
            if batch is None or isinstance(batch, BaseException):
                logger.warning("tiktok_search batch failed: %s", batch)
                continue
            keyword_id = keyword_ids.get(keyword) if keyword_ids else None
            for raw in batch:
                content = _coerce_candidate(raw, self.name)
                if content is None or content.content_id in seen:
                    continue
                content.source_keyword_id = keyword_id
                seen.add(content.content_id)
                candidates.append(content)

        logger.info("tiktok_search: %d keywords → %d candidates", len(keywords), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation or discovery_raw_candidate_mode_enabled():
            return candidates[:limit]

        evaluator = self.content_evaluator()
        candidates = self.filter_candidates_for_eval(candidates)
        trimmed = trim_candidates_for_llm(candidates, limit=limit, source_context=self.name)
        scores = await evaluator.evaluate_content_batch(trimmed, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(trimmed, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _dedupe_keywords(words: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in words:
            word = str(item or "").strip()
            if not word or word.casefold() in seen:
                continue
            seen.add(word.casefold())
            deduped.append(word)
        return deduped

    async def _generate_keywords(self, profile: SoulProfile) -> list[str]:
        profile_summary = build_query_generation_profile_summary(profile)
        user_input = json.dumps(
            {"profile": profile_summary, "max_keywords": self.keywords_per_run},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            complete_structured = self.llm_service.complete_structured_task
            raw = await complete_structured(
                system_instruction=_SEARCH_SYSTEM_PROMPT,
                user_input=user_input,
                temperature=0.8,
                max_tokens=512,
                caller="tiktok_search.generate_keywords",
                **without_core_memory_kwargs(complete_structured),
            )
            parsed = _extract_llm_json_payload(raw)
            if isinstance(parsed, dict):
                keywords = parsed.get("keywords") or []
                return self._dedupe_keywords([str(item) for item in keywords])[
                    : self.keywords_per_run
                ]
        except Exception as exc:
            logger.warning(
                "tiktok_search: keyword generation failed, falling back to interests: %s", exc
            )

        return [interest.name for interest in profile.preferences.interests][
            : self.keywords_per_run
        ]
