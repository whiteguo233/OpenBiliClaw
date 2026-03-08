"""User Soul Engine — the heart of OpenBiliClaw.

Transforms raw behavioral data into deep, layered understanding of a person.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMProvider
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.memory.manager import MemoryManager

from .awareness_analyzer import AwarenessAnalyzer
from .insight_analyzer import InsightAnalyzer
from .preference_analyzer import PreferenceAnalyzer
from .profile import (
    AwarenessNote,
    InsightHypothesis,
    SoulProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
    preference_layer_from_dict,
)
from .profile_builder import ProfileBuilder

logger = logging.getLogger(__name__)


class SoulProfileNotInitializedError(Exception):
    """Raised when the soul layer has not been initialized yet."""


class SoulEngine:
    """Engine for building and maintaining deep user understanding.

    The Soul Engine orchestrates the transformation of raw behavioral data
    through the five-layer memory architecture:
      Event → Preference → Awareness → Insight → Soul

    It is responsible for:
    1. Analyzing new behavioral events
    2. Updating preference patterns
    3. Writing daily awareness notes
    4. Generating insight hypotheses
    5. Maintaining the soul-level personality portrait
    """

    def __init__(self, llm: LLMProvider, memory: MemoryManager) -> None:
        from openbiliclaw.llm.service import LLMService

        self._llm = llm
        self._memory = memory
        self._llm_service: LLMService = LLMService(registry=llm, memory=memory)
        self._awareness_analyzer = AwarenessAnalyzer(self._llm_service)
        self._insight_analyzer = InsightAnalyzer(self._llm_service)
        self._preference_analyzer = PreferenceAnalyzer(self._llm_service)
        self._profile_builder = ProfileBuilder(self._llm_service)

    async def analyze_events(self, events: list[dict[str, Any]]) -> None:
        """Analyze new behavioral events and update all memory layers.

        This is the primary entry point for processing new user behavior.
        Events flow upward through the memory layers, with each layer
        potentially triggering updates in the layers above.

        Args:
            events: List of behavioral event dicts from the collector.
        """
        logger.info("Analyzing %d new events...", len(events))
        preference_layer = self._memory.get_layer("preference")
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=preference_layer.data,
        )
        preference_layer.data.clear()
        preference_layer.data.update(updated_preference)
        preference_layer.save()

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> SoulProfile:
        """Build an initial soul profile from historical data.

        Used on first run to bootstrap the user understanding model
        from existing Bilibili watch history, favorites, etc.

        Args:
            history: Historical data from Bilibili API.

        Returns:
            Initial SoulProfile.
        """
        logger.info("Building initial soul profile from %d history items...", len(history))
        preference_layer = self._memory.get_layer("preference").data
        profile = await self._profile_builder.build(
            history=history,
            preference=preference_layer,
        )
        profile.preferences = preference_layer_from_dict(preference_layer)
        soul_layer = self._memory.get_layer("soul")
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        return profile

    async def get_profile(self) -> SoulProfile:
        """Get the current soul profile.

        Returns:
            Current SoulProfile from the soul memory layer.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        return SoulProfile.from_dict(soul_data)

    async def update_from_feedback(self, feedback: dict[str, Any]) -> None:
        """Update soul understanding based on explicit user feedback.

        This can trigger updates across all memory layers, depending
        on the significance of the feedback.

        Args:
            feedback: User feedback data.
        """
        logger.info("Updating soul from feedback...")
        await self._memory.propagate_event(
            {
                "event_type": "feedback",
                "title": str(feedback.get("hypothesis", "")),
                "metadata": feedback,
            }
        )
        hypotheses = self._load_insights()
        target = self._normalize_text(str(feedback.get("hypothesis", "")))
        signal = str(feedback.get("signal", "")).strip().lower()
        updated = False
        for item in hypotheses:
            if self._normalize_text(item.hypothesis) != target:
                continue
            if signal in {"confirm", "like", "support"}:
                item.validated = True
                item.confidence = min(1.0, round(max(item.confidence, 0.75), 4))
            elif signal in {"reject", "dislike", "deny"}:
                item.validated = False
                item.confidence = max(0.0, round(min(item.confidence, 0.35), 4))
            updated = True
            break
        if updated:
            self._save_insights(hypotheses)

    async def process_feedback_batch_if_needed(self) -> dict[str, object]:
        """Reanalyze preference/profile after enough new feedback has accumulated."""
        state = self._memory.load_feedback_state()
        last_processed_id = self._to_int(state.get("last_processed_feedback_event_id", 0))
        feedback_events = [
            self._deserialize_event(event)
            for event in self._memory.query_events(event_types=["feedback"], limit=500)
            if int(event.get("id", 0) or 0) > last_processed_id
        ]
        feedback_events.sort(key=lambda item: int(item.get("id", 0) or 0))
        feedback_count = len(feedback_events)
        if feedback_count < 3:
            return {
                "triggered": False,
                "feedback_count": feedback_count,
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=feedback_events,
            existing_preference=existing_preference,
        )
        preference_layer.data.clear()
        preference_layer.data.update(updated_preference)
        preference_layer.save()

        profile_rebuilt = False
        if self._preference_changed_significantly(existing_preference, updated_preference):
            try:
                profile = await self._profile_builder.build(
                    history=[],
                    preference=updated_preference,
                )
                profile.preferences = preference_layer_from_dict(updated_preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                profile_rebuilt = True
            except Exception:
                logger.exception("Failed to rebuild soul profile after feedback refresh.")

        self._memory.save_feedback_state(
            {
                "last_processed_feedback_event_id": self._to_int(
                    feedback_events[-1].get("id", 0)
                ),
                "last_feedback_reanalyzed_at": datetime.now().isoformat(),
            }
        )
        return {
            "triggered": True,
            "feedback_count": feedback_count,
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

    async def generate_awareness_note(self) -> str:
        """Generate a daily awareness note.

        The awareness note captures what the agent has observed about
        the user's recent behavior patterns, mood changes, and interest shifts.

        Returns:
            Natural language awareness note.
        """
        events = self._memory.query_events(limit=50)
        notes = await self._awareness_analyzer.analyze(
            events=events,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not notes:
            return ""
        merged = self._awareness_analyzer.merge_notes(self._load_awareness_notes(), notes)
        self._save_awareness_notes(merged)
        return notes[0].observation

    async def generate_insight(self) -> str:
        """Generate or update insight hypotheses.

        Insights are deeper interpretations of user behavior:
        - Why they do what they do
        - What psychological needs are being met
        - What latent interests might exist

        Returns:
            Natural language insight.
        """
        awareness_notes = self._load_awareness_notes()
        insights = await self._insight_analyzer.analyze(
            awareness_notes=awareness_notes,
            preference=self._memory.get_layer("preference").data,
            soul_profile=self._memory.get_layer("soul").data,
        )
        if not insights:
            return ""
        merged = self._insight_analyzer.merge_insights(self._load_insights(), insights)
        self._save_insights(merged)
        return insights[0].hypothesis

    def _load_awareness_notes(self) -> list[AwarenessNote]:
        layer_data = self._memory.get_layer("awareness").data
        notes = layer_data.get("notes", [])
        return [awareness_note_from_dict(item) for item in notes if isinstance(item, dict)]

    def _save_awareness_notes(self, notes: list[AwarenessNote]) -> None:
        layer = self._memory.get_layer("awareness")
        layer.data.clear()
        layer.data.update({"notes": [awareness_note_to_dict(item) for item in notes]})
        layer.save()

    def _load_insights(self) -> list[InsightHypothesis]:
        layer_data = self._memory.get_layer("insight").data
        hypotheses = layer_data.get("hypotheses", [])
        return [
            insight_hypothesis_from_dict(item)
            for item in hypotheses
            if isinstance(item, dict)
        ]

    def _save_insights(self, insights: list[InsightHypothesis]) -> None:
        layer = self._memory.get_layer("insight")
        layer.data.clear()
        layer.data.update({"hypotheses": [insight_hypothesis_to_dict(item) for item in insights]})
        layer.save()

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())

    @staticmethod
    def _deserialize_event(event: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(event)
        for key in ("context", "metadata"):
            raw_value = normalized.get(key)
            if isinstance(raw_value, str):
                try:
                    parsed = json.loads(raw_value)
                except json.JSONDecodeError:
                    parsed = {}
                normalized[key] = parsed if isinstance(parsed, dict) else {}
        return normalized

    @staticmethod
    def _preference_changed_significantly(
        old_preference: dict[str, Any],
        new_preference: dict[str, Any],
    ) -> bool:
        def high_weight_interests(source: dict[str, Any]) -> dict[tuple[str, str], float]:
            items = source.get("interests", [])
            if not isinstance(items, list):
                return {}
            result: dict[tuple[str, str], float] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                weight = float(item.get("weight", 0.0) or 0.0)
                if weight < 0.6:
                    continue
                key = (str(item.get("name", "")).strip(), str(item.get("category", "")).strip())
                result[key] = weight
            return result

        old_interests = high_weight_interests(old_preference)
        new_interests = high_weight_interests(new_preference)
        changed_keys = set(old_interests) ^ set(new_interests)
        if len(changed_keys) >= 2:
            return True
        for key in set(old_interests) & set(new_interests):
            if abs(old_interests[key] - new_interests[key]) >= 0.2:
                return True
        old_disliked = {
            str(item).strip()
            for item in old_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        new_disliked = {
            str(item).strip()
            for item in new_preference.get("disliked_topics", [])
            if str(item).strip()
        }
        return len(new_disliked - old_disliked) >= 1

    @staticmethod
    def _to_int(raw_value: object) -> int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            try:
                return int(raw_value)
            except ValueError:
                return 0
        return 0
