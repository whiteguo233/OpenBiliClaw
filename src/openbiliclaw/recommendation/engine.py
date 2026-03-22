"""Recommendation Engine — ranking, expression, and delivery.

Handles the final stage: taking discovered content and presenting it
to the user in a warm, friend-like manner with deep personal insights.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.soul.tone import build_tone_profile

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.llm.base import LLMResponse
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)


class SupportsCoreMemoryTask(Protocol):
    """Protocol for a core-memory-aware structured LLM task executor."""

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...


@dataclass
class Recommendation:
    """A recommendation ready to present to the user."""

    content: DiscoveredContent
    recommendation_id: int = 0
    expression: str = ""  # Friend-style recommendation reason
    topic_label: str = ""  # Personal topic (not generic categories)
    confidence: float = 0.0  # How confident the agent is in this rec
    presented: bool = False
    feedback: str | None = None  # User feedback after seeing it


@dataclass
class PersonalTopic:
    """A deeply personalized recommendation topic.

    Not generic labels like "Weekend Pack" but personal ones like:
    "你最近在探索摄影——这几个视频从你习惯的'搞明白原理'的角度讲构图"
    """

    title: str = ""
    description: str = ""
    recommendations: list[Recommendation] = field(default_factory=list)


class RecommendationEngine:
    """Produces warm, personalized recommendations.

    The engine takes discovered content and transforms it into
    friend-style recommendations with:
    - "我觉得" — subjective, personal judgment
    - "我理解你" — demonstrates deep understanding
    - Personal insights connecting content to the user's soul
    """

    def __init__(self, llm: SupportsCoreMemoryTask, database: Database) -> None:
        self._llm = llm
        self._database = database

    async def generate_recommendations(
        self,
        discovered: list[DiscoveredContent] | None,
        profile: SoulProfile,
        limit: int = 10,
    ) -> list[Recommendation]:
        """Generate friend-style recommendations from discovered content.

        Args:
            discovered: Content discovered by the discovery engine.
            profile: User's soul profile for personalization.
            limit: Maximum number of recommendations.

        Returns:
            List of personalized recommendations.
        """
        candidates = (
            self._normalize_discovered(discovered)
            if discovered is not None
            else self._load_unrecommended_content(limit=max(limit * 3, 20))
        )
        candidates = self._exclude_recently_viewed(candidates)
        logger.info(
            "Recommendation candidate summary (generate): %s",
            json.dumps(self._build_debug_summary(candidates), ensure_ascii=False),
        )
        ranked = self._select_diversified_batch(candidates, limit=limit)
        logger.info(
            "Recommendation picked summary (generate): %s",
            json.dumps(self._build_debug_summary(ranked), ensure_ascii=False),
        )

        recommendations = [
            Recommendation(
                content=item,
                confidence=item.relevance_score,
                presented=False,
            )
            for item in ranked
        ]
        for item in recommendations:
            item.recommendation_id = self._database.insert_recommendation(
                item.content.bvid,
                confidence=item.confidence,
                expression=item.expression,
                topic=item.topic_label,
                presented=0,
            )
            item.expression, item.topic_label = await self.generate_expression(
                item.content,
                profile,
            )
            self._database.update_recommendation_content(
                item.recommendation_id,
                expression=item.expression,
                topic=item.topic_label,
            )
        return recommendations

    async def reshuffle_recommendations(
        self,
        *,
        profile: SoulProfile,
        limit: int = 5,
    ) -> list[Recommendation]:
        """Instantly pick a new batch from the discovery pool.

        This path is intentionally fast: it does not wait for friend-style
        expression generation and falls back to pool relevance reasons.
        """
        candidates = self._load_pool_candidates(limit=max(limit * 3, 20))
        candidates = self._exclude_recently_viewed(candidates)
        logger.info(
            "Recommendation candidate summary (reshuffle): %s",
            json.dumps(self._build_debug_summary(candidates), ensure_ascii=False),
        )
        ranked = self._select_diversified_batch(candidates, limit=limit)
        logger.info(
            "Recommendation picked summary (reshuffle): %s",
            json.dumps(self._build_debug_summary(ranked), ensure_ascii=False),
        )
        recommendations: list[Recommendation] = []
        shown_bvids: list[str] = []
        topic_label = self._fallback_topic_label(profile)

        for item in ranked:
            expression = item.relevance_reason.strip() or self._fallback_expression(item)
            recommendation = Recommendation(
                content=item,
                confidence=item.relevance_score,
                presented=False,
                expression=expression,
                topic_label=topic_label,
            )
            recommendation.recommendation_id = self._database.insert_recommendation(
                item.bvid,
                confidence=recommendation.confidence,
                expression=recommendation.expression,
                topic=recommendation.topic_label,
                presented=0,
            )
            recommendations.append(recommendation)
            shown_bvids.append(item.bvid)

        self._database.mark_pool_items_shown(shown_bvids)
        return recommendations

    async def append_recommendations(
        self,
        *,
        profile: SoulProfile,
        excluded_bvids: list[str],
        limit: int = 10,
    ) -> list[Recommendation]:
        """Append another page of recommendations from the discovery pool."""
        excluded = {item.strip() for item in excluded_bvids if item and item.strip()}
        candidates = self._load_pool_candidates(limit=max(limit * 4, 40))
        candidates = [item for item in candidates if item.bvid not in excluded]
        candidates = self._exclude_recently_viewed(candidates)
        logger.info(
            "Recommendation candidate summary (append): %s",
            json.dumps(self._build_debug_summary(candidates), ensure_ascii=False),
        )
        ranked = self._select_diversified_batch(candidates, limit=limit)
        logger.info(
            "Recommendation picked summary (append): %s",
            json.dumps(self._build_debug_summary(ranked), ensure_ascii=False),
        )

        recommendations: list[Recommendation] = []
        shown_bvids: list[str] = []
        topic_label = self._fallback_topic_label(profile)
        for item in ranked:
            expression = item.relevance_reason.strip() or self._fallback_expression(item)
            recommendation = Recommendation(
                content=item,
                confidence=item.relevance_score,
                presented=False,
                expression=expression,
                topic_label=topic_label,
            )
            recommendation.recommendation_id = self._database.insert_recommendation(
                item.bvid,
                confidence=recommendation.confidence,
                expression=recommendation.expression,
                topic=recommendation.topic_label,
                presented=0,
            )
            recommendations.append(recommendation)
            shown_bvids.append(item.bvid)

        self._database.mark_pool_items_shown(shown_bvids)
        return recommendations

    async def generate_personal_topic(
        self,
        recommendations: list[Recommendation],
        profile: SoulProfile,
    ) -> PersonalTopic:
        """Create a deeply personalized recommendation topic.

        The topic is unique to this user — not "周末放松包" but something
        that connects to their specific personality and current state.

        Args:
            recommendations: Recommendations to group into a topic.
            profile: User's soul profile.

        Returns:
            A PersonalTopic with a custom title and description.
        """
        # TODO: Use LLM to create a personal topic narrative
        return PersonalTopic()

    async def generate_expression(
        self,
        content: DiscoveredContent,
        profile: SoulProfile,
    ) -> tuple[str, str]:
        """Generate a friend-style recommendation expression.

        The expression should feel like a close friend recommending something:
        warm, insightful, personal, with genuine understanding of why this
        specific person would enjoy this specific content.

        Args:
            content: The content being recommended.
            profile: User's soul profile.

        Returns:
            Expression text and a lightly personalized topic label.
        """
        from openbiliclaw.llm.prompts import build_recommendation_expression_prompt

        tone_profile = build_tone_profile(
            profile=profile,
            preference_summary={
                "exploration_openness": profile.preferences.exploration_openness,
            },
            recent_feedback=[],
        )
        messages = build_recommendation_expression_prompt(
            profile_summary={
                "personality_portrait": profile.personality_portrait,
                "core_traits": profile.core_traits[:5],
                "deep_needs": profile.deep_needs[:5],
                "interests": [
                    {
                        "name": item.name,
                        "category": item.category,
                        "weight": item.weight,
                    }
                    for item in profile.preferences.interests[:10]
                ],
            },
            content_summary={
                "title": content.title,
                "up_name": content.up_name,
                "description": content.description,
                "source_strategy": content.source_strategy,
                "relevance_score": content.relevance_score,
            },
            tone_profile=tone_profile,
        )
        try:
            response = await self._llm.complete_structured_task(
                system_instruction=messages[0]["content"],
                user_input=messages[1]["content"],
            )
            payload = json.loads(response.content.strip())
            if not isinstance(payload, dict):
                raise ValueError("Expression response must be a JSON object.")
            expression = str(payload.get("expression", "")).strip()
            topic_label = str(payload.get("topic_label", "")).strip()
            if expression and topic_label:
                return expression, topic_label
        except Exception:
            logger.exception("Failed to generate recommendation expression: %s", content.bvid)
        return self._fallback_expression(content), self._fallback_topic_label(profile)

    def mark_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendation rows as presented."""
        ids = [item for item in recommendation_ids if item > 0]
        if not ids:
            return
        self._database.mark_recommendations_presented(ids)

    async def record_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        note: str = "",
    ) -> None:
        """Persist explicit user feedback for a recommendation."""
        self._database.update_recommendation_feedback(
            recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )

    def get_recommendation(self, recommendation_id: int) -> dict[str, object] | None:
        """Load a recommendation row for CLI or feedback workflows."""
        return self._database.get_recommendation_by_id(recommendation_id)

    @staticmethod
    def _normalize_discovered(
        discovered: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        return list(discovered)

    @staticmethod
    def _ranking_key(item: DiscoveredContent) -> tuple[int, float, float, int, str]:
        return (
            0 if item.candidate_tier == "primary" else 1,
            -item.relevance_score,
            -RecommendationEngine._timestamp_score(item.last_scored_at or item.discovered_at),
            -item.view_count,
            item.bvid,
        )

    @staticmethod
    def _timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace(" ", "T")).timestamp()
        except ValueError:
            return 0.0

    @staticmethod
    def _fallback_expression(content: DiscoveredContent) -> str:
        title = content.title or "这条内容"
        style_key = content.style_key.strip()
        if style_key == "game_strategy":
            return f"《{title}》偏你会点开的那种机制/攻略向，不只是热闹，重点是真有东西能翻。"
        if style_key == "news_brief":
            return f"《{title}》这条胜在信息来得快，而且不是纯复读，适合你先抓重点。"
        if style_key == "practical_guide":
            return f"《{title}》偏实操一点，信息是能直接拿来用的，不会只有概念。"
        if style_key == "story_doc":
            return f"《{title}》这条没那么硬，但会把故事和信息一起带出来，适合你换口气的时候看。"
        if style_key == "visual_showcase":
            return f"《{title}》更偏轻一点，适合你换换脑子，但内容不空。"
        if style_key == "deep_dive":
            return f"《{title}》还是你常吃那一路，偏讲透来龙去脉，不会只给结论。"
        return (
            f"《{title}》这条大概率还是对你胃口，重点是它不空，"
            "能接住你最近那股想继续往深处看的状态。"
        )

    @staticmethod
    def _fallback_topic_label(profile: SoulProfile) -> str:
        if profile.core_traits:
            return f"你最近那股偏{profile.core_traits[0]}的状态"
        return "想先丢给你的一条"

    @classmethod
    def _select_diversified_batch(
        cls,
        candidates: list[DiscoveredContent],
        *,
        limit: int,
    ) -> list[DiscoveredContent]:
        ranked = sorted(candidates, key=cls._ranking_key)
        if limit <= 1 or len(ranked) <= 1:
            return ranked[:limit]

        per_topic_cap = cls._topic_cap(limit)
        soft_topic_cap = cls._soft_topic_cap(limit)
        per_style_cap = cls._style_cap(limit)
        selected: list[DiscoveredContent] = []
        deferred: list[DiscoveredContent] = []
        topic_counts: dict[str, int] = {}
        style_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        seen_sources: set[str] = set()
        per_source_cap = cls._source_cap(limit)
        unique_source_target = min(
            limit,
            len(
                {
                    cls._normalize_topic_token(item.source_strategy)
                    for item in ranked
                    if cls._normalize_topic_token(item.source_strategy)
                }
            ),
        )

        for item in ranked:
            tokens = cls._diversity_tokens(item)
            style_token = cls._style_token(item)
            source_token = cls._normalize_topic_token(item.source_strategy)
            prioritize_new_source = (
                bool(source_token)
                and source_token not in seen_sources
                and len(seen_sources) < unique_source_target
            )
            if tokens and any(topic_counts.get(token, 0) >= per_topic_cap for token in tokens):
                deferred.append(item)
                continue
            if (
                not prioritize_new_source
                and style_token
                and style_counts.get(style_token, 0) >= per_style_cap
            ):
                deferred.append(item)
                continue
            if source_token and source_counts.get(source_token, 0) >= per_source_cap:
                deferred.append(item)
                continue
            if not prioritize_new_source and source_token and source_token in seen_sources:
                deferred.append(item)
                continue
            selected.append(item)
            for token in tokens:
                topic_counts[token] = topic_counts.get(token, 0) + 1
            if style_token:
                style_counts[style_token] = style_counts.get(style_token, 0) + 1
            if source_token:
                seen_sources.add(source_token)
                source_counts[source_token] = source_counts.get(source_token, 0) + 1
            if len(selected) >= limit:
                return selected

        def try_fill(
            pool: list[DiscoveredContent],
            *,
            topic_cap: int,
            enforce_style_cap: bool,
            enforce_source_cap: bool,
        ) -> list[DiscoveredContent]:
            remaining: list[DiscoveredContent] = []
            for item in pool:
                tokens = cls._diversity_tokens(item)
                style_token = cls._style_token(item)
                source_token = cls._normalize_topic_token(item.source_strategy)
                if tokens and any(topic_counts.get(token, 0) >= topic_cap for token in tokens):
                    remaining.append(item)
                    continue
                if (
                    enforce_style_cap
                    and style_token
                    and style_counts.get(style_token, 0) >= per_style_cap
                ):
                    remaining.append(item)
                    continue
                if (
                    enforce_source_cap
                    and source_token
                    and source_counts.get(source_token, 0) >= per_source_cap
                ):
                    remaining.append(item)
                    continue
                selected.append(item)
                for token in tokens:
                    topic_counts[token] = topic_counts.get(token, 0) + 1
                if style_token:
                    style_counts[style_token] = style_counts.get(style_token, 0) + 1
                if source_token:
                    source_counts[source_token] = source_counts.get(source_token, 0) + 1
                if len(selected) >= limit:
                    return []
            return remaining

        remaining = try_fill(
            deferred,
            topic_cap=per_topic_cap,
            enforce_style_cap=True,
            enforce_source_cap=True,
        )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=per_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=True,
            )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=per_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=False,
            )
        if len(selected) < limit:
            remaining = try_fill(
                remaining,
                topic_cap=soft_topic_cap,
                enforce_style_cap=False,
                enforce_source_cap=False,
            )
        if len(selected) < limit:
            for item in remaining:
                selected.append(item)
                if len(selected) >= limit:
                    break
        return selected[:limit]

    @staticmethod
    def _diversity_tokens(item: DiscoveredContent) -> set[str]:
        topic_key = RecommendationEngine._normalize_topic_token(item.topic_key)
        if topic_key:
            return {topic_key}

        tokens = {
            RecommendationEngine._normalize_topic_token(tag)
            for tag in item.tags
            if RecommendationEngine._normalize_topic_token(tag)
        }
        if tokens:
            return tokens

        fallback_fields = [item.source_strategy, item.up_name]
        title = item.title
        fallback_fields.extend(re.findall(r"[A-Za-z0-9]{2,}", title))
        return {
            RecommendationEngine._normalize_topic_token(value)
            for value in fallback_fields
            if RecommendationEngine._normalize_topic_token(value)
        }

    @staticmethod
    def _style_token(item: DiscoveredContent) -> str:
        return RecommendationEngine._normalize_topic_token(item.style_key)

    @staticmethod
    def _normalize_topic_token(value: str) -> str:
        text = value.strip().lower()
        if not text:
            return ""
        compact = re.sub(r"\s+", "", text)
        return compact[:24]

    @staticmethod
    def _topic_cap(limit: int) -> int:
        return 1 if limit <= 5 else 2

    @staticmethod
    def _soft_topic_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    @staticmethod
    def _style_cap(limit: int) -> int:
        return max(1, min(3, (limit + 1) // 3))

    @staticmethod
    def _source_cap(limit: int) -> int:
        return 2 if limit <= 5 else 3

    def _load_unrecommended_content(self, *, limit: int) -> list[DiscoveredContent]:
        from openbiliclaw.discovery.engine import DiscoveredContent

        rows = self._database.get_unrecommended_content(limit=limit)
        return [
            DiscoveredContent(
                bvid=str(row.get("bvid", "")),
                title=str(row.get("title", "")),
                up_name=str(row.get("up_name", "")),
                up_mid=int(row.get("up_mid", 0) or 0),
                duration=int(row.get("duration", 0) or 0),
                description=str(row.get("description", "")),
                cover_url=str(row.get("cover_url", "")),
                view_count=int(row.get("view_count", 0) or 0),
                like_count=int(row.get("like_count", 0) or 0),
                tags=self._parse_tags(row.get("tags", "[]")),
                topic_key=str(row.get("topic_key", "")),
                style_key=str(row.get("style_key", "")),
                source_strategy=str(row.get("source", "")),
                relevance_score=float(row.get("relevance_score", 0.0) or 0.0),
                relevance_reason=str(row.get("relevance_reason", "")),
                candidate_tier=str(row.get("candidate_tier", "primary") or "primary"),
                discovered_at=str(row.get("discovered_at", "")),
                last_scored_at=str(row.get("last_scored_at", "")),
            )
            for row in rows
        ]

    def _load_pool_candidates(self, *, limit: int) -> list[DiscoveredContent]:
        from openbiliclaw.discovery.engine import DiscoveredContent

        rows = self._database.get_pool_candidates(limit=limit)
        return [
            DiscoveredContent(
                bvid=str(row.get("bvid", "")),
                title=str(row.get("title", "")),
                up_name=str(row.get("up_name", "")),
                up_mid=int(row.get("up_mid", 0) or 0),
                duration=int(row.get("duration", 0) or 0),
                description=str(row.get("description", "")),
                cover_url=str(row.get("cover_url", "")),
                view_count=int(row.get("view_count", 0) or 0),
                like_count=int(row.get("like_count", 0) or 0),
                tags=self._parse_tags(row.get("tags", "[]")),
                topic_key=str(row.get("topic_key", "")),
                style_key=str(row.get("style_key", "")),
                source_strategy=str(row.get("source", "")),
                relevance_score=float(row.get("relevance_score", 0.0) or 0.0),
                relevance_reason=str(row.get("relevance_reason", "")),
                candidate_tier=str(row.get("candidate_tier", "primary") or "primary"),
                discovered_at=str(row.get("discovered_at", "")),
                last_scored_at=str(row.get("last_scored_at", "")),
            )
            for row in rows
        ]

    def _exclude_recently_viewed(
        self,
        candidates: list[DiscoveredContent],
    ) -> list[DiscoveredContent]:
        viewed_bvids = self._database.get_recent_viewed_bvids()
        if not viewed_bvids:
            return candidates
        return [item for item in candidates if item.bvid not in viewed_bvids]

    @staticmethod
    def _parse_tags(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [str(item).strip() for item in payload if str(item).strip()]

    @classmethod
    def _build_debug_summary(
        cls,
        candidates: list[DiscoveredContent],
    ) -> dict[str, object]:
        style_counts = Counter(
            cls._style_token(item) or "unknown" for item in candidates
        )
        source_counts = Counter(
            cls._normalize_topic_token(item.source_strategy) or "unknown"
            for item in candidates
        )
        topic_counts: Counter[str] = Counter()
        for item in candidates:
            tokens = cls._diversity_tokens(item)
            if not tokens:
                topic_counts["unknown"] += 1
                continue
            topic_counts[sorted(tokens)[0]] += 1
        return {
            "count": len(candidates),
            "styles": dict(style_counts.most_common(5)),
            "sources": dict(source_counts.most_common(5)),
            "topics": dict(topic_counts.most_common(5)),
            "sample_titles": [item.title for item in candidates[:5]],
        }
