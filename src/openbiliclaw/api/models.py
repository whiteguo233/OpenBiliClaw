"""Pydantic models for the local backend API."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from openbiliclaw.api.source_auth.contract import SourceAuthContract, SourceCapabilityAuth
from openbiliclaw.saved_sync.identity import canonical_source_platform, make_item_key
from openbiliclaw.sources.platforms import CANONICAL_SOURCE_FAMILIES, normalize_source_platform

NativeSaveStatusOut = Literal[
    "pending",
    "syncing",
    "synced",
    "already_synced",
    "login_required",
    "unsupported",
    "rate_limited",
    "extension_required",
    "failed",
]
NativeSaveActionOut = Literal["favorite", "watch_later"]
_SAVED_PLATFORM_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_URL_FALLBACK_ID_RE = re.compile(r"[0-9a-f]{24}")
_ZHIHU_TYPED_CONTENT_ID_RE = re.compile(r"(?:question|answer|article):[0-9]+")
IdempotencyKey = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=400),
]


def _has_unicode_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _has_identity_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


def _validate_http_url(value: str) -> str:
    if _has_identity_whitespace(value) or _has_unicode_control(value):
        raise ValueError("URL fields must not contain whitespace or control characters")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL fields must use a valid absolute HTTP(S) URL") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or hostname is None:
        raise ValueError("URL fields must use a valid absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL fields must not contain credentials")
    if port is not None and port <= 0:
        raise ValueError("URL fields must use a valid TCP port")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("URL fields must contain a valid hostname") from exc
        labels = ascii_hostname.removesuffix(".").split(".")
        if (
            not ascii_hostname
            or len(ascii_hostname.removesuffix(".")) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[A-Za-z0-9-]+", label) is None
                for label in labels
            )
        ):
            raise ValueError("URL fields must contain a valid hostname") from None
    return value


class BehaviorEventIn(BaseModel):
    """One behavior event reported by the extension."""

    type: str
    url: str = ""
    title: str = ""
    timestamp: int
    # Empty means the client omitted the field.  The endpoint preserves the
    # legacy B站 fallback but records that it was not an authoritative tag.
    source_platform: str = ""
    context: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    event_id: IdempotencyKey
    # v0.3.x event-satisfaction signal: dwell on video-page exit. Either
    # top-level or `metadata.watch_seconds` is accepted; the endpoint
    # folds top-level into metadata before persistence so the storage
    # classifier reads from a single canonical location.
    watch_seconds: float | None = None
    video_duration_seconds: float | None = None


class BehaviorEventBatchIn(BaseModel):
    """Batch payload used by the service worker."""

    events: list[BehaviorEventIn]


class HealthResponse(BaseModel):
    """Health-check response."""

    status: str
    service: str
    profile_ready: bool | None = None
    lan_ip: str | None = None
    # v0.3.95+: surfaces whether the embedding service built successfully.
    # ``False`` means semantic dedup / diversity is degraded (recommendations
    # may repeat near-identical content under different ids) — the popup
    # turns this into a one-click "enable local Ollama" banner.
    embedding_ready: bool | None = None
    # issue #170: distinguish "provider registered" from "default model chain
    # actually callable". ``llm_registered`` mirrors the startup registry
    # build; ``llm_callable`` is the latest real capability signal where one
    # exists (currently persisted by ``login codex --import`` for Codex
    # OAuth). ``None`` means "no live signal yet", not "broken".
    llm_registered: bool | None = None
    llm_callable: bool | None = None


class ProjectStatsResponse(BaseModel):
    """Public project metadata shown by local browser surfaces."""

    github_stars: int | None = None
    stale: bool = False
    source: Literal["github", "cache", "unavailable"] = "unavailable"


class InitStageProgressOut(BaseModel):
    """Fine-grained progress inside a running stage (init-progress spec).

    Additive/optional: only present while a stage exposes sub-progress (e.g.
    stage 2 chunked preference analysis). Absent on stages with no natural
    progress points or on a status produced by an older backend.
    """

    done: int = 0
    total: int = 0
    note: str | None = None
    # ``indeterminate`` means the backend is inside one long operation with
    # no honest item count (for example one LLM request). elapsed/max still
    # provide a bounded countdown without inventing a percentage.
    mode: str = "determinate"  # determinate | indeterminate
    elapsed_seconds: int | None = None
    max_seconds: int | None = None


class InitStageOut(BaseModel):
    """One stage of guided init (gui-init spec API shape)."""

    n: int
    label: str
    status: str  # pending | running | ok | warning | failed
    reason: str | None = None
    # Optional (backward-compatible): intra-stage sub-progress + typical
    # duration hint. Old stages_json rows lack these; both default to None.
    progress: InitStageProgressOut | None = None
    eta_seconds: int | None = None


class InitPrerequisitesOut(BaseModel):
    """Pre-init checklist surfaced to the UI."""

    bilibili_logged_in: bool = False
    bilibili_check: str = "checking"  # ok | failed | checking
    bilibili_detail: str = ""  # why the last probe failed ("" when ok)
    llm_ready: bool = False
    embedding_ready: bool = False
    # Classified cause when embedding_ready is False, so the UI can say
    # WHY instead of a dead retry (v0.3.155+): ok | disabled | misconfigured |
    # not_running | model_missing | model_broken | model_path_encoding |
    # disk_full | network | model_oom | provider_error | checking | error.
    embedding_check: str = "ok"
    embedding_detail: str = ""  # human-readable hint ("" when ok/disabled)
    # Live pull progress while a one-click repair is downloading the model
    # (embedding_check == "repairing"), so init pages can render a real
    # progress indicator instead of an opaque wait. total may be 0 while
    # Ollama resolves the manifest.
    embedding_repair_running: bool = False
    embedding_repair_completed: int = 0
    embedding_repair_total: int = 0
    ollama_phase: str = "ready"
    embedding_pull_status: str = ""
    embedding_required: bool = False
    enabled_platforms: list[str] = Field(default_factory=list)
    # Per-source, per-capability readiness projected from the same backend
    # contract as /api/sources/status.  Guided-init clients use ``bootstrap``;
    # they must not equate anonymous discovery readiness with account readiness.
    source_capabilities: dict[str, dict[str, SourceCapabilityAuth]] = Field(default_factory=dict)


class InitStatusOut(BaseModel):
    """Authoritative guided-init status / progress (gui-init spec API shape)."""

    initialized: bool = False
    running: bool = False
    run_id: str | None = None
    sequence: int = 0
    current_stage: int = 0
    total_stages: int = 4
    stages: list[InitStageOut] = Field(default_factory=list)
    partial_success: bool = False
    can_start: bool = False
    can_manage: bool = False
    prerequisites: InitPrerequisitesOut = Field(default_factory=InitPrerequisitesOut)
    reason: str = "none"
    detail: str = ""
    # Backward-compatible alias of ``last_heartbeat_at``. New clients must use
    # the explicit liveness/progress clocks below instead of treating a heartbeat
    # as proof that useful work advanced.
    last_activity: str = ""
    # Explicit liveness/progress clocks. ``last_activity`` remains as a
    # compatibility alias of ``last_heartbeat_at`` for older clients.
    last_heartbeat_at: str = ""
    last_progress_at: str = ""
    progress_sequence: int = 0


class RecommendationOut(BaseModel):
    """Recommendation payload exposed to the popup."""

    id: int
    bvid: str
    item_key: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    expression: str = ""
    topic_label: str = ""
    presented: bool = False
    feedback_type: str = ""
    # Multi-source fields (additive, backward-compatible)
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    published_at: str = ""
    published_label: str = ""
    # Text-first sources (X tweet/thread): the popup renders a no-cover
    # text card from body_text/title when content_type is tweet/thread or
    # cover_url is empty.
    content_type: str = "video"
    body_text: str = ""
    # Desktop card metadata (additive for issue #75; extension popup ignores unknown keys).
    duration: int = 0
    view_count: int = 0
    like_count: int = 0
    danmaku_count: int = 0
    # Cross-platform engagement counts (issue #79): text-first sources like
    # Zhihu have no view/danmaku but do carry favorites/comments — surface them
    # so the card stats row is not left with a lone like count.
    favorite_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    rating_score: float = 0.0
    rating_count: int = 0
    source_rank: int = 0
    up_mid: int = 0


class RecommendationListResponse(BaseModel):
    """Wrapper response for recommendation lists."""

    items: list[RecommendationOut]


ContentHistoryCategory = Literal["clicked", "shown", "removed"]
ContentHistoryContext = Literal["favorite", "watch_later", "dismiss", "dislike"]


class ContentHistoryContextOut(BaseModel):
    """Latest state for one removal context attached to a history card."""

    context: ContentHistoryContext
    occurred_at: str
    restored: bool = False


class ContentHistoryItemOut(BaseModel):
    """One canonical item in a bounded content-history category."""

    item_key: str
    source_platform: str
    content_id: str
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""
    body_text: str = ""
    recommendation_id: int | None = None
    occurred_at: str = ""
    context: str = ""
    restored: bool = False
    contexts: list[ContentHistoryContextOut] = Field(default_factory=list)


class ContentHistoryResponse(BaseModel):
    """One paginated 30-day history category."""

    category: ContentHistoryCategory
    items: list[ContentHistoryItemOut]
    total: int
    retention_days: int = 30
    next_cursor: str | None = None
    has_more: bool = False


class RecommendationReshuffleResponse(BaseModel):
    """Immediate recommendation reshuffle result."""

    items: list[RecommendationOut]


class _PlatformScopedRecommendationIn(BaseModel):
    """Shared exclusions plus the optional canonical platform scope.

    ``source_platform`` is additive: omitting it (or sending an empty
    string) keeps the pre-existing cross-platform behaviour, so clients
    that predate platform tabs are unaffected. Aliases are canonicalized
    here — the API boundary is the only place that accepts them — and an
    unknown value is rejected with 422 rather than quietly degrading to
    "全部" or bilibili, which would hand the user a tab full of content
    from a platform they did not ask for.
    """

    excluded_bvids: list[str] = Field(default_factory=list)
    source_platform: str = ""

    @field_validator("source_platform", mode="before")
    @classmethod
    def _canonicalize_source_platform(cls, value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        canonical = normalize_source_platform(raw)
        if canonical not in CANONICAL_SOURCE_FAMILIES:
            supported = ", ".join(CANONICAL_SOURCE_FAMILIES)
            raise ValueError(f"unsupported source_platform {raw!r}; expected one of: {supported}")
        return canonical


class RecommendationReshuffleIn(_PlatformScopedRecommendationIn):
    """Optional visible-card exclusions and platform scope for a reshuffle."""


class RecommendationAppendIn(_PlatformScopedRecommendationIn):
    """Request payload for appending another recommendation page."""


class PlatformAvailabilityResponse(BaseModel):
    """Servable candidate inventory, split by canonical source platform.

    ``total_available`` matches the "还有 N 条" count and always equals the
    sum of ``by_platform``; both come from one isolated storage snapshot.
    Platforms with no stock may be omitted — clients render ``0`` for an
    enabled platform whose key is absent.
    """

    total_available: int = 0
    by_platform: dict[str, int] = Field(default_factory=dict)


class RecommendationRefreshResponse(BaseModel):
    """Result of one explicit recommendation refresh request."""

    ok: bool
    accepted: bool
    state: str = "idle"
    reason: str = ""


class RuntimeStatusResponse(BaseModel):
    """Runtime summary for popup and background status checks."""

    initialized: bool
    recommendation_count: int
    pending_signal_events: int
    last_refresh_at: str = ""
    last_notification_at: str = ""
    unread_count: int
    pool_available_count: int = 0
    pool_raw_count: int = 0
    pool_pending_count: int = 0
    pool_pending_eval_count: int = 0
    pool_evaluated_pending_count: int = 0
    pool_target_count: int = 0
    candidate_eval_state: str = "idle"
    candidate_eval_workers: int = 0
    candidate_eval_in_flight: int = 0
    candidate_eval_pending: int = 0
    candidate_eval_backoff_until: float = 0.0
    candidate_eval_last_error: str = ""
    candidate_eval_last_batch_seconds: float = 0.0
    candidate_eval_last_cached: int = 0
    candidate_eval_last_rejected: int = 0
    expression_pending_count: int = 0
    expression_batch_state: str = "idle"
    expression_batch_deadline: float = 0.0
    expression_last_completed: int = 0
    expression_last_error: str = ""
    llm_total_concurrency: int = 0
    llm_background_concurrency: int = 0
    llm_total_active: int = 0
    llm_total_waiting: int = 0
    llm_background_active: int = 0
    llm_background_waiting: int = 0
    llm_refill_active: int = 0
    llm_refill_waiting: int = 0
    llm_maintenance_active: int = 0
    llm_maintenance_waiting: int = 0
    llm_refill_priority_active: bool = False
    inventory_priority_state: str = "healthy"
    last_discovered_count: int = 0
    last_replenished_count: int = 0
    recent_pool_topics: list[str] = Field(default_factory=list)
    manual_refresh_state: str = "idle"
    manual_refresh_message: str = ""
    last_account_sync_at: str = ""
    last_account_sync_error: str = ""
    last_account_sync_error_kind: str = ""
    last_account_sync_issues: list[dict[str, str]] = Field(default_factory=list)
    last_account_sync_message: str = ""
    last_account_sync_severity: str = ""
    event_lane_depth: int = 0
    event_lane_active: bool = False
    event_lane_paused: bool = False
    event_lane_last_error: str = ""
    event_lane_processed: int = 0
    chat_reply_depth: int = 0
    chat_reply_active: bool = False
    chat_reply_last_error: str = ""
    chat_reply_processed: int = 0
    image_fetch_active: int = 0
    image_fetch_waiting: int = 0
    image_fetch_inflight_keys: int = 0
    image_fetch_upstream_started: int = 0
    image_fetch_singleflight_joins: int = 0
    image_fetch_peak_active: int = 0
    image_fetch_peak_background: int = 0
    auto_update_enabled: bool = False
    install_mode: str = ""
    current_version: str = ""
    latest_remote_version: str = ""
    last_update_check_at: str = ""
    last_update_error: str = ""
    backend_update_state: str = "unknown"
    backend_update_reason: str = "none"


class ActivityFeedItemOut(BaseModel):
    """One recent user-visible activity item for the popup."""

    id: str
    kind: str
    summary: str
    detail: str = ""
    created_at: str = ""
    tone: str = "info"


class ActivityFeedResponse(BaseModel):
    """Aggregated activity feed for the popup activity card."""

    live_summary: str = ""
    headline: str = ""
    items: list[ActivityFeedItemOut] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str = ""


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


class PendingDelightOut(BaseModel):
    """One proactive delight recommendation."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    delight_reason: str = ""
    delight_score: float = 0.0
    delight_hook: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    published_at: str = ""
    published_label: str = ""
    content_type: str = "video"
    body_text: str = ""
    # Engagement stats (from content_cache), so the delight card can show the
    # same ▶ / 👍 / 💬 / 🔁 metadata row as the recommendation grid. 0 = unknown /
    # not fetched (platforms that don't populate a metric render nothing).
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    favorite_count: int = 0
    rating_score: float = 0.0
    rating_count: int = 0
    source_rank: int = 0


