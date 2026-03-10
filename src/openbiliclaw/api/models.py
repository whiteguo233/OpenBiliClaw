"""Pydantic models for the local backend API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BehaviorEventIn(BaseModel):
    """One behavior event reported by the extension."""

    type: str
    url: str = ""
    title: str = ""
    timestamp: int
    context: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class BehaviorEventBatchIn(BaseModel):
    """Batch payload used by the service worker."""

    events: list[BehaviorEventIn]


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str
    service: str


class RecommendationOut(BaseModel):
    """Recommendation payload exposed to the popup."""

    id: int
    bvid: str
    title: str = ""
    up_name: str = ""
    expression: str = ""
    topic_label: str = ""
    presented: bool = False


class RecommendationListResponse(BaseModel):
    """Wrapper response for recommendation lists."""

    items: list[RecommendationOut]


class RuntimeStatusResponse(BaseModel):
    """Runtime summary for popup and background status checks."""

    initialized: bool
    recommendation_count: int
    pending_signal_events: int
    last_refresh_at: str = ""
    last_notification_at: str = ""
    unread_count: int


class PendingNotificationOut(BaseModel):
    """One notification-worthy recommendation."""

    recommendation_id: int
    bvid: str
    title: str = ""
    reason: str = ""


class PendingNotificationResponse(BaseModel):
    """Wrapper for a pending notification candidate."""

    item: PendingNotificationOut | None = None


class PendingCognitionUpdateOut(BaseModel):
    """One cognition update worthy of notifying in the extension."""

    id: str
    kind: str
    summary: str


class PendingCognitionUpdateResponse(BaseModel):
    """Wrapper for a pending cognition update."""

    item: PendingCognitionUpdateOut | None = None


class NotificationAckIn(BaseModel):
    """Acknowledge one browser notification delivery."""

    bvid: str


class NotificationAckResponse(BaseModel):
    """Response after marking a notification as delivered."""

    ok: bool
    bvid: str


class CognitionUpdateSeenIn(BaseModel):
    """Acknowledge one cognition update as seen/notified."""

    id: str


class CognitionUpdateSeenResponse(BaseModel):
    """Response after marking a cognition update as seen."""

    ok: bool
    id: str


class ProfileSummaryResponse(BaseModel):
    """Lightweight soul profile exposed to the popup."""

    initialized: bool
    personality_portrait: str = ""
    core_traits: list[str] = Field(default_factory=list)
    deep_needs: list[str] = Field(default_factory=list)
    top_interests: list[str] = Field(default_factory=list)
    recent_cognition_updates: list[str] = Field(default_factory=list)


class EventIngestResponse(BaseModel):
    """Response after accepting a batch of events."""

    accepted: int


class FeedbackIn(BaseModel):
    """Feedback payload submitted from CLI-compatible clients."""

    recommendation_id: int
    feedback_type: str
    note: str = ""


class FeedbackResponse(BaseModel):
    """Response after accepting recommendation feedback."""

    ok: bool
    recommendation_id: int
    feedback_type: str


class ChatIn(BaseModel):
    """Popup chat request."""

    message: str


class ChatResponse(BaseModel):
    """Popup chat response."""

    reply: str
