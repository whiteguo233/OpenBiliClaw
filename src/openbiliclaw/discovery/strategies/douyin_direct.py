"""Douyin direct-cookie discovery strategy."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.discovery.engine import (
    ContentDiscoveryEngine,
    DiscoveredContent,
    DiscoveryConcurrencyController,
    DiscoveryStrategy,
    SupportsStructuredTask,
    discovery_raw_candidate_mode_enabled,
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.strategies._utils import build_profile_summary
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.task_options import without_core_memory_kwargs
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


# Douyin-flavored keyword generation. Mirrors x.py / xhs_keyword_gen: a
# byte-static system prompt (prompt-cache convention) with all per-call data
# (the build_profile_summary dict) in the user message.
_DOUYIN_KEYWORDS_SYSTEM_PROMPT = """\
你要为抖音内容发现生成一组适合抖音搜索的关键词。

抖音风格的关键词特征：
- 口语化、具体、贴近大众日常，不要宽泛的学科 / 品类词
- 偏话题 / 场景 / 热点表达（"教程 / 合集 / 名场面 / 挑战 / vlog / 测评"等尾词常见）
- 2~10 个字为主，中文为主
- 避免只给单字类目词（"科技""游戏"），要加限定词

规则：
1. 输出必须是严格 JSON，不要附带解释。
2. 数量 3 到 6 个，覆盖用户画像中不同兴趣领域，避开 disliked_topics。