class PendingDelightResponse(BaseModel):
    """Wrapper for a pending delight candidate."""

    item: PendingDelightOut | None = None


class DelightAckIn(BaseModel):
    """Acknowledge delivery of a delight notification."""

    bvid: str


class DelightAckResponse(BaseModel):
    """Response after marking a delight notification as delivered."""

    ok: bool
    bvid: str


class DelightResponseIn(BaseModel):
    """One idempotent user action on a proactive delight card."""

    bvid: str
    response: str
    title: str = ""
    message: str = ""
    request_id: str = Field(default="", max_length=400)


class BilibiliCookieIn(BaseModel):
    """Cookie sync payload from the browser extension.

    Lets the extension push the user's live bilibili.com session cookies
    to the backend (writes to data/bilibili_cookie.json + config.toml's
    [bilibili].cookie). Replaces the manual F12 → copy → paste flow.
    """

    cookie: str = Field(
        ...,
        description="Cookie header string ('SESSDATA=...; bili_jct=...; ...').",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )
    validate_with_bilibili: bool = Field(
        default=True,
        description=(
            "**Accepted but ignored.** The live check always runs. Kept on the "
            "wire because installed extensions send it on every cookie sync and "
            "rejecting the key would 422 them into a silent sync failure — but "
            "no request may lower the validation strength, which is the same "
            "reason the unified endpoint carries no such flag at all. Sending "
            "false once persisted a structurally complete, dead cookie with the "
            "probe never called."
        ),
    )


class BilibiliCookieResponse(BaseModel):
    """Result of a cookie-sync attempt.

    ``error_code`` lets the extension pick a smart retry cadence
    (network errors → quick retry, expired cookie → wait for next
    login). Empty when ``ok=True``.
    """

    ok: bool
    authenticated: bool
    username: str = ""
    user_id: int = 0
    message: str = ""
    # v0.3.42+ machine-readable code for the extension to branch retry
    # logic on. One of:
    #   ""                       — success
    #   "empty_cookie"           — payload was empty
    #   "cookie_invalid"         — Bilibili says cookie is bad / expired
    #   "validation_network"     — backend couldn't reach api.bilibili.com
    error_code: str = ""


class DouyinCookieIn(BaseModel):
    """Cookie sync payload for Douyin direct-cookie discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from douyin.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class DouyinCookieResponse(BaseModel):
    """Result of syncing a Douyin Cookie header."""

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    message: str = ""
    error_code: str = ""


class XCookieIn(BaseModel):
    """Cookie sync payload for X (Twitter) server-side cookie-replay discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from x.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class XCookieResponse(BaseModel):
    """Result of syncing an X (Twitter) Cookie header.

    ``has_cookie`` is true only when BOTH ``auth_token`` and ``ct0`` are
    present — twitter-cli needs both to authenticate.
    """

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    message: str = ""
    error_code: str = ""


class RedditCookieIn(BaseModel):
    """Cookie sync payload for Reddit rdt-cli discovery."""

    cookie: str = Field(
        ...,
        description="Cookie header string from reddit.com.",
        min_length=1,
    )
    source: str = Field(
        default="extension",
        description="Where the cookie came from. Used for telemetry only.",
    )


class RedditCookieResponse(BaseModel):
    """Result of syncing Reddit cookies into the rdt-cli credential store."""

    ok: bool
    has_cookie: bool
    cookie_names: list[str] = Field(default_factory=list)
    credential_file: str = ""
    message: str = ""
    error_code: str = ""


class XhsLoginStateIn(BaseModel):
    """Privacy-preserving xhs login state reported by the browser extension."""

    logged_in: StrictBool


class XhsLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed xhs login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class ZhihuLoginStateIn(BaseModel):
    """Privacy-preserving Zhihu login state reported by the browser extension."""

    logged_in: StrictBool


class ZhihuLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed Zhihu login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class LinuxdoLoginStateIn(BaseModel):
    """Privacy-preserving Linux.do login state reported by the extension."""

    logged_in: StrictBool


class LinuxdoLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed Linux.do login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class WeiboLoginStateIn(BaseModel):
    """Privacy-preserving Weibo login state reported by the extension."""

    logged_in: StrictBool


class WeiboLoginStateResponse(BaseModel):
    """Result of persisting the browser-observed Weibo login state."""

    ok: bool = True
    logged_in: bool
    updated_at: str = ""


class XStatusResponse(BaseModel):
    """Current X (Twitter) source health (spec §7).

    ``state`` is one of ``ok`` / ``missing_cookie`` / ``expired_cookie`` /
    ``rate_limited`` / ``blocked``. ``feed_paused`` is true when repeated
    For-You failures have auto-paused the high-visibility home-timeline fetch.
    """

    state: str = "ok"
    consecutive_failures: int = 0
    feed_paused: bool = False
    cooldown_until: str = ""
    detail: str = ""
    updated_at: str = ""


class SourceStatusItem(BaseModel):
    """Unified per-source login / cookie readiness (settings pages).

    ``state`` is a coarse, source-agnostic status so every platform can render
    the same chip:

    - ``ok``         — credential present AND live-validated (X only, from the
      health store).
    - ``ready``      — credential present and structurally valid, but not
      live-validated (B站 cookie with login fields, or a fresh browser login
      state recently synced).
    - ``partial``    — credential present but structurally incomplete, likely
      broken (B站 cookie missing some of the core login fields).
    - ``stale``      — credential synced before but not recently, likely
      expired.
    - ``missing``    — source enabled but no usable credential.
    - ``unverified`` — a credential or source is configured, but local state
      does not prove that it currently works.
    - ``login_required`` / ``error`` — a local command credential is missing,
      or its saved credential file is invalid.
    - ``expired`` / ``blocked`` — X live-health states.
    - ``rate_limited`` — X live-health or XHS persisted safety cooldown.
    - ``no_auth``    — source needs no login (YouTube / Weibo public paths).
    - ``disabled``   — source switched off in config (Bangumi only, and only
      until it moves onto ``auth``, where scheduling and credential state are
      separate dimensions rather than two values of one field).

    ``logged_in`` is a convenience flag (``state in {ok, ready, no_auth}``) so
    the UI can pick a dot colour without re-deriving the rule. For anonymous
    sources it represents local readiness, not a literal authenticated session.

    **``state`` and ``logged_in`` are superseded by ``auth``.** They pack four
    independent questions into one string, which is why the same ``ready`` can
    mean "three cookie field names were counted" on one platform and "a file
    exists on disk" on another. ``auth`` separates those questions; these two
    stay for the frontends that have not switched over yet, and their output is
    byte-identical to what the endpoint has always returned.
    """

    enabled: bool = False
    state: str = "missing"
    detail: str = ""
    logged_in: bool = False
    # Discovery sub-feed/task execution is circuit-broken independently of the
    # login verdict (X For-You, XHS safety cooldown, and Weibo 429 cooldown).
    feed_paused: bool = False
    # Discovery health is independent of authentication. Anonymous sources can
    # remain correctly labelled ``no_auth`` while their most recent fetch is
    # partial, failed, or cooling down. Empty means the source does not expose a
    # separate discovery-health projection yet.
    discovery_state: Literal[
        "", "disabled", "unverified", "ready", "partial", "error", "rate_limited"
    ] = ""
    # ``None`` means "this source has no auth contract", the honest answer for a
    # backend older than the contract — not a missing value to be defaulted away
    # (a default-constructed contract reads ``auth_required=True`` +
    # ``credential="none"``, which renders as 「需要登录」 and would be a fabricated
    # verdict for a public source — the overclaim invariant I3 forbids). All nine
    # sources now ship a real contract: Bangumi resolved the "auth optional" shape
    # by staying ``auth_required=False`` (anonymous-public) while its optional
    # personal token reports ``verify_method='live_probe'`` when configured. The
    # ``| None`` is kept for the three surfaces' older-backend fallback path, not
    # because any provider emits it today.
    auth: SourceAuthContract | None = None
    # Optional personal-token dimension (currently Bangumi only): ``"ok"`` when a
    # token is configured and not rejected, ``"rejected"`` when Bangumi denied it
    # and discovery degraded to anonymous, ``""`` when no token is configured.
    token_state: str = ""
    # Overseas-egress advisory, authored entirely by the backend so no settings
    # surface has to keep its own platform list or re-read ``[network].mode``
    # (both facts live in ``sources.platforms``). ``requires_overseas_network``
    # is the static platform property; ``network_hint`` is ready-to-render copy
    # that is non-empty ONLY when the user's current mode makes that platform
    # unreachable-by-configuration. A surface renders ``network_hint`` verbatim
    # when it is non-empty and the row is currently enabled.
    requires_overseas_network: bool = False
    network_hint: str = ""


class SourcesStatusResponse(BaseModel):
    """Login / cookie readiness for every content source, keyed by platform.

    Backs the unified status chip shown on both the desktop-Web and the
    extension settings pages. Derived entirely from local signals (config
    cookie fields, the X health store, the Douyin cookie file/env, and the
    privacy-preserving 小红书 browser login-state flag) — no outbound platform calls.
    """

    bilibili: SourceStatusItem = Field(default_factory=SourceStatusItem)
    xiaohongshu: SourceStatusItem = Field(default_factory=SourceStatusItem)
    douyin: SourceStatusItem = Field(default_factory=SourceStatusItem)
    youtube: SourceStatusItem = Field(default_factory=SourceStatusItem)
    twitter: SourceStatusItem = Field(default_factory=SourceStatusItem)
    zhihu: SourceStatusItem = Field(default_factory=SourceStatusItem)
    reddit: SourceStatusItem = Field(default_factory=SourceStatusItem)
    bangumi: SourceStatusItem = Field(default_factory=SourceStatusItem)
    linuxdo: SourceStatusItem = Field(default_factory=SourceStatusItem)
    v2ex: SourceStatusItem = Field(default_factory=SourceStatusItem)
    weibo: SourceStatusItem = Field(default_factory=SourceStatusItem)


class SourceVerifyResponse(BaseModel):
    """Result of ``POST /api/sources/{slug}/verify``.

    ``outcome`` is a deliberate third state alongside success and failure.
    "Could not tell" is a real answer — a proxy hiccup, an extension that never
    replied, a throttled platform, or YouTube needing no login at all — and
    rendering any of those as a failure would tell users their credential is
    broken when it is not. The frontends key their tone off this field rather
    than each re-deriving one from ``auth.verification``; three surfaces
    independently mapping six values to three tones is exactly the drift that
    produced the two divergent status maps in spec D6.

    ``changed`` is true only when the verification moved ``auth.credential`` or
    ``auth.verification``. A live probe rewrites ``verified_at`` on every call,
    so a timestamp refresh alone is not a change.

    ``replayed`` says this response reused a stored result instead of doing the
    work, so the frontends can stop a debounced click from looking identical to
    a fresh one — otherwise a user who just repaired a cookie clicks again,
    gets the cached failure back, and concludes the repair failed.
    ``retry_after_seconds`` carries the debounce window itself so no surface has
    to hardcode it (invariant I4).
    """

    slug: str = ""
    outcome: Literal["verified", "failed", "indeterminate"] = "indeterminate"
    changed: bool = False
    message: str = ""
    replayed: bool = False
    retry_after_seconds: float = 0.0
    auth: SourceAuthContract = Field(default_factory=SourceAuthContract)


class SourceCredentialWriteIn(BaseModel):
    """Body of ``POST /api/sources/{slug}/credential`` — the one write shape.

    ``kind`` is explicit rather than inferred from the slug because 小红书
    accepts two genuinely different things: a login *state* (a bare boolean the
    extension reports) and content access *tokens* (per-note ``xsec_token``
    values that say nothing about being logged in). Letting the platform imply
    the kind is how those two ended up sharing a "credential" vocabulary in the
    first place (spec D5).
    """

    kind: Literal["cookie", "token", "login_state"] = "cookie"
    value: bool | str = ""
    source: str = Field(
        default="settings",
        description="Where the credential came from. Telemetry only.",
    )
    pairs: list[dict[str, str]] = Field(
        default_factory=list,
        description="``kind='token'`` only: {note_id, xsec_token} pairs.",
    )
    # There is deliberately no ``validate_live`` opt-out. It shipped here with
    # no caller anywhere — not the extension, not the three frontends, not the
    # CLI — and offered anything that could reach localhost a documented way to
    # turn off the one promise this endpoint is named after, in exchange for
    # nothing. **No write surface has such a switch any more**: the deprecated
    # route's ``validate_with_bilibili`` is still accepted on the wire (installed
    # extensions send it) but no longer consulted. Deleting the flag here while
    # leaving the identical one running next door would have made the deletion
    # cosmetic — "the extension always sends true" describes the extension, not
    # everything that can reach this port.


class SourceCredentialWriteResponse(BaseModel):
    """Result of a unified credential write.

    ``checked`` is the honesty field required by invariant I5: a write that
    could not be verified says so instead of returning a bare success, and
    ``unverified_reason`` carries the platform-specific "why". Without it the
    only way to tell a probed B站 cookie from an unprobed 知乎 boolean would be
    to know the implementation — which is exactly the confusion the orthogonal
    contract exists to end.

    ``auth`` is the freshly recomputed contract, so a save and a status poll
    can never disagree about what just happened. It is the receipt that the old
    save-and-hope-for-the-best flow never gave anyone (spec D7).
    """

    slug: str = ""
    accepted: bool = False
    error_code: str = ""
    message: str = ""
    # False on an accepted no-op: the incoming value was already the stored one.
    persisted: bool = False
    checked: Literal["live_probe", "structural", "none"] = "none"
    unverified_reason: str = ""
    cookie_names: list[str] = Field(default_factory=list)
    auth: SourceAuthContract = Field(default_factory=SourceAuthContract)


