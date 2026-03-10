"""User Soul Engine — the heart of OpenBiliClaw.

Transforms raw behavioral data into deep, layered understanding of a person.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from openbiliclaw.llm.base import LLMProvider
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.memory.manager import MemoryManager

from .awareness_analyzer import AwarenessAnalyzer
from .dialogue_insight_analyzer import (
    DialogueInsightAnalysisError,
    DialogueInsightAnalyzer,
)
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
        self._dialogue_insight_analyzer = DialogueInsightAnalyzer(self._llm_service)
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

    async def learn_from_dialogue(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        session: str,
    ) -> dict[str, object]:
        """Persist a chat turn and update long-term understanding when warranted."""
        await self._memory.propagate_event(
            {
                "event_type": "dialogue",
                "title": user_message[:60],
                "metadata": {
                    "user_message": user_message,
                    "assistant_reply": assistant_reply,
                    "source": "chat",
                    "session": session,
                },
            }
        )
        try:
            extracted = await self._dialogue_insight_analyzer.extract(
                user_message=user_message,
                assistant_reply=assistant_reply,
                core_memory=self._memory.get_core_memory(),
            )
        except DialogueInsightAnalysisError:
            logger.exception("Failed to extract dialogue insight candidates.")
            extracted = []

        merged_candidates = self._merge_insight_candidates(
            self._memory.load_insight_candidates(),
            extracted,
        )
        self._memory.save_insight_candidates(merged_candidates)
        eligible_candidates = [
            item for item in merged_candidates if self._candidate_ready_for_learning(item)
        ]
        if not eligible_candidates:
            return {
                "event_logged": True,
                "candidate_count": len(extracted),
                "preference_updated": False,
                "profile_rebuilt": False,
            }

        preference_layer = self._memory.get_layer("preference")
        existing_preference = dict(preference_layer.data)
        existing_profile = dict(self._memory.get_layer("soul").data)
        updated_preference = await self._preference_analyzer.analyze_events(
            events=[
                {
                    "event_type": "dialogue_insight",
                    "title": str(item.get("content", "")),
                    "metadata": {
                        "kind": item.get("kind", ""),
                        "confidence": item.get("confidence", 0.0),
                        "evidence": item.get("evidence", ""),
                        "source": "dialogue",
                        "occurrences": item.get("occurrences", 1),
                    },
                }
                for item in eligible_candidates
            ],
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
                logger.exception("Failed to rebuild soul profile after dialogue learning.")

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="chat",
        )

        for item in merged_candidates:
            if self._candidate_ready_for_learning(item):
                item["applied"] = True
                item["updated_at"] = datetime.now().isoformat()
        self._memory.save_insight_candidates(merged_candidates)

        return {
            "event_logged": True,
            "candidate_count": len(extracted),
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

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
        existing_profile = dict(self._memory.get_layer("soul").data)
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

        self._record_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=existing_profile,
            current_profile=dict(self._memory.get_layer("soul").data),
            source="feedback",
        )

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

    def _merge_insight_candidates(
        self,
        existing_candidates: list[dict[str, object]],
        new_candidates: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        merged = [dict(item) for item in existing_candidates if isinstance(item, dict)]
        for raw_candidate in new_candidates:
            kind = str(raw_candidate.get("kind", "")).strip() or "state"
            content = str(raw_candidate.get("content", "")).strip()
            if not content:
                continue
            normalized_content = self._normalize_text(content)
            existing = next(
                (
                    item
                    for item in merged
                    if self._normalize_text(str(item.get("content", ""))) == normalized_content
                    and str(item.get("kind", "")).strip() == kind
                ),
                None,
            )
            now = datetime.now().isoformat()
            confidence = self._to_float(raw_candidate.get("confidence", 0.0))
            evidence = str(raw_candidate.get("evidence", "")).strip()
            if existing is None:
                merged.append(
                    {
                        "id": str(uuid4()),
                        "kind": kind,
                        "content": content,
                        "confidence": max(0.0, min(1.0, round(confidence, 4))),
                        "evidence": evidence,
                        "occurrences": 1,
                        "confirmed": False,
                        "applied": False,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                continue
            existing["occurrences"] = self._to_int(existing.get("occurrences", 0)) + 1
            existing["confidence"] = max(
                self._to_float(existing.get("confidence", 0.0)),
                max(0.0, min(1.0, round(confidence, 4))),
            )
            if evidence:
                existing["evidence"] = evidence
            existing["updated_at"] = now
        return merged

    def _candidate_ready_for_learning(self, candidate: dict[str, object]) -> bool:
        if bool(candidate.get("applied", False)):
            return False
        confidence = self._to_float(candidate.get("confidence", 0.0))
        occurrences = self._to_int(candidate.get("occurrences", 0))
        kind = str(candidate.get("kind", "")).strip()
        if confidence >= 0.9 and kind in {"dislike", "goal"}:
            return True
        return confidence >= 0.8 and occurrences >= 2

    def _record_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> None:
        new_updates = self._build_cognition_updates(
            existing_preference=existing_preference,
            updated_preference=updated_preference,
            previous_profile=previous_profile,
            current_profile=current_profile,
            source=source,
        )
        if not new_updates:
            return
        updates = self._memory.load_cognition_updates()
        updates.extend(new_updates)
        self._memory.save_cognition_updates(updates)

    def _build_cognition_updates(
        self,
        *,
        existing_preference: dict[str, Any],
        updated_preference: dict[str, Any],
        previous_profile: dict[str, Any],
        current_profile: dict[str, Any],
        source: str,
    ) -> list[dict[str, object]]:
        now = datetime.now().isoformat()
        updates: list[dict[str, object]] = []

        existing_interests = {
            self._normalize_text(str(item.get("name", ""))): item
            for item in self._as_dict_list(existing_preference.get("interests", []))
            if str(item.get("name", "")).strip()
        }
        for item in self._as_dict_list(updated_preference.get("interests", [])):
            name = str(item.get("name", "")).strip()
            normalized_name = self._normalize_text(name)
            if not normalized_name or normalized_name in existing_interests:
                continue
            weight = self._to_float(item.get("weight", 0.0))
            if weight < 0.75:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "interest_added",
                    "summary": f"阿B 现在更确定你会吃“{name}”这一口。",
                    "confidence": round(weight, 4),
                    "created_at": now,
                    "source": source,
                    "notified": False,
                }
            )

        existing_dislikes = {
            self._normalize_text(item)
            for item in self._as_str_list(existing_preference.get("disliked_topics", []))
        }
        for topic in self._as_str_list(updated_preference.get("disliked_topics", [])):
            normalized_topic = self._normalize_text(topic)
            if not normalized_topic or normalized_topic in existing_dislikes:
                continue
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "dislike_added",
                    "summary": f"阿B 记住了：像“{topic}”这种内容你大概率会划走。",
                    "confidence": 0.86,
                    "created_at": now,
                    "source": source,
                    "notified": False,
                }
            )

        if self._profile_shifted(previous_profile, current_profile):
            portrait = str(current_profile.get("personality_portrait", "")).strip()
            summary = (
                portrait[:72].rstrip("，。！？,.!?") if portrait else "我对你又对上了一点。"
            )
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "profile_shift",
                    "summary": summary,
                    "confidence": 0.9,
                    "created_at": now,
                    "source": "profile_refresh",
                    "notified": False,
                }
            )

        return updates

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
        if not old_interests and new_interests:
            return True
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
    def _profile_shifted(previous_profile: dict[str, Any], current_profile: dict[str, Any]) -> bool:
        if not current_profile:
            return False
        if not previous_profile:
            return bool(
                SoulEngine._as_str_list(current_profile.get("core_traits", []))
                or SoulEngine._as_str_list(current_profile.get("deep_needs", []))
                or str(current_profile.get("personality_portrait", "")).strip()
            )
        previous_traits = set(SoulEngine._as_str_list(previous_profile.get("core_traits", [])))
        current_traits = set(SoulEngine._as_str_list(current_profile.get("core_traits", [])))
        if current_traits - previous_traits:
            return True
        previous_needs = set(SoulEngine._as_str_list(previous_profile.get("deep_needs", [])))
        current_needs = set(SoulEngine._as_str_list(current_profile.get("deep_needs", [])))
        if current_needs - previous_needs:
            return True
        previous_portrait = SoulEngine._normalize_text(
            str(previous_profile.get("personality_portrait", ""))
        )
        current_portrait = SoulEngine._normalize_text(
            str(current_profile.get("personality_portrait", ""))
        )
        return bool(
            previous_portrait
            and current_portrait
            and previous_portrait != current_portrait
        )

    @staticmethod
    def _as_dict_list(raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [item for item in raw_value if isinstance(item, dict)]

    @staticmethod
    def _as_str_list(raw_value: object) -> list[str]:
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

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

    @staticmethod
    def _to_float(raw_value: object) -> float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            try:
                return float(raw_value)
            except ValueError:
                return 0.0
        return 0.0
