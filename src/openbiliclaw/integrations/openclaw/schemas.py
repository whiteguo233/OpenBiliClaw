"""Protocol-neutral request and response DTOs for the agent bridge.

The public names retain ``OpenClaw`` compatibility because that was the first
host shipped by the project.  The payloads themselves are intentionally
host-neutral so Hermes, WorkBuddy and other local agent hosts can consume the
same bridge without copying business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import AdapterValidationError

_VALID_FEEDBACK_TYPES = {"like", "dislike", "comment", "dismiss"}
_VALID_INTEREST_RESPONSES = {"confirm", "reject", "defer", "chat"}
_VALID_AVOIDANCE_RESPONSES = {"confirm", "reject", "defer", "chat"}
_VALID_DELIGHT_RESPONSES = {"view", "like", "dislike", "chat", "dismiss"}
_VALID_SAVED_LIST_KINDS = {"favorite", "watch_later"}


@dataclass(slots=True)
class CapabilitiesResponse:
    """Versioned capability manifest for host negotiation."""

    protocol_version: str
    adapter_version: str
    host_names: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)


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
    """One multi-source recommendation item exposed to an agent host.

    ``bvid`` and ``up_name`` are retained as compatibility aliases for the
    original Bilibili-only contract.  New consumers should use
    ``content_id``, ``source_platform`` and ``author_name``.
    """

    recommendation_id: int
    bvid: str
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    reason: str = ""
    topic_label: str = ""
    confidence: float = 0.0
    item_key: str = ""
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    author_name: str = ""
    published_at: str = ""
    published_label: str = ""
    content_type: str = "video"
    body_text: str = ""
    expression: str = ""
    presented: bool = False
    feedback_type: str = ""
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    danmaku_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0
    rating_score: float = 0.0
    rating_count: int = 0
    source_rank: int = 0
    up_mid: int = 0
    batch_ref: str = ""
    stream_ref: str = ""


@dataclass(slots=True)
class RecommendationResponse:
    """Recommendation result returned to OpenClaw."""

    items: list[RecommendationItem] = field(default_factory=list)
    batch_ref: str = ""
    stream_ref: str = ""
    request_id: str = ""
    director_mode: str = "off"
    director_strategy: str = ""
    requires_generation: bool = False


@dataclass(slots=True)
class PresentationResponse:
    """Acknowledgement of cards actually visible to the user, not tool delivery."""

    batch_ref: str
    ack_id: str
    item_keys: list[str] = field(default_factory=list)
    state_revision: int = 0
    event_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class FeedbackRequest:
    """Normalized feedback payload received from OpenClaw."""

    recommendation_id: int
    feedback_type: str
    note: str = ""
    request_id: str = ""
    batch_ref: str = ""

    def __post_init__(self) -> None:
        if self.recommendation_id <= 0:
            raise AdapterValidationError("recommendation_id must be positive.")
        self.feedback_type = self.feedback_type.strip().lower()
        self.note = self.note.strip()
        self.request_id = self.request_id.strip()
        if not self.request_id:
            raise AdapterValidationError("request_id is required.")
        if len(self.request_id) > 400:
            raise AdapterValidationError("request_id is too long.")
        if len(self.batch_ref) > 256:
            raise AdapterValidationError("batch_ref is too long.")
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
    event_id: int = 0
    duplicate: bool = False
    processing: str = "completed"


@dataclass(slots=True)
class RuntimeStatusResponse:
    """Trimmed runtime status summary exposed to OpenClaw."""

    initialized: bool
    recommendation_count: int
    pending_signal_events: int
    unread_count: int
    pool_available_count: int = 0
    pool_target_count: int = 0
    llm_refill_active: int = 0
    llm_refill_waiting: int = 0
    llm_maintenance_active: int = 0
    llm_maintenance_waiting: int = 0
    llm_refill_priority_active: bool = False
    inventory_priority_state: str = "healthy"
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
    """One proactive multi-source delight recommendation."""

    bvid: str = ""
    title: str = ""
    delight_reason: str = ""
    delight_score: float = 0.0
    delight_hook: str = ""
    cover_url: str = ""
    item_key: str = ""
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    published_at: str = ""
    published_label: str = ""
    content_type: str = "video"
    body_text: str = ""
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    danmaku_count: int = 0
    favorite_count: int = 0
    rating_score: float = 0.0
    rating_count: int = 0
    source_rank: int = 0


@dataclass(slots=True)
class DelightResponse:
    """Proactive delight recommendation result returned to OpenClaw."""

    item: DelightItem | None = None


@dataclass(slots=True)
class ChatRequest:
    """Normalized chat payload received from an agent host.

    ``turn_id`` is optional for compatibility.  The adapter creates one when
    the configured database supports durable chat turns.
    """

    message: str
    session: str = "openclaw"
    scope: str = "chat"
    turn_id: str = ""
    subject_id: str = ""
    subject_title: str = ""
    reply_to_turn_id: str = ""

    def __post_init__(self) -> None:
        self.message = self.message.strip()
        self.session = self.session.strip() or "openclaw"
        self.scope = self.scope.strip() or "chat"
        self.turn_id = self.turn_id.strip()
        self.subject_id = self.subject_id.strip()
        self.subject_title = self.subject_title.strip()
        self.reply_to_turn_id = self.reply_to_turn_id.strip()
        if not self.message:
            raise AdapterValidationError("chat message must not be empty.")


@dataclass(slots=True)
class ChatResponse:
    """Socratic dialogue reply returned to OpenClaw."""

    reply: str
    session: str = "openclaw"
    scope: str = "chat"
    turn_id: str = ""
    status: str = "completed"


@dataclass(slots=True)
class ChatTurnItem:
    """One durable chat turn in the host-neutral history contract."""

    turn_id: str
    session: str = "openclaw"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""
    reply_to_turn_id: str = ""
    message: str = ""
    reply: str = ""
    status: str = "pending"
    error: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ChatHistoryResponse:
    """Durable chat history returned in display order."""

    items: list[ChatTurnItem] = field(default_factory=list)


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


@dataclass(slots=True)
class InterestProbeFeedbackRequest:
    """User response to a speculative-interest probe."""

    domain: str
    response: str
    message: str = ""
    confirmation_source: str = ""
    surface: str = "agent"

    def __post_init__(self) -> None:
        self.domain = self.domain.strip()
        self.response = self.response.strip().lower()
        self.message = self.message.strip()
        self.confirmation_source = self.confirmation_source.strip()
        self.surface = self.surface.strip().lower() or "agent"
        if not self.domain:
            raise AdapterValidationError("interest probe domain must not be empty.")
        if self.response not in _VALID_INTEREST_RESPONSES:
            allowed = ", ".join(sorted(_VALID_INTEREST_RESPONSES))
            raise AdapterValidationError(f"interest probe response must be one of: {allowed}.")


@dataclass(slots=True)
class InterestProbeFeedbackResponse:
    """Result of recording user feedback for a speculative interest probe."""

    ok: bool
    action: str
    domain: str
    reply: str = ""
    deferred_until: str = ""
    defer_count: int = 0


@dataclass(slots=True)
class AvoidanceProbeItem:
    """One speculative avoidance hypothesis the agent wants the user to confirm."""

    domain: str
    reason: str = ""
    confidence: float = 0.0
    weight: float = 0.0
    source_mode: str = ""
    source_signal: str = ""
    experience_mode: str = ""
    entry_load: str = ""
    specifics: list[str] = field(default_factory=list)
    question: str = ""


@dataclass(slots=True)
class AvoidanceProbeResponse:
    """Next avoidance-confirmation probe returned to OpenClaw."""

    probe: AvoidanceProbeItem | None = None


@dataclass(slots=True)
class AvoidanceProbeFeedbackRequest:
    """User response to a speculative avoidance probe."""

    domain: str
    response: str
    message: str = ""

    def __post_init__(self) -> None:
        self.domain = self.domain.strip()
        self.response = self.response.strip().lower()
        self.message = self.message.strip()
        if not self.domain:
            raise AdapterValidationError("avoidance probe domain must not be empty.")
        if self.response not in _VALID_AVOIDANCE_RESPONSES:
            allowed = ", ".join(sorted(_VALID_AVOIDANCE_RESPONSES))
            raise AdapterValidationError(f"avoidance probe response must be one of: {allowed}.")


@dataclass(slots=True)
class AvoidanceProbeFeedbackResponse:
    """Result of recording user feedback for a speculative avoidance probe."""

    ok: bool
    action: str
    domain: str
    reply: str = ""
    deferred_until: str = ""
    defer_count: int = 0


@dataclass(slots=True)
class DelightFeedbackRequest:
    """User action on a proactive delight recommendation."""

    bvid: str = ""
    response: str = ""
    title: str = ""
    message: str = ""
    request_id: str = ""
    content_id: str = ""
    source_platform: str = ""

    def __post_init__(self) -> None:
        self.bvid = self.bvid.strip()
        self.response = self.response.strip().lower()
        self.title = self.title.strip()
        self.message = self.message.strip()
        self.request_id = self.request_id.strip()
        self.content_id = self.content_id.strip()
        self.source_platform = self.source_platform.strip().lower()
        if not self.bvid and not self.content_id:
            raise AdapterValidationError("delight content identifier is required.")
        if self.response not in _VALID_DELIGHT_RESPONSES:
            allowed = ", ".join(sorted(_VALID_DELIGHT_RESPONSES))
            raise AdapterValidationError(f"delight response must be one of: {allowed}.")
        if self.response in {"like", "dislike", "dismiss"} and not self.request_id:
            raise AdapterValidationError("request_id is required for delight feedback.")
        if len(self.request_id) > 400:
            raise AdapterValidationError("request_id is too long.")


@dataclass(slots=True)
class DelightFeedbackResponse:
    """Result of a delight card action."""

    ok: bool
    action: str
    bvid: str = ""
    reply: str = ""
    event_id: int = 0
    duplicate: bool = False
    processing: str = "completed"


@dataclass(slots=True)
class PlatformAvailabilityResponse:
    """Servable pool inventory split by canonical source platform."""

    total_available: int = 0
    by_platform: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ActivityFeedItem:
    """One recent user-visible runtime activity item."""

    id: str
    kind: str
    summary: str
    detail: str = ""
    created_at: str = ""
    tone: str = "info"


@dataclass(slots=True)
class ActivityFeedResponse:
    """Aggregated activity feed returned to an agent host."""

    live_summary: str = ""
    headline: str = ""
    items: list[ActivityFeedItem] = field(default_factory=list)
    has_more: bool = False
    next_cursor: str = ""


@dataclass(slots=True)
class ProfileEditRequest:
    """One deterministic user profile overlay edit."""

    target: str
    op: str
    value: str | float | None = None
    parent: str = ""
    weight: float | None = None

    def __post_init__(self) -> None:
        self.target = self.target.strip()
        self.op = self.op.strip().lower()
        self.parent = self.parent.strip()
        if not self.target:
            raise AdapterValidationError("profile edit target must not be empty.")
        if not self.op:
            raise AdapterValidationError("profile edit operation must not be empty.")


@dataclass(slots=True)
class ProfileEditResponse:
    """Result of a profile edit, including fresh edit state when available."""

    ok: bool
    target: str = ""
    op: str = ""
    edit_state: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ProfileEditStateResponse:
    """Full editable profile state and user-overlay drift information."""

    initialized: bool = False
    fields: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SavedItemRequest:
    """Local-first save request for a multi-source content item."""

    list_kind: str
    source_platform: str
    content_id: str = ""
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        self.list_kind = self.list_kind.strip().lower()
        self.source_platform = self.source_platform.strip().lower()
        self.content_id = self.content_id.strip()
        self.content_url = self.content_url.strip()
        self.content_type = self.content_type.strip() or "video"
        self.title = self.title.strip()
        self.author_name = self.author_name.strip()
        self.cover_url = self.cover_url.strip()
        self.note = self.note.strip()
        if self.list_kind not in _VALID_SAVED_LIST_KINDS:
            allowed = ", ".join(sorted(_VALID_SAVED_LIST_KINDS))
            raise AdapterValidationError(f"saved list_kind must be one of: {allowed}.")
        if not self.source_platform:
            raise AdapterValidationError("saved source_platform must not be empty.")
        if not self.content_id and not self.content_url:
            raise AdapterValidationError("saved content_id or content_url is required.")


@dataclass(slots=True)
class SavedItemResponse:
    """Result of a local-first save operation."""

    saved: bool
    list_kind: str
    item_key: str
    sync_status: str = "pending"
    sync_task_id: str = ""


@dataclass(slots=True)
class SavedListResponse:
    """Saved items in one local list."""

    list_kind: str
    items: list[dict[str, object]] = field(default_factory=list)
    total: int = 0


@dataclass(slots=True)
class SavedRemoveRequest:
    """Remove one item from a local saved list."""

    list_kind: str
    item_key: str

    def __post_init__(self) -> None:
        self.list_kind = self.list_kind.strip().lower()
        self.item_key = self.item_key.strip()
        if self.list_kind not in _VALID_SAVED_LIST_KINDS:
            allowed = ", ".join(sorted(_VALID_SAVED_LIST_KINDS))
            raise AdapterValidationError(f"saved list_kind must be one of: {allowed}.")
        if not self.item_key:
            raise AdapterValidationError("saved item_key must not be empty.")


@dataclass(slots=True)
class SavedRemoveResponse:
    """Result of removing a local saved-list membership."""

    removed: bool
    list_kind: str
    item_key: str


@dataclass(slots=True)
class SavedSyncResponse:
    """Result of an explicit native-save synchronization task."""

    task_id: str
    items: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class SavedSyncRequest:
    """Explicit native-save synchronization request."""

    list_kind: str
    item_keys: list[str] = field(default_factory=list)
    allow_state_changing: bool = False

    def __post_init__(self) -> None:
        self.list_kind = self.list_kind.strip().lower()
        self.item_keys = [str(item).strip() for item in self.item_keys if str(item).strip()]
        if not isinstance(self.allow_state_changing, bool):
            raw = str(self.allow_state_changing).strip().lower()
            self.allow_state_changing = raw in {"1", "true", "yes", "on"}
        if self.list_kind not in _VALID_SAVED_LIST_KINDS:
            allowed = ", ".join(sorted(_VALID_SAVED_LIST_KINDS))
            raise AdapterValidationError(f"saved list_kind must be one of: {allowed}.")
        if not self.allow_state_changing:
            raise AdapterValidationError("native save sync requires allow_state_changing=true.")