class FormAction(BaseModel):
    """One button a settings surface may offer for a credential.

    Actions are advertised only where the capability exists. ``clear`` is
    deliberately absent from every platform: no endpoint can erase a stored
    credential today (``PUT /api/config`` reads an empty field as "not edited",
    which is the opposite of a delete), and a button that silently does nothing
    is exactly the "appears to work" failure CLAUDE.md pitfall #7 is about.
    """

    action: Literal["verify", "copy", "open_login_window"]
    label: str
    #: ``open_login_window`` only — where the user goes to log in.
    url: str = ""


class CredentialFormSpec(BaseModel):
    """How a settings surface should ask for one platform's credential.

    The point of shipping this from the backend is invariant I4: with a
    descriptor, three frontends render every registered platform without a single
    ``key === "xiaohongshu"``. Without it, each surface re-derives "does this
    platform even take a paste box" from platform knowledge it has no business
    holding — and the two that got it wrong disagreed about 小红书.

    ``kind`` is a *capability* statement, so ``extension_only`` is binding: the
    backend stores no pasteable credential for those platforms, and a surface
    that renders an input anyway is inviting the user to type into a void.
    """

    kind: Literal["cookie_textarea", "token_input", "extension_only", "none"] = "none"
    label: str = ""
    placeholder: str = ""
    #: Name of the env var that overrides this credential, or ``None`` when the
    #: platform honours no env var.
    env_var: str | None = None
    required_keys: list[str] = Field(default_factory=list)
    #: How ``required_keys`` is evaluated. 抖音 accepts *any one* of three
    #: session cookies, so rendering its three names as jointly required would
    #: tell users the write path demands something it does not. The spec's
    #: original field table had only ``required_keys``; splitting the mode out
    #: keeps the descriptor honest instead of overstating the gate.
    required_keys_mode: Literal["all", "any"] = "all"
    actions: list[FormAction] = Field(default_factory=list)
    help_text: str = ""


class SourceCredentialItem(BaseModel):
    """Current local credential snapshot for a source settings page."""

    label: str = "Cookie"
    value: str = ""
    available: bool = False
    detail: str = ""
    #: How to ask for this credential. Added in Phase 4 so the frontends stop
    #: hardcoding per-platform form knowledge.
    form: CredentialFormSpec = Field(default_factory=CredentialFormSpec)
    #: Ready-to-render summary line for the credential row. Lives here rather
    #: than in three ``if (available)`` ladders because 小红书 needs a different
    #: sentence ("a content token is saved, which is not a login") and that
    #: sentence was the last per-platform branch on the desktop page.
    summary: str = ""


class SourcesCredentialsResponse(BaseModel):
    """Current local Cookie / token values for source settings pages."""

    bilibili: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    xiaohongshu: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    douyin: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    youtube: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    twitter: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    zhihu: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    reddit: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    bangumi: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    linuxdo: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    v2ex: SourceCredentialItem = Field(default_factory=SourceCredentialItem)
    weibo: SourceCredentialItem = Field(default_factory=SourceCredentialItem)


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


class CognitionUpdateSummary(BaseModel):
    """Structured cognition card shown in the popup profile tab."""

    summary: str
    context_line: str = ""
    impact: str = ""
    reasoning: str = ""
    evidence: str = ""
    source: str = ""
    source_label: str = ""
    expand_hint: str = "summary_only"
    created_at: str = ""


class SpeculativeSpecificOut(BaseModel):
    """A narrow topic within a speculative domain."""

    name: str = ""
    confirmation_count: int = 0


class SpeculativeInterestOut(BaseModel):
    """A speculated interest direction with two-level structure."""

    domain: str = ""
    reason: str = ""
    confidence: float = 0.0
    probe_mode: str = "near"
    challenge: bool = False
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    specifics: list[SpeculativeSpecificOut] = Field(default_factory=list)


class SpeculativeAvoidanceOut(BaseModel):
    """A speculated avoidance direction with two-level structure."""

    domain: str = ""
    reason: str = ""
    confidence: float = 0.0
    source_mode: str = ""
    source_signal: str = ""
    confirmation_count: int = 0
    confirmation_threshold: int = 3
    status: str = "active"
    specifics: list[SpeculativeSpecificOut] = Field(default_factory=list)


class MBTIDimensionOut(BaseModel):
    """A single MBTI dimension pole with strength."""

    pole: str = ""
    strength: float = 0.5


class MBTIOut(BaseModel):
    """MBTI personality type with dimensional breakdown."""

    type: str = ""
    dimensions: dict[str, MBTIDimensionOut] = Field(default_factory=dict)
    confidence: float = 0.0


class InterestSpecificOut(BaseModel):
    """A narrow interest within a domain."""

    name: str = ""
    weight: float = 0.5


class InterestDomainOut(BaseModel):
    """A broad interest domain with optional specific sub-interests."""

    domain: str = ""
    weight: float = 0.5
    specifics: list[InterestSpecificOut] = Field(default_factory=list)


class StylePreferenceOut(BaseModel):
    """Content style preferences."""

    preferred_duration: str = ""
    preferred_pace: str = ""
    quality_sensitivity: float = 0.5
    humor_preference: float = 0.5
    depth_preference: float = 0.5


class ContextModeOut(BaseModel):
    """Contextual usage patterns."""

    weekday_patterns: str = ""
    weekend_patterns: str = ""
    time_of_day_patterns: str = ""
    session_type: str = ""


class AwarenessNoteOut(BaseModel):
    """A single awareness observation from the soul layer."""

    date: str = ""
    observation: str = ""
    trend: str = ""
    emotion_guess: str = ""


class InsightHypothesisOut(BaseModel):
    """An active insight or hypothesis about the user."""

    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    validated: bool = False
    created_at: str = ""


class ProfileSummaryResponse(BaseModel):
    """Full soul profile exposed to the popup — all five Onion layers."""

    initialized: bool
    personality_portrait: str = ""
    # Core layer
    core_traits: list[str] = Field(default_factory=list)
    deep_needs: list[str] = Field(default_factory=list)
    mbti: MBTIOut = Field(default_factory=MBTIOut)
    # Values layer
    values: list[str] = Field(default_factory=list)
    motivational_drivers: list[str] = Field(default_factory=list)
    # Interest layer
    likes: list[InterestDomainOut] = Field(default_factory=list)
    dislikes: list[InterestDomainOut] = Field(default_factory=list)
    favorite_up_users: list[str] = Field(default_factory=list)
    # Role layer
    life_stage: str = ""
    current_phase: str = ""
    # Surface layer
    cognitive_style: list[str] = Field(default_factory=list)
    style: StylePreferenceOut = Field(default_factory=StylePreferenceOut)
    context: ContextModeOut = Field(default_factory=ContextModeOut)
    exploration_openness: float = 0.5
    # Cross-cutting
    speculative_interests: list[SpeculativeInterestOut] = Field(default_factory=list)
    speculative_avoidances: list[SpeculativeAvoidanceOut] = Field(default_factory=list)
    recent_cognition_updates: list[CognitionUpdateSummary] = Field(default_factory=list)
    has_more_cognition_updates: bool = False
    next_cognition_cursor: str = ""
    active_insights: list[InsightHypothesisOut] = Field(default_factory=list)
    recent_awareness: list[AwarenessNoteOut] = Field(default_factory=list)
    # User-authored overrides (ProfileOverrides.to_dict()), so the display UI
    # can badge edited/pinned fields. Empty when the user has made no edits.
    overrides: dict[str, object] = Field(default_factory=dict)


class EventRejectedOut(BaseModel):
    """One event skipped during batch ingest."""

    index: int
    type: str
    reason: str


class EventReceiptOut(BaseModel):
    """One accepted/duplicate event's stable durable receipt."""

    index: int
    event_id: int
    event_type: str
    inserted: bool
    duplicate: bool


class EventIngestResponse(BaseModel):
    """Response after accepting a batch of events."""

    accepted: int
    duplicates: int = 0
    rejected: list[EventRejectedOut] = Field(default_factory=list)
    receipts: list[EventReceiptOut] = Field(default_factory=list)


