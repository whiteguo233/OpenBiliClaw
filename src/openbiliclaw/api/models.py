"""Pydantic models for the local backend API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BehaviorEventIn(BaseModel):
    """One behavior event reported by the extension."""

    type: str
    url: str = ""
    title: str = ""
    timestamp: int
    source_platform: str = "bilibili"
    context: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
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


class InitStageOut(BaseModel):
    """One stage of guided init (gui-init spec API shape)."""

    n: int
    label: str
    status: str  # pending | running | ok | warning | failed
    reason: str | None = None


class InitPrerequisitesOut(BaseModel):
    """Pre-init checklist surfaced to the UI."""

    bilibili_logged_in: bool = False
    bilibili_check: str = "checking"  # ok | failed | checking
    llm_ready: bool = False
    embedding_ready: bool = False
    enabled_platforms: list[str] = Field(default_factory=list)


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


class RecommendationOut(BaseModel):
    """Recommendation payload exposed to the popup."""

    id: int
    bvid: str
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
    # Text-first sources (X tweet/thread): the popup renders a no-cover
    # text card from body_text/title when content_type is tweet/thread or
    # cover_url is empty.
    content_type: str = "video"
    body_text: str = ""


class RecommendationListResponse(BaseModel):
    """Wrapper response for recommendation lists."""

    items: list[RecommendationOut]


class RecommendationReshuffleResponse(BaseModel):
    """Immediate recommendation reshuffle result."""

    items: list[RecommendationOut]


class RecommendationAppendIn(BaseModel):
    """Request payload for appending another recommendation page."""

    excluded_bvids: list[str] = Field(default_factory=list)


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
    last_discovered_count: int = 0
    last_replenished_count: int = 0
    recent_pool_topics: list[str] = Field(default_factory=list)
    manual_refresh_state: str = "idle"
    manual_refresh_message: str = ""
    last_account_sync_at: str = ""
    last_account_sync_error: str = ""
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
    title: str = ""
    delight_reason: str = ""
    delight_score: float = 0.0
    delight_hook: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""


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
        description="If true, hit the Bilibili nav endpoint before saving "
        "to confirm the cookie is actually authenticated.",
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
      live-validated (B站 cookie with login fields, 抖音 cookie present, 小红书
      access tokens synced within the freshness window).
    - ``partial``    — credential present but structurally incomplete, likely
      broken (B站 cookie missing some of the core login fields).
    - ``stale``      — credential synced before but not recently, likely
      expired (小红书 tokens older than the freshness window).
    - ``missing``    — source enabled but no usable credential.
    - ``expired`` / ``rate_limited`` / ``blocked`` — X live-health states.
    - ``no_auth``    — source needs no login (YouTube, public).

    ``logged_in`` is a convenience flag (``state in {ok, ready, no_auth}``) so
    the UI can pick a dot colour without re-deriving the rule.
    """

    enabled: bool = False
    state: str = "missing"
    detail: str = ""
    logged_in: bool = False
    feed_paused: bool = False


class SourcesStatusResponse(BaseModel):
    """Login / cookie readiness for every content source, keyed by platform.

    Backs the unified status chip shown on both the desktop-Web and the
    extension settings pages. Derived entirely from local signals (config
    cookie fields, the X health store, the Douyin cookie file/env, and the
    recency of token-bearing 小红书 cache rows) — no outbound platform calls.
    """

    bilibili: SourceStatusItem = Field(default_factory=SourceStatusItem)
    xiaohongshu: SourceStatusItem = Field(default_factory=SourceStatusItem)
    douyin: SourceStatusItem = Field(default_factory=SourceStatusItem)
    youtube: SourceStatusItem = Field(default_factory=SourceStatusItem)
    twitter: SourceStatusItem = Field(default_factory=SourceStatusItem)


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


class WatchLaterItem(BaseModel):
    """One item in the watch-later list."""

    bvid: str
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    added_at: str = ""


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


class FavoriteItem(BaseModel):
    """One item in the favorites list."""

    bvid: str
    title: str = ""
    up_name: str = ""
    cover_url: str = ""
    content_url: str = ""
    source_platform: str = ""
    added_at: str = ""


class FavoriteListResponse(BaseModel):
    """Paginated favorites list."""

    items: list[FavoriteItem]
    total: int


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
    layers_updated: list[str]


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


class ChatTurnOut(BaseModel):
    """One durable popup chat turn."""

    turn_id: str
    session: str = "popup"
    scope: str = "chat"
    subject_id: str = ""
    subject_title: str = ""
    message: str = ""
    reply: str = ""
    status: str = "pending"
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class ChatTurnListResponse(BaseModel):
    """Durable popup chat history."""

    items: list[ChatTurnOut]


# --- Configuration API models ---


