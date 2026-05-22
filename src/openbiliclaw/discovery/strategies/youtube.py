"""YouTube discovery strategies.

Three strategies backed by the ``YtScraperClient`` (scrapetube + yt-dlp):

YoutubeSearchStrategy
    LLM generates keywords from the soul profile → keyword search via
    scrapetube → LLM evaluates candidates.

YoutubeTrendingStrategy
    Fetches the YouTube trending feed (yt-dlp flat-extract) for the
    configured region → LLM evaluates against soul profile.

YoutubeChannelStrategy
    Reads subscribed YouTube channels from the user's stored follow events
    → fetches recent uploads via scrapetube → LLM evaluates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.youtube.client import YtScraperClient, normalize_yt_video

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_QUERIES_SYSTEM_PROMPT = """\
你要为 YouTube 内容发现生成一组适合 YouTube 搜索的关键词。

规则：
1. 输出必须是严格 JSON，不要附带解释。
2. query 是 2-4 个词的短语，适合直接在 YouTube 搜索框输入。
3. 可以中文或英文，根据话题选最常见的搜索语言。
4. 数量 5 到 8 个，覆盖用户画像中不同兴趣领域。
5. 避免与已有很多内容的领域过度集中。

输出格式：
{"queries": ["机器学习 入门", "history documentary", "摄影 vlog", ...]}
"""


def _extract_llm_json_payload(raw: object) -> object:
    """Return a JSON-like payload from either raw provider JSON or LLMResponse."""
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        raw = content
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


# ---------------------------------------------------------------------------
# YoutubeSearchStrategy
# ---------------------------------------------------------------------------


@dataclass
class YoutubeSearchStrategy(DiscoveryStrategy):
    """Discover YouTube content by LLM-generated keyword search."""

    client: YtScraperClient
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    queries_per_run: int = 6
    results_per_query: int = 15
    score_threshold: float = 0.65
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "yt_search"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        queries = await self._generate_queries(profile)
        self.last_intermediates = {"queries": list(queries)}
        if not queries:
            return []

        raw_batches = await asyncio.gather(
            *[self.client.search_videos(q, limit=self.results_per_query) for q in queries],
            return_exceptions=True,
        )

        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for batch in raw_batches:
            if isinstance(batch, BaseException):
                logger.warning("yt_search batch failed: %s", batch)
                continue
            for raw in batch:
                content = normalize_yt_video(raw, source_strategy=self.name)
                if content is None or content.content_id in seen:
                    continue
                seen.add(content.content_id)
                candidates.append(content)

        logger.info("yt_search: %d queries → %d candidates", len(queries), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation:
            return candidates[:limit]

        return await self._evaluate(candidates, profile, limit)

    async def _generate_queries(self, profile: SoulProfile) -> list[str]:
        profile_summary = build_profile_summary(profile)
        user_input = json.dumps(
            {"profile": profile_summary, "max_queries": self.queries_per_run},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        try:
            raw = await self.llm_service.complete_structured_task(
                system_instruction=_QUERIES_SYSTEM_PROMPT,
                user_input=user_input,
                temperature=0.8,
                max_tokens=512,
                caller="yt_search.generate_queries",
            )
            parsed = _extract_llm_json_payload(raw)
            if isinstance(parsed, dict):
                queries = parsed.get("queries") or []
                return [str(q).strip() for q in queries if str(q).strip()][: self.queries_per_run]
        except Exception as exc:
            logger.warning("yt_search: query generation failed, falling back to interests: %s", exc)

        # Fallback: use interest names directly
        return [
            str(interest.name).strip()
            for interest in profile.preferences.interests
            if str(interest.name).strip()
        ][: self.queries_per_run]

    async def _evaluate(
        self, candidates: list[DiscoveredContent], profile: SoulProfile, limit: int
    ) -> list[DiscoveredContent]:
        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=self.database,
            concurrency=self.concurrency,
        )
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
# YoutubeTrendingStrategy
# ---------------------------------------------------------------------------


@dataclass
class YoutubeTrendingStrategy(DiscoveryStrategy):
    """Discover YouTube content from the trending feed."""

    client: YtScraperClient
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    fetch_limit: int = 50
    score_threshold: float = 0.60
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "yt_trending"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        raw = await self.client.get_trending(limit=self.fetch_limit)
        self.last_intermediates = {"fetched": len(raw)}

        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for item in raw:
            content = normalize_yt_video(item, source_strategy=self.name)
            if content is None or content.content_id in seen:
                continue
            seen.add(content.content_id)
            candidates.append(content)

        logger.info("yt_trending: %d trending → %d candidates", len(raw), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation:
            return candidates[:limit]

        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=self.database,
            concurrency=self.concurrency,
        )
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
# YoutubeChannelStrategy
# ---------------------------------------------------------------------------


class SupportsYtFollowQuery(Protocol):
    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


@dataclass
class YoutubeChannelStrategy(DiscoveryStrategy):
    """Discover recent uploads from YouTube channels the user subscribes to.

    Channel IDs are read at discover() time from stored follow events
    (event_type='follow', source_platform='youtube') so the list stays
    fresh without requiring a restart.
    """

    client: YtScraperClient
    llm_service: SupportsStructuredTask
    memory: SupportsYtFollowQuery
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    max_channels: int = 10
    videos_per_channel: int = 5
    score_threshold: float = 0.65
    llm_evaluation: bool = True
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "yt_channel"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        channel_ids = self._subscribed_channel_ids()
        self.last_intermediates = {"channel_ids": channel_ids}
        if not channel_ids:
            logger.debug("yt_channel: no subscribed channels in DB")
            return []

        batches = await asyncio.gather(
            *[
                self.client.get_channel_videos(ch, limit=self.videos_per_channel)
                for ch in channel_ids
            ],
            return_exceptions=True,
        )

        seen: set[str] = set()
        candidates: list[DiscoveredContent] = []
        for batch in batches:
            if isinstance(batch, BaseException):
                logger.warning("yt_channel batch failed: %s", batch)
                continue
            for raw in batch:
                content = normalize_yt_video(raw, source_strategy=self.name)
                if content is None or content.content_id in seen:
                    continue
                seen.add(content.content_id)
                candidates.append(content)

        logger.info("yt_channel: %d channels → %d candidates", len(channel_ids), len(candidates))
        if not candidates:
            return []

        if not self.llm_evaluation:
            return candidates[:limit]

        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=self.database,
            concurrency=self.concurrency,
        )
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

    def _subscribed_channel_ids(self) -> list[str]:
        """Read YouTube channel references from stored follow events."""
        import json as _json

        channel_refs: list[str] = []
        seen: set[str] = set()
        try:
            events = self.memory.query_events(event_types=["follow"], limit=500)
        except Exception:
            return []

        for ev in events:
            # metadata is a JSON string in DB rows; may already be a dict
            meta = ev.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            if not isinstance(meta, dict):
                continue
            if str(meta.get("source_platform", "")) != "youtube":
                continue
            channel_ref = (
                str(meta.get("channel_id", "")).strip()
                or str(meta.get("channel_url", "")).strip()
                or str(ev.get("url", "")).strip()
            )
            if channel_ref and channel_ref not in seen:
                seen.add(channel_ref)
                channel_refs.append(channel_ref)
                if len(channel_refs) >= self.max_channels:
                    break

        return channel_refs