ExtensionE2EPlatform = Literal["douyin", "xiaohongshu", "twitter", "reddit"]
ExtensionE2EAction = Literal[
    "snapshot",
    "scroll",
    "click",
    "like",
    "favorite",
    "share",
    "follow",
    "repost",
    "bookmark",
]
ExtensionE2EActionList = Annotated[list[ExtensionE2EAction], Field(min_length=1)]
ExtensionE2EActionStatus = Literal["ok", "skipped", "failed"]
ExtensionE2ERunStatus = Literal["ok", "partial", "failed", "timeout"]
ExtensionNativeSaveE2EPlatform = Literal[
    "youtube",
    "xiaohongshu",
    "douyin",
    "twitter",
    "zhihu",
    "reddit",
]
_EXTENSION_NATIVE_SAVE_E2E_TARGETS: dict[str, dict[NativeSaveActionOut, str]] = {
    "youtube": {"favorite": "OpenBiliClaw", "watch_later": "YouTube Watch Later"},
    "xiaohongshu": {"favorite": "小红书收藏", "watch_later": "小红书收藏"},
    "douyin": {"favorite": "抖音收藏", "watch_later": "抖音收藏"},
    "twitter": {"favorite": "X Bookmarks", "watch_later": "X Bookmarks"},
    "zhihu": {"favorite": "知乎收藏", "watch_later": "知乎收藏"},
    "reddit": {"favorite": "Reddit Saved", "watch_later": "Reddit Saved"},
}
_EXTENSION_NATIVE_SAVE_E2E_CONTENT_IDS: dict[str, re.Pattern[str]] = {
    "youtube": re.compile(r"[A-Za-z0-9_-]{11}"),
    "xiaohongshu": re.compile(r"[0-9a-f]{24}"),
    "douyin": re.compile(r"[0-9]{5,30}"),
    "twitter": re.compile(r"[0-9]{5,30}"),
    "zhihu": re.compile(r"(?:question|answer|article):[0-9]+"),
    "reddit": re.compile(r"t[13]_[a-z0-9]+"),
}
_EXTENSION_NATIVE_SAVE_E2E_ERROR_CODES: dict[NativeSaveStatusOut, frozenset[str]] = {
    "pending": frozenset({""}),
    "syncing": frozenset({""}),
    "synced": frozenset({""}),
    "already_synced": frozenset({""}),
    "login_required": frozenset({""}),
    "rate_limited": frozenset({""}),
    "unsupported": frozenset({"unsupported_content_type"}),
    "extension_required": frozenset({"extension_unavailable"}),
    "failed": frozenset(
        {
            "adapter_exception",
            "adapter_timeout",
            "extension_task_timeout",
            "interrupted",
            "invalid_adapter_result",
            "item_heartbeat_failed",
            "native_confirmation_not_observed",
            "native_content_not_ready",
            "native_control_not_found",
            "native_dialog_not_opened",
            "native_request_rejected",
            "native_save_failed",
            "native_save_timeout",
            "native_target_not_found",
            "not_saved_locally",
            "sync_already_in_progress",
        }
    ),
}


def _default_extension_e2e_platforms() -> list[ExtensionE2EPlatform]:
    return ["douyin", "xiaohongshu", "twitter", "reddit"]


