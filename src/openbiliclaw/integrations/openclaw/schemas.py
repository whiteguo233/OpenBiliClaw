"""Protocol-neutral request and response DTOs for OpenClaw integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AdapterValidationError

_VALID_FEEDBACK_TYPES = {"like", "dislike", "comment", "dismiss"}


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


@dataclass(slots=True)
class DelightItem:
    """One proactive delight recommendation exposed to OpenClaw."""

    bvid: str
    title: str = ""
    delight_reason: str = ""
    delight_score: float = 0.0
    delight_hook: str = ""
    cover_url: str = ""


@dataclass(slots=True)
class DelightResponse:
    """Proactive delight recommendation result returned to OpenClaw."""

    item: DelightItem | None = None


@dataclass(slots=True)
class ChatRequest:
    """Normalized chat payload received from OpenClaw."""

    message: str
    session: str = "openclaw"

    def __post_init__(self) -> None:
        self.message = self.message.strip()
        self.session = self.session.strip() or "openclaw"
        if not self.message:
            raise AdapterValidationError("chat message must not be empty.")


@dataclass(slots=True)
class ChatResponse:
    """Socratic dialogue reply returned to OpenClaw."""

    reply: str
    session: str = "openclaw"


@dataclass(slots=True)
class InterestProbeItem:
    """One speculative interest hypothesis the agent wants the user to confirm.

    ``question`` is a ready-to-ask prompt OpenClaw can pose to the user as-is;
    ``domain`` / ``category`` / ``reason`` / ``confidence`` / ``specifics``
    expose the raw hypothesis so the agent can rephrase if it prefers.
    """

    domain: str
    category: str = ""
    reason: str = ""
    confidence: float = 0.0
    weight: float = 0.0
    experience_mode: str = ""
    entry_load: str = ""
    specifics: list[str] = field(default_factory=list)
    question: str = ""


@dataclass(slots=True)
class InterestProbeResponse:
    """Next interest-confirmation probe returned to OpenClaw."""

    probe: InterestProbeItem | None = None
