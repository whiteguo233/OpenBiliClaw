"""Business operations exposed by the OpenClaw adapter."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from openbiliclaw.llm.base import safe_llm_failure_message
from openbiliclaw.soul.avoidance_speculator import choose_next_avoidance_candidate
from openbiliclaw.soul.dislike_writeback import apply_new_dislikes, topics_for_confirmed_avoidance
from openbiliclaw.soul.speculator import (
    _normalize_probe_mode,
    build_probe_axis,
    choose_next_probe_candidate,
)
from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES, normalize_source_platform

from .errors import AdapterOperationError, AdapterValidationError
from .schemas import (
    ActivityFeedItem,
    ActivityFeedResponse,
    AvoidanceProbeFeedbackRequest,
    AvoidanceProbeFeedbackResponse,
    AvoidanceProbeItem,
    AvoidanceProbeResponse,
    CapabilitiesResponse,
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    ChatTurnItem,
    DelightFeedbackRequest,
    DelightFeedbackResponse,
    DelightItem,
    DelightResponse,
    FeedbackRequest,
    FeedbackResponse,
    InterestProbeFeedbackRequest,
    InterestProbeFeedbackResponse,
    InterestProbeItem,
    InterestProbeResponse,
    PlatformAvailabilityResponse,
    ProfileEditRequest,
    ProfileEditResponse,
    ProfileEditStateResponse,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SavedItemRequest,
    SavedItemResponse,
    SavedListResponse,
    SavedRemoveRequest,
    SavedRemoveResponse,
    SavedSyncRequest,
    SavedSyncResponse,
    SyncAccountResponse,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip() or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_text(value: object, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _source_scope(value: object) -> str:
    """Normalize and validate the optional recommendation platform scope."""
    scope = normalize_source_platform(value)
    if scope and scope not in CANONICAL_SOURCE_FAMILIES:
        supported = ", ".join(CANONICAL_SOURCE_FAMILIES)
        raise AdapterValidationError(
            f"unsupported source_platform {str(value).strip()!r}; expected one of: {supported}"
        )
    return scope


async def _maybe_await(value: object) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@contextmanager
def _suppress_exceptions(label: str) -> Iterator[None]:
    """Keep retryable follow-up hooks from changing a committed response."""
    try:
        yield
    except Exception:
        logger.warning("%s failed", label, exc_info=True)


def _recommendation_from_row(row: dict[str, object]) -> RecommendationItem:
    """Project one canonical recommendation row into the bridge DTO."""
    bvid = _as_text(row.get("bvid"))
    content_id = _as_text(row.get("content_id"), bvid)
    source_platform = normalize_source_platform(
        row.get("source_platform"),
        default="bilibili" if bvid else "",
    )
    author_name = _as_text(row.get("author_name"), _as_text(row.get("up_name")))
    title = _as_text(row.get("title"))
    expression = _as_text(row.get("expression"), _as_text(row.get("reason")))
    topic_label = _as_text(
        row.get("topic_label"),
    ) or _as_text(row.get("pool_topic_label"), _as_text(row.get("topic")))
    return RecommendationItem(
        recommendation_id=_as_int(row.get("recommendation_id", row.get("id", 0))),
        bvid=bvid,
        title=title,
        up_name=author_name,
        cover_url=_as_text(row.get("cover_url")),
        reason=expression,
        topic_label=topic_label,
        confidence=_as_float(row.get("confidence")),
        item_key=_as_text(row.get("item_key")),
        content_id=content_id,
        content_url=_as_text(row.get("content_url")),
        source_platform=source_platform,
        author_name=author_name,
        published_at=_as_text(row.get("published_at")),
        published_label=_as_text(row.get("published_label")),
        content_type=_as_text(row.get("content_type"), "video"),
        body_text=_as_text(row.get("body_text")),
        expression=expression,
        presented=_as_bool(row.get("presented", False)),
        feedback_type=_as_text(row.get("feedback_type")),
        duration=_as_int(row.get("duration")),
        view_count=_as_int(row.get("view_count")),
        like_count=_as_int(row.get("like_count")),
        danmaku_count=_as_int(row.get("danmaku_count")),
        favorite_count=_as_int(row.get("favorite_count")),
        comment_count=_as_int(row.get("comment_count")),
        rating_score=_as_float(row.get("rating_score")),
        rating_count=_as_int(row.get("rating_count")),
        source_rank=_as_int(row.get("source_rank")),
        up_mid=_as_int(row.get("up_mid")),
    )


def _recommendation_from_object(item: object) -> RecommendationItem:
    content = getattr(item, "content", None)
    row: dict[str, object] = {
        "recommendation_id": getattr(item, "recommendation_id", 0),
        "bvid": getattr(content, "bvid", ""),
        "item_key": getattr(content, "item_key", ""),
        "content_id": getattr(content, "content_id", ""),
        "content_url": getattr(content, "content_url", ""),
        "title": getattr(content, "title", ""),
        "up_name": getattr(content, "up_name", ""),
        "author_name": getattr(content, "author_name", ""),
        "cover_url": getattr(content, "cover_url", ""),
        "source_platform": getattr(content, "source_platform", ""),
        "published_at": getattr(content, "published_at", ""),
        "published_label": getattr(content, "published_label", ""),
        "content_type": getattr(content, "content_type", "video"),
        "body_text": getattr(content, "body_text", ""),
        "expression": getattr(item, "expression", ""),
        "topic_label": getattr(item, "topic_label", ""),
        "confidence": getattr(item, "confidence", 0.0),
        "duration": getattr(content, "duration", 0),
        "view_count": getattr(content, "view_count", 0),
        "like_count": getattr(content, "like_count", 0),
        "danmaku_count": getattr(content, "danmaku_count", 0),
        "favorite_count": getattr(content, "favorite_count", 0),
        "comment_count": getattr(content, "comment_count", 0),
        "rating_score": getattr(content, "rating_score", 0.0),
        "rating_count": getattr(content, "rating_count", 0),
        "source_rank": getattr(content, "source_rank", 0),
        "up_mid": getattr(content, "up_mid", 0),
    }
    return _recommendation_from_row(row)


def _delight_from_row(row: dict[str, object]) -> DelightItem:
    bvid = _as_text(row.get("bvid"))
    return DelightItem(
        bvid=bvid,
        title=_as_text(row.get("title")),
        delight_reason=_as_text(row.get("delight_reason")),
        delight_score=_as_float(row.get("delight_score")),
        delight_hook=_as_text(row.get("delight_hook")),
        cover_url=_as_text(row.get("cover_url")),
        item_key=_as_text(row.get("item_key")),
        content_id=_as_text(row.get("content_id"), bvid),
        content_url=_as_text(row.get("content_url")),
        source_platform=normalize_source_platform(
            row.get("source_platform"),
            default="bilibili" if bvid else "",
        ),
        published_at=_as_text(row.get("published_at")),
        published_label=_as_text(row.get("published_label")),
        content_type=_as_text(row.get("content_type"), "video"),
        body_text=_as_text(row.get("body_text")),
        view_count=_as_int(row.get("view_count")),
        like_count=_as_int(row.get("like_count")),
        comment_count=_as_int(row.get("comment_count")),
        danmaku_count=_as_int(row.get("danmaku_count")),
        favorite_count=_as_int(row.get("favorite_count")),
        rating_score=_as_float(row.get("rating_score")),
        rating_count=_as_int(row.get("rating_count")),
        source_rank=_as_int(row.get("source_rank")),
    )


def _chat_turn_from_row(row: dict[str, object]) -> ChatTurnItem:
    raw_payload = row.get("payload", {})
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    return ChatTurnItem(
        turn_id=_as_text(row.get("turn_id")),
        session=_as_text(row.get("session"), "openclaw"),
        scope=_as_text(row.get("scope"), "chat"),
        subject_id=_as_text(row.get("subject_id")),
        subject_title=_as_text(row.get("subject_title")),
        reply_to_turn_id=_as_text(row.get("reply_to_turn_id")),
        message=_as_text(row.get("message")),
        reply=_as_text(row.get("reply")),
        status=_as_text(row.get("status"), "pending"),
        error=_as_text(row.get("error")),
        payload=dict(payload),
        created_at=_as_text(row.get("created_at")),
        updated_at=_as_text(row.get("updated_at")),
    )


class SupportsOpenClawServices(Protocol):
    """Dependency bundle required by the OpenClaw adapter."""

    soul_engine: Any
    memory_manager: Any
    database: Any
    runtime_controller: Any
    account_sync_service: Any
    recommendation_engine: Any
    llm_service: Any
    event_ingress: Any
    saved_sync_service: Any


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
            process_profile = getattr(
                self.services.soul_engine,
                "process_profile_events_if_needed",
                None,
            )
            if callable(process_profile):
                await process_profile()
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

    async def get_capabilities(self) -> CapabilitiesResponse:
        """Return the versioned host-negotiation manifest."""
        from .capabilities import build_capabilities

        return build_capabilities(self)

    async def recommend(
        self,
        *,
        limit: int = 5,
        refresh_if_needed: bool = False,
        source_platform: str = "",
        excluded_item_ids: list[str] | None = None,
        realtime: bool = False,
    ) -> RecommendationResponse:
        """Generate multi-source recommendations for an agent host.

        Serves precomputed pool copy (fast) by default.  Set ``realtime=True``
        to generate fresh per-item LLM expressions at request time (slow).
        ``source_platform`` and ``excluded_item_ids`` mirror the current API
        recommendation contract.  Older engines and test doubles continue to
        work through the historical ``generate_recommendations`` fallback.
        """
        if limit <= 0:
            raise AdapterValidationError("recommendation limit must be positive.")
        if limit > 50:
            raise AdapterValidationError("recommendation limit must not exceed 50.")
        scope = _source_scope(source_platform)
        excluded = {str(item).strip() for item in (excluded_item_ids or []) if str(item).strip()}
        try:
            profile = await self.services.soul_engine.get_profile()
            fallback_dislikes = [
                str(item).strip()
                for item in getattr(profile.preferences, "disliked_topics", [])
                if str(item).strip()
            ]

            def latest_dislikes() -> list[str]:
                getter = getattr(
                    self.services.soul_engine,
                    "get_effective_disliked_topics",
                    None,
                )
                if not callable(getter):
                    return fallback_dislikes
                try:
                    return [str(item).strip() for item in getter() if str(item).strip()]
                except Exception:
                    logger.warning(
                        "OpenClaw effective dislike read failed; using profile snapshot",
                        exc_info=True,
                    )
                    return fallback_dislikes

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
                    history_limit = limit
                    try:
                        stored_rows = get_recommendations(
                            limit=history_limit,
                            exclude_processed=True,
                        )
                    except TypeError:
                        stored_rows = get_recommendations(limit=history_limit)
                    rows = [
                        row
                        for row in stored_rows
                        if isinstance(row, dict)
                        and not str(row.get("feedback_type", "") or "").strip()
                        and (
                            not scope
                            or normalize_source_platform(row.get("source_platform")) == scope
                        )
                        and not excluded.intersection(
                            {
                                _as_text(row.get("item_key")),
                                _as_text(row.get("content_id")),
                                _as_text(row.get("bvid")),
                            }
                        )
                    ]
                    if rows:
                        from openbiliclaw.recommendation.exclusion import (
                            filter_recommendation_rows,
                        )

                        rows = filter_recommendation_rows(rows, latest_dislikes())[:limit]
            # A fresh one-shot runtime has canonical pool rows but no
            # recommendation-history rows yet.  Only return the history fast
            # path when it actually has entries; otherwise serve the newly
            # copied pool below.
            if rows:
                return RecommendationResponse(items=[_recommendation_from_row(row) for row in rows])
            engine = self.services.recommendation_engine
            serve = getattr(engine, "serve", None)
            expression_mode = "realtime" if realtime else "precomputed"
            if callable(serve):
                result = await _maybe_await(
                    serve(
                        profile,
                        limit=limit,
                        excluded_bvids=frozenset(excluded),
                        expression_mode=expression_mode,
                        source_platform=scope,
                    )
                )
                items = list(getattr(result, "items", result) or [])
            else:
                items = await engine.generate_recommendations(
                    None,
                    profile,
                    limit=limit,
                )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to generate recommendations.") from exc
        response_items = [_recommendation_from_object(item) for item in items]
        if response_items:
            from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

            filtered_rows = filter_recommendation_rows(
                [asdict(item) for item in response_items],
                latest_dislikes(),
            )
            response_items = [RecommendationItem(**row) for row in filtered_rows]
        if scope:
            response_items = [
                item
                for item in response_items
                if not item.source_platform or item.source_platform == scope
            ]
        if excluded:
            response_items = [
                item
                for item in response_items
                if not excluded.intersection({item.item_key, item.content_id, item.bvid})
            ]
        return RecommendationResponse(items=response_items[:limit])

    async def reshuffle(
        self,
        *,
        limit: int = 5,
        source_platform: str = "",
        excluded_item_ids: list[str] | None = None,
    ) -> RecommendationResponse:
        """Return a fresh precomputed recommendation batch."""
        return await self._serve_page(
            operation="reshuffle",
            limit=limit,
            source_platform=source_platform,
            excluded_item_ids=excluded_item_ids,
        )

    async def append_recommendations(
        self,
        *,
        limit: int = 10,
        source_platform: str = "",
        excluded_item_ids: list[str] | None = None,
    ) -> RecommendationResponse:
        """Append another precomputed recommendation page."""
        return await self._serve_page(
            operation="append",
            limit=limit,
            source_platform=source_platform,
            excluded_item_ids=excluded_item_ids,
        )

    async def _serve_page(
        self,
        *,
        operation: str,
        limit: int,
        source_platform: str,
        excluded_item_ids: list[str] | None,
    ) -> RecommendationResponse:
        if limit <= 0:
            raise AdapterValidationError("recommendation limit must be positive.")
        if limit > 50:
            raise AdapterValidationError("recommendation limit must not exceed 50.")
        scope = _source_scope(source_platform)
        excluded = [str(item).strip() for item in (excluded_item_ids or []) if str(item).strip()]
        try:
            profile = await self.services.soul_engine.get_profile()
            engine = self.services.recommendation_engine
            method = getattr(
                engine,
                f"{operation}_recommendations_with_result",
                None,
            )
            if callable(method):
                result = await _maybe_await(
                    method(
                        profile=profile,
                        excluded_bvids=excluded,
                        limit=limit,
                        source_platform=scope,
                    )
                )
                items = list(getattr(result, "items", []) or [])
            else:
                legacy = getattr(engine, f"{operation}_recommendations", None)
                if not callable(legacy):
                    raise AdapterOperationError(
                        f"Recommendation operation is unavailable: {operation}."
                    )
                items = list(
                    await _maybe_await(
                        legacy(
                            profile=profile,
                            excluded_bvids=excluded,
                            limit=limit,
                            source_platform=scope,
                        )
                    )
                    or []
                )
            response_items = [_recommendation_from_object(item) for item in items]
        except (AdapterOperationError, AdapterValidationError):
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError(f"Failed to {operation} recommendations.") from exc
        if response_items:
            from openbiliclaw.recommendation.exclusion import filter_recommendation_rows

            get_effective_dislikes = getattr(
                self.services.soul_engine,
                "get_effective_disliked_topics",
                None,
            )
            disliked_topics = (
                [str(item).strip() for item in get_effective_dislikes() if str(item).strip()]
                if callable(get_effective_dislikes)
                else []
            )
            filtered_rows = filter_recommendation_rows(
                [asdict(item) for item in response_items],
                disliked_topics,
            )
            response_items = [RecommendationItem(**row) for row in filtered_rows]
        if scope:
            response_items = [
                item
                for item in response_items
                if not item.source_platform or item.source_platform == scope
            ]
        if excluded:
            excluded_set = set(excluded)
            response_items = [
                item
                for item in response_items
                if not excluded_set.intersection({item.item_key, item.content_id, item.bvid})
            ]
        return RecommendationResponse(items=response_items[:limit])

    async def submit_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        """Persist recommendation feedback and trigger downstream learning hooks."""
        try:
            recommendation = self.services.database.get_recommendation_by_id(
                request.recommendation_id
            )
            if recommendation is None:
                raise AdapterOperationError("Recommendation not found.")
            from openbiliclaw.sources.event_format import build_event

            event = build_event(
                event_type="feedback",
                source_platform=str(recommendation.get("source_platform", "bilibili")),
                title=str(recommendation.get("title", "")),
                metadata={
                    "recommendation_id": request.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": request.feedback_type,
                    "feedback_note": request.note,
                    "event_namespace": "recommendation",
                    "profile_update_owner": "content_feedback",
                },
            )
            event["ingest_key"] = request.request_id
            receipt = await self.services.event_ingress.accept(
                event,
                producer="openclaw",
            )
            if receipt.accepted != 1 or receipt.rejected:
                raise AdapterOperationError("Feedback event was rejected.")
            item_receipt = receipt.items[0]
            stored_rows = self.services.database.query_event_rows_by_ids([item_receipt.event_id])
            if len(stored_rows) != 1:
                raise AdapterOperationError("Durable feedback event could not be read back.")
            raw_metadata = stored_rows[0].get("metadata", {})
            if isinstance(raw_metadata, str):
                try:
                    raw_metadata = json.loads(raw_metadata) if raw_metadata else {}
                except (TypeError, ValueError):
                    raw_metadata = {}
            stored_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            stored_recommendation_id = self._to_int(stored_metadata.get("recommendation_id", 0))
            stored_feedback_type = str(stored_metadata.get("feedback_type") or "").strip().lower()
            stored_note = str(stored_metadata.get("feedback_note") or "").strip()
            if (
                stored_recommendation_id != request.recommendation_id
                or stored_feedback_type != request.feedback_type
                or stored_note != request.note
            ):
                raise AdapterValidationError("request_id was already used for different feedback.")
            # Idempotent projection repair driven by the durable first write.
            self.services.database.update_recommendation_feedback(
                stored_recommendation_id,
                feedback_type=stored_feedback_type,
                feedback_note=stored_note,
            )
        except (AdapterOperationError, AdapterValidationError):
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to submit recommendation feedback.") from exc

        # Durable first write + recommendation projection is the command's
        # commit boundary. The preference-analysis + refresh hooks are retryable
        # and must NOT block the command: the durable event is already committed
        # and the runtime's background feedback-batch + refresh schedulers drain
        # it asynchronously (the same non-blocking shape the chat path uses).
        immediate = getattr(
            self.services.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if item_receipt.inserted and callable(immediate):
            try:
                immediate(
                    feedback_type=request.feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=request.note,
                )
            except Exception:
                logger.warning("OpenClaw feedback cognition follow-up deferred", exc_info=True)

        return FeedbackResponse(
            ok=True,
            recommendation_id=request.recommendation_id,
            feedback_type=request.feedback_type,
            event_id=item_receipt.event_id,
            duplicate=item_receipt.duplicate,
            processing="queued",
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
            return DelightResponse(item=_delight_from_row(dict(candidate)))
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to get delight candidate.") from exc

    async def respond_delight(
        self,
        request: DelightFeedbackRequest,
    ) -> DelightFeedbackResponse:
        """Record a view, reaction or contextual chat on a delight card."""
        identifier = request.bvid or request.content_id
        if request.response == "view":
            marker = getattr(self.services.database, "mark_delight_notified", None)
            if callable(marker):
                marker(identifier)
            return DelightFeedbackResponse(ok=True, action="viewed", bvid=request.bvid)

        if request.response == "dismiss":
            marker = getattr(self.services.runtime_controller, "mark_delight_seen", None)
            if not callable(marker):
                marker = getattr(self.services.database, "mark_delight_seen", None)
            if callable(marker):
                marker(identifier)
            return DelightFeedbackResponse(ok=True, action="dismissed", bvid=request.bvid)

        if request.response == "chat":
            message = (
                request.message or f"聊聊你为什么觉得「{request.title or identifier}」我会喜欢"
            )
            reply = await self.chat(
                ChatRequest(
                    message=f"[关于惊喜推荐「{request.title or identifier}」的反馈] {message}",
                    session="openclaw",
                    scope="delight",
                    subject_id=identifier,
                    subject_title=request.title,
                )
            )
            return DelightFeedbackResponse(
                ok=True,
                action="chat",
                bvid=request.bvid,
                reply=reply.reply,
            )

        try:
            from openbiliclaw.sources.event_format import build_event

            event = build_event(
                event_type="feedback",
                source_platform=request.source_platform or "web",
                title=request.title or identifier,
                metadata={
                    "bvid": request.bvid,
                    "content_id": request.content_id or identifier,
                    "feedback_type": request.response,
                    "event_namespace": "recommendation",
                    "source": "delight_response",
                    "profile_update_owner": "content_feedback",
                },
            )
            event["ingest_key"] = request.request_id
            receipt = await self.services.event_ingress.accept(event, producer="delight")
            if receipt.accepted != 1 or receipt.rejected:
                raise AdapterOperationError("Delight feedback event was rejected.")
            item_receipt = receipt.items[0]
            query_rows = getattr(self.services.database, "query_event_rows_by_ids", None)
            if callable(query_rows):
                stored_rows = query_rows([item_receipt.event_id])
                if len(stored_rows) != 1:
                    raise AdapterOperationError("Durable delight event could not be read back.")
                raw_metadata = stored_rows[0].get("metadata", {})
                if isinstance(raw_metadata, str):
                    try:
                        raw_metadata = json.loads(raw_metadata) if raw_metadata else {}
                    except (TypeError, ValueError):
                        raw_metadata = {}
                metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                stored_id = _as_text(metadata.get("bvid"), _as_text(metadata.get("content_id")))
                stored_type = _as_text(metadata.get("feedback_type")).lower()
                if stored_id != identifier or stored_type != request.response:
                    raise AdapterValidationError(
                        "request_id was already used for a different delight reaction."
                    )

            execute_write = getattr(self.services.database, "_execute_write", None)
            if callable(execute_write):
                if request.response == "like":
                    execute_write(
                        "UPDATE content_cache SET feedback_type='like', "
                        "feedback_at=CURRENT_TIMESTAMP, "
                        "relevance_score=MAX(COALESCE(relevance_score, 0.5), 0.65) "
                        "WHERE bvid = ? OR content_id = ? OR item_key = ?",
                        (request.bvid, request.content_id or identifier, identifier),
                    )
                else:
                    execute_write(
                        "UPDATE content_cache SET pool_status='purged_by_dislike', "
                        "feedback_type='dislike', feedback_at=CURRENT_TIMESTAMP "
                        "WHERE bvid = ? OR content_id = ? OR item_key = ?",
                        (request.bvid, request.content_id or identifier, identifier),
                    )
            if request.response == "dislike":
                marker = getattr(self.services.runtime_controller, "mark_delight_sent", None)
                if not callable(marker):
                    marker = getattr(self.services.database, "mark_delight_notified", None)
                if callable(marker):
                    marker(identifier)
            immediate = getattr(
                self.services.soul_engine, "record_immediate_feedback_cognition", None
            )
            if item_receipt.inserted and callable(immediate):
                with _suppress_exceptions("delight feedback cognition follow-up"):
                    immediate(
                        feedback_type=request.response,
                        title=request.title or identifier,
                        note="",
                    )
            return DelightFeedbackResponse(
                ok=True,
                action="liked" if request.response == "like" else "disliked",
                bvid=request.bvid,
                event_id=_as_int(getattr(item_receipt, "event_id", 0)),
                duplicate=bool(getattr(item_receipt, "duplicate", False)),
                processing="queued",
            )
        except (AdapterOperationError, AdapterValidationError):
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to respond to delight candidate.") from exc

    async def get_platform_availability(self) -> PlatformAvailabilityResponse:
        """Read the current servable pool inventory by source platform."""
        loader = getattr(self.services.database, "load_pool_platform_availability_async", None)
        if not callable(loader):
            return PlatformAvailabilityResponse()
        try:
            runtime_state_loader = getattr(
                self.services.memory_manager,
                "load_discovery_runtime_state",
                None,
            )
            runtime_state = runtime_state_loader() if callable(runtime_state_loader) else {}
            self_info = (
                runtime_state.get("xhs_self_info", {}) if isinstance(runtime_state, dict) else {}
            )
            nickname = str(self_info.get("nickname", "")) if isinstance(self_info, dict) else ""
            try:
                snapshot = await _maybe_await(loader(xhs_self_nickname=nickname))
            except TypeError:
                snapshot = await _maybe_await(loader())
            return PlatformAvailabilityResponse(
                total_available=max(0, _as_int(getattr(snapshot, "total_available", 0))),
                by_platform={
                    str(name): max(0, _as_int(count))
                    for name, count in dict(getattr(snapshot, "by_platform", {}) or {}).items()
                },
            )
        except Exception as exc:  # pragma: no cover - storage boundary
            raise AdapterOperationError("Failed to read platform availability.") from exc

    async def get_activity_feed(
        self,
        *,
        limit: int = 10,
        before: str = "",
    ) -> ActivityFeedResponse:
        """Build the same compact activity feed used by the current API."""
        if limit <= 0:
            raise AdapterValidationError("activity feed limit must be positive.")
        if limit > 50:
            raise AdapterValidationError("activity feed limit must not exceed 50.")
        try:
            from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

            runtime_status: dict[str, object] = {}
            get_runtime_status = getattr(
                self.services.runtime_controller, "get_runtime_status", None
            )
            if callable(get_runtime_status):
                runtime_status.update(dict(get_runtime_status()))
            get_account_status = getattr(
                self.services.account_sync_service, "get_runtime_status", None
            )
            if callable(get_account_status):
                runtime_status.update(dict(get_account_status()))
            cognition_updates: list[dict[str, object]] = []
            load_updates = getattr(self.services.memory_manager, "load_cognition_updates", None)
            if callable(load_updates):
                cognition_updates = [item for item in load_updates() if isinstance(item, dict)]
            payload = ActivityFeedBuilder(database=self.services.database).build(
                runtime_status=runtime_status,
                cognition_updates=cognition_updates,
                limit=limit,
                before=before,
            )
            raw_items = payload.get("items", [])
            raw_item_list = raw_items if isinstance(raw_items, list) else []
            items = [
                ActivityFeedItem(
                    id=_as_text(item.get("id")),
                    kind=_as_text(item.get("kind")),
                    summary=_as_text(item.get("summary")),
                    detail=_as_text(item.get("detail")),
                    created_at=_as_text(item.get("created_at")),
                    tone=_as_text(item.get("tone"), "info"),
                )
                for item in raw_item_list
                if isinstance(item, dict)
            ]
            return ActivityFeedResponse(
                live_summary=_as_text(payload.get("live_summary")),
                headline=_as_text(payload.get("headline")),
                items=items,
                has_more=_as_bool(payload.get("has_more")),
                next_cursor=_as_text(payload.get("next_cursor")),
            )
        except AdapterValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to build activity feed.") from exc

    async def get_profile_edit_state(self) -> ProfileEditStateResponse:
        """Read the full editable profile overlay state."""
        try:
            from openbiliclaw.soul.overrides import build_edit_state

            raw = await self.services.soul_engine.get_raw_profile()
            effective = await self.services.soul_engine.get_profile()
            payload = build_edit_state(
                raw,
                effective,
                self.services.soul_engine.get_overrides(),
            )
            fields = payload.get("fields", {})
            return ProfileEditStateResponse(
                initialized=_as_bool(payload.get("initialized")),
                fields=dict(fields) if isinstance(fields, dict) else {},
            )
        except Exception as exc:  # pragma: no cover - profile boundary
            if "not initialized" in str(exc).lower():
                return ProfileEditStateResponse(initialized=False)
            raise AdapterOperationError("Failed to read profile edit state.") from exc

    async def edit_profile(self, request: ProfileEditRequest) -> ProfileEditResponse:
        """Apply one deterministic profile overlay edit."""
        try:
            result = await self.services.soul_engine.apply_user_edit(
                target=request.target,
                op=request.op,
                value=request.value,
                parent=request.parent,
                weight=request.weight,
                database=self.services.database,
            )
            state = await self.get_profile_edit_state()
            return ProfileEditResponse(
                ok=_as_bool(result.get("ok", True)) if isinstance(result, dict) else True,
                target=request.target,
                op=request.op,
                edit_state={
                    "initialized": state.initialized,
                    "fields": state.fields,
                },
            )
        except AdapterValidationError:
            raise
        except Exception as exc:  # pragma: no cover - profile boundary
            raise AdapterOperationError("Failed to edit profile.") from exc

    async def save_local(self, request: SavedItemRequest) -> SavedItemResponse:
        """Save one item locally without silently writing to a platform account."""
        service = getattr(self.services, "saved_sync_service", None)
        if service is None:
            raise AdapterOperationError("Saved-list service is unavailable.")
        try:
            from openbiliclaw.saved_sync.models import SavedItemInput

            item = SavedItemInput(
                source_platform=request.source_platform,
                content_id=request.content_id,
                content_url=request.content_url,
                content_type=request.content_type,
                title=request.title,
                author_name=request.author_name,
                cover_url=request.cover_url,
            )
            result = service.save_local(
                request.list_kind,
                item,
                note=request.note,
                auto_sync=False,
            )
            return SavedItemResponse(
                saved=bool(result.saved),
                list_kind=request.list_kind,
                item_key=str(result.item_key),
                sync_status=str(result.sync_status),
                sync_task_id=str(result.sync_task_id),
            )
        except (AdapterOperationError, AdapterValidationError):
            raise
        except Exception as exc:  # pragma: no cover - storage boundary
            raise AdapterOperationError("Failed to save item locally.") from exc

    async def remove_saved(self, request: SavedRemoveRequest) -> SavedRemoveResponse:
        """Remove one local saved-list membership."""
        remover = getattr(self.services.database, "remove_saved_membership", None)
        if not callable(remover):
            raise AdapterOperationError("Saved-list storage is unavailable.")
        try:
            removed = bool(remover(request.list_kind, request.item_key))
            return SavedRemoveResponse(
                removed=removed,
                list_kind=request.list_kind,
                item_key=request.item_key,
            )
        except Exception as exc:  # pragma: no cover - storage boundary
            raise AdapterOperationError("Failed to remove saved item.") from exc

    async def list_saved(self, *, list_kind: str, limit: int = 50) -> SavedListResponse:
        """List local saved memberships and their native-sync status."""
        normalized_kind = list_kind.strip().lower()
        if normalized_kind not in {"favorite", "watch_later"}:
            raise AdapterValidationError("saved list_kind must be favorite or watch_later.")
        if limit <= 0 or limit > 200:
            raise AdapterValidationError("saved list limit must be between 1 and 200.")
        lister = getattr(self.services.database, "list_saved_memberships", None)
        if not callable(lister):
            raise AdapterOperationError("Saved-list storage is unavailable.")
        try:
            rows = lister(normalized_kind, limit=limit)
            return SavedListResponse(
                list_kind=normalized_kind,
                items=[dict(row) for row in rows if isinstance(row, dict)],
                total=len(rows),
            )
        except Exception as exc:  # pragma: no cover - storage boundary
            raise AdapterOperationError("Failed to list saved items.") from exc

    async def sync_saved(self, request: SavedSyncRequest) -> SavedSyncResponse:
        """Run an explicitly authorized native-save synchronization task."""
        service = getattr(self.services, "saved_sync_service", None)
        if service is None:
            raise AdapterOperationError("Saved-list service is unavailable.")
        try:
            created = service.create_sync_task(
                request.list_kind,
                request.item_keys,
                "agent",
            )
            result = await _maybe_await(service.run_sync_task(created.task_id))
            raw_items = getattr(result, "items", ())
            items: list[dict[str, object]] = [
                {
                    "item_key": str(getattr(item, "item_key", "")),
                    "status": str(getattr(item, "status", "")),
                    "resolved_action": str(getattr(item, "resolved_action", "")),
                    "resolved_target": str(getattr(item, "resolved_target", "")),
                    "error_code": str(getattr(item, "error_code", "")),
                    "error_message": str(getattr(item, "error_message", "")),
                }
                for item in raw_items
            ]
            return SavedSyncResponse(task_id=str(created.task_id), items=items)
        except (AdapterOperationError, AdapterValidationError):
            raise
        except Exception as exc:  # pragma: no cover - external state boundary
            raise AdapterOperationError("Failed to synchronize saved items.") from exc

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Run one Socratic dialogue turn and return the agent's reply.

        The bridge keeps the historical ``legacy_direct`` learning ownership,
        but now also writes a durable chat envelope when the storage backend
        supports it.  This gives host reconnects a stable ``turn_id`` without
        taking ownership of the API runtime's queue or worker guard.
        """
        database = getattr(self.services, "database", None)
        create_turn = getattr(database, "create_chat_turn", None)
        get_turn = getattr(database, "get_chat_turn", None)
        complete_turn = getattr(database, "complete_chat_turn", None)
        fail_turn = getattr(database, "fail_chat_turn", None)
        durable = all(callable(item) for item in (create_turn, get_turn, complete_turn, fail_turn))
        turn_id = request.turn_id or (str(uuid4()) if durable else "")
        create_turn_fn = cast("Any", create_turn)
        get_turn_fn = cast("Any", get_turn)
        complete_turn_fn = cast("Any", complete_turn)
        fail_turn_fn = cast("Any", fail_turn)

        if durable and turn_id:
            try:
                existing = get_turn_fn(turn_id)
                if isinstance(existing, dict) and str(existing.get("status", "")) == "completed":
                    return ChatResponse(
                        reply=_as_text(existing.get("reply")),
                        session=request.session,
                        scope=request.scope,
                        turn_id=turn_id,
                        status="completed",
                    )
                if existing is None:
                    create_turn_fn(
                        turn_id=turn_id,
                        message=request.message,
                        session=request.session,
                        scope=request.scope,
                        subject_id=request.subject_id,
                        subject_title=request.subject_title,
                        reply_to_turn_id=request.reply_to_turn_id,
                    )
            except Exception as exc:  # pragma: no cover - storage boundary
                raise AdapterOperationError("Failed to persist chat turn.") from exc
        try:
            from openbiliclaw.soul.dialogue import (
                DialogueLearningMode,
                SocraticDialogue,
            )

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
                learning_mode=DialogueLearningMode.LEGACY_DIRECT,
                database=database if durable else None,
            )
            reply = await dialogue.respond(
                request.message,
                scope=request.scope,
                turn_id=turn_id,
                session=request.session,
            )
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            message = safe_llm_failure_message(exc)
            if durable and turn_id:
                try:
                    fail_turn_fn(turn_id, error=message)
                except Exception:
                    logger.warning("Failed to mark OpenClaw chat turn failed", exc_info=True)
            raise AdapterOperationError(f"Failed to run Socratic dialogue turn: {message}") from exc
        if durable and turn_id:
            try:
                complete_turn_fn(turn_id, reply=str(reply))
            except Exception as exc:  # pragma: no cover - storage boundary
                raise AdapterOperationError(
                    "Chat reply generated but could not be persisted."
                ) from exc
        return ChatResponse(
            reply=str(reply),
            session=request.session,
            scope=request.scope,
            turn_id=turn_id,
            status="completed",
        )

    async def get_chat_history(
        self,
        *,
        session: str = "openclaw",
        scope: str = "",
        limit: int = 50,
    ) -> ChatHistoryResponse:
        """Read durable dialogue history for a host session."""
        if limit <= 0:
            raise AdapterValidationError("chat history limit must be positive.")
        if limit > 200:
            raise AdapterValidationError("chat history limit must not exceed 200.")
        lister = getattr(self.services.database, "list_chat_turns", None)
        if not callable(lister):
            return ChatHistoryResponse(items=[])
        try:
            rows = lister(session=session.strip() or "openclaw", scope=scope.strip(), limit=limit)
            return ChatHistoryResponse(
                items=[_chat_turn_from_row(row) for row in rows if isinstance(row, dict)]
            )
        except Exception as exc:  # pragma: no cover - storage boundary
            raise AdapterOperationError("Failed to read chat history.") from exc

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

    async def respond_interest_probe(
        self,
        request: InterestProbeFeedbackRequest,
    ) -> InterestProbeFeedbackResponse:
        """Record confirm, reject, defer or chat feedback for an interest probe."""
        try:
            speculator = getattr(self.services.soul_engine, "_speculator", None)
            if request.response == "confirm":
                confirm = getattr(speculator, "user_confirm_speculation", None)
                source = request.confirmation_source or (
                    "profile_confirmed" if request.surface == "profile" else "probe_confirmed"
                )
                if not callable(confirm):
                    return InterestProbeFeedbackResponse(
                        ok=False,
                        action="confirmed",
                        domain=request.domain,
                    )
                try:
                    ok = bool(confirm(request.domain, confirmation_source=source))
                except TypeError:
                    ok = bool(confirm(request.domain))
                if ok:
                    self._schedule_speculator_tick(speculator)
                return InterestProbeFeedbackResponse(
                    ok=ok,
                    action="confirmed",
                    domain=request.domain,
                )
            if request.response == "reject":
                reject = getattr(speculator, "user_reject_speculation", None)
                ok = bool(reject(request.domain) if callable(reject) else False)
                return InterestProbeFeedbackResponse(
                    ok=ok,
                    action="rejected",
                    domain=request.domain,
                )
            if request.response == "defer":
                defer = getattr(speculator, "user_defer_speculation", None)
                result = defer(request.domain) if callable(defer) else None
                outcome = _as_text(getattr(result, "outcome", ""))
                return InterestProbeFeedbackResponse(
                    ok=outcome in {"deferred", "exhausted"},
                    action="defer_exhausted" if outcome == "exhausted" else "deferred",
                    domain=request.domain,
                    deferred_until=_as_text(getattr(result, "deferred_until", "")),
                    defer_count=_as_int(getattr(result, "defer_count", 0)),
                )

            message = request.message or f"我想聊聊你猜我可能感兴趣的「{request.domain}」"
            reply = await self.chat(
                ChatRequest(
                    message=f"[关于猜测兴趣「{request.domain}」的反馈] {message}",
                    session="openclaw",
                    scope="interest_probe",
                    subject_id=request.domain,
                    subject_title=request.domain,
                )
            )
            return InterestProbeFeedbackResponse(
                ok=True,
                action="chat",
                domain=request.domain,
                reply=reply.reply,
            )
        except AdapterValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            raise AdapterOperationError("Failed to respond to interest probe.") from exc

    def _schedule_speculator_tick(self, speculator: object) -> None:
        """Refresh probe inventory after confirmation without blocking the host."""
        tick = getattr(speculator, "force_tick", None)
        if not callable(tick):
            return

        async def _run() -> None:
            try:
                profile = await self.services.soul_engine.get_profile()
                runtime_state: object = {}
                loader = getattr(self.services.memory_manager, "load_discovery_runtime_state", None)
                if callable(loader):
                    runtime_state = loader()
                history = (
                    runtime_state.get("probe_feedback_history", [])
                    if isinstance(runtime_state, dict)
                    else []
                )
                try:
                    result = tick(profile, feedback_history=history)
                except TypeError:
                    result = tick(profile)
                await _maybe_await(result)
            except Exception:
                logger.warning("OpenClaw probe inventory refresh failed", exc_info=True)

        task = asyncio.create_task(_run())
        task.add_done_callback(
            lambda completed: completed.exception() if not completed.cancelled() else None
        )

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

            if request.response == "defer":
                defer = getattr(speculator, "user_defer_avoidance", None)
                result = defer(request.domain) if callable(defer) else None
                outcome = _as_text(getattr(result, "outcome", ""))
                return AvoidanceProbeFeedbackResponse(
                    ok=outcome in {"deferred", "exhausted"},
                    action="defer_exhausted" if outcome == "exhausted" else "deferred",
                    domain=request.domain,
                    deferred_until=_as_text(getattr(result, "deferred_until", "")),
                    defer_count=_as_int(getattr(result, "defer_count", 0)),
                )

            message = request.message or f"我想聊聊你猜我可能想避开的「{request.domain}」"
            reply = await self.chat(
                ChatRequest(
                    message=f"[关于避雷方向「{request.domain}」的反馈] {message}",
                    session="openclaw",
                    scope="avoidance_probe",
                    subject_id=request.domain,
                    subject_title=request.domain,
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
            llm_refill_active=self._to_int(runtime_status.get("llm_refill_active", 0)),
            llm_refill_waiting=self._to_int(runtime_status.get("llm_refill_waiting", 0)),
            llm_maintenance_active=self._to_int(runtime_status.get("llm_maintenance_active", 0)),
            llm_maintenance_waiting=self._to_int(runtime_status.get("llm_maintenance_waiting", 0)),
            llm_refill_priority_active=bool(
                runtime_status.get("llm_refill_priority_active", False)
            ),
            inventory_priority_state=str(runtime_status.get("inventory_priority_state", "healthy")),
            last_discovered_count=self._to_int(runtime_status.get("last_discovered_count", 0)),
            last_refresh_at=str(runtime_status.get("last_refresh_at", "")),
            last_account_sync_at=str(runtime_status.get("last_account_sync_at", "")),
            last_account_sync_error=str(runtime_status.get("last_account_sync_error", "")),
        )
