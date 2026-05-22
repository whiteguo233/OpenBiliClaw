"""Trending/ranking content discovery strategy."""

from __future__ import annotations

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
    trim_candidates_for_llm,
)
from openbiliclaw.discovery.strategies._utils import (
    SupportsRankingClient,
    _gather_bounded,
    build_profile_summary,
    clean_text,
    parse_duration,
    to_int,
)

if TYPE_CHECKING:
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class TrendingStrategy(DiscoveryStrategy):
    """Discover content from trending/ranking pages."""

    bilibili_client: SupportsRankingClient
    llm_service: SupportsStructuredTask
    concurrency: DiscoveryConcurrencyController | None = None
    database: Database | None = None
    score_threshold: float = 0.70
    max_related_rids: int = 4
    # Broader default RIDs covering more top-level categories:
    # 36=科技, 188=资讯, 181=影视, 119=纪录片, 3=音乐, 129=舞蹈, 4=游戏, 160=生活
    default_rids: tuple[int, ...] = (36, 188, 181, 119, 3, 129, 4, 160)
    # Mapping from Bilibili ranking rid to semantic topic category
    RID_TO_TOPIC: dict[int, str] = field(
        default_factory=lambda: {
            0: "综合热门",
            1: "动画",
            3: "音乐",
            4: "游戏",
            5: "娱乐",
            11: "电视剧",
            13: "番剧",
            17: "单机游戏",
            23: "电影",
            36: "科技",
            119: "纪录片",
            129: "舞蹈",
            155: "时尚",
            160: "生活",
            167: "国创",
            177: "纪录片",
            181: "影视",
            188: "资讯",
            211: "美食",
            217: "动物",
            218: "运动",
            223: "汽车",
            234: "运动",
        }
    )
    last_intermediates: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return "trending"

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        if self.score_threshold <= 0.58:
            return None
        return replace(
            self,
            score_threshold=max(0.58, round(self.score_threshold - 0.07, 2)),
            last_intermediates={},
        )

    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Scan trending and ranking content, filter by soul relevance.

        Args:
            profile: User soul profile.
            limit: Maximum results.

        Returns:
            Discovered content list.
        """
        evaluator = ContentDiscoveryEngine(
            llm_service=self.llm_service,
            database=self.database,
            concurrency=self.concurrency,
        )
        rids = await self._select_rids(profile)
        self.last_intermediates = {"rids": list(rids)}
        runner = self.concurrency.run_bilibili if self.concurrency is not None else None
        ranking_outcomes = await _gather_bounded(
            [self.bilibili_client.get_ranking(rid) for rid in rids],
            runner=runner,
        )
        per_rid: list[list[DiscoveredContent]] = []
        seen_bvids: set[str] = set()

        for rid, outcome in zip(rids, ranking_outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.error(
                    "Trending ranking request failed: rid=%s",
                    rid,
                    exc_info=outcome,
                    extra={
                        "strategy": "trending",
                        "rid": rid,
                        "error_type": type(outcome).__name__,
                    },
                )
                per_rid.append([])
                continue
            if not isinstance(outcome, list):
                per_rid.append([])
                continue
            bucket: list[DiscoveredContent] = []
            for item in outcome:
                content = self._map_ranking_item(item, rid=rid)
                if content is None or content.bvid in seen_bvids:
                    continue
                seen_bvids.add(content.bvid)
                bucket.append(content)
            per_rid.append(bucket)

        # Round-robin interleave so the downstream eval hard-cap (30) gives
        # each rid roughly equal representation. Without this, the rid=0
        # bucket (always first) consumes the entire eval window when it has
        # 100+ ranking entries, leaving the other 4 rids unevaluated.
        candidates: list[DiscoveredContent] = []
        max_depth = max((len(bucket) for bucket in per_rid), default=0)
        for depth in range(max_depth):
            for bucket in per_rid:
                if depth < len(bucket):
                    candidates.append(bucket[depth])
        candidates = trim_candidates_for_llm(
            candidates,
            limit=limit,
            source_context=self.name,
        )

        scores = await evaluator.evaluate_content_batch(candidates, profile)
        results: list[DiscoveredContent] = []
        for content, score in zip(candidates, scores, strict=True):
            if score < self.score_threshold:
                continue
            results.append(content)
            if len(results) >= limit:
                return results

        return results

    async def _select_rids(self, profile: SoulProfile) -> list[int]:
        from openbiliclaw.llm.prompts import build_trending_rids_prompt

        messages = build_trending_rids_prompt(profile_summary=build_profile_summary(profile))
        try:
            response = await self.llm_service.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
                caller="discovery.trending.rids",
            )
            parsed = json.loads(str(getattr(response, "content", "")).strip())
            if isinstance(parsed, dict) and isinstance(parsed.get("rids"), list):
                selected = [to_int(item) for item in parsed["rids"] if to_int(item) > 0]
                selected = self._dedupe_ints(selected)[: self.max_related_rids]
                return [0, *selected]
        except Exception:
            logger.exception("Trending rid selection failed; using defaults.")
        return [0, *list(self.default_rids[: self.max_related_rids])]

    def _map_ranking_item(
        self,
        item: dict[str, object],
        *,
        rid: int = 0,
    ) -> DiscoveredContent | None:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid:
            return None
        owner = item.get("owner")
        up_name = str(item.get("author", "")).strip()
        up_mid = to_int(item.get("mid", 0))
        if isinstance(owner, dict):
            up_name = str(owner.get("name", up_name)).strip()
            up_mid = to_int(owner.get("mid", up_mid))
        stat = item.get("stat")
        view_count = to_int(item.get("play", 0))
        like_count = to_int(item.get("like", 0))
        if isinstance(stat, dict):
            view_count = to_int(stat.get("view", view_count))
            like_count = to_int(stat.get("like", like_count))

        title = clean_text(str(item.get("title", "")))
        description = clean_text(str(item.get("description", item.get("desc", ""))))
        # Prefer item's tname (B站分区名), then RID mapping, then tag/title fallback
        tname = str(item.get("tname", "")).strip()
        if tname:
            topic_key = re.sub(r"\s+", "", tname).lower()[:16]
        elif rid in self.RID_TO_TOPIC:
            topic_key = re.sub(r"\s+", "", self.RID_TO_TOPIC[rid]).lower()[:16]
        else:
            topic_key = self._infer_topic_key(item, title)

        return DiscoveredContent(
            bvid=bvid,
            title=title,
            up_name=clean_text(up_name),
            up_mid=up_mid,
            cover_url=str(item.get("pic", "")),
            duration=parse_duration(item.get("duration", 0)),
            view_count=view_count,
            like_count=like_count,
            description=description,
            topic_key=topic_key,
            topic_group=topic_key,
            style_key=ContentDiscoveryEngine.infer_style_key(
                title=title,
                description=description,
                source_strategy=self.name,
            ),
            source_strategy=self.name,
        )

    @staticmethod
    def _infer_topic_key(item: dict[str, object], title: str) -> str:
        """Infer topic_key from tags or title for ranking items."""
        tags = item.get("tags", [])
        if isinstance(tags, list) and tags:
            first_tag = str(tags[0]).strip()
            if first_tag:
                return re.sub(r"\s+", "", first_tag).lower()[:32]
        # Fallback: first meaningful segment of title
        cleaned = re.sub(r"[【】\[\]《》「」\s]+", " ", title).strip()
        parts = cleaned.split()
        if parts:
            return re.sub(r"\s+", "", parts[0]).lower()[:32]
        return ""

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
