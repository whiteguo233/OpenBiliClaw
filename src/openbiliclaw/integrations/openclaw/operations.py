"""Business operations exposed by the OpenClaw adapter."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from openbiliclaw.soul.avoidance_speculator import choose_next_avoidance_candidate
from openbiliclaw.soul.dislike_writeback import apply_new_dislikes, topics_for_confirmed_avoidance
from openbiliclaw.soul.speculator import (
    _normalize_probe_mode,
    build_probe_axis,
    choose_next_probe_candidate,
)

from .errors import AdapterOperationError, AdapterValidationError
from .schemas import (
    AvoidanceProbeFeedbackRequest,
    AvoidanceProbeFeedbackResponse,
    AvoidanceProbeItem,
    AvoidanceProbeResponse,
    ChatRequest,
    ChatResponse,
    DelightItem,
    DelightResponse,
    FeedbackRequest,
    FeedbackResponse,
    InterestProbeItem,
    InterestProbeResponse,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SyncAccountResponse,
)

logger = logging.getLogger(__name__)


class SupportsOpenClawServices(Protocol):
    """Dependency bundle required by the OpenClaw adapter."""

    soul_engine: Any
    memory_manager: Any
    database: Any
    runtime_controller: Any
    account_sync_service: Any
    recommendation_engine: Any
    llm_service: Any


@dataclass(slots=True)
class OpenClawAdapter:
    """Stable adapter interface consumed by the OpenClaw integration layer."""

    services: SupportsOpenClawServices
    refresh_timeout_seconds: float = 45.0

    @staticmethod
    def _to_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        try:
            return int(text)
        except ValueError:
            return 0

    @staticmethod
    def _to_float(value: object) -> float:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    async def sync_account(self) -> SyncAccountResponse:
        """Run one account sync and normalize the result."""
        try:
            result = await self.services.account_sync_service.sync_now()
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to sync account signals.") from exc
        return SyncAccountResponse(
            synced=bool(result.get("synced", False)),
            new_event_count=int(result.get("new_event_count", 0) or 0),
            errors=[str(item) for item in result.get("errors", []) if str(item).strip()],
        )

    async def get_profile(self) -> ProfileResponse:
        """Return a trimmed profile summary."""
        try:
            profile = await self.services.soul_engine.get_profile()
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to load soul profile.") from exc
        return ProfileResponse(
            initialized=True,
            personality_portrait=str(getattr(profile, "personality_portrait", "")),
            core_traits=[str(item) for item in getattr(profile, "core_traits", [])[:5]],
            deep_needs=[str(item) for item in getattr(profile, "deep_needs", [])[:5]],
            top_interests=[
                str(getattr(item, "name", "")).strip()
                for item in getattr(getattr(profile, "preferences", None), "interests", [])[:5]
                if str(getattr(item, "name", "")).strip()
            ],
        )

    async def recommend(
        self,
        *,
        limit: int = 5,
        refresh_if_needed: bool = False,
    ) -> RecommendationResponse:
        """Generate recommendations for OpenClaw consumption."""
        try:
            profile = await self.services.soul_engine.get_profile()
            rows: list[dict[str, object]] | None = None
            if refresh_if_needed:
                refresh = getattr(self.services.runtime_controller, "refresh_if_needed", None)
                if callable(refresh):
                    try:
                        await asyncio.wait_for(
                            refresh(),
                            timeout=max(self.refresh_timeout_seconds, 0.1),
                        )
                    except TimeoutError:
                        logger.warning(
                            "OpenClaw recommend refresh timed out after %.2fs; "
                            "falling back to cached recommendations.",
                            self.refresh_timeout_seconds,
                        )
                    except Exception:
                        logger.exception(
                            "OpenClaw recommend refresh failed; "
                            "falling back to cached recommendations."
                        )
                get_recommendations = getattr(self.services.database, "get_recommendations", None)
                if callable(get_recommendations):
                    rows = [
                        row for row in get_recommendations(limit=limit) if isinstance(row, dict)
                    ]
            if rows is not None:
                return RecommendationResponse(
                    items=[
                        RecommendationItem(
                            recommendation_id=self._to_int(row.get("id", 0)),
                            bvid=str(row.get("bvid", "")),
                            title=str(row.get("title", "")),
                            up_name=str(row.get("up_name", "")),
                            cover_url=str(row.get("cover_url", "")),
                            reason=str(row.get("expression", "")),
                            topic_label=str(row.get("topic", "")),
                            confidence=self._to_float(row.get("confidence", 0.0)),
                        )
                        for row in rows
                    ]
                )
            items = await self.services.recommendation_engine.generate_recommendations(
                None,
                profile,
                limit=limit,
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to generate recommendations.") from exc
        return RecommendationResponse(
            items=[
                RecommendationItem(
                    recommendation_id=int(getattr(item, "recommendation_id", 0) or 0),
                    bvid=str(getattr(getattr(item, "content", None), "bvid", "")),
                    title=str(getattr(getattr(item, "content", None), "title", "")),
                    up_name=str(getattr(getattr(item, "content", None), "up_name", "")),
                    cover_url=str(getattr(getattr(item, "content", None), "cover_url", "")),
                    reason=str(getattr(item, "expression", "")),
                    topic_label=str(getattr(item, "topic_label", "")),
                    confidence=float(getattr(item, "confidence", 0.0) or 0.0),
                )
                for item in items
            ]
        )

    async def submit_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        """Persist recommendation feedback and trigger downstream learning hooks."""
        try:
            recommendation = self.services.database.get_recommendation_by_id(
                request.recommendation_id
            )
            if recommendation is None:
                raise AdapterOperationError("Recommendation not found.")
            self.services.database.update_recommendation_feedback(
                request.recommendation_id,
                feedback_type=request.feedback_type,
                feedback_note=request.note,
            )
            await self.services.memory_manager.propagate_event(
                {
                    "event_type": "feedback",
                    "title": str(recommendation.get("title", "")),
                    "metadata": {
                        "recommendation_id": request.recommendation_id,
                        "bvid": recommendation.get("bvid", ""),
                        "feedback_type": request.feedback_type,
                        "feedback_note": request.note,
                    },
                }
            )
            immediate = getattr(
                self.services.soul_engine,
                "record_immediate_feedback_cognition",
                None,
            )
            if callable(immediate):
                immediate(
                    feedback_type=request.feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=request.note,
                )
            process_feedback = getattr(
                self.services.soul_engine,
                "process_feedback_batch_if_needed",
                None,
            )
            if callable(process_feedback):
                await process_feedback()
            refresh = getattr(self.services.runtime_controller, "refresh_after_feedback", None)
            if callable(refresh):
                await refresh()
        except AdapterOperationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to submit recommendation feedback.") from exc
        return FeedbackResponse(
            ok=True,
            recommendation_id=request.recommendation_id,
            feedback_type=request.feedback_type,
        )

    async def get_delight(self) -> DelightResponse:
        """Return the current best proactive delight candidate, if any."""
        try:
            get_pending_delight = getattr(
                self.services.runtime_controller,
                "get_pending_delight",
                None,
            )
            if not callable(get_pending_delight):
                return DelightResponse(item=None)
            candidate = get_pending_delight()
            if candidate is None:
                return DelightResponse(item=None)
            return DelightResponse(
                item=DelightItem(
                    bvid=str(candidate.get("bvid", "")),
                    title=str(candidate.get("title", "")),
                    delight_reason=str(candidate.get("delight_reason", "")),
                    delight_score=self._to_float(candidate.get("delight_score", 0.0)),
                    delight_hook=str(candidate.get("delight_hook", "")),
                    cover_url=str(candidate.get("cover_url", "")),
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to get delight candidate.") from exc

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Run one Socratic dialogue turn and return the agent's reply.

        The agent's reply already flows back into the soul engine through
        ``SocraticDialogue``'s internal ``learn_from_dialogue`` hook, so the
        caller does not need to persist anything separately — the user's
        answer becomes signal the next time the profile is rebuilt.
        """
        try:
            from openbiliclaw.soul.dialogue import SocraticDialogue

            soul_engine = self.services.soul_engine
            llm_service = getattr(self.services, "llm_service", None)
            llm_provider = (
                getattr(soul_engine, "_llm", None) or getattr(llm_service, "_registry", None)
                if llm_service is not None
                else getattr(soul_engine, "_llm", None)
            )
            dialogue = SocraticDialogue(
                llm=llm_provider,
                soul_engine=soul_engine,
                llm_service=llm_service,
                session=request.session,
            )
            reply = await dialogue.respond(request.message)
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to run Socratic dialogue turn.") from exc
        return ChatResponse(reply=str(reply), session=request.session)

    async def get_next_probe(self) -> InterestProbeResponse:
        """Return the next speculative-interest hypothesis to ask the user about.

        Picks the active speculation with the lowest confirmation_count (i.e.
        the hypothesis that still needs the most validation). Returns ``None``
        when the speculator has no active candidates — which means the agent
        currently has no pending interest question to ask.
        """
        try:
            soul_engine = self.services.soul_engine
            speculator = getattr(soul_engine, "_speculator", None)
            get_active = getattr(speculator, "get_active_speculations", None)
            if not callable(get_active):
                return InterestProbeResponse(probe=None)
            specs = list(get_active())
            if not specs:
                return InterestProbeResponse(probe=None)
            load_runtime_state = getattr(
                self.services.memory_manager,
                "load_discovery_runtime_state",
                None,
            )
            runtime_state = load_runtime_state() if callable(load_runtime_state) else {}
            if not isinstance(runtime_state, dict):
                runtime_state = {}
            probed_domains = set((runtime_state.get("probed_domains") or {}).keys())
            probed_axes = set((runtime_state.get("probed_axes") or {}).keys())
            probed_probe_modes = set((runtime_state.get("probed_distance_bands") or {}).keys())
            top = choose_next_probe_candidate(
                specs,
                probed_domains=probed_domains,
                probed_axes=probed_axes,
                probed_probe_modes=probed_probe_modes,
                feedback_history=runtime_state.get("probe_feedback_history", []),
            )
            if top is None:
                return InterestProbeResponse(probe=None)
            domain = str(getattr(top, "domain", "")).strip()
            if not domain:
                return InterestProbeResponse(probe=None)
            self._record_probe_history(runtime_state, top, domain)
            category = str(getattr(top, "category", "")).strip()
            reason = str(getattr(top, "reason", "")).strip()
            confidence = self._to_float(getattr(top, "confidence", 0.0))
            weight = self._to_float(getattr(top, "weight", 0.0))
            specifics = [
                str(getattr(item, "name", "")).strip()
                for item in getattr(top, "specifics", [])
                if str(getattr(item, "name", "")).strip()
            ][:5]
            question = self._build_probe_question(
                domain=domain,
                reason=reason,
                specifics=specifics,
            )
            return InterestProbeResponse(
                probe=InterestProbeItem(
                    domain=domain,
                    category=category,
                    reason=reason,
                    confidence=confidence,
                    weight=weight,
                    experience_mode=str(getattr(top, "experience_mode", "")),
                    entry_load=str(getattr(top, "entry_load", "")),
                    specifics=specifics,
                    question=question,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to read next interest probe.") from exc

    def _record_probe_history(
        self,
        runtime_state: dict[str, object],
        probe: Any,
        domain: str,
        *,
        domains_key: str = "probed_domains",
        axes_key: str = "probed_axes",
        probe_modes_key: str | None = "probed_distance_bands",
    ) -> None:
        """Persist OpenClaw probe selection so repeated calls avoid repeats."""
        update_runtime_state = getattr(
            self.services.memory_manager,
            "update_discovery_runtime_state",
            None,
        )
        save_runtime_state = getattr(
            self.services.memory_manager,
            "save_discovery_runtime_state",
            None,
        )
        if not callable(update_runtime_state) and not callable(save_runtime_state):
            return
        now = datetime.now().isoformat()
        axis = build_probe_axis(
            experience_mode=getattr(probe, "experience_mode", ""),
            entry_load=getattr(probe, "entry_load", ""),
        )
        probe_mode = _normalize_probe_mode(getattr(probe, "probe_mode", ""))

        def _mutate(state: dict[str, object]) -> None:
            raw_domains = state.get(domains_key)
            raw_axes = state.get(axes_key)
            raw_probe_modes = state.get(probe_modes_key) if probe_modes_key else None
            probed_domains = dict(raw_domains) if isinstance(raw_domains, dict) else {}
            probed_axes = dict(raw_axes) if isinstance(raw_axes, dict) else {}
            probed_probe_modes = dict(raw_probe_modes) if isinstance(raw_probe_modes, dict) else {}
            probed_domains[domain.lower()] = now
            if axis:
                probed_axes[axis] = now
            if probe_modes_key:
                probed_probe_modes[probe_mode] = now
                state[probe_modes_key] = probed_probe_modes
            state[domains_key] = probed_domains
            state[axes_key] = probed_axes

        if callable(update_runtime_state):
            update_runtime_state(_mutate)
            return
        if not callable(save_runtime_state):
            return
        _mutate(runtime_state)
        save_runtime_state(runtime_state)

    @staticmethod
    def _build_probe_question(
        *,
        domain: str,
        reason: str,
        specifics: list[str],
    ) -> str:
        """Template a ready-to-ask probe question from a speculation."""
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        if reason:
            return (
                f"我从你最近的轨迹里嗅到你可能对【{domain}】{specific_hint}感兴趣"
                f"——{reason} 这个方向你自己认不认？"
            )
        return f"我感觉你可能对【{domain}】{specific_hint}有潜在兴趣，这个方向你自己认不认？"

    async def get_next_avoidance_probe(self) -> AvoidanceProbeResponse:
        """Return the next speculative-avoidance hypothesis to ask about."""
        try:
            soul_engine = self.services.soul_engine
            speculator = getattr(soul_engine, "_avoidance_speculator", None)
            get_active = getattr(speculator, "get_active_avoidances", None)
            if not callable(get_active):
                return AvoidanceProbeResponse(probe=None)
            avoidances = list(get_active())
            if not avoidances:
                return AvoidanceProbeResponse(probe=None)
            load_runtime_state = getattr(
                self.services.memory_manager,
                "load_discovery_runtime_state",
                None,
            )
            runtime_state = load_runtime_state() if callable(load_runtime_state) else {}
            if not isinstance(runtime_state, dict):
                runtime_state = {}
            probed_domains = set((runtime_state.get("probed_avoidance_domains") or {}).keys())
            probed_axes = set((runtime_state.get("probed_avoidance_axes") or {}).keys())
            top = choose_next_avoidance_candidate(
                avoidances,
                probed_domains=probed_domains,
                probed_axes=probed_axes,
                feedback_history=runtime_state.get("avoidance_probe_feedback_history", []),
            )
            if top is None:
                return AvoidanceProbeResponse(probe=None)
            domain = str(getattr(top, "domain", "")).strip()
            if not domain:
                return AvoidanceProbeResponse(probe=None)
            self._record_probe_history(
                runtime_state,
                top,
                domain,
                domains_key="probed_avoidance_domains",
                axes_key="probed_avoidance_axes",
                probe_modes_key=None,
            )
            reason = str(getattr(top, "reason", "")).strip()
            confidence = self._to_float(getattr(top, "confidence", 0.0))
            weight = self._to_float(getattr(top, "weight", 0.0))
            specifics = [
                str(getattr(item, "name", "")).strip()
                for item in getattr(top, "specifics", [])
                if str(getattr(item, "name", "")).strip()
            ][:5]
            question = self._build_avoidance_probe_question(
                domain=domain,
                reason=reason,
                specifics=specifics,
            )
            return AvoidanceProbeResponse(
                probe=AvoidanceProbeItem(
                    domain=domain,
                    reason=reason,
                    confidence=confidence,
                    weight=weight,
                    source_mode=str(getattr(top, "source_mode", "")),
                    source_signal=str(getattr(top, "source_signal", "")),
                    experience_mode=str(getattr(top, "experience_mode", "")),
                    entry_load=str(getattr(top, "entry_load", "")),
                    specifics=specifics,
                    question=question,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to read next avoidance probe.") from exc

    async def respond_avoidance_probe(
        self,
        request: AvoidanceProbeFeedbackRequest,
    ) -> AvoidanceProbeFeedbackResponse:
        """Record user feedback for a speculative avoidance probe."""
        try:
            speculator = getattr(self.services.soul_engine, "_avoidance_speculator", None)
            if request.response == "confirm":
                confirm = getattr(speculator, "user_confirm_avoidance", None)
                active = confirm(request.domain) if callable(confirm) else None
                ok = active is not None
                get_layer = getattr(self.services.memory_manager, "get_layer", None)
                if ok and callable(get_layer):
                    await apply_new_dislikes(
                        memory=self.services.memory_manager,
                        database=getattr(self.services, "database", None)
                        or getattr(self.services.memory_manager, "_database", None),
                        embedding_service=getattr(
                            self.services.soul_engine,
                            "_embedding_service",
                            None,
                        ),
                        llm_service=getattr(self.services, "llm_service", None),
                        topics=topics_for_confirmed_avoidance(active),
                    )
                return AvoidanceProbeFeedbackResponse(
                    ok=ok,
                    action="confirmed",
                    domain=request.domain,
                )
            if request.response == "reject":
                reject = getattr(speculator, "user_reject_avoidance", None)
                ok = bool(reject(request.domain) if callable(reject) else False)
                return AvoidanceProbeFeedbackResponse(
                    ok=ok,
                    action="rejected",
                    domain=request.domain,
                )

            message = request.message or f"我想聊聊你猜我可能想避开的「{request.domain}」"
            reply = await self.chat(
                ChatRequest(
                    message=f"[关于避雷方向「{request.domain}」的反馈] {message}",
                    session="openclaw",
                )
            )
            return AvoidanceProbeFeedbackResponse(
                ok=True,
                action="chat",
                domain=request.domain,
                reply=reply.reply,
            )
        except AdapterValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to respond to avoidance probe.") from exc

    @staticmethod
    def _build_avoidance_probe_question(
        *,
        domain: str,
        reason: str,
        specifics: list[str],
    ) -> str:
        """Template a ready-to-ask avoidance probe question."""
        specific_hint = ""
        if specifics:
            specific_hint = "（比如：" + "、".join(specifics[:3]) + "）"
        if reason:
            return f"我猜【{domain}】{specific_hint}可能是你想避开的方向——{reason} 这个判断准吗？"
        return f"我感觉【{domain}】{specific_hint}可能不是你想看的方向，这个判断准吗？"

    async def get_runtime_status(self) -> RuntimeStatusResponse:
        """Return the merged runtime and account sync summary."""
        try:
            runtime_status: dict[str, object] = {}
            get_runtime_status = getattr(
                self.services.runtime_controller,
                "get_runtime_status",
                None,
            )
            if callable(get_runtime_status):
                runtime_status = dict(get_runtime_status())
            get_account_sync_status = getattr(
                self.services.account_sync_service,
                "get_runtime_status",
                None,
            )
            if callable(get_account_sync_status):
                runtime_status.update(get_account_sync_status())
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to read runtime status.") from exc
        return RuntimeStatusResponse(
            initialized=bool(runtime_status.get("initialized", False)),
            recommendation_count=self._to_int(runtime_status.get("recommendation_count", 0)),
            pending_signal_events=self._to_int(runtime_status.get("pending_signal_events", 0)),
            unread_count=self._to_int(runtime_status.get("unread_count", 0)),
            pool_available_count=self._to_int(runtime_status.get("pool_available_count", 0)),
            pool_target_count=self._to_int(runtime_status.get("pool_target_count", 0)),
            last_discovered_count=self._to_int(runtime_status.get("last_discovered_count", 0)),
            last_refresh_at=str(runtime_status.get("last_refresh_at", "")),
            last_account_sync_at=str(runtime_status.get("last_account_sync_at", "")),
            last_account_sync_error=str(runtime_status.get("last_account_sync_error", "")),
        )