输出格式：
{"keywords": ["露营装备测评", "和田玉鉴别教程", ...]}
"""


def _build_douyin_keyword_user_prompt(profile: SoulProfile, count: int) -> str:
    # Same canonical structured profile every other discovery prompt sees
    # (B站 / YouTube / X / 小红书 query-gen, all-platform evaluation) — no
    # divergent representation. Deterministic dump keeps the cache prefix stable.
    summary = build_profile_summary(profile)
    return (
        "<profile_summary>\n"
        + json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n</profile_summary>\n\n"
        + "请基于上面画像里的兴趣（interests / interest_domains），避开 disliked_topics，"
        + f"输出 {count} 个适合抖音搜索的关键词。"
    )


def _parse_douyin_keywords(content: str, *, count: int) -> list[str]:
    payload = parse_llm_json_tolerant(content)
    if payload is None:
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            logger.warning("douyin keyword LLM returned non-JSON: %r", content[:200])
            return []
    if not isinstance(payload, dict):
        return []
    raw_keywords = payload.get("keywords", [])
    if not isinstance(raw_keywords, list):
        return []
    seen: set[str] = set()
    keywords: list[str] = []
    for item in raw_keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        keywords.append(text)
        if len(keywords) >= count:
            break
    return keywords


@dataclass
class DouyinDirectStrategy(DiscoveryStrategy):
    """Discover Douyin candidates using backend direct-cookie Web requests."""

    client: SupportsDouyinDirectClient
    llm_service: SupportsStructuredTask | None = None
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    sources: tuple[str, ...] = ("search", "hot", "feed")
    seed_keywords: tuple[str, ...] = ()
    # P1.8 yield provenance: ``keyword text → discovery_keywords.id`` for the
    # injected search words. Empty for legacy runs. Search candidates produced
    # by a mapped keyword carry its id so admission can backfill yield.
    seed_keyword_ids: dict[str, int] = field(default_factory=dict)
    creator_sec_uids: tuple[str, ...] = ()
    keywords_per_run: int = 5
    per_source_limit: int = 20
    llm_evaluation: bool = True
    score_threshold: float = 0.60
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "douyin_direct"

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        # Each raw item carries (source_strategy, raw_dict, source_keyword_id).
        # Only search items get a non-None keyword id (P1.8 yield provenance);
        # hot / feed / creator items are attribution-free (None).
        raw_items: list[tuple[str, dict[str, object], int | None]] = []
        # Only synthesize / LLM-generate search keywords when the search source
        # is active. hot/feed/creator-only modes must NOT burn an LLM call (or
        # fall back to interest names) for keywords they will never search.
        keywords = await self._keywords(profile) if "search" in self.sources else []
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
                keyword_id = self.seed_keyword_ids.get(keyword) if self.seed_keyword_ids else None
                for item in await self.client.search_aweme(
                    keyword,
                    limit=min(self.per_source_limit, max(1, limit)),
                ):
                    raw_items.append((search_source_strategy, item, keyword_id))

        if "hot" in self.sources:
            hot_limit = min(self.per_source_limit, max(1, limit))
            hot_source_strategy = str(
                getattr(self.client, "hot_source_strategy", "dy-direct-hot") or "dy-direct-hot"
            )
            for item in await self.client.get_hot_board(limit=hot_limit):
                raw_items.append((hot_source_strategy, item, None))

        if "feed" in self.sources:
            feed_limit = min(self.per_source_limit, max(1, limit))
            feed_source_strategy = str(
                getattr(self.client, "feed_source_strategy", "dy-direct-feed") or "dy-direct-feed"
            )
            for item in await self.client.get_recommend_feed(limit=feed_limit):
                raw_items.append((feed_source_strategy, item, None))

        if "creator" in self.sources:
            for sec_uid in self.creator_sec_uids:
                for item in await self.client.get_creator_posts(
                    sec_uid,
                    limit=min(self.per_source_limit, max(1, limit)),
                ):
                    raw_items.append(("dy-direct-creator", item, None))

        candidates = self._normalize_and_dedupe(raw_items)
        if not candidates:
            return []

        if (
            not self.llm_evaluation
            or discovery_raw_candidate_mode_enabled()
            or self.llm_service is None
        ):
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

    async def _keywords(self, profile: SoulProfile) -> list[str]:
        # Explicit recipe keywords win — no need to synthesize.
        seeds = self._dedupe_cap([str(k).strip() for k in self.seed_keywords])
        if seeds:
            return seeds
        # LLM keyword generation, aligned with B站 / 小红书 / X: feed the same
        # build_profile_summary dict and rewrite into Douyin-native search terms.
        llm_keywords = await self._generate_keywords_llm(profile)
        if llm_keywords:
            return llm_keywords
        # Fallback (no llm_service wired / call failed / empty): raw interest
        # names — the original deterministic behavior, so Douyin keeps
        # discovering even when no LLM is injected.
        return self._dedupe_cap([str(i.name).strip() for i in profile.preferences.interests])

    def _dedupe_cap(self, candidates: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for keyword in candidates:
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            deduped.append(keyword)
            if len(deduped) >= self.keywords_per_run:
                break
        return deduped

    async def _generate_keywords_llm(self, profile: SoulProfile) -> list[str]:
        if self.llm_service is None or not profile.preferences.interests:
            return []
        try:
            complete_structured = self.llm_service.complete_structured_task
            response = await complete_structured(
                system_instruction=_DOUYIN_KEYWORDS_SYSTEM_PROMPT,
                user_input=_build_douyin_keyword_user_prompt(profile, self.keywords_per_run),
                temperature=0.8,
                max_tokens=512,
                caller="discovery.douyin.keyword_gen",
                **without_core_memory_kwargs(complete_structured),
            )
        except Exception as exc:  # noqa: BLE001 - degrade to deterministic fallback
            logger.warning("douyin keyword LLM call failed: %s", exc)
            return []
        content = getattr(response, "content", response)
        return _parse_douyin_keywords(str(content).strip(), count=self.keywords_per_run)

    @staticmethod
    def _normalize_and_dedupe(
        raw_items: list[tuple[str, dict[str, object], int | None]],
    ) -> list[DiscoveredContent]:
        seen: set[str] = set()
        normalized: list[DiscoveredContent] = []
        for source_strategy, item, source_keyword_id in raw_items:
            content = normalize_aweme_item(item, source_strategy=source_strategy)
            if content is None:
                continue
            key = content.content_id or content.bvid
            if key in seen:
                continue
            # P1.8 yield provenance — search items carry the producing keyword's
            # id; hot/feed/creator pass None (attribution-free).
            if source_keyword_id is not None:
                content.source_keyword_id = source_keyword_id
            seen.add(key)
            normalized.append(content)
        return normalized