class LLMProviderConfigOut(BaseModel):
    """LLM provider configuration (keys masked by default)."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    auth_mode: str = ""
    http_referer: str = ""
    x_title: str = ""
    reasoning_effort: str = ""


class EmbeddingConfigOut(BaseModel):
    provider: str = ""
    model: str = ""
    # v0.3.32+ embedding owns its own credentials; api_key is masked.
    api_key: str = ""
    base_url: str = ""
    output_dimensionality: int = 1024
    similarity_threshold: float = 0.82
    fallback_enabled: bool = False
    fallback_provider: str = ""


class ModuleLLMConfigOut(BaseModel):
    provider: str = ""
    model: str = ""


class LLMConfigOut(BaseModel):
    default_provider: str = "deepseek"
    concurrency: int = 3
    timeout: int = 300
    fallback_enabled: bool = False
    fallback_provider: str = ""
    openai: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    claude: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    gemini: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    deepseek: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    ollama: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    openrouter: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
    # v0.3.32+ — generic OpenAI-protocol-compatible provider.
    openai_compatible: LLMProviderConfigOut = Field(default_factory=LLMProviderConfigOut)
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


class SourcesBrowserConfigOut(BaseModel):
    cdp_url: str = ""
    headed: bool = False


class BilibiliSourceConfigOut(BaseModel):
    enabled: bool = True


class XiaohongshuSourceConfigOut(BaseModel):
    enabled: bool = False
    daily_search_budget: int = 0
    daily_creator_budget: int = 0
    task_interval_seconds: int = 45


class DouyinSourceConfigOut(BaseModel):
    enabled: bool = False
    mode: str = "direct"
    # Resolved Cookie header (env override, else data/douyin_cookie.json).
    # Read-only mirror for the settings pages — masked unless reveal_keys.
    # PUT routes a non-empty value to DouyinCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_DOUYIN_COOKIE"
    daily_search_budget: int = 0
    daily_hot_budget: int = 0
    daily_feed_budget: int = 0
    request_interval_seconds: int = 2


class YoutubeSourceConfigOut(BaseModel):
    enabled: bool = False
    daily_search_budget: int = 0
    daily_trending_budget: int = 0
    daily_channel_budget: int = 0
    request_interval_seconds: int = 2
    min_interval_minutes: int = 60


class TwitterSourceConfigOut(BaseModel):
    enabled: bool = False
    mode: str = "cookie"
    # Resolved Cookie header (env override, else data/x_cookie.json).
    # Read-only mirror for the settings pages — masked unless reveal_keys.
    # PUT routes a non-empty value to XCookieManager, never config.toml.
    cookie: str = ""
    cookie_env: str = "OPENBILICLAW_X_COOKIE"
    daily_search_budget: int = 0
    daily_feed_budget: int = 0
    daily_creator_budget: int = 0
    request_interval_seconds: int = 3
    min_interval_minutes: int = 60


class SourcesConfigOut(BaseModel):
    browser: SourcesBrowserConfigOut = Field(default_factory=SourcesBrowserConfigOut)
    bilibili: BilibiliSourceConfigOut = Field(default_factory=BilibiliSourceConfigOut)
    xiaohongshu: XiaohongshuSourceConfigOut = Field(default_factory=XiaohongshuSourceConfigOut)
    douyin: DouyinSourceConfigOut = Field(default_factory=DouyinSourceConfigOut)
    youtube: YoutubeSourceConfigOut = Field(default_factory=YoutubeSourceConfigOut)
    twitter: TwitterSourceConfigOut = Field(default_factory=TwitterSourceConfigOut)


class SchedulerConfigOut(BaseModel):
    enabled: bool = True
    pause_on_extension_disconnect: bool = False
    extension_disconnect_grace_seconds: int = 90
    discovery_cron: str = "0 */8 * * *"
    pool_target_count: int = 300
    pool_source_shares: dict[str, int] = Field(default_factory=dict)
    account_sync_interval_hours: int = 6
    refresh_check_interval_seconds: int = 60
    signal_event_threshold: int = 6
    feedback_batch_threshold: int = 3
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    discovery_limit: int = 30
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
    sources: SourcesConfigOut = Field(default_factory=SourcesConfigOut)
    scheduler: SchedulerConfigOut = Field(default_factory=SchedulerConfigOut)
    autostart: AutostartConfigOut = Field(default_factory=AutostartConfigOut)
    storage: StorageConfigOut = Field(default_factory=StorageConfigOut)
    logging: LoggingConfigOut = Field(default_factory=LoggingConfigOut)
    issues: list[ConfigIssueOut] = Field(default_factory=list)


class ConfigUpdateIn(BaseModel):
    """Partial config update. Only provided fields are updated."""

    language: str | None = None
    data_dir: str | None = None
    reset_fields: list[str] | None = None
    llm: dict[str, object] | None = None
    bilibili: dict[str, object] | None = None
    sources: dict[str, object] | None = None
    scheduler: dict[str, object] | None = None
    storage: dict[str, object] | None = None
    logging: dict[str, object] | None = None


class ConfigServiceProbeIn(BaseModel):
    """No-write request to probe the submitted LLM or embedding config."""

    kind: Literal["llm", "embedding"]
    config: dict[str, object] = Field(default_factory=dict)


class ConfigServiceProbeResponse(BaseModel):
    """Result of a user-triggered provider connectivity probe."""

    ok: bool
    kind: Literal["llm", "embedding"]
    provider: str = ""
    model: str = ""
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


class SourceShareSuggestionResponse(BaseModel):
    """Suggested source shares based on observed source event counts."""

    event_counts: dict[str, int] = Field(default_factory=dict)
    enabled_sources: dict[str, bool] = Field(default_factory=dict)
    suggested_shares: dict[str, int] = Field(default_factory=dict)
