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
    from collections.abc import Mapping

    from openbiliclaw.llm.service import ModuleOverride, SupportsComplete
    from openbiliclaw.memory.manager import MemoryManager

from openbiliclaw.llm.service import LLMService

from .awareness_analyzer import AwarenessAnalyzer
from .cognition_cycle import (
    DEFAULT_MIN_INTERVAL_SECONDS as _DEFAULT_COG_INTERVAL,
)
from .cognition_cycle import (
    CognitionCycle,
)
from .dialogue_insight_analyzer import (
    DialogueInsightAnalysisError,
    DialogueInsightAnalyzer,
)
from .insight_analyzer import InsightAnalyzer
from .pipeline import ProfileUpdatePipeline
from .preference_analyzer import PreferenceAnalyzer
from .profile import (
    AwarenessNote,
    InsightHypothesis,
    OnionProfile,
    awareness_note_from_dict,
    awareness_note_to_dict,
    insight_hypothesis_from_dict,
    insight_hypothesis_to_dict,
)
from .profile_builder import ProfileBuilder
from .speculator import InterestSpeculator

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}


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

    def __init__(
        self,
        llm: SupportsComplete,
        memory: MemoryManager,
        *,
        embedding_service: Any | None = None,
        cognition_cycle_interval_seconds: int | None = None,
        usage_recorder: Any | None = None,
        satisfaction_filter_enabled: bool = True,
        module_overrides: Mapping[str, ModuleOverride] | None = None,
        llm_concurrency: int = 3,
        speculation_interval_minutes: int = 10,
        speculation_ttl_days: int = 3,
        speculation_cooldown_days: int = 7,
        speculation_confirmation_threshold: int = 3,
        speculation_max_active: int = 5,
        speculation_max_primary_interests: int = 15,
        speculation_max_secondary_interests: int = 60,
        speculator_idle_interval_minutes: int = 30,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._satisfaction_filter_enabled = satisfaction_filter_enabled
        self._module_overrides = dict(module_overrides or {})
        self._llm_concurrency = llm_concurrency
        # Pass usage_recorder through so internal LLM calls
        # (preference / awareness / insight / profile_builder / speculator
        # / dialogue_insight) appear in the cost ledger with their caller
        # tags. Without this, the entire ``soul.*`` namespace was
        # invisible in `openbiliclaw cost --by caller` and bypassed the
        # empty-content guard in LLMService — speculator failures showed
        # up as silent "0 new generations" instead of explicit WARNs.
        self._llm_service: LLMService = LLMService(
            registry=llm,
            memory=memory,
            usage_recorder=usage_recorder,
            module_overrides=self._module_overrides,
            concurrency=llm_concurrency,
        )
        self._awareness_analyzer = AwarenessAnalyzer(self._llm_service)
        self._dialogue_insight_analyzer = DialogueInsightAnalyzer(self._llm_service)
        self._insight_analyzer = InsightAnalyzer(self._llm_service)
        self._preference_analyzer = PreferenceAnalyzer(
            self._llm_service,
            satisfaction_filter_enabled=satisfaction_filter_enabled,
        )
        self._profile_builder = ProfileBuilder(self._llm_service)
        data_dir = getattr(memory, "_data_dir", None)
        self._speculator = InterestSpeculator(
            llm_service=self._llm_service,
            data_dir=data_dir,
            generation_interval_minutes=speculation_interval_minutes,
            default_ttl_days=speculation_ttl_days,
            cooldown_days=speculation_cooldown_days,
            confirmation_threshold=speculation_confirmation_threshold,
            max_active=speculation_max_active,
            max_primary_interests=speculation_max_primary_interests,
            max_secondary_interests=speculation_max_secondary_interests,
        )
        self._embedding_service = embedding_service
        self._cognition_cycle = CognitionCycle(
            memory=memory,
            awareness_analyzer=self._awareness_analyzer,
            insight_analyzer=self._insight_analyzer,
            min_interval_seconds=(
                cognition_cycle_interval_seconds
                if cognition_cycle_interval_seconds is not None
                else _DEFAULT_COG_INTERVAL
            ),
        )
        self._pipeline = ProfileUpdatePipeline(
            memory=memory,
            preference_analyzer=self._preference_analyzer,
            profile_builder=self._profile_builder,
            speculator=self._speculator,
            embedding_service=embedding_service,
            cognition_cycle=self._cognition_cycle,
            speculator_idle_interval_minutes=speculator_idle_interval_minutes,
        )

    def set_embedding_service(self, embedding_service: Any) -> None:
        """Attach or update the embedding service after construction.

        Useful when the embedding service is built later than the soul
        engine in the bootstrap order.
        """
        self._embedding_service = embedding_service
        self._pipeline.set_embedding_service(embedding_service)

    @property
    def pipeline(self) -> Any:
        """Access the ProfileUpdatePipeline for direct signal ingestion."""
        return self._pipeline

    async def analyze_events(
        self,
        events: list[dict[str, Any]],
        *,
        event_chunk_size: int = 0,
    ) -> None:
        """Analyze new behavioral events and update all memory layers.

        This is the primary entry point for processing new user behavior.
        Events flow upward through the memory layers, with each layer
        potentially triggering updates in the layers above.

        Args:
            events: List of behavioral event dicts from the collector.
            event_chunk_size: When > 0, split the event list into chunks
                of this size and analyse each chunk in parallel. Useful
                for the init bootstrap where a single max-thinking call
                on ~800 events would block for ~6 minutes.
        """
        import time as _time

        logger.info(
            "analyze_events start: events=%d chunk_size=%d",
            len(events),
            event_chunk_size,
        )
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference")
        updated_preference = await self._preference_analyzer.analyze_events(
            events=events,
            existing_preference=preference_layer.data,
            event_chunk_size=event_chunk_size,
        )
        preference_layer.data.clear()
        preference_layer.data.update(updated_preference)
        preference_layer.save()
        logger.info(
            "analyze_events done: events=%d elapsed=%.1fs",
            len(events),
            _time.monotonic() - t0,
        )

    async def build_initial_profile(self, history: list[dict[str, Any]]) -> OnionProfile:
        """Build an initial soul profile from historical data.

        Used on first run to bootstrap the user understanding model
        from existing Bilibili watch history, favorites, etc.

        Args:
            history: Historical data from Bilibili API.

        Returns:
            Initial OnionProfile.
        """
        import time as _time

        logger.info("build_initial_profile start: history=%d items", len(history))
        t0 = _time.monotonic()
        preference_layer = self._memory.get_layer("preference").data
        legacy_profile = await self._profile_builder.build(
            history=history,
            preference=preference_layer,
            awareness_notes=[awareness_note_to_dict(item) for item in self._load_awareness_notes()],
            active_insights=[insight_hypothesis_to_dict(item) for item in self._load_insights()],
        )
        logger.info(
            "build_initial_profile: legacy profile built in %.1fs",
            _time.monotonic() - t0,
        )
        profile = OnionProfile.from_legacy(legacy_profile)
        profile.populate_from_flat_preference(preference_layer)
        soul_layer = self._memory.get_layer("soul")
        soul_layer.data.clear()
        soul_layer.data.update(profile.to_dict())
        soul_layer.save()
        self._memory.sync_profile_files(profile)
        logger.info(
            "build_initial_profile done: total_elapsed=%.1fs",
            _time.monotonic() - t0,
        )

        # Trigger speculator immediately after init to seed speculative interests
        try:
            feedback_history: object = []
            load_runtime_state = getattr(self._memory, "load_discovery_runtime_state", None)
            if callable(load_runtime_state):
                runtime_state = load_runtime_state()
                if isinstance(runtime_state, dict):
                    feedback_history = runtime_state.get("probe_feedback_history", [])
            try:
                await self._speculator.force_tick(
                    profile,
                    feedback_history=feedback_history,
                )
            except TypeError:
                await self._speculator.force_tick(profile)
        except Exception:
            logger.debug("Speculator force_tick after init failed", exc_info=True)

        return profile

    def is_profile_ready(self) -> bool:
        """Cheap, non-raising check for whether a soul profile exists.

        Background-task consumers call this first to avoid using
        ``SoulProfileNotInitializedError`` as flow control during the
        ~7-minute init window — which would otherwise produce ERROR-level
        traces for every classify / awareness / speculator tick that
        runs before the profile lands.
        """
        try:
            return bool(self._memory.get_layer("soul").data)
        except Exception:
            return False

    async def get_profile(self) -> OnionProfile:
        """Get the current soul profile.

        Returns:
            Current OnionProfile from the soul memory layer.
            Active speculative interests are attached as _active_speculations.
        """
        soul_data = self._memory.get_layer("soul").data
        if not soul_data:
            raise SoulProfileNotInitializedError("Soul profile has not been initialized yet.")
        profile = OnionProfile.from_dict(soul_data)
        # Attach active speculations so downstream consumers (Discovery) can use them
        active_specs = self._speculator.get_active_speculations()
        if active_specs:
            profile._active_speculations = active_specs  # type: ignore[attr-defined]
        return profile

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
        self._record_immediate_dialogue_cognition(merged_candidates)
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
                legacy_profile = await self._profile_builder.build(
                    history=[],
                    preference=updated_preference,
                    awareness_notes=[
                        awareness_note_to_dict(item) for item in self._load_awareness_notes()
                    ],
                    active_insights=[
                        insight_hypothesis_to_dict(item) for item in self._load_insights()
                    ],
                )
                profile = OnionProfile.from_legacy(legacy_profile)
                profile.populate_from_flat_preference(updated_preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                self._memory.sync_profile_files(profile)
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
            event_chunk_size=200,
        )
        preference_layer.data.clear()
        preference_layer.data.update(updated_preference)
        preference_layer.save()

        profile_rebuilt = False
        if self._preference_changed_significantly(existing_preference, updated_preference):
            try:
                legacy_profile = await self._profile_builder.build(
                    history=[],
                    preference=updated_preference,
                    awareness_notes=[
                        awareness_note_to_dict(item) for item in self._load_awareness_notes()
                    ],
                    active_insights=[
                        insight_hypothesis_to_dict(item) for item in self._load_insights()
                    ],
                )
                profile = OnionProfile.from_legacy(legacy_profile)
                profile.populate_from_flat_preference(updated_preference)
                soul_layer = self._memory.get_layer("soul")
                soul_layer.data.clear()
                soul_layer.data.update(profile.to_dict())
                soul_layer.save()
                self._memory.sync_profile_files(profile)
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
                "last_processed_feedback_event_id": self._to_int(feedback_events[-1].get("id", 0)),
                "last_feedback_reanalyzed_at": datetime.now().isoformat(),
            }
        )
        return {
            "triggered": True,
            "feedback_count": feedback_count,
            "preference_updated": True,
            "profile_rebuilt": profile_rebuilt,
        }

    def record_immediate_feedback_cognition(
        self,
        *,
        feedback_type: str,
        title: str,
        note: str = "",
    ) -> None:
        """Record one lightweight cognition update from a single strong feedback.

        This path is intentionally cheap: it only appends a short cognition update
        for UI visibility and does not trigger preference/profile rebuilds.
        """
        normalized_feedback = feedback_type.strip().lower()
        summary = ""
        kind = ""
        impact = ""
        reasoning = ""
        evidence = ""
        context_line = ""
        if normalized_feedback == "comment" and note.strip():
            kind = "profile_shift"
            title_text = title.strip()
            if title_text:
                summary = f"阿B 刚记下了你对《{title_text}》的评论。"
                evidence = f"你评论《{title_text}》时说：{note.strip()}"
                context_line = f"来自：《{title_text}》"
            else:
                summary = f"阿B 刚记下了：{note.strip()}"
                evidence = note.strip()
                context_line = "来自：这次推荐反馈"
            impact = "画像里对这类方向的偏好会更明确，后面会更容易继续往深一点补。"
            reasoning = "这属于单条明确反馈，先记作方向修正，不直接重写整张画像。"
        elif normalized_feedback == "dislike":
            note_text = note.strip()
            generic_dislike_notes = {"太浅了", "不喜欢", "一般", "太水了", "没意思"}
            topic = (
                title.strip() if not note_text or note_text in generic_dislike_notes else note_text
            )
            if topic:
                kind = "dislike_added"
                summary = f"阿B 记住了：像“{topic}”这种内容你大概率会划走。"
                impact = "画像里的避雷方向会更明确，后面会更主动绕开这类内容。"
                reasoning = "这是一次明确负反馈，先把这个方向记成近期避雷。"
                evidence = note_text or title.strip()
                context_line = self._build_feedback_context_line(title)
        elif normalized_feedback == "like":
            title_text = title.strip()
            if title_text:
                kind = "interest_added"
                summary = f"阿B 记住了：像《{title_text}》这一路你大概率会继续想看。"
                impact = "画像里对这类方向的偏好会更明确，后面会更愿意继续补。"
                reasoning = "这是一次明确正反馈，先把这个方向记成近期偏好强化。"
                evidence = note.strip() or title_text
                context_line = self._build_feedback_context_line(title)
        else:
            return

        if not summary:
            return

        updates = self._memory.load_cognition_updates()
        if any(
            str(item.get("summary", "")).strip() == summary
            for item in updates
            if isinstance(item, dict)
        ):
            return
        updates.insert(
            0,
            {
                "id": f"cognition-{uuid4()}",
                "kind": kind,
                "summary": summary,
                "impact": impact,
                "reasoning": reasoning,
                "evidence": evidence,
                "context_line": context_line or "基于最近几条相关内容",
                "confidence": 0.82 if kind == "dislike_added" else 0.84,
                "created_at": datetime.now().isoformat(),
                "source": "feedback",
                "source_label": self._build_source_label("feedback"),
                "expand_hint": self._build_expand_hint(
                    impact=impact,
                    reasoning=reasoning,
                    evidence=evidence,
                ),
                "notified": False,
            },
        )
        self._memory.save_cognition_updates(updates)

    def _record_immediate_dialogue_cognition(
        self,
        candidates: list[dict[str, object]],
    ) -> None:
        """Record one lightweight cognition update from a single strong chat signal."""
        updates = self._memory.load_cognition_updates()
        changed = False
        for candidate in candidates:
            if not self._candidate_ready_for_immediate_dialogue_cognition(candidate):
                continue
            (
                summary,
                kind,
                impact,
                reasoning,
                evidence,
                context_line,
            ) = self._build_immediate_dialogue_cognition(candidate)
            if not summary:
                continue
            if any(
                str(item.get("summary", "")).strip() == summary
                for item in updates
                if isinstance(item, dict)
            ):
                continue
            updates.insert(
                0,
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": kind,
                    "summary": summary,
                    "impact": impact,
                    "reasoning": reasoning,
                    "evidence": evidence,
                    "context_line": context_line,
                    "confidence": round(self._to_float(candidate.get("confidence", 0.0)), 4),
                    "created_at": datetime.now().isoformat(),
                    "source": "chat",
                    "source_label": self._build_source_label("chat"),
                    "expand_hint": self._build_expand_hint(
                        impact=impact,
                        reasoning=reasoning,
                        evidence=evidence,
                    ),
                    "notified": False,
                },
            )
            changed = True
        if changed:
            self._memory.save_cognition_updates(updates)

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
        return [insight_hypothesis_from_dict(item) for item in hypotheses if isinstance(item, dict)]

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
        return confidence >= 0.8 and occurrences >= 2

    def _candidate_ready_for_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> bool:
        kind = str(candidate.get("kind", "")).strip()
        confidence = self._to_float(candidate.get("confidence", 0.0))
        if kind in {"goal", "dislike", "interest", "value"}:
            return confidence >= 0.8
        return confidence >= 0.9 and kind == "state"

    def _build_immediate_dialogue_cognition(
        self,
        candidate: dict[str, object],
    ) -> tuple[str, str, str, str, str, str]:
        kind = str(candidate.get("kind", "")).strip()
        content = str(candidate.get("content", "")).strip()
        evidence = str(candidate.get("evidence", "")).strip() or content
        context_line = self._build_dialogue_context_line(content)
        if not content:
            return "", "", "", "", "", ""
        if kind == "goal":
            return (
                f"阿B 刚记下了：你最近在意的是“{content}”。",
                "profile_shift",
                "画像里这类目标感会更靠前，后面更容易往因果链和结构解释上贴。",
                "因为你在聊天里主动提到这个目标，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        if kind == "dislike":
            return (
                f"阿B 刚听出来：像“{content}”这种你现在大概率不太想看。",
                "dislike_added",
                "画像里的避雷方向会更靠前，推荐时会更主动避开这类内容。",
                "因为你在聊天里明确表达了排斥，这比普通停留信号更直接。",
                evidence,
                context_line,
            )
        if kind == "interest":
            return (
                f"阿B 刚摸到一点：你最近可能开始吃“{content}”这一口。",
                "interest_added",
                "画像里这类兴趣会更靠前，后面更容易继续补同方向内容。",
                "因为你在聊天里主动提到这个方向，已经不只是被动刷到。",
                evidence,
                context_line,
            )
        if kind == "value":
            return (
                f"阿B 刚摸到一点：你其实挺看重“{content}”。",
                "profile_shift",
                "画像里的价值取向会更靠前，后面会更偏向同类表达方式。",
                "因为你在聊天里主动提到这类判断标准，这是一次高置信即时信号。",
                evidence,
                context_line,
            )
        return "", "", "", "", "", ""

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
                    "context_line": self._build_topic_context_line([name]),
                    "impact": f"画像里“{name}”这条兴趣会更靠前，后面补货会更主动覆盖这个方向。",
                    "reasoning": "这不是一次偶发波动，更像是最近重复出现后的稳定兴趣强化。",
                    "evidence": f"最近聚合到的新主题里，“{name}”已经达到高权重。",
                    "confidence": round(weight, 4),
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
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
                    "context_line": self._build_topic_context_line([topic]),
                    "impact": f"画像里对“{topic}”这类内容的避雷会更明确。",
                    "reasoning": "这不是一次情绪化表达，而是最近反馈里重复浮出来的排斥方向。",
                    "evidence": f"最近聚合到的负反馈里，多次指向“{topic}”这个方向。",
                    "confidence": 0.86,
                    "created_at": now,
                    "source": source,
                    "source_label": self._build_source_label(source),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        if self._profile_shifted(previous_profile, current_profile):
            portrait = str(current_profile.get("personality_portrait", "")).strip()
            summary = portrait[:72].rstrip("，。！？,.!?") if portrait else "我对你又对上了一点。"
            updates.append(
                {
                    "id": f"cognition-{uuid4()}",
                    "kind": "profile_shift",
                    "summary": summary,
                    "context_line": self._build_profile_shift_context_line(updated_preference),
                    "impact": "画像里的人格描述和关注重心已经发生可见调整。",
                    "reasoning": "这不是单次波动，而是最近重复出现后的稳定变化。",
                    "evidence": self._build_profile_shift_evidence(updated_preference),
                    "confidence": 0.9,
                    "created_at": now,
                    "source": "profile_refresh",
                    "source_label": self._build_source_label("profile_refresh"),
                    "expand_hint": "expandable",
                    "notified": False,
                }
            )

        return updates

    @staticmethod
    def _normalize_text(value: str) -> str:
        return "".join(value.split())

    def _build_profile_shift_evidence(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ][:2]
        if interests:
            return f"最近重复出现的主题包括：{' / '.join(interests)}。"
        return "最近重复出现的信号已经足够多，开始推动画像整体调整。"

    @staticmethod
    def _build_source_label(source: str) -> str:
        return SOURCE_LABELS.get(source.strip(), "")

    @staticmethod
    def _build_expand_hint(*, impact: str, reasoning: str, evidence: str) -> str:
        if any((impact.strip(), reasoning.strip(), evidence.strip())):
            return "expandable"
        return "summary_only"

    @staticmethod
    def _build_feedback_context_line(title: str) -> str:
        title_text = title.strip()
        if title_text:
            return f"来自：《{title_text}》"
        return "来自：这次推荐反馈"

    @staticmethod
    def _build_dialogue_context_line(content: str) -> str:
        if content.strip():
            return f"来自最近这轮聊天：{content.strip()}"
        return "来自最近这轮聊天"

    @staticmethod
    def _build_topic_context_line(topics: list[str]) -> str:
        normalized = [topic.strip() for topic in topics if topic.strip()]
        if normalized:
            return f"基于最近主题：{' / '.join(normalized[:3])}"
        return "基于最近几条相关内容"

    def _build_profile_shift_context_line(self, preference: dict[str, Any]) -> str:
        interests = [
            str(item.get("name", "")).strip()
            for item in self._as_dict_list(preference.get("interests", []))
            if str(item.get("name", "")).strip()
        ]
        dislikes = self._as_str_list(preference.get("disliked_topics", []))
        return self._build_topic_context_line([*interests[:2], *dislikes[:1]])

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
            previous_portrait and current_portrait and previous_portrait != current_portrait
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
