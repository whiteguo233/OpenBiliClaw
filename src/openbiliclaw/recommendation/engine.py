"""Recommendation Engine — ranking, expression, and delivery.

Handles the final stage: taking discovered content and presenting it
to the user in a warm, friend-like manner with deep personal insights.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
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
        ranked = sorted(
            candidates,
            key=lambda item: (-item.relevance_score, -item.view_count, item.bvid),
        )[:limit]

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
    def _fallback_expression(content: DiscoveredContent) -> str:
        title = content.title or "这条内容"
        return f"我感觉《{title}》会比较对你胃口，它应该能接住你最近那股想继续往深处看的状态。"

    @staticmethod
    def _fallback_topic_label(profile: SoulProfile) -> str:
        if profile.core_traits:
            return f"你最近那股偏{profile.core_traits[0]}的状态"
        return "想先丢给你的一条"

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
                source_strategy=str(row.get("source", "")),
            )
            for row in rows
        ]
