"""Business operations exposed by the OpenClaw adapter."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import AdapterOperationError
from .schemas import (
    FeedbackRequest,
    FeedbackResponse,
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
                        row
                        for row in get_recommendations(limit=limit)
                        if isinstance(row, dict)
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