class ExtensionNativeSaveE2EAuthorizationIn(BaseModel):
    """Exact, non-secret authorization for one named native-save mutation."""

    model_config = ConfigDict(extra="forbid")

    allow_state_changing: StrictBool
    platform: ExtensionNativeSaveE2EPlatform
    action: NativeSaveActionOut
    content_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    expected_target: Annotated[StrictStr, Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def _validate_exact_mapping(self) -> Self:
        if self.allow_state_changing is not True:
            raise ValueError("allow_state_changing must be true")
        if _EXTENSION_NATIVE_SAVE_E2E_CONTENT_IDS[self.platform].fullmatch(self.content_id) is None:
            raise ValueError("content_id is not an allowed public content identity")
        if self.expected_target != _EXTENSION_NATIVE_SAVE_E2E_TARGETS[self.platform][self.action]:
            raise ValueError("expected_target does not match platform action")
        return self


class ExtensionE2ERunIn(BaseModel):
    """Request to run a local browser-extension E2E simulation."""

    model_config = ConfigDict(extra="forbid")

    platforms: list[ExtensionE2EPlatform] = Field(
        default_factory=_default_extension_e2e_platforms,
        min_length=1,
    )
    actions: dict[ExtensionE2EPlatform, ExtensionE2EActionList] = Field(default_factory=dict)
    allow_state_changing: bool = False
    timeout_seconds: int = Field(default=45, ge=5, le=180)
    native_save_authorization: ExtensionNativeSaveE2EAuthorizationIn | None = None

    @model_validator(mode="after")
    def _validate_native_save_mode(self) -> Self:
        if self.native_save_authorization is None:
            return self
        if self.allow_state_changing is not True:
            raise ValueError("allow_state_changing must be true for native save")
        if self.actions:
            raise ValueError("native-save E2E cannot include generic actions")
        return self


class ExtensionE2EActionResultIn(BaseModel):
    """One action result reported by the extension E2E runner."""

    action: ExtensionE2EAction
    status: ExtensionE2EActionStatus
    detail: str = ""


class ExtensionE2EPlatformResultIn(BaseModel):
    """Per-platform action results reported by the extension."""

    platform: ExtensionE2EPlatform
    actions: list[ExtensionE2EActionResultIn] = Field(default_factory=list)
    detail: str = ""


class ExtensionNativeSaveE2EResultIn(BaseModel):
    """Only fields allowed in a native-save E2E result record."""

    model_config = ConfigDict(extra="forbid")

    platform: ExtensionNativeSaveE2EPlatform
    action: NativeSaveActionOut
    content_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    expected_target: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    task_status: NativeSaveStatusOut
    error_code: Annotated[StrictStr, Field(max_length=64)] = ""

    @model_validator(mode="after")
    def _validate_safe_pair(self) -> Self:
        authorization = ExtensionNativeSaveE2EAuthorizationIn(
            allow_state_changing=True,
            platform=self.platform,
            action=self.action,
            content_id=self.content_id,
            expected_target=self.expected_target,
        )
        del authorization
        if self.error_code not in _EXTENSION_NATIVE_SAVE_E2E_ERROR_CODES[self.task_status]:
            raise ValueError("task_status and error_code combination is not allowed")
        return self


class ExtensionE2EResultIn(BaseModel):
    """Signed extension callback payload for a local E2E run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    token: str
    platforms: list[ExtensionE2EPlatformResultIn] = Field(default_factory=list)
    error: str = ""
    native_save_result: ExtensionNativeSaveE2EResultIn | None = None

    @model_validator(mode="after")
    def _validate_result_mode(self) -> Self:
        if self.native_save_result is not None and (self.platforms or self.error):
            raise ValueError("native-save result cannot include generic result fields")
        return self


class ExtensionE2EEventMatchOut(BaseModel):
    """Natural backend event matched to a requested extension action."""

    event_id: int
    event_type: str
    url: str = ""
    title: str = ""


class ExtensionE2EActionReportOut(BaseModel):
    """Final report for one requested action."""

    action: ExtensionE2EAction
    extension_status: ExtensionE2EActionStatus = "skipped"
    extension_executed: bool = False
    extension_detail: str = ""
    backend_event_matched: bool = False
    backend_event: ExtensionE2EEventMatchOut | None = None


class ExtensionE2EPlatformReportOut(BaseModel):
    """Final report for one requested platform."""

    platform: ExtensionE2EPlatform
    actions: list[ExtensionE2EActionReportOut] = Field(default_factory=list)
    detail: str = ""


class ExtensionE2ERunOut(BaseModel):
    """Final local E2E run report."""

    run_id: str
    status: ExtensionE2ERunStatus
    platforms: list[ExtensionE2EPlatformReportOut] = Field(default_factory=list)
    error: str = ""
    timeout_seconds: int
    native_save_result: ExtensionNativeSaveE2EResultIn | None = None


class FeedbackIn(BaseModel):
    """Feedback payload submitted from CLI-compatible clients."""

    recommendation_id: int
    feedback_type: str
    note: str = ""
    request_id: IdempotencyKey


class FeedbackResponse(BaseModel):
    """Response after accepting recommendation feedback."""

    ok: bool
    recommendation_id: int
    feedback_type: str
    event_id: int = 0
    duplicate: bool = False
    processing: str = "queued"


class InsightFeedbackIn(BaseModel):
    """User confirm/reject on a specific insight hypothesis (insight cards)."""

    hypothesis: str
    signal: str  # confirm/like/support (positive) or reject/dislike/deny


class InsightFeedbackResponse(BaseModel):
    """Result of calibrating an insight hypothesis from user feedback."""

    ok: bool
    matched: bool
    hypothesis: str = ""
    signal: str = ""
    validated: bool = False
    confidence: float = 0.0


class ProfileEditIn(BaseModel):
    """One user edit to the AI-generated profile overlay.

    ``target`` is an onion field path (e.g. ``core.core_traits``) or an
    interest polarity (``likes`` / ``dislikes``). ``op`` ∈
    {set, add, remove, reset}. ``parent`` targets a specific under an
    interest domain; ``weight`` pins an interest domain's weight.
    """

    target: str
    op: str
    value: str | float | None = None
    parent: str = ""
    weight: float | None = None


class WatchLaterAddIn(BaseModel):
    """Payload to bookmark a video."""

    bvid: str
    note: str = ""


class WatchLaterStateResponse(BaseModel):
    """Whether a single video is bookmarked, plus the total count."""

    saved: bool
    total: int
    item_key: str = ""
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class WatchLaterItem(BaseModel):
    """One item in the watch-later list."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    content_type: str = "video"
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class WatchLaterListResponse(BaseModel):
    """Paginated watch-later list."""

    items: list[WatchLaterItem]
    total: int


class FavoriteAddIn(BaseModel):
    """Payload to favorite (收藏) a video."""

    bvid: str
    note: str = ""


class FavoriteStateResponse(BaseModel):
    """Whether a single video is favorited, plus the total count."""

    saved: bool
    total: int
    item_key: str = ""
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class FavoriteItem(BaseModel):
    """One item in the favorites list."""

    bvid: str
    item_key: str = ""
    content_id: str = ""
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    content_type: str = "video"
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class FavoriteListResponse(BaseModel):
    """Paginated favorites list."""

    items: list[FavoriteItem]
    total: int


_SavedIdentityString = Annotated[StrictStr, Field(max_length=2048)]


def validate_saved_item_key(value: str) -> str:
    """Validate a canonical item key without guessing or alias resolution."""
    if not isinstance(value, str):
        raise ValueError("item_key must be a string")
    item_key = value.strip()
    if (
        not item_key
        or item_key != value
        or len(item_key) > 2048
        or _has_identity_whitespace(item_key)
        or _has_unicode_control(item_key)
    ):
        raise ValueError("item_key must be a non-blank canonical key")
    parts = item_key.split(":")
    platform = parts[0]
    stable_key = len(parts) == 2 and bool(parts[1])
    zhihu_typed_key = (
        len(parts) == 3
        and platform == "zhihu"
        and _ZHIHU_TYPED_CONTENT_ID_RE.fullmatch(":".join(parts[1:])) is not None
    )
    url_fallback_key = (
        len(parts) == 3
        and parts[1] == "url"
        and _URL_FALLBACK_ID_RE.fullmatch(parts[2]) is not None
    )
    if (
        not platform
        or not (stable_key or zhihu_typed_key or url_fallback_key)
        or canonical_source_platform(platform) != platform
        or _SAVED_PLATFORM_RE.fullmatch(platform) is None
    ):
        raise ValueError("item_key must be a canonical platform:content identity")
    return item_key


class SavedItemIn(BaseModel):
    """Canonical local-save input for every supported source platform."""

    model_config = ConfigDict(extra="forbid")

    source_platform: _SavedIdentityString
    content_id: _SavedIdentityString = ""
    content_url: _SavedIdentityString = ""
    content_type: Annotated[StrictStr, Field(min_length=1, max_length=128)] = "video"
    title: _SavedIdentityString = ""
    author_name: _SavedIdentityString = ""
    cover_url: _SavedIdentityString = ""
    note: _SavedIdentityString = ""

    @field_validator(
        "source_platform",
        "content_id",
        "content_url",
        "content_type",
        "title",
        "author_name",
        "cover_url",
        "note",
    )
    @classmethod
    def _strip_safe_text(cls, value: str, info: ValidationInfo) -> str:
        if _has_unicode_control(value):
            raise ValueError("saved item fields must not contain Unicode control characters")
        if (
            info.field_name
            in {
                "source_platform",
                "content_id",
                "content_url",
                "cover_url",
            }
            and value != value.strip()
        ):
            raise ValueError("saved identity and URL fields must not have surrounding whitespace")
        normalized = value.strip()
        return normalized

    @field_validator("source_platform")
    @classmethod
    def _canonicalize_platform(cls, value: str) -> str:
        platform = canonical_source_platform(value)
        if not platform:
            raise ValueError("source_platform is required")
        if _SAVED_PLATFORM_RE.fullmatch(platform) is None:
            raise ValueError("source_platform must be a canonical platform slug")
        return platform

    @field_validator("content_type")
    @classmethod
    def _require_content_type(cls, value: str) -> str:
        if not value:
            raise ValueError("content_type is required")
        return value

    @field_validator("content_url", "cover_url")
    @classmethod
    def _validate_optional_http_url(cls, value: str) -> str:
        if not value:
            return value
        return _validate_http_url(value)

    @field_validator("content_id")
    @classmethod
    def _validate_content_id(cls, value: str, info: ValidationInfo) -> str:
        platform = str(info.data.get("source_platform", ""))
        typed_zhihu_id = (
            platform == "zhihu" and _ZHIHU_TYPED_CONTENT_ID_RE.fullmatch(value) is not None
        )
        if (
            (":" in value and not typed_zhihu_id)
            or _has_identity_whitespace(value)
            or _has_unicode_control(value)
        ):
            raise ValueError("content_id must be one non-blank stable identity segment")
        return value

    def model_post_init(self, __context: object) -> None:
        del __context
        validate_saved_item_key(
            make_item_key(self.source_platform, self.content_id, self.content_url)
        )


class SavedItemKeyIn(BaseModel):
    """Exact local membership identity used for removal."""

    model_config = ConfigDict(extra="forbid")

    item_key: _SavedIdentityString

    @field_validator("item_key")
    @classmethod
    def _validate_item_key(cls, value: str) -> str:
        return validate_saved_item_key(value)


class SavedSyncRequest(BaseModel):
    """Explicit manual-sync selection; an empty list means all eligible rows."""

    model_config = ConfigDict(extra="forbid")

    item_keys: Annotated[list[StrictStr], Field(max_length=500)] = Field(default_factory=list)

    @field_validator("item_keys")
    @classmethod
    def _validate_item_keys(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(validate_saved_item_key(value) for value in values))


class ExtensionNativeSaveResultIn(BaseModel):
    """Strict extension callback for one durable native-save job."""

    model_config = ConfigDict(extra="forbid")

    task_id: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    item_key: Annotated[StrictStr, Field(min_length=1, max_length=768)]
    status: Literal[
        "synced",
        "already_synced",
        "login_required",
        "rate_limited",
        "unsupported",
        "failed",
    ]
    error_code: Annotated[StrictStr, Field(max_length=128)] = ""
    error_message: Annotated[StrictStr, Field(max_length=512)] = ""


class SavedItemStateResponse(BaseModel):
    """Local membership plus its latest native-sync state."""

    saved: bool
    item_key: str
    sync_status: NativeSaveStatusOut | None = None
    sync_task_id: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedListItem(BaseModel):
    """One platform-neutral saved membership and sync snapshot."""

    item_key: str
    source_platform: str
    content_id: str
    content_url: str = ""
    content_type: str = "video"
    title: str = ""
    author_name: str = ""
    cover_url: str = ""
    note: str = ""
    added_at: str = ""
    sync_status: NativeSaveStatusOut = "pending"
    sync_task_id: str = ""
    requested_action: str = ""
    resolved_action: str = ""
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedListResponse(BaseModel):
    """Paginated platform-neutral saved memberships."""

    items: list[SavedListItem]
    total: int


class SavedSyncItemResponse(BaseModel):
    """One truthful item result in a native-save task."""

    item_key: str
    status: NativeSaveStatusOut
    resolved_action: NativeSaveActionOut
    resolved_target: str = ""
    error_code: str = ""
    error_message: str = ""


class SavedSyncBatchResponse(BaseModel):
    """Durable native-save batch state returned at creation and polling."""

    task_id: str
    items: list[SavedSyncItemResponse]


class RecommendationClickIn(BaseModel):
    """Payload for a recommendation click-through from the extension popup."""

    recommendation_id: int | None = None
    bvid: str = ""
    content_id: str = ""
    content_url: str = ""
    source_platform: str = ""
    title: str = ""
    topic_label: str = ""
    up_name: str = ""
    request_id: IdempotencyKey
    # v0.3.x event-satisfaction signal: optional dwell on the
    # recommendation click-through. When present, these flow into the
    # persisted click event's metadata so storage classification can
    # tell meaningful_dwell vs quick_exit on recommended content.
    watch_seconds: float | None = None
    video_duration_seconds: float | None = None


class RecommendationClickResponse(BaseModel):
    """Response after ingesting a recommendation click-through."""

    ok: bool
    bvid: str
    layers_updated: list[str] = Field(default_factory=list)
    event_id: int = 0
    duplicate: bool = False
    processing: str = "queued"


class ChatIn(BaseModel):
    """Popup chat request."""

    message: str


class ChatResponse(BaseModel):
    """Popup chat response."""

    reply: str


class ChatTurnIn(BaseModel):
    """Durable popup chat turn request.

    The popup uses this endpoint for lifecycle-safe chat.  The POST
    returns quickly with a pending turn; the backend completes it in the
    background and the popup polls by ``turn_id`` after reloads.
    """

    message: str
    turn_id: str = ""
    session: str = "popup"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""
    # The only client-declared relation.  Canonical kind/ref/generation/title
    # are resolved from this durable target by the server at POST time.
    reply_to_turn_id: str = ""
    payload: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reserved_binding_payload(self) -> Self:
        """Do not accept client-supplied canonical binding facts."""
        reserved = {
            "dialogue_binding",
            "source_type",
            "kind",
            "ref",
            "generation",
            "anchor_origin_turn_id",
            "title",
            "evidence",
            "evidence_labels",
            "context_digest",
            "context",
            "mode",
            "inventory_settles_allowed",
        }
        # Card creation legitimately accepts ``evidence_refs`` as input. Once
        # a request declares a reply relation, however, even evidence is
        # server-owned and must come from the target row.
        if self.reply_to_turn_id.strip():
            reserved.update({"evidence_refs", "evidence_ref"})
        forbidden = sorted(set(self.payload).intersection(reserved))
        if forbidden:
            raise ValueError(f"reserved_payload_key: {', '.join(forbidden)} is server-owned")
        return self


class ChatTurnOut(BaseModel):
    """One durable popup chat turn."""

    turn_id: str
    session: str = "popup"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""
    reply_to_turn_id: str = ""
    message: str = ""
    reply: str = ""
    status: str = "pending"
    error: str = ""
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class DialogueContextPreview(BaseModel):
    """Read-only canonical context returned before a bound send."""

    active: bool = True
    reply_to_turn_id: str
    source_type: str
    kind: str
    generation: int
    title: str
    evidence_labels: list[str] = Field(default_factory=list)
    context_digest: str


class ChatTurnListResponse(BaseModel):
    """Durable popup chat history."""

    items: list[ChatTurnOut]


# --- Configuration API models ---


class LLMProviderConfigOut(BaseModel):
    """LLM provider configuration (keys are always masked on reads)."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    auth_mode: str = ""
    api_flavor: str = ""
    http_referer: str = ""
    x_title: str = ""
    reasoning_effort: str = "medium"
    num_ctx: int = 0


class LLMInstanceConfigOut(LLMProviderConfigOut):
    """One independently addressable chat endpoint."""

    name: str = ""
    provider_type: str = ""
    enabled: bool = True


class EmbeddingConfigOut(BaseModel):
    provider: str = ""
    model: str = ""
    # v0.3.32+ embedding owns its own credentials; api_key is always masked.
    api_key: str = ""
    base_url: str = ""
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    fallback_enabled: bool = False
    fallback_provider: str = ""
    # Optional cover image-only embedding (needs a multimodal embedding model
    # such as gemini-embedding-2 or dashscope qwen3-vl-embedding). Default off.
    multimodal_enabled: bool = False


class ModuleLLMConfigOut(BaseModel):
    provider: str = ""
    model: str = ""
    inherit: bool = True
    chain: list[str] = Field(default_factory=list)


class LLMConfigOut(BaseModel):
    routing_version: int = 2
    instances: dict[str, LLMInstanceConfigOut] = Field(default_factory=dict)
    default_chain: list[str] = Field(default_factory=list)
    routes: dict[str, ModuleLLMConfigOut] = Field(default_factory=dict)
    default_provider: str = "deepseek"
    concurrency: int = 3
    timeout: int = 1200
    # Non-empty fallback_provider = chat fallback on (the legacy
    # fallback_enabled bool was never consulted and is no longer echoed;
    # old clients still sending it are ignored on PUT).
    fallback_provider: str = ""
    openai: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    claude: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    gemini: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    deepseek: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    ollama: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    openrouter: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    # v0.3.32+ — generic OpenAI-protocol-compatible provider.
    openai_compatible: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    # OrcaRouter model-routing gateway (OpenAI-compatible).
    orcarouter: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    embedding: EmbeddingConfigOut = Field(default_factory=EmbeddingConfigOut)
    soul: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    discovery: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    recommendation: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)
    evaluation: ModuleLLMConfigOut = Field(default_factory=ModuleLLMConfigOut)


class BilibiliConfigOut(BaseModel):
    auth_method: str = "cookie"
    cookie: str = ""
    browser_executable: str = ""
    browser_headed: bool = False


class NetworkConfigOut(BaseModel):
    """Overseas routing policy. Any proxy URL userinfo is masked in responses."""

    mode: str = "direct"
    proxy: str = ""


class SourcesBrowserConfigOut(BaseModel):
    cdp_url: str = ""
    headed: bool = False


class SourceDatePreferenceOut(BaseModel):
    recommendation_date_preset: str = "all"
    recommendation_date_start: str = ""
    recommendation_date_end: str = ""
    recommendation_date_weight: float = 0.5


class BilibiliSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = True
    min_interval_minutes: int = 3


class XiaohongshuSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    daily_search_budget: int = 20
    daily_creator_budget: int = 0
    task_interval_seconds: int = 1200
    min_interval_minutes: int = 20


class DouyinSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    mode: str = "direct"
    # Resolved Cookie header (env override, else data/douyin_cookie.json).
    # Read-only mirror for settings pages — always masked on API reads.
    # PUT routes a non-empty value to DouyinCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_DOUYIN_COOKIE"
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    request_interval_seconds: int = 2
    min_interval_minutes: int = 3


class YoutubeSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    daily_search_budget: int = 0
    daily_trending_budget: int = 0
    daily_channel_budget: int = 0
    request_interval_seconds: int = 2
    min_interval_minutes: int = 3


class TwitterSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    mode: str = "cookie"
    # Resolved Cookie header (env override, else data/x_cookie.json).
    # Read-only mirror for settings pages — always masked on API reads.
    # PUT routes a non-empty value to XCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_X_COOKIE"
    daily_search_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


class ZhihuSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "hot", "feed", "creator", "related"]
    )
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    daily_related_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


class RedditSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    backend: str = "rdt"
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "hot", "subreddit", "related"]
    )
    daily_search_budget: int = 300
    daily_hot_budget: int = 300
    daily_subreddit_budget: int = 300
    daily_related_budget: int = 300
    request_interval_seconds: int = 3
    min_interval_minutes: int = 3


class BangumiSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    username: str = ""
    # The personal access token itself is a secret and is NEVER echoed back;
    # this flag only tells the settings UI whether one is stored so it can show
    # a "已配置（留空保持不变）" affordance instead of a bare empty field.
    access_token_set: bool = False
    subject_types: list[str] = Field(default_factory=lambda: ["anime", "book", "game"])
    source_modes: list[str] = Field(default_factory=lambda: ["search", "ranked", "latest"])
    daily_search_budget: int = 300
    daily_ranked_budget: int = 100
    daily_latest_budget: int = 100
    request_interval_seconds: int = 1
    min_interval_minutes: int = 3
    bootstrap_limit: int = 300


class LinuxdoSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "hot", "feed", "creator", "related"]
    )
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    daily_related_budget: int = 0
    request_interval_seconds: int = Field(default=3, ge=0, le=30)
    min_interval_minutes: int = 3
    bootstrap_limit: int = Field(default=300, ge=1, le=300)


class V2EXSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    incremental_enabled: bool = False
    username: str = ""
    access_token_set: bool = False
    token_env: str = "OPENBILICLAW_V2EX_TOKEN"
    source_modes: list[str] = Field(
        default_factory=lambda: ["search", "node", "tab", "hot", "latest"]
    )
    tab_modes: list[str] = Field(default_factory=lambda: ["tech", "creative", "qna"])
    node_allowlist: list[str] = Field(default_factory=list)
    node_blocklist: list[str] = Field(default_factory=lambda: ["sandbox"])
    node_downweight: list[str] = Field(default_factory=lambda: ["promotions", "jobs", "deals"])
    daily_search_budget: int = 120
    daily_node_budget: int = 180
    daily_tab_budget: int = 80
    daily_hot_budget: int = 40
    daily_latest_budget: int = 40
    request_interval_seconds: int = 2
    min_interval_minutes: int = 5
    detail_fetch_limit: int = 15
    reply_enrichment_limit: int = 10
    max_topic_chars: int = 6000
    max_reply_digest_chars: int = 1200
    max_profile_nodes: int = 12
    bootstrap_topics_limit: int = 100
    bootstrap_replies_limit: int = 300
    bootstrap_favorites_limit: int = 300
    bootstrap_max_pages_per_scope: int = 20


class WeiboSourceConfigOut(SourceDatePreferenceOut):
    enabled: bool = False
    source_modes: list[str] = Field(default_factory=lambda: ["search", "hot", "creator"])
    daily_search_budget: int = 60
    daily_hot_budget: int = 10
    daily_creator_budget: int = 30
    request_interval_seconds: int = 3
    min_interval_minutes: int = 10


class SourcesConfigOut(BaseModel):
    browser: SourcesBrowserConfigOut = Field(default_factory=SourcesBrowserConfigOut)
    bilibili: BilibiliSourceConfigOut = Field(default_factory=BilibiliSourceConfigOut)
    xiaohongshu: XiaohongshuSourceConfigOut = Field(default_factory=XiaohongshuSourceConfigOut)
    douyin: DouyinSourceConfigOut = Field(default_factory=DouyinSourceConfigOut)
    youtube: YoutubeSourceConfigOut = Field(default_factory=YoutubeSourceConfigOut)
    twitter: TwitterSourceConfigOut = Field(default_factory=TwitterSourceConfigOut)
    zhihu: ZhihuSourceConfigOut = Field(default_factory=ZhihuSourceConfigOut)
    reddit: RedditSourceConfigOut = Field(default_factory=RedditSourceConfigOut)
    bangumi: BangumiSourceConfigOut = Field(default_factory=BangumiSourceConfigOut)
    linuxdo: LinuxdoSourceConfigOut = Field(default_factory=LinuxdoSourceConfigOut)
    v2ex: V2EXSourceConfigOut = Field(default_factory=V2EXSourceConfigOut)
    weibo: WeiboSourceConfigOut = Field(default_factory=WeiboSourceConfigOut)


class SchedulerConfigOut(BaseModel):
    enabled: bool = True
    llm_budget_max_calls: int = 120
    llm_budget_window_seconds: int = 3600
    pause_on_extension_disconnect: bool = False
    extension_disconnect_grace_seconds: int = 90
    discovery_cron: str = "0 */8 * * *"
    pool_target_count: int = 300
    copy_ready_target_count: int = Field(default=90, ge=0, le=600)
    pool_source_shares: dict[str, int] = Field(default_factory=dict)
    account_sync_interval_hours: int = 6
    source_incremental_enabled: bool = False
    source_incremental_hours: int = 24
    xhs_incremental_hours: int | None = None
    douyin_incremental_hours: int | None = 0
    youtube_incremental_hours: int | None = None
    zhihu_incremental_hours: int | None = None
    reddit_incremental_hours: int | None = None
    linuxdo_incremental_hours: int | None = None
    v2ex_incremental_hours: int | None = None
    refresh_check_interval_seconds: int = 60
    eval_min_batch_size: int = Field(default=15, ge=1, le=90)
    eval_max_wait_seconds: float = Field(default=90.0, ge=0.0, le=600.0)
    signal_event_threshold: int = 6
    feedback_batch_threshold: int = 3
    trending_refresh_minutes: int = 3
    explore_refresh_minutes: int = 3
    discovery_limit: int = 30
    delight_queue_limit: int = 20
    proactive_push_interval_seconds: int = 120
    speculator_idle_interval_minutes: int = 30
    speculation_interval_minutes: int = 10
    speculation_ttl_days: int = 3
    speculation_cooldown_days: int = 7
    speculation_confirmation_threshold: int = 3
    speculation_max_active: int = 5
    speculation_max_primary_interests: int = 15
    speculation_max_secondary_interests: int = 60
    avoidance_speculation_interval_minutes: int = 10
    avoidance_speculation_ttl_days: int = 3
    avoidance_speculation_cooldown_days: int = 7
    avoidance_speculation_confirmation_threshold: int = 3
    avoidance_speculation_max_active: int = 5
    auto_update_enabled: bool = False
    auto_update_check_interval_hours: int = 6
    auto_update_allow_prerelease: bool = False
    auto_update_allowed_remotes: list[str] = Field(default_factory=list)


class SoulConfigOut(BaseModel):
    preference_prompt_view: Literal["legacy", "compact-v1"] = "legacy"
    awareness_prompt_view: Literal["legacy", "compact-v1"] = "compact-v1"
    insight_prompt_view: Literal["legacy", "compact-v1"] = "legacy"
    posture_gate_mode: Literal["shadow", "enforce", "off"] = "shadow"
    posture_gate_force_enforce: bool = False
    topic_lifecycle_serialization: Literal["off", "on"] = "off"
    awareness_event_batch_size: int = Field(default=300, ge=10, le=900)
    insight_note_batch_size: int = Field(default=150, ge=10, le=450)
    cognition_max_tokens: int = Field(default=32768, ge=1024, le=128000)


class DiscoveryConfigOut(BaseModel):
    unified_keyword_planner_enabled: bool = True
    kw_cache_high: int = 30
    kw_cache_low: int = 10
    gen_batch: int = 30
    fetch_batch: int = 5
    history_window_size: int = 150
    history_window_hours: int = 48
    claim_lease_minutes: int = 10
    planner_poll_seconds: int = 120
    plan_ttl_hours: int = 12
    keyword_digest_grace_hours: int = Field(default=24, ge=0, le=168)
    admission_min_score: float = 0.60
    eval_prefilter_mode: Literal["off", "shadow", "enforce"] = "shadow"
    eval_scorer: Literal["llm", "shadow", "learned"] = "llm"
    candidate_eval_concurrency: int = Field(default=3, ge=1, le=3)
    multimodal_evaluation_enabled: bool = False
    visual_profile_enabled: bool = False
    keyframe_enabled: bool = False
    keyframe_max_frames: int = 4
    keyframe_fetch_limit: int = 50
    danmaku_enabled: bool = False
    danmaku_fetch_limit: int = 50
    danmaku_max_chars: int = 500
    multimodal_batch_size: int = 8
    multimodal_image_max_px: int = 384
    multimodal_image_quality: int = 72
    multimodal_image_timeout_seconds: int = 6
    # Read-only UI/API-derived enum over the two canonical DiscoveryConfig
    # booleans (inspiration_search_enabled / inspiration_replace_merged_keywords).
    # Not a config.toml field — the two booleans stay the single source of truth.
    keyword_generation_mode: Literal["legacy", "hybrid", "inspiration"] = "hybrid"


class BackendUpdateStatusOut(BaseModel):
    state: str = "unknown"
    auto_update_enabled: bool = False
    install_mode: str = ""
    current_version: str = ""
    latest_version: str = ""
    latest_tag: str = ""
    last_check_at: str = ""
    last_error: str = ""
    reason: str = "none"


class UpdateStatusResponse(BaseModel):
    backend: BackendUpdateStatusOut


class UpdateCheckIn(BaseModel):
    include_backend: bool = True


class UpdateApplyIn(BaseModel):
    target: Literal["backend"]
    tag: str = ""


class UpdateApplyResponse(BaseModel):
    target: str = "backend"
    state: str
    reason: str = "none"
    accepted: bool
    observe_via: str = "runtime-stream"


class StorageConfigOut(BaseModel):
    db_path: str = "data/openbiliclaw.db"


class LoggingConfigOut(BaseModel):
    level: str = "INFO"
    file_level: str = "DEBUG"
    directory: str = "logs"
    filename: str = "openbiliclaw.log"
    file_path: str = "logs/openbiliclaw.log"
    max_file_size_mb: int = 100
    backup_count: int = 1
    aggregate_budget_mb: int = 500
    unmanaged_truncate_mb: int = 200
    unmanaged_max_age_days: int = 30


class AutostartConfigOut(BaseModel):
    enabled: bool = False
    manage_ollama: bool = True


class SavedSyncConfigOut(BaseModel):
    auto_sync_enabled: bool = False


class SavedSyncConfigUpdateIn(BaseModel):
    auto_sync_enabled: StrictBool | None = None

    @field_validator("auto_sync_enabled", mode="before")
    @classmethod
    def reject_explicit_null_auto_sync(cls, value: object) -> object:
        if value is None:
            raise ValueError("saved_sync.auto_sync_enabled must be a boolean")
        return value


class AutostartStatusOut(BaseModel):
    supported: bool
    enabled: bool
    registered: bool
    can_manage: bool
    platform: str
    mechanism: str
    manage_ollama: bool
    ollama_required: bool
    reason: str = "none"
    detail: str = ""


class AutostartApplyIn(BaseModel):
    enabled: bool


class ConfigIssueOut(BaseModel):
    field: str
    message: str
    severity: str = "warning"


class ConfigResponse(BaseModel):
    """Full configuration response."""

    language: str = "zh"
    data_dir: str = "data"
    degraded: bool = False
    degraded_reason: str = ""
    llm: LLMConfigOut = Field(default_factory=LLMConfigOut)
    bilibili: BilibiliConfigOut = Field(default_factory=BilibiliConfigOut)
    network: NetworkConfigOut = Field(default_factory=NetworkConfigOut)
    sources: SourcesConfigOut = Field(default_factory=SourcesConfigOut)
    scheduler: SchedulerConfigOut = Field(default_factory=SchedulerConfigOut)
    discovery: DiscoveryConfigOut = Field(default_factory=DiscoveryConfigOut)
    autostart: AutostartConfigOut = Field(default_factory=AutostartConfigOut)
    saved_sync: SavedSyncConfigOut = Field(default_factory=SavedSyncConfigOut)
    storage: StorageConfigOut = Field(default_factory=StorageConfigOut)
    logging: LoggingConfigOut = Field(default_factory=LoggingConfigOut)
    soul: SoulConfigOut = Field(default_factory=SoulConfigOut)
    issues: list[ConfigIssueOut] = Field(default_factory=list)


class ConfigUpdateIn(BaseModel):
    """Partial config update. Only provided fields are updated."""

    language: str | None = None
    data_dir: str | None = None
    reset_fields: list[str] | None = None
    suppress_background_llm_work: bool | None = None
    llm: dict[str, object] | None = None
    bilibili: dict[str, object] | None = None
    network: dict[str, object] | None = None
    sources: dict[str, object] | None = None
    scheduler: dict[str, object] | None = None
    discovery: dict[str, object] | None = None
    saved_sync: SavedSyncConfigUpdateIn | None = None
    storage: dict[str, object] | None = None
    logging: dict[str, object] | None = None
    soul: dict[str, object] | None = None

    @field_validator("saved_sync", mode="before")
    @classmethod
    def reject_explicit_null_saved_sync(cls, value: object) -> object:
        if value is None:
            raise ValueError("saved_sync must be an object")
        return value


class ConfigServiceProbeIn(BaseModel):
    """No-write request to probe the submitted LLM or embedding config.

    ``llm_instance`` probes one exact endpoint instance. ``llm_chain``
    exercises the submitted global route, including sequential fallback.
    ``llm_fallback`` remains for legacy clients and probes the second route
    entry (or legacy ``fallback_provider``) exactly.
    """

    kind: Literal[
        "llm",
        "llm_instance",
        "llm_chain",
        "embedding",
        "llm_fallback",
        "network_proxy",
    ]
    instance_id: str = ""
    config: dict[str, object] = Field(default_factory=dict)


class ConfigServiceProbeResponse(BaseModel):
    """Result of a user-triggered provider connectivity probe."""

    ok: bool
    kind: Literal[
        "llm",
        "llm_instance",
        "llm_chain",
        "embedding",
        "llm_fallback",
        "network_proxy",
    ]
    instance_id: str = ""
    provider: str = ""
    model: str = ""
    message: str = ""
    error: str = ""
    latency_ms: int = 0


class ConfigModelDiscoveryIn(BaseModel):
    """No-write request to list models for one submitted LLM instance."""

    instance_id: str
    config: dict[str, object] = Field(default_factory=dict)


class ConfigModelDiscoveryResponse(BaseModel):
    """Models advertised by an endpoint plus local effort guidance.

    The OpenAI-compatible ``GET /models`` response does not standardize
    reasoning-effort capabilities. ``reasoning_efforts`` is therefore an
    explicitly labelled local advisory, never presented as provider-discovered
    metadata.
    """

    ok: bool
    instance_id: str = ""
    provider: str = ""
    models: list[str] = Field(default_factory=list)
    reasoning_efforts: list[str] = Field(default_factory=list)
    reasoning_efforts_source: Literal["local_advisory", "not_available"] = "not_available"
    message: str = ""
    error: str = ""
    latency_ms: int = 0


class SourceShareSuggestionIn(BaseModel):
    """Optional overrides from a settings form that has not been saved yet."""

    enabled_sources: dict[str, bool] | None = None
    configured_shares: dict[str, int] | None = None


class ConfigUpdateResponse(BaseModel):
    """Response after config save."""

    ok: bool = True
    config: ConfigResponse
    message: str = ""
    reloaded: bool = False
    rollback_applied: bool = False
    restart_required: bool = False
    apply_state: Literal["idle", "queued", "applying", "applied", "failed"] = "idle"
    apply_revision: int = 0


class ConfigApplyStatusResponse(BaseModel):
    """Non-sensitive status for the latest persisted runtime-config revision."""

    state: Literal["idle", "queued", "applying", "applied", "failed"] = "idle"
    requested_revision: int = 0
    applied_revision: int = 0
    message: str = ""
    error: str = ""
    updated_at: str = ""


class SourceShareSuggestionResponse(BaseModel):
    """Suggested source shares based on observed source event counts."""

    event_counts: dict[str, int] = Field(default_factory=dict)
    enabled_sources: dict[str, bool] = Field(default_factory=dict)
    suggested_shares: dict[str, int] = Field(default_factory=dict)
