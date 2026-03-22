"""Protocol-neutral request and response DTOs for OpenClaw integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AdapterValidationError

_VALID_FEEDBACK_TYPES = {"like", "dislike", "comment"}


@dataclass(slots=True)
class ProfileResponse:
    """Trimmed profile summary exposed to OpenClaw."""

    initialized: bool
    personality_portrait: str = ""
    core_traits: list[str] = field(default_factory=list)
    deep_needs: list[str] = field(default_factory=list)
    top_interests: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecommendationItem:
    """One recommendation item exposed to OpenClaw."""

    recommendation_id: int
    bvid: str
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    reason: str = ""
    topic_label: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class RecommendationResponse:
    """Recommendation result returned to OpenClaw."""

    items: list[RecommendationItem] = field(default_factory=list)


@dataclass(slots=True)
class FeedbackRequest:
    """Normalized feedback payload received from OpenClaw."""

    recommendation_id: int
    feedback_type: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.recommendation_id <= 0:
            raise AdapterValidationError("recommendation_id must be positive.")
        self.feedback_type = self.feedback_type.strip().lower()
        self.note = self.note.strip()
        if self.feedback_type not in _VALID_FEEDBACK_TYPES:
            raise AdapterValidationError(f"Unsupported feedback type: {self.feedback_type}")
        if self.feedback_type == "comment" and not self.note:
            raise AdapterValidationError("Comment feedback requires note.")


@dataclass(slots=True)
class FeedbackResponse:
    """Feedback acceptance result returned to OpenClaw."""

    ok: bool
    recommendation_id: int
    feedback_type: str


@dataclass(slots=True)
class RuntimeStatusResponse:
    """Trimmed runtime status summary exposed to OpenClaw."""

    initialized: bool
    recommendation_count: int
    pending_signal_events: int
    unread_count: int
    pool_available_count: int = 0
    pool_target_count: int = 0
    last_discovered_count: int = 0
    last_refresh_at: str = ""
    last_account_sync_at: str = ""
    last_account_sync_error: str = ""


@dataclass(slots=True)
class SyncAccountResponse:
    """Account sync summary returned to OpenClaw."""

    synced: bool
    new_event_count: int
    errors: list[str] = field(default_factory=list)
